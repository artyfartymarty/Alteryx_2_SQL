# Task 14 report: `scripts/validate_segment.py` and end-to-end parity

## What was implemented

**`scripts/validate_segment.py`** — the deterministic segment-validation driver.

`validate_segment(repo, wf_id, seg, golden_sets=None, *, proc_path=None) -> dict`:

1. Checks prerequisites (`contract.json`, the procedure file — `proc_path` or
   `segments/<seg>/proc.sql`, `intake/mappings.yaml`, a non-empty set of golden sets — default
   `manifest.golden_sets`). Any missing prerequisite raises `FileNotFoundError`/`ValueError`
   *before anything else runs*, so a usage error can never leave a partial report on disk.
2. For each golden set, in a **fresh in-memory `DuckDBBackend`**: `load_golden.load_set`, then for
   every `contract.inputs[]` entry that has a `stream` (i.e. `from` an upstream segment rather than
   a mapped source), `load_golden.load_intermediate` of that stream into its literal `table`; then
   `run_proc` with `load_set(...)["args"]` plus a deterministic `RUN_ID`.
3. For every `contract.outputs[]` entry, loads its golden CSV into its own
   `MIG_COMPARE.EXPECTED_<n>` table and calls `compare.compare()` against the output's actual table
   (`work` → the contract's literal table; `target` → `MIGDB.MIG_WORK.<logical>`), passing
   `tolerances`/`accepted_classes` from `mappings/global.yaml` and this segment's
   `manifest.accepted_diffs` approvals, plus `segment_dag` when `dag.json` exists.
4. **Never re-derives or overrides anything `compare.py` decided.** The per-output reports are
   merged into one report per golden set: verdict = worst of the per-output verdicts (`FAIL` >
   `PASS_WITH_ACCEPTED_DIFF` > `PASS`), `needs_human` = true if any output's report says so, and
   every diff cluster is carried over with a `"stream"` key added.
5. **Idempotency**, per the design's ruling (determinism, not "run twice without resetting", since
   an Append output legitimately doubles under that test): for the *first* golden set only, the
   whole pipeline (steps 2–3's load+run, not the compare) runs a **second** time in its own fresh
   backend from the same starting state, and every `contract.outputs[]` table is read back fully
   ordered by all its columns from both backends and compared for equality — a row-multiset
   comparison, since sorting makes duplicate rows' relative order irrelevant. The result is a
   property of the procedure, so it is carried onto every golden set's report, not just the first.
6. A `ProcError`/`BackendError` while running the procedure (`run_proc`, i.e. a syntax/compile error
   or a runtime error) is caught and turned into that set's report:
   `{"verdict": "FAIL", "error": "<message>", "diff_clusters": [], ...}` — a domain failure (exit 1,
   report still written), not a usage error, because it is exactly what feeds the fixer loop.
7. Writes `segments/<seg>/validation.<set>.json` for every processed set, and
   `segments/<seg>/validation.json` = the first non-passing set's report, else the last set's,
   plus `"sets": {"<set>": "<verdict>", ...}`.

CLI: `python scripts/validate_segment.py <wf> <seg> [--set NAME]... [--proc FILE] [--root .]`,
following `scripts/parse.py`'s `main(argv=None) -> int` contract: 0 on PASS/PASS_WITH_ACCEPTED_DIFF,
1 on a FAIL verdict, 2 on a usage error (`parser.error`, nothing written) or any unexpected
exception (`traceback.print_exc()`, never a bare exit-1 traceback).

**`tests/helpers.py`** — `prepare_workflow(tmp_path, wf_id) -> Repo`: copies `mappings/`/`catalog/`,
runs `dev.build_samples.build`, then drives intake directly with `sample.json`'s own `answers` map
(matched by touchpoint key first, then tool id — wf_0002's DB output and wf_0003's two touchpoints
answer by tool id) via `intake_touchpoints.run` + `intake_prompt.apply_answers` (bypassing the
interactive/non-interactive prompt loop entirely, since the answers are already known), asserts
`READY`, then copies `samples/<wf>/canned/segments/<seg>/{contract.json,proc.sql}` into
`repo.seg(wf_id, seg, ...)` for every segment named in `segments/order.json`. **Skips the calling
test** (`pytest.skip`) when `samples/<wf_id>/canned/segments/` does not exist yet — Task 13 has not
run, so this keeps the whole suite green in the meantime rather than failing on missing fixtures.

**`tests/test_e2e_parity.py`** — the brief's Step 1 code, copied verbatim (I did not touch it).
Currently: the 4 hand-migration cases each skip (no `canned/` yet); `broken_cases()` naturally
yields zero parametrize entries (no `broken_sql/broken.json` anywhere yet), so
`test_broken_migration_fails_with_the_right_class` collects with an empty parameter set (pytest
reports this as one more `SKIPPED`, not a failure).

**`tests/test_validate_segment.py`** — a self-contained hand-built workflow, `workflows/wf_0009/`,
built entirely in `tmp_path` (never depends on `samples/`), matching the brief's spec: one source
(`ITEMS`, tool 1), a Filter segment writing both a work table (stream `2_T`,
`golden/intermediates/seg_01/normal/2_T.csv`) and a target `ITEMS_OUT` (tool 3, sharing the same
`stream` id per plan contract C5). Covers every scenario the brief lists:

- correct procedure → `PASS`, `idempotent is True`, exit 0, both `validation.json` and
  `validation.normal.json` written, `sets == {"normal": "PASS"}`.
- wrong filter → `FAIL` with every cluster carrying `"stream": "2_T"`.
- SQL syntax error → `verdict == "FAIL"`, `error` set, `diff_clusters == []`, exit 1, and the report
  is still written (a domain failure, not a usage error).
- `UNIFORM(1, 1000000, RANDOM())` in a column → `idempotent is False` (verified `sqlglot` actually
  translates `UNIFORM` to DuckDB-executable SQL, and that two fresh DuckDB connections really do
  produce different values, before relying on it in the test).
- unknown segment / a missing golden CSV / an empty golden-set list → `FileNotFoundError`/
  `ValueError`, nothing written (also exercised through the CLI: exit 2, no `Traceback` in stderr,
  no `validation.json`).
- a two-segment case (`seg_02`) whose only input is `seg_01`'s golden intermediate
  (`inputs[].from: "seg_01"`, `stream: "2_T"`, literal `table`) — `seg_01`'s own `proc.sql` is
  replaced with one that would raise if ever run, and `validate_segment(repo, wf, "seg_02")` still
  passes, proving the upstream procedure is never consulted.
- `--proc FILE` overrides the segment's own procedure (both as a direct function argument and via
  the CLI `--proc` flag).
- CLI exit codes 0/1/2 (pass, fail, unknown segment, missing golden file, `--proc`, `--set`
  overriding the manifest default, and an unexpected exception via `monkeypatch` returning 2, never
  a bare traceback at exit 1).

## Design decisions not fully pinned down by the brief

The brief's report-shape prose is precise about clusters (`"stream"` added, nothing re-derived) and
about `validation.json`'s own construction ("first non-passing set's report, else the last set's,
plus `sets`/`idempotent`/`runtime_ms`/`credits`"), but does not fully specify two things, since no
test in the brief constrains them:

1. **How multiple outputs' `checks` are combined into one per-golden-set report.** I keyed the
   merged `"checks"` dict by `"<stream>:<kind>"` (a work output and the target it feeds share the
   same `stream` id per contract C5, so `kind` disambiguates) — each value is exactly the `checks`
   dict `compare.compare()` produced for that output, untouched.
2. **Whether the "worst wins" verdict/`needs_human` combination rule is applied within one golden
   set's outputs only, or globally across every set.** I applied it only within one golden set (to
   combine that set's own outputs); the top-level `validation.json`'s `verdict`/`needs_human` are
   exactly whichever set's report was chosen ("first non-passing, else last"), not a separate
   global re-aggregation. I verified by hand that in every case the brief's own tests exercise, this
   is indistinguishable from a global aggregation (the e2e test's all-PASS case, and the broken-SQL
   test's single-golden-set case, can't tell the two designs apart) — so I picked the simpler, more
   literal reading of "`validation.json` = the first non-passing set's report, else the last set's,
   plus `sets`, `idempotent`, `runtime_ms`, `credits`." I flag this rather than silently deciding it,
   since a genuinely pathological ordering (an earlier set `PASS_WITH_ACCEPTED_DIFF`, a later one
   `FAIL`) would make the two readings disagree.

Neither is a "Brief correction" — the brief's own tests pass either way — just a documented choice
in case the design intended the global reading.

## Brief corrections

None. I did not change `tests/test_e2e_parity.py` from what the brief gave verbatim, including the
`@pytest.mark.parametrize("wf,case", broken_cases())` line, which triggers a `PytestRemovedIn10Warning`
("Passing a non-Collection iterable to parametrize is deprecated") under pytest 9.1.1 whenever
`broken_cases()` is empty (as it is now, before Task 13). I considered wrapping the call in `list(...)`
at the parametrize call site, but the brief's instructions were explicit that this file is "the test
code to use verbatim," and the warning is cosmetic (the suite still passes; it will very likely
render moot once Task 13 populates `broken_sql/broken.json` files and the generator yields real
cases). I left it as given and note it here instead of silently changing brief-mandated code.

## Testing

TDD evidence: per implementer-rules.md, `git stash` is off-limits for producing RED evidence, and
this is new code with no earlier version to diff against, so RED came from writing
`tests/test_validate_segment.py` and `tests/test_e2e_parity.py` against a repo with no
`scripts/validate_segment.py` yet: every test in both files failed on collection
(`ModuleNotFoundError: No module named 'validate_segment'`). I did not capture that raw output
separately since it is definitionally what "the module does not exist yet" produces; I went straight
to implementing.

GREEN:
```
.venv/Scripts/python.exe -m pytest tests/test_validate_segment.py
17 passed in 3.71s

.venv/Scripts/python.exe -m pytest tests/test_e2e_parity.py -m e2e
5 skipped (4 hand-migration cases skip: canned/ not written yet; the broken-SQL parametrize
collects with an empty case list)

.venv/Scripts/python.exe -m pytest
721 passed, 5 skipped, 1 warning in ~31s
```
(the lone warning is the pytest-deprecation note above, from the brief's own verbatim code). Output
is otherwise pristine — no other warnings, no stray prints.

I additionally smoke-tested `dev.build_samples.build` against the real `wf_0001` sample to see what
a genuine `segment.py`-produced `dag.json`/golden layout looks like (single segment, 8 tools
including two Output Data tools) before finalizing the two-output aggregation design, but did not
hand-write a canned migration for it — that would be Task 13's work, which the brief explicitly
says not to do here (samples had no `canned/`/`broken_sql/` before or after this task; I created
none).

## Files changed

- `scripts/validate_segment.py` (new)
- `tests/helpers.py` (new)
- `tests/test_e2e_parity.py` (new, verbatim from the brief)
- `tests/test_validate_segment.py` (new)

## Self-review findings

- No `tests/__init__.py` was needed: `from tests.helpers import prepare_workflow` resolves as a
  PEP 420 namespace package since `pyproject.toml` already puts `.` on `pythonpath`, confirmed by
  the full suite collecting and running `tests/test_e2e_parity.py` without an import error.
- `scripts/validate_segment.py` stays a single, ~360-line module with one clear responsibility
  (drive one segment's validation); it does not grow the plan's intent (`scripts/lib/*` unchanged,
  no new shared module introduced).
- No stray `git add -A`; only the four new files under `scripts/validate_segment.py` and `tests/`
  are staged.

## Concerns

- The two "not fully pinned down" design choices above (`checks` shape, per-set vs. global
  verdict/needs_human aggregation) are my best-effort reading of the brief's prose; a reviewer who
  reads it differently should say so before this lands in `main`, though I verified neither choice
  is distinguishable by any test the brief specifies.
- The real end-to-end path (`tests/test_e2e_parity.py`'s hand-migration cases) cannot be exercised
  until Task 13 writes `samples/<wf>/canned/segments/` and `samples/<wf>/broken_sql/`; my confidence
  in `validate_segment.py`'s correctness against *real* `segment.py`/`compare.py` output rests on
  the hand-built `wf_0009` fixture (which deliberately mirrors the two-output/upstream-intermediate
  shapes a real segment can have) plus reading `compare.py`, `load_golden.py` and `proc_runner.py`
  closely rather than a live run against a real hand migration.
