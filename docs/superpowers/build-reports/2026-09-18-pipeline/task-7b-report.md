# Task 7b — `compare.py` classifies keyless streams

**Status: DONE_WITH_CONCERNS.** Everything the brief asks for is implemented, the suite is green
(800 passed, 2 skipped, no warnings, `-W error` clean) and three of the four sample variants now
produce the class the plan asked for. The concerns are all disclosure, not known breakage: one
binding ruling in the brief makes one of the four sample goals arithmetically unreachable
(§"Brief corrections" 2), and I added a bounded pairing budget the brief does not mention
(§"Brief corrections" 4). Neither can make a report pass that would not have passed before.

## What I implemented

`scripts/compare.py`, keyless path only (`_multiset_diff` and what it now calls). The keyed path
is byte-for-byte unchanged in behaviour; the one shared function I touched, `_value_clusters`,
takes a new keyword that only the keyless caller passes.

| Ruling | Where |
|---|---|
| 1. exact SQL `set_diff`, surplus rows fetched, bounded, `truncated` | `_multiset_counts`, `_surplus_rows`, `_multiset_diff` |
| 2. greedy nearest-match pairing, deterministic | `_pair_surplus`, `_differing_columns` |
| 3. a zero-difference pair is a MATCH, `set_diff` reduced, said out loud | `_multiset_diff` (writes into `normalizations_applied`) |
| 4. paired-with-differences rows go through the keyed classification | `_value_clusters(…, paired_by="nearest_match")` |
| 5. unpaired rows go through `_row_presence_cluster` | `_unpaired_side` + the existing `_row_presence_cluster` |
| 6. keyless accounting for a moved aggregate | `_KEYLESS_ROW_ACCOUNTABLE` + `_Diff.keyless` in `_unexplained` |
| 7. `needs_human` no longer set merely because rows differ | falls out: the catch-all `UNKNOWN` cluster is gone, so no check is unexplained |
| 8. no invented counts | `set_diff` is the SQL multiset difference less proved matches; cluster counts are accepted pairs / unpaired rows |

Details worth a reviewer's time:

* **The surplus fetch is one query per side.** SQL returns each surplus *distinct* row with its
  surplus multiplicity (`GROUP_ROWS` on this side minus `GROUP_ROWS` on the other), ordered by
  every column, and Python repeats each row that many times. Surplus copies of the same row are
  indistinguishable, so there is nothing to lose by not materialising them in SQL, and this avoids
  a `GENERATE_SERIES`/`LATERAL` construct that DuckDB and Snowflake spell differently.
* **`truncated` comes from the exact SQL totals**, not from how many rows came back:
  `max(only_expected, only_actual) > max_diff_rows`. So it is right even though the fetch itself is
  limited.
* **Pairing aborts early.** A candidate is abandoned as soon as it reaches `allowed + 1`
  disagreements (nothing over `allowed` can be accepted) or the best count found so far (ties go
  to the earlier row, which is the brief's rule). Determinism therefore does not depend on the
  abort: both the expected and the candidate list are in SQL `ORDER BY` order.
* **Acceptance** is `len(differing) <= max(1, (columns - 1) // 2)` (enforced by that abort limit)
  and `len(differing) < columns` ("at least one column is equal"), and `columns < 2` returns
  immediately — a one-column table is never paired.
* **A keyless value cluster declares `_sides = ("expected", "actual")`.** Each accepted pair is
  literally one surplus row from each side, so the cluster is what explains those rows' presence
  in `set_diff`. Without this every keyless value cluster would leave `set_diff` unaccounted for
  and the report would synthesise an `UNKNOWN` + `needs_human` — i.e. the brief's own test
  "ROUNDING accepted + an approval → `PASS_WITH_ACCEPTED_DIFF`" could not pass. See
  §"Brief corrections" 1.
* `_unexplained` now reads `_sides` off **every** cluster rather than only `scope == "rows"` ones.
  On the keyed path this is a no-op: nothing but a row cluster has ever set `sides` there
  (`_keyed_diff`'s duplicate-key and row-presence clusters), and the regression test
  `test_a_keyed_row_cluster_still_cannot_account_for_a_moved_aggregate` pins it.

## RED / GREEN evidence

**RED** — `tests/test_compare_keyless.py` written first, run before any change to `compare.py`
(HEAD `1360be7`):

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare_keyless.py
.FFFFFFFFFF..FF.                                                         [100%]
…
E       AssertionError: assert [('UNKNOWN', ... 'synthetic')] == [('NULL_SEMAN...], 2, 'rows')]
E         At index 0 diff: ('UNKNOWN', [], 2, 'rows') != ('NULL_SEMANTICS', ['REGION'], 2, 'rows')
E         Left contains one more item: ('UNKNOWN', ['CUSTOMER', 'ORDER_ID', 'QTY', 'TOTAL_NET'], 4, 'synthetic')
…
12 failed, 4 passed in 2.26s
```

The four that passed before the change are the ones that assert the *unchanged* half of the
contract: identical tables pass, and the two keyed regression guards. Every failure is the
expected one — the old keyless path returns `UNKNOWN / [] / "no keys: the rows differ but cannot
be paired"`, the moved aggregates become a synthetic `UNKNOWN`, and `needs_human` is `True`.
(Commit `58e75d5` is that file, alone, at RED.)

**GREEN** — after `8ddb082`, and again after the last commit:

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare_keyless.py
..................                                                       [100%]
18 passed in 3.47s

$ .venv/Scripts/python.exe -m pytest tests/test_compare.py tests/test_compare_keyless.py
75 passed in 17.21s

$ .venv/Scripts/python.exe -m pytest tests/test_e2e_parity.py tests/test_canned_artifacts.py
..ss..............................                                       [100%]
32 passed, 2 skipped in 9.33s

$ .venv/Scripts/python.exe -m pytest
800 passed, 2 skipped in 47.41s

$ .venv/Scripts/python.exe -m pytest -W error
800 passed, 2 skipped in 45.79s
```

Baseline before the task (same command, HEAD `1360be7`): **782 passed, 2 skipped in 42.53s**.
800 − 782 = the 18 tests in `tests/test_compare_keyless.py`. No test was deleted.

## Tests written

`tests/test_compare_keyless.py`, 18 tests. Every bullet of the brief's test list is there:

| Brief's bullet | Test |
|---|---|
| identical keyless with byte-identical duplicates → PASS, no clusters | `test_identical_keyless_tables_with_duplicate_rows_pass` |
| multiplicity differs → `LOGIC` rows, count 1, expected side, no human | `test_a_duplicate_row_lost_in_actual_is_a_logic_row_cluster` |
| NULL-REGION rows missing → `NULL_SEMANTICS`/`["REGION"]`, no synthetic | `test_missing_null_region_rows_are_null_semantics_and_need_no_human` |
| `"ALEXANDER"` vs `"ALEXA"` → `TRUNCATION`, `paired_by` | `test_a_string_left_untruncated_is_a_truncation_cluster` |
| case-only → `LOGIC` + `case_only` | `test_a_case_only_difference_keeps_its_hint` |
| 0.005 on three rows → `ROUNDING`, accepted only with an approval | `test_a_money_column_half_a_cent_out_is_rounding_and_can_be_accepted` |
| `1e-12` float noise → PASS, and the report says so | `test_float_noise_inside_the_tolerance_is_a_match_and_is_reported` |
| unrelated rows are not paired → two row clusters, no value cluster | `test_unrelated_rows_are_not_paired` |
| determinism, twice and under a shuffled physical order | `test_the_report_does_not_depend_on_how_the_rows_were_stored` |
| mixed truncation pair + missing row, `set_diff` = multiset − matches | `test_a_truncated_pair_and_a_missing_row_are_both_reported` |
| surplus beyond `max_diff_rows` → truncated, never PASS | `test_more_surplus_rows_than_max_diff_rows_is_truncated_and_never_passes` |
| `order_dependent_columns` renumbered → `ORDERING` | `test_the_same_rows_numbered_in_another_order_are_an_ordering_cluster` |
| keyed-path regression guard (rule 6 is keyless-only) | `test_a_keyed_row_cluster_still_cannot_account_for_a_moved_aggregate` |

…plus five I added for behaviour the brief describes in prose but does not test:

* `test_a_keyless_row_cluster_accounts_for_the_aggregates_the_missing_rows_moved` — rule 6(b)
  itself, as the mirror image of the keyed guard above (same monkeypatched aggregate failure, same
  data, only the contract's `keys` differ).
* `test_a_keyless_value_cluster_does_not_excuse_an_aggregate_on_another_column` — the *limit* of
  rule 6(b): it is about rows that moved, so with every surplus row paired there is no row cluster
  and an aggregate on an unnamed column is still `UNKNOWN` + `needs_human`.
* `test_a_keyless_stream_never_passes_on_a_difference_it_wrote_down` — the property test, over
  nine keyless shapes: a `PASS` means no check failed.
* `test_a_one_column_table_is_never_paired` — rule 2's explicit clause.
* `test_rows_past_the_pairing_budget_stay_unpaired` — the budget I added (below), including that
  the same data with the budget lifted reaches `PASS_WITH_ACCEPTED_DIFF`, so the budget's effect is
  visibly one-directional.

## Existing tests I had to change

One, exactly as the brief predicted.

`tests/test_compare.py::test_no_keys_uses_row_multiset`

```python
# before
assert run(edit(0, "AMOUNT", 1.0), contract=c)["diff_clusters"][0]["class"] == "UNKNOWN"

# after
# The counts are still a row multiset, but since task 7b the surplus rows are fetched, paired
# by nearest match and classified; tests/test_compare_keyless.py covers that path in full.
x = run(edit(0, "AMOUNT", 1.0), contract=c)["diff_clusters"][0]
assert (x["class"], x["columns"], x["paired_by"]) == ("LOGIC", ["AMOUNT"], "nearest_match")
```

Why: this is the test that literally asserted the old keyless `UNKNOWN` output. The six-column
fixture's surplus row differs in `AMOUNT` alone (1 ≤ `max(1, (6-1)//2)` = 2, and five columns
agree), so it is now paired; `|100.0 − 1.0|` is far outside `rounding.abs`, the column is not a
string and the values are not the same number in another notation, so `_classify` reaches `LOGIC`
with no hint. The first line of the test (`run(ROWS[::-1]) == "PASS"`) is untouched and still
proves the multiset itself is order-blind.

`tests/test_compare.py::_every_scenario`'s `"no keys, different"` row needed no change — the
property test `test_a_pass_verdict_always_means_every_check_passed` only requires that a
non-`FAIL` verdict leave nothing unaccounted for, and that scenario is still `FAIL`.

No other test in the repository changed. `tests/test_validate_segment.py`, `scripts/validate_segment.py`,
the golden data and every `proc.sql` are untouched (Task 14's reviewer is reading those).

## The sample variants: observed class before and after

Measured by running each `broken.json` row through `validate_segment` exactly as
`tests/test_e2e_parity.py` does.

| Variant | Before (recorded `expect`) | After (recorded `expect`) | Intended |
|---|---|---|---|
| wf_0001 `seg_01/01_filter_false_drops_null_region.sql` | `UNKNOWN` / `[]` / `3_F` + a synthetic `UNKNOWN` naming four columns, `needs_human: true` | **`NULL_SEMANTICS` / `["REGION"]` / `3_F`**, scope rows, suspect `t3_filter`, `needs_human: false` | ✅ reached — `intended`/`note` replaced with a note describing the new path |
| wf_0001 `seg_01/02_select_customer_not_truncated.sql` | `UNKNOWN` / `["CUSTOMER"]` / `3_F` | **`TRUNCATION` / `["CUSTOMER"]` / `3_F`**, scope columns, `paired_by: nearest_match` | ✅ reached |
| wf_0001 `seg_01/03_net_rounds_the_raw_float.sql` | `ROUNDING` / `["TOTAL_NET"]` / `6_Output` | unchanged (keyed stream) | n/a |
| wf_0001 `seg_01/04_filter_true_keeps_null_region.sql` | `NULL_SEMANTICS` / `["REGION"]` / `6_Output` | unchanged (keyed stream) | n/a |
| wf_0002 `seg_01/01_cleansing_without_upper.sql` | `UNKNOWN` / `[]` / `2_Output` | **`LOGIC` / `["CITY"]` / `2_Output`, hint `case_only`** — plus two `LOGIC` row clusters of five rows each | ⚠️ partly — see below |
| wf_0002 `seg_01/02_cleansing_keeps_null_names.sql` | `NULL_SEMANTICS` / `["NAME"]` / `2_Output` | still matches; the report now also carries a second `NULL_SEMANTICS`/`["NAME"]` cluster from the paired row | n/a |
| wf_0002 `seg_03/01_union_drops_the_join_left_branch.sql` | `UNKNOWN` / `["CUST_ID", "MATCH_FLAG"]` / `9_Output` | **`NULL_SEMANTICS` / `["AMOUNT", "ORDER_DATE", "ORDER_ID"]` / `9_Output`**, scope rows, suspect `t5_join` | ⚠️ different class — see below |

All seven still `FAIL`, which is what the fixture is for, and **every one of them now reports
`needs_human: false`** — which is the whole point of the task: `orchestrator/stages.ts` breaks the
fixer loop on `validation.needs_human`, and before this change every keyless mistake skipped the
fixer.

`intended`/`note` removed where observed == intended (wf_0001 01 and 02 keep a fresh `note`
explaining the mechanism, since the old one described behaviour that no longer exists).
`intended` kept for wf_0002 `seg_03/01`. The correct procedures still pass every golden set with
`idempotent: true` — `test_hand_migration_passes_every_golden_set` is green for wf_0001 and
wf_0002 (wf_0003/wf_0004 are the two pre-existing skips: no `canned/` yet).

## Brief corrections

### 1. Rule 6 as written is not sufficient for the brief's own ROUNDING test

Rule 6 loosens accounting for **per-column aggregate/count** checks on a keyless stream. It says
nothing about `set_diff`, which on a keyless stream is a *verdict-bearing* check
(`_check_failed("set_diff", …)` is `bool(only_expected or only_actual)`) and which only a cluster
carrying `_sides` can account for. With rule 6 alone, the brief's own required test —

> a money column off by 0.005 on three rows → `ROUNDING`/`["TOTAL_NET"]`; and with ROUNDING
> accepted + an approval for that segment/column → `PASS_WITH_ACCEPTED_DIFF`

— could not pass: `set_diff` would be `{3, 3}`, no cluster would answer for it, and
`_account_for_every_check` would add a synthetic `UNKNOWN` and force `FAIL`.

The smallest correction, and the one I made: a keyless value cluster declares the two sides its
rows came from. That is not a loosening but a statement of fact — an accepted pair *is* one
surplus row from each side, and after the pairing every surplus row is in exactly one cluster
(matched-within-tolerance rows having already left `set_diff`). It does not touch the keyed path,
where no value cluster has ever set `sides`, and it cannot be the sole justification for the
`counts` check either: if the row counts differ then one side has more surplus than the other, so
unpaired rows on that side exist by pigeonhole and a row cluster is always there too.

### 2. wf_0002 `01_cleansing_without_upper` cannot reach `LOGIC`/`case_only` on every row

Rule 2 fixes the acceptance threshold at `max(1, (n_columns - 1) // 2)`. Stream `2_Output` has
four columns (`CUST_ID`, `NAME`, `CITY`, `TIER`), so `(4 - 1) // 2 = 1`: a pair may disagree in
**one** column. The mistake (Data Cleansing translated without `UPPER`) changes `NAME` *and*
`CITY` on five of the six rows, which is two, so those five stay unpaired.

The sixth row is the exception and it rescues the demonstration: `CUST_ID 5` has
`NAME = "\N"` in `golden_inputs/normal/1.csv`, which the correct translation replaces with a blank
string, so its `NAME` is blank on both sides and `CITY "DARWIN"` vs `"Darwin"` is its whole
difference. It pairs, and comes out **`LOGIC` with hint `case_only`** — precisely the class and
hint the plan asked for. `expect` now pins that cluster (`columns: ["CITY"]`), and the `note`
records the five-row split.

I did **not** loosen the threshold to make the other five pair. It is a safety knob: pairing two
rows asserts "these are the same row, changed", and the looser it gets the more a genuine
"row missing + row added" can be re-told as a value difference of a class someone has approved.
Tightening it is safe, loosening it is not, and it is a binding coordinator ruling. **If the
coordinator wants the fuller demonstration, `columns // 2` (2 for a four-column stream) would do
it** — that is a decision for the coordinator, not for me.

### 3. wf_0002 `seg_03/01_union_drops_the_join_left_branch` produces `NULL_SEMANTICS`, not `LOGIC`

Brief-intended: `LOGIC`, rows missing. Observed: `NULL_SEMANTICS` / `["AMOUNT", "ORDER_DATE",
"ORDER_ID"]`. This is rule 5 working exactly as specified — `_row_presence_cluster` reaches
`NULL_SEMANTICS` *before* `LOGIC` when a column is NULL in every missing row and not NULL
throughout the golden data. The missing row is the unmatched left-join row (`MATCH_FLAG` =
`NO_ORDERS`), so its three order columns are NULL by construction. I recorded the observation and
kept `intended` + a note, as the brief instructs. I did not force it, and I would argue the
observed class is the better one: it names the three columns that explain the row, where `LOGIC`
would have named none.

### 4. I added a bound the brief does not mention: `_MAX_PAIRED_ROWS`

Nearest-match pairing is quadratic. Measured locally on a five-column table, the worst shape —
nothing close enough to pair with anything, so every candidate is scanned — costs about **1.3 s at
1,000 surplus rows a side, 5 s at 2,000 and 19 s at 4,000**; extrapolating, the default
`max_diff_rows` of 10,000 would sit there for minutes, per output stream, per golden set, inside
an agent fixer loop. So `_pair_surplus` pairs at most `_MAX_PAIRED_ROWS = 1000` rows per side and
leaves the rest unpaired.

Why this cannot hide anything: an unpaired row becomes a row-presence cluster of class
`NULL_SEMANTICS` or `LOGIC`, neither of which is in `mappings/global.yaml`'s
`accepted_diff_classes`, so the bound can only ever make a report **stricter**. The two directions
are pinned by `test_rows_past_the_pairing_budget_stay_unpaired`: with the budget at 1 the same
data is `FAIL` with a value cluster plus two row clusters; with the budget lifted it is
`PASS_WITH_ACCEPTED_DIFF` with the value cluster alone. The fetch bound the brief *does* specify
(`max_diff_rows` per side, and `truncated` beyond it) is implemented separately and exactly as
written.

### 5. `sides == ["expected"]` is not assertable from the report

The brief's second test asks for `sides == ["expected"]`. `compare()` pops `_sides` off every
cluster before returning (it is documented as internal), so no public report field carries it. The
test asserts the two things that *are* public and say the same: `set_diff ==
{"only_expected": 1, "only_actual": 0}` and the cluster's `note == "rows only in expected"`.

## Files changed

| File | What |
|---|---|
| `scripts/compare.py` | the keyless path; `_KEYLESS_ROW_ACCOUNTABLE` in `_unexplained`; `_Diff.keyless`; `paired_by` on `_cluster`; module docstring |
| `tests/test_compare_keyless.py` | new, 18 tests |
| `tests/test_compare.py` | one test updated (above) |
| `samples/wf_0001/broken_sql/broken.json` | variants 01 and 02 re-recorded |
| `samples/wf_0002/broken_sql/broken.json` | `seg_01/01` and `seg_03/01` re-recorded |
| `docs/superpowers/plans/2026-09-18-pipeline/02-sql-runtime-compare.md` | check 5 and the classification table's last row, both marked "amended by task 7b" |

`grep -rn "cannot be paired" --include=*.md --include=*.py .` now matches only the historical
records — this report, the Task 13a report, the Task 7b brief and `progress.md`, all of which
should keep saying what was true when they were written — plus one hit in
`.worktrees/task-14-fix/scripts/compare.py`, another agent's worktree, which is not mine to touch
and will pick this change up when it rebases.

## Self-review concerns

1. **Rule 6(b) is a real, if narrow, weakening of the accounting guard, and it is the thing to
   review hardest.** On a keyless stream an unpaired row-presence cluster now answers for the
   `aggregates` check on columns no cluster names. The justification is arithmetic and I believe
   it holds: with no keys `_comparable_set()` returns `None`, so `_aggregates` runs over the whole
   table, so a missing or extra row necessarily moves `SUM`/`MIN`/`MAX`/`AVG` (and the string
   lengths) of every column it carries a value in, and nothing can separate that movement from a
   value difference. Could an *independent* value difference hide behind it? Only if it were
   inside `_values_equal`'s per-row tolerance, and `_aggregate_equal`'s bound is exactly the
   accumulation of that same per-row tolerance, so a difference small enough to escape the pairing
   is too small to move the aggregates. The practical exposure is: a human who accepts and
   approves `LOGIC` for a keyless segment's missing rows is also, implicitly, accepting whatever
   those rows did to that stream's aggregates. The report still says both things out loud.
2. **Example rows on the keyless path show *normalized* values; the keyed path shows raw ones.**
   The multiset is grouped by `self._ref(...)`, i.e. after `trim:`/`upper:`, so the surplus fetch
   can only return normalized values — returning a raw value would need a non-deterministic
   `ANY_VALUE` per group. This only shows when a contract declares `normalizations`, and it is
   self-consistent (the values shown are the ones that were compared), but a reader comparing a
   keyed and a keyless cluster side by side could be surprised. Not fixed; flagged.
3. **The pairing is a heuristic and a wrong pairing is not detectable from the report** beyond the
   `paired_by: "nearest_match"` marker. I convinced myself this is inherent rather than a defect:
   in a multiset there is no fact of the matter about which copy is which, so "row A was rounded
   into row B" and "row A vanished and row B appeared" are two descriptions of the same data, and
   the threshold in rule 2 is what decides which one the report tells. That makes the threshold —
   not the greedy search — the safety-critical number, which is why I did not touch it (§2 above).
4. **Six GROUP BYs instead of two** when a keyless stream differs (`_multiset_counts` runs two,
   each `_surplus_rows` two more). Only when there *is* a difference; the clean case returns after
   the first query, as before. Snowflake would plan these independently. Not optimised.
5. `_MAX_PAIRED_ROWS` is a judgement call I made alone (§4 above). A coordinator who would rather
   have exactness than latency can raise it; nothing else depends on its value.

---

# Fix round 1

Applied against HEAD `43299b0` (Task 14's `validate_segment.py` fix and the coordinator's
doc-only `samples/*/canned` edits already merged; I touched neither). Suite at the end:
**826 passed, 2 skipped**, clean under `-W error`. Previous round ended at 800 + 2.

## IMPORTANT 1 — row-scope clusters are never approvable

**What changed.** `scripts/compare.py:354-374` — `_approved()` now opens with

```python
if cluster.get("scope") != "columns" or not cluster["columns"]:
    return False
```

and a docstring that says why: an approval record is `{segment, class, columns, approver, date}`,
so it can pin which *columns* a difference is allowed in and can never pin *which rows*, or how
many. Rows on one side only, a duplicated key, a schema failure and a synthetic cluster therefore
end in `FAIL` on **either** path, whatever `accepted_diff_classes` holds.

**On the `scope` question the ruling asked me to check:** `scope` is written by `_cluster()` onto
every cluster it builds and is *still present* when `_verdict` runs — `compare()` pops only
`_sides`, and only after `_verdict` has returned. So the test is never vacuous today. I used
`cluster.get("scope")` rather than `cluster["scope"]` anyway, so an absent `scope` reads as "not a
column cluster", i.e. not approvable, which is the safe way round; the docstring states that.

**RED (before the change), the reviewer's own probes:**

```
$ .venv/Scripts/python.exe .../t7brev/probe_a.py
   keyless: ('PASS_WITH_ACCEPTED_DIFF', 'FAIL', False, [('NULL_SEMANTICS', ['REGION'], 3, 'rows'), ...])
   run 1 (rows dropped, approved): ('PASS_WITH_ACCEPTED_DIFF', ...)
   run 2 (values rewritten)     : ('PASS_WITH_ACCEPTED_DIFF', ...)
$ .venv/Scripts/python.exe .../t7brev/probe_d.py
   with pairing (budget 1000): ('FAIL', [('LOGIC', ['QTY'], 6, 'columns')])
   budget exhausted      : ('PASS_WITH_ACCEPTED_DIFF', [('LOGIC', [], 6, 'rows'), ('LOGIC', [], 6, 'rows')])
   => budget made the report LOOSER
```

and as tests, written first (`git show 2cce143`):

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare_keyless.py
................F.FFF.F......                                            [100%]
...
>       assert r["verdict"] == "FAIL"
E       AssertionError: assert 'PASS_WITH_ACCEPTED_DIFF' == 'FAIL'
...
5 failed, 24 passed in 6.36s
```

**GREEN:** `29 passed` in that file; the same probes now read

```
   keyless: ('FAIL', 'FAIL', False, [('NULL_SEMANTICS', ['REGION'], 3, 'rows'), ...])
   run 1 (rows dropped, approved): ('FAIL', ...)     run 2 (values rewritten): ('FAIL', ...)
   with pairing (budget 1000): ('FAIL', ...)   budget exhausted: ('FAIL', ...)   => no flip here
```

**New tests** (`tests/test_compare_keyless.py`):

| Test | Scenario |
|---|---|
| `test_an_approval_can_never_cover_a_row_presence_cluster`, both parametrisations | probe_a (a), with a `["REGION"]` approval and with a `[]` one |
| `test_an_approval_for_missing_rows_does_not_cover_them_coming_back_wrong` | probe_a's two-run story |
| `test_keyed_rows_on_one_side_are_not_approvable_either` | the KEYED mirror: missing rows **and** a duplicated key |
| `test_the_pairing_budget_can_never_turn_a_fail_into_a_pass` | probe_d's budget flip |
| `test_a_value_cluster_is_never_the_sole_witness_for_a_count_difference` | the pigeonhole invariant (MINOR) |

**Existing tests that obtained `PASS_WITH_ACCEPTED_DIFF` by approving a row-scope cluster** — two,
both in `tests/test_compare.py`, both re-pointed at a column-scope diff as instructed:

1. `test_the_repro_passes_for_the_right_reason_under_the_corrected_sum_bound`. Its subject is the
   accumulated-tolerance sum bound, not the cluster, so I gave `repro()` a `finding` parameter:
   `repro(push, "value")` replaces the duplicated key 201 with a single row 201 whose `Z` is
   `0.5` instead of `0.0` — a `LOGIC` cluster on `["Z"]`, scope columns, which an approval can
   cover. The bound assertions are unchanged (`aggregates == "PASS"`,
   `aggregate_mismatches == {}`); `aggregates_rows` moves 200 to 201 because all 201 keys are now
   unique on both sides, and `column_mismatches == {"Z": 1}` is asserted as well. The old shape
   is kept, and is now its own test, `test_the_same_repro_with_a_duplicated_key_cannot_be_approved`,
   asserting `FAIL` — so `repro(0.99)` still exercises the duplicate-key path and
   `test_an_approved_row_cluster_cannot_excuse_an_aggregate_failure_elsewhere` keeps its subject.
2. `test_moved_rows_do_not_disturb_aggregates`. Here the missing row *is* the subject, so I kept
   the fixture and changed the verdict to `FAIL` (the aggregate assertions are untouched), then
   added the column-scope half to the same test: the same three keys with row 3 **present but
   wrong** gives a `LOGIC`/`["Z"]` cluster, `aggregates_rows == 3`, `aggregates == "FAIL"` and
   `PASS_WITH_ACCEPTED_DIFF`. The test now shows the contrast rather than losing it.

`_every_scenario` renamed its two affected entries — `"approved duplicate key"` to `"signed
duplicate key"` and `"approved missing rows"` to `"signed missing rows"` (both now `FAIL`, which
the property test allows) — and gained `"approved value diff beside cancelling magnitudes"`, so
the `PASS_WITH_ACCEPTED_DIFF` arm of `test_a_pass_verdict_always_means_every_check_passed` is
still reached from two independent shapes.

**Side effect worth a reviewer's eye:** a **schema** `TYPE` cluster (`scope == "schema"`) is now
unapprovable too. The ruling's test is `scope == "columns"`, and a schema cluster is not one; I
implemented it literally. No test or sample approved a schema cluster, so nothing moved, but it
does mean a column-type disagreement can no longer be signed off — which reads right to me (the
report's remaining checks are `SKIPPED` behind it, so there is nothing to sign *for*).

## THRESHOLD RULING — at least half the columns equal

**What changed.** `scripts/compare.py:962` — `allowed = max(1, (count - 1) // 2)` became
`allowed = count // 2`. `scripts/compare.py:982` — the dead branch `or len(best_differing) >= count`
(unreachable for `count >= 2`, since `count // 2 <= count - 1`) and its misleading comment
`# nothing left that has even one column in common` are gone; the "at least one equal column"
property is now stated in the docstring as a consequence of the arithmetic and pinned by
`test_an_accepted_pair_always_has_a_column_in_common`. The one-column early return is untouched.

**I was wrong in fix-round-0's brief correction #2** and the reviewer is right: I wrote that
"tightening is safe, loosening is not". It is the other way round. Tightening pushes a value
regression out of a cluster that *names the columns* and into a row-presence cluster that names
none — a strictly worse diagnosis for the fixer, and (before IMPORTANT 1) a vacuously approvable
one. The docstring at `scripts/compare.py:948-953` now says this in the code, so the next person
to reach for the knob sees which way it cuts.

**RED:** `test_two_of_four_columns_rewritten_still_pairs` failed with
`('LOGIC', [], 3, 'rows') != ('LOGIC', ['CITY', 'NAME'], 3, 'columns')`.
**GREEN:** that test and `test_more_than_half_the_columns_rewritten_does_not_pair` (three of four
columns differ, so still two row clusters) both pass; probe_thr's 4-column shape now reports
`('FAIL', 'agg=PASS', 'human=False', [('LOGIC', ['CITY', 'NAME'], 3, 'columns')])`.

Thresholds by column count, before to after: 2 cols 1 to 1, 3 cols 1 to 1, 4 cols 1 to **2**,
5 cols 2 to 2, 6 cols 2 to **3**, 7 cols 3 to 3. Only even column counts move, so the existing
five-column and three-column fixtures are unaffected; the four-column `2_Output` stream is the
one that changes.

## IMPORTANT 2 — the two false load-bearing comments

| Where | Was | Now |
|---|---|---|
| `compare.py:140-153` (`_MAX_PAIRED_ROWS`) | "the bound can only ever make a report stricter, never let one pass" | states that the bound cannot turn a `FAIL` into a pass *because* `_approved` refuses every cluster about rows, that the most it can do is trade a named value difference for an unapprovable "these rows are missing" — a worse diagnosis, never a weaker verdict — and records that before fix round 1 this was **not** true |
| `compare.py:62-70` (module docstring, rule 6) | "a report still cannot pass on them until a human has signed for exactly those rows" | says such a cluster cannot be signed off **at all**, so a report whose only finding is rows missing/extra/duplicated ends in `FAIL` on either path, and what rule 6 silences is an aggregate sitting behind a cluster that was already forcing a `FAIL` |
| `compare.py:28` and `compare.py:944` | "a minority of them" / "`max(1, (columns - 1) // 2)` ... a minority" | "at least half of the columns are equal" (`differing <= n // 2`) |
| `compare.py:38-42` (verdict bullet) | — | now states that only a cluster about columns can be approved at all |

## MINORS

* **Pigeonhole test** — `test_a_value_cluster_is_never_the_sole_witness_for_a_count_difference`:
  one value diff plus one genuinely missing row gives `counts` `{5, 4, FAIL}`, clusters
  `[NULL_SEMANTICS/["REGION"] rows, ROUNDING/["TOTAL_NET"] columns]`, and `FAIL` even with
  ROUNDING accepted and signed for `TOTAL_NET` — because the row cluster beside it can never be.
  (The invariant itself: a pair consumes one surplus row from each side, so whenever the row
  counts differ the heavier side keeps unpaired rows and a row cluster is always there.)
* **Dead branch** — removed, see above.
* **Determinism test made real** — `test_the_report_does_not_depend_on_how_the_rows_were_stored`
  is now parametrized over three shapes (a value diff beside a missing row; NULLs together with
  byte-identical duplicates; a table that is nothing but duplicates) and, for each, compares the
  byte-identical JSON of the report (after popping `runtime_ms`) against six
  `random.Random(seed).shuffle` permutations of **both** tables. The old version reversed one
  list once.
* `validate_segment.py`'s `normalizations_applied` dedupe left alone, as instructed.

## Broken variants, re-recorded

Re-run through `validate_segment` exactly as `tests/test_e2e_parity.py` does. All seven still
`FAIL` with `needs_human: false`.

| Variant | Fix round 0 | Fix round 1 |
|---|---|---|
| wf_0001 `01_filter_false_drops_null_region` | `NULL_SEMANTICS` / `["REGION"]` / rows | unchanged |
| wf_0001 `02_select_customer_not_truncated` | `TRUNCATION` / `["CUSTOMER"]` / columns | unchanged |
| wf_0001 `03_net_rounds_the_raw_float` | `ROUNDING` / `["TOTAL_NET"]` | unchanged (keyed) |
| wf_0001 `04_filter_true_keeps_null_region` | `NULL_SEMANTICS` / `["REGION"]` + `LOGIC` rows | unchanged (keyed) |
| wf_0002 `seg_01/01_cleansing_without_upper` | `LOGIC` / `["CITY"]` hint `case_only`, count 1, **plus two `LOGIC` row clusters of 5** | **`LOGIC` / `["CITY", "NAME"]` / columns, hint `case_only`, count 6 — one cluster, all six rows, no row clusters** |
| wf_0002 `seg_01/02_cleansing_keeps_null_names` | `NULL_SEMANTICS` / `["NAME"]` | unchanged |
| wf_0002 `seg_03/01_union_drops_the_join_left_branch` | `NULL_SEMANTICS` / `["AMOUNT", "ORDER_DATE", "ORDER_ID"]` / rows | unchanged |

`samples/wf_0002/broken_sql/broken.json` `seg_01/01` re-recorded to
`{"class": "LOGIC", "columns": ["CITY", "NAME"], "stream": "2_Output", "hint": "case_only"}` with
a note describing the single cluster; `intended` was already gone and the old note (which quoted
the retired threshold formula) is replaced. **Observed now equals intended for that variant**, so
nothing is left claiming a gap. `wf_0001`'s two rows were re-checked and are unchanged.
`wf_0002 seg_03/01` keeps its `intended` + note (still `NULL_SEMANTICS`, for the reason given in
fix round 0's brief correction 3).

## Suite

```
$ .venv/Scripts/python.exe -m pytest tests/test_compare.py tests/test_compare_keyless.py
89 passed in 22.73s
$ .venv/Scripts/python.exe -m pytest tests/test_e2e_parity.py tests/test_canned_artifacts.py
32 passed, 2 skipped in 9.66s
$ .venv/Scripts/python.exe -m pytest
826 passed, 2 skipped in 55.39s
$ .venv/Scripts/python.exe -m pytest -W error
826 passed, 2 skipped in 55.50s
```

This round adds **12** tests: `tests/test_compare_keyless.py` 18 → 29 (5 for the approval,
budget and pigeonhole rulings — one of them parametrised twice, so 6 test ids; 3 for the
threshold; and the single determinism test becoming a family of 3) and `tests/test_compare.py`
59 → 60 (the duplicate-key shape split out of the sum-bound test). The rest of the gap from fix
round 0's 800 arrived with Task 14's merged fix, which I did not touch.

## Docs updated

* `scripts/compare.py` — module docstring (checks list, the verdict bullet, the rule-6 paragraph),
  `_approved`, `_pair_surplus`, `_MAX_PAIRED_ROWS`.
* `docs/superpowers/plans/2026-09-18-pipeline/02-sql-runtime-compare.md` — check 5's threshold
  wording, and the **Verdict** paragraph now records that only a column-scope cluster naming
  columns can be matched by an approval.
* `samples/*/canned/**` mention "nearest-match pairing, a heuristic" without quoting a threshold,
  so the coordinator's doc edits there needed no change.

## What the re-reviewer should look at

1. **The literal reading of the ruling makes a schema `TYPE` cluster unapprovable as well.** Nothing
   in the repo depended on it, but it is a behaviour change beyond "row-scope", and it is the one
   place where I extended the ruling's wording to a cluster kind it did not name.
2. **`repro()`'s new `finding` parameter** — whether the value variant really still exercises the
   accumulated-tolerance sum bound. It does: `aggregates == "PASS"` with `Z` pushed 0.99 on 200
   cancelling rows of +/-1e9, and the `avg` check lands at 0.9876 against a bound of 0.9950, which
   is the narrowest margin in the file and would move if anyone changed `float_rel`.
3. **Rule 6(b) after this change.** It now sits behind an unapprovable cluster in every case I can
   construct, so it can no longer convert a `FAIL` into a pass — but it still suppresses the
   synthetic `UNKNOWN` and therefore `needs_human`, which is what routes a keyless mistake to the
   fixer instead of a human. That is the intended behaviour, and probe_a's
   "6(b) disabled/enabled" pair shows exactly what it changes now: `needs_human` and the synthetic
   cluster, not the verdict.
