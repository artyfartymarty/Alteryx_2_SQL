# Task B report — `scripts/validate_dbt.py`

Worktree: `.worktrees/p2-B`, branch `wt/p2-B`, base `cfd4896` (Task A merged). Commit: `a785a96`.

## What was built

- **`scripts/validate_dbt.py`** (new). `validate_dbt(repo, wf_id, golden_sets=None, *,
  project_dir=None) -> dict[str, dict]`: for every golden set (default `manifest.golden_sets`),
  builds a fresh on-disk sandbox (`dbt_sandbox_<set>.duckdb`) loaded exactly as
  `load_golden.load_set` loads one for the SQL validators, runs the whole dbt project once through
  `lib.dbt_project.run_dbt` (the only place this repo ever invokes dbt), and — if the run succeeds
  — compares every segment's `contract.outputs[]` table against its golden CSV with the unchanged
  `compare.py`, through `lib.validation`'s shared report-shaping (`combine`, `missing_table_report`,
  `expected_fqn`, `golden_path`). A `dbt run` failure (`result.code != 0`) is turned into a domain
  FAIL for every segment via `DbtRunFailed`/`fail_report`, naming the failed model(s) from
  `run_results.json` — never a Python traceback. For the *first* golden set only (DV7), a second,
  `_rerun`-suffixed sandbox is built from scratch, the project runs again, and every segment's
  declared output tables are compared as row multisets (`lib.validation.ordered_rows`); a
  divergence forces that segment's verdict to `FAIL` and records the diverging table names in
  `idempotency_diff`. The `_rerun` sandbox (and its `.wal`) is deleted once compared; the per-set
  sandboxes are kept for inspection (both patterns are already git-ignored from Task A). Every
  segment's stale `validation*.json` is cleared before any prerequisite check runs
  (`_segments`/`_prerequisites`, mirroring `validate_segment.validate_segment`'s own checks), so a
  usage error or crash never leaves a stale or partial report. CLI: `validate_dbt.py <wf> [--set
  NAME]... [--project DIR] [--root .]`, exit 0 when every segment's verdict starts with `PASS`, 1 on
  any `FAIL`, 2 on a usage error (`FileNotFoundError`/`ValueError`/`DbtUnavailable`, via
  `parser.error`) or any other crash (`traceback.print_exc(); return 2`).

- **`scripts/lib/validation.py`** (modified). New public `ordered_rows(backend, table) ->
  list[tuple]`, moved verbatim out of `validate_segment.py`'s old private `_read_ordered_rows` — the
  idempotency snapshot helper `validate_dbt.py`'s `_snapshot` also needs. `combine()` now
  disambiguates a colliding `checks` key (ruling R-B1): keys are built first, and every key that
  occurs more than once gets `:<tool_id>` appended to **every** occurrence, while a key that never
  collides is left exactly as it was — so no already-committed `validation.json` changes shape.
  This is the fix `wf_0009`'s `seg_02` (one stream, `2_T`, feeding two `target` outputs
  `ITEMS_OUT`/`ITEMS_HIST`) needs, and what `wf_0007` (Task C) will need too.

- **`scripts/validate_segment.py`** (modified, re-export only). Imports `ordered_rows` from
  `lib.validation` and keeps `_read_ordered_rows = ordered_rows` so its own tests (which reference
  the old private name) keep working unchanged; the old function body was deleted.

- **`tests/test_validation_combine.py`** (new) — the two tests given verbatim in the brief:
  `test_two_targets_on_one_stream_keep_both_checks`, `test_ordered_rows_sorts_by_every_column`.

- **`tests/test_validate_dbt.py`** (new) — nine tests covering every scenario the brief names:
  a correct project (every set/segment PASS, idempotent, files/logs/sandboxes exactly as
  specified); wrong logic (FAIL with clusters on the right stream, chained into the next segment
  because the whole project runs as one unit); a model that fails to run (`DbtRunFailed`, both via
  the API and the CLI, no traceback); a non-deterministic model (only the affected segment loses
  idempotency); a missing output table (FAIL naming it); usage errors (stale report cleared and
  never replaced, for three separate causes, both via the API and the CLI); a dropped golden set
  (its stale per-set report disappears for every segment); `--project` validating a different
  directory while the workflow's own `dbt/` stays untouched; `DbtUnavailable` before anything runs.

## Brief corrections

- **`main`'s `parser.error(...)` path raises `SystemExit`, not a returned `2`.** The brief's Step 3
  literally instructs `main` to "mirror `validate_segment.main`", whose `FileNotFoundError`/
  `ValueError` branch is `parser.error(str(exc))` — and `argparse.ArgumentParser.error()` calls
  `self.exit(2, …)`, which raises `SystemExit(2)`. That is only observable as a subprocess exit
  code when `main()` is called directly in-process (as `test_dbt_unavailable_is_a_usage_error` and
  the CLI portion of `test_usage_errors_leave_no_report` do) — `test_validate_segment.py` itself
  only ever exercises this branch through a subprocess (`run_cli`), never a direct `vs.main(...)`
  call, for exactly this reason. I kept `parser.error(...)` (faithful to "mirrors
  `validate_segment.main`" and to DV/ruling text elsewhere that assumes standard argparse usage-error
  behaviour) and fixed the *tests* instead: the three CLI usage-error scenarios go through a
  `run_cli` subprocess helper (matching `test_validate_segment.py`'s own convention), and the
  `DbtUnavailable` CLI test (which needs `monkeypatch`, so it must stay in-process) asserts
  `pytest.raises(SystemExit)` with `.value.code == 2` instead of `rc == 2`. No production code
  changed by this correction — it only affects how the tests observe the same exit-code-2 behaviour.
  Confirmed by hand: `argparse.ArgumentParser.error` → `self.exit(status=2, message=…)` →
  `sys.exit(status)`, standard library, Python 3.14.

- **`golden_sets=[]` has no CLI spelling.** The brief's `test_usage_errors_leave_no_report` lists
  three raising scenarios including "`golden_sets=[]` (`ValueError`)" and separately says "the CLI
  returns 2 for each" of the three. `--set` only *appends* to a list (`action="append"`), so there
  is no flag combination that makes `args.sets` an explicit empty list — omitting `--set` entirely
  yields `None`, which falls back to `manifest.golden_sets`. I reproduced the CLI equivalent by
  writing `manifest.json` with `"golden_sets": []` in a second, independent repo and calling the CLI
  with no `--set` at all, which reaches the identical `sets = []` state inside `_prerequisites`
  (`sets = list(golden_sets) if golden_sets is not None else list(manifest.get("golden_sets") or
  [])`) and raises the same `ValueError`.

## Design decisions not spelled out in the brief

- **`_prerequisites`' check order.** The brief names the checks ("`dbt/dbt_project.yml` …, every
  segment's `contract.json` …, `intake/mappings.yaml`, non-empty sets …, `settings`") without
  stating an order beyond the sequence they're listed in; I implemented them in exactly that order
  (`dbt_project.yml` → per-segment contracts → mappings.yaml → non-empty sets → settings), mirroring
  how `validate_segment.validate_segment` checks its own five prerequisites top to bottom.
- **`settings` shape.** The brief's `_compare_set` snippet reads `settings["tolerances"]` and
  `settings["accepted"]` (not `"accepted_diff_classes"`, the raw `global.yaml` key). `_prerequisites`
  builds that renamed dict explicitly: `{"tolerances": raw.get("tolerances") or
  compare.DEFAULT_TOLERANCES, "accepted": raw.get("accepted_diff_classes") or ()}`, the same values
  `validate_segment.py`/`validate_snowpark.py` compute, just gathered under the two keys the given
  `_compare_set` code already expects.
- **Test golden-set restriction.** Per implementer-rules.md's cost note and the dispatch's own
  reminder ("keep the test count of dbt runs reasonable"), every test that doesn't specifically need
  the second golden set calls `validate_dbt(repo, WF, ["normal"])` rather than the two-set default.
  Only `test_a_correct_project_passes_every_set_and_every_segment` and
  `test_a_dropped_golden_set_loses_its_stale_report` need both sets, since they assert on
  `"second"`'s own files. Total dbt invocations across the new suite: ~18 (~55 s of the ~66 s
  `test_validate_dbt.py` takes alone) — no test uses a shared/module-scoped sandbox, since every
  scenario needs its own broken variant of the fixture project and DV7's fresh-sandbox contract
  means a shared on-disk sandbox across tests would be actively wrong, not just slower.

## TDD evidence

**RED** — before any implementation existed:

```
$ .venv/Scripts/python.exe -m pytest tests/test_validation_combine.py tests/test_validate_dbt.py -v
ERROR collecting tests/test_validate_dbt.py
E   ModuleNotFoundError: No module named 'validate_dbt'

$ .venv/Scripts/python.exe -m pytest tests/test_validation_combine.py -v
FAILED tests/test_validation_combine.py::test_two_targets_on_one_stream_keep_both_checks
  AssertionError: assert {'3_J:work', '5_Output:target'} == {'3_J:work', '5_Output:target:6', '5_Output:target:7'}
FAILED tests/test_validation_combine.py::test_ordered_rows_sorts_by_every_column
  AttributeError: module 'lib.validation' has no attribute 'ordered_rows'
2 failed in 0.17s
```

Exactly the three failures the brief predicts (`ModuleNotFoundError`, the combine test failing with
2 keys instead of 3, `AttributeError` for `ordered_rows`).

**GREEN** — after `lib/validation.py`, `validate_segment.py` and `validate_dbt.py`:

```
$ .venv/Scripts/python.exe -m pytest tests/test_validation_combine.py tests/test_validate_segment.py \
    tests/test_validate_segment_fix_round_1.py tests/test_validate_snowpark.py -v
60 passed in 25.73s

$ .venv/Scripts/python.exe -m pytest tests/test_validate_dbt.py -v
9 passed in 62.88s

$ .venv/Scripts/python.exe -m pytest tests/test_validate_dbt.py tests/test_validation_combine.py \
    tests/test_validate_segment.py tests/test_validate_segment_fix_round_1.py \
    tests/test_validate_snowpark.py tests/test_e2e_parity.py tests/test_dbt_project.py \
    tests/test_compile_check_dbt.py -v
123 passed in 207.43s

$ .venv/Scripts/python.exe -m pytest
1377 passed in 256.00s   (baseline 1366, +11, 0 skipped)
```

Baseline was re-measured at the start of this task from the dispatch base (`cfd4896`): `1366 passed
in 182.96s`, matching the dispatch's stated baseline exactly.

## Files changed

- `scripts/validate_dbt.py` (new, 273 lines)
- `scripts/lib/validation.py` (modified: +`ordered_rows`, `combine`'s duplicate-key fix)
- `scripts/validate_segment.py` (modified: re-export only, old `_read_ordered_rows` body removed)
- `tests/test_validate_dbt.py` (new, 9 tests)
- `tests/test_validation_combine.py` (new, 2 tests)

Commit `a785a96`: `feat: validate_dbt.py runs the whole dbt project per golden set on DuckDB and
judges every contract output with compare.py`.

## Self-review findings

- Hand-off hygiene: grepped the diff and new files for `<user>`/`C:\Users`/`/c/Users` — none found.
- `.gitignore` already covers `*.duckdb`, `*.duckdb.wal` and `workflows/*/dbt/logs/` from Task A;
  verified by name, not re-added.
- Confirmed no stray absolute paths leak into any `error` string a test asserts on
  (`test_a_model_that_fails_to_run_is_a_domain_fail_naming_it` explicitly asserts `str(tmp_path) not
  in report["error"]`), relying on `dbt_project.run_dbt`'s own redaction (Task A).
- Working tree is clean after the commit (`git status --short` empty); nothing scratch was left
  under the repo.

## Concerns

None. The implementation follows the brief's given code near-verbatim (the `_run_project`,
`_run_error`, `_snapshot`, `_compare_set`, and `validate_dbt` bodies are the brief's own snippets,
adjusted only for the two corrections above and for filling in `_segments`/`_prerequisites`, which
the brief described in prose rather than code). All nine new `test_validate_dbt.py` scenarios and
both `test_validation_combine.py` tests pass; the full suite is green at 1377/0 skipped with no
regressions in `validate_segment.py`/`validate_snowpark.py`/`compile_check.py`'s own dbt tests.

## Interfaces for C/D/W1

**`scripts/validate_dbt.py`** (import as `import validate_dbt` or `from validate_dbt import …`):

```python
class DbtRunFailed(Exception): ...   # constructed, never raised -- stringified into fail_report's error

def validate_dbt(repo: Repo, wf_id: str, golden_sets: Sequence[str] | None = None, *,
                 project_dir: Path | None = None) -> dict[str, dict]
    # {segment: top-level report}; each report is validate_segment's own shape plus "target": "dbt"
    # at the top level only (never in validation.<set>.json). Raises FileNotFoundError/ValueError
    # for a missing prerequisite, lib.dbt_project.DbtUnavailable if no dbt console script is beside
    # this interpreter. Writes segments/<seg>/validation.json + validation.<set>.json per segment,
    # dbt/logs/validate_<set>.log (+ validate_<first set>_rerun.log), and keeps
    # workflows/<wf>/dbt_sandbox_<set>.duckdb per set (the _rerun one is deleted).

def main(argv: Sequence[str] | None = None) -> int
    # CLI: validate_dbt.py <wf> [--set NAME]... [--project DIR] [--root .]
    # exit 0 every segment PASS*, 1 any FAIL, 2 usage (parser.error -> SystemExit(2) when
    # FileNotFoundError/ValueError/DbtUnavailable; traceback.print_exc()+return 2 otherwise)
```

**`scripts/lib/validation.py`** additions (shared with `validate_segment.py`/`validate_snowpark.py`):

```python
def ordered_rows(backend, table: str) -> list[tuple]
    # every row of `table`, ORDER BY every column (positional 1..n) -- the row-multiset comparison
    # primitive validate_dbt.py's idempotency snapshots (_snapshot) use directly.
```

`combine()`'s signature and return shape are unchanged; only the `checks` dict's *keys* can now
carry a `:<tool_id>` suffix, and only for a `stream:kind` pair that occurs more than once among the
outputs passed to one `combine()` call (ruling R-B1). No caller needs to change to keep working —
`validate_segment.py`/`validate_snowpark.py`'s existing single-output-per-stream:kind segments are
never affected (their tests, unchanged, prove it: 60/60 still pass).

**What C (sample `wf_0007`) needs to know:** a dbt model that feeds two targets from one stream
(`wf_0009`'s own `seg_02` already exercises this) will report both targets' `checks` under
`<stream>:target:<tool_id>` keys, not a silently-overwritten single `<stream>:target` key — write
`docs/migration.md`/any report-reading code accordingly if it names a specific check key.

**What D (orchestrator) needs to know:** dispatching a dbt workflow's translate/validate stage is
still exactly `compile_check.py <wf> --target dbt` then `validate_dbt.py <wf>` (both take the whole
workflow, no segment argument) — same two-script shape as the SQL/Snowpark targets' per-segment
loop, just called once for the whole workflow instead of once per segment. `validate_dbt`'s returned
dict is keyed by segment exactly like `validate_segment`'s/`validate_snowpark`'s per-segment report,
so a per-segment status table in the orchestrator's stage gate needs no special-casing beyond "read
`report["target"]` if you need to know which validator produced it."

**What W1 (chain validation) needs to know:** `validate_dbt.py` never loads a golden intermediate
for a downstream segment — the whole project runs together, so a downstream model's `ref()` always
sees its *upstream's own dbt-computed table* from the same run, not golden data reloaded in
isolation. This is already what W1's "the actual output of upstream segments" chain semantics want
for a dbt-target workflow; no golden-intermediate-loading code path needs adding for the dbt case
the way it might for extending the SQL/Snowpark path. `_segments(repo, wf_id)` (reads
`segments/order.json`, flattens wave order) and `dbt_project.model_relation(wf_id, seg, output)`
(the real table an output lands in, `database` defaults to `lib.backend.SANDBOX_DB`) are both
reusable building blocks if W1 needs to name a dbt segment's actual table directly.
