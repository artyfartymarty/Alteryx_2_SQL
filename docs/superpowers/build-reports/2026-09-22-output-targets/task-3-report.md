# Task 3 report: Snowpark procedure rules, renderer, `compile_check.py --target`

Worktree: `C:\Users\<user>\Desktop\Alteryx to Snowflake\.worktrees\ot-task-3` (branch `wt/ot-task-3`)

## Commits

1. `5109cc3` fix: simulate absent snowflake-connector-python in backend test (add-on, own commit)
2. `8c1fcd9` wip: Snowpark procedure rules, proc.py -> proc.sql renderer, Snowpark type mapping
3. `45b6e36` feat: compile_check.py --target snowpark

## What was implemented

### Add-on: `tests/test_backend.py::test_snowflake_backend_fails_clearly_without_connector`

`snowflake-snowpark-python` (needed for this task) pulls in `snowflake-connector-python` as a
transitive dependency, so the test's premise ("the connector is absent") no longer held in any
worktree's venv, and the real `snowflake.connector.connect()` call failed with a connection-config
error instead of the `BackendError` the test wanted. Fixed by simulating absence at the exact
import site `backend.py` uses (`import snowflake.connector` inside `SnowflakeBackend.__init__`):
`monkeypatch.setitem(sys.modules, "snowflake.connector", None)`. Python's import machinery raises
`ImportError` immediately when a module name is present in `sys.modules` mapped to `None`, so this
works regardless of whether the connector is actually installed, and the test still asserts the
backend fails clearly (`BackendError` matching "snowflake-connector-python").

### Step 1/2 (brief, verbatim): `scripts/lib/snowpark_rules.py`, `scripts/render_snowpark.py`

Copied verbatim from `task-3-brief.md`'s Step 2 code blocks, as instructed ("it holds the code and
tests to use verbatim"). No changes needed to either file to make their tests pass.

### `scripts/lib/proc_runner.py`

- `ProcInfo` gained `language: str = "SQL"`.
- New `_LANGUAGE_RE` matches `LANGUAGE (SQL|PYTHON)` in the header (shared with `_HEADER_RE`/
  `_EXECUTE_AS_RE`, which already scan the same header text).
- `parse_proc`: when the header says `LANGUAGE PYTHON`, the C4 header (name, params, execute_as)
  is parsed exactly as before, but the `$$…$$` body is *not* run through `_split_code`/the
  scripting-keyword check -- it is kept whole as a single-element `statements` list, since it's a
  Python module, not SQL. `session` stays `{}` for a Python body (no `ALTER SESSION`).
- Every existing `test_proc_runner.py` test stays green (language defaults to `"SQL"`, and the SQL
  branch is untouched).
- Added a short paragraph to the module docstring describing the `LANGUAGE PYTHON` shape, since the
  original docstring described only the SQL shape as "the one stored-procedure shape."

### `scripts/lib/types_map.py`: `alteryx_to_snowpark`

Maps the same Alteryx field families as `alteryx_to_snowflake`, as instances of
`snowflake.snowpark.types` classes (`LongType`, `DoubleType`, `DecimalType`, `BooleanType`,
`DateType`, `TimestampType`, `TimeType`, `StringType`), importing `snowflake.snowpark.types`
lazily inside the function so `types_map` (and everything that imports it, notably `backend.py`)
stays importable with no Snowpark installed.

**Deviation from `alteryx_to_snowflake`'s string handling** (see "Deviations" below):
`alteryx_to_snowpark` sizes all four Alteryx string types (`String`, `WString`, `V_String`,
`V_WString`) uniformly from `field["size"]`, per the brief's literal wording ("strings ->
`StringType(size)` (or `StringType()` when size is None)"). `alteryx_to_snowflake` instead leaves
`V_String`/`V_WString` unsized (`VARCHAR` with no length) and only sizes `String`/`WString`.

### `scripts/compile_check.py`: `--target`

- `compile_check(repo, wf_id, seg, target="auto")`: reads `contract.json` first (still raises
  `FileNotFoundError` naming the path if absent, same wording as before: "has no contract to
  check"); resolves `"auto"` to `contract.get("target") or "sql"` (so every existing segment
  without a `"target"` field keeps behaving exactly as before -- confirmed by the unmodified
  `test_compile_check.py` suite staying green, including the exact-equality assertion in
  `test_a_matching_procedure_compiles`, which has no `"target"` key in its expected dict).
- `snowpark` path (`_check_snowpark`): reads `proc.py` (missing -> `FileNotFoundError`, "has no
  procedure module to check", -> CLI exit 2); builds `data_nodes` from `segments/<seg>/dag.json`'s
  `nodes[]`, filtered to the brief's literal list `{container, comment, interface, action,
  browse}` (a local `_NON_DATA_NODE_TYPES` constant -- deliberately not `lib.vocab.NON_DATA_TYPES`,
  which is missing `"browse"`; commented as such) -- empty when `dag.json` doesn't exist; calls
  `snowpark_rules.check_proc_py(source, wf_id, seg, {**contract, "nodes": data_nodes})`; separately
  checks `proc.sql` is in sync with `render_snowpark.render(proc.py, wf_id, seg, runtime)` (runtime
  from `mappings/global.yaml`'s `program.snowpark_runtime`, default `"3.11"`, read defensively --
  missing `global.yaml` is not fatal here, unlike in `render_snowpark.py`'s own CLI) and, when
  `proc.sql` exists and parses, its own C4 signature via `parse_proc` + the existing
  `_signature_errors`. Report: `{"status", "target": "snowpark", "errors", "statements": 0}`.
- `sql` path: unchanged (`_check`), no `"target"` key added to its report.
- CLI: `--target {auto,sql,snowpark}`, default `auto`, threaded into `compile_check(...)`.

## TDD evidence

### RED (add-on fix)

```
.venv/Scripts/python.exe -m pytest tests/test_backend.py::test_snowflake_backend_fails_clearly_without_connector
```
```
F
...
E           snowflake.connector.errors.Error: Default connection with name 'default' cannot be found, known ones are []
1 failed in 1.09s
```
Expected: the connector *is* installed in this venv (a transitive dependency of
`snowflake-snowpark-python`), so `get_backend("snowflake")` reaches a real (and different) connector
error instead of the `BackendError` the test wants.

### GREEN (add-on fix)

```
.venv/Scripts/python.exe -m pytest tests/test_backend.py
```
```
................................................                         [100%]
48 passed in 0.93s
```

### RED (Task 3, Step 1 -- new modules missing)

```
.venv/Scripts/python.exe -m pytest tests/test_snowpark_rules.py tests/test_render_snowpark.py tests/test_compile_check_snowpark.py
```
```
ERROR collecting tests/test_snowpark_rules.py
E   ImportError: cannot import name 'snowpark_rules' from 'lib' (...\scripts\lib\__init__.py)
ERROR collecting tests/test_render_snowpark.py
E   ModuleNotFoundError: No module named 'render_snowpark'
ERROR collecting tests/test_compile_check_snowpark.py
E   ModuleNotFoundError: No module named 'render_snowpark'
3 errors in 0.24s
```

### GREEN, incremental

After `snowpark_rules.py`:
```
.venv/Scripts/python.exe -m pytest tests/test_snowpark_rules.py tests/test_render_snowpark.py
```
```
..........F..                                                            [100%]
1 failed, 12 passed in 1.49s   (only test_render_is_deterministic_..., needs ProcInfo.language)
```

After `render_snowpark.py` + `proc_runner.py` (`ProcInfo.language`, `LANGUAGE PYTHON` parsing):
```
.venv/Scripts/python.exe -m pytest tests/test_proc_runner.py tests/test_render_snowpark.py tests/test_snowpark_rules.py
```
```
..................................                                       [100%]
34 passed in 1.65s
```

After `types_map.alteryx_to_snowpark` + its `test_backend.py` coverage:
```
.venv/Scripts/python.exe -m pytest tests/test_backend.py
```
```
..................................................................       [100%]
66 passed in 1.70s
```

After `compile_check.py --target snowpark`:
```
.venv/Scripts/python.exe -m pytest tests/test_compile_check_snowpark.py tests/test_compile_check.py
```
```
.......................                                                  [100%]
23 passed in 2.14s
```

### GREEN, full suite (final, after every commit)

```
.venv/Scripts/python.exe -m pytest
```
```
........................................................................ [ ...]
...
....................................                                     [100%]
1044 passed in 60.19s (0:01:00)
```

Baseline before this task was 1005 passed. New tests: `test_snowpark_rules.py` (10),
`test_render_snowpark.py` (3), `test_compile_check_snowpark.py` (8, one more than the brief's prose
minimum -- see below), `test_backend.py` additions for `alteryx_to_snowpark` (18) = 39 new passing
tests; 1005 + 39 = 1044. 0 skipped, 0 failed, no warnings emitted at any point.

## Deviations from the brief (and why)

1. **`tests/test_compile_check_snowpark.py` has no literal code in the brief** (only a prose
   description, unlike the other two new test files). I wrote it from that prose, following the
   existing `tests/test_compile_check.py`'s style (`build()` helper, `run_cli()` subprocess
   helper). It imports `CONTRACT`/`GOOD` from `test_snowpark_rules` (as the brief's prose says:
   "the `GOOD` proc.py from the rules test (import it)") -- this works because pytest's default
   "prepend" import mode puts `tests/` on `sys.path` (no `tests/__init__.py` exists), which I
   confirmed empirically rather than assuming.
   I added one test beyond the prose's list:
   `test_a_proc_py_that_cannot_be_rendered_is_a_render_mismatch` -- proc.py mutated (after a
   successful render) to contain `$$`, so `render_snowpark.render()` raises `ValueError`.
   `_render_errors` catches this and turns it into one more `rule:render_mismatch` entry rather
   than letting the exception escape into an exit-2 crash. This is prose-adjacent behavior
   (`render()`'s `$$` refusal is directly tested at the function level in
   `test_render_snowpark.py`, but reaching it *through* `compile_check` was untested) --
   implementer-rules.md item 9 ("add further tests for behaviour the brief describes in prose but
   does not test").

2. **`types_map.alteryx_to_snowpark`'s string sizing** treats all four Alteryx string type names
   (`String`, `WString`, `V_String`, `V_WString`) the same way -- sized from `field["size"]` when
   present, else unsized -- rather than mirroring `alteryx_to_snowflake`'s SQL-only split (which
   leaves `V_String`/`V_WString` deliberately unsized, per that function's own comment about DuckDB
   not enforcing length). The brief's spec for `alteryx_to_snowpark` states one rule for "strings"
   generically ("strings -> `StringType(size)` (or `StringType()` when size is None)") without
   naming `V_String`/`V_WString` as a separate case the way `_SIZED_STRING_TYPES` does for the SQL
   function, so I took the brief's wording at face value. Documented in a comment on the new
   `_STRING_TYPES` constant in `types_map.py`. **This is the one place I'm least certain I read the
   brief's intent correctly** -- flagging for review.

3. **`render_snowpark.py`'s CLI exit code for a `$$`-containing `proc.py`** is `2`, not `1`, per the
   brief's given code verbatim (the `ValueError` from `render()` is caught in the same
   `except (FileNotFoundError, ValueError)` block as the missing-file case). This reads as a
   "domain failure" under implementer-rules.md's general convention (exit 1), not a "missing
   prerequisite" (exit 2), but the brief instructs using its code as given and no test in the brief
   (or that I added) requires exit 1 here -- `test_a_proc_py_containing_dollar_dollar_is_refused`
   only exercises `render()` directly, not the CLI. Kept as given rather than silently "fixing" it;
   flagging for review since it's a real (if minor) inconsistency with the project-wide exit-code
   rule.

## Self-review concerns

- The `render_snowpark.py` exit-code point above (item 3) -- worth a decision from the coordinator
  on whether the brief's code should be amended, since it's an explicit house rule
  (implementer-rules.md) and the given code predates it or simply didn't consider this case.
- The `alteryx_to_snowpark` string-sizing point above (item 2) -- worth a second read of
  task-3-brief.md's intent, or the design doc it's implementing, if precision here matters for a
  later task (e.g. if a golden CSV's Snowpark schema needs to match a hand-written procedure's
  `VARCHAR(n)` exactly for `V_String` columns).
- `_data_nodes` treats a missing `dag.json` as "no nodes" rather than an error. This wasn't
  specified by the brief either way; it seemed the more useful default (a snowpark segment's
  `dag.json` may not always be present depending on how far the pipeline has run), and
  `check_proc_py`'s existing "no tools and no comments -> one violation" fallback still catches a
  procedure with literally no `# tool N:` comments in that case.
- `compile_check.py`'s snowpark path reads `mappings/global.yaml` defensively (missing file ->
  `program = {}` -> runtime defaults to `"3.11"`), which is *stricter* than `render_snowpark.py`'s
  own CLI (missing `global.yaml` there is a hard `FileNotFoundError` -> exit 2). I chose this
  because by the time `compile_check` runs, `proc.sql` should already have been rendered
  successfully (which does require `global.yaml`), so re-deriving the same runtime without
  re-imposing that hard requirement seemed right for a read-only check -- but it's a second place
  (with item 2 above) where I made a judgment call the brief didn't spell out.

## Files changed

- `scripts/lib/snowpark_rules.py` (new)
- `scripts/render_snowpark.py` (new)
- `scripts/lib/proc_runner.py` (modified: `ProcInfo.language`, `LANGUAGE PYTHON` parsing, docstring)
- `scripts/lib/types_map.py` (modified: `alteryx_to_snowpark`, `_STRING_TYPES`)
- `scripts/compile_check.py` (modified: `--target`, `_check_snowpark`, `_data_nodes`, `_render_errors`)
- `tests/test_snowpark_rules.py` (new, verbatim from brief)
- `tests/test_render_snowpark.py` (new, verbatim from brief)
- `tests/test_compile_check_snowpark.py` (new, written from the brief's prose)
- `tests/test_backend.py` (modified: connector-absence simulation; new `alteryx_to_snowpark` coverage)

## Final state (before fix round 1)

`git status` is clean after each commit; three commits total (one add-on fix, two feat/wip for
Task 3 itself -- squashing the wip into the feat commit is left to the coordinator's judgement per
`finishing-a-development-branch`, since implementer-rules.md says "squash or follow up at the
end" and review happens before that step). Full suite: 1044 passed, 0 skipped, 0 failed, no
warnings.

---

## Fix round 1

Commit `0d674fb` "fix: make Snowpark AST rules conservative and structural (fix round 1)".

Review found the rule set from the initial pass evadable by ordinary Python -- a defect in the
brief's own code (`task-3-brief.md` Step 2's `snowpark_rules.py`, copied verbatim), not something
introduced during implementation. Reproduced with
`scratchpad/ot-rev3/probe_rules.py` and `probe_rules2.py`; every probe now reports a violation
(re-run after the fix, output included below). The coordinator's ruling is binding and is
implemented as given, including two earlier rulings folded into this round.

### File:line changes

- `scripts/lib/snowpark_rules.py` -- rewritten. Line references are to the file after this
  round's changes.
  - Module docstring (1-24): rewritten to describe the structural rules.
  - `FORBIDDEN_NAMES` (29-30): added `getattr`, `setattr`, `delattr`, `vars` (ruling (b), C1).
  - `FORBIDDEN_SESSION` constant: removed (superseded by the structural session_scope check).
  - `RAW_SQL_NAMES` (32-34, new): `sql_expr`, `call_function`, `call_builtin`, `function`,
    `call_udf`, `call_table_function`, `table_function` (ruling (d), C3).
  - `PERMITTED_SESSION_ATTRS` (36, new): `{"table", "create_dataframe"}`.
  - `_TOOL_COMMENT` (38): dropped `re.MULTILINE` and the leading `^\s*` slack -- it now matches a
    single tokenize COMMENT token's own text, not a line inside the whole source.
  - `_tool_comments` (67-79, new): walks `tokenize.generate_tokens`, collects `# tool <id>:` from
    `tokenize.COMMENT` tokens only (ruling (e), I1). A string literal containing the same text
    produces no COMMENT token, so it is invisible to this function.
  - `_param_names` (82-88, new): every name a `def`/`lambda` parameter list binds (positional,
    positional-only, keyword-only, `*args`, `**kwargs`).
  - `_same_scope` (91-96, new): yields a node and its descendants without descending into a
    nested `FunctionDef`/`AsyncFunctionDef`/`Lambda`/`ClassDef` -- the lexical scope a node opens.
  - `_permitted_session_uses` (99-108, new): the `id()`s of `session` `Name` nodes that are the
    receiver of `session.table(...)`/`session.create_dataframe(...)`, found via `_same_scope`
    starting at the top-level `run` node -- so a `.table(...)` call inside a *nested* function
    defined inside `run` does **not** count as permitted (ruling (a) reads "a nested ... function
    ... that takes or references session" as an unconditional violation, not exempted by being
    lexically inside `run`).
  - `_session_scope_errors` (111-128, new): (i) `ast.walk`-based count of every `def run`/
    `async def run` anywhere in the module; not exactly one is a violation regardless of what the
    top-level scan already found (catches a `def run` nested in a class, invisible to the old
    `tree.body`-only scan). (ii) every `Name('session')` in the whole tree not in the permitted
    set is a violation (aliasing, passing as an argument, any other attribute, default-arg
    binding, use inside any other function's body -- all reach this as a bare `Name` node
    regardless of which function encloses it). (iii) every other `FunctionDef`/`AsyncFunctionDef`
    with `session` among its parameters, and every `Lambda` with `session` among its parameters.
  - `check_proc_py` (131-186): now builds `run_node` (the single valid top-level `run`, or `None`
    if the count/signature check already failed) and calls `_session_scope_errors(tree, run_node)`
    right after the signature check. The `ast.walk` loop: the old `no_session_sql` Attribute check
    is gone; two new checks add `rule:no_raw_sql` for `RAW_SQL_NAMES` matched as a `Name`, an
    `Attribute.attr`, or a `from`-import's `alias.name` (ruling (d)); the `table`/`save_as_table`
    branch now rejects any `node.keywords` outright and requires `len(node.args) == 1` before
    looking at the literal (ruling (c), C2) -- previously `node.args` being falsy (keyword-only
    calls) skipped validation entirely, which is exactly what both `probe_rules2.py` cases hit.

- `scripts/lib/types_map.py` -- reverted `alteryx_to_snowpark`'s string handling to match ruling
  (f): removed the `_STRING_TYPES` constant added in the first pass; `_SIZED_STRING_TYPES`
  (`String`/`WString`) now maps to `StringType(field["size"])` (required, like
  `alteryx_to_snowflake`'s `field['size']`), and `V_String`/`V_WString` map to unsized
  `StringType()` unconditionally, mirroring `alteryx_to_snowflake` exactly. Comment on
  `_SIZED_STRING_TYPES` updated to explain both functions now share this policy.

- `scripts/render_snowpark.py` -- `main()` split into three `try` blocks per ruling (g): reading
  `proc.py` and `mappings/global.yaml` stays `(FileNotFoundError, ValueError) -> exit 2` (a bad
  workflow/segment id from `Repo.seg`'s own validation surfaces as `ValueError` there too, and
  still gets the friendly per-segment message rather than a raw traceback); `render(proc_py, ...)`
  is now its own `try` with `ValueError -> exit 1` (the `$$`-in-body domain failure); writing
  `proc.sql` stays a generic `except Exception -> exit 2`. `compile_check.py`'s `_render_errors`
  was already correct (calls `render_snowpark.render()` directly, not `main()`, and already turns
  its `ValueError` into `rule:render_mismatch`) -- confirmed by re-reading it, no change needed.

- `tests/test_snowpark_rules.py` -- added `GOOD2` (a second canned-style procedure:
  `session.create_dataframe(rows, schema=...)` from literal rows, `.write.mode("append")`) and
  `test_a_procedure_using_create_dataframe_from_rows_and_append_also_passes`; changed the
  `session.sql(...)` mutation's expected tag from `"rule:no_session_sql"` to `"rule:session_scope"`
  (documented inline -- the old rule name no longer exists, the evasion is now caught by the
  structural rule); added 8 new tests reproducing the reviewer's probes verbatim (see RED/GREEN
  below).

- `tests/test_render_snowpark.py` -- added `test_cli_exits_one_when_proc_py_cannot_be_rendered`.

- `tests/test_backend.py` -- updated the `alteryx_to_snowpark` parametrize table:
  `V_String`/`V_WString` with `size: 50` now expect `"StringType()"` (was `"StringType(50)"`).

### RED

New/changed tests run against the pre-fix code (all fail as expected):

```
.venv/Scripts/python.exe -m pytest tests/test_snowpark_rules.py -v
```
```
9 failed, 10 passed in 0.07s
FAILED test_each_rule_names_its_violation[mutation1-rule:session_scope]   (old tag was no_session_sql)
FAILED test_session_aliased_then_used_for_sql_is_a_violation
FAILED test_getattr_session_sql_is_a_violation
FAILED test_save_as_table_with_a_keyword_argument_is_a_violation
FAILED test_session_table_with_a_keyword_argument_is_a_violation
FAILED test_functions_sql_expr_attribute_is_a_violation
FAILED test_from_import_of_call_function_is_a_violation
FAILED test_a_run_nested_inside_a_class_is_a_violation
FAILED test_a_fabricated_tool_comment_inside_a_string_literal_does_not_count
```
Every failure was `assert False` against `errors == []` -- i.e. the rules found nothing, as
`probe_rules.py`/`probe_rules2.py` first showed. `test_a_procedure_using_create_dataframe_...`
already passed against the old rules (it is a positive example, not an evasion).

```
.venv/Scripts/python.exe -m pytest tests/test_render_snowpark.py::test_cli_exits_one_when_proc_py_cannot_be_rendered
```
```
1 failed: AssertionError: assert 2 == 1
```

```
.venv/Scripts/python.exe -m pytest tests/test_backend.py -k alteryx_to_snowpark
```
```
2 failed: AssertionError: assert 'StringType(50)' == 'StringType()'   (V_String, V_WString)
```

### GREEN

```
.venv/Scripts/python.exe -m pytest tests/test_snowpark_rules.py
```
```
19 passed in 0.03s
```
```
.venv/Scripts/python.exe -m pytest tests/test_render_snowpark.py
```
```
4 passed in 1.49s
```
```
.venv/Scripts/python.exe -m pytest tests/test_backend.py
```
```
66 passed in 1.80s
```
```
.venv/Scripts/python.exe -m pytest tests/test_snowpark_rules.py tests/test_render_snowpark.py tests/test_compile_check_snowpark.py tests/test_backend.py tests/test_compile_check*.py tests/test_proc_runner*.py
```
(`tests/test_types_map*.py` from the dispatch does not exist as a separate file -- `types_map`
coverage lives in `tests/test_backend.py`, as it did before this task; substituted that file in
the command above and note it here rather than silently dropping the requested check.)
```
134 passed in 5.35s
```

Full suite:
```
.venv/Scripts/python.exe -m pytest
```
```
1054 passed in 66.17s (0:01:06)
```
1044 (previous total) + 10 new tests (9 in `test_snowpark_rules.py` + 1 in
`test_render_snowpark.py`) = 1054. 0 skipped, 0 failed, no warnings.

### Probe re-run (extra verification beyond the test suite)

```
.venv/Scripts/python.exe scratchpad/ot-rev3/probe_rules.py
.venv/Scripts/python.exe scratchpad/ot-rev3/probe_rules2.py
```
All 9 probes now print `VIOLATIONS:` (none print `NO VIOLATIONS`). `nested_run_class_alias_call`
and `run_defined_twice` report multiple violations (both the whole-tree run-count check and the
per-name checks fire), which is expected: when the top-level `run` is itself ambiguous or absent,
`run_node` is `None`, `permitted_session_uses` is empty, and every `session` reference anywhere
becomes a violation rather than the code guessing which `run` was meant.

### Self-review concerns (fix round 1)

- I did not touch `docs/superpowers/plans/2026-09-22-output-targets-phase1.md`, which still shows
  the old `no_session_sql`/`FORBIDDEN_SESSION` code (lines 639, 724, 774-775) -- per the dispatch,
  "Update docs? Not in this task." Flagging so it isn't mistaken for an oversight: the plan doc
  and `scripts/lib/snowpark_rules.py` now disagree, by design, until a doc pass happens.
  Follow-up task suggested separately.
- `_session_scope_errors`'s whole-tree run-count check is tagged `rule:session_scope` throughout
  (per the ruling's placement of that sentence inside bullet (a)), even for the "two top-level
  `run`s, both otherwise well-formed" case, which already gets `rule:signature` too. This is
  intentional redundancy, not a bug -- flagging in case a reviewer expected the count check under
  a different tag.
- `RAW_SQL_NAMES`'s `Attribute.attr` check is a blunt name match (e.g. `df.function(...)` on some
  unrelated object would also be flagged, not just `F.function(...)`). The ruling says "wherever
  they appear" without qualification, so I did not scope it to only Snowpark's `functions` module
  -- matches the "conservative and structural" instruction, but is worth confirming is the
  intended breadth.

---

## Fix round 2

Commit `cc9066a` "wip: fix round 2 — refuse dunder access in proc.py; render CLI catches
non-ValueError as exit 2".

Scoped re-review passed all seven findings from fix round 1; two residuals remained, both applied
exactly as ruled.

### File:line changes

- `scripts/lib/snowpark_rules.py`:
  - Module docstring: added a paragraph describing the dunder rule and why it is filed under the
    existing `rule:no_io` rather than a new rule name.
  - `_DUNDER_RE` (new, near `_TOOL_COMMENT`): `re.compile(r"^__.*__$")`.
  - `check_proc_py`'s `ast.walk` loop, `ast.Name` branch: `elif _DUNDER_RE.match(node.id)` after
    the existing `FORBIDDEN_NAMES` check (so `__import__` -- in both sets -- takes the
    `FORBIDDEN_NAMES` branch and keeps its existing message, per the ruling's explicit exception;
    `elif` makes that automatic rather than a special-cased name check).
  - Same loop, `ast.Attribute` branch: added `if _DUNDER_RE.match(node.attr): ...` alongside the
    existing `RAW_SQL_NAMES` check (both can fire independently on the same node if a name were
    ever in both sets, which none currently are).
  - Message form used verbatim: `` rule:no_io: dunder attribute access `<name>` is not allowed
    (line N) `` , using `node.lineno` from the AST node.

- `scripts/render_snowpark.py`: `main()`'s middle `try` block (around `render(...)`) gained
  `except Exception: traceback.print_exc(); return 2` after the existing `except ValueError`
  branch, matching the pattern already used for the first and third `try` blocks in the same
  function.

- `tests/test_snowpark_rules.py`: added `GOOD3` (session.table(...).to_pandas(), a pandas groupby
  loop, `StructType([...])`, `session.create_dataframe(rows, schema=...)`,
  `.write.mode("overwrite").save_as_table(...)`) and its positive-control test, plus four new
  violation tests: `F.__dict__["sql_expr"]`, `F.__getattribute__("sql_expr")`,
  `session.__class__` (bare statement), and `().__class__.__base__.__subclasses__()` (asserting
  all three dunder attributes in that chain are individually reported, not just the first).

- `tests/test_render_snowpark.py`: added
  `test_cli_exits_two_when_render_raises_something_other_than_valueerror`, monkeypatching
  `rs.render` to raise `RuntimeError`, asserting exit 2, a traceback on stderr, and no `proc.sql`
  written.

### RED

```
.venv/Scripts/python.exe -m pytest tests/test_snowpark_rules.py tests/test_render_snowpark.py -v
```
```
5 failed, 24 passed in 1.80s
FAILED test_dict_dunder_via_the_functions_module_is_a_violation                    -- errors == []
FAILED test_getattribute_dunder_via_the_functions_module_is_a_violation            -- errors == []
FAILED test_session_dunder_class_is_a_violation                                    -- only rule:session_scope, no rule:no_io
FAILED test_object_graph_walk_via_chained_dunders_is_a_violation                   -- errors == []
FAILED test_cli_exits_two_when_render_raises_something_other_than_valueerror       -- RuntimeError escaped uncaught (no exit code at all)
```
The last failure is the sharpest confirmation possible: before the fix, `rs.main(...)` did not
return 2 -- it let the monkeypatched `RuntimeError` propagate straight out of `pytest`'s test
call, exactly the "traceback with no controlled exit code" the finding described.

### GREEN

```
.venv/Scripts/python.exe -m pytest tests/test_snowpark_rules.py
```
```
24 passed in 0.03s
```
```
.venv/Scripts/python.exe -m pytest tests/test_render_snowpark.py
```
```
5 passed in 0.06s
```

Full suite:
```
.venv/Scripts/python.exe -m pytest
```
```
1060 passed in 67.63s (0:01:07)
```
1054 (after fix round 1) + 6 new tests (5 in `test_snowpark_rules.py` + 1 in
`test_render_snowpark.py`) = 1060. 0 skipped, 0 failed, no warnings.

### Self-review concerns (fix round 2)

- `session.__class__` (and the `().__class__...` chain) now produces both a `rule:session_scope`
  violation (session used for something other than `.table`/`.create_dataframe`) and a
  `rule:no_io` dunder violation on the same statement -- expected and harmless (callers use
  `any(...)`/check for a specific prefix, not exact-match on the error list), noted here in case a
  reviewer expects exactly one tag per evasion.
- The dunder check applies to literally every `ast.Name`/`ast.Attribute` in the module, including
  ones a legitimate procedure might plausibly need -- e.g. `__init__` if someone ever wrote a
  class (already separately forbidden: only `def run` may exist at all), or a dunder used purely
  as a string key into an actual dict a procedure built itself (`row["__id__"]` would false-positive
  if `__id__` were ever a real column-ish name someone chose, though nothing in the existing
  fixtures does this). This is the "conservative" instruction taken literally; flagging in case a
  future legitimate procedure needs an allowlisted exception this ruling didn't anticipate.
- `node.lineno` is used for the message's `(line N)`; this is the line the `Name`/`Attribute` node
  itself starts on, which for a multi-line chained expression (like the `().__class__...` test) is
  the same line for all three attributes in these test cases (single-line chain) but would differ
  correctly if the chain were split across lines -- not separately tested, since the ruling's own
  example is single-line.
