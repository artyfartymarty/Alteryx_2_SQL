# Task 1 report — the `python` tool in the parser, the segmenter and the simulator

Worktree: `.worktrees/ot-task-1`, branch `wt/ot-task-1`.

## What I implemented

1. **Parser** (`scripts/parsers/plugin_map.py`): added
   `"AlteryxBasePluginsGui.PythonTool.PythonTool": "python"` to `PLUGIN_TYPES` (after `RunCommand`,
   with the "verify against your Alteryx version" comment the brief specifies), and
   `"python": {"in": ["Input"], "out": {"Output1": "1", …, "Output5": "5"}}` to `ANCHORS`. Only these
   two dicts were touched, per the boundary with task 2.

2. **Config parser** (`scripts/parsers/tool_config.py`): added `_python(config)` returning
   `{"script": (_find(config, "Script") or "").strip()}` and registered `"python": _python` in
   `PARSERS`, verbatim as the brief specifies.

3. **Segmenter** (`scripts/segment.py`): `is_hard` now treats a `python` node exactly like a
   `macro` — a hard cut on either side of the edge — so a Python tool is always alone in its
   segment. I also had to extend the **ordering-protection walk** (step 3, the loop that unions an
   order-dependent chain upstream to its Sort), which independently checked
   `pred_node["type"] == "macro"` to stop and warn: without extending that check to `"python"` too,
   an order-dependent tool sitting just downstream of a Python tool would get unioned into the same
   group as the Python tool, silently defeating the hard cut `is_hard` establishes elsewhere. I
   generalized the check to `pred_node["type"] in ("macro", "python")` and the warning text to name
   whichever type it actually crossed (`f"{pred_node['type']} {pred}"` instead of a hardcoded
   `macro`). I also updated the module-level docstring (lines 5–16), which described "Tool
   Containers and macro boundaries" and "an ordering dependency crossing a macro" — both now mention
   Python tools too, since it's user-facing documentation of the same algorithm `is_hard`'s
   docstring documents.

4. **Simulator** (`scripts/dev/alteryx_sim.py`): added `PYTHON_TOOL_ALLOWED_MODULES`,
   `_PYTHON_TOOL_BUILTINS`, `_guarded_import`, `_pandas_to_table`, `_table_to_pandas`,
   `run_python_tool`, and `sim_python`, exactly as the brief's code, with one systematic renaming
   (see "Brief corrections" below). Registered `"python": sim_python` in `SIMULATORS`. Added
   `import importlib` and `import math` to the top-of-file imports (both were absent).

5. **Docs**:
   - `docs/reference/dag-contract.md`: §2 table row for the plugin (kept to the table's real 4
     columns — see "Brief corrections"); §4 config entry for `python`, placed after `run_command`,
     covering the `script` key, the notebook-JSON note for real Alteryx, the `dst_order` rule, and
     that it's always its own segment.
   - `docs/reference/simulator-semantics.md`: new §7.1 "The Python tool" (sandbox, guarded import,
     the `Alteryx` shim, the `Alteryx.read`/`Alteryx.write` dtype mapping in both directions,
     NaN/NaT → NULL, script failures → `UnsupportedTool`), every line carrying the file's
     *(assumption — verify per Alteryx version)* marker per its own convention. Also added a §9
     bullet cross-referencing §7.1's failure path, and updated §10 to name
     `tests/test_alteryx_sim_python.py`.

## Brief corrections

- **`Unsupported` doesn't exist — the real class is `UnsupportedTool`.** The brief's tests and
  simulator code both use `sim.Unsupported` / `raise Unsupported(...)`. I grepped
  `scripts/dev/alteryx_sim.py` before writing anything: the only exception class defined there is
  `class UnsupportedTool(Exception)` (line 39), used by every other `sim_*` function
  (`sim_summarize`, `sim_sample`, `sim_regex`, `sim_macro`, `_write_to_database`,
  `_refuse_unsupported`, …), all with the `f"tool {node['tool_id']}: …"` message convention I
  reused. I used `UnsupportedTool` throughout — both in the two new test files (`sim.UnsupportedTool`
  in place of the brief's `sim.Unsupported`) and in the simulator code (`raise UnsupportedTool(...)`
  in place of `raise Unsupported(...)`) — since introducing a second, differently-named exception
  class for one tool would break the simulator's single existing refusal contract that
  `_refuse_unsupported`, `run()` and every caller already catch by name. This is a rename only; no
  test assertion, message text or behavior changed.

- **`segment()`'s real return shape.** The brief's own note says to adjust
  `test_a_python_node_is_always_its_own_segment` to the real shape; I read `scripts/segment.py`'s
  docstring and `tests/test_segment.py`'s `test_macro_is_its_own_segment` for the pattern:
  `segment()` returns `{"segments": {"seg_01": ["1", "2", …], …}, "order": [...], ...}` — a dict
  keyed by seg id, not a list of groups. I wrote the assertion as
  `assert ["3"] in result["segments"].values()`, matching the existing macro test's own style
  (`assert ["2"] in r["segments"].values()`), which is no weaker than the brief's intent ("the
  python node must be alone in its segment").

- **The `dst_order` rule needed no code change.** The brief asks me to check whether
  `scripts/parse.py`'s `dst_order` assignment is keyed on tool type `union` and, if so, extend it to
  `python`. I read `_edges()` (`scripts/parse.py` ~line 256–275): the rule is
  `order = label.lstrip("#"); "dst_order": int(order) if order.isdigit() else 1` — it keys only on
  the connection's XML `name` attribute being a digit-string like `#1`/`#2`, never on the
  destination node's type. It already applies uniformly to any tool, `python` included. I made no
  change to `scripts/parse.py` (which the task boundary excludes me from touching in any case), and
  proved the existing behavior is correct for `python` with
  `tests/test_alteryx_sim_python.py::test_sim_python_reads_connections_in_order_and_returns_only_written_anchors`,
  which goes through `sim.sim_python` and the two-input ordering the brief's own snippet exercises
  (§4 of the brief also confirmed this independently: `_gather`, which every `sim_*` function
  consumes via `inputs_by_anchor`, already orders by `dst_order` generically, not per-type).

- **dag-contract §2 table row column count.** The brief's literal row text
  (`| … | python | Input (connections #1..#n, ordered) | 1..5 (Output1..Output5) | verify the plugin
  id against your Alteryx version |`) has five cells, but the table's header
  (`| Plugin | type | in anchors | out anchors |`) has four. I folded the "verify…" text into the
  fourth (out-anchors) cell with an em dash rather than adding a fifth column that would break every
  other row's alignment; the required verification note is still present verbatim.

## TDD evidence

**RED** — `tests/test_alteryx_sim_python.py tests/test_parse_python_tool.py
tests/test_segment.py::test_python_tool_is_a_hard_cut_like_a_macro`, run before any production
code changed:

```
FAILED tests/test_alteryx_sim_python.py::test_shim_runs_the_script_and_types_the_output
FAILED tests/test_alteryx_sim_python.py::test_nan_becomes_null_and_ints_bools_dates_map_to_alteryx_types
FAILED tests/test_alteryx_sim_python.py::test_sandbox_refuses_filesystem_network_and_disallowed_imports[...] (x5)
FAILED tests/test_alteryx_sim_python.py::test_sim_python_reads_connections_in_order_and_returns_only_written_anchors
FAILED tests/test_alteryx_sim_python.py::test_a_script_error_is_reported_as_unsupported_with_the_tool_id
FAILED tests/test_parse_python_tool.py::test_plugin_maps_to_python_with_one_input_and_five_outputs
   AssertionError: assert 'unknown' == 'python'
FAILED tests/test_parse_python_tool.py::test_config_carries_the_script_verbatim
   AssertionError: assert {} == {'script': ...}
FAILED tests/test_parse_python_tool.py::test_a_python_node_is_always_its_own_segment
   AssertionError: assert ['3'] in dict_values([['1', '2', '3', '4', '5']])
FAILED tests/test_segment.py::test_python_tool_is_a_hard_cut_like_a_macro
   AssertionError: assert ('seg_01' != 'seg_01')
13 failed in 0.17s
```

All 13 failures are for the expected reason: the plugin classifies as `unknown` (not registered
yet), `tool_config.parse_config` returns `{}` for an unregistered type, `is_hard` doesn't yet know
about `python` so everything collapses into one segment, and `sim.run_python_tool`/`sim.sim_python`
don't exist yet (`AttributeError`, visible in the collection/call failures for the sandbox and
sim_python tests).

**GREEN** — after Steps 2–3 (parser, segmenter, simulator):

```
$ .venv/Scripts/python.exe -m pytest tests/test_alteryx_sim_python.py tests/test_parse_python_tool.py tests/test_segment.py tests/test_parse.py tests/test_alteryx_sim.py -v
...
tests\test_alteryx_sim_python.py .........                               [  8%]
tests\test_parse_python_tool.py ...                                      [ 11%]
tests\test_segment.py ....................                               [ 31%]
tests\test_parse.py ...........................................          [ 74%]
tests\test_alteryx_sim.py ..........................                     [100%]
101 passed in 0.89s
```

**Full suite**, from the worktree root, after the docs commit:

```
$ .venv/Scripts/python.exe -m pytest
...
FAILED tests/test_backend.py::test_snowflake_backend_fails_clearly_without_connector
1 failed, 1017 passed in 64.41s
```

1005 (stated baseline) + 13 (my new tests: 9 in `test_alteryx_sim_python.py`, 3 in
`test_parse_python_tool.py`, 1 added to `test_segment.py`) = 1018, matching `1017 passed + 1 failed`.

The one failure, `tests/test_backend.py::test_snowflake_backend_fails_clearly_without_connector`, is
**pre-existing and unrelated to this task**: neither `scripts/lib/backend.py` nor
`tests/test_backend.py` appear in `git diff --stat e3052fe HEAD` (empty output — I never touched
them), and the last commit to touch either file is `2c40f71`, long before my work. It also fails the
same way in complete isolation (`pytest tests/test_backend.py`: `1 failed, 47 passed`). The failure
itself is environmental: the installed `snowflake-connector-python` now reaches its own
`_get_default_connection_params()` and raises `Default connection with name 'default' cannot be
found` instead of the `BackendError` about a missing `snowflake-connector-python` package the test
expects — i.e. the connector package **is** installed in this venv (contradicting what the test's
own docstring/name assume), which is an environment drift outside this task's scope. No warnings
were printed in the full run (pytest would print a warnings summary section if there were any; there
is none).

## Files changed

- `scripts/parsers/plugin_map.py` — `PLUGIN_TYPES`, `ANCHORS` only.
- `scripts/parsers/tool_config.py` — `_python`, `PARSERS`.
- `scripts/segment.py` — `is_hard`, the ordering-protection walk's macro check, two docstrings.
- `scripts/dev/alteryx_sim.py` — sandbox + shim + `sim_python`, `SIMULATORS`, two new imports.
- `docs/reference/dag-contract.md` — §2 row, §4 entry.
- `docs/reference/simulator-semantics.md` — new §7.1, §9 bullet, §10 update.
- `tests/test_alteryx_sim_python.py` (new), `tests/test_parse_python_tool.py` (new),
  `tests/test_segment.py` (one test added).

## Commits

- `4b8fb17` — `wip: python tool in the parser and segmenter`
- `0d75db2` — `wip: simulate the python tool in a sandbox with the Alteryx.read/write shim`
- `c9e91e5` — `feat: the Python tool — parsed, segmented alone, simulated in a sandbox with the Alteryx.read/write shim` (docs)

Worktree is clean (`git status --short` empty) after all three commits.

## Self-review

- **Completeness**: every brief requirement implemented — `PLUGIN_TYPES`/`ANCHORS`, `_python`
  config parser, `is_hard`, the sandboxed simulator, both docs sections. The one place I extended
  beyond the brief's literal text was the ordering-protection walk's macro check in `segment.py`,
  which the brief flagged only as "update the warning text if it names macros only" — I read the
  code and found the check itself (not just its warning string) needed extending, or `is_hard`'s new
  guarantee would be silently violated for one specific shape (an order-dependent tool directly
  downstream of a Python tool with exactly one inbound edge).
- **Quality**: kept the sandbox/shim code exactly as the brief specifies (it's already carefully
  written and tested), only renaming `Unsupported` → `UnsupportedTool` throughout for consistency
  with the rest of the module.
- **Discipline**: did not touch `scripts/parse.py`, `scripts/target_check.py`,
  `mappings/global.yaml`, `scripts/dev/build_samples.py`, or any `TARGET_CLASS` entry in
  `plugin_map.py` — confirmed `TARGET_CLASS` doesn't exist yet in this worktree, so there was
  nothing to avoid disturbing there. Did not add a `python` tool to any sample workflow (that's
  task 2's `wf_0006`, per the plan this brief sits under).
- **Testing**: all new tests assert real behavior (sandbox execution, dtype mapping, segment
  membership) rather than mocks; the sandbox tests exercise the actual `exec()` sandbox, not a stub.
- **Concern worth flagging for review**: the honesty rule ("nothing has run against real Alteryx")
  is upheld throughout — every new simulator-semantics.md line carries the *(assumption — verify per
  Alteryx version)* marker, and §7.1 explicitly says the shim is an unverified model.

## Fix round 1 (coordinator ruling: C1, C2 — both Critical)

Both findings were against the brief's own sandbox code (task 1 brief step 3), not something I
deviated into; the coordinator's message confirms C1 is a plan defect being corrected here, not an
implementer mistake. Commits: `b0a6302` (code) and `a0f48d2` (docs).

### C1 — the sandbox is an accident guard, not a security boundary

**What was wrong.** The reviewer's `probe_sandbox.py`/`probe_sandbox2.py` (in the scratchpad path
the coordinator's message named) showed the restricted-builtins/guarded-`__import__` sandbox from
the brief has a well-known hole: dunder **attribute** access isn't a builtin name and isn't an
`import`, so neither guard touches it. `pd.__builtins__['__import__']` (or `['open']`) recovers the
*real*, unrestricted `__import__`/`open` — a module's own `__builtins__` is bound to the real
interpreter builtins at import time, not to the `exec()` namespace's substituted dict — and
`().__class__.__base__.__subclasses__()` reaches the whole loaded-class graph the same way. Before
the fix, `probe_sandbox2.py`'s "recover real open() via pandas.__builtins__" case ran to completion
with **no exception at all** and could read arbitrary files.

**RULING (binding).** Keep the guarded import and restricted builtins (accident guard). Add an AST
pre-check before `exec` refusing dunder names/attributes and imports outside the allow-list at
parse time too. Update the docs to say, in these words, that this is an accident guard and not a
security boundary, and remove any "no filesystem or network" framing.

**What I changed** — `scripts/dev/alteryx_sim.py`:
- New `_check_python_tool_script(script) -> ast.Module` (inserted after `_guarded_import`, ~line
  798): `ast.parse`s the script, then `ast.walk`s the tree and raises `UnsupportedTool` (naming the
  offending attribute/name/module) for: any `ast.Attribute` whose `.attr` starts with `__`; any
  `ast.Name` whose `.id` starts with `__` (this alone blocks a script calling `__import__(...)`
  directly, which the brief's guarded `__import__` already handled at run time — now also blocked
  at parse time); any `ast.Import` alias whose root module isn't in `PYTHON_TOOL_ALLOWED_MODULES`;
  any `ast.ImportFrom` with `level != 0` (relative import) or whose root module isn't allowed. A
  `SyntaxError` from `ast.parse` itself is reported as `UnsupportedTool` (previously `compile()`
  inside the try/except caught this the same way, so behavior for a malformed script is unchanged).
- `run_python_tool` (~line 895) now calls `tree = _check_python_tool_script(script)` **before**
  building the `Alteryx` shim or the `exec()` namespace at all, and `exec(compile(tree, …), …)`
  instead of `exec(compile(script, …), …)` — compiling the already-parsed, already-checked AST
  object rather than re-parsing the source.
- Added `import ast`. Removed `import math` (now dead — see C2: its only use, `math.isnan`, is
  gone).
- Docstring on `run_python_tool` and a new bullet in `Alteryx.write` document the "accident guard,
  not a security boundary" framing at the code level too, not just in the reference docs.

**What I deliberately left alone, per the ruling.** `DataFrame.to_csv(path)` and `pandas.read_csv`
on a path that exists are legitimate calls into an *allowed* library and are not attribute access
on a dunder name — the AST check correctly does not touch them, and re-running
`probe_sandbox.py`'s "DataFrame.to_csv writing outside sandbox" case after the fix still writes the
file with no exception, exactly as the ruling says it should (this residual risk is now the
explicit, quoted subject of the docs changes below).

**RED** (`tests/test_alteryx_sim_python.py`, run before `_check_python_tool_script` existed):
```
FAILED ...test_ast_precheck_refuses_dunder_access_and_bad_imports[import pandas as pd\nreal_import = pd.__builtins__['__import__']]
FAILED ...test_ast_precheck_refuses_dunder_access_and_bad_imports[import pandas as pd\npd.__builtins__]
FAILED ...test_ast_precheck_refuses_dunder_access_and_bad_imports[x = ().__class__.__base__.__subclasses__()]
FAILED ...test_ast_precheck_refuses_dunder_access_and_bad_imports[def f():\n    pass\nreal_builtins = f.__globals__['__builtins__']]
FAILED ...test_recovering_real_open_via_module_builtins_never_runs
  Failed: DID NOT RAISE UnsupportedTool
9 failed, 12 passed in 1.72s
```
(The two `from os import path` / `from . import os` parametrize cases in the same test did **not**
fail — they were already caught by the brief's existing guarded `__import__` at run time, which is
correct and expected; the AST check makes them fail earlier, not newly.) The `DID NOT RAISE`
failure on the marker-file test is itself evidence the pre-fix code had a real, working filesystem
escape, not just a theoretical one.

**GREEN**:
```
$ .venv/Scripts/python.exe -m pytest tests/test_alteryx_sim_python.py -v
21 passed in 1.74s
```
Also reran the reviewer's own probe scripts directly after the fix:
```
$ .venv/Scripts/python.exe probe_sandbox2.py
--- recover real __import__ via pandas.__builtins__ and import os ---
Blocked (UnsupportedTool): python tool: attribute access '__builtins__' is not allowed
--- recover real open() via pandas.__builtins__ and read hosts file ---
Blocked (UnsupportedTool): python tool: attribute access '__builtins__' is not allowed
marker file exists on disk: False

$ .venv/Scripts/python.exe probe_sandbox.py
--- attr traversal via ().__class__.__base__.__subclasses__() ---
Blocked (UnsupportedTool): python tool: attribute access '__subclasses__' is not allowed
--- DataFrame.to_csv writing outside sandbox ---
NO EXCEPTION. Result: {1: {...}}          # <- expected: this is the documented residual risk
```

**Docs** (`docs/reference/simulator-semantics.md` §7.1, and the §9/§10 bullets referencing it, plus
`docs/reference/dag-contract.md`'s `python` entry) now carry, character-for-character (verified with
a whitespace-normalized substring count = 1 per location, 4 locations total):

> This is an accident guard, not a security boundary: an allowed library can still reach the
> filesystem (for example `DataFrame.to_csv`); the simulator runs only this repository's own
> committed sample scripts and must never be pointed at an untrusted workflow's Python tool.

§7.1 also gained a bullet explaining *why* the static pre-check exists (dunder attribute access is
neither a builtin name nor an import) and a bullet on the AST check's own behavior. No prior wording
in my docs literally said "no filesystem or network" (I checked — `grep` found none), but the old
§7.1 import bullet undersold the risk by only discussing imports; it now explicitly says an allowed
library's own legitimate API is not and cannot be refused by either guard.

### C2 — nullable pandas dtypes corrupted by `_pandas_to_table`

**What was wrong.** `_pandas_to_table` matched dtypes with `str(series.dtype).startswith(...)`,
which only matches numpy's own spellings (`"int64"`, `"float64"`, `"bool"`). Pandas' **nullable**
dtypes spell differently (`"Int64"`, `"boolean"`, capitalized) and fell through to the `else`
branch, which used `isinstance(v, float) and math.isnan(v)` for NULL detection — `pd.NA` is neither
`None` nor a `float`, so it slipped through as a non-NULL value and got stringified to the literal
`"<NA>"`. Concretely: (1) any script producing `pd.array(..., dtype="Int64")` or `dtype="boolean"`
came out as `V_WString` with `"<NA>"` instead of typed NULLs; (2) `_table_to_pandas` itself types an
incoming `Bool` field as pandas' nullable `"boolean"` (not numpy `"bool"`), so the straight-through
round trip `Alteryx.read` → `Alteryx.write` on a `Bool` column was **silently corrupted** on every
single Python-tool script that just passed a Bool field through — not just an edge case.

**RULING (binding).** Use `pandas.api.types.is_bool_dtype`/`is_integer_dtype`/`is_float_dtype`/
`is_datetime64_any_dtype` (bool checked first, explicitly), and `pd.isna(v)` uniformly for NULL in
every branch including the object/string one.

**What I changed** — `_pandas_to_table` (`scripts/dev/alteryx_sim.py`, ~line 826): replaced the
`str(dtype)`-prefix `if/elif` chain with `is_bool_dtype` → `is_integer_dtype` → `is_float_dtype` →
`is_datetime64_any_dtype` → else-`V_WString`, imported from `pandas.api.types` inside the function
(matching the existing lazy-`import pandas as pd` pattern in this module). Every branch's NULL test
is now `None if pd.isna(v) else …` — including the final `V_WString` branch, which previously used
the `isinstance(v, float) and math.isnan(v)` check that misses `pd.NA`/`pd.NaT`. This made `math`
dead code in the file, so I removed `import math` (grepped the whole file first to confirm no other
use).

**RED** (before the fix):
```
FAILED test_nullable_int64_with_a_null_stays_int64_with_none
  AssertionError: assert 'V_WString' == 'Int64'
FAILED test_nullable_boolean_with_a_null_stays_bool_with_none
  AssertionError: assert 'V_WString' == 'Bool'
FAILED test_object_column_with_pd_na_becomes_v_wstring_with_none
  AssertionError: assert [['x'], ['<NA>']] == [['x'], [None]]
FAILED test_bool_round_trips_through_read_and_write_unchanged
  AssertionError: assert ['V_WString', 'V_WString'] == ['V_WString', 'Bool']
```

**GREEN**: all 4 pass as part of the same 21/21 `test_alteryx_sim_python.py` run above.

For the round-trip test, I did not mutate the shared module-level `TABLE`/`ROWS` fixture (other
tests, e.g. `test_shim_runs_the_script_and_types_the_output`, assert exact output rows that depend
on `TABLE`'s existing `CANCELLED` values) — I built a small local table reusing `FIELDS` with a
second row whose `CANCELLED` is `None`, which is what "add a NULL Bool to the fixture table for this
test" meant in context.

### Minor — documented that a later write to the same anchor replaces the first

Added as a docstring on the `Alteryx.write` staticmethod inside `run_python_tool`
(`scripts/dev/alteryx_sim.py`) and as a bullet in `docs/reference/simulator-semantics.md` §7.1. No
new test: the behavior already follows directly from `written[int(anchor)] = _pandas_to_table(pdf)`
being a plain dict assignment, which `test_sim_python_reads_connections_in_order_and_returns_only_written_anchors`
and the dtype tests already exercise indirectly (each anchor's most recent value is what's
returned); this was a documentation-only gap per the ruling's "if cheap" framing.

### Verification after fix round 1

```
$ .venv/Scripts/python.exe -m pytest tests/test_alteryx_sim_python.py tests/test_parse_python_tool.py tests/test_segment.py tests/test_alteryx_sim.py tests/test_parse.py
113 passed in 0.79s

$ .venv/Scripts/python.exe -m pytest   # full suite, worktree root
1 failed, 1029 passed in 59.65s
FAILED tests/test_backend.py::test_snowflake_backend_fails_clearly_without_connector
```
1029 = 1017 (post-task-1 count) + 12 new fix-round-1 tests. The one failure is the same
pre-existing, unrelated, environment-dependent failure reported in the original submission
(confirmed again: neither `scripts/lib/backend.py` nor `tests/test_backend.py` appear in this
commit range's diff), which the coordinator's message says another task owns. No new warnings.
Worktree clean after `a0f48d2`.
