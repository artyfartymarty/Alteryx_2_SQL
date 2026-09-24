"""The rules a Snowpark procedure module must satisfy (spec §4.2), as one AST walk.

Conservative and structural (Task 3 fix round 1, coordinator ruling -- the rules as first written
were evadable by ordinary Python, which the task-3 review's evasion probes demonstrated; every one
of those probes is reproduced as a test in `tests/test_snowpark_rules.py`):

* `session` may appear ONLY as the receiver of `session.table(...)` or
  `session.create_dataframe(...)`, written directly in the one top-level `run` -- not aliased
  (`s = session`), not passed as a call argument, not read through any other attribute
  (`session.sql`, `session.call`, `getattr(session, ...)`, ...), and not taken as a parameter or
  referenced (free variable) by any other function, lambda, or nested/class-level `def run`
  anywhere in the module. Exactly one `def run` may exist in the whole module (found via
  `ast.walk`, not just the module body), and it must be that one top-level definition.
* `.table(...)`, `.save_as_table(...)` and its real camelCase alias `.saveAsTable(...)` --
  whatever they are chained into afterward, `.merge(...)` included -- take exactly one positional
  argument, a string literal or a C4-parameter f-string; no keyword arguments
  (`name=...`/`table_name=...` are refused, not silently unchecked).

Final fix wave (whole-branch review I1/I2/I4, coordinator ruling): the rules above still keyed on
two method names and on the literal receiver name `session`, so three ordinary-Python moves walked
straight past them. All three are closed here, in the same conservative-and-structural register:

* A C4 procedure has exactly ONE sink. Every other `DataFrameWriter` sink (`insert_into`,
  `copy_into_location`, `csv`, `json`, `parquet`, `orc`, `save`, and the camelCase aliases) writes
  somewhere no contract declares, so the NAME is refused wherever it appears -- as an
  `ast.Attribute`'s `attr` or as a bare `ast.Name` -- whatever the receiver is called. Round 2
  (scoped re-review, R1) adds the three routes that are not `DataFrameWriter` at all:
  `create_or_replace_view` / `_temp_view` / `_dynamic_table` (which SUCCEED in the Local Testing
  Framework, so the undeclared object is really created and the segment still PASSes),
  `write_pandas` / `copy_into_table` / `cache_result`, and the `pandas` writers a `to_pandas()`
  frame carries (`to_csv`, `to_parquet`, `to_sql`, ... -- `PANDAS_WRITERS`). `to_pandas` itself
  stays legal: it is how row-sequential logic is expressed at all.
* A write method that is REFERENCED rather than called (`sink = w.save_as_table; sink("ANY.TABLE")`)
  is an `ast.Attribute` that is never a call's `func`, so no argument is ever checked. Refused.
* The `{src_db}.{src_schema}.NAME` / `{tgt_db}.{tgt_schema}.NAME` forms were checked by SHAPE only,
  so any identifier at all passed. `NAME` must now be a logical the contract declares --
  `inputs[].logical` for the source form, `outputs[].logical` for the target form. There is no
  fallback: a write to a logical this segment never declared is exactly what must be refused.
* `Session` is refused outright (bare name, attribute, or `from ... import` alias): a C4 procedure
  is HANDED a session, it never builds or fetches one, and `Session.builder.getOrCreate()` returns
  the very session `run` was given under a name no receiver-based rule was watching. Every session
  method (`sql`, `call`, `add_packages`, `udf`, `file`, `use_role`, `close`, ...) is likewise
  refused as an attribute of ANY receiver -- and so, from round 2, is the attribute `session`
  itself: `DataFrame.session` is the live session, so `df.session.write_pandas(...)` reached it
  without ever writing the bare name.
* A fixed list of DataFrame-API raw-SQL escape hatches (`sql_expr`, `call_function`, ...) is
  refused wherever it appears: as a bare name, as an attribute (`F.sql_expr`), or in a
  `from ... import ...` -- even one that is never called.
* `# tool <id>:` comments are found through the tokenizer's COMMENT tokens, not a regex over the
  raw source text, so the same text sitting inside a string literal does not count.

Task 3 fix round 2 (coordinator ruling): a fixed list of Name/Attribute checks cannot see a name
reached through `__dict__`, `__getattribute__`, or the classic `().__class__.__base__.
__subclasses__()` object-graph walk -- none of those are the forbidden name itself, `__dict__` and
friends are. So any dunder access anywhere in the module (an `ast.Attribute` whose `attr` starts
with `__`, or an `ast.Name` whose `id` does, except `__import__`, which is already forbidden by
name and keeps its own message) is refused under the existing `rule:no_io` -- the spec fixes the
rule-name list, so this is not a new rule. `startswith("__")` is the simulator's own test
(`alteryx_sim._check_python_tool_script`), so the "mirrors it" claim above is exact: a
name-mangled `__private` is refused here too (final fix wave M4).
"""
from __future__ import annotations

import ast
import io
import re
import tokenize

from .vocab import DATA_LESS_TYPES, TARGET_WRITE_MODES
from .write_modes import dag_nodes, mappings_of, target_writes

ALLOWED_MODULES = frozenset({"snowflake.snowpark", "snowflake.snowpark.functions", "snowflake.snowpark.types",
                             "pandas", "numpy", "re", "math", "datetime", "decimal"})
FORBIDDEN_NAMES = frozenset({"exec", "eval", "open", "__import__", "compile", "globals", "locals",
                             "getattr", "setattr", "delattr", "vars"})
FORBIDDEN_MODULES = frozenset({"os", "sys", "subprocess", "socket", "urllib", "requests", "http", "shutil", "pathlib"})
# DataFrame-API/Snowpark escape hatches into raw SQL text (spec §4.2, contract C3): refused by name
# wherever they appear, regardless of how they are imported or accessed.
RAW_SQL_NAMES = frozenset({"sql_expr", "call_function", "call_builtin", "function", "call_udf",
                           "call_table_function", "table_function"})
# The only two attributes `session` may ever be the receiver of (contract C4's Snowpark shape).
PERMITTED_SESSION_ATTRS = frozenset({"table", "create_dataframe"})
# The calls whose table name is checked against the contract. `saveAsTable` is a real
# `DataFrameWriter` alias of `save_as_table` in snowflake-snowpark-python, not a typo, so it is
# CHECKED like it rather than refused (review I1).
TABLE_NAME_CALLS = frozenset({"table", "save_as_table", "saveAsTable"})
# Every other way a procedure can put something somewhere. None of them writes a table this
# project's contracts can name -- they write a stage, a file, a view, a dynamic table, or rows
# into something that already exists -- so there is nothing to check and the name itself is
# refused wherever it appears (review I1, extended by round 2's R1).
FORBIDDEN_SINKS = frozenset({"insert_into", "insertInto", "copy_into_location",
                             "copyIntoLocation", "csv", "json", "parquet", "orc", "save",
                             # Round 2: `DataFrame` creates objects too, and the view forms even
                             # SUCCEED in the Local Testing Framework -- an undeclared object
                             # created, and the segment still PASSes.
                             "create_or_replace_view", "create_or_replace_temp_view",
                             "create_or_replace_dynamic_table", "write_pandas",
                             "copy_into_table", "cache_result"})
# `to_pandas()` is how row-sequential logic is expressed at all (spec §4.2), and what it returns
# is a real `pandas.DataFrame` whose own writers reach the filesystem and other services -- the
# reach the accident guard only ever caveated for the simulator. `to_pandas` itself stays legal;
# these do not (round 2, R1).
PANDAS_WRITERS = frozenset({"to_csv", "to_parquet", "to_json", "to_excel", "to_pickle", "to_sql",
                            "to_feather", "to_hdf", "to_clipboard", "to_html", "to_latex",
                            "to_markdown", "to_xml", "to_stata", "to_gbq"})
# Output targets, phase 2, Task F (phase-1 residual): the third leftover from the pandas-writers
# rule above -- a `.to_numpy()`/`.values` array is real `numpy`, and `numpy`'s own file writers
# reach the filesystem exactly the same way. `numpy` stays on `ALLOWED_MODULES` (row-sequential
# math needs it); only these write routes are refused. Ledger: filesystem-write routes through
# allowed modules -- an accident guard, not a security boundary, exactly like `PANDAS_WRITERS`.
NUMPY_WRITERS = frozenset({"savetxt", "savez", "savez_compressed", "tofile", "dump"})
# Fix round 2 (X1, R2): pandas and numpy also expose object-loading and file-reading entry points,
# not just writers. A gate-passing `proc.py` that calls one runs host code (an object load
# reconstructs an object whose reconstruction runs code) or reads a host file -- the re-review's
# `probe_rules.py`/`probe_pickle.py`. Refused by name, as an attribute or a bare name. Built from the
# probes and the two libraries' public file API. The runtime sandbox (`lib.snowpark_sandbox`) is the
# real boundary; this catches the obvious call sites before the module is ever spawned.
PANDAS_FILE_APIS = frozenset({
    "read_csv", "read_table", "read_fwf", "read_excel", "read_json", "read_html", "read_xml",
    "read_hdf", "read_feather", "read_parquet", "read_orc", "read_sas", "read_spss", "read_stata",
    "read_pickle", "read_sql", "read_sql_query", "read_sql_table", "read_gbq", "read_clipboard",
    "ExcelFile", "ExcelWriter", "HDFStore"})
NUMPY_FILE_APIS = frozenset({
    "load", "loadtxt", "genfromtxt", "fromfile", "memmap", "fromregex", "DataSource", "save"})
# The submodules that expose those entry points under another name (`np.lib.npyio.load`,
# `pd.io.pickle.read_pickle`): the namespace itself is refused as an attribute.
IO_NAMESPACES = frozenset({"io", "lib", "npyio"})
FILE_READERS = PANDAS_FILE_APIS | NUMPY_FILE_APIS | IO_NAMESPACES
# Names that must be CALLED where they are written, never handed around: an Attribute that is not
# the `func` of a Call carries the write past every argument check (review I1).
CALL_ONLY_ATTRS = frozenset({"table", "save_as_table", "saveAsTable", "create_dataframe"})
# A C4 procedure is handed a session; `Session.builder.getOrCreate()` fetches the same one under a
# name no receiver-based rule watches, so the class itself is refused (review I2).
FORBIDDEN_SESSION_NAMES = frozenset({"Session"})
# Session methods, refused as an attribute of ANY receiver (review I2). `sql` and `call` are the
# raw-SQL pair the spec names; the rest reach the account in other ways and none of them has a
# local-testing equivalent that would fail, so no local run would ever surface them.
NO_SESSION_SQL_ATTRS = frozenset({"sql", "call"})
FORBIDDEN_SESSION_ATTRS = frozenset({"add_packages", "add_import", "add_requirements", "udf",
                                     "sproc", "udtf", "udaf", "file", "query_history",
                                     "use_database", "use_schema", "use_role", "use_warehouse",
                                     "close"})
SIGNATURE = ["session", "src_db", "src_schema", "tgt_db", "tgt_schema", "run_id"]
_TOOL_COMMENT = re.compile(r"^#\s*tool\s+(\S+)\s*:")


# Fix round 4 (the sandbox attack, E1a/E4): the static gate must keep the real `sys` (and every other
# stdlib module) out of reach -- the attack read `snowflake.snowpark.types.sys` as a plain attribute,
# then `sys.modules["sqlite3"]` and `sys._getframe(1).f_locals`. Refuse these names wherever they are
# reached as an attribute of ANY receiver, as a bare name, or imported as an alias through an allowed
# package: the stdlib modules whose reach voids the import allow-list, and the frame-introspection
# attributes that walk the interpreter's stack.
REACHABLE_MODULE_NAMES = frozenset({
    "sys", "os", "nt", "posix", "modules", "builtins", "__builtins__", "importlib", "imp",
    "subprocess", "ctypes", "socket", "sqlite3", "sqlite", "marshal", "pickle", "_pickle", "shutil",
    "io", "_io", "pathlib", "tempfile", "threading", "multiprocessing", "gc", "inspect", "types",
    "traceback", "code", "codecs", "mmap", "atexit", "signal", "resource", "fcntl", "winreg",
    "_winapi", "msvcrt", "platform", "sysconfig", "site", "runpy", "pty", "glob", "fileinput",
    "linecache", "webbrowser", "ftplib", "smtplib", "telnetlib", "http", "urllib", "requests"})
# Frame / traceback / generator introspection attributes (none start with an underscore, so the
# underscore rule below does not catch them) that reach a live stack frame's names and globals.
FRAME_ATTRS = frozenset({
    "f_locals", "f_globals", "f_builtins", "f_back", "f_code", "f_lineno", "f_lasti", "f_trace",
    "f_trace_lines", "f_trace_opcodes", "gi_frame", "cr_frame", "ag_frame", "tb_frame", "tb_next",
    "gi_code", "cr_code", "ag_code", "co_consts", "co_names", "func_globals"})
# `getattr`/`setattr`/`delattr`/`vars`/`globals`/`locals` are already in FORBIDDEN_NAMES; `hasattr`
# and `dir` join them (they, too, reach names by string) -- fix round 4.
INTROSPECTION_NAMES = frozenset({"hasattr", "dir"})

# Fix round 5 (allow-lists instead of deny-lists): the gate now accepts only the surface the benign
# corpus uses, and refuses everything else. Derived from every canned Snowpark procedure, every
# cookbook Snowpark example, Task L8's Snowpark skeleton filled with the canned bodies and the
# positive-control fixtures (see `task-L4-report.md` "Fix round 5" and the method survey there): a
# deny-list loses to the next unlisted name (`sqlite3`, `pd.eval`), an allow-list does not.
#
# METHOD CALLS: a `x.m(...)` whose NAME `m` is not on this union of the Snowpark DataFrame/Column/Row
# methods, the pandas carry-over methods and the builtin str/list/dict methods is refused. So
# `.eval`, `.query`, `.pipe`, `.style`, `.plot`, `.to_pandas_batches`, `.apply`, every `to_*` writer
# (except `to_pandas`/`to_numpy`/`to_dict`) and every `read_*` reader are refused just by omission.
ALLOWED_METHODS = frozenset({
    # Snowpark session / DataFrame construction and the reads that feed row-sequential logic.
    "table", "create_dataframe", "to_pandas", "to_numpy",
    # Snowpark DataFrame transformation API.
    "filter", "where", "select", "with_column", "with_columns", "with_column_renamed",
    "drop", "drop_table", "dropna", "fillna", "distinct", "sort", "limit", "group_by", "agg",
    "join", "cross_join", "union", "union_all", "union_by_name", "except_", "intersect", "rename",
    "count", "describe", "first", "collect", "sample",
    # Snowpark Column API.
    "alias", "asc", "desc", "asc_nulls_first", "desc_nulls_last", "is_null", "is_not_null",
    "within_group", "cast", "col",
    # The one sink and the MERGE clause builders (`rule:write_mode` checks their arguments).
    "write", "mode", "save_as_table", "saveAsTable", "merge", "update", "insert", "delete",
    # pandas carry-over (`to_pandas()` -> a real frame -> row-sequential logic -> create_dataframe).
    "sort_values", "reset_index", "groupby", "iterrows", "itertuples", "to_dict", "sum",
    "isna", "notna", "random",
    # builtin str/list/dict methods the carry-over reaches for.
    "append", "get", "items", "keys", "values",
})
# MODULE ATTRIBUTES: `pd.<attr>`/`np.<attr>` (and any alias of pandas/numpy) is refused unless
# `<attr>` is on the module's own allow-list. This is the route the second escape took
# (`pd.eval(<string>, engine="python")`): `eval` is simply not here, so `pd.eval` never resolves.
PANDAS_MODULE_ATTRS = frozenset({"isna", "notna", "NA", "NaT", "DataFrame", "Series", "Timestamp"})
NUMPY_MODULE_ATTRS = frozenset({"random", "nan"})


def _refused_attr(name: str) -> bool:
    """An attribute name a gate-passing procedure may not reach on any receiver (fix round 4): every
    private/dunder name (single or double underscore), every stdlib-module name, and every
    frame-introspection attribute."""
    return name.startswith("_") or name in REACHABLE_MODULE_NAMES or name in FRAME_ATTRS


def _is_dunder(name: str) -> bool:
    """`alteryx_sim._check_python_tool_script`'s test, to the letter (final fix wave M4): any
    dunder access is a sandbox-escape vector (`__dict__`, `__getattribute__`, the
    `__class__`/`__base__`/`__subclasses__` object-graph walk, ...), and a name-mangled
    `__private` reaches just as far. `__import__` is handled before this in the Name branch,
    because it is already in FORBIDDEN_NAMES and keeps that message instead."""
    return name.startswith("__")


def _format_field_traverses(template: str) -> bool:
    """Whether a `str.format`/`format_map` template reaches an attribute (`{0.x}`, `{x.y}`) or an
    item (`{0[k]}`) -- an attribute-traversal info-leak the AST rules cannot otherwise see, because
    the traversal is text inside a string literal (fix round 4, E4)."""
    import string  # noqa: PLC0415
    try:
        for _, field_name, _, _ in string.Formatter().parse(template):
            if field_name and ("." in field_name or "[" in field_name):
                return True
    except (ValueError, IndexError):
        return True   # an unparseable template is refused rather than guessed at
    return False


def _module_allowed(name: str) -> bool:
    return name in ALLOWED_MODULES or any(name.startswith(a + ".") for a in ALLOWED_MODULES if a.count(".") == 0)


def _literal(node: ast.AST) -> str | None:
    """A string literal or an f-string whose only placeholders are the C4 parameters."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in SIGNATURE:
                parts.append("{" + v.value.id + "}")
            else:
                return None
        return "".join(parts)
    return None


def _tool_comments(source: str) -> set[str]:
    """The tool ids named in real `# tool <id>:` comments, found through the tokenizer's COMMENT
    tokens -- the same text sitting inside a string literal is not a comment and does not count."""
    commented: set[str] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                match = _TOOL_COMMENT.match(tok.string)
                if match:
                    commented.add(match.group(1))
    except (tokenize.TokenizeError, SyntaxError, IndentationError):
        pass  # ast.parse already validated the source in check_proc_py; this is defense in depth.
    return commented


def _param_names(args: ast.arguments) -> set[str]:
    """Every name `args` binds: positional, positional-only, keyword-only, *args, **kwargs."""
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


def _same_scope(node: ast.AST):
    """`node` and its descendants, without descending into a nested function/lambda/class -- the
    scope `node` itself opens, and nothing a scope nested inside it opens."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield from _same_scope(child)


def _permitted_session_uses(run_node: ast.FunctionDef) -> set[int]:
    """id()s of the `session` Name nodes that are the receiver of `session.table(...)` or
    `session.create_dataframe(...)`, written directly in `run_node` -- the only session use
    contract C4 permits. Nothing found inside a function/lambda/class nested in `run_node` counts:
    that is a separate violation (a nested scope may not reference `session` at all)."""
    permitted: set[int] = set()
    for node in _same_scope(run_node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "session"
                and node.func.attr in PERMITTED_SESSION_ATTRS):
            permitted.add(id(node.func.value))
    return permitted


def _called_attribute_ids(tree: ast.Module) -> set[int]:
    """id()s of every `ast.Attribute` that is the `func` of a `Call` -- i.e. every attribute that
    is invoked where it is written rather than referenced and handed somewhere else."""
    return {id(node.func) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}


def _session_scope_errors(tree: ast.Module, run_node: ast.FunctionDef | None) -> list[str]:
    errors: list[str] = []
    all_runs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run"]
    if len(all_runs) != 1:
        errors.append(f"rule:session_scope: exactly one `def run` may exist in the module, at top "
                      f"level (found {len(all_runs)})")
    permitted = _permitted_session_uses(run_node) if run_node is not None else set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "session" and id(node) not in permitted:
            errors.append("rule:session_scope: `session` may only be used as `session.table(...)` "
                          "or `session.create_dataframe(...)`, written directly in `run`")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not run_node:
            if "session" in _param_names(node.args):
                errors.append(f"rule:session_scope: only the top-level `run` may take `session` "
                              f"(found `{node.name}`)")
        if isinstance(node, ast.Lambda) and "session" in _param_names(node.args):
            errors.append("rule:session_scope: a lambda may not take `session`")
    return errors


# --- rule:write_mode (Task L4): a final target is written with the one call its write mode picks ---
#
# The Snowpark twin of `compile_check.py`'s `c4:write_mode`. The write mode is the contract output's
# `write_mode` (`compile_check.py` fills it from `intake/mappings.yaml` when the contract has none);
# `contract["nodes"]` (the segment's data nodes) says whether the Output tool has a PreSQL/PostSQL.

SAVE_CALLS = frozenset({"save_as_table", "saveAsTable"})
#: `Table` methods that change the table they are called on (snowflake-snowpark-python 1.55).
TABLE_WRITES = frozenset({"merge", "update", "delete"})
_SAVE_MODE = {"overwrite": "overwrite", "append": "append", "truncate_append": "truncate"}


def _required_call(mode: str, shown: str, keys: list[str]) -> str:
    if mode == "update_insert":
        join = " & ".join(f'<target>["{key}"] == <source>["{key}"]' for key in keys)
        return (f"session.table({shown}).merge(<source>, {join}, "
                f"[when_matched().update({{…}}), when_not_matched().insert({{…}})])")
    return f'.write.mode("{_SAVE_MODE[mode]}").save_as_table({shown})'


def _chained_mode(receiver: ast.AST) -> str | None:
    """The literal of `<df>.write.mode("<m>")` when `receiver` is exactly that call, else None."""
    if (isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Attribute)
            and receiver.func.attr == "mode" and isinstance(receiver.func.value, ast.Attribute)
            and receiver.func.value.attr == "write" and len(receiver.args) == 1 and not receiver.keywords
            and isinstance(receiver.args[0], ast.Constant) and isinstance(receiver.args[0].value, str)):
        return receiver.args[0].value
    return None


def _target_calls(tree: ast.Module, name: str) -> list[tuple[str, str, ast.Call]]:
    """(form, label, call) of every write to the table `name` (`{tgt_db}.{tgt_schema}.<LOGICAL>`), in
    source order: a `save_as_table(name)` (form `save:<mode>`), or `merge`/`update`/`delete` called on
    `session.table(name)` itself or on a plain name bound to it."""
    tables = {id(node) for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "table"
              and len(node.args) == 1 and _literal(node.args[0]) == name}
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and id(node.value) in tables:
            bound |= {target.id for target in node.targets if isinstance(target, ast.Name)}
        if isinstance(node, ast.AnnAssign) and node.value is not None and id(node.value) in tables \
                and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    calls = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr, receiver = node.func.attr, node.func.value
        if attr in SAVE_CALLS and len(node.args) == 1 and _literal(node.args[0]) == name:
            mode = _chained_mode(receiver)
            form = f"save:{mode.lower()}" if mode is not None else "save:"
            label = f'.mode("{mode}").{attr}' if mode is not None else f".{attr} with no .mode(…)"
            calls.append((node, form, label))
        elif attr in TABLE_WRITES and (id(receiver) in tables
                                       or (isinstance(receiver, ast.Name) and receiver.id in bound)):
            calls.append((node, attr, f".{attr}"))
    calls.sort(key=lambda call: (call[0].lineno, call[0].col_offset))
    return [(form, label, node) for node, form, label in calls]


def _column_ref(node: ast.AST) -> tuple[str, str] | None:
    """(receiver, column) of `<x>["COL"]` or `<x>.col("COL")`; None for anything else."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        receiver, column = node.value, node.slice.value
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "col"
          and len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Constant)
          and isinstance(node.args[0].value, str)):
        receiver, column = node.func.value, node.args[0].value
    else:
        return None
    # Case-insensitive, quoted or not (fix round 1, I2): contract keys are Alteryx field names verbatim.
    column = column[1:-1] if len(column) > 1 and column.startswith('"') and column.endswith('"') else column
    return ast.dump(receiver), column.upper()


def _join_keys(expr: ast.AST | None) -> list[str] | None:
    """The key columns a merge's join expression equates -- `<a>["K"] == <b>["K"]` per operand of
    `&`, two different receivers -- or None as soon as an operand is anything else."""
    if expr is None:
        return None
    operands, pending = [], [expr]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd):
            pending += [node.right, node.left]
        else:
            operands.append(node)
    keys = []
    for node in operands:
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)):
            return None
        left, right = _column_ref(node.left), _column_ref(node.comparators[0])
        if left is None or right is None or left[0] == right[0] or left[1] != right[1]:
            return None
        keys.append(left[1])
    return keys


def _merge_argument(call: ast.Call, position: int, keyword: str) -> ast.AST | None:
    if len(call.args) > position:
        return call.args[position]
    return next((kw.value for kw in call.keywords if kw.arg == keyword), None)


def _clause(clauses: ast.AST | None, factory: str, action: str) -> bool:
    """Whether `clauses` holds `<factory>(…).<action>(…)`, e.g. `when_matched().update({…})`."""
    return clauses is not None and any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == action
        and isinstance(node.func.value, ast.Call)
        and ((isinstance(node.func.value.func, ast.Name) and node.func.value.func.id == factory)
             or (isinstance(node.func.value.func, ast.Attribute) and node.func.value.func.attr == factory))
        for node in ast.walk(clauses))


def _write_mode_errors(tree: ast.Module, contract: dict) -> list[str]:
    """`rule:write_mode`: every final target is written with exactly the call its write mode
    picks -- overwrite `.write.mode("overwrite").save_as_table(...)`, append `.mode("append")`,
    truncate_append `.mode("truncate")` (Snowpark's own truncate-then-append), update_insert
    (intake's merge) one `.merge(...)` on `session.table(<target>)` joining on exactly the contract's
    keys with both `when_matched().update` and `when_not_matched().insert`. A `.update`/`.delete` of the
    target is the Output tool's PreSQL (before the write) or PostSQL (after it), allowed only when the
    tool has one."""
    by_tool = {str(node.get("tool_id")): node.get("config") or {} for node in contract.get("nodes") or []}
    errors = []
    for output in contract.get("outputs") or []:
        logical = output.get("logical")
        if output.get("kind") != "target" or not logical:
            continue
        spelling = output.get("write_mode")
        mode = TARGET_WRITE_MODES.get(str(spelling)) if spelling else None
        keys = [str(key) for key in output.get("keys") or []]
        if mode is None:
            errors.append(f"rule:write_mode: target {logical} has no known write mode (the contract says "
                          f"{spelling!r}; one of {', '.join(sorted(TARGET_WRITE_MODES))})")
            continue
        if mode == "update_insert" and not keys:
            errors.append(f"rule:write_mode: target {logical} is {spelling} and the contract names no keys for "
                          f"its merge to join on")
            continue
        name = "{tgt_db}.{tgt_schema}." + logical
        shown = f'f"{name}"'
        head = f"rule:write_mode: {logical} is {spelling}: write it with {_required_call(mode, shown, keys)}"
        calls = _target_calls(tree, name)
        if not calls:
            errors.append(f"{head}; the module never writes {shown}")
            continue
        found = " + ".join(label for _, label, _ in calls)
        wanted = "merge" if mode == "update_insert" else f"save:{_SAVE_MODE[mode]}"
        core = [i for i, (form, _, _) in enumerate(calls) if form == wanted]
        if not core:
            errors.append(f"{head}, not {found}")
            continue
        if len(core) > 1:
            errors.append(f"{head} once, not {found}")
            continue
        at = core[0]
        config = by_tool.get(str(output.get("tool_id")), {})
        tool = output.get("tool_id")
        problems = []
        if at > 0 and not str(config.get("pre_sql") or "").strip():
            problems.append(f"a call before the write is allowed only as tool {tool}'s PreSQL, and it has none")
        if calls[at + 1:] and not str(config.get("post_sql") or "").strip():
            problems.append(f"a call after the write is allowed only as tool {tool}'s PostSQL, and it has none")
        if problems:
            errors.append(f"{head} alone, not {found} ({'; '.join(problems)})")
            continue
        if mode != "update_insert":
            continue
        merge = calls[at][2]
        join = _merge_argument(merge, 1, "join_expr")
        joined = _join_keys(join)
        if joined is None or set(joined) != {key.upper() for key in keys}:
            written = ast.unparse(join) if join is not None else "(no join expression)"
            errors.append(f"rule:write_mode: {logical} is {spelling}: the merge into {shown} must join on exactly "
                          f"the contract's keys {', '.join(keys)} -- <target>[\"<KEY>\"] == <source>[\"<KEY>\"] "
                          f"for each key, joined by &, nothing else -- not {written}")
        clauses = _merge_argument(merge, 2, "clauses")
        if not (_clause(clauses, "when_matched", "update") and _clause(clauses, "when_not_matched", "insert")):
            errors.append(f"rule:write_mode: {logical} is {spelling}: the merge into {shown} needs both "
                          f"when_matched().update(…) and when_not_matched().insert(…) (Update; Insert if new)")
    return errors


def check_proc_py(source: str, wf_id: str, seg: str, contract: dict) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"rule:syntax: {exc.msg} (line {exc.lineno})"]
    wf_u, seg_u = wf_id.upper().replace("_", ""), seg.upper()
    own = f"MIG_WORK.{wf_u}_{seg_u}_OUT"
    # Contract C3: a work stream's table is `MIG_WORK.<WF>_<SEG>_OUT` for the first, `…_OUT_<STREAM>`
    # for any other -- the contract's own `table`, which every validator splices through
    # `lib.backend.qualified` (plain UPPER-case identifiers only), so `contract_scaffold.py` writes the
    # stream upper-cased. Snowflake folds an unquoted identifier to upper case, so a name is allowed
    # in any case (live-hardening L3, fix round 1, I1): the contract's spelling and the raw stream's.
    work = [o for o in contract.get("outputs", []) if isinstance(o, dict) and o.get("kind") == "work"]
    allowed_tables = {own} | {f"{own}_{o.get('stream')}" for o in work}
    allowed_tables |= {o["table"] for o in work if isinstance(o.get("table"), str)}
    allowed_tables |= {i["table"] for i in contract.get("inputs", []) if isinstance(i.get("table"), str)}
    allowed_upper = {table.upper() for table in allowed_tables}
    # The mapped names this segment declares. A `{src_db}`/`{tgt_db}` f-string may only name one
    # of these (review I4): shape alone let any identifier at all through, in either direction.
    src_logicals = {i["logical"] for i in contract.get("inputs", []) if i.get("logical")}
    tgt_logicals = {o["logical"] for o in contract.get("outputs", []) if o.get("logical")}
    src_form = re.compile(r"^\{src_db\}\.\{src_schema\}\.([A-Z_][A-Z0-9_$]*)$")
    tgt_form = re.compile(r"^\{tgt_db\}\.\{tgt_schema\}\.([A-Z_][A-Z0-9_$]*)$")

    def table_name_error(text: str) -> str | None:
        if text.upper() in allowed_upper:
            return None
        for form, logicals, field in ((src_form, src_logicals, "inputs"), (tgt_form, tgt_logicals, "outputs")):
            match = form.match(text)
            if match:
                if match.group(1) in logicals:
                    return None
                return (f"rule:table_names: `{text}` names no `{field}[].logical` of this "
                        f"segment's contract")
        return f"rule:table_names: `{text}` is not this segment's input, output or a C4 source/target form"

    top_level_runs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"]
    run_node: ast.FunctionDef | None = None
    if len(top_level_runs) != 1 or [a.arg for a in top_level_runs[0].args.args] != SIGNATURE:
        errors.append(f"rule:signature: expected exactly one `def run({', '.join(SIGNATURE)})`")
    else:
        run_node = top_level_runs[0]
    errors.extend(_session_scope_errors(tree, run_node))

    # Fix round 5: the names bound to the `pandas`/`numpy` modules (`import pandas as pd`,
    # `import numpy`), so their module attributes can be held to a per-module allow-list.
    pandas_aliases: set[str] = set()
    numpy_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == "pandas":
                    pandas_aliases.add(alias.asname or alias.name)
                elif top == "numpy":
                    numpy_aliases.add(alias.asname or alias.name)

    called_attributes = _called_attribute_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if name.split(".")[0] in FORBIDDEN_MODULES or not _module_allowed(name):
                    errors.append(f"rule:imports: `{name}` is not on the allow-list")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in RAW_SQL_NAMES:
                        errors.append(f"rule:no_raw_sql: `{alias.name}` is not allowed")
                    if alias.name in FORBIDDEN_SESSION_NAMES:
                        errors.append(f"rule:session_scope: `{alias.name}` is not allowed; a C4 "
                                      f"procedure is handed its session, it never builds one")
                    # Fix round 4 (E1a): `from snowflake.snowpark.types import sys` aliases the real
                    # stdlib module through an allowed package.
                    if alias.name in REACHABLE_MODULE_NAMES or alias.name.startswith("_"):
                        errors.append(f"rule:no_io: `{alias.name}` is a stdlib module or private name "
                                      f"reached through an allowed package; it is not allowed")
        # Fix round 4 (E4): a def/class named with a leading underscore (a dunder method like
        # `__del__`, or any private helper) is refused -- a finalizer runs at shutdown while the hook
        # is armed, and a private name is a reach primitive.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_") and node.name != "run":
                errors.append(f"rule:no_io: a definition named `{node.name}` is not allowed "
                              f"(private or dunder name)")
        # Fix round 4 (E4): a `.format`/`.format_map` whose template traverses attributes or items.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("format", "format_map")):
            template = node.func.value.value if isinstance(node.func.value, ast.Constant) else None
            if isinstance(template, str) and _format_field_traverses(template):
                errors.append("rule:no_io: a format string that traverses attributes or items "
                              f"(`{template[:40]}`) is not allowed")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _format_field_traverses(node.value):
            errors.append("rule:no_io: a format template that traverses attributes or items "
                          f"(`{node.value[:40]}`) is not allowed")
        if isinstance(node, ast.Name):
            if node.id in INTROSPECTION_NAMES:
                errors.append(f"rule:no_io: `{node.id}` reaches names by string; it is not allowed")
            if node.id in REACHABLE_MODULE_NAMES:
                errors.append(f"rule:no_io: `{node.id}` names a stdlib module; it is not allowed")
            if node.id in PANDAS_WRITERS:
                errors.append(f"rule:no_io: `{node.id}` writes outside this segment's declared "
                              f"output; a C4 procedure's only sink is "
                              f"`.write.mode(...).save_as_table(...)`")
            if node.id in NUMPY_WRITERS:
                errors.append(f"rule:no_io: `{node.id}` writes outside this segment's declared "
                              f"output; a C4 procedure's only sink is "
                              f"`.write.mode(...).save_as_table(...)`")
            if node.id in FILE_READERS:
                errors.append(f"rule:no_io: `{node.id}` reads or writes a host file (a pandas/numpy "
                              f"file API); a C4 procedure's only I/O is the session's tables")
            if node.id in FORBIDDEN_NAMES:
                errors.append(f"rule:no_io: `{node.id}` is not allowed")
            elif _is_dunder(node.id):
                errors.append(f"rule:no_io: dunder attribute access `{node.id}` is not allowed "
                              f"(line {node.lineno})")
            if node.id in RAW_SQL_NAMES:
                errors.append(f"rule:no_raw_sql: `{node.id}` is not allowed")
            if node.id in FORBIDDEN_SINKS:
                errors.append(f"rule:no_io: `{node.id}` is not a permitted write; a C4 procedure's "
                              f"only sink is `.write.mode(...).save_as_table(...)`")
            if node.id in FORBIDDEN_SESSION_NAMES:
                errors.append(f"rule:session_scope: `{node.id}` is not allowed; a C4 procedure is "
                              f"handed its session, it never builds one")
        # Fix round 5: a `pd.`/`np.` module attribute is refused unless it is on the module's own
        # allow-list (`pd.eval`, `pd.read_csv`, `np.load`, `np.savetxt` are all off it). Checked on
        # the direct-alias form (`Attribute` whose value is a Name bound to pandas/numpy) so a chained
        # `pd.io.pickle` is refused at `pd.io` (io is not an allowed pandas attribute).
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and not node.attr.startswith("_")):
            if node.value.id in pandas_aliases and node.attr not in PANDAS_MODULE_ATTRS:
                errors.append(f"rule:no_io: `pandas.{node.attr}` is not on the pandas allow-list "
                              f"({', '.join(sorted(PANDAS_MODULE_ATTRS))}); a C4 procedure's only I/O "
                              f"is the session's tables (line {node.lineno})")
            if node.value.id in numpy_aliases and node.attr not in NUMPY_MODULE_ATTRS:
                errors.append(f"rule:no_io: `numpy.{node.attr}` is not on the numpy allow-list "
                              f"({', '.join(sorted(NUMPY_MODULE_ATTRS))}); a C4 procedure's only I/O "
                              f"is the session's tables (line {node.lineno})")
        # Fix round 5: `engine="python"` runs a string in the Python engine (`pd.eval`/`DataFrame.eval`
        # /`DataFrame.query`), the AST gate never sees the string -- refused wherever it appears.
        if isinstance(node, ast.keyword) and node.arg == "engine" and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) and node.value.value == "python":
            errors.append("rule:no_io: `engine=\"python\"` executes a string the gate never sees; "
                          "it is not allowed")
        # Fix round 5: a method call `x.m(...)` whose NAME is not on `ALLOWED_METHODS` is refused --
        # the gate is an allow-list, not a deny-list. Private/dunder names are handled above (they
        # keep their own message); the raw-SQL and session methods keep theirs too and are simply
        # also absent here.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and not node.func.attr.startswith("_")
                and node.func.attr not in ALLOWED_METHODS
                and node.func.attr not in RAW_SQL_NAMES and node.func.attr not in NO_SESSION_SQL_ATTRS
                and node.func.attr not in FORBIDDEN_SESSION_ATTRS):
            errors.append(f"rule:no_io: method `.{node.func.attr}(...)` is not on the Snowpark/pandas "
                          f"allow-list; a C4 procedure uses only the Snowpark DataFrame/Column API and "
                          f"the pandas carry-over methods (line {node.func.lineno})")
        if isinstance(node, ast.Attribute):
            if node.attr in RAW_SQL_NAMES:
                errors.append(f"rule:no_raw_sql: `{node.attr}` is not allowed")
            if _refused_attr(node.attr):
                # Fix round 4 (E1a/E3/E4): a private/dunder name, a stdlib-module name (`.sys`,
                # `.os`, `.sqlite3`, `.modules`), or a frame-introspection attribute (`.f_locals`)
                # reached on any receiver -- the whole `sys.modules`/frame-walk reach.
                errors.append(f"rule:no_io: attribute access `.{node.attr}` is not allowed "
                              f"(a private, module or frame name -- line {node.lineno})")
            if node.attr in FORBIDDEN_SINKS:
                errors.append(f"rule:no_io: `{node.attr}` is not a permitted write; a C4 "
                              f"procedure's only sink is `.write.mode(...).save_as_table(...)` "
                              f"(line {node.lineno})")
            if node.attr in PANDAS_WRITERS:
                errors.append(f"rule:no_io: `{node.attr}` writes outside this segment's declared "
                              f"output; a C4 procedure's only sink is "
                              f"`.write.mode(...).save_as_table(...)` (line {node.lineno})")
            if node.attr in NUMPY_WRITERS:
                errors.append(f"rule:no_io: `{node.attr}` writes outside this segment's declared "
                              f"output; a C4 procedure's only sink is "
                              f"`.write.mode(...).save_as_table(...)` (line {node.lineno})")
            if node.attr in FILE_READERS:
                errors.append(f"rule:no_io: `{node.attr}` reads or writes a host file (a pandas/numpy "
                              f"file API); a C4 procedure's only I/O is the session's tables "
                              f"(line {node.lineno})")
            if node.attr == "session":
                # `DataFrame.session` IS the live session (round 2, R1): reading it off anything
                # hands the module the very session `run` was given, under a name the bare-Name
                # rule below never sees. The bare-Name rule is unchanged and still the one that
                # licenses `session.table(...)` / `session.create_dataframe(...)`.
                errors.append(f"rule:session_scope: `session` may not be read off another object "
                              f"(line {node.lineno}); it is only ever the receiver of "
                              f"`session.table(...)` or `session.create_dataframe(...)` in `run`")
            if node.attr in NO_SESSION_SQL_ATTRS:
                errors.append(f"rule:no_session_sql: `.{node.attr}(...)` is not allowed on any "
                              f"receiver; use the DataFrame API (line {node.lineno})")
            if node.attr in FORBIDDEN_SESSION_ATTRS:
                errors.append(f"rule:no_io: `{node.attr}` is not allowed on any receiver "
                              f"(line {node.lineno})")
            if node.attr in FORBIDDEN_SESSION_NAMES:
                errors.append(f"rule:session_scope: `{node.attr}` is not allowed; a C4 procedure "
                              f"is handed its session, it never builds one")
            if node.attr in CALL_ONLY_ATTRS and id(node) not in called_attributes:
                errors.append(f"rule:table_names: `{node.attr}` must be called directly, not "
                              f"referenced (line {node.lineno})")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in TABLE_NAME_CALLS:
            if node.keywords:
                errors.append(f"rule:table_names: `{node.func.attr}` must not use keyword arguments")
            elif len(node.args) != 1:
                errors.append(f"rule:table_names: `{node.func.attr}` needs exactly one positional argument")
            else:
                text = _literal(node.args[0])
                if text is None:
                    errors.append(f"rule:table_names: `{node.func.attr}` needs a literal or a C4-parameter f-string")
                else:
                    bad_name = table_name_error(text)
                    if bad_name:
                        errors.append(bad_name)
    errors.extend(_write_mode_errors(tree, contract))
    tools = {str(n["tool_id"]) for n in contract.get("nodes", [])} if contract.get("nodes") else set()
    commented = _tool_comments(source)
    for tool_id in sorted(tools - commented):
        errors.append(f"rule:tool_comments: no `# tool {tool_id}:` comment")
    if not tools and not commented:
        errors.append("rule:tool_comments: no `# tool <id>:` comment at all")
    return sorted(set(errors))


def segment_rule_errors(repo, wf_id: str, seg: str, contract: dict, source: str) -> list[str]:
    """The whole Snowpark gate, as `compile_check.py --target snowpark` applies it and as
    `validate_snowpark.py` applies it before a module is ever imported (live hardening L4 fix round 1,
    I1): `check_proc_py` against the contract, with the segment's data nodes (`dag.json` minus
    `DATA_LESS_TYPES`, for the tool comments and the PreSQL/PostSQL of `rule:write_mode`) and each
    final target's write mode resolved by `lib.write_modes.target_writes` (the contract's
    `write_mode`, else `intake/mappings.yaml`'s). Raises `write_modes.ContractError` -- a usage error
    -- for a target with no known write mode or a merge target with no keys."""
    nodes = dag_nodes(repo, wf_id, seg)
    resolved = {target.logical: target for target in target_writes(contract, mappings_of(repo, wf_id), nodes)}
    outputs = [{**output, "write_mode": resolved[output["logical"]].spelling,
                "keys": list(resolved[output["logical"]].keys)}
               if output.get("kind") == "target" and output.get("logical") in resolved else output
               for output in contract.get("outputs") or []]
    data_nodes = [node for node in nodes if node.get("type") not in DATA_LESS_TYPES]
    return check_proc_py(source, wf_id, seg, {**contract, "outputs": outputs, "nodes": data_nodes})
