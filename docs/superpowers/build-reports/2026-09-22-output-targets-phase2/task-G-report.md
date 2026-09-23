# Task G — the offline run for all seven samples and the committed `workflows/wf_0007/`

**Status: DONE** (after one NEEDS_CONTEXT stop at Step 3; the controller ruled the stop's finding
in as kind (h)). pytest 1835 passed / 0 skipped; node 311/311; tsc clean.

Worktree `.worktrees/p2-G` (branch `wt/p2-G`, base `5e3d8a9`). Scratch roots under
`<scratchpad>\p2-G\`, a scratch root outside the repo: `run1` (the run that was copied, re-made fresh
after the ruling), `run1/before-pass3`, `run2` (the independent `git archive` reproducibility export),
`old` (a `git archive 5e3d8a9 workflows` export — the frozen "old" side of the classification),
`norm/{a,b}` (normalised copies), `base` (a `git archive 5e3d8a9` export used only to count the base
collection), `diffs1` (unified diffs of every changed text file), `classify.py` (the key-by-key
classifier). No `git stash` / `checkout --` / `reset --hard`; no subagents; no other worktree or the
main tree touched; the pipeline never ran in the repo or the worktree.

## Commits

**`eabebca`** feat: offline run refreshed for all seven samples — wf_0007's dbt project committed; chain
reports, batch plans and seam checks in every workflow. (The `wip:` commits made along the way —
`8e8558d` tests (RED), `b8c340d` workflows/, `40ed89d` test_deploy fixtures, `de8fbe3` README — were
squashed into it with `git reset --soft 5e3d8a9`; `git diff de8fbe3 eabebca` is only a two-line
docstring rewrap in `tests/test_committed_workflows.py`, re-run: 69 passed.) The commit body records the
classification counts and both reproducibility counts. 227 paths: 133 added, 94 modified (90 under
`workflows/`, the two test files, `tests/test_deploy.py`, `README.md`), 0 deleted.

---

## The first stop (NEEDS_CONTEXT) and the ruling

The first run's old-vs-new classification found one kind outside (a)–(g): `segments/segmentation.json`
gains `params.max_prompt_chars: 60000` in wf_0001–0006 (Task W3 `f3030bc`, documented in
`task-W3-report.md` line 34; `segments`/`order`/`warnings`, `order.json` and every segment `dag.json`
byte-identical). **Ruling: accepted as kind (h).** The controller asked for a FRESH run so the copied
product and the classification come from the same run — everything below is from that fresh run.

## Step 2 — the run (`run1`, fresh)

```bash
# cwd: the worktree. Absolute interpreter (a worktree has no .venv); samplesDir = the worktree's samples/.
SCRATCH="<scratch root outside the repo>/p2-G/run1"
PY="C:/Users/<you>/Desktop/Alteryx to Snowflake/.venv/Scripts/python.exe"
FNM="C:/Users/<you>/AppData/Local/Microsoft/WinGet/Links/fnm.exe"
REPO="$(pwd -W)"
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH" && cp -r scripts mappings catalog "$SCRATCH/"
printf '{"python": "%s", "samplesDir": "%s/samples"}' "$PY" "$REPO" > "$SCRATCH/orchestrator.config.json"
"$PY" scripts/dev/build_samples.py seed --root "$SCRATCH" --samples "$REPO/samples"
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --runner mock --no-interactive
"$PY" scripts/dev/answer_samples.py --root "$SCRATCH" --samples "$REPO/samples"
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --runner mock --no-interactive
cp -r "$SCRATCH/workflows" "$SCRATCH/before-pass3"
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$SCRATCH" --runner mock --no-interactive
diff -rq "$SCRATCH/before-pass3" "$SCRATCH/workflows"          # no exclusions at all
```

- seed: `wf_0001: seeded (AMP)`, `wf_0002 (E1)`, `wf_0003 (AMP)`, `wf_0004 (E1)`, `wf_0005 (E1)`,
  `wf_0006 (AMP)`, `wf_0007 (E1)` — seven.
- pass 1: all seven `parse=PARSED intake=WAITING_FOR_ANSWERS`, exit 0.
- answers: `wf_0001: 3`, `wf_0002: 3`, `wf_0003: 2`, `wf_0004: 3`, `wf_0005: 2`, `wf_0006: 2`,
  **`wf_0007: 4 answered, 0 unanswered`** (all `0 unanswered`).
- pass 2 (exit 0; plus `wf_0005: target_check: unknown nodes …` / `tier T3 — translation stays manual`):

```
wf_0001  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0002  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0003  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0004  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0005  parse=PARSED intake=READY analyze=DONE translate=MANUAL
wf_0006  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0007  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
```

- pass 3: the raw `diff -rq` (nothing excluded) lists 7 lines, one `manifest.json` per workflow; each
  of those diffs is exactly the `updated_at` line (2 diff lines, 0 others per workflow).

### Terminal states (read back from the seven committed `manifest.json`)

| workflow | tier | parse | intake | analyze | golden | translate | output_kind | segments | chain (`validation_workflow.json`) |
|---|---|---|---|---|---|---|---|---|---|
| wf_0001 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | procedures | seg_01: PASS | PASS ×4 sets, idempotent |
| wf_0002 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | procedures | seg_01/02/03: PASS | PASS ×4, idempotent |
| wf_0003 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | procedures | seg_01/02: PASS | PASS ×4, idempotent |
| wf_0004 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | procedures | seg_01/02/03: PASS | PASS ×4, idempotent |
| wf_0005 | T3 | PARSED (2 attempts) | READY | DONE | — | **MANUAL** | procedures | — | none (T3) |
| wf_0006 | T2 | PARSED | READY | DONE | DONE | **VALIDATED** | procedures | seg_01/02/03: PASS | PASS ×4, idempotent |
| wf_0007 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | **dbt** | seg_01/02: PASS (`"target": "dbt"`) | PASS ×4, idempotent, `"target": "dbt"` |

wf_0007's `dbt/` holds `dbt_project.yml`, `profiles.yml`, `README.md`, `translation_notes.md`,
`models/{sources,schema}.yml`, `models/{wf0007_seg_01_out,region_attainment,attainment_history}.sql`
— every one byte-equal to `samples/wf_0007/canned/dbt/…` — plus `compile_check.json`
(`{"status": "OK", "target": "dbt", "errors": [], "statements": 0, "models": 3}`) and `review.json`
(PASS). `procs/README.md` is `dbtReadme("wf_0007")`; there is no `procs/master.sql`, no `proc.sql`,
no `proc.py`. `docs/migration.md` is byte-equal to the canned one.

## Step 3 — classification, old (committed `5e3d8a9`) vs new (`run1`, fresh)

`classify.py old/workflows run1/workflows`: every file compared; JSON key by key; each SQL `proc.sql`
mapped from the LET form back to the old form (drop the `-- contract C4:` comment, the LET block and
the blank line after it; put each LET's expression, arguments colon-prefixed, back inside its
`IDENTIFIER(…)`) and compared byte for byte; each `master.sql` compared with its two `$$` lines
removed; `.duckdb`, `audit.jsonl`, `dbt/logs/`, `dbt/target/` excluded.

`files: old 409, new 542; only-old 0, only-new 133; both 409`

| kind | files | detail |
|---|---|---|
| **(a)** new tree `wf_0007/` | 97 | the dbt project (above); no `dbt/logs/`, `dbt/target/`, `dbt_sandbox_*.duckdb` copied |
| **(b)** new `validation_workflow.json` + `validation_workflow.<set>.json` | 25 | 5 per VALIDATED procedures workflow (wf_0001–0004, wf_0006) |
| **(b)** new `segments/batches.json` | 6 | wf_0001–0006 (wf_0007's is inside (a)); one batch each |
| **(b)** new `segments/seams.json` | 5 | every non-T3 old workflow (wf_0001–0004, wf_0006), `"ok": true` |
| **(c)** `docs/migration.md` | 5 | wf_0001–0004 gain exactly the canned `## Deployment` section; all five get C4V's prose (the `IDENTIFIER(:SRC_DB \|\| …)` sentence → `LET <LOGICAL>_SRC VARCHAR := SRC_DB \|\| …` + `IDENTIFIER(:<LOGICAL>_SRC)`, colon-free inside the LET). Each file byte-equal to `samples/<wf>/canned/docs/migration.md` |
| **(c)** `translation_notes.md` (canned doc, C4V prose) | 3 | wf_0001/seg_01, wf_0003/seg_02, wf_0004/seg_03 — only their C4V sentences; each byte-equal to the canned file |
| **(d)** `proc.sql`: `IDENTIFIER(expr)` → `LET` + `IDENTIFIER(:var)`, nothing else | 10 | every SQL segment of wf_0001–0004 and wf_0006 seg_01/03 maps back to the old text exactly; wf_0006/seg_02 (Snowpark) unchanged |
| **(e)** `procs/master.sql`: `$$` around the body, nothing else | 5 | wf_0001–0004, wf_0006 |
| **(f)** `compile_check.json` keys added | **0** | no `compile_check.json` changed at all — the new checks add no key |
| **(g)** `runtime_ms` | 55 | `validation*.json` (each validator's own wall clock) |
| **(g)** `updated_at` | 6 | `manifest.json` of wf_0001–0006 |
| **(h)** `segments/segmentation.json`: key added `params.max_prompt_chars = 60000` (Task W3) | 6 | wf_0001–0006; nothing else in the file |
| **anything else (regression)** | **0** | |

No `validation*.json` verdict or non-volatile key, no `checks` key, no manifest field other than
`updated_at`, no golden CSV/schema, no `contract.json`/`review.json`/`dag.json`/`order.json`/
`targets.json` changed; no file removed. (The first run's classification was identical except
`runtime_ms` in 54 files instead of 55 — whether a wall clock happens to repeat a millisecond value.)

Hygiene over `run1/workflows` (logs and sandboxes excluded): no login name, no `C:\Users` /
`C:/Users` / `/c/Users`, no `scratchpad`, no `temp\claude`; `confirmed_by: automation` is the only
value (19 occurrences across the seven `intake/mappings.yaml`).

## Step 4 — copy and check

```bash
tar -C "$SCRATCH/workflows" -cf - --exclude=logs --exclude=target --exclude='dbt_sandbox_*' \
    --exclude='*.duckdb' --exclude='*.duckdb.wal' --exclude=audit.jsonl --exclude=__pycache__ . \
  | tar -C workflows -xf -
```

- `find workflows -type f` → 542 (= the classified new count, so nothing legitimate was excluded);
  0 `*.duckdb*` / `audit.jsonl` / `logs` / `target` / `dbt_sandbox_*` / `__pycache__`.
- `diff -rq -x logs -x '*.duckdb' -x '*.duckdb.wal' -x audit.jsonl <run1>/workflows workflows` → exit 0.
- `git status --porcelain workflows`: 90 ` M`, 37 `??` (133 new files; `wf_0007/` shows as one
  entry), **0 deletions**. 90 = (c) 8 + (d) 10 + (e) 5 + (g) 55 + 6 + (h) 6.
- `git ls-files --others --exclude-standard workflows` → 133 (every new file), piped to
  `git check-ignore --stdin -v` → nothing printed (exit 1); `git ls-files --others --ignored
  --exclude-standard workflows` → nothing.
- Commit `b8c340d`: 223 files changed (90 + 133).

## Step 1 — tests, RED first (commit `8e8558d`), then GREEN

`tests/test_committed_workflows.py` (44 → 69 tests):
- `EXPECTED_TERMINAL["wf_0007"] = "VALIDATED"`; `DBT_WORKFLOW_IDS = ["wf_0007"]`; the module docstring
  says seven (Task 6B's six, then Task G's seven); the stale "`dbt` … no committed sample uses it yet"
  comment and one "these six workflows" docstring updated.
- `test_a_committed_dbt_workflow_is_a_project_with_a_readme_and_no_procedures` — `output_kind` and
  `output_target` `dbt`; the five `dbt_project.PROJECT_FILES` + `translation_notes.md`;
  `compile_check.json` `target: dbt`, `status: OK`; `review.json` PASS; `profiles.yml ==
  PROFILES_TEMPLATE` (added: the fixed template); `procs/README.md` equal to a Python mirror of
  `stages.ts`'s `dbtReadme` AND containing the literal §4.3 command; no `master.sql`, no
  `segments/*/proc.sql` or `proc.py`; every segment's `validation.json` has `"target": "dbt"` and a
  passing verdict; `segment_status` covers exactly the segments.
- `test_a_committed_sql_segment_s_validation_report_carries_no_target_key` — now filters out
  `manifest.output_kind == "dbt"` workflows (a filter, not a skip).
- `test_every_validated_workflow_has_a_passing_chain_report` — `validation_workflow.json` exists,
  `workflow` matches, verdict PASS*, `first_divergence` and `divergence_kind` null, `idempotent is
  True`, its `sets` equal `manifest.golden_sets`, one `validation_workflow.<set>.json` per set (each
  PASS*, no divergence) and no other set file; `target` is `dbt` for the dbt workflow and absent
  otherwise (added: the on-disk distinction between `validate_dbt.py` and `validate_workflow.py`).
- `test_every_committed_workflow_has_a_batch_plan_and_translated_ones_have_clean_seams` — one batch
  holding exactly `order.json`'s segments; every non-T3 `seams.json` `ok: true` with every seam `ok`.
- `test_a_committed_master_procedure_quotes_its_scripting_body` (added, ruling (e)) — every procedures
  workflow's `master.sql` has `\nAS\n$$\nBEGIN\n`, ends `END;\n$$;\n`, and calls every segment.
- `test_no_duckdb_or_audit_log_files_are_tracked` — also no tracked `/dbt/logs/`, `/dbt/target/`,
  `dbt_sandbox_`.

`tests/test_documented_identifier_form.py`: `"workflows/"` removed from `EXCLUDED_PREFIXES` and its
comment rewritten; `ALLOWED` / `ALLOWED_COLON_LETS` unchanged.

RED against the OLD tree (before the copy):
```
$ .venv/Scripts/python.exe -m pytest tests/test_committed_workflows.py
25 failed, 44 passed
  7 x [wf_0007]: terminal state, golden/document, segments, targets.json, output_kind, contracts, the dbt test
  6 x test_every_validated_workflow_has_a_passing_chain_report       (FileNotFoundError: validation_workflow.json)
  7 x test_every_committed_workflow_has_a_batch_plan_...             (FileNotFoundError: batches.json)
  5 x test_a_committed_master_procedure_quotes_its_scripting_body    (no "\nAS\n$$\nBEGIN\n")
$ .venv/Scripts/python.exe -m pytest tests/test_documented_identifier_form.py
1 failed, 5 passed — test_no_tracked_file_shows_an_expression_inside_identifier: 27 offenders, all under
workflows/ (10 proc.sql, 5 docs/migration.md, 1 translation_notes.md files); 0 colon-in-LET offenders
```
(`test_no_duckdb_or_audit_log_files_are_tracked` passed on the old tree too: nothing dbt-related was
tracked there. It pins that the copy keeps it so.)

GREEN after the copy:
```
$ .venv/Scripts/python.exe -m pytest tests/test_committed_workflows.py tests/test_documented_identifier_form.py tests/test_segment_prompt_size.py
92 passed        (69 + 6 + 17)
```

### `tests/test_deploy.py` (commit `40ed89d`)

Both module fixtures now copy the committed tree instead of rebuilding it (simpler and faster: no dbt
run, no chain re-run inside the fixture):
- `validated_wf_0007` was built by hand (`prepare_workflow` + `validate_dbt` + a hand-set manifest +
  a hand-written `procs/README.md`) "because Task G has not committed wf_0007 yet". It now copies
  `workflows/wf_0007` and asserts `output_kind: dbt`, `translate: VALIDATED`, a PASS
  `validation_workflow.json` and `procs/README.md`. The Python `_dbt_readme` mirror moved to
  `test_committed_workflows.py` (one copy in the Python suite), where it is compared to the committed
  file.
- `validated_wf_0006` re-ran `validate_workflow` on its copy ("the committed tree predates W1"); the
  committed tree now carries the chain report, so it asserts that report is PASS instead. (A small
  step beyond the instruction, for a docstring that had become false; say if it should be reverted.)
- Unused imports (`validate_dbt`, `validate_workflow`, `prepare_workflow`) removed. Still 13 tests,
  `13 passed`.

## Step 5 — reproducibility from an independent `git archive` export

```bash
S2="<scratch root outside the repo>/p2-G/run2"
git archive HEAD | tar -x -C "$S2"          # HEAD = 40ed89d (workflows + tests committed)
rm -rf "$S2/workflows"                      # rebuild from nothing, not from the committed product
printf '{"python": "%s", "samplesDir": "%s/samples"}' "$PY" "$S2" > "$S2/orchestrator.config.json"
cd "$S2"                                    # the EXPORT's own orchestrate.ts, scripts/, samples/, mappings/, catalog/
"$PY" scripts/dev/build_samples.py seed --root "$S2" --samples "$S2/samples"
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$S2" --runner mock --no-interactive
"$PY" scripts/dev/answer_samples.py --root "$S2" --samples "$S2/samples"
"$FNM" exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root "$S2" --runner mock --no-interactive
# then, from the worktree:
diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' -x logs workflows "$S2/workflows"
```

The export runs its OWN `orchestrate.ts` (the orchestrator imports `@github/copilot-sdk` only as types
and through a dynamic import on the Copilot path, so `--runner mock` needs no `node_modules`).
`scripts/parsers/ext/` in the export holds only `README.md` and `__init__.py`, so wf_0005's
parser-recovery path really ran (`parse.attempts: 2` reproduced). Same terminal states as run 1;
answers `wf_0007: 4 answered, 0 unanswered`.

**Raw diff:** 105 `… differ` lines, **0 `Only in`** lines:

```
Files workflows/wf_0001/manifest.json and <scratch>/run2/workflows/wf_0001/manifest.json differ
Files workflows/wf_0001/segments/seg_01/validation.edge.json and <scratch>/run2/workflows/wf_0001/segments/seg_01/validation.edge.json differ
…
   7 manifest.json
  14 validation.json               54 validation.<set>.json
   6 validation_workflow.json      24 validation_workflow.<set>.json
```

Key by key over all 542 files (`classify.py … repro`):
```
files: old 542, new 542; only-old 0, only-new 0; both 542
--- (g) value changed: runtime_ms  (98 files)
--- (g) value changed: updated_at  (7 files)
=== REGRESSIONS: 0
```

**Normalised diff** (copies of both trees; `"runtime_ms": <n>` → `0`, `"updated_at": "<…>"` →
`"<normalised>"`; 214 files rewritten, 107 per side):
```
$ diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' -x logs norm/a/workflows norm/b/workflows
DIFF_EXIT=0   (no output)
```

All 542 committed files reproduce byte for byte from a clean export except `runtime_ms` (98 files)
and `updated_at` (7 files). Both counts are in the final commit's body.

## Step 6 — README (commit `de8fbe3`)

§6 only, plus the repo-map sample line in §10 ("the sample table"; P4's brief touches §1/§7/§8 only):
- "The **seven** sample workflows under `workflows/`"; "in all **seven** trees"; "None of the
  **seven** committed workflows needed [the fixer loop]".
- the "Every stage writes into `workflows/<id>/`" paragraph names the new writers and files
  (`target_check.py`, `plan_batches.py`, `check_seams.py` → `targets.json`, `batches.json`,
  `seams.json`; `validate_snowpark.py`/`validate_dbt.py`; `validate_workflow.py` →
  `validation_workflow*.json`; the orchestrator → `procs/master.sql` or `procs/README.md`).
- terminal-states row `| wf_0007 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | seg_01/02: PASS (one dbt project) |`.
- paragraph "**The worked dbt example is `workflows/wf_0007/dbt/`.**" — why it is dbt, each file
  (project, fixed profile, sources, the work-stream `table` model, the two aliased target models with
  the `merge` on `REGION, PERIOD`, `schema.yml`, `README.md`, `translation_notes.md`,
  `compile_check.json`, `review.json`), `validation.json` carrying `"target": "dbt"`,
  `validation_workflow.json`, `procs/README.md` in place of `master.sql` (never run:
  dbt-snowflake not installed), no `proc.sql`, by-products git-ignored.
- paragraph: every VALIDATED workflow carries `validation_workflow.json` (+ per set; `deploy.py`
  refuses without it); every workflow `segments/batches.json` (one batch); every workflow with
  contracts (all but T3 wf_0005) `segments/seams.json` `ok: true`.
- paragraph "**Reproducing the product.**" — the `git archive` export, the exact `diff -rq`
  command, only `runtime_ms`/`updated_at` differ, empty once normalised (P4's verification ladder
  step 1 can point here).
- §10: "fixtures for the **7** sample workflows".

## Step 7 — full suites (final tree)

| suite | command | result |
|---|---|---|
| pytest (whole) | `.venv/Scripts/python.exe -m pytest -rs` (from the worktree) | **1835 passed in 401.02s**, exit 0, **0 skipped** (`-rs` listed none) |
| node | `fnm exec --using=22 npm.cmd test` | `# tests 311 / # pass 311 / # fail 0 / # skipped 0` |
| tsc | `fnm exec --using=22 node.exe <main>/node_modules/typescript/bin/tsc --noEmit -p .` | clean, exit 0 |

Collection delta measured against a `git archive 5e3d8a9` export: `test_committed_workflows.py`
44 → 69 (+25), `test_segment_prompt_size.py` 16 → 17 (+1: its committed-sample test is parametrised
over every `workflows/*/segments/order.json`, so wf_0007 joins it and passes — W3's segmentation
reproduces wf_0007's committed segments), `test_deploy.py` 13 → 13, `test_documented_identifier_form.py`
6 → 6. 1809 + 26 = 1835 — exactly the measured total: this task's only count change is its own tests.

## Brief corrections

1. Step 3's allowed list is extended by the controller's rulings (a)–(g) and the Step-3 ruling (h);
   the brief's "a changed `proc.sql`" is allowed only as (d), verified by mapping each one back.
2. Step 3 (c) names only `docs/migration.md`; the three canned `translation_notes.md` changed by the
   same C4V prose rewrite are classified under (c) (each byte-equal to its canned source).
3. Step 5's "only `runtime_ms`/`updated_at` differ" is shown as a raw diff + key-level count + an
   empty normalised diff, as Task 6B did.
4. Brief Step 6's "sample table (wherever README lists samples)": the README has no separate sample
   table; the per-sample statements are §6's terminal-states table and prose counts and §10's
   repo-map line — all updated.

## Things P4, H and P5 must know

**P4 (hand-off guide, backlog, README pointers):**
- `workflows/wf_0007/` is committed now: `workflows/wf_0007/dbt/` (the project),
  `workflows/wf_0007/procs/README.md` (the deploy command), `workflows/wf_0007/validation_workflow.json`.
  README §1's dbt row ("the committed worked example is `workflows/wf_0007/` (§6)") is now true; I did
  not touch §1/§7/§8.
- Verification ladder step 1 ("offline mock run reproduces the committed `workflows/`") has an exact
  recipe now: README §6 "**Reproducing the product.**" (a `git archive` export, the §6 sequence, the
  `diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' -x logs` command; only `runtime_ms` /
  `updated_at` differ; empty once normalised). `-x logs` matters: a dbt run leaves `dbt/logs/`.
- An export runs its own `orchestrate.ts` under `--runner mock` without `node_modules`.
- README edits of mine: §6 and one line of §10 (repo map: 7 sample workflows) — none in §1/§7/§8.

**H (bounded live test):**
- The mock run's verdicts to compare a hosted-model run against are the committed trees: every
  VALIDATED workflow has per-segment `validation*.json` AND `validation_workflow*.json`, all PASS,
  idempotent. `runtime_ms` and `updated_at` always differ run to run; nothing else should for the
  deterministic scripts.
- Run it in a scratch root; never copy `dbt/logs/` (`validate_<set>.log`, `validate_normal_rerun.log`
  — this run's logs held no user path, but they are machine-local by-products), `dbt_sandbox_*.duckdb`
  or `audit.jsonl` (a live run writes one; it may hold paths) into the repo. The committed-tree scans
  would catch a path; `test_no_duckdb_or_audit_log_files_are_tracked` catches the files.
- MockRunner writes no `compactions` / `peakInputTokens` (W4); every committed `manifest.metrics`
  is `{}`.

**P5 (README diagrams, last):** the committed product now shows, per workflow: `segments/batches.json`
and `segments/seams.json` at analyze (before/after the analyzer), `validation_workflow*.json` as the
translate stage's VALIDATED gate, and for a dbt workflow `dbt/**` + `procs/README.md` instead of
per-segment procedures + `procs/master.sql` (whose body is now `$$`-quoted).

## Concerns

1. `runtime_ms` sits in 98+ committed files and makes a raw reproducibility `diff -rq` list ~105
   benign lines (pre-existing since Task 17; unchanged here).
2. The committed tree grew to 542 files under `workflows/` (+133).
3. Nothing here ran on real Snowflake, real Alteryx, dbt-snowflake or a hosted model; the README text
   I added says so for the dbt deploy command.
4. The `test_deploy.py` wf_0006 fixture change goes slightly beyond the instruction (wf_0007 only) —
   named above so a reviewer can judge it.

## Files changed

- `workflows/**` — 90 modified, 133 new (wf_0007's 97 + 36), 0 deleted.
- `tests/test_committed_workflows.py` (+25 tests), `tests/test_documented_identifier_form.py`
  (exclusion removed), `tests/test_deploy.py` (fixtures read the committed trees).
- `README.md` — §6 and the §10 repo-map sample line.
