# Task E report — `cookbook/snowpark.md` and `cookbook/dbt.md` with executable examples

Worktree: `.worktrees/p2-E`, branch `wt/p2-E`, base `cfd4896` (Task A + P3 merged). Commit:
`6b6fdbf`.

## What was implemented

- `cookbook/snowpark.md`: opening paragraph naming design spec §4.2's contract as the rule every
  fragment on the page has to fit inside ("these are fragments that go inside `run`"), then one
  section per tool -- `## Filter`, `## Formula`, `## Summarize`, `## Sort`, `## Python tool with
  carry-over (pandas)` -- each with "What the SQL page does", the exact ```python block (equal to
  its `example.py`), and "Parity notes"; closed by `## What the local double does not prove`.
- `cookbook/dbt.md`: `## Materialisations` (table/append/merge configs, the alias rule and spike
  S3's exact "approximate match" error text, the `unique_key`-too-narrow silent-row-loss finding),
  `## Hooks` (`pre_hook`/`post_hook` against `{{ this }}`, the blank-hook fix-round-1 finding),
  `## Sources and refs` (the closed Jinja allow-list, fix rounds 1-2), `## Naming` (model file
  naming, DV1's sandbox-name rule, DV3's flattened `--vars`), `## Tests` (`not_null`/`unique` from
  the contract, explicitly not run by `validate_dbt.py`), `## Executable example: a merge model`
  (the one ```sql block, equal to `project/models/history.sql`), `## What dbt-duckdb does not
  prove`.
- `tests/cookbook_examples/snowpark/{filter,formula,summarize,sort}/`: `input.csv`/
  `input.schema.json` copied byte-for-byte from the SQL examples (formula's `input.csv` has one
  intentional value change -- see "Brief corrections" below); `case.json` reusing the SQL node/
  config with `compare[]` entries `{"stream", "py": "example.py", "function", "keys"}`; `example.py`
  each defining plain `(session) -> DataFrame` functions reading `session.table("MIG_COOKBOOK.
  IN_1")`, DataFrame-API only.
- `tests/cookbook_examples/snowpark/python_carry_over/`: a SUBSCRIPTIONS-shaped `input.csv`
  (`CUSTOMER, PERIOD, BILLED, CAP, CANCELLED`) with a duplicate row (`ACME, 2026-01` twice,
  byte-identical) and a NULL (`CAP` on `BETA, 2026-01`); `case.json`'s node is
  `{"tool_id": "2", "type": "python", "config": {"script": <the exact script embedded in
  samples/wf_0006/source/subscription_revenue.yxmd>}}`; `example.py`'s `transform(session)` is the
  same body as `samples/wf_0006/canned/segments/seg_02/proc.py`, returning the assembled DataFrame
  instead of writing it.
- `tests/cookbook_examples/dbt/merge/`: `input.csv` (incoming rows: `ID 1` twice byte-identical --
  matches an existing `HISTORY` row, `ID 2` with a NULL `REGION` -- a new key), `history_before.csv`
  (pre-existing `HISTORY`: `ID 1`, `ID 7`), and `project/` (`dbt_project.yml`, `profiles.yml` =
  `dbt_project.PROFILES_TEMPLATE` verbatim, `models/sources.yml`, `models/history.sql` -- a
  `materialized='incremental', incremental_strategy='merge', unique_key=['ID'], alias='HISTORY'`
  model reading `{{ source('src', 'IN_1') }}`). Verified directly (both via the oracle's
  `update_insert` write mode and a real `dbt run` against DuckDB, before writing any test) that the
  duplicate incoming row does not raise "duplicate row" ambiguity on either engine, because both
  copies update the same existing target row to the same value.
- `tests/test_cookbook_snowpark.py`: the brief's Step 1 content verbatim, plus two tests it
  describes in prose but doesn't write out: `test_every_example_inputs_carry_a_null_and_a_
  duplicate_row` (mirrors the SQL harness's own guarantee, parametrized over all five tools) and
  `test_the_carry_over_pattern_promoted_to_a_full_procedure_passes_snowpark_rules` (see "Design
  decisions" below).
- `tests/test_cookbook_dbt.py`: the four tests the brief names in prose --
  `test_the_dbt_page_shows_the_executable_merge_model`,
  `test_the_example_inputs_carry_a_null_and_a_duplicate_row`,
  `test_the_merge_example_matches_the_simulator`,
  `test_running_the_example_writes_nothing_into_the_committed_project`.
- `tests/test_cookbook_examples.py`: added `TARGET_PAGES = ("snowpark", "dbt")`; `test_every_
  cookbook_tool_has_a_page` now subtracts it from the discovered page set before comparing against
  `COOKBOOK_TOOLS`.
- `cookbook/index.md`: a new `## Target pages` section (right after the per-tool page list, before
  "Local verification") linking both pages.

## Design decisions not spelled out in the brief

**The "full procedure" `check_proc_py` test (snowpark).** The brief says: "Where an example is a
full procedure, add a test that runs `scripts/lib/snowpark_rules.check_proc_py` on it with zero
violations." Every `example.py` on the page, carry-over included, is fixed by the brief's own Step
1 test code to be a plain `(session) -> DataFrame` function -- never a `def run(session, src_db,
src_schema, tgt_db, tgt_schema, run_id)`, and never itself calling `.save_as_table(...)` (the
harness does that, on the returned DataFrame). `check_proc_py` requires exactly one top-level `run`
matching that six-parameter signature and refuses `session` as a parameter of any *other* top-level
function -- so literally running it against any of these fragments (carry-over included) cannot
pass with zero violations; it would fail on `rule:signature` and (for a two-function module)
`rule:session_scope` regardless of what the function bodies do. Rather than fabricate a synthetic
`run()` wrapper by string-templating, I pointed the test at `samples/wf_0006/canned/segments/
seg_02/proc.py` -- a real, already-committed procedure that *is* this exact carry-over pattern
promoted to a full `run()` (same body, same schema, same source-of-truth this page's own "Parity
notes" cite by name) -- and asserted `check_proc_py(source, "wf_0006", "seg_02", contract) == []`,
which I confirmed directly before writing the test. This proves the pattern this page teaches,
written out as a real procedure, obeys every C4 rule with zero violations, without duplicating
`seg_02/proc.py`'s content into a second, synthetic copy.

**Merge example's write-mode vocabulary.** The Alteryx-level `dag.json` node config uses the
simulator's own vocabulary (`write_mode: "update_insert"`, matching `test_alteryx_sim.py`'s own
`test_update_insert_with_pre_and_post_sql`), not the migration-mapping-level `"merge"` string
`dbt_project.expected_model_config`/`intake/mappings.yaml` use -- these are different vocabularies
at different layers (confirmed by reading `dbt_fixtures.py`'s own `MAPPINGS` vs `DAG_SEG_02`), and
the case.json follows the DAG-level one since `_dag()`/`simulate()` are what actually consume it.

## Brief corrections

**Formula's Snowpark example uses `1.006`, not the SQL page's `1.005`.** The brief instructs
reusing the SQL examples' `input.csv` verbatim for filter/formula/summarize/sort ("copy the files;
the page says so"). I did, for three of the four; formula's copy changes one cell.

Proof: `formula.md`'s own Parity risk 1 documents that `ROUND(1.005::FLOAT, 2)` gives `1.00` on
this project's SQL runtime because `1.005`'s nearest IEEE-754 double is `1.00499999999999989…`,
and that the SQL pattern avoids it by casting to `NUMBER(38,10)` *before* rounding. I verified,
against `snowflake-snowpark-python` 1.55.0's actual Local Testing Framework (not by inspection
alone -- direct `session.select(...).show()`/`.collect()` probes, then read the mock's own source
to confirm the mechanism):

1. `functions.round(...)` raises `NotImplementedError` unconditionally -- there is no working
   `ROUND` DataFrame function to call at all, in any context (aggregate or not).
2. Every decimal-narrowing path this version implements -- `cast`/`try_cast` to a smaller-scale
   `DecimalType`, `to_decimal(...)`, and `to_char(...)`/`to_varchar(...)` -- routes through
   `snowflake/snowpark/mock/_functions.py`'s `mock_to_decimal`, whose `cast_as_float_convert_to_
   decimal` does `x = float(x)` (even when `x` is already a `decimal.Decimal`) and then
   `Decimal(str(round(x, remaining_decimal_len)))` -- Python's own built-in `round()`, on the raw
   binary64 value, every time.
3. For `x = float('1.005')` (`1.00499999999999989…`), `round(x, 2)` is `1.0`, not `1.01`. I
   confirmed the identical result end to end in a real local session: `try_cast(col("AMOUNT"),
   DecimalType(38,10))` on this value (a scale change large enough not to force any real
   rounding) does recover an exact `Decimal('1.005')` -- `round(x, 10)` happens to return a float
   whose `repr()` round-trips to `'1.005'` -- but narrowing *that* value to scale 2 re-enters the
   same `float(x)` branch (`float(Decimal('1.0050000000')) == 1.005`, the identical lossy double)
   and gives `1.00` again. `to_char`'s own Decimal-to-string path (`"{:.2f}".format(data)`) was
   also checked directly and is a dead end for a different reason: Python's `Decimal.__format__`
   rounds half-to-even by default, so even a *genuinely exact* `Decimal('1.005')` formats as
   `'1.00'`, not Alteryx's half-away-from-zero `'1.01'`.
4. I found no DataFrame-API function in this version (`floor`, `ceil`, `sign`, `trunc` are none of
   them `@patch`-registered either) that can implement "round half away from zero" manually without
   re-entering one of the two broken paths above.

Given `tests/test_cookbook_snowpark.py`'s compare call (fixed by the brief's Step 1) asserts
`report["verdict"] == "PASS"` with no `accepted_classes`, a one-cent difference is not tolerated
(TOLERANCES' own `rounding: {abs: 0.01}` only *classifies* such a difference as `ROUNDING`; it
still fails verdict `_verdict` without an explicit acceptance this call never supplies). There is
therefore no DataFrame-API-only Snowpark fragment that can reproduce the oracle's `1.005 -> 1.01`
on this local double.

**Smallest correction:** the Snowpark copy of `formula/input.csv` (only) replaces `1.005` with
`1.006` -- a value that rounds to `1.01` unambiguously under every rounding rule, so the fragment's
*correctness* (NULL/unparseable `AMOUNT_TXT`, the NUMBER-based discount arithmetic, `SIZE_BAND`'s
branching) is still checked end to end without exercising a Local Testing Framework bug that has no
DataFrame-API workaround. `cookbook/formula.md`'s own `tests/cookbook_examples/formula/input.csv`
(the SQL page's file) is untouched and still carries `1.005`; only the new, separate Snowpark copy
changed. `cookbook/snowpark.md`'s "What the local double does not prove" section documents this
finding in full, version-pinned, so it is checkable against a future `snowflake-snowpark-python`
upgrade.

**A second, smaller local-double finding (also documented on the page, no data change needed):**
the Local Testing Framework's `!=`/`==` operators do not implement three-valued NULL logic --
`col("REGION") != lit("WEST")` on a NULL `REGION` evaluates to `True` (not NULL), so a comparison
Column's own `.is_null()` never fires. I confirmed this directly (`df.select((col("REGION") !=
"WEST").alias("NE")).show()` on the filter example's own NULL-`REGION` row prints `True`). Filter's
`false_branch` therefore tests `col("REGION").is_null()` (the source column's own nullity) rather
than the brief's suggested `cond.is_null()` (the derived condition's), which is semantically
equivalent on real Snowflake (where the comparison *would* correctly propagate NULL) but is the
only version of the pattern that is actually correct against this specific local double.

## TDD evidence

**RED** -- `tests/test_cookbook_snowpark.py`/`tests/test_cookbook_dbt.py`/the `test_cookbook_
examples.py` exclusion were all written, and the two example directory trees populated, before
either `cookbook/snowpark.md` or `cookbook/dbt.md` existed. First run:

```
$ .venv/Scripts/python.exe -m pytest tests/test_cookbook_snowpark.py tests/test_cookbook_dbt.py \
    tests/test_cookbook_examples.py::test_every_cookbook_tool_has_a_page -v
...
FAILED tests/test_cookbook_snowpark.py::test_every_listed_tool_has_an_example_and_a_section
FAILED tests/test_cookbook_snowpark.py::test_snowpark_example_matches_the_simulator[filter]
FAILED tests/test_cookbook_snowpark.py::test_snowpark_example_matches_the_simulator[formula]
FAILED tests/test_cookbook_snowpark.py::test_snowpark_example_matches_the_simulator[summarize]
FAILED tests/test_cookbook_dbt.py::test_the_dbt_page_shows_the_executable_merge_model
5 failed, 12 passed in 9.00s
```

`cookbook/snowpark.md`/`cookbook/dbt.md` missing accounts for two of these (`FileNotFoundError`,
matching the brief's stated RED: "`cookbook/snowpark.md` missing"); `test_every_cookbook_tool_has_
a_page` passed immediately (also as the brief predicts: "still passes only after the exclusion" --
true before either page existed, since neither name was in the glob to begin with). The other three
failures were real bugs in my first-draft `example.py` files (see "Brief corrections" and the
column-order/cast-in-aggregate fixes below), found and fixed before either page was written, each
re-run individually to confirm the fix before moving on.

**GREEN** -- full cookbook suite:

```
$ .venv/Scripts/python.exe -m pytest tests/test_cookbook_dbt.py tests/test_cookbook_snowpark.py \
    tests/test_cookbook_examples.py -v
...
93 passed in 8.73s
```

**Full suite:**

```
$ .venv/Scripts/python.exe -m pytest
1382 passed in 201.54s (0:03:21)
```

Baseline was 1366 passed / 0 skipped; this task adds 16 tests (12 in `test_cookbook_snowpark.py` +
4 in `test_cookbook_dbt.py`; `test_cookbook_examples.py`'s own test count is unchanged), and
1366 + 16 = 1382 exactly. 0 skipped throughout.

## Files changed

- Created: `cookbook/snowpark.md`, `cookbook/dbt.md`, `tests/test_cookbook_snowpark.py`,
  `tests/test_cookbook_dbt.py`, `tests/cookbook_examples/snowpark/{filter,formula,summarize,sort,
  python_carry_over}/{case.json,example.py,input.csv,input.schema.json}`,
  `tests/cookbook_examples/dbt/merge/{case.json,input.csv,input.schema.json,history_before.csv,
  history_before.schema.json,project/{dbt_project.yml,profiles.yml,models/{sources.yml,
  history.sql}}}`.
- Modified: `tests/test_cookbook_examples.py` (`TARGET_PAGES`, `test_every_cookbook_tool_has_a_
  page`), `cookbook/index.md` (`## Target pages` section).

## Self-review findings

- Confirmed `tests/cookbook_examples/dbt/merge/project/profiles.yml` is byte-identical to
  `dbt_project.PROFILES_TEMPLATE` programmatically, not by eye.
- Confirmed no `__pycache__` directories from `importlib`-loading `example.py` modules were staged
  (they are `.gitignore`d; removed the stray ones from the working tree anyway before committing).
- Confirmed no absolute machine path, OS login name, or scratch-directory pointer appears in any
  new or modified file (`grep` across the whole changeset; `tests/test_committed_workflows.py`
  passes, 44/44).
- Verified the dbt merge example's duplicate-row scenario against both engines by hand before
  writing the test (not just trusting the oracle), since a source-side duplicate key is exactly the
  kind of thing that can silently diverge between a hand simulator and a real MERGE statement.

## Concerns

None outstanding. The two Local Testing Framework limitations documented above are real, checked
directly against the pinned `snowflake-snowpark-python` version, and written into the page itself
(not just this report) so a translator relying on this cookbook sees them.

## Fix round 1

Review of `6b6fdbf` confirmed both provisional conditions from the original review were met (the
explicit-NULL filter form is portable to real Snowflake; the `1.005` half-boundary rounding gap is
named as unprovable locally) and that the dbt half was solid. Four rulings remained, in
`task-E-fix1.md`; all four applied RED-first. Commit: `3f1843d`.

### C1 -- every Snowpark snippet on the page must pass the rules (`rule:tool_comments`)

`snowpark_rules._TOOL_COMMENT` is `^#\s*tool\s+(\S+)\s*:` -- it needs the colon immediately after
the tool id. `cookbook/snowpark.md`'s Filter and Formula snippets (and their `example.py` files)
wrote `# tool 2 (anchor T): …` / `# tool 2 (AMOUNT): …`, which that regex never matches (`\S+`
greedily consumes `2 ` up to the first non-space, backtracks to `2`, then needs a colon
immediately -- `" (anchor"` is not one). A translator pasting either fragment into a real `proc.py`
would fail `rule:tool_comments` silently, because the page itself was never run through the gate.

**Fix:** reworded every offending comment to put the tool id's colon first and the annotation
after it -- `# tool 2: Filter [REGION] != "WEST" -- True branch (anchor T): …` and `# tool 2:
Formula, AMOUNT -- …` / `, NET -- …` / `, SIZE_BAND -- …` -- in both `tests/cookbook_examples/
snowpark/{filter,formula}/example.py` and the matching fenced blocks on the page (kept byte-
identical, as `test_every_listed_tool_has_an_example_and_a_section` requires).

**New test:** `test_every_snippet_on_the_snowpark_page_passes_the_rules` (`tests/test_cookbook_
snowpark.py`). It extracts every fenced `python` block from the page, and for each top-level
function in it builds a synthetic module with that function promoted to a full `def run(session,
src_db, src_schema, tgt_db, tgt_schema, run_id)` -- the exact shape a translator pastes a
`(session) -> DataFrame` fragment into: rename the `def` line, and turn the function's own
`return <expr>` into `<expr>.write.mode("overwrite").save_as_table(<the one table a fixed
synthetic contract allows>)` followed by `return "OK"`. This is done with a **line slice off the
original source**, never `ast.unparse()` (which drops every comment -- and the comments are
exactly what C1 is checking). A fragment already shaped as `run(...)` with the full C4 signature
is yielded unwrapped. Every one of the five snippets' functions (filter's two, formula's,
summarize's, sort's, carry-over's) is asserted `check_proc_py(...) == []`.

**RED:** ran the new test against the pre-fix comments in two ways. First, the equivalent wrapper
logic against `git show 6b6fdbf:.../filter/example.py` and `.../formula/example.py` directly:

```
=== true_branch ===
ERRORS: ['rule:tool_comments: no `# tool 2:` comment']
=== false_branch ===
ERRORS: ['rule:tool_comments: no `# tool 2:` comment']
=== transform ===
ERRORS: ['rule:tool_comments: no `# tool 2:` comment']
```

Second, the real test against the real pre-fix page (`git show 6b6fdbf:cookbook/snowpark.md`
copied over the working file, then restored):

```
$ .venv/Scripts/python.exe -m pytest tests/test_cookbook_snowpark.py::test_every_snippet_on_the_snowpark_page_passes_the_rules -v
FAILED ... AssertionError: true_branch: ... ['rule:tool_comments: no `# tool 2:` comment']
1 failed in 0.18s
```

**GREEN:** `tests/test_cookbook_snowpark.py`: 13 passed (was 12).

### C2 -- the carry-over golden data must exercise the cancellation reset

`python_carry_over/input.csv`'s only `CANCELLED=true` row (`BETA/2026-02`) carried no deferred
balance into its own cancellation (its prior period's `CAP` was NULL, so nothing was ever
deferred), so dropping `if bool(row["CANCELLED"]): deferred = 0.0` from `example.py`'s
`transform()` still PASSed the harness -- the review's own probe against the original data found
exactly that.

**Fix:** added a third customer, `GAMMA`, whose `2026-01` period is genuinely capped (billed
`100.0` against cap `40.0`, carrying a deferred `60.0` into the next period) and whose `2026-02` is
the cancelled one. Regenerated the expected table through `dev.alteryx_sim.simulate` (never by
hand) and hand-verified the arithmetic against the printed oracle output:

```
GAMMA 2026-01 -> RECOGNIZED 40.0, DEFERRED 60.0   (billed 100, cap 40, deferred carries)
GAMMA 2026-02 -> RECOGNIZED 30.0, DEFERRED 0.0    (cancelled: reset drops the carried 60 first)
```

**New committed mutant:** `tests/cookbook_examples/snowpark/python_carry_over/
broken_dropped_reset.py` -- `example.py`'s own `transform()` with exactly the two reset lines
deleted (the NULL-`CANCELLED` guard above them deliberately left in place, so the diff against
`example.py` is the reset and nothing else), mirroring `samples/wf_0006/broken_sql/seg_02/
01_cancellation_reset_ignored.py`'s own mutation of the identical pattern one segment over.

**New test:** `test_the_carry_over_example_catches_a_dropped_reset` runs the same compare-harness
logic as `test_snowpark_example_matches_the_simulator` but against the mutant module, and asserts
`verdict == "FAIL"` with a `LOGIC` cluster naming `RECOGNIZED` or `DEFERRED`. Confirmed directly
before writing the assertion:

```
verdict: FAIL
diff_clusters: [{"class": "LOGIC", "columns": ["DEFERRED", "RECOGNIZED"], "count": 1,
  "example_rows": [{"expected": {"DEFERRED": 0.0, "RECOGNIZED": 30.0},
                     "actual": {"DEFERRED": 40.0, "RECOGNIZED": 50.0}}], ...}]
```

**RED:** ran the new test against the pre-fix `input.csv` (`git show 6b6fdbf:...` copied over the
working file, then restored) to reproduce the review's own finding:

```
$ .venv/Scripts/python.exe -m pytest tests/test_cookbook_snowpark.py::test_the_carry_over_example_catches_a_dropped_reset -v
FAILED ... AssertionError: ... "verdict": "PASS" ... assert 'PASS' == 'FAIL'
1 failed in 1.43s
```

**GREEN:** `tests/test_cookbook_snowpark.py`: 14 passed (was 13).

### I3 -- the dbt merge example must catch a narrowed `unique_key` at run time too

`tests/cookbook_examples/dbt/merge`'s `history_before.csv` had `EAST` naming only `ID` 1, so
`target.REGION = incoming.REGION` and `target.ID = incoming.ID` picked out the same single row --
narrowing the model's `unique_key` to `['REGION']` produced an identical result and PASSed the
runtime compare; only `compile_check.py`'s static `dbt:model_config` check (comparing `unique_key`
against the mapping's declared keys) would have refused it.

**Fix:** gave `EAST` a second row in `history_before.csv` (`ID` 3, `AMOUNT` 4.00). Verified,
directly and before writing the test, that this makes the two keys genuinely different at runtime
on **both** engines -- the oracle's `update_insert` write mode and a real `dbt run` against
DuckDB agree exactly:

```
unique_key=['ID']     (correct): (1,EAST,100.00) (2,NULL,50.00) (3,EAST,4.00) (7,WEST,7.00)
unique_key=['REGION'] (narrowed): (1,EAST,100.00) (1,EAST,100.00) (2,NULL,50.00) (7,WEST,7.00)
```

(`REGION`'s single incoming `EAST` row matches both of `HISTORY`'s `EAST` rows; the `UPDATE`/
`MERGE` overwrites both, including their `ID` column since it is a "shared", non-key column here
-- `ID` 3's own row is gone, replaced by a second copy of `ID` 1.)

**New test:** `test_the_merge_example_catches_a_narrowed_unique_key` (`tests/test_cookbook_dbt.py`)
-- a **scratch** copy of the committed project (`shutil.copytree` into `tmp_path`, per the
ruling's wording, not a committed mutant the way C2's is) with `models/history.sql`'s
`unique_key=['ID']` replaced by `unique_key=['REGION']`, run for real through `lib.dbt_project.
run_dbt`, judged against the correct (`keys=['ID']`) oracle output. Asserts `result.ok` (the run
itself still succeeds -- this is a silent LOGIC bug, not a crash) and `verdict == "FAIL"`.

**RED:** ran the new test against the pre-fix `history_before.csv` (`git show 6b6fdbf:...` copied
over the working file, then restored) to reproduce the review's own finding:

```
$ .venv/Scripts/python.exe -m pytest tests/test_cookbook_dbt.py::test_the_merge_example_catches_a_narrowed_unique_key -v
FAILED ... AssertionError: ... "verdict": "PASS" ... assert 'PASS' == 'FAIL'
1 failed in 4.11s
```

**Page addition:** `cookbook/dbt.md`'s "Materialisations" section gains one paragraph naming both
lines of defence explicitly -- `dbt:model_config`'s static check (before anything runs) and the
runtime `compare.py` judgment (over real merged data) -- and states plainly that a narrowed key
can pass one while failing the other, which is why both exist; also updated the "Executable
example" paragraph to describe the two-`ID` `EAST` fixture and point at the new test.

**GREEN:** `tests/test_cookbook_dbt.py`: 5 passed (was 4).

### Spec §9 -- name the two mock limits

Added to `docs/superpowers/specs/2026-09-22-output-targets-design.md` §9's Snowpark bullet (never
`docs/spec/**`, which stays frozen): the two gaps C1 and the original review's own finding already
established -- `==`/`!=` do not propagate NULL through a comparison, and there is no working
`round()` (every decimal-narrowing path reaches Python's own binary-float `round()` regardless of
source type) -- version-pinned to `snowflake-snowpark-python` 1.55.0, the version this was checked
against.

### Verification

```
$ .venv/Scripts/python.exe -m pytest tests/test_cookbook_dbt.py tests/test_cookbook_snowpark.py \
    tests/test_cookbook_examples.py -v
96 passed in 13.42s   (was 93; +3: C1, C2, I3's own new tests)

$ .venv/Scripts/python.exe -m pytest tests/test_committed_workflows.py
44 passed

$ .venv/Scripts/python.exe -m pytest
1385 passed in 208.87s (0:03:28)   (was 1382; +3, 0 skipped)
```

### Files changed this round

- `tests/cookbook_examples/snowpark/filter/example.py`, `.../formula/example.py`: tool-comment
  wording (C1).
- `cookbook/snowpark.md`: matching tool-comment wording in the fenced blocks (C1).
- `tests/test_cookbook_snowpark.py`: `test_every_snippet_on_the_snowpark_page_passes_the_rules`
  (C1); `test_the_carry_over_example_catches_a_dropped_reset` (C2).
- `tests/cookbook_examples/snowpark/python_carry_over/input.csv`: added `GAMMA` (C2).
- `tests/cookbook_examples/snowpark/python_carry_over/broken_dropped_reset.py`: new committed
  mutant (C2).
- `tests/cookbook_examples/dbt/merge/history_before.csv`: `EAST` gains a second `ID` (I3).
- `tests/test_cookbook_dbt.py`: `test_the_merge_example_catches_a_narrowed_unique_key` (I3).
- `cookbook/dbt.md`: the "two lines of defence" paragraph and an updated executable-example
  paragraph (I3).
- `docs/superpowers/specs/2026-09-22-output-targets-design.md`: §9's Snowpark bullet (§9 ruling).

### Self-review findings

- Every RED-first check in this round used the actual committed pre-fix state (`git show 6b6fdbf:
  <path>` copied over the working file, test run, then the fixed content restored from a scratch
  backup) rather than hand-argued RED, per the worktree rule against `git stash` for this purpose.
- Confirmed the I3 divergence on **both** engines (the oracle's `update_insert` write mode and a
  real `dbt run`) before writing the assertion, not just one -- a source-side non-unique join is
  exactly the kind of thing that could plausibly behave differently between a hand-written
  simulator and a real `MERGE`, and here they agree.
- Cleaned stray `__pycache__` directories under `tests/cookbook_examples/snowpark/*/` (created by
  `importlib`-loading `example.py`/the mutant module without `sys.dont_write_bytecode`) before
  committing; confirmed they are `.gitignore`d either way.
- Re-ran the full hygiene scan (`grep` for machine paths/usernames across every changed file,
  `tests/test_committed_workflows.py`) after this round's changes, not just the first.

### Concerns

None outstanding.
