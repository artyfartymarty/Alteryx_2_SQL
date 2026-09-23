# Task W1 report — workflow-level chain validation (`scripts/validate_workflow.py`)

Worktree `.worktrees/p2-W1`, branch `wt/p2-W1`, base `8299324` (F merged). Commits: see "Commits" at the end.

## What was built

**`scripts/validate_workflow.py`** (new). `validate_workflow(repo, wf_id, golden_sets=None, *, backend="duckdb") -> dict`;
CLI `validate_workflow.py <wf> [--set NAME]... [--backend duckdb] [--root .]`, exit 0 chain PASS*, 1 FAIL, 2 usage
(`parser.error`) or crash (`traceback.print_exc(); return 2`). Per golden set: ONE fresh `DuckDBBackend` loaded by
`load_golden.load_set` (raw golden inputs + `targets_before` only — no golden intermediate is ever loaded), every
segment run in `order.json` wave order on its upstream segments' actual tables, and every contract output judged right
after its segment ran with the unchanged `compare.compare` (the same arguments `validate_segment._run_one_set` passes:
that segment's contract/output, tolerances, accepted classes, that segment's approvals, its `dag.json`), the golden file
loaded as `MIG_COMPARE.CHAIN_EXPECTED_<n>` in the chain's own backend; a missing actual table is
`missing_table_report`. SQL segments: `run_proc` in the backend (`ProcError`/`BackendError` → `ChainError`). Snowpark
segments (`_run_snowpark_segment`): a fresh local session, `load_set_snowpark` (raw inputs + `targets_before`; a missing
golden file stays a usage error), then — inside the `ChainError` scope — every declared upstream stream and every
target's current chain state handed in, `run()`, every declared output handed back with the schema Snowpark really wrote.
For the first set a second independent chain from a fresh backend; every judged relation compared as a row multiset
(`v.ordered_rows`); a relation missing on either side, or a second run that raises, diverges. A dbt workflow
(`manifest.output_kind == "dbt"`) delegates to `validate_dbt.validate_dbt` and returns the `validation_workflow.json`
that run wrote. Stale `validation_workflow*.json` are deleted before anything else. Writes
`workflows/<wf>/validation_workflow.json` + `validation_workflow.<set>.json`.

**`scripts/lib/handoff.py`** (new). `HandoffError(ValueError)`; `ReadBackError(HandoffError)` (the old
`validate_snowpark.ReadBackError`, same constructor); `table_from_backend(backend, fqn)` (fields from
`backend.table_columns` via `types_map.duckdb_to_alteryx`, an unmapped type → `HandoffError("<fqn>.<column>: …")`, a
missing table → `HandoffError`; rows from `validation.ordered_rows`, `Decimal` kept, date/timestamp/time → ISO text);
`load_into_backend` (= `backend.load_table`); `table_from_snowpark` / `load_into_snowpark` = `validate_snowpark._read_back`
/ `_save` moved verbatim with their helpers `_coerce` / `_to_snowpark`. No helper takes a contract.

**`scripts/lib/types_map.py`**: `duckdb_to_alteryx(type_str) -> {"type","size","scale"}` exactly as the brief lists
(integers incl. HUGEINT/unsigned → Int64; `DECIMAL(p,s)` → FixedDecimal(p,s); DOUBLE/FLOAT/REAL → Double; `VARCHAR`
and `VARCHAR(n)` → V_String; BOOLEAN → Bool; DATE → Date; `TIMESTAMP*` → DateTime; TIME → Time; else `ValueError`).

**`scripts/lib/validation.py`**: `ChainError(segment, cause)` (message `chain stopped at <seg>: <Type>: <cause>`);
`chain_report(wf_id, golden_set, entries, error=None, *, diverging=())`; `write_workflow_reports(repo, wf_id, sets,
reports, idempotent, *, extra=None)`; `clear_stale_workflow_reports(repo, wf_id)`. `write_reports`' tail is factored into
`_write_set_reports(directory, stem, …, aggregate=)`, which both use (segment reports unchanged byte for byte).

**`scripts/validate_snowpark.py`**: `_read_back = handoff.table_from_snowpark`, `_save = handoff.load_into_snowpark`,
`ReadBackError = handoff.ReadBackError`, public aliases `new_local_session = _session`, `load_module = _load_module`;
docstring points at the new home. Behaviour unchanged (`test_validate_snowpark.py` and `test_cookbook_snowpark.py`, which
reach `_session`/`_save`/`_read_back`, pass unchanged).

**`scripts/validate_dbt.py`**: clears stale workflow reports first; `_compare_set` also returns chain entries (the same
per-output compare reports, chain order); a failed `dbt run` becomes `ChainError(<segment owning the first failed model,
else skipped, else the first segment>, DbtRunFailed(…))`; the workflow idempotency is derived from the segments' own
(`None` if any is `None`, else all-true, diverging relations unioned in chain order); writes
`validation_workflow*.json` with `"target": "dbt"` in the same call. Per-segment reports and the return value unchanged.

**`orchestrator/stages.ts`**: `migrateSegment(env, m, segment, opts: SegmentOptions = {})` with `firstIteration`,
`iterations`, `note` (appended to every fixer task after the standing text); defaults byte for byte as before (the
exhausted-loop reasons count `iterations - firstIteration`, which is `maxFixIterations` by default). `ChainReport`
interface and `chainCheck` as the brief's code, called in `stageTranslate` after the wave loop and before `master.sql`.
`translateDbt`: after every segment PASSes (fresh run or the all-PASS resume) reads `validation_workflow.json`; absent or
not `PASS*` → park `dbt: chain FAIL`.

**Docs**: `docs/reference/large-workflows.md` gains "## The chain test" (why per-segment validation cannot see
composition; what runs; report fields; R-W1 with both fixture examples; non-determinism; the typed hand-off; dbt; the
orchestrator's routes and reasons as a table; what it does not prove); its intro no longer says "segment sizing only".
`docs/reference/output-targets.md` §4: one table row (the chain test per target) and one reason row (`dbt: chain FAIL`)
— outside the brief's file list, added so the reference table stays true (see Concerns).

**Agents: no change.** The orchestrator runs `validate_workflow.py` itself (`env.py`, like `target_check.py`); no role
runs it, so `ROLE_SCRIPTS`/`WORKFLOW_ID_SCRIPTS` and the agent files are untouched. The one agent-facing text added — the
fixer note — names `scripts/validate_workflow.py` without arguments and `workflows/<wf>/validation_workflow.json`; no
flag, no `--root` (Task D's policy).

## Tests (all written first; RED below)

- `tests/chain_fixtures.py`: `build_composition(tmp_path, *, rounding=True, seg_02_expression=…)` (wf_0008, 3 waves) and
  `build_drift(tmp_path)` (wf_0010) exactly as the brief's data (1.005/2.115/0.125; seg_01 `ROUND(PRICE, 2)`; the
  accepted `ROUNDING` approval; seg_02 `PRICE * 1000` / `SUM(PRICE)`; seg_03 copies to `MILLI_OUT`, tool 9), plus
  `make_seg_03_read_what_seg_02_never_writes(repo)` (see Brief corrections). Writes its own `mappings/global.yaml`
  (the real tolerances and `accepted_diff_classes: [ROUNDING, ORDERING]`). Premise asserted in the tests: seg_01 alone is
  `PASS_WITH_ACCEPTED_DIFF` (a `ROUNDING` cluster on PRICE, count 3), every other segment alone `PASS`.
- `tests/test_handoff.py` (41): the brief's four, plus: the backend side's exact fields/values; a missing Snowpark table
  reads back `None`; a missing DuckDB table is a `HandoffError`; the error hierarchy and the `validate_snowpark` aliases;
  `duckdb_to_alteryx` over every listed type (23 cases) and 9 refused ones; a TIME value crosses as ISO text.
- `tests/test_validate_workflow.py` (16): the brief's seven by name, plus: CLI on a never-prepared workflow → 2, nothing
  created; CLI exit 0/1 follow the verdict (with `--set`/`--backend`); `main` → 2 on an unexpected exception; a golden set
  no longer requested loses its stale per-set report; dbt delegation on the fast `wf_0009` fixture (PASS, boundaries
  `seg_01/2_T`, both finals) and a model that fails to run (`boundary`, `stream: null`, `seg_02`, `items_out` in error);
  `chain_report` ordering (failing boundary before a later raise and before an earlier failing final; raise; drift;
  clean); non-determinism alone localised to the first diverging relation; a boundary in any set outranks an earlier set's
  drift in `validation_workflow.json`.
- `tests/test_e2e_chain.py` (6, `e2e`): the brief's two, by name.
- Node (`stages.test.ts`, +9): the brief's six by name, plus "a resumed translate whose segments all PASSed still runs the
  chain before VALIDATED", "a resumed dbt workflow with no chain report on disk parks with dbt: chain FAIL", "the chain
  check's fixer note is appended to the fixer's standing task, never replacing it". Fakes: `validate_workflow.py`
  (PASS default; `chain-boundary:<seg>` FAIL once then PASS; `chain-boundary-stuck:<seg>`; `chain-drift`; `chain-crash`
  exit 2) and `validate_dbt.py` now also writes `validation_workflow.json` (FAIL when a segment fails or on
  `chain-fail:dbt`).
- **Pinned tests updated deliberately:** "a SQL workflow's script calls are exactly what they were…" now expects one
  `["scripts/validate_workflow.py", ["wf_0001"]]` after `validate_segment.py` (title extended to say so; every other
  entry unchanged). "a dbt workflow whose segments all PASSed is not re-translated on resume": its setup now also writes
  the PASS `validation_workflow.json` the crash window leaves beside the segments' reports (translate now requires it);
  its assertions are unchanged. No other existing test changed.

### TDD evidence

RED (tests and fakes written, no implementation; `wip:` commit `d1d3237`):
```
$ .venv/Scripts/python.exe -m pytest tests/test_handoff.py tests/test_validate_workflow.py tests/test_e2e_chain.py
E   ImportError: cannot import name 'handoff' from 'lib' (…/scripts/lib/__init__.py)
E   ModuleNotFoundError: No module named 'validate_workflow'          (×2)
3 errors during collection
$ fnm exec --using=22 npm.cmd test
not ok 208 - a SQL workflow's script calls are exactly what they were, plus target_check.py, the two prompt_context.py calls and the one validate_workflow.py call
not ok 243 … not ok 251   (the nine new chain tests)
# tests 251  # pass 241  # fail 10  # skipped 0
```
Expected: the modules did not exist; the orchestrator never called `validate_workflow.py` and never read
`validation_workflow.json`.

GREEN (focused, after the implementation):
```
$ .venv/Scripts/python.exe -m pytest tests/test_e2e_chain.py tests/test_validate_dbt.py tests/test_validate_snowpark.py \
    tests/test_validate_segment.py tests/test_validation_combine.py tests/test_e2e_parity.py tests/test_cookbook_snowpark.py \
    tests/test_handoff.py tests/test_validate_workflow.py
159 passed in 195.65s (0:03:15)
$ fnm exec --using=22 npm.cmd test
# tests 251  # pass 251  # fail 0  # skipped 0        (baseline 242; +9; the real-Python integration test ran, not skipped)
$ tsc --noEmit -p .                                     clean (exit 0)
```
Full pytest: see "Verification" below.

## Per-sample chain verdicts (CLI, scratch copy outside the repo)

`validate_workflow.py <wf> --root <scratch>` over a copy of the committed `workflows/` + `mappings/`, and `wf_0007`
built with `tests.helpers.prepare_workflow` (manifest `output_kind: dbt` recorded as analyze would):

| workflow | exit | verdict | sets | idempotent | first_divergence |
|---|---|---|---|---|---|
| wf_0001 | 0 | PASS | normal/period_end/empty/edge all PASS | true | null |
| wf_0002 | 0 | PASS | all four PASS | true | null |
| wf_0003 | 0 | PASS | all four PASS | true | null |
| wf_0004 | 0 | PASS | all four PASS | true | null |
| wf_0005 | 2 | — | — | — | — (T3/MANUAL: nothing translated; `seg_01 has no contract.json` — a usage error, correctly; the orchestrator never runs translate for T3) |
| wf_0006 | 0 | PASS | all four PASS (a Snowpark seam crossed twice) | true | null |
| wf_0007 (dbt) | 0 | PASS | all four PASS | true | null; boundaries `seg_01/3_J` PASS; finals REGION_ATTAINMENT, ATTAINMENT_HISTORY PASS |

No committed sample has a composition finding.

## Brief corrections

1. **The raising fixture reads a TABLE seg_02 never writes, not a column.** The brief: "seg_03 selects a column seg_02
   does not produce (make seg_03's isolated golden intermediate carry it so it passes alone)". That golden intermediate
   is `golden/intermediates/seg_02/<set>/3_Output.csv` — the same file `seg_02`'s own boundary is judged against
   (`validation.golden_path` for a work output and `load_golden.load_intermediate` for a downstream input resolve to one
   path). A column in it that `seg_02` does not produce is `compare._schema_problems`' "columns missing from actual" →
   a `TYPE` FAIL at `seg_02`'s boundary, which (a) makes `seg_02` fail alone and (b) under R-W1 makes the first
   divergence `seg_02`, contradicting the test's `first_divergence["segment"] == "seg_03"`. Smallest correction:
   `seg_03`'s contract declares a second input from `seg_02` (stream `3_Keep`, table `MIG_WORK.WF0008_SEG_02_KEEP`) with its
   own golden intermediate, and `seg_03` joins it; alone every segment PASSes (asserted), in the chain nothing creates
   that table and `seg_03` raises. The test's assertions are the brief's.
2. **`test_a_non_deterministic_segment…` uses `UNIFORM(1, 1000000, RANDOM()) * 0.000001`**, the form
   `test_validate_segment.py` uses: sqlglot renders `UNIFORM(0, 1, RANDOM())` with integer bounds as
   `CAST(FLOOR(0 + RANDOM() * 2) AS BIGINT)` — a coin flip per row, so two runs would be equal 1 time in 8 (flaky). The
   brief allowed "whichever random form".
3. **`test_the_dbt_sample…` records `manifest.output_kind = "dbt"` before calling `validate_workflow`.**
   `prepare_workflow` never runs the analyze stage, which is what writes `output_kind` into a manifest (stages.ts
   `mirroredKind`); no Python script writes it. Without it `validate_workflow` would (correctly, never guessing) treat
   `wf_0007` as a procedures workflow and exit 2 for its missing `proc.sql`.

## Design decisions not spelled out in the brief

- **`ChainError` lives in `lib/validation.py`** (re-exported as `validate_workflow.ChainError`): `validate_dbt` builds one
  for a failed `dbt run`, and `validate_workflow` imports `validate_dbt`, so defining it in `validate_workflow` would be a
  circular import. `chain_report` reads the segment from `error.segment`.
- **What counts as a segment raising.** SQL: `ProcError`/`BackendError` only — exactly what `validate_segment` treats as a
  domain FAIL (the brief's sketch caught any `Exception`, which would have routed an internal bug to a fixer). Snowpark:
  any exception from the hand-in, `run()` or the hand-back — exactly what `validate_snowpark` treats as domain. Loading
  golden data into the Snowpark session is outside that scope, so a missing golden file stays a usage error (exit 2), as
  in `validate_snowpark._prepare_run`.
- **Non-determinism is localised.** A chain that is not idempotent is FAIL (the brief); its divergence is `boundary` at
  the first entry (chain order) whose relation differs between the runs, unless a failing boundary or a raise came first
  (`chain_report(..., diverging=…)`). Without this a non-idempotent chain whose outputs all passed on the judged run
  would be FAIL with `divergence_kind: null`, and the brief's `chainCheck` would park it as
  `chain: ? (raised) after 1 fixer round`. A non-deterministic segment is that segment's defect — never accumulated
  tolerance — so it goes to the fixer.
- **Across golden sets a `boundary` outranks a `chain_drift`** in `validation_workflow.json` (`_aggregate_chain`):
  `aggregate_sets` would take the body of the first FAIL set, so a drift in `normal` would hide a fixable boundary in
  `edge`. R-W1's "if any boundary FAILs → boundary", applied across sets. Per-set reports are untouched.
- **Report shape**: every key of the shared shape is present; `segment` is `null` (a workflow report belongs to no single
  segment); clusters keep `compare`'s order within an entry and are listed in chain order (not re-sorted by class), so the
  first cluster is the earliest divergence; the `error` of a raise is `chain stopped at <seg>: <Type>: <message>`.
- **`chainCheck` records the fixer round's outcome in `segment_status`** before parking (`NEEDS_HUMAN` when the round did
  not PASS the segment); the brief's sketch left the stale `PASS`. `status.translate` is still set before any save (F11).
- **The dbt gate also applies to the all-PASS resume path** (the brief: "after a PASS"): the procedures resume path runs
  the chain again, the dbt analogue is re-reading the report `validate_dbt.py` wrote in the same run.
- `_failed_segment` (dbt): the segment owning the first model dbt reports failed (else skipped), in chain order.
- `--backend` is `choices=("duckdb",)` (`BACKENDS`); `validate_workflow(..., backend=…)` raises `ValueError` for any other.

## Verification (final, on the committed tree)

On the tree committed as `ff25202` (identical tree to the last `wip:` commit the suites ran on):
```
$ fnm exec --using=22 npm.cmd test
# tests 251  # pass 251  # fail 0  # skipped 0        (baseline 242, +9; the real-Python wf_0001 integration run
                                                       now runs validate_workflow.py for real, and passes)
$ node …/typescript/bin/tsc --noEmit -p .             clean (exit 0)
$ .venv/Scripts/python.exe -m pytest
1551 passed in 358.70s (0:05:58)                      (baseline 1488 / 0 skipped; +63: handoff 41, validate_workflow 16,
                                                       e2e_chain 6; 0 skipped)
```
The per-sample chain verdicts above came from the CLI on the same tree.

## Files changed

`scripts/validate_workflow.py` (new), `scripts/lib/handoff.py` (new), `scripts/lib/types_map.py`,
`scripts/lib/validation.py`, `scripts/validate_snowpark.py`, `scripts/validate_dbt.py`, `orchestrator/stages.ts`,
`orchestrator/test/fakes.ts`, `orchestrator/test/stages.test.ts`, `tests/chain_fixtures.py` (new),
`tests/test_handoff.py` (new), `tests/test_validate_workflow.py` (new), `tests/test_e2e_chain.py` (new),
`docs/reference/large-workflows.md`, `docs/reference/output-targets.md` (two table rows).

## Self-review findings

- Hand-off hygiene: no absolute path, login name or scratch pointer in any committed file (grep of the diff); the scratch
  runner stayed in the scratch directory.
- `compare.py` untouched; `validate_segment.py` untouched; the per-segment script-call sequences (SQL, Snowpark, dbt) are
  unchanged apart from the one chain call.
- Fixed during self-review: the fixture comment said Alteryx "never rounded" (it is the *translation* that rounds and a
  human accepted it); a heredoc test edit that had split a string literal.

## Concerns

- **Runtime.** The chain costs one extra full run per golden set plus one idempotency re-run, after every segment already
  ran twice for its own validation (the whole pytest suite now takes ~6 minutes here; the focused run of the new and touched
  files, e2e chains included, took 3 min 16 s). A large real workflow pays this once per translate, and again after the one fixer round.
- **`docs/reference/output-targets.md` is outside the brief's file list** (two added table rows, no other change); no
  other wave-5/6 task owns it.
- **Rows cross into Snowpark ordered by every column.** Deterministic by design; a Snowpark procedure that silently
  depends on its input's physical order (and does not sort, as `wf_0006`'s does) would see a stable order here that
  production does not promise. The reviewer's rules already require such a procedure to sort.
- Pre-existing, not changed (moved verbatim): `_coerce` returns a non-string cell of a `StringType` column as-is (e.g.
  a float), and DuckDB casts it to text on load — which is what lets the wrong-type test see a `TYPE` difference.

## Things P2, W2 and G must know

**P2 (real Snowflake backend, `--backend snowflake`):**
- `validate_workflow.py` takes `--backend` with `choices=BACKENDS = ("duckdb",)`; `validate_workflow(repo, wf, sets, *,
  backend="duckdb")` raises `ValueError` for anything else. The chain backend is built in ONE place: `_run_chain`'s
  `backend = DuckDBBackend()` (the idempotency re-run calls `_run_chain` again, so it gets a fresh one too).
- Judging uses the chain backend itself: golden files are loaded as `MIG_COMPARE.CHAIN_EXPECTED_<n>` and
  `compare.compare` runs against the actual relation (`v.actual_table`) in the same backend; `v.ordered_rows` for the
  idempotency snapshot.
- The hand-off exists only because the local Snowpark double and DuckDB are two engines. `table_from_backend` maps
  DuckDB type names (`types_map.duckdb_to_alteryx`), so it will refuse Snowflake `DESCRIBE` type names; on a real account
  a Snowpark segment would run against the same account and need no hand-off at all — `_run_snowpark_segment` is the
  seam to branch.
- `validate_dbt.py` now writes `validation_workflow*.json` (with `"target": "dbt"`) at the end of `validate_dbt()`;
  `--backend` there must keep doing so. Stale workflow reports are cleared first thing in both scripts.
- `lib.validation.ChainError(segment, cause)` and `chain_report(wf, set, entries, error=None, *, diverging=())`,
  `write_workflow_reports(repo, wf, sets, reports, (idempotent, diff), *, extra=None)`,
  `clear_stale_workflow_reports(repo, wf)` are the report API; entries are
  `{segment, stream, kind: "work"|"target", output, relation, report}` in chain order.

**W2 (seam check, batched analyzer; stages.ts):**
- In `stageTranslate` the order is now: wave loop → `chainCheck(env, m)` → `procs/master.sql` → VALIDATED. `chainCheck`
  calls `migrateSegment(env, m, seg, { firstIteration: 1, iterations: 2, note })`; `migrateSegment`'s new third
  parameter is `SegmentOptions` (all optional; defaults are the old behaviour).
- New park reasons: `chain: script-error`, `chain-drift: <output>`, `chain: needs_human`,
  `chain: <seg> <stream> after 1 fixer round`, `chain: <seg>: <reason>`, `chain: FAIL` (unreachable in practice), and for
  dbt `dbt: chain FAIL`. `ChainReport` is a module-private interface in stages.ts (nothing added to types.ts — P1 owned it
  this wave).
- Fakes: `fakeValidateWorkflow` inside `makeEnv` (a per-env `chainCalls` counter) with scenarios `chain-boundary:<seg>`,
  `chain-boundary-stuck:<seg>`, `chain-drift`, `chain-crash`; `fakeValidateDbt` writes `validation_workflow.json`
  (FAIL on `chain-fail:dbt` or any failing segment). Any new fake scenario that reaches translate must leave the chain
  PASSing, or expect a chain park.
- The raising fixture (`seg_03` declares an input stream `seg_02` never produces) is exactly the static seam mismatch
  `check_seams.py` exists to catch before translate; the chain catches it dynamically after.

**G (offline run, committed workflows):**
- Every procedures workflow that reaches VALIDATED now also leaves `workflows/<wf>/validation_workflow.json` and
  `validation_workflow.<set>.json` (four sets); `wf_0007` gets the same two from `validate_dbt.py`, with
  `"target": "dbt"` at the top level. Their verdicts on the current samples are all PASS (table above). They contain no
  paths (a PASS carries no `error`); `runtime_ms` differs run to run like every other report.
- `tests/test_committed_workflows.py` has no file allow-list, so nothing breaks; a pin that every VALIDATED workflow's
  `validation_workflow.json` is PASS with `first_divergence: null` would be cheap.
- A committed dbt workflow's manifest must carry `output_kind: dbt` (the orchestrator writes it) for
  `validate_workflow.py <wf>` to delegate rather than look for `proc.sql`.
- wf_0005 (T3, MANUAL) has no chain: `validate_workflow.py wf_0005` is a usage error (exit 2), and the orchestrator never
  calls it for a T3 workflow.

## Commits

`ff25202` feat: validate_workflow.py tests the stitched whole — every segment on its upstream's actual output, first
divergence localised, chain drift parked. (The three `wip:` commits made along the way — `d1d3237` tests (RED),
`0b08226` implementation, `ea23df6` docs — were squashed into it with `git reset --soft 8299324`; the tree is
identical, the wip SHAs are no longer on the branch.)

## Fix round 1

Rulings in `task-W1-fix1.md` (review of `ff25202`: 23 adversarial chains, 20 correct; brief corrections and all five
design additions accepted). Every item RED first. Commits: see the end of this section.

### What changed

- **I1 — crash inside the chain's fixer round** (`orchestrator/stages.ts`, `chainCheck`). Before the round starts,
  the segment's PASS is withdrawn (`delete m.segment_status[seg]`) and the manifest SAVED. A crash anywhere in the round
  (compile check, review, validation) now leaves that segment unrecorded, so the resume's wave loop runs its whole
  sequence again (translator → compile check → reviewer → validator), then the chain, and only then `VALIDATED`. I chose
  deletion over a marker value: the resume path already treats "not recorded PASS" as "run this segment", and no new
  status value reaches the vocabulary.
- **I2 — order dependence** (`scripts/lib/handoff.py`, `scripts/validate_workflow.py`).
  - `handoff.table_from_backend` returns rows in the backend's physical order (`SELECT *`, no sort).
  - The judged (first) run changes no table's order and records, per `(segment, input relation)`, a SHA-256 of the row
    list IN ORDER that the segment was about to see (`_present`).
  - The idempotency re-run (`_run_chain(..., rerun=True)`) reverses the raw golden inputs once (the `MIG_GOLDEN.*` base
    tables behind the source views, from `load_set`'s own `loaded` list; for a Snowpark segment the session's source
    tables, read back and re-saved reversed), and before every segment reverses each upstream stream it declares and
    each append/merge target it writes into — SQL: `CREATE OR REPLACE TABLE t AS SELECT * FROM t ORDER BY rowid DESC`
    (`_reverse`, types kept); Snowpark: the handed-in row list reversed — **but only when that input arrives in exactly
    the order the first run showed it** (digest equal). Without that rule the chain reversed twice: an order-preserving
    upstream (`seg_01` 1:1 from ITEMS) already passes its reversed raw input on reversed, and reversing it again handed
    `seg_02` the first run's order (the reviewer's d1a/d2a would still PASS).
  - The multiset comparison of the two runs is unchanged; an order-dependent segment makes them differ → not idempotent
    → `boundary` at that segment (design addition 1).
- **I3 — vanished Snowpark output** (`_run_snowpark_segment`): when `table_from_snowpark` returns `None`,
  `DROP TABLE IF EXISTS <fqn>` in the chain backend, so the judge reports the missing table instead of the
  `targets_before` copy the backend held.
- **M1** (`chainCheck`): `validation_workflow.json` is deleted before every chain call; any exit (0 or 1) that left no
  report parks `chain: script-error` (exit 2 already did).
- **M2** (`_idempotency`, `lib.validation.chain_report(..., rerun_error=)`): a re-run that raises returns its
  `ChainError`; the report's `error` gains `idempotency re-run: chain stopped at <seg>: …` and the divergence is a
  `boundary` at the raising segment (`stream: null`), placed in chain order right after that segment's own judged
  outputs (so a failing boundary of the SAME segment on the judged run is still reported with its stream, and one
  earlier in the chain still wins). `idempotency_diff` still lists every relation (none could be compared).
- **M3** (`migrateSegment`'s `SegmentOptions.note` → `repairTask`): the chain round's fixer task REPLACES the standing
  "read validation.json and review.json first and change only what their diagnosis points at" sentence. It names
  `first_divergence` and the diff clusters in `workflows/<wf>/validation_workflow.json`, says the segment's own
  `validation.json` PASSes as expected (it was fed golden intermediates) and asks to keep that PASS. Every other fixer
  task is byte for byte as before (pinned by the new test's second half).
- **M4**: the second-failure reason is `chain: <seg> <stream> after 1 fixer round on <fixed seg>`. A round-0 FAIL
  without a named segment (unreachable with `chain_report`) parks `chain: FAIL with no first_divergence`.
- **M5** (`tests/chain_fixtures.py::build_keyed_drift`): wf_0010 in doubles with a KEYED final `TOTALS(K, TOTAL)`;
  `seg_01` adds 9e-7 per row (inside `float_abs`). 3 rows → keyed final 2.7e-6 off → `chain_drift` with the boundary
  PASS; 1 row → PASS. (Both pass on the old code as well: they are pins, as the ruling asks.)
- **M6**: a chain that exits 0 with `needs_human: true` parks `chain: needs_human`. On a FAIL, `chain_drift` is still
  checked before `needs_human` (both park for a human; the drift reason says more).
- **M7**: `ChainError.entries: list[dict]` is set to `[]` in `__init__`.
- **Docs**: `docs/reference/large-workflows.md` — a "Row order" paragraph (what the re-run does, why, what it still
  cannot see: a dependence that gives identical results under both orders, fewer than two distinct rows, golden sets
  after the first, and a dbt workflow's re-run, which is DV7's plain fresh sandbox); the Snowpark-seam paragraph no
  longer claims a sorted hand-off and names the vanished-output rule; the orchestrator table gains the withdrawn PASS,
  the M3 task, the M4 reason, needs_human "whatever the verdict", and "no report written" under script-error; the
  "what this does not prove" paragraph says `rowid` order is the double's. `validate_workflow.py`'s docstring likewise.

### Tests (reviewer probes turned into committed tests)

`tests/test_validate_workflow.py` (+14, now 30):
- `test_a_segment_that_relies_on_its_input_order_is_a_non_idempotent_boundary[sql|snowpark]` — reviewer d1a / d2a
  (`build_order_dependent`: `seg_02` is a Sample, `LIMIT 2` / `.limit(2)`); each segment alone PASS (asserted); chain
  FAIL, `idempotent: false`, `idempotency_diff` = seg_02's stream and the final, every judged boundary PASS,
  `boundary` at `seg_02/3_Output`.
- `test_an_upstream_order_by_does_not_hide_a_downstream_order_dependence[sql|snowpark]` — reviewer d1b / d2b
  (`seg_01` ends `ORDER BY PRICE`): FAIL, `boundary` at `seg_02`, not idempotent.
- `test_the_wf_0006_snowpark_segment_without_its_own_sort_fails_the_chain` — reviewer d4: FAIL, `boundary` at
  `seg_02/3_1`.
- `test_the_idempotency_rerun_presents_every_input_in_reversed_order` and
  `test_the_rerun_reverses_what_an_upstream_sort_would_otherwise_hand_on_unchanged` — a spy on the Snowpark hand-in:
  `[[1,2,3],[3,2,1]]` and `[[3,1,2],[2,1,3]]` (the no-double-reversal rule, both ways).
- `test_reversing_a_table_keeps_its_types_and_reverses_its_physical_order`.
- `test_a_vanished_snowpark_output_is_a_missing_table_not_a_stale_copy` — reviewer k.
- `test_a_rerun_that_raises_is_a_boundary_at_the_raising_segment` — reviewer e3 (a Snowpark segment that raises only on
  its second run, via a marker file under `tmp_path`), and `test_chain_report_places_a_rerun_raise_at_its_segment_in_chain_order`.
- `test_a_keyed_final_past_tolerance_is_chain_drift_while_every_boundary_is_inside_it`,
  `test_a_keyed_final_inside_tolerance_passes` — reviewer h3/h4 (M5).
- `test_a_chain_error_carries_typed_entries` (M7).

`orchestrator/test/stages.test.ts` (+6, net +5):
- `a crash at seg_02's {compile_check|reviewer|validator} inside the chain's fixer round re-runs seg_02's full gates on
  resume` — the reviewer's crash-window probe: after the crash, `seg_02` is not recorded PASS on disk and translate is
  unset; the resume runs `translator`, `compile_check`, `reviewer`, `validate_segment` for seg_02 only, then
  `validate_workflow`, and ends VALIDATED.
- `an exit 1 that left no chain report parks with chain: script-error, never acting on an older report` — the
  reviewer's stale-report probe; no fixer runs and the stale file is gone.
- `a PASSing chain report that says needs_human parks translate` (M6; fake scenario `chain-needs-human`).
- `a chain-triggered fixer round points at validation_workflow.json's first_divergence and expects the segment's own
  PASS` replaces my round-0 test of the appended note (M3), and pins the ordinary fixer task unchanged.
- Updated: "a boundary that survives the round parks translate" now expects `… after 1 fixer round on seg_02` (M4).

### RED (tests written, no fix; `188b791`)

```
$ .venv/Scripts/python.exe -m pytest tests/test_validate_workflow.py
FAILED …test_a_segment_that_relies_on_its_input_order_is_a_non_idempotent_boundary[sql]       assert 'PASS' == 'FAIL'
FAILED …test_a_segment_that_relies_on_its_input_order_is_a_non_idempotent_boundary[snowpark]  assert 'PASS' == 'FAIL'
FAILED …test_an_upstream_order_by_does_not_hide_a_downstream_order_dependence                 assert 'PASS' == 'FAIL'
FAILED …test_the_wf_0006_snowpark_segment_without_its_own_sort_fails_the_chain                  assert 'PASS' == 'FAIL'
FAILED …test_the_idempotency_rerun_presents_every_input_in_reversed_order    [[1,2,3],[1,2,3]] != [[1,2,3],[3,2,1]]
FAILED …test_the_rerun_reverses_what_an_upstream_sort_would_otherwise_hand_on_unchanged   [1,2,3] != [3,1,2] (sorted)
FAILED …test_reversing_a_table_keeps_its_types_and_reverses_its_physical_order   no attribute '_reverse'
FAILED …test_a_vanished_snowpark_output_is_a_missing_table_not_a_stale_copy       assert 'PASS' == 'FAIL'
FAILED …test_a_rerun_that_raises_is_a_boundary_at_the_raising_segment   first_divergence seg_01/2_Output (the first relation)
FAILED …test_chain_report_places_a_rerun_raise_at_its_segment_in_chain_order   unexpected keyword argument 'rerun_error'
FAILED …test_a_chain_error_carries_typed_entries   'ChainError' object has no attribute 'entries'
11 failed, 18 passed                     (the two keyed-drift pins passed: M5 adds guards, not a behaviour change)
$ fnm exec --using=22 npm.cmd test
not ok 245 - a boundary that survives the round parks translate            (old reason, without "on seg_02")
not ok 251 - a chain-triggered fixer round points at validation_workflow.json's first_divergence …
not ok 252..254 - a crash at seg_02's compile_check|reviewer|validator … re-runs seg_02's full gates on resume
not ok 255 - an exit 1 that left no chain report parks with chain: script-error …
not ok 256 - a PASSing chain report that says needs_human parks translate
# tests 256  # pass 249  # fail 7
```

### GREEN

```
$ .venv/Scripts/python.exe -m pytest tests/test_validate_workflow.py tests/test_handoff.py   70 passed (then 71 with the SQL upstream-sort case added)
$ fnm exec --using=22 npm.cmd test         # tests 256  # pass 256  # fail 0  # skipped 0   (251 + 5)
$ tsc --noEmit -p .                         clean
$ .venv/Scripts/python.exe -m pytest        1565 passed in 361.51s (0:06:01)   (1551 + 14; 0 skipped)
```

### Per-sample chain verdicts after I2 (CLI on a scratch copy; reversed inputs on every re-run)

| workflow | exit | verdict (4 sets) | idempotent | first_divergence |
|---|---|---|---|---|
| wf_0001 | 0 | PASS ×4 | true | null |
| wf_0002 | 0 | PASS ×4 | true | null |
| wf_0003 | 0 | PASS ×4 | true | null |
| wf_0004 | 0 | PASS ×4 | true | null |
| wf_0005 | 2 | — (T3/MANUAL, nothing translated: a usage error, correctly) | — | — |
| wf_0006 | 0 | PASS ×4 (its `seg_02` sorts its own input) | true | null |
| wf_0007 (dbt) | 0 | PASS ×4 | true | null |

**No canned procedure relied on physical order; no `samples/**` file was changed.**

### Files changed this round

`scripts/lib/handoff.py`, `scripts/lib/validation.py`, `scripts/validate_workflow.py`, `orchestrator/stages.ts`,
`orchestrator/test/{fakes,stages.test}.ts`, `tests/chain_fixtures.py`, `tests/test_validate_workflow.py`,
`docs/reference/large-workflows.md`.

### Concerns

- **The reversal is the DuckDB double's.** `ORDER BY rowid DESC` and "physical order" exist on DuckDB and in the
  Local Testing Framework; a Snowflake backend (P2) has no physical order to reverse. P2 must either keep order exposure
  on the local double only or find another way to present a different order; `_reverse`/`_present` are the seam.
- **What it still cannot see** (documented): a dependence whose result is the same under both orders, and any order
  dependence that shows only on a golden set after the first (the re-run is on the first set only, as for a segment).
- **dbt is not covered.** A dbt workflow's re-run is `validate_dbt.py`'s DV7 fresh sandbox (inputs in file order,
  dbt runs every model itself), so order dependence between dbt models is not exposed. Reversing the rerun sandbox's
  raw inputs there would be a small change, but it alters DV7's semantics (a ruled deviation), so I did not do it
  without a ruling.
- **Memory.** The re-run keeps one SHA-256 per `(segment, input)`, not the rows; reading each input's rows once per run
  to compute it is the same order of cost as the existing `ordered_rows` idempotency snapshot.
- The I1 resume re-translates the segment from iteration 0 (the translator, not a fixer): the safest re-entry, at the
  cost of one translator call after a crash.

### Things P2, W2 and G must know (additions)

- **P2**: `_run_chain(repo, wf, set, plan, seen, *, rerun=False)`; `_reverse(backend, fqn)` uses DuckDB's `rowid` through
  `backend.execute`; `_present` compares digests of `SELECT * FROM <fqn>` in order. On a Snowflake backend "physical
  order" does not exist — decide there. `handoff.table_from_backend` no longer sorts.
- **W2**: `SegmentOptions.note` is now `repairTask` (replaces the fixer's standing sentence); `chainCheck` deletes
  `validation_workflow.json` before each call and saves the manifest with the segment's PASS withdrawn before the
  round; reasons added: `chain: <seg> <stream> after 1 fixer round on <fixed seg>`, `chain: FAIL with no
  first_divergence`; fake scenario `chain-needs-human`.
- **G**: the committed samples' chain reports stay PASS with the reversed re-run (table above).

### Commit

`05612f2` wip: fix round 1 (I1, I2, I3, M1-M7) — reversed-order re-run exposes order dependence, crash-safe chain
fixer round, vanished Snowpark output is missing, fresh chain report per call. (The round's own `wip:` commits —
`188b791` RED tests, `474a024` implementation, `a2d7ae3` docs, `3e8fa4b` M6 ordering + SQL upstream-sort case — were
squashed into it with `git reset --soft ff25202`; the tree is identical.) Verified on that tree: node 256/256, tsc clean,
pytest 1565 passed / 0 skipped, and the per-sample chain table above re-run on it.

## Fix round 2

Ruling (coordinator): my round-1 concern 2 ruled YES — it amends DV7. Concern 1 (no physical order on real Snowflake)
ruled for Task P2; nothing implemented here. Commit `1976385` wip: fix round 2 — dbt re-run loads inputs in reversed
order.

### What changed

- `scripts/validate_dbt.py`: `_load_sandbox(..., *, reverse=False)` / `_run_project(..., *, reverse=False)`. The DV7
  re-run (`_rerun` sandbox, first golden set) is built with `reverse=True`: after `load_set`, every raw golden input
  table (`MIG_GOLDEN.*`, the base tables the source views read) and every `targets_before` table (`MIGDB.MIG_WORK.*`) is
  rewritten in reversed row order. The first sandbox keeps file order; the two are still two FRESH sandboxes compared as
  row multisets. Docstring's DV7 paragraph updated.
- `scripts/lib/validation.py`: `reverse_physical_order(backend, table)` — the one reversal helper
  (`CREATE OR REPLACE TABLE t AS SELECT * FROM t ORDER BY rowid DESC`, types kept); `validate_workflow._reverse` is now
  that function (`validate_dbt` cannot import `validate_workflow`, which imports it).
- `docs/superpowers/specs/2026-09-22-output-targets-design.md` §5.3: one sentence — per DV7 as amended in this round, the
  second run's fresh sandbox loads raw golden inputs and `targets_before` in reversed row order, compared as row
  multisets, so a row-order-dependent model is non-idempotent and FAILs.
- `docs/reference/large-workflows.md`: the dbt paragraph says what the re-run now does and what it still cannot see (an
  upstream model that sorts its own output hands the same order to both runs); the round-1 "dbt is not covered" sentence
  is removed.

### Tests (`tests/test_validate_dbt.py`, +2)

- `test_a_model_that_depends_on_row_order_is_not_idempotent`: the wf_0009 dbt fixture with `items_out.sql` taking
  `limit 1` from the unordered upstream model, its golden output the first record in file order (ID 1). The judged run
  matches (no diff clusters); the reversed re-run keeps ID 3 → `seg_02` FAIL, `idempotent: false`,
  `MIGDB.MIG_WORK.ITEMS_OUT` in `idempotency_diff`; `seg_01` stays PASS and idempotent; `validation_workflow.json` FAIL,
  `boundary` at `seg_02/2_T` (`ITEMS_OUT`).
- `test_the_rerun_sandbox_loads_raw_inputs_and_targets_before_in_reversed_order`: `_load_sandbox` with and without
  `reverse`, read back: raw input `[1,2,3,4]` → `[4,3,2,1]` (and through the source view), `targets_before` `[1,7]` →
  `[7,1]`, column types unchanged. No dbt run.

RED (tests written, no fix):
```
$ .venv/Scripts/python.exe -m pytest tests/test_validate_dbt.py -k "row_order or reversed_order"
E       assert (True is False)                                            (seg_02 idempotent under the file-order re-run)
E       TypeError: _load_sandbox() got an unexpected keyword argument 'reverse'
2 failed, 9 deselected
```

GREEN:
```
$ .venv/Scripts/python.exe -m pytest tests/test_validate_dbt.py tests/test_validate_workflow.py tests/test_handoff.py
82 passed in 88.83s
$ fnm exec --using=22 npm.cmd test          # tests 256  # pass 256  # fail 0  # skipped 0   (unchanged: no TS change)
$ tsc --noEmit -p .                         clean
$ .venv/Scripts/python.exe -m pytest        1567 passed in 376.00s (0:06:15)   (1565 + 2; 0 skipped)
```
The committed dbt sample still PASSes on all four sets with the reversed re-run: `test_e2e_parity.py`'s wf_0007 case and
`test_e2e_chain.py::test_the_dbt_sample_writes_its_chain_report_from_its_own_run` are in the full run above, and the CLI
on a scratch copy gives wf_0001-0004 and wf_0006 PASS ×4, idempotent; wf_0007 (dbt) PASS ×4, idempotent;
wf_0005 exit 2 (T3, nothing translated), as before.

### Things P2 must know (addition)

- Ruled for P2: on `--backend snowflake` the idempotency re-run (chain and dbt) loads the raw inputs in reversed INSERT
  order as a best-effort perturbation, documented as weaker than the local reversal because Snowflake guarantees no scan
  order either way. Today's seams: `lib.validation.reverse_physical_order` (DuckDB `rowid`), `validate_dbt._load_sandbox(...,
  reverse=True)`, and `validate_workflow._run_chain(..., rerun=True)` / `_present`.

## Fix round 3

Rulings (coordinator, after the opus re-review confirmed all round-1 findings addressed): R1 and R2 below, plus the
fixer's inputs. Commit `ff708b2` wip: fix round 3 — reverse unless already reversed; materialise view inputs.

### What changed

- **R1 — partial reorders** (`validate_workflow._present`). The first run now records the digest of the exact REVERSE of
  what each segment saw of each input; the re-run reverses an input UNLESS it arrives as exactly that reverse. So an
  input that arrives in the first run's order, or only partly reordered (an upstream sort on a non-unique key, a
  Filter's branches unioned back), is reversed, and one an order-preserving upstream already handed on reversed is left
  alone: whatever order it arrives in, the segment never sees the first run's order again, unless that order reads the
  same backwards (a single row, identical rows). The rule the reviewer verified in scratch (`rr_attack_spy.py alt`).
- **R2 — a view stream** (`lib.validation.reverse_physical_order`): a VIEW is materialised into a table under the same
  name in the order it presents (`CREATE OR REPLACE TABLE MIG_COMPARE.REVERSE_MATERIALISED AS SELECT * FROM <view>`,
  `DROP VIEW`, `CREATE TABLE <name> AS … ORDER BY rowid DESC`), then reversed; a table is reversed as before. View
  detection reads `information_schema.tables.table_type` through `backend.query`. Any error while presenting a SQL
  segment's inputs (reading, reversing) is now a `ChainError` at that segment (`_run_sql_segment`), and an error
  reversing the raw golden inputs at the start of the re-run is a `ChainError` at the chain's first segment — reported
  as a `boundary` with `error`, never an uncaught crash (exit 2). The Snowpark hand-in was already inside that scope.
- **Fixer inputs** (`.github/agents/fixer.agent.md`): one line — `workflows/<id>/validation_workflow.json when the task
  says the stitched workflow (the chain test) first diverges at your segment`, marked
  `<!-- amended: output targets phase 2 -->` (the fixer is one of `PHASE_2_AGENTS`; no Task 12 marker, so
  `test_amended_paragraphs_are_marked` and `test_unamended_agents_keep_their_spec_bodies` stay green).
- **Docs** (`docs/reference/large-workflows.md`): the "Row order" paragraph no longer claims the re-run presents "the
  reverse of the order that segment saw"; it states the rule above, that a view stream is materialised first, and the
  remaining blind spot — an order dependence that yields the same result under both presentations (a "first N" that
  picks the same rows from both orders, an order that reads the same backwards) and one that shows only on a golden set
  after the first. The exit-code sentence that round 1's insertion had glued to that paragraph is its own paragraph
  again. The dbt paragraph says a model after an upstream that re-sorts fully or partly may not be exposed (dbt runs
  every model itself, so the order cannot be changed between two models).

### Tests (RED first)

`tests/chain_fixtures.py`: `build_partial_reorder(tmp_path, *, seg_01_select, snowpark=False)` — 4 items with PRICE
tied in pairs, `seg_01` = `TIE_SORT_SELECT` (A2) or `FILTER_UNION_SELECT` (A3) giving 1, 3, 2, 4 (the golden stream is
written in that order, so every segment passes alone), `seg_02` keeps the first two (`LIMIT 2` / `.limit(2)`);
`make_seg_01_write_a_sorted_view(repo)` (the reviewer's V2).

`tests/test_validate_workflow.py` (+8, now 38):
- `test_a_partial_reorder_upstream_does_not_hide_a_first_n_consumer[tie_sort|filter_union × sql|snowpark]` — A2/A3:
  every segment alone PASS (asserted), judged boundaries PASS, chain FAIL, not idempotent, `boundary` at `seg_02/3_Output`.
- `test_the_rerun_presents_a_partly_reordered_input_reversed` — spy on the Snowpark hand-in: `[[1,3,2,4],[2,4,1,3]]`
  (seg_01's re-run output 3, 1, 4, 2 is not the exact reverse, so it is reversed once more).
- `test_a_correct_segment_that_writes_its_stream_as_a_sorted_view_still_passes` — V2: PASS, idempotent, CLI exit 0.
- `test_reversing_a_view_materialises_it_into_a_reversed_table` — types kept; a second reversal works on the table.
- `test_a_reversal_that_fails_is_reported_at_its_segment_never_a_crash` — a reversal forced to raise on seg_02's input:
  FAIL, not idempotent, `boundary` at `seg_02` (`stream: null`), the error text in `error`.

`tests/test_agents_config.py` (+1): `test_fixer_reads_the_chain_report_when_the_stitched_workflow_diverges`.

RED (tests written, no fix):
```
FAILED …test_a_partial_reorder_upstream_does_not_hide_a_first_n_consumer[tie_sort-sql]        assert 'PASS' == 'FAIL'
FAILED …[tie_sort-snowpark]  FAILED …[filter_union-sql]  FAILED …[filter_union-snowpark]        (the same)
FAILED …test_the_rerun_presents_a_partly_reordered_input_reversed    [[1,3,2,4],[3,1,4,2]] != [[1,3,2,4],[2,4,1,3]]
FAILED …test_a_correct_segment_that_writes_its_stream_as_a_sorted_view_still_passes
         BackendError: Binder Error: Referenced column "rowid" not found in FROM clause!   (raised, uncaught)
FAILED …test_reversing_a_view_materialises_it_into_a_reversed_table     (same Binder Error)
FAILED …test_a_reversal_that_fails_is_reported_at_its_segment_never_a_crash   (RuntimeError escaped the chain)
FAILED tests/test_agents_config.py::test_fixer_reads_the_chain_report_when_the_stitched_workflow_diverges
9 failed, 86 deselected
```

GREEN:
```
$ .venv/Scripts/python.exe -m pytest tests/test_validate_workflow.py tests/test_agents_config.py tests/test_handoff.py
136 passed
$ fnm exec --using=22 npm.cmd test          # tests 256  # pass 256  # fail 0  # skipped 0
$ tsc --noEmit -p .                         clean
$ .venv/Scripts/python.exe -m pytest        1576 passed in 373.77s (0:06:13)   (1567 + 9; 0 skipped)
```

### The reviewer's probes against the fixed code (default rule, `scratchpad\p2-rev-W1\`)

`rr_attack.py` — all 16 cases as expected:
```
CASE A1_total_sort_limit2               expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // upstream ORDER BY PRICE (unique), seg_02 LIMIT 2
CASE A2_tie_sort_limit2_sql             expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // upstream ORDER BY PRICE (ties), LIMIT 2 = one tie group
CASE A2_tie_sort_limit2_snowpark        expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // same, seg_02 Snowpark .limit(2)
CASE A2b_tie_sort_limit1_sql            expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // upstream ORDER BY PRICE (ties), LIMIT 1 (inside a tie group)
CASE A3_filter_union_limit2_sql         expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // seg_01 = WHERE P<1.5 UNION ALL WHERE P>=1.5, seg_02 LIMIT 2
CASE A4_upstream_reverses_sql           expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // seg_01 ORDER BY ROW_NUMBER() OVER () DESC; seg_02 LIMIT 2
CASE A4_upstream_reverses_snowpark      expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // Snowpark seg_01 reverses the rows it collected; seg_02 LIMIT 2
CASE A5_snowpark_raw_limit2             expect=FAIL  got=('FAIL', 'boundary', 'seg_01', False, ['PASS', 'PASS'], None)  // Snowpark seg_01 .limit(2) on raw ITEMS
CASE A6_two_inputs_raw_dep_sql          expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // seg_02 joins its stream with the first 2 raw ITEMS ids
CASE A7_single_row                      expect=PASS  got=('PASS', None, None, True, ['PASS', 'PASS'], None)  // 1 row, LIMIT 1
CASE A8_explicit_order_limit_sql        expect=PASS  got=('PASS', None, None, True, ['PASS', 'PASS'], None)  // tie-sort upstream, seg_02 ORDER BY ID LIMIT 2
CASE A8_rownumber_total_order_sql       expect=PASS  got=('PASS', None, None, True, ['PASS', 'PASS'], None)  // QUALIFY ROW_NUMBER() OVER (ORDER BY PRICE, ID) <= 2
CASE A8_snowpark_sort_limit             expect=PASS  got=('PASS', None, None, True, ['PASS', 'PASS'], None)  // Snowpark .sort('ID').limit(2)
CASE A8_plain_1to1_4rows                expect=PASS  got=('PASS', None, None, True, ['PASS', 'PASS'], None)  // the fixture's own 1:1 formula on 4 rows
CASE A0_passthrough_limit2_sql          expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // seg_01 1:1 pass-through, seg_02 LIMIT 2
CASE A0_passthrough_limit2_snowpark     expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // seg_01 1:1 pass-through, Snowpark seg_02 .limit(2)
```
`rr_attack_spy.py spy A2_tie_sort_limit2_sql A3_filter_union_limit2_sql A0_passthrough_limit2_sql A8_explicit_order_limit_sql`:
```
   run1  seg_02 <- MIG_WORK.WF0008_SEG_01_OUT: ids=[Decimal('1'), Decimal('3'), Decimal('2'), Decimal('4')] reverse=False
   rerun seg_02 <- MIG_WORK.WF0008_SEG_01_OUT: ids=[Decimal('3'), Decimal('1'), Decimal('4'), Decimal('2')] reverse=True
CASE A2_tie_sort_limit2_sql             expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // upstream ORDER BY PRICE (ties), LIMIT 2 = one tie group
   run1  seg_02 <- MIG_WORK.WF0008_SEG_01_OUT: ids=[Decimal('1'), Decimal('3'), Decimal('2'), Decimal('4')] reverse=False
   rerun seg_02 <- MIG_WORK.WF0008_SEG_01_OUT: ids=[Decimal('3'), Decimal('1'), Decimal('4'), Decimal('2')] reverse=True
CASE A3_filter_union_limit2_sql         expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // seg_01 = WHERE P<1.5 UNION ALL WHERE P>=1.5, seg_02 LIMIT 2
   run1  seg_02 <- MIG_WORK.WF0008_SEG_01_OUT: ids=[Decimal('1'), Decimal('3'), Decimal('2'), Decimal('4')] reverse=False
   rerun seg_02 <- MIG_WORK.WF0008_SEG_01_OUT: ids=[Decimal('3'), Decimal('1'), Decimal('4'), Decimal('2')] reverse=True
CASE A8_explicit_order_limit_sql        expect=PASS  got=('PASS', None, None, True, ['PASS', 'PASS'], None)  // tie-sort upstream, seg_02 ORDER BY ID LIMIT 2
   run1  seg_02 <- MIG_WORK.WF0008_SEG_01_OUT: ids=[Decimal('1'), Decimal('2'), Decimal('3'), Decimal('4')] reverse=False
   rerun seg_02 <- MIG_WORK.WF0008_SEG_01_OUT: ids=[Decimal('4'), Decimal('3'), Decimal('2'), Decimal('1')] reverse=False
CASE A0_passthrough_limit2_sql          expect=FAIL  got=('FAIL', 'boundary', 'seg_02', False, ['PASS', 'PASS'], None)  // seg_01 1:1 pass-through, seg_02 LIMIT 2
```
`rr_view_sorted.py` / `rr_view_stream.py` (their `import rr_attack` re-runs the 16 cases, same results):
```
CASE V2 CLI exit 0 ["wf_0008: chain PASS {'normal': 'PASS'} (idempotent=True)"]
CASE V1 view stream chain PASS None None True None
CASE V1 CLI exit 0 ["wf_0008: chain PASS {'normal': 'PASS'} (idempotent=True)"]
CASE A7b all-identical rows LIMIT 2 FAIL True {'segment': 'seg_01', 'stream': '2_Output', …}
```
A7b's FAIL is the probe's own data, not the rule: three identical rows repeat `ID = 1` in a stream keyed on `ID`, so
compare reports duplicate keys (`GOLDEN_DATA`/`LOGIC` "duplicate keys in expected/actual") on the judged run; it is
idempotent, as an input that reads the same backwards must be.

### Per-sample chain verdicts (CLI, scratch copy of the final tree)

| workflow | exit | verdict (4 sets) | idempotent | first_divergence |
|---|---|---|---|---|
| wf_0001 | 0 | PASS ×4 | true | null |
| wf_0002 | 0 | PASS ×4 | true | null |
| wf_0003 | 0 | PASS ×4 | true | null |
| wf_0004 | 0 | PASS ×4 | true | null |
| wf_0005 | 2 | — (T3, nothing translated) | — | — |
| wf_0006 | 0 | PASS ×4 | true | null |
| wf_0007 (dbt) | 0 | PASS ×4 | true | null |

### Files changed this round

`scripts/validate_workflow.py`, `scripts/lib/validation.py`, `.github/agents/fixer.agent.md`,
`docs/reference/large-workflows.md`, `tests/chain_fixtures.py`, `tests/test_validate_workflow.py`,
`tests/test_agents_config.py`.
