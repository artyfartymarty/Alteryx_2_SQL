# Task 7 — `scripts/compare.py` — report

**Status:** DONE_WITH_CONCERNS. Fix round 1 is on `wt/task-7-fix`, fix round 2 on
`wt/task-7-fix2`; see the "Fix round 1" and "Fix round 2" sections at the end of this file.

Worktree: `C:\Users\<user>\Desktop\Alteryx to Snowflake\.worktrees\task-7`, branch `wt/task-7`.
Nothing outside that worktree was edited; no existing file was modified.

## What I implemented

`scripts/compare.py` — the deterministic parity comparison between a golden (expected) table and
the table a translated Snowflake procedure wrote. Public surface:

```python
compare(backend, expected, actual, contract, tolerances, *, output=None, accepted_classes=(),
        approvals=(), segment_dag=None, golden_set=None, sample_rows=5, max_diff_rows=10000) -> dict
select_output(contract, stream=None) -> dict
main(argv=None) -> int          # the CLI of program spec §9.2
```

Checks in the brief's order, stopping early only on a schema failure. Everything about the data is
SQL through `backend.query` in Snowflake dialect, built from the contract's column list (never
`SELECT *`); only a bounded mismatch sample is pulled into Python.

1. **Schema** — expected vs actual in full (names upper-cased, in order, type *family* per column,
   missing/extra listed), and the contract's column list required to name the same columns as both
   sides, because every statement below is built from it.
2. **Counts** — row counts, plus each column's NULL count and distinct count
   (`COUNT_IF(x IS NULL)`, `COUNT(DISTINCT x)`), one query per side.
3. **Aggregates** — `SUM/MIN/MAX/AVG` per numeric column, `MIN/MAX(LENGTH(x))` per string column,
   compared with the same tolerance rules as values.
4. **Keyed diff** — key uniqueness per side (`GROUP BY … HAVING COUNT(*) > 1`), `only_expected` /
   `only_actual` via `NOT EXISTS` anti-joins, and the disagreeing rows via a join on the keys where
   any non-key column `IS DISTINCT FROM` its counterpart, `LIMIT max_diff_rows + 1`.
5. **Row multiset** — when `keys` is empty: both sides grouped by every column with counts, joined
   `FULL OUTER … ON NOT (… IS DISTINCT FROM …)`, reporting only the two totals.
6. **Tolerances** — in Python over the pulled rows: `abs(e-a) <= max(float_abs, float_rel·max(|e|,|a|))`
   for `float` columns, exact for `number` columns unless `contract.tolerances[COL].float_abs` says
   otherwise (that override also applies to `number` columns), timestamps truncated to
   `timestamp_precision`. `-0.0 == 0.0` and `Decimal("1") == 1.0` fall out of Python's own numeric
   equality, which is tried before any tolerance.

Classification, cluster shape, `suspect_cte` from the segment DAG, the verdict rules and the 0/1/2
exit codes are as the brief specifies. `suspect_cte` reads the config fields the dag contract
defines per tool type (formula `field`, summarize/select `rename`, multi-row/record-id `field`,
regex `output_fields`/`match_field`/`field`, datetime `out_field`, cross-tab headers, transpose
`Name`/`Value`) and picks the *last* writer in topological order; row-presence clusters pick the
last `filter`/`join`/`unique`/`sample`.

**Determinism.** Cluster order is `GOLDEN_DATA ORDERING NULL_SEMANTICS ROUNDING TRUNCATION TYPE
LOGIC UNKNOWN`, then the column list, then the cluster note. Columns inside a cluster are sorted.
Example rows come back in key order because every `LIMIT` query carries `ORDER BY` over all
selected positions, and the mismatch sample is carried as an ordered list of rows (not a per-column
map) so a multi-column cluster keeps that order. Verified across two separate CLI processes: the
two `validation.json` files differ only in `runtime_ms`.

## The `validation.json` it writes

Follows `docs/spec/02-schemas-reference.md` exactly, plus the fields listed under "Departures":

```json
{"segment", "golden_set", "verdict", "checks", "diff_clusters", "normalizations_applied",
 "idempotent": null, "runtime_ms", "credits": null, "needs_human", "truncated"}
```

`checks`: `schema`, `counts`, `aggregates`, `set_diff`, `column_mismatches`, `column_counts`,
`aggregate_mismatches` — every one of them `"SKIPPED"` when a schema failure stopped the run.

`needs_human` is true when the diff points away from the SQL: duplicate keys in the golden data
(`GOLDEN_DATA`), or a schema failure in which the contract disagrees with the *expected* table.

## Tests — `tests/test_compare.py`, 37 tests

The brief's 16 tests **verbatim**, plus 21 for behaviour the brief describes in prose but does not
test: `output`/`--stream` selection, contract tolerance overrides (float and `number`), the
tolerance boundary in exact decimals, timestamp precision, duplicate keys in actual,
`max_diff_rows` truncation, report shape and JSON-safety, run-to-run determinism, multi-column
cluster grouping and hint-based splitting, two-sided row presence in a stable order, the
`case_only` hint, the `TYPE` value class, missing/extra columns in a schema failure, the "100% NULL
in expected" guard on the row-presence NULL rule, per-column counts and aggregate-mismatch detail,
an unknown normalization, a missing table, and the CLI's exit-0 path.

## TDD evidence

RED — before `scripts/compare.py` existed:

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare.py
ERROR collecting tests/test_compare.py
E   ModuleNotFoundError: No module named 'compare'
1 error in 0.24s
```

GREEN — `tests/test_compare.py`:

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare.py
..................................... [100%]
37 passed in 4.13s
```

Full suite (run once before the final commit):

```
$ .venv/Scripts/python.exe -m pytest
........................................................................ [ 24%]
........................................................................ [ 48%]
........................................................................ [ 72%]
........................................................................ [ 96%]
..........                                                               [100%]
298 passed in 6.32s
```

Output is pristine: no warnings, no stray prints.

### CLI smoke run (beyond the tests)

Ran the real CLI over a typed CSV, a `.duckdb` sandbox, `mappings/global.yaml`, a `manifest.json`
with an approval and a `dag.json`, on a `NUMBER(19,2)` money column:

- no `--manifest`: `FAIL: 1 diff clusters: ROUNDING(AMOUNT)`, exit 1, `suspect_cte: "t7_formula"`;
- with the signed approval: `PASS_WITH_ACCEPTED_DIFF: 1 diff clusters: ROUNDING(AMOUNT)`, exit 0;
- `--db` under a non-existent directory: exit 2.

## Bug found and fixed during self-review

The CLI smoke run classified an exact one-cent difference (`250.01` vs `250.02`, a
`NUMBER(19,2)` column) as `LOGIC` instead of `ROUNDING`. `rounding.abs` is `0.01` and the values
are exactly on that boundary, but `float(250.02) - float(250.01)` is `0.010000000000019`, just
over it. `|e-a|` is now computed with `Decimal` whenever both values are decimal-representable —
for the `ROUNDING` rule and for a contract `float_abs` alike — so the threshold sits where the
tolerance says it does. This is the case `ROUNDING` exists for (money), so it would have misfired
on real work. Covered by
`test_a_threshold_is_where_the_tolerance_says_it_is_not_where_binary_floats_put_it`, which also
pins the other side: `float_abs: 0.009` still fails.

## Brief corrections (two, both small and argued)

1. **`TRUNCATION` must not swallow a trailing-blank difference.** The brief's rules are evaluated
   in order, and `TRUNCATION` ("one value is a proper prefix of the other") comes before `LOGIC`.
   But `"Bo"` *is* a proper prefix of `"Bo "`, so rule 4 would claim the brief's own
   `test_whitespace_hint`, which requires `LOGIC` + `hint: "whitespace_only"`. The two tests are
   only both satisfiable if truncation means content was lost: `_is_truncation` therefore also
   requires the extra suffix to contain something other than blanks. `"Chandrasekhar"` vs
   `"Chandrasek"` (the brief's `test_truncation`) is unaffected.
2. **Test data for the `TYPE` value class.** Not a change to the brief — the brief has no `TYPE`
   value test — but worth recording: because `TRUNCATION` is evaluated first, a `TYPE` example has
   to avoid being a prefix. `"0100"` vs `"100"` is the case I used; `"100"` vs `"100.0"` would be
   classified `TRUNCATION`, correctly per the rule order.

## Departures from the brief's letter (deliberate, please review)

1. **Two extra `checks` fields.** The brief says the report is the program schema "plus
   `checks.column_mismatches` and `truncated`". I also write `checks.column_counts` (only the
   columns whose NULL or distinct count differs) and `checks.aggregate_mismatches` (only the
   aggregates that differ, with both values). Spec §9.2 requires checks 2 and 3 to compute those
   numbers, the schema pins `checks.counts`/`checks.aggregates` to a shape with nowhere to put
   them (and the brief's own `test_identical_passes` pins `checks.counts` to exactly three keys),
   and "numbers about data come only from scripts … nothing is silent" says a computed number must
   not be thrown away. Both are `{}` when nothing differs, so a passing report is unchanged in
   substance. Easy to drop if the reviewer disagrees.
2. **`truncated` is top-level, not under `checks`.** The brief's sentence prefixes only the first
   of the two added fields with `checks.`.
3. **Normalizations are applied in the SQL as well as in Python.** The brief puts them under
   check 6 ("applied in Python to the pulled rows"). I wrap the declared `trim:`/`upper:` around
   the column in every statement too (counts, distincts, aggregates, joins, grouping) and keep the
   Python application for the pulled values. Otherwise a report can say `PASS` while its own
   `aggregates` check says `FAIL` for a column whose difference the contract explicitly declared
   away. `example_rows` still show the raw stored values, not the normalized ones. Python's `trim`
   strips spaces only (`.strip(" ")`), matching what SQL `TRIM` does, rather than `.strip()`.
4. **The schema check also holds the contract to account.** The brief says "column names compared
   upper-cased and in order; type family per column" without saying against what. I compare
   expected vs actual in full (that is the parity question) *and* require the contract's column
   list to name the same columns as both sides, because every statement is built from that list —
   a contract naming a column the golden table lacks would otherwise fail as an opaque
   `BackendError`. A contract/expected disagreement sets `needs_human`, since it points at the
   contract or the golden data, not at SQL.
5. **Verdict is cluster-driven only**, per the brief's verdict rules: a failing `aggregates` check
   does not by itself make the verdict `FAIL`. The brief says this explicitly for the rounding
   case ("the verdict is FAIL unless an approval is supplied").

## Notes for downstream tasks

- **`type_family` needed no change.** It already covers every DuckDB spelling this task meets:
  `VARCHAR`→string, `DOUBLE`→float, `DECIMAL(38,0)`→number, `DATE`→date, `TIMESTAMP`→timestamp,
  `BOOLEAN`→bool. Nothing was added inside `compare.py` to bridge it. Two spellings I did *not*
  meet would fall through to `"other"` if a procedure ever produced them: `TIMESTAMP WITH TIME
  ZONE` and `HUGEINT`. Because `"other"` means "unknown", `compare.py` does not treat two `"other"`
  types as the same family — it compares the declared type names instead, so an unknown type pair
  cannot pass silently.
- **`_Column.family` comes from the contract's Snowflake type, not from DuckDB.** That is what
  decides `number` (exact) vs `float` (tolerant), and on a real account it is the type that exists.
- **Golden data caveat found while smoke-testing, not a `compare.py` issue.** Loading a typed CSV
  whose `FixedDecimal(19,2)` cell holds `250.005` stores `250.01`: the value is rounded to the
  declared scale on insert, by DuckDB and by Snowflake alike. Whoever writes golden data for
  decimal columns (Tasks 8/9/13) should keep values inside the declared scale, or the golden file
  and the golden table will not agree.
- **Two `LIMIT`ed pulls are samples, everything else is exact.** `set_diff` totals, per-column
  NULL/distinct counts, aggregates, duplicate-key totals and the "NULL in every missing row"
  judgement all come from SQL over the whole table. Only `example_rows` and the per-column
  mismatch counts are bounded — by `sample_rows` and `max_diff_rows` — and `truncated: true` says
  so when the cap was hit.

## Files changed

- `scripts/compare.py` (new, 1002 lines)
- `tests/test_compare.py` (new, 277 lines)

## Concerns

1. The two extra `checks` fields and the three other departures above are judgement calls; each is
   argued and each is cheap to reverse.
2. `scripts/compare.py` is 1002 lines — about 2.5× the largest existing script (`parse.py`, 427).
   The plan gives `compare.py` as one file and this is the whole of Task 7 (six checks, eight diff
   classes, the SQL for both diff modes, the DAG suspect lookup and the CLI), so I did not split it
   on my own initiative, per the implementer rules. If the reviewer wants it split, the natural
   seam is the classification predicates plus the DAG helpers moving to a sibling module.
3. Nothing here has run on Snowflake or on Alteryx. Locally, "parity" means DuckDB agrees with
   DuckDB; the SQL is Snowflake dialect transpiled by `lib/backend.py`, which is the only evidence
   that it would run on an account.

---

# Fix round 1

Worktree `.worktrees/task-7-fix`, branch `wt/task-7-fix`. All four rulings implemented. The
reviewer's Critical was real, and both repro paths are now covered by tests that fail against the
merged code and pass against this one.

## 1. The invariant: a report may never pass on a difference it wrote down

`compare()` now ends with two steps that nothing above them can talk its way past:

- `_account_for_every_check(checks, diff, max_diff_rows)` gives every failing check that no
  cluster accounts for a cluster of its own —
  `{"class": "UNKNOWN", "columns": [...], "count": ..., "example_rows": [], "suspect_cte": null,
  "hint": "<check> failed with no row-level difference"}` — and sets `needs_human`. A `truncated`
  diff with no cluster at all gets `hint: "diff truncated at max_diff_rows before every difference
  was examined; raise --max-diff-rows"`.
- the guard: `if diff.truncated or _unexplained(checks, diff): verdict = "FAIL"`. It re-derives the
  unexplained set itself rather than trusting the synthesis step, and `truncated: true` is always
  a `FAIL`.

`_check_failed(name, value)` is the single place that says whether a `checks` entry reported a
difference. It **raises** for an entry that is declared neither a verdict nor detail, so a future
`checks` key cannot quietly slip past the guard —
`test_every_checks_entry_is_classified_as_a_verdict_or_as_detail` pins that.

Each cluster carries an internal `_scope` (`"schema"`, `"rows"`, `"columns"`, `"synthetic"`),
stripped before the report is returned, and `_Diff.evidence` records `{check: (columns, count)}`.
A check is accounted for when a cluster could have caused it: a row-scoped cluster explains any
number in the report (rows appearing or vanishing move all of them), a column-scoped cluster
explains its own columns.

`checks.column_counts` and `checks.aggregate_mismatches` are declared detail rather than verdicts.
`aggregate_mismatches` is only the breakdown behind `aggregates`; `column_counts` is deliberate —
two values inside tolerance are still two *distinct* values, so a distinct-count difference is not
by itself a failure, and treating it as one would fail runs that the tolerance policy passes.

**One deviation from the ruling's letter, argued.** The ruling says a `PASS_WITH_ACCEPTED_DIFF`
verdict also requires every `checks` entry to be `"PASS"`. That is not satisfiable together with
the brief's own `test_rounding_can_be_accepted_with_an_approval` (verbatim, must keep passing): an
approved `ROUNDING` diff on `AMOUNT` necessarily makes `checks.aggregates` `"FAIL"` for that same
column — the brief says so explicitly ("the aggregate check will also FAIL for that column, which
is correct and expected"). So the implemented rule is:

- `PASS` — every check passed and nothing was truncated (the strong form, unchanged);
- `PASS_WITH_ACCEPTED_DIFF` — nothing truncated, and every failing check is accounted for by a
  cluster that a human approved;
- `FAIL` — otherwise.

`test_a_pass_verdict_always_means_every_check_passed` asserts the strong form over 17 scenarios
(identical, float noise, each diff class, schema failure, no keys both ways, duplicate keys, rows
only in expected/actual, count-only, aggregate drift, truncated), none of which carries an
approval; `test_an_accepted_diff_may_leave_a_failing_check_only_when_a_cluster_covers_it` pins the
one exception, including that the failing aggregate's columns are a subset of the cluster columns.

## 2. SUM tolerance

`_aggregate_equal(column, aggregate, mine, theirs)` gives `SUM` a threshold of
`max(n_rows * float_abs, float_rel * max(|e|, |a|))` with `n_rows = max(expected_rows,
actual_rows)`; `AVG`, `MIN`, `MAX` and the string length aggregates keep the per-value tolerance.
The per-column `contract.tolerances[COL].float_abs` override feeds it through the same
`_tolerance()`, and an exact `number` column with no override still compares exactly.

## 3. No crowding

`_differs(column)` replaces the bare `IS DISTINCT FROM` for any numeric column that has an
absolute tolerance (float family, or a `number` column the contract loosened — the ruling says
float family; including the loosened `number` case is the same argument closing the same hole):

```
((e.c IS NULL) <> (a.c IS NULL) OR ABS(e.c - a.c) > <float_abs>)
```

Non-numeric and exact `number` columns keep `IS DISTINCT FROM`, and the Python tolerance still
runs on everything pulled, so the relative part can still clear a row. The same predicate is what
produces `checks.column_mismatches`, so the counts and the clusters cannot disagree. Two edges I
checked rather than assumed: both sides NULL makes the second half NULL, which `WHERE` treats as
"no difference" — correct; and a NaN is still pulled, because DuckDB and Snowflake both order NaN
above every number, so `ABS(x - NaN) > tol` is true (verified in DuckDB).

The float-vs-exact-decimal gap at the boundary that I first disclosed here as a residual turned
out to be reachable, and is now closed by `_PRE_FILTER_MARGIN` — see the follow-up section at the
end of this file.

## 4. Nullability

New check 3, on the data, after the schema check, using the NULL counts check 2 already computed
(no extra counting queries; only the example rows are pulled). For each contract column with
`"nullable": false`: NULLs in **actual** produce a `NULL_SEMANTICS` cluster on that column, count
= number of NULLs, example rows by key, note `NULL in a column the contract calls NOT NULL`; NULLs
in **expected** produce a `GOLDEN_DATA` cluster with the same note plus `, in the golden data`,
and `needs_human: true`. Recorded as `checks.nullability` = `"PASS"` / `"FAIL"` / `"SKIPPED"`.
DDL-level nullability is not compared. The brief's CONTRACT declares every column `nullable: True`,
so the 16 brief tests are untouched.

## TDD evidence

RED — the fix-round-1 tests against the merged `scripts/compare.py`
(`git checkout 96b523c -- scripts/compare.py`):

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare.py \
    -k "noise or crowd or truncated or failing_check or pass_verdict or nullab or classified"
FAILED tests/test_compare.py::test_per_row_noise_within_tolerance_does_not_accumulate_into_a_sum_failure
FAILED tests/test_compare.py::test_noise_rows_cannot_crowd_out_a_real_difference
FAILED tests/test_compare.py::test_a_failing_check_with_no_cluster_is_never_a_pass
FAILED tests/test_compare.py::test_a_truncated_diff_with_no_surviving_difference_is_still_a_failure
FAILED tests/test_compare.py::test_a_truncated_diff_is_a_failure_even_when_every_cluster_is_approved
FAILED tests/test_compare.py::test_a_pass_verdict_always_means_every_check_passed
FAILED tests/test_compare.py::test_every_checks_entry_is_classified_as_a_verdict_or_as_detail
FAILED tests/test_compare.py::test_a_null_in_a_non_nullable_column_of_actual_is_a_null_semantics_diff
FAILED tests/test_compare.py::test_a_null_in_a_non_nullable_column_of_expected_needs_a_human
FAILED tests/test_compare.py::test_nulls_in_a_nullable_column_are_not_a_nullability_failure
10 failed, 3 passed, 36 deselected in 1.47s
```

The two headline assertions, verbatim from that run:

```
repro (a)  assert r["checks"]["aggregates"] == "PASS" and r["checks"]["aggregate_mismatches"] == {}
           E  AssertionError: assert ('FAIL' == 'PASS')       # and the verdict was PASS anyway
repro (b)  assert r["truncated"] is False and r["verdict"] == "FAIL"
           E  assert (True is False)                          # the real difference was never pulled
```

An earlier RED run of the same tests, before I rewrote the two 10,000-row fixtures to build their
rows in SQL, showed the same 10 failures (`10 failed, 39 passed in 60.71s`).

GREEN:

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare.py
................................................. [100%]
49 passed in 6.38s

$ .venv/Scripts/python.exe -m pytest
........................................................................ [ 91%]
.........................................                                [100%]
473 passed in 10.81s
```

CLI re-run, where behaviour should be unchanged: ROUNDING with no approval →
`FAIL: 1 diff clusters: ROUNDING(AMOUNT)`, exit 1; with the signed approval →
`PASS_WITH_ACCEPTED_DIFF`, exit 0, `checks.nullability: "PASS"`, `truncated: false`.

## Tests added (12)

`test_per_row_noise_within_tolerance_does_not_accumulate_into_a_sum_failure` (10,000 rows × 5e-7),
`test_per_row_noise_outside_tolerance_is_a_failure` (10,000 × 5e-6),
`test_noise_rows_cannot_crowd_out_a_real_difference`,
`test_a_failing_check_with_no_cluster_is_never_a_pass` (monkeypatched, as the ruling directs),
`test_a_truncated_diff_with_no_surviving_difference_is_still_a_failure` (timestamps below the
declared precision are pulled but are not differences, so they can still fill `max_diff_rows`),
`test_a_truncated_diff_is_a_failure_even_when_every_cluster_is_approved`,
`test_an_accepted_diff_may_leave_a_failing_check_only_when_a_cluster_covers_it`,
`test_a_pass_verdict_always_means_every_check_passed` (17 scenarios),
`test_every_checks_entry_is_classified_as_a_verdict_or_as_detail`, and three nullability tests.

The two 10,000-row fixtures build their rows with one `INSERT … SELECT` over a 100-row cross join
instead of `load_table`: pushing 20,000 rows through `executemany` took 24 seconds per fixture and
dominated the whole suite (88s → 6.3s), while what the fixture is for is the arithmetic, not the
loading. The row count is exactly the 10,000 the ruling specified, and the test asserts it.

## Files changed in this round

- `scripts/compare.py` (1002 → 1196 lines)
- `tests/test_compare.py` (277 → 460 lines, 37 → 49 tests)

## Concerns from this round

1. The `PASS_WITH_ACCEPTED_DIFF` rule — **confirmed by the coordinator** and now the ruling, and
   stated in those words in the module docstring. No open question remains.
2. `scripts/compare.py` is now 1217 lines. The same note as before applies: the plan gives it as
   one file, so I have not split it on my own initiative.

## Fix round 1, follow-up: the pre-filter margin (concern 3)

The disclosed residual turned out to be reachable, not theoretical. Searching near the boundary
for a pair whose exact decimal difference exceeds the tolerance while its binary subtraction does
not finds plenty; the one the test uses is:

```
expected 0.19999999999999965   actual 0.09999999999999964   float_abs 0.1
exact decimal difference  0.10000000000000001   -> over the tolerance: a real difference
binary subtraction        0.1                   -> not over `> 0.1`: the row was dropped
```

So the pre-filter was not a strict superset of what the exact test could flag. Fixed:
`_PRE_FILTER_MARGIN = 1e-9` (relative) now comes off the SQL threshold, which for a `float_abs`
of 0.1 makes the predicate `ABS(e - a) > 0.09999999990000001`. Justification, in the constant's
own comment: rounding error in that subtraction is at most half an ulp of the result, about 1e-16
relative, so a 1e-9 margin is seven orders of magnitude clear of it, while the only extra rows it
pulls are those within 1e-9 of the boundary — which `_values_equal` then judges as usual. The
pull's predicate is still the predicate behind every reported count.

`_differs` is now documented as choosing *candidates*, never as judging, and the module docstring
says what the counts therefore are: candidates are chosen in SQL, differences are decided in
Python, so `checks.column_mismatches` and every cluster `count` are counted after the exact test
and are exact unless `truncated` says the pull was cut short, in which case they are lower bounds.
(There is no separate SQL-side mismatch count in this script; the numbers come from the pulled
rows after the exact test, which is why they can be described as exact at all.)

The module docstring also now states the verdict rule in full, in the coordinator's words, as the
contract the rest of the pipeline trusts: `PASS` requires every check to have passed and nothing
truncated; `PASS_WITH_ACCEPTED_DIFF` requires nothing truncated and every failing check to be
accounted for by a cluster a human approved; anything else is `FAIL`.

### TDD evidence

RED — before the margin (the row is invisible; note the verdict was already FAIL, because the
invariant guard from the first round synthesized an `UNKNOWN` cluster out of the failing aggregate
— the guard working exactly as intended, but the row-level difference still unseen):

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare.py -k pre_filter
>       assert r["checks"]["column_mismatches"] == {"AMOUNT": 1} and r["verdict"] == "FAIL"
E       AssertionError: assert ({} == {'AMOUNT': 1}
E         Right contains 1 more item: {'AMOUNT': 1})
>       assert cmp._PRE_FILTER_MARGIN == 1e-9
E       AttributeError: module 'compare' has no attribute '_PRE_FILTER_MARGIN'
2 failed, 49 deselected in 0.24s
```

GREEN:

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare.py
................................................... [100%]
51 passed in 6.71s

$ .venv/Scripts/python.exe -m pytest
475 passed in 11.21s
```

### Tests added (2)

- `test_the_sql_pre_filter_never_excludes_a_row_the_exact_test_would_flag` — asserts both halves
  of the trap on the found pair (`_abs_difference(...) > 0.1` and `abs(e - a) <= 0.1`), then that
  the row is pulled, counted in `column_mismatches` and classified `LOGIC`.
- `test_the_pre_filter_margin_is_far_wider_than_float_subtraction_error` — pins the margin, so
  shrinking it towards the 1e-16 error floor is a deliberate act.

### Files changed in this follow-up

- `scripts/compare.py` (1196 → 1217 lines)
- `tests/test_compare.py` (460 → 481 lines, 49 → 51 tests)

---

# Fix round 2

Worktree `.worktrees/task-7-fix2`, branch `wt/task-7-fix2`. Both rulings implemented; the
re-reviewer's finding was real and is the kind of mistake my round-1 design invited — I made
accounting a single global flag instead of asking what each cluster actually explains.

## Ruling 2 — accounting is specific, never global

`_unexplained` used `rows_moved = any(cluster["_scope"] == "rows" …)`, a flag over the whole
report, and then `accounted = rows_moved or set(columns) <= explained`. Any row cluster anywhere
therefore excused any failing check anywhere. Deleted. In its place:

- a `"rows"` cluster (rows on one side only, or a duplicated key) accounts for the row-count check
  and `set_diff`, **and only on the side its rows are on** — each row cluster carries the internal
  `_sides` it covers, and `_sides_needed()` reads from `checks` which side would have to be
  explained (`expected` when expected has more rows, `only_actual` needs an `actual` cluster, and
  so on);
- a `"columns"` cluster accounts for exactly the columns it names;
- a `"schema"` cluster accounts for the schema check;
- nothing accounts for anything else, so `aggregates_rows` — and any future table-wide check —
  always synthesizes.

`scope` is now **reported** rather than stripped, so the invariant is checkable by a reader who
does not trust the code that produced the report. `test_every_cluster_says_what_it_can_account_for`
pins the mapping, and the scenario test's `_unaccounted()` helper is written entirely against the
public report, using no helper from `compare.py`.

### Comparable set

When the contract declares keys, the aggregates and the per-column null/distinct detail are now
computed over the keys that occur **exactly once on both sides**:

```sql
WITH COMPARABLE_KEYS AS (
  SELECT <keys> FROM <expected> GROUP BY <keys> HAVING COUNT(*) = 1
  INTERSECT
  SELECT <keys> FROM <actual>   GROUP BY <keys> HAVING COUNT(*) = 1
)
SELECT <aggregates> FROM <side> T WHERE EXISTS (SELECT 1 FROM COMPARABLE_KEYS WHERE …)
```

A row that moved or was duplicated is one difference and is reported once, by `set_diff` and its
own cluster; it no longer also disturbs the arithmetic of every column. `checks.aggregates_rows`
is `{"rows": n, "verdict": …}` — the size of that set, which is also the `n` in the SUM bound —
and having **nothing** comparable while both tables hold rows is itself a failing check, because
no approval should pass off a comparison that compared nothing.

Nullability deliberately keeps whole-table counts: a NULL in a `NOT NULL` column is a contract
violation wherever it sits, including in a row that moved. So `_count_differences` computes both
(whole-table, kept on the instance for nullability and for the row-presence NULL rule; comparable,
for what the report shows).

## Ruling 1 — the SUM bound (your correction, and it was right)

`max(n*float_abs, float_rel*|Σ|)` collapses when large values cancel: 200 rows of ±1e9 sum to 0,
and a sum of 0 grants no tolerance at all while each of those rows is entitled to 1.0. Replaced by
the triangle-inequality bound, derived in `_aggregate_equal`'s docstring:

```
|Σe − Σa| ≤ Σ|eᵢ − aᵢ| ≤ Σ max(float_abs, float_rel·max(|eᵢ|,|aᵢ|))
                      ≤ n·float_abs + float_rel·Σ max(|eᵢ|,|aᵢ|)
```

`SUM(ABS(col))` is computed in the same query as `SUM(col)` (label `sum_abs`, never reported —
it is an input to the bound, not a claim about the data). `AVG` uses the bound over `n`;
`MIN`, `MAX` and the string lengths keep the per-value tolerance; an exact `number` column with no
override still compares exactly.

**One note on the arithmetic, not a disagreement.** The derivation's last term is
`Σ max(|eᵢ|,|aᵢ|)`, and what one query per side can produce is `max(Σ|eᵢ|, Σ|aᵢ|)`. Those are not
equal, and the inequality runs the wrong way: `Σ max ≥ max(Σ, Σ)`. So the implemented bound is a
slight *under*-estimate — the check is if anything stricter than the true bound, which is the safe
direction (it can only cause a FAIL, never a pass). The gap is negligible where it matters: a row
that is within tolerance has `|eᵢ|` and `|aᵢ|` within tolerance of each other, so
`Σ max ≤ max(Σ|e|, Σ|a|) + B` and the computed bound is within a factor `(1 + float_rel)` of the
true one. I implemented your formula as ruled and recorded the reasoning in the comment.

## TDD evidence

RED — the round-2 tests against the merged `scripts/compare.py`:

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare.py
FAILED tests/test_compare.py::test_the_repro_passes_for_the_right_reason_under_the_corrected_sum_bound
FAILED tests/test_compare.py::test_an_approved_row_cluster_cannot_excuse_an_aggregate_failure_elsewhere
FAILED tests/test_compare.py::test_moved_rows_do_not_disturb_aggregates
FAILED tests/test_compare.py::test_cancelling_magnitudes_with_noise_inside_the_declared_tolerance_pass
FAILED tests/test_compare.py::test_no_comparable_rows_at_all_is_itself_a_failure
FAILED tests/test_compare.py::test_every_cluster_says_what_it_can_account_for
FAILED tests/test_compare.py::test_a_pass_verdict_always_means_every_check_passed
7 failed, 51 passed in 8.22s
```

The bypass itself, verbatim from that run — the assertion is `verdict == "FAIL"`:

```
E  AssertionError: assert ('PASS_WITH_ACCEPTED_DIFF' == 'FAIL')
```

GREEN:

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare.py
........................................................... [100%]
59 passed in 14.02s

$ .venv/Scripts/python.exe -m pytest
558 passed in 21.70s
```

The re-reviewer's input, run standalone (not through the test suite):

```
verdict     : PASS_WITH_ACCEPTED_DIFF | needs_human: False
aggregates  : PASS {}
rows        : {'expected': 201, 'actual': 202, 'verdict': 'FAIL'} {'rows': 200, 'verdict': 'PASS'}
clusters    : [('LOGIC', ['ID'], 'rows', 'duplicate keys in actual')]
```

That verdict is the same string it was before, but it is now reached honestly: the aggregate
passes because the corrected bound says 198 of drift on 2e11 of magnitude is declared noise, the
row count is accounted for by the duplicate-key cluster on the side that has the extra row, and
that cluster is approved. Nothing is being waved through.

CLI re-run: ROUNDING with no approval → `FAIL: 1 diff clusters: ROUNDING(AMOUNT)`, exit 1; with the
signed approval → `PASS_WITH_ACCEPTED_DIFF`, exit 0, with `aggregates_rows: {"rows": 2,
"verdict": "PASS"}` and the cluster reporting `scope: "columns"`.

## Tests added (8)

1. `test_the_repro_passes_for_the_right_reason_under_the_corrected_sum_bound` — the re-reviewer's
   input exactly, with a comment saying why the pass is now correct.
2. `test_an_unrelated_row_cluster_cannot_excuse_a_real_difference_on_another_column` — same shape,
   `Z` pushed past its own tolerance so per-row clusters form → `FAIL`, 200 mismatches on `Z`.
3. `test_an_approved_row_cluster_cannot_excuse_an_aggregate_failure_elsewhere` — the direct
   regression: a monkeypatched aggregate failure on `Z` beside an approved duplicate-key cluster
   on `ID` → `FAIL`, synthesized `UNKNOWN` on `["Z"]`, `needs_human`.
4. `test_moved_rows_do_not_disturb_aggregates` — approved row-presence cluster → aggregates over
   the comparable set `PASS`, verdict `PASS_WITH_ACCEPTED_DIFF`; without the approval, `FAIL`.
5. `test_cancelling_magnitudes_with_noise_inside_the_declared_tolerance_pass` — and clearly
   outside → per-row clusters and `FAIL`.
6. `test_no_comparable_rows_at_all_is_itself_a_failure`.
7. `test_two_empty_tables_compare_clean` — see below.
8. `test_every_cluster_says_what_it_can_account_for`, plus the scenario sweep extended with five
   approved shapes and the stronger property, asserted from the report alone.

## A latent bug this surfaced

DuckDB's `COUNT_IF` returns NULL over an empty set where Snowflake's returns 0. The count detail
crashed with a `TypeError` the moment the comparable set could legitimately be empty — and the
same crash was already reachable through the program's own `empty` golden set, before any of this
round's changes. `COALESCE`d, and pinned by `test_two_empty_tables_compare_clean`.

## Files changed in this round

- `scripts/compare.py` (1217 → 1340 lines)
- `tests/test_compare.py` (481 → 646 lines, 51 → 59 tests)

## Concerns from this round

1. `scope` is now a reported field on every cluster, beyond the shape the brief lists. It is what
   makes the verdict checkable from the report rather than on trust, which the ruling asked for.
2. The `Σ max` vs `max(Σ, Σ)` note above — implemented as ruled, erring strict; flagged only so
   nobody later "simplifies" it in the unsafe direction.
3. `scripts/compare.py` is 1340 lines. Same standing note: the plan gives it as one file.
