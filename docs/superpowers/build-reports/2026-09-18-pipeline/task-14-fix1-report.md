# Task 14 fix round 1 report

Worktree: `.worktrees/task-14-fix` (branch `wt/task-14-fix`, based on `1360be7`). Only
`scripts/validate_segment.py` was modified; only `tests/test_validate_segment_fix_round_1.py` was
added. `scripts/compare.py`, `tests/test_compare.py`, samples and golden data were not touched.

## Findings, what changed, evidence

### K1 -- top-level verdict must be worst-of-all-sets, not "first non-passing"

**Change:** `scripts/validate_segment.py:272-285` -- new `_aggregate_sets(sets, reports)` helper:
verdict = `_worst_verdict()` across every processed set's own verdict; body = `dict(chosen)` where
`chosen` is the first set (in `sets` order) whose own verdict equals that worst verdict;
`needs_human` = `any(...)` across every set, independent of which set supplied the body; `sets`
lists every set's own verdict. Wired in at `validate_segment()` (`scripts/validate_segment.py:444`,
`result = _aggregate_sets(sets, reports)`), replacing the old
`next((r for r in sets if r["verdict"] != "PASS"), reports[sets[-1]])` selection and the separate
`result["sets"] = {...}` line.

**RED** (`tests/test_validate_segment_fix_round_1.py::test_k1_end_to_end_worst_of_all_sets_beats_first_non_passing`,
against the pre-fix code):
```
assert report["sets"] == {"normal": "PASS_WITH_ACCEPTED_DIFF", "edge": "FAIL"}
>   assert report["verdict"] == "FAIL"
E   AssertionError: assert 'PASS_WITH_ACCEPTED_DIFF' == 'FAIL'
```
(exactly K1's repro: normal=PASS_WITH_ACCEPTED_DIFF, edge=FAIL -> old code stopped at the first
non-passing set and reported PASS_WITH_ACCEPTED_DIFF at the top level.)
The two `_aggregate_sets` unit tests failed with `AttributeError: module 'validate_segment' has no
attribute '_aggregate_sets'` (the helper didn't exist yet).

**GREEN:** all three K1 tests pass (see full run below), including the needs_human any-of case
(`test_k1_aggregate_sets_worst_wins_body_and_needs_human_is_any_of`, unit-testing the aggregation
directly per the brief, since real `compare()` output can't easily produce "chosen set says
needs_human=False but an earlier set said True").

### K2 -- a failed idempotency check FAILS validation

**Change:**
- `_outputs_equal` (`scripts/validate_segment.py:178-192`) now returns `(bool, list[str])` instead
  of a bare `bool`: a table missing on either side of the two runs counts as diverging (never
  silently "equal"), and the diverging table names are returned.
- `_run_one_set`'s idempotency block (`scripts/validate_segment.py:343-362`): if the second run's
  `_load_and_run` itself raises `ProcError`/`BackendError`, idempotency is `False` with every
  declared output table counted as diverging (no second run means it can never be verified as
  idempotent). Otherwise uses `_outputs_equal`'s result. `report["idempotency_diff"]` is always
  set; if not idempotent, `report["verdict"] = _worst_verdict([report["verdict"], "FAIL"])` --
  forcing FAIL regardless of what `compare()` said about that one run's output.
- `_combine`/`_fail_report` (`scripts/validate_segment.py:247-248`, `267`) now default
  `idempotency_diff: []` alongside the existing `idempotent: None` placeholder.
- `validate_segment()` (`scripts/validate_segment.py:428-442`) captures and propagates
  `idempotency_diff` from the first golden set onto every set's report, the same way `idempotent`
  already propagated.

**RED** (`test_k2_nondeterministic_column_fails_validation_even_though_compare_alone_would_pass`):
```
assert report["idempotent"] is False
>   assert report["idempotency_diff"], "expected the diverging table name(s) to be listed"
E   KeyError: 'idempotency_diff'
```
This is the reviewer's exact repro: a `UNIFORM(1, 1000000, RANDOM())` AMOUNT column with a
contract column tolerance (`float_abs: 2000000`) wide enough that `compare()` alone -- one run's
value against a fixed golden value -- always passes. Before the fix, `report["idempotent"]` was
already `False` (that part worked) but nothing read it: `report["verdict"]` stayed whatever
`compare()` said (PASS), and there was no `idempotency_diff` key at all.

**GREEN:** both K2 tests pass -- the nondeterministic case now gets `verdict == "FAIL"`,
`idempotency_diff` naming `MIG_WORK.WF0009_SEG_01_OUT` (and the target table), CLI exit 1; the
deterministic case keeps `idempotent is True` and `idempotency_diff == []`.

### C3 -- empty/missing `contract.outputs[]` is a usage error

**Change:** `validate_segment()`, right after reading the contract
(`scripts/validate_segment.py:393-396`):
```python
contract = read_json(contract_path)
if not contract.get("outputs"):
    raise ValueError(f"{wf_id}/{seg} contract.json has no outputs[] to validate (plan "
                     f"contract C5 requires at least one); translate this segment first")
```
This runs before any golden set is touched, alongside the other prerequisite checks.

**RED** (`test_c3_cli_exits_two_and_writes_nothing`, golden data present so the *only* problem is
the empty `outputs[]`):
```
>   assert done.returncode == 2 and "Traceback" not in done.stderr
E   assert (0 == 2)
E   ... stdout="wf_0009/seg_01: PASS {'normal': 'PASS'} (idempotent=True)\n"
```
Confirms C3 exactly: pre-fix, an empty `outputs[]` was a vacuous PASS with `idempotent: True` and
zero comparisons, exit 0. The two direct-call tests failed with `Failed: DID NOT RAISE ValueError`.
(First draft of these three tests, without golden data, accidentally "passed" pre-fix for the wrong
reason -- a `FileNotFoundError` from a missing golden CSV also exits 2 -- so I added golden data to
make sure the RED/GREEN distinction is caused by the empty-outputs check itself, not a coincidental
fixture gap.)

**GREEN:** all three C3 tests pass -- `ValueError` for `outputs: []` and for a contract with no
`outputs` key at all, CLI exit 2, `Traceback` absent from stderr, no `validation.json` written.

### C4(a) -- a declared output table the procedure never created is a domain FAIL

**Change:**
- New `_missing_table_report(actual_fqn)` (`scripts/validate_segment.py:204-215`): shaped like one
  of `compare.compare()`'s own per-output reports (`verdict: FAIL`, empty checks/clusters) plus an
  `"error"` naming the table.
- `_run_one_set`'s per-output loop (`scripts/validate_segment.py:329-338`): checks
  `backend.table_exists(actual_fqn)` itself before calling `compare.compare()`; missing -> uses
  `_missing_table_report` instead of letting `compare.compare()` raise
  `ValueError("there is no table … to compare")`.
- `_combine` (`scripts/validate_segment.py:239-255`): collects any per-output `"error"` and joins
  them (`"; ".join(errors)`) onto the merged report's own `"error"` key, exactly as it already
  merges verdict/checks/clusters/needs_human/truncated -- nothing is re-derived, just carried up.
- `_outputs_equal`'s missing-table handling (described under K2) covers the idempotency side of
  this same finding: a missing table can never make idempotency look `True`.

**RED** (`test_c4a_missing_output_table_is_a_domain_fail_naming_the_table`):
```
ValueError: there is no table MIGDB.MIG_WORK.ITEMS_OUT to compare
    scripts\compare.py:373: ValueError
```
raised out of `validate_segment()` uncaught -- exactly C4's bug: this landed in the CLI's usage-error
bucket. `test_c4a_cli_exits_one_and_writes_a_report` confirmed the CLI symptom: `assert 2 == 1`.

**GREEN:** both tests pass -- `verdict == "FAIL"`, `"ITEMS_OUT"` present in both the returned
report's and the on-disk `validation.json`'s `"error"`, CLI exit 1, report written.

### C4(b) / I5 -- every invocation clears stale `validation*.json` first

**Change:** new `_clear_stale_reports(repo, wf_id, seg)` (`scripts/validate_segment.py:288-302`),
called as the very first line of `validate_segment()` (`scripts/validate_segment.py:387`), before
even the `contract.json` existence check. Deletes exactly `validation.json` (exact match) and
`validation.*.json` (glob, which cannot also match the bare `validation.json`) in that one segment
directory -- nothing else. Module docstring updated
(`scripts/validate_segment.py:48-55`) to state this precisely.

**RED:**
- `test_c4b_a_later_usage_error_leaves_no_report_behind`:
  ```
  >   assert not repo.seg(WF, SEG, "validation.json").exists()
  E   AssertionError: assert not True
  ```
  (a PASS run's `validation.json` survived a later run that hit a usage error.)
- `test_i5_a_golden_set_no_longer_requested_loses_its_report`: same assertion failure for
  `validation.edge.json` surviving a re-run with `["normal"]` only.
- `test_unknown_segment_directory_absent_is_still_a_clean_exit_two` already passed pre-fix (no
  regression risk there; kept as a regression guard).

**GREEN:** all pass -- after a usage error, `list(repo.seg(WF, SEG).glob("validation*.json")) == []`;
after re-running with fewer sets, only the still-requested set's report file remains; an unknown
segment (directory absent) still exits 2 cleanly.

### Minor -- `checks` key shape

`test_checks_key_shape_is_stream_colon_kind` asserts `set(report["checks"]) == {"2_T:work",
"2_T:target"}` and that each value is exactly `compare.compare()`'s own `checks` dict (e.g.
`{"expected": 1, "actual": 1, "verdict": "PASS"}` for `counts`). This already passed pre-fix (no
bug here) -- added as a direct regression test per the brief's "minor to include", not a fix.

## Existing tests: no changes needed

All 17 tests in `tests/test_validate_segment.py` still pass unmodified. None of them exercised
multiple golden sets with differing verdicts, a failed-idempotency verdict assertion, empty
`outputs[]`, a missing output table, or a stale-report survival case, so none of the five rulings
changed behaviour any existing assertion depended on. **Before/after list: none** -- no existing
test was edited.

## Test evidence

Focused suite (existing + new fix-round-1 tests):
```
"<venv>/Scripts/python.exe" -m pytest tests/test_validate_segment.py tests/test_validate_segment_fix_round_1.py -v
tests\test_validate_segment.py .................                         [ 54%]
tests\test_validate_segment_fix_round_1.py ..............                [100%]
31 passed in 8.78s
```
(17 existing + 14 new = 31, all green, output pristine.)

Full suite from the worktree root:
```
"<venv>/Scripts/python.exe" -m pytest
796 passed, 2 skipped in 46.01s
```
The 2 skips are the known wf_0003/wf_0004 e2e cases (unrelated to this fix; other in-flight work in
sibling worktrees is why the total count differs from Task 14's original 721/5 -- `compare.py` and
its tests are being edited concurrently by another agent in the main tree, and Task 13's canned
samples have since landed). No warnings in the output.

## Files changed

- `scripts/validate_segment.py` (modified) -- see diffs above; new helpers `_missing_table_report`,
  `_aggregate_sets`, `_clear_stale_reports`; `_outputs_equal`/`_combine`/`_fail_report`/
  `_run_one_set`/`validate_segment` updated; module docstring rewritten for the new invariants.
- `tests/test_validate_segment_fix_round_1.py` (new) -- 14 tests covering K1, K2, C3, C4(a),
  C4(b)/I5, and the `checks`-shape regression test, with their own self-contained wf_0009-shaped
  fixture (adds an AMOUNT column, multiple golden sets, `mappings/global.yaml` and
  `manifest.accepted_diffs` support that the original fixture in `tests/test_validate_segment.py`
  doesn't need).

## Concerns

- `_missing_table_report`'s `"error"` message format
  (`f"output table {actual_fqn} does not exist after running the procedure"`) is my own wording --
  the ruling only requires that `error` "name the table," which it does (asserted via substring
  match on the logical name, e.g. `"ITEMS_OUT"`, in the tests). A re-reviewer who wants a specific
  message shape should say so.
- K2's ruling explicitly addresses only "the procedure itself errored" on the *first* run (existing
  behaviour: `idempotent` stays `None`). I additionally hardened the case where the *second* run's
  procedure errors while the first succeeded (not explicitly covered by the ruling's prose): treated
  as non-idempotent with every declared output table listed as diverging, consistent with the
  invariant "never true without both runs having been compared." This isn't independently tested
  (constructing a proc that succeeds once and fails the second time deterministically is awkward),
  so it's exercised only by code inspection, not a test. Flagging it in case a re-reviewer wants
  explicit coverage or disagrees with the chosen behaviour.
- `_aggregate_sets` sets `result["verdict"] = worst` explicitly even though `chosen["verdict"]`
  already equals `worst` by construction (redundant but harmless, kept for clarity/defensiveness).
