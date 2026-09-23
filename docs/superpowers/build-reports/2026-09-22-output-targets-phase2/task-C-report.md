# Task C report — sample `wf_0007`, regional targets as a dbt project

Worktree `.worktrees/p2-C`, branch `wt/p2-C`, base `ed03637`. One commit: `76668ef` feat: sample
wf_0007 — a two-segment workflow migrated as one dbt project (overwrite + merge), with goldens,
canned project and broken models. (Three `wip:` commits — `228c607` tests RED, `5646a76` sample,
`f33e056` Deployment sections — were squashed into it with `git reset --soft ed03637`; the
squashed tree is byte-identical to `f33e056`, the tree the full suite ran on: `git diff f33e056
76668ef` is empty.)

## What was built

- **`samples/wf_0007/source/regional_targets.yxmd`** — the brief's tool table verbatim, in the XML
  shape of wf_0002/wf_0003: Container 10 "Pair plan targets with actuals" (tools 1–3), Container 11
  "Current-year attainment" (tools 4–7); tool 3 Join on `REGION, PERIOD` with `Right_REGION`,
  `Right_PERIOD` deselected (L/J/R MetaInfo); tool 4 Filter `Left([PERIOD], 4) = [User.CurrentYear]`
  (True/False MetaInfo); tool 5 Summarize (`Count` on `REGION` → `LINES`, Int64 — what the simulator
  types a Count as; the two Sums stay FixedDecimal(19,2)); tool 6 yxdb Overwrite; tool 7 ODBC
  `PROD_PLAN` … `dbo.ATTAINMENT_HISTORY`, `Update; Insert if new`, `UpdateKeys` REGION, PERIOD, no
  PreSQL/PostSQL; constant `User.CurrentYear` = `2026`, `IsNumeric False`. E1 engine.
- **`samples/_tools/make_golden_inputs.py`** — the wf_0007 block (field lists = the input MetaInfo;
  `ATTAINMENT_HISTORY` typed exactly like tool 5's output) and the `SAMPLE_JSON` entry (the brief's
  JSON verbatim, with comments on `min_tools: 2` and on answering tool 7 by id). Re-running the
  generator left every other sample's files byte-identical (`git status` showed only wf_0007 and the
  generator).
- **Segmentation**, built with `build_samples.py build --only wf_0007` (rc 0): `seg_01 = [1, 2, 3]`,
  `seg_02 = [4, 5, 6, 7]`, one edge `3:J → 4`, so the work stream is `3_J` → `MIG_WORK.WF0007_SEG_01_OUT`.
  Goldens produced: `golden/outputs/<set>/6.csv`, `7.csv`, `golden/intermediates/seg_01/<set>/3_J.csv`
  for all four sets.
- **Canned artefacts**: `segments/seg_0{1,2}/contract.json` (`target: sql`, C5 keys; seg_02 has the
  two targets on stream `5_Output` with keys `["REGION", "PERIOD"]`; types from the golden
  `.schema.json` through `types_map.alteryx_to_snowflake`), `unsupported.json` (T1), `review.json`
  at the canned ROOT, `intake/plan.md`, `analysis.md` (quotes targets.json's reason verbatim — a test
  pins it), `docs/migration.md` (every required heading, `## Deployment` quoting `dbtReadme`'s exact
  command line from the Task D diff), and `canned/dbt/`: `dbt_project.yml`, `profiles.yml` (written
  from `dbt_project.PROFILES_TEMPLATE` by Python, never typed), `README.md`, `translation_notes.md`,
  `models/{sources,schema}.yml`, `models/wf0007_seg_01_out.sql`, `models/region_attainment.sql`,
  `models/attainment_history.sql`. Only translator-lane files are under `canned/dbt/` (a test pins it).
- **Broken models**: `broken_sql/dbt/models/region_attainment.sql` (no `t4_filter_t`),
  `broken_sql/dbt/models/attainment_history.sql` (`unique_key=['REGION']`), each = broken header +
  `-- The mistake: …` lines + the canned model with that one change (generated from the canned file
  by a script, so nothing else drifts); `broken_sql/broken.json` with the observed classes.
- **R-C1**: `## Deployment` before `## Assumptions` in wf_0001–0004's canned `docs/migration.md`:
  each `segments/<seg>/proc.sql` and `procs/master.sql` are the DDL (master generated from
  `segments/order.json`, calling the segments wave by wave — orders checked against the committed
  `workflows/<wf>/segments/order.json`), deployed by a human or CI under `MIGRATION_CI`, never from
  an agent session, never run on Snowflake from here (master.sql's own first line says so).
- **Tests**: `tests/helpers.py` (`prepare_workflow` copies only `contract.json` per segment plus
  `canned/dbt/` for a dbt sample; new `overlay_dbt_project`, verbatim from the brief);
  `tests/test_e2e_parity.py` (verbatim from the brief, plus a `needs_human is False` assertion in the
  dbt branch — the brief's Step 3 prose); `tests/test_canned_artifacts.py`; `tests/test_target_check.py`;
  `tests/test_samples_wellformed.py`; `tests/test_helpers.py` (see Brief corrections for the last two).

## How each validator PASS/FAIL was verified

All runs in a scratch root outside the repo, through `tests.helpers.prepare_workflow` (build →
intake with sample.json's answers → canned contracts + `canned/dbt/`), dbt only ever via
`validate_dbt.py`/`compile_check.py` (i.e. `lib.dbt_project.run_dbt`).

| Check | Result |
|---|---|
| `compile_check.compile_check_dbt(repo, "wf_0007")` on the canned project | `OK`, 3 models, `errors []` |
| `validate_dbt(repo, "wf_0007")` (all four sets) | seg_01 and seg_02 `PASS` on normal/period_end/empty/edge, `idempotent: true`, `needs_human: false`, no clusters; seg_02 carries two checks, `5_Output:target:6` and `5_Output:target:7` (R-B1) |
| dbt log (`dbt/logs/validate_normal.log`) | `Found 3 models, 5 data tests, 2 sources`; `PASS=3 WARN=0 ERROR=0`; no deprecation warning (the tests use `data_tests:`) |
| `target_check.py wf_0007 --prefer auto` (CLI) | rc 0, `output_kind: dbt`, `dbt_blockers: []`, segments both `sql`, reason `preference dbt; every segment is sql and every output is dbt-expressible` |
| `compile_check_dbt` with each broken model laid over the project | `region_attainment.sql`: `OK` (nothing static catches a dropped filter — tool 4 still has its comment in the other model); `attainment_history.sql`: `ERROR` — `dbt:model_config: models/attainment_history.sql has unique_key=['REGION']; the merge write mode needs unique_key=['PERIOD', 'REGION']` |
| G's offline intake path (`build_samples build` → `intake_touchpoints.py` → `answer_samples.py --only wf_0007` → `intake_prompt.py --no-interactive`) | 5 touchpoints (4 blocking + the constant), 4 answered, intake `READY` |

Golden data the simulator produced (normal): `3_J` = 5 rows (EAST 2026-01 twice — the fan-out;
WEST 2026-01 with NULL ACTUAL; WEST 2025-12); `6.csv` = EAST 2026-01 (200.00, 105.00, 2), EAST
2026-02, WEST 2026-01 (ACTUAL_TOTAL NULL); `7.csv` = those three plus the kept NORTH 2025-11 and
WEST 2025-12, EAST 2026-01 updated from its before-row. The `targets_before` table has one updated
row and two kept rows in `normal`, and the edge set's `BAD 2026-1` group is kept by the filter.

## Observed broken classes

Each variant was validated with `overlay_dbt_project` + `validate_dbt(repo, wf, ["normal"],
project_dir=…)` on a fresh workflow per run.

- **`dbt/models/region_attainment.sql`** (no year filter) — 3 of 3 runs identical: `FAIL`,
  `needs_human: false`, `idempotent: true`, one cluster `LOGIC`, `columns []`, stream `5_Output`,
  scope `rows`, "rows only in actual", count 1 (WEST 2025-12), suspect CTE `t4_filter`. seg_01
  PASS. No column is NULL in the extra row, so it is LOGIC, not NULL_SEMANTICS (spec §7.3 predicted
  LOGIC). Recorded `expect: {"class": "LOGIC", "columns": [], "stream": "5_Output"}`.
- **`dbt/models/attainment_history.sql`** (`unique_key=['REGION']`) — 3 recorded runs plus 5 more,
  all 8 identical in class: `FAIL`, `needs_human: false`, one cluster `LOGIC`, `columns []`, stream
  `5_Output`, scope `rows`, "rows only in expected", count 2. The data: dbt exits 0; the before-row
  WEST 2025-12 is overwritten with WEST 2026-01's values (PERIOD included); only one of EAST's two
  periods survives, written over the before-row EAST 2026-01; nothing is inserted. **Which EAST
  period survives is not deterministic on dbt-duckdb** (a MERGE with two source rows per target
  row): it varied run to run, so `idempotent` was `false` in 2 of the 8 runs — the class, columns
  and stream never changed. Recorded `expect: {"class": "LOGIC", "columns": [], "stream": "5_Output"}`
  (spec §7.3 predicted LOGIC, rows).

## TDD evidence

**RED** — tests and helpers written before any wf_0007 file existed (`228c607`):

```
$ .venv/Scripts/python.exe -m pytest tests/test_samples_wellformed.py tests/test_target_check.py tests/test_helpers.py "tests/test_e2e_parity.py::test_hand_migration_passes_every_golden_set[wf_0007]" -rfs
FAILED tests/test_samples_wellformed.py::test_samples_are_exactly_the_planned_workflows
FAILED tests/test_samples_wellformed.py::test_every_source_parses_and_is_an_alteryx_document[wf_0007]
FAILED tests/test_samples_wellformed.py::test_sample_json_has_the_planned_keys[wf_0007]      (FileNotFoundError: samples/wf_0007/sample.json)
FAILED … test_golden_inputs_match_their_input_tool_metainfo[wf_0007], test_node_count_matches_the_plan[wf_0007],
        test_output_tools_carry_the_schema_they_write[wf_0007], test_every_input_and_output_tool_has_a_logical_name[wf_0007],
        test_targets_before_is_present_for_append_and_merge_outputs[wf_0007], test_every_workflow_documents_its_golden_rows,
        test_the_dbt_sample_asks_for_the_dbt_output_kind
SKIPPED [3] tests\helpers.py:79: samples/wf_0007/canned not written yet (plan Task 13)
10 failed, 106 passed, 3 skipped

$ .venv/Scripts/python.exe -m pytest tests/test_canned_artifacts.py -rfs
E  AssertionError: no samples/wf_*/canned/dbt/dbt_project.yml exists under …/samples
E  AssertionError: wf_0001: docs/migration.md has no section for ['deployment']   (and wf_0002, wf_0003, wf_0004)
SKIPPED [3] got empty parameter set for (wf_id)        (the three new DBT_WORKFLOWS tests)
5 failed, 73 passed, 3 skipped
```

The three `prepare_workflow`-based wf_0007 tests (e2e, two target_check) were RED as the helper's
existing `pytest.skip` naming the missing `samples/wf_0007/canned`, not as a FileNotFoundError — the
helper skips by design until `canned/segments/` exists. `test_overlay_dbt_project_…` in
`tests/test_helpers.py` passed at once because the helper was written in the same step as the
brief-given code; `git show ed03637:tests/helpers.py` has no `overlay_dbt_project`, so against the
base it fails with `AttributeError` (not run separately — no stash allowed).

**GREEN**:

```
$ .venv/Scripts/python.exe -m pytest tests/test_canned_artifacts.py tests/test_samples_wellformed.py tests/test_target_check.py tests/test_helpers.py -rfs
206 passed in 5.38s
$ .venv/Scripts/python.exe -m pytest tests/test_e2e_parity.py tests/test_build_samples.py tests/test_survey_corpus.py tests/test_committed_workflows.py -rfs --durations=8
18.19s call  tests/test_e2e_parity.py::test_hand_migration_passes_every_golden_set[wf_0007]
 7.96s call  tests/test_e2e_parity.py::test_broken_migration_fails_with_the_right_class[wf_0007-dbt/models/region_attainment.sql]
 7.55s call  tests/test_e2e_parity.py::test_broken_migration_fails_with_the_right_class[wf_0007-dbt/models/attainment_history.sql]
140 passed in 54.52s
$ .venv/Scripts/python.exe -m pytest -rfs
1425 passed in 318.56s (0:05:18)        (baseline 1396; +29; 0 skipped, 0 failed)
```

`tests/test_survey_corpus.py::test_the_committed_samples_survey_as_expected` now exercises its
`if "wf_0007" in SAMPLE_IDS` branch (`--prefer dbt` → `output_kind: dbt`) and passes.

## Brief corrections

1. **`test_every_dbt_sample_passes_compile_check_dbt` uses `prepare_workflow`, not the module's
   `build_workflow`.** The brief says "`build_workflow(wf)`, copy canned contracts and `canned/dbt/`
   in". `build_samples.build` never writes `intake/mappings.yaml` (only a resolved intake does), and
   `compile_check_dbt` reads it unconditionally. Proof, the brief's literal recipe in a scratch root:
   `mappings.yaml exists: False`, then `compile_check_dbt` raises `FileNotFoundError: …\workflows\
   wf_0007\intake\mappings.yaml` (from `read_yaml`). `prepare_workflow` does exactly the brief's
   copying plus intake, and does not mutate the module-shared build either.
2. **`tests/test_samples_wellformed.py` had to change** although the brief's file list omits it
   (its Step 5 and the dispatch both require it green). `test_samples_are_exactly_the_planned_workflows`
   compares the sample directories with `EXPECTED`, and `test_sample_json_has_the_planned_keys`
   asserted `set(meta) == SAMPLE_JSON_KEYS`, which the brief's own `sample.json` (with
   `"output_target"`) cannot satisfy. Change: `EXPECTED["wf_0007"]` (9 nodes, inputs 1/2, outputs
   6/7, targets_before `ATTAINMENT_HISTORY`), `output_target` accepted as an optional key with value
   in `{procedures, dbt}`, and one new test that only wf_0007 sets it.
3. **`unsupported.json`**: the brief writes `{"tier": "T1", "tools": []}` "(match the existing key
   shape)"; the existing shape (and `test_unsupported_json_declares_a_valid_tier`) is
   `{"tier", "unsupported", "unknown"}`, so that is what was written.
4. **Casts are `DECIMAL(19,2)`, not `NUMBER(19,2)`.** dbt-duckdb runs model text as written (no
   transpiling), and DuckDB 1.5.5 refuses `NUMBER`: `Catalog Error: Type with name NUMBER does not
   exist!`; `DECIMAL(19,2)` is valid in both engines. Recorded in `translation_notes.md`.
5. **Tool comments are `-- tool <id>: …` with the colon right after the id** (the anchor note after
   it), following Task E's fix-round ruling C1 on the same rule, rather than `-- tool 3 (anchor J):`.
6. **Contract column types**: through `types_map.alteryx_to_snowflake`, as the brief says, which
   gives unsized `VARCHAR` for `V_String` (a deliberate types_map ruling) — unlike the older
   hand-written contracts' `VARCHAR(n)`. `compare.py` compares type families, so it makes no
   difference to any verdict.
7. **Model keywords are upper case** (`t1_input AS (`): `test_canned_artifacts._CTE_RE` is
   case-sensitive, so with lower-case `as` the brief's "CTE names are a subset" check would have been
   vacuous. The new dbt broken-variant test also asserts the canned model has CTEs at all.

Not corrections, but additions beyond the brief's tests (behaviour it describes in prose):
`test_at_least_one_workflow_has_canned_artifacts` guards `DBT_WORKFLOWS` non-empty;
`test_the_dbt_artifact_set_is_complete` also pins the translator-lane-only content of
`canned/dbt/`, no per-segment `review.json`, and ≥1 dbt broken row (the dbt twin of
`…do_not_skip…`); `test_work_outputs_use_the_contract_c3_table_names` (it read `proc.sql`, not in
the brief's switch list) gained a dbt branch checking the work model file instead;
`test_no_procedure_names_a_real_catalog_table` also scans the dbt models and broken models; the dbt
broken-variant test requires `The mistake:` in the header like the procedure one;
`test_the_wf_0007_canned_analysis_quotes_the_real_targets_json_reason` (CLI `--prefer auto` default,
and analysis.md quotes the reason target_check really writes); `test_overlay_dbt_project_…` in
`tests/test_helpers.py`; `needs_human is False` in the e2e dbt branch.

## Files changed

`samples/_tools/make_golden_inputs.py`; `samples/wf_0007/**` (46 files: source, sample.json,
README, golden inputs incl. targets_before, canned/**, broken_sql/**);
`samples/wf_000{1,2,3,4}/canned/docs/migration.md`; `tests/helpers.py`, `tests/test_e2e_parity.py`,
`tests/test_canned_artifacts.py`, `tests/test_target_check.py`, `tests/test_samples_wellformed.py`,
`tests/test_helpers.py`.

## Self-review

- Hand-off hygiene: `git diff ed03637 | grep -i "<user>|C:\\Users|C:/Users|/c/Users|scratchpad|AppData"`
  is empty; `tests/test_committed_workflows.py` (which scans every tracked file) passes.
- Every broken.json claim was observed (compile_check behaviour of both variants included), and the
  broken-model header text was checked against the observed data.
- No machine-dependent content: `profiles.yml` is the template; nothing under `canned/dbt/` is
  outside the translator's lane.

## Concerns

- The narrowed-key variant is **non-deterministic on dbt-duckdb** (which EAST period survives), so
  its `idempotent` flag flips between runs. The e2e test only asserts `FAIL` + class/columns, which
  were stable in 8/8 runs; if a future check asserts on example rows or idempotency of a broken
  variant, this one will flake.
- `compile_check_dbt` raises a raw `FileNotFoundError` from `read_yaml` when `intake/mappings.yaml`
  is missing (its docstring lists only the project, order.json and contracts). The CLI turns it into
  exit 2, so it is a documentation gap in Task A's code, not a behaviour bug; left untouched.

## Things G must know

- `EXPECTED_TERMINAL` needs `"wf_0007": "VALIDATED"`; the OUTPUT_KINDS comment "no committed sample
  uses it yet" goes stale. The offline intake path works for wf_0007 (`answer_samples.py --only
  wf_0007` answers 4, `intake_prompt.py --no-interactive` → READY; tool 7 is answered by tool id,
  its touchpoint key is `alias:prod_plan/dbo.attainment_history`).
- A wf_0007 run leaves `workflows/wf_0007/dbt/**` (canned replay + `compile_check.json`,
  `review.json`), git-ignored `dbt/logs/` and `dbt_sandbox_<set>.duckdb`, both segments'
  `validation*.json` with `"target": "dbt"` (seg_02 has checks `5_Output:target:6` and `:7`), and
  `procs/README.md` — no `master.sql`. The canned `docs/migration.md` quotes `procs/README.md`'s
  command exactly as Task D's `dbtReadme` writes it.
- **wf_0001–0004's committed `workflows/<wf>/docs/migration.md` will change** on the re-run: the
  documenter replays the canned docs, which now carry `## Deployment` (R-C1).
- On a `fix-loop:dbt`/`never-fixed:dbt` scenario the MockRunner serves `broken_sql/dbt/models/
  attainment_history.sql` first (name order), and that variant is caught by
  `compile_check.py --target dbt` (`dbt:model_config`), **before** validation — so such a run parks
  or loops on a compile failure, not a validation FAIL. The `region_attainment.sql` variant is the
  one only validation catches.
- wf_0007's contracts use unsized `VARCHAR` (types_map); a committed `contract.json` diff against a
  hand-sized expectation is expected, not a regression.
- The full wf_0007 validation takes ~18 s (5 dbt runs: four sets + the idempotency rerun).
