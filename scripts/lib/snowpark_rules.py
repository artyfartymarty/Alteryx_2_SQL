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


def _is_dunder(name: str) -> bool:
    """`alteryx_sim._check_python_tool_script`'s test, to the letter (final fix wave M4): any
    dunder access is a sandbox-escape vector (`__dict__`, `__getattribute__`, the
    `__class__`/`__base__`/`__subclasses__` object-graph walk, ...), and a name-mangled
    `__private` reaches just as far. `__import__` is handled before this in the Name branch,
    because it is already in FORBIDDEN_NAMES and keeps that message instead."""
    return name.startswith("__")


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


def check_proc_py(source: str, wf_id: str, seg: str, contract: dict) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"rule:syntax: {exc.msg} (line {exc.lineno})"]
    wf_u, seg_u = wf_id.upper().replace("_", ""), seg.upper()
    own = f"MIG_WORK.{wf_u}_{seg_u}_OUT"
    allowed_tables = {own} | {f"{own}_{o['stream']}" for o in contract.get("outputs", []) if o.get("kind") == "work"}
    allowed_tables |= {i["table"] for i in contract.get("inputs", []) if i.get("table")}
    # The mapped names this segment declares. A `{src_db}`/`{tgt_db}` f-string may only name one
    # of these (review I4): shape alone let any identifier at all through, in either direction.
    src_logicals = {i["logical"] for i in contract.get("inputs", []) if i.get("logical")}
    tgt_logicals = {o["logical"] for o in contract.get("outputs", []) if o.get("logical")}
    src_form = re.compile(r"^\{src_db\}\.\{src_schema\}\.([A-Z_][A-Z0-9_$]*)$")
    tgt_form = re.compile(r"^\{tgt_db\}\.\{tgt_schema\}\.([A-Z_][A-Z0-9_$]*)$")

    def table_name_error(text: str) -> str | None:
        if text in allowed_tables:
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
        if isinstance(node, ast.Name):
            if node.id in PANDAS_WRITERS:
                errors.append(f"rule:no_io: `{node.id}` writes outside this segment's declared "
                              f"output; a C4 procedure's only sink is "
                              f"`.write.mode(...).save_as_table(...)`")
            if node.id in NUMPY_WRITERS:
                errors.append(f"rule:no_io: `{node.id}` writes outside this segment's declared "
                              f"output; a C4 procedure's only sink is "
                              f"`.write.mode(...).save_as_table(...)`")
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
        if isinstance(node, ast.Attribute):
            if node.attr in RAW_SQL_NAMES:
                errors.append(f"rule:no_raw_sql: `{node.attr}` is not allowed")
            if _is_dunder(node.attr):
                errors.append(f"rule:no_io: dunder attribute access `{node.attr}` is not allowed "
                              f"(line {node.lineno})")
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
    tools = {str(n["tool_id"]) for n in contract.get("nodes", [])} if contract.get("nodes") else set()
    commented = _tool_comments(source)
    for tool_id in sorted(tools - commented):
        errors.append(f"rule:tool_comments: no `# tool {tool_id}:` comment")
    if not tools and not commented:
        errors.append("rule:tool_comments: no `# tool <id>:` comment at all")
    return sorted(set(errors))
