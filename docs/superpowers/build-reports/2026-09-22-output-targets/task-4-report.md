# Task 4 report: shared validation library and `validate_snowpark.py`

Worktree: `C:\Users\<user>\Desktop\Alteryx to Snowflake\.worktrees\ot-task-4` (branch `wt/ot-task-4`)

## Commits

1. `a33e168` wip: extract validation-report shaping into scripts/lib/validation.py
2. `f342cac` wip: validate_snowpark.py runs a Snowpark procedure in the local testing framework

## What was implemented

### Step 2: `scripts/lib/validation.py`

Moved out of `validate_segment.py`, made public, and re-exported from `validate_segment.py` under
the old private names:

- `VERDICT_SEVERITY`, `worst_verdict(verdicts)`
- `missing_table_report(actual_fqn)`, `combine(contract, golden_set, output_reports)`,
  `fail_report(contract, golden_set, error)`, `aggregate_sets(sets, reports)`
- `clear_stale_reports(repo, wf_id, seg)`
- `actual_table(wf_id, seg, output)`, `golden_path(repo, wf_id, seg, golden_set, output)`

Two new names, not previously separate functions in `validate_segment.py`:

- `expected_fqn(index) -> str` -- `f"{EXPECTED_SCHEMA}.EXPECTED_{index}"`, pulled out of what was
  an inline f-string in `_run_one_set`.
- `write_reports(repo, wf_id, seg, sets, reports, idempotent, *, extra=None) -> dict` -- the tail
  of `validate_segment()`: stamps the segment-wide `idempotent`/`idempotency_diff` onto every
  set's report, calls `aggregate_sets`, optionally merges `extra` into the *top-level* result only
  (never into a per-set report), writes `validation.json` + `validation.<set>.json` for every set,
  returns the top-level result.

`validate_segment.py` does `from lib.validation import (...)` and then
`_combine = combine`, `_worst_verdict = worst_verdict`, etc. -- every old private name still
resolves to the same function object, so `_run_one_set`/`_load_and_run`/`validate_segment()`'s
bodies needed almost no internal changes (only the tail, which now calls the new
`write_reports(...)` instead of doing the aggregate-and-write inline). `_EXPECTED_SCHEMA` (module
constant) is gone; the one place that used it (`_run_one_set`'s per-output loop) now calls
`_expected_fqn(index)`.

`tests/test_validate_segment.py` + `tests/test_validate_segment_fix_round_1.py`: **31/31 green,
unchanged** (only `vs._aggregate_sets` is called directly by name in these tests -- confirmed by
grep before touching anything -- and it still resolves correctly through the alias).

### Step 3: `scripts/validate_snowpark.py`

`validate_snowpark(repo, wf_id, seg, golden_sets=None, *, proc_path=None) -> dict`: same
prerequisite-check order as `validate_segment()` (clear stale reports first, then
`contract.json` -> `proc.py` -> `intake/mappings.yaml` -> non-empty golden sets, `outputs[]`
non-empty checked via the same `contract.get("outputs")` guard), same per-set loop with
idempotency checked only on the first set, `lib.validation.write_reports(..., extra={"target":
"snowpark"})` for the tail. `run_handler(repo, wf_id, seg, golden_set, contract, proc_path,
run_id) -> Session` loads one golden set into a fresh local-testing `Session` and runs `proc.py`'s
`run()` against it. CLI mirrors `validate_segment.main` exactly (0 PASS*/1 FAIL/2 usage).

`_alteryx_for(sql_type)` maps the contract's declared Snowflake column type back to an Alteryx
type (`number` w/ scale 0 -> `Int64`, other `number` -> `FixedDecimal`, `float` -> `Double`,
`string` -> `V_WString`, `bool` -> `Bool`, `date` -> `Date`, `timestamp` -> `DateTime`) via
`types_map.type_family` plus a small regex for the declared scale. `_coerce(value, sql_type)`
turns one `to_pandas()` cell into a `typed_csv` value using that same type (see "Snowpark
local-testing behaviours" below for why the type has to drive this, not just `type(value)`).
`_read_back(session, fqn, columns)` returns `None` if the table doesn't exist (or any other
`session.table(...)` error), else a typed `Table` dict built from the contract's declared columns
-- fed straight into `DuckDBBackend.load_table` the same way `typed_csv.read_table`'s output is.

## RED/GREEN evidence

### RED: `tests/test_validate_snowpark.py` before `scripts/validate_snowpark.py` existed

Implementation was written before the test file was run once (not strict TDD), so RED was
captured after the fact by moving the finished `validate_snowpark.py` out of the way and
re-running:

```
mv scripts/validate_snowpark.py <scratch>/validate_snowpark.py.aside
.venv/Scripts/python.exe -m pytest tests/test_validate_snowpark.py -q
```
```
ERROR collecting tests/test_validate_snowpark.py
ModuleNotFoundError: No module named 'validate_snowpark'
Interrupted: 1 error during collection
```
Matches the brief's stated expectation exactly ("Run: FAIL (module missing)"). File restored
immediately after.

### GREEN, first pass (17/18)

```
.venv/Scripts/python.exe -m pytest tests/test_validate_snowpark.py -q -rA
```
17 passed, 1 failed: `test_cli_without_set_flag_uses_the_manifest_default` got exit code 1
instead of the expected 2. Root cause (see "Bug found and fixed" below):
`_run_one_set`'s original blanket `except Exception` around the whole `run_handler(...)` call
also swallowed `FileNotFoundError` from a missing golden CSV, turning what should be a usage
error (exit 2, nothing written) into a domain FAIL (exit 1, a report written).

### GREEN, after the phase-split fix (20/20, brief's cases + 1 extra)

```
.venv/Scripts/python.exe -m pytest tests/test_validate_snowpark.py -q -rA
```
```
....................                                                      [100%]
=== 20 passed ===
```
(19 from the brief's required cases -- correct/idempotent, wrong filter, raising handler,
random/not-idempotent, missing target table, `--proc` override, unknown segment, missing golden
file, empty golden-set list, missing `proc.py`, dropped-set stale-report cleanup, and the 8 CLI
exit-code/flag tests -- plus one I added for the upstream-intermediate `contract["inputs"]`
branch, see "Extra test" below.)

### GREEN: `validate_segment.py`'s own tests, unchanged

```
.venv/Scripts/python.exe -m pytest tests/test_validate_segment.py tests/test_validate_segment_fix_round_1.py -q -rA
```
```
=== 31 passed ===
```

### GREEN: full suite

```
.venv/Scripts/python.exe -m pytest -q -rA
```
```
RC=0
1064 PASSED, 0 FAILED, 0 ERROR, 0 skipped
```
Baseline before this task (per task-3-report.md) was 1044. `tests/test_validate_snowpark.py` adds
20; 1044 + 20 = 1064, matching exactly. No warnings at any point (checked with
`-W error::DeprecationWarning` too -- none raised).

Note on this environment's pytest output: a plain `-q` run's stdout is consistently missing its
final `"N passed in Xs"` summary line no matter how it's invoked or redirected (confirmed on an
untouched HEAD before any of my changes too, so it predates this task and isn't something I
introduced) -- some interaction between this Windows/Git-Bash tool setup and pytest's terminal
writer. `-rA`'s `PASSED`/`FAILED`/`ERROR` per-test lines are unaffected and were used for every
count in this report instead (`grep -c "^PASSED"` etc.), cross-checked against the process exit
code (`0` throughout).

## Bug found and fixed: usage errors vs. domain FAILs in `_run_one_set`

`validate_segment._run_one_set` only catches `(ProcError, BackendError)` around
`_load_and_run(...)` -- a `FileNotFoundError`/`ValueError` from *loading* the golden set (missing
CSV, malformed mapping) is never caught there, so it propagates all the way to the CLI as a usage
error (exit 2, nothing written). My first draft of `validate_snowpark._run_one_set` instead
wrapped the *entire* `run_handler(...)` call (golden-loading + `run()`) in one blanket
`except Exception`, since arbitrary `proc.py` code can raise anything and there's no
`ProcError`/`BackendError`-style marker class to filter on the way the SQL path has. That silently
converted a missing golden CSV into a domain FAIL instead of a usage error --
`test_cli_without_set_flag_uses_the_manifest_default` caught it (expected exit 2, got exit 1).

Fixed by splitting `run_handler` into two phases: `_prepare_run(repo, wf_id, seg, golden_set,
contract) -> (Session, args)` (creates the session, loads the golden set and any upstream
intermediate -- lets `FileNotFoundError`/`ValueError` propagate uncaught) and the actual
`module.run(...)` call (wrapped in its own `try/except Exception` -> `lib.validation.fail_report`,
a domain FAIL). `run_handler` itself still exists as the brief's named interface, calling both
phases in sequence for anyone who wants the bundle; `_run_one_set` calls the two phases separately
-- for both the primary run and the idempotency check's second run -- so it can classify a
failure in each phase the same way the SQL path already does.

## Snowpark local-testing behaviours found and worked around

All confirmed empirically against `snowflake-snowpark-python` 1.55 on Python 3.14 in this venv,
not assumed from documentation:

- **Session isolation.** Two separate `Session.builder.configs({"local_testing": True}).create()`
  calls in the *same process* cannot see each other's tables (`session2.table(...)` on a table
  `session1` wrote raises `SnowparkLocalTestingException: ... does not exist`). So a fresh
  `Session` per run plays exactly the role a fresh `DuckDBBackend()` plays for the SQL path,
  including for the idempotency check's two independent runs -- no extra isolation (unique run
  ids, separate processes, etc.) was needed.
- **NULL upcasts an Int64 column to float64 on read-back.** A `LongType` column with *no* NULL
  stays `int64`/Python `int` through `to_pandas()`. The moment even one row is NULL in that
  column, the whole column upcasts to `float64`: the real values come back as `1.0` (a Python
  `float`), not `1`, and NULLs come back as `NaN`, not `None`. `_alteryx_for`/`_coerce` handle
  this by branching on the *contract's declared type* (`int(value)` for anything the contract
  says is an integer family, whatever Python type the cell actually arrived as), not on
  `type(value)`.
- **`DecimalType` -> `decimal.Decimal` (or `None`)**, never upcast the way integers are.
- **`TimestampType` -> `pandas.Timestamp` (or `NaT`)**; **`DateType` -> `datetime.date`
  (or `None`)** -- both need `.isoformat()` (space-separated for timestamps, to match this
  codebase's own DateTime string convention, e.g. `typed_csv`/`test_formula.py`'s
  `"2024-01-31 12:00:00"`, not `T`-separated) to get back to the ISO text `typed_csv` reads and
  writes.
- **A missing table raises, it doesn't return empty.** `session.table("...NOPE").to_pandas()`
  raises `SnowparkLocalTestingException`; `_read_back` catches any exception there and returns
  `None`, which `_run_one_set` turns into `v.missing_table_report(...)`.
- **Column-name case.** Even a lower/mixed-case `StructField` name comes back upper-cased from
  `to_pandas()` (confirmed with `id`/`Note` fields -> columns `ID`/`NOTE`), matching real
  Snowflake's unquoted-identifier behaviour and this project's own documented assumption in
  `backend.py`. `_read_back` looks columns up by the contract's (already upper-case) names, so
  this never mattered in practice, but it was worth confirming rather than assuming.
- **A local-testing `Session` has an ambient default database/schema** (`"MOCK_DATABASE"`.
  `"MOCK_SCHEMA"`) used to resolve an *unqualified* table name. The brief's own `PROC_PY` fixture
  writes the `work` output with a two-part name (`"MIG_WORK.WF0009_SEG_01_OUT"`, no database) --
  this resolves against that ambient database rather than `MIGDB` (`SANDBOX_DB`). It still works
  correctly because both the write (inside `proc.py`) and the read-back
  (`v.actual_table(...)` for a `work`-kind output, which returns the same literal two-part string
  from the contract) happen in the *same* session, so they resolve to the same place consistently
  -- `MIGDB` is only ever used explicitly for a `target`-kind output's fully three-part name. Not
  something I had to fix, but worth recording since it looked like a bug in the brief's fixture at
  first glance until I traced through where the SQL twin's own contract table names come from
  (also unqualified two-part names for `work`-kind outputs, resolved against DuckDB's single
  default catalog the same way).
- **`session.sql` raises `NotImplementedError` locally** -- confirmed via the project's own
  "facts checked" note; never called anywhere in this module (matches the rules).

## Deviations from the brief (and why)

1. **`run_handler`'s signature.** The brief's prose "Interfaces" line gives
   `run_handler(repo, wf_id, seg, golden_set, proc_path, run_id) -> Session` (6 args, no
   `contract`), but the literal code block for it takes `contract: dict` as well. I kept
   `contract` (7 args) -- it's used inside `run_handler` to load an upstream segment's golden
   intermediate (`contract["inputs"]`, the same thing `validate_segment._load_and_run` needs its
   own `contract` argument for), so dropping it would silently break that path. No test calls
   `run_handler` directly by name/arity (confirmed by grep), so this couldn't be settled by "the
   tests are the contract" -- I went with the literal code block over the abbreviated prose line,
   on the basis that the parameter is functionally required and the code block is explicitly
   "the core code to use."
2. **`_prepare_run` is a new function, not named in the brief.** Added to split `run_handler`'s
   two phases apart for `_run_one_set`'s error classification (see "Bug found and fixed" above).
   `run_handler` itself is unchanged in behaviour and still matches the brief's given code exactly
   when called on its own.
3. **`write_reports`'s exact signature** (`idempotent` as a `(bool | None, list[str])` tuple, plus
   a keyword-only `extra: dict | None = None`) is my own design, not given verbatim -- the brief's
   Interfaces line only names `write_reports(repo, wf_id, seg, sets, reports, idempotent)`.
   `idempotent` bundles what `validate_segment()`'s tail always used to stamp onto every report as
   a pair (`idempotent` and `idempotency_diff` are never set independently anywhere in the
   codebase). `extra` exists purely so `validate_snowpark`'s `"target": "snowpark"` lands in the
   persisted top-level `validation.json` without a second write -- verified directly against disk
   (`validation.json["target"] == "snowpark"`, `validation.normal.json` has no `"target"` key at
   all).
4. **`load_manifest` and `time` added to `validate_snowpark.py`'s imports** beyond the brief's
   given import block. `load_manifest` is needed to resolve `manifest.golden_sets` the same way
   `validate_segment.py` does (the brief's own prose says "non-empty sets" is one of the
   prerequisite checks, which requires it); `time` is needed for `runtime_ms`, which the brief's
   `_run_one_set` prose says should "follow `validate_segment._run_one_set` step for step" and
   that includes timing. Both are functionally unavoidable, not stylistic additions.
5. **`_outputs_equal` uses `collections.Counter` for multiset equality, not a sorted-rows
   comparison.** `validate_segment._outputs_equal`/`_read_ordered_rows` use `SELECT * ... ORDER BY`
   in SQL, which handles `NULL` correctly for free. There's no SQL to lean on here (no
   `session.sql`, by design), and Python's own `sorted()` raises `TypeError` comparing `None`
   against a non-`None` value of a different row. `Counter` equality is multiset equality by
   hashing rather than ordering, so it sidesteps that entirely and needed no special-casing for
   `None`.
6. **One extra test beyond the brief's list:**
   `test_upstream_intermediate_feeds_the_next_segment_without_running_its_procedure` -- exercises
   `_prepare_run`'s loop over `contract["inputs"]` entries that have a `stream`/`from` (an
   upstream segment's golden intermediate), which the brief's code shows but none of its listed
   test cases exercise (implementer-rules.md item 9: "add further tests for behaviour the brief
   describes in prose but does not test"). Mirrors
   `test_validate_segment.py::test_upstream_intermediate_feeds_the_next_segment_without_running_its_procedure`
   almost exactly, adapted to a two-part Snowpark `save_as_table` for the `seg_02` output.

## Self-review concerns

- **`FixedDecimal`'s `{"size": None, "scale": None}` in `_read_back`'s constructed fields.** The
  brief's own stub builds `_alteryx_for`'s output fields with `"size": None, "scale": None`
  unconditionally. For every family the required tests exercise (`Int64`, `V_WString`, `Double`)
  this is harmless, because `types_map.alteryx_to_snowflake` doesn't need `size`/`scale` for any
  of those (`NUMBER(38,0)`, unsized `VARCHAR`, `FLOAT`). It *would* produce a broken
  `NUMBER(None,0)` if a `FixedDecimal`-mapped (nonzero-scale `NUMBER`) actual column ever went
  through `DuckDBBackend.load_table` this way -- not exercised by any golden fixture here (no
  contract in this test file declares a nonzero-scale `NUMBER` output column), and literally what
  the brief's own code does, so I kept it as given rather than silently changing the interface.
  Worth a second look if a future segment's contract declares a `FixedDecimal` output column.
- **A session that fails inside `_prepare_run` after partially loading data is never closed** (no
  reference to it survives the exception). This mirrors the brief's own `run_handler` having no
  try/finally around its body either. The Local Testing Framework's state is in-process Python
  objects, not a real network connection, so this is an acceptable leak (reclaimed by normal
  garbage collection) rather than a resource problem -- flagged for the record, not fixed, since
  "fixing" it would mean adding cleanup logic the brief's own code doesn't have and no test
  requires.
- **`GOLDEN_SCHEMA` is imported but never used** in `validate_snowpark.py` -- kept because the
  brief's own import line names it explicitly (`from load_golden import golden_view_schema,
  GOLDEN_SCHEMA, SANDBOX_DB`); no lint tooling is configured in this project (checked
  `pyproject.toml`/`requirements.txt`, no ruff/flake8/pylint), so it doesn't affect any check.

## Files changed

- `scripts/lib/validation.py` (new)
- `scripts/validate_segment.py` (modified: imports moved names from `lib.validation`, keeps old
  underscore aliases, tail now calls `write_reports`)
- `scripts/validate_snowpark.py` (new)
- `tests/test_validate_snowpark.py` (new)

## Final state

`git status` is clean after both commits. Full suite from the worktree root:
**1064 passed, 0 failed, 0 errors, 0 skipped**, exit code 0, no warnings.
`tests/test_validate_segment.py` + `tests/test_validate_segment_fix_round_1.py`: 31/31, unchanged.
`tests/test_validate_snowpark.py`: 20/20 (19 from the brief + 1 added).

## Fix round 1

Findings addressed: `task-4-review-findings.md`'s Critical 1/2, Important 3/4, Minor 6 (Minor 5
and 7 were accepted as-is / calibration-only, per `task-4-fix1.md`'s R3).

### Merge (R0)

`git merge --no-edit wt/ot-task-3` — merge commit **`958fe8c`**. Clean merge (no conflicts): T3's
fix round touched `scripts/lib/snowpark_rules.py`, `scripts/lib/types_map.py`,
`scripts/render_snowpark.py` and their tests, plus `tests/test_backend.py`; T4 had touched only
`scripts/lib/validation.py`, `scripts/validate_segment.py`, `scripts/validate_snowpark.py`,
`tests/test_validate_snowpark.py` — no overlapping lines. Full suite immediately after the merge
(before any fix-round-1 code): **1074 passed, 0 failed, 0 errors, 0 skipped**, exit code 0
(baseline 1044 (task-3-report.md) + task-3's own fix-round-1 tests + this task's 20 = 1074).

### Fix commit

**`5362305`** — `wip: fix round 1 (C1, C2, I3, I4, M6) — actual schema read from Snowpark, not
the contract`.

### R1 — the actual table's schema comes from Snowpark, never the contract

Added `scripts/lib/types_map.py::snowpark_to_alteryx(data_type) -> dict` (`alteryx_to_snowpark`'s
exact inverse), following the ruling's policy exactly: `LongType`/`IntegerType`/`ShortType`/
`ByteType` -> `Int64`; `DoubleType`/`FloatType` -> `Double`; `DecimalType(p, s)` -> `FixedDecimal`
size `p` scale `s` (including `s == 0`, confirmed with its own regression test so it can never
silently become `Int64`); `BooleanType` -> `Bool`; `DateType` -> `Date`; any `TimestampType`
(default/NTZ/LTZ/TZ — one class in this Snowpark version, so a single `isinstance` check covers
every tz variant) -> `DateTime`; `TimeType` -> `Time`; `StringType` -> `String(n)` for a concrete
length under the VARCHAR max, else `V_String` (checked via the framework's own `_is_max_size`
flag *and* `length is None` *and* `length >= 16777216`, confirmed empirically that an "unsized"
`StringType()` reports `length=None, _is_max_size=True`); anything else (`VariantType` etc.)
raises `ValueError` naming the type. Round-trip test in `tests/test_backend.py` (parametrized,
mirroring the existing `alteryx_to_snowpark` cases 1:1) with the three lossy cases the ruling
named stated explicitly: `Int16`/`Int32`/`Byte` -> `Int64`, `V_WString` -> `V_String`,
`WString(n)` -> `String(n)`.

`scripts/validate_snowpark.py::_read_back(session, fqn)` (dropped the `columns` parameter
entirely) now reads `session.table(fqn).schema.fields` — real `StructField`s, in the table's own
order — and calls `snowpark_to_alteryx` on each `.datatype`, instead of mapping the *contract's*
declared columns. `_coerce(value, alteryx_type)` takes that real Alteryx type directly (renamed
the parameter from `sql_type`; it's no longer a Snowflake type string from the contract). A cell
that still can't convert (`ValueError`/`TypeError`/`decimal.InvalidOperation`/`OverflowError`) —
or a column whose Snowpark type `snowpark_to_alteryx` doesn't recognise — raises a new
`ReadBackError(table, column, row_index, reason)` (module-level exception class); `_run_one_set`
catches it around the per-output `_read_back` call and turns it into that golden set's FAIL report
via `lib.validation.fail_report`, the same way a `proc.py` `run()` exception already was, with
`runtime_ms` stamped and the report written (exit 1, not exit 2). `_outputs_equal` (the
idempotency check) also catches `ReadBackError` per output and treats it as diverging — the same
way a missing/unreadable table already was — since the ruling only specified `_run_one_set`'s
primary-run path; I extended the same treatment there for consistency and to avoid a
`ReadBackError` from a non-deterministic second run crashing the whole CLI to exit 2 (documented
in the code as a self-directed addition, not literally required by the ruling).

RED-first (`tests/test_validate_snowpark.py`, run against the pre-fix code before touching
`validate_snowpark.py`/`types_map.py`):

```
.venv/Scripts/python.exe -m pytest tests/test_validate_snowpark.py -q -k "type_mismatch or extra_column or missing_column or bad_id or fixed_decimal or still_passes_after_the_schema_fix"
```
5 of 6 failed as expected (the 6th, the positive control, already passed): the wrong-type test
found `assert clusters` empty (no TYPE cluster — the bug: schema check passed on the contract's
say-so); the extra-column and missing-column tests failed the same way; the FixedDecimal test hit
`lib.backend.BackendError: ... CREATE OR REPLACE TABLE ... "AMT" NUMBER(None,0) ... Expected a
constant as type modifier` (finding 4's exact repro); the bad-ID-string test raised
`ValueError: invalid literal for int() with base 10: 'not-a-number'` inside `_coerce`, uncaught,
propagating out as a crash (finding 3's exact repro).

GREEN: `tests/test_validate_snowpark.py` **26/26** (the original 20 + the 6 new fix-round-1 tests).

### R2 — no `"size": None` for a FixedDecimal anywhere

`_alteryx_for` and its `_NUMBER_SCALE_RE` regex are deleted outright (confirmed unused after R1 —
`grep -n "_alteryx_for\|_NUMBER_SCALE_RE" scripts/validate_snowpark.py` returns nothing); nothing
else in this module needed the contract-columns-as-Alteryx-fields path R1 removed.
`grep -n '"size": None' scripts/validate_snowpark.py` also returns nothing.

### R3 — accepted as-is

Nothing done; `run_handler`'s 7-arg signature (with `contract`) is unchanged from the original
implementation.

### R4 — dead `GOLDEN_SCHEMA` import removed

`from load_golden import golden_view_schema, GOLDEN_SCHEMA, SANDBOX_DB` -> `from load_golden
import golden_view_schema, SANDBOX_DB`.

### Full suite

```
.venv/Scripts/python.exe -m pytest -q -rA
```
**1099 passed, 0 failed, 0 errors, 0 skipped**, exit code 0 (merged baseline 1074 + 25 new tests
here: 6 in `tests/test_validate_snowpark.py`, 19 in `tests/test_backend.py` — a 16-case
parametrized round-trip test plus 3 unit tests for `snowpark_to_alteryx`). No warnings.
`compare.py`, `scripts/validate_segment.py` and `scripts/lib/validation.py` are untouched (`git
diff --stat` after the fix commit touches only `scripts/lib/types_map.py`,
`scripts/validate_snowpark.py`, `tests/test_backend.py`, `tests/test_validate_snowpark.py`).

### Concerns

- `_outputs_equal` catching `ReadBackError` (noted above) is my own addition beyond the ruling's
  literal text, which only discussed the primary run's per-output loop. I believe it's the right
  call — the alternative (an uncaught `ReadBackError` from the idempotency check's second run
  crashing the whole CLI to exit 2, with no report written) seemed clearly wrong given the same
  exception is a documented domain condition everywhere else it can occur — but flagging the
  extrapolation for the record.
- `StringType`'s `_is_max_size` attribute (used in `snowpark_to_alteryx`) is a private
  (underscore-prefixed) attribute of the Snowpark SDK, confirmed empirically on
  `snowflake-snowpark-python` 1.55 rather than from public API documentation, since no public
  alias exists on the class (`dir()` was checked directly). This is inherently a little fragile
  against a future Snowpark SDK upgrade; the `length is None or length >= 16777216` checks in the
  same condition are the public-surface fallback that would still catch an unsized column even if
  `_is_max_size` were ever renamed or removed.
