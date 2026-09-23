# Task W2 report — seam check, batched analyzer, stitched analysis

Worktree `.worktrees/p2-W2`, branch `wt/p2-W2`, base `3baf4b8`, commit `eed25e8`. Status: DONE_WITH_CONCERNS
(one interpretation flagged under "Brief corrections"; the budget is uncalibrated, as documented).

## What was built

**`scripts/check_seams.py <wf> [--segments seg_01,seg_02] [--root .]`** (new). A seam is one work
stream crossing a cut: the producer contract's `outputs[]` entry (`kind: "work"`) and the consumer
contract's `inputs[]` entry carrying its `stream`. Checked, per seam, and every problem listed (never
only the first): same table and stream; exactly one producer of the table, the one the consumer's
`from` names, in an EARLIER wave; same column names in order (upper-cased, as `compare.py`); same type
FAMILY per column via `lib.types_map.type_family` — the function `compare.py` judges schemas with,
including its "both `other` → compare base types" fallback — so `NUMBER(19,2)`/`NUMBER(38,0)` agree and
`FLOAT`/`NUMBER(38,0)` do not; same nullability; same keys (order-insensitive, upper-cased). Two checks
beyond the brief's sketch (see "Design decisions"): a stream the segment sub-DAG (`segments/<seg>/dag.json`,
C7) shows crossing into a segment must be declared in that consumer's `inputs[]`, and the producer must
run in an earlier wave. Workflow-wide: no table written by two work outputs (`duplicates`). Writes
`segments/seams.json` in the brief's shape. `--segments` restricts to seams whose CONSUMER is listed
(producers must still exist; a duplicate is reported when any of its producers is listed). Exit 0 / 1
(first stderr line `seam-mismatch: <producer>-><consumer> <stream>`, then every mismatching seam and
its problems, then duplicates) / 2 (no `order.json` or an unknown `--segments` id: `parser.error`,
nothing written; a contract that is not JSON: traceback, exit 2, nothing written).

**`scripts/plan_batches.py <wf> [--budget-chars N] [--root .]`** (new), `DEFAULT_ANALYZER_BUDGET_CHARS =
60000`. The brief's greedy algorithm verbatim: `base = len(prompt_context.render_global(...))`, each
segment's size from `prompt_context`, consecutive waves while `current + wave <= budget`, a single wave
over budget gets its own batch plus a warning. Writes `segments/batches.json` in the brief's shape.
Exit 0 (warnings on stderr) / 2 (not parsed or segmented → `parser.error`, nothing written; budget ≤ 0
→ `parser.error`; crash → 2). No exit 1: planning has no domain failure.

**`scripts/stitch_analysis.py <wf> [--root .]`** (new). Reads `segments/order.json` + `batches.json`
and every `analysis/<batch>.md` / `analysis/<batch>.unsupported.json`; writes `analysis.md` (`# <wf>
analysis (stitched from N batches by scripts/stitch_analysis.py)`, then per batch `## batch_NN: segments
…` and the fragment verbatim) and `unsupported.json` (highest tier T1<T2<T3; every list-valued key —
`unsupported`, `unknown` — concatenated in batch order, each `tool_id` once, first entry wins). Refuses
with exit 1 and writes nothing: a segment in two batches, in no batch, not in `order.json`, batches
out of `order.json` order, a fragment missing / empty / not JSON / with no valid tier. Exit 2: no
`order.json` / `batches.json` (`parser.error`) or a crash (e.g. `batches.json` not JSON).

**`scripts/prompt_context.py`** (extended). `render(repo, wf, role, budget_chars, batch=None)`;
`render_global(repo, wf)`; `segment_detail_chars(repo, wf, seg)`; plus `segment_detail_sizes(repo, wf)`
(all segments in one pass). `--batch batch_NN` (analyzer only; intake → exit 2; unknown batch or no
`batches.json` → exit 2). The batch render is: header naming the batch and its segments; `### Workflow
map` (one line per segment, e.g. `- seg_03 [tools 4, 5] reads 3_1 from seg_02; writes output tool 5`);
`### Target proposal`; `### Producer contracts at this batch's input seams` (for every stream the batch
reads from a segment in an earlier batch: `- <stream> from <producer> (read by <consumers>): <that
producer's outputs[] entry as compact JSON>`); `### DAG detail for <batch>` (the DAG-summary lines of
the batch's tools only); `### Touchpoints for <batch>` (the batch's tools' touchpoints only). Every
section goes through the same `_section_block` (data sentence + fence), every workflow- or
contract-derived value through `_esc` (the JSON is dumped with `ensure_ascii=False` and then `_esc`-ed,
whose `\uXXXX` spellings are themselves valid JSON escapes), and the whole through the same
`_min_budget` / `_truncate_sections` (factored into `_fit`, shared with the unbatched render, which is
byte-identical to before). `dag_summary_lines` gained an optional `tool_ids` filter and now indexes
inbound edges once (it was O(nodes × edges); identical output, pinned by F's existing tests).

**Orchestrator.**
- `types.ts`: `AgentCtx.batch?: AnalyzerBatch`, `interface AnalyzerBatch { id; segments }`,
  `OrchestratorConfig.analyzerBudgetChars?: number`. `cli.ts` `DEFAULT_CONFIG.analyzerBudgetChars = 60000`;
  `orchestrator.config.json` the same key.
- `stages.ts` `stageAnalyze`: after `target_check.py`, `plan_batches.py [wf, --budget-chars,
  <analyzerBudgetChars>]` (any non-zero exit, or a malformed `batches.json` → `script-error`; warnings
  logged). One batch (or none) → today's single call, whose verify gains `check_seams.py [wf]` after
  `checkTargets` (`seamReason`: deletes `seams.json` first; exit 1 → `seam-mismatch: <p>-><c> <stream>`
  from the first mismatching seam, or `seam-mismatch: <p1>+<p2>->? <table>` for a duplicate nobody reads;
  exit 2, or an exit 1 that left no mismatch → `script-error`). More batches → `analyzeInBatches`.
- `analyzeInBatches`: empties `analysis/`; per batch, `prompt_context.py [wf, --role, analyzer, --batch,
  <id>, --budget-chars, <analyzerBudgetChars>]` (failure logged, the batch runs without context, as
  `inlineContext`), a task naming the batch, its segments and exactly the files it may write, and
  `runAgent(…, { batch: { id, segments } }, verify)`. Verify: `analysis/<id>.md` exists and
  `analysis/<id>.unsupported.json` carries a valid tier; then, unless that fragment or an earlier one
  said T3, every contract of the batch exists, `checkTargets(batch segments)` (same `target-mismatch` /
  `target-missing` reasons), and `check_seams.py [wf, --segments, <batch segments joined by ,>]`. One
  retry per batch (runAgent's existing RETRY_ONCE), then `escalate`. After all batches:
  `stitch_analysis.py [wf]` (1 → `domainFailure(…, "stitch", …)`, 2 → `scriptError`), then
  `finishAnalyze(env, m, undefined, segments)`.
- `finishAnalyze(env, m, verdict?, segments?)`, shared by both paths: tier from `unsupported.json`
  (T1/T2/T3 only); for the batched path, T3 → mirrored `output_kind`, else `checkTargets` over EVERY
  segment decides `output_kind` (a failure parks with its reason); then the old tail (lowered-target log
  lines, dbt-drop log, `output_kind`, `DONE`, T3 → `MANUAL` / `tier-T3`).
- `policy.ts`: `PolicyOptions.analyzerBatch`; with it the analyzer's lanes are exactly
  `workflows/<wf>/segments/(<ids>)/contract\.json$` and `workflows/<wf>/analysis/<id>\.(md|unsupported\.json)$`,
  ids lower-cased and escaped; an id failing `ID_PATTERN` opens nothing (fail-closed). Same machinery as
  D's `dbtProject`: rule 1a/1b (another workflow), `FORBIDDEN_WRITES`, `--root` denial all still apply
  first. Other roles unaffected.
- `hooks.ts`: `hooksFor(…, dbt?, batch?)` passes `analyzerBatch` to `decide`, records `"batch": "<id>"`
  on every audit line of a batched session (absent otherwise), and adds one `Batch …: segments … only.`
  line to `onSessionStart`'s context (only when batched). `runner.ts`: `CopilotRunner` passes
  `ctx.batch`; `MockRunner` with `ctx.batch` copies only that batch's canned contracts and writes
  `analysis/<id>.md` / `analysis/<id>.unsupported.json` from the canned `analysis.md` /
  `unsupported.json`, never the stitched files and never `wf.tier`.

**Docs.** `.github/agents/analyzer.agent.md`: Outputs bullet for a batched run, new Procedure step 7
"Seams and batches" (the seam definition verbatim from the brief, the checks, the retry and
`seam-mismatch: <producer>-><consumer> <stream>`, the batch instructions, what each call gets and may
write, the stitch), Rules line; both marked `<!-- amended: output targets phase 2 -->`.
`docs/reference/large-workflows.md`: intro updated; new §"Batched analysis and seams" (seams, where the
orchestrator runs it, when it batches — budget, estimate and what it leaves out, determinism, single
wave over budget —, one batch is today's single call + the sample pin with measured estimates, what each
call gets, what each batch may write and its verify, stitching, what this does not prove).

## Tests (all written first; RED below)

Python (52 new): `tests/test_check_seams.py` 18 — the brief's six (the committed-sample one
parametrised over wf_0002/3/4/6/7, the four sub-cases parametrised) plus
`test_a_width_or_precision_change_is_not_a_mismatch`, `test_every_mismatch_is_reported_not_just_the_first`,
`test_a_stream_the_dag_carries_but_no_contract_declares_is_a_mismatch`,
`test_a_producer_that_runs_after_its_consumer_is_a_mismatch`, `test_cli_usage_errors_write_nothing`.
`tests/test_plan_batches.py` 12 — the brief's five (the sample pin parametrised over all seven) plus
`test_cli`. `tests/test_stitch_analysis.py` 14 — the brief's five plus a six-case parametrised
`test_anything_but_every_segment_exactly_once_is_refused` (no batch, unknown segment, out of order, empty
fragment, missing unsupported, bad tier), `test_stitch_returns_what_it_wrote`,
`test_cli_usage_errors_write_nothing`, `test_a_fragment_that_is_not_json_is_refused`.
`tests/test_prompt_context.py` +7 — the brief's
`test_batch_context_carries_the_global_map_and_the_upstream_producer_contracts` plus: first batch has
no producer contracts; `render_global` is contained verbatim in every batch render; `segment_detail_chars`
equals the detail lines a batch render carries; fence + escaping hold in a batch render (an injected
annotation on a batch tool and an injected column name — U+2028, `\n`, a backtick run — in an upstream
contract); a small budget is honoured, deterministic and fence-balanced; the `--batch` CLI (0, unknown
batch 2, intake 2, no `batches.json` 2). `tests/test_agents_config.py` +1
`test_analyzer_documents_batches_and_seams`.

Node (+18, 256 → 274): `stages.test.ts` — the brief's four ("a small workflow keeps one analyzer call",
"a workflow over the budget is analysed batch by batch and stitched", "a seam mismatch parks analyze with
seam-mismatch after one analyzer retry", "a stitch failure parks analyze") plus: a seam mismatch in a batch
parks after that batch's retry and nothing is stitched; `check_seams.py` exit 2 → `script-error` (one
call and batched); a batch raising a target parks `target-mismatch` with that batch's retry; a batched T3
workflow needs no contracts and ends MANUAL; a batched analyze starts from an empty `analysis/`;
`plan_batches.py` exit 2 is a script error before any analyzer; the budget comes from the configuration;
a batch fragment without a rankable tier is retried then parks. The pinned SQL sequence test was renamed
and gained exactly two entries (`plan_batches.py` after `target_check.py`, `check_seams.py` after the
analyzer's `prompt_context.py`); the task-text scan test gained the `batched` scenario. `policy.test.ts`
+4 (the brief's probe list, extended with `seg_010`, `dag.json`, `seams.json`, `batches.json`,
`batch_01.md.bak` and two other-workflow paths; several segments; malformed ids open nothing; other
roles unaffected). `runner.test.ts` +2 (the brief's MockRunner replay — plus "without a batch the
replay is today's" — and CopilotRunner passes the batch to the policy and the audit line).
Fakes: `plan_batches.py` one batch unless `batched` / `stitch-fails` (one per wave); `check_seams.py`
writes `seams.json`, exit 1 on `seam-mismatch:<seg>` when `<seg>` is in scope; `stitch_analysis.py`
concatenates the fragments with the highest tier, exit 1 on `stitch-fails` or a missing fragment;
`prompt_context.py` names the batch; `calls.tasks[].batch`.

### TDD evidence

RED (tests committed first as `6400e47`, before any implementation):

```
$ python -m pytest tests/test_check_seams.py tests/test_plan_batches.py tests/test_stitch_analysis.py ...
E   ModuleNotFoundError: No module named 'check_seams'      (likewise plan_batches, stitch_analysis)
$ python -m pytest tests/test_prompt_context.py tests/test_agents_config.py
FAILED …7 batch tests: TypeError: render() got an unexpected keyword argument 'batch' (5),
       AttributeError: module 'prompt_context' has no attribute 'render_global' (1), SystemExit: 2 (1)
FAILED tests/test_agents_config.py::test_analyzer_documents_batches_and_seams  (AssertionError: analysis/<batch>.md)
8 failed, 81 passed
$ npm test
# tests 273  # pass 256  # fail 17    (the 17 new/changed tests; baseline 256 still green)
```

The later fragment-tier test was shown RED by removing the one `if (!tier) return false;` line (expected
`NEEDS_HUMAN`, actual `DONE`), then restored.

GREEN: see Verification.

## Design decisions not spelled out in the brief

- **Two extra seam checks.** (a) *DAG crossings*: "every inter-segment stream" is defined by the parsed
  DAG, not by what the consumer chose to declare — a batch-2 analyzer that forgets an input would
  otherwise pass silently, which is exactly the lost-cross-chunk-context risk. Applied only where the
  sub-DAG exists (the brief's hand-built fixtures have none). (b) *Wave order*: a producer at or after its
  consumer's wave means the input does not exist when the consumer runs, and would also be invisible to a
  batch's producer-contract section. Before adding either I measured all five multi-segment samples'
  crossings against their canned declarations (identical) — `test_every_committed_sample_has_clean_seams`
  is green with both checks.
- **Table-mismatch diagnosis.** When no segment writes the consumer's table, the declared `from`
  segment's work output for the same stream is used as the producer (problem: "table X consumed, but
  seg_01 writes 2_T to Y"), so a wrong table is one named problem with a real producer in the reason,
  and the columns/keys are still compared. `producer` in `seams.json` is the actual writer, or — when none
  or several write the table — the declared `from` (documented in the docstring).
- **`segment_detail_chars` counts touchpoint lines too**, not only DAG-summary lines: a batch render
  carries its tools' touchpoints (the analyzer needs the resolved tables for `inputs[]`), so the estimate
  counts exactly the detail lines the render carries for that segment (pinned by
  `test_segment_detail_chars_is_what_a_batch_adds_for_that_segment`). `plan_batches` uses
  `segment_detail_sizes` (one pass over the files) instead of calling `segment_detail_chars` per segment:
  per-segment re-reading of a large `dag.json` would be quadratic on exactly the workflows this is for.
  Values are identical (the tests compute expectations with `segment_detail_chars`).
- **Section order in a batch render**: map, targets, producer contracts, DAG detail, touchpoints — the
  brief lists detail before contracts; contracts first means a truncation cuts the batch's own detail
  (re-readable from files) before the one thing a batch cannot rediscover.
- **Batch verify also runs `checkTargets` on the batch's segments**, so a raised target gets the per-batch
  retry with the same reason format as one call; `finishAnalyze` re-checks all segments to decide
  `output_kind`.
- **T3 in batches**: as the single call, a T3 workflow is never held to the contract bar — once a fragment
  says T3, that batch and every later one only need their fragments (a later batch's seams would read
  contracts a T3 batch never wrote). The workflow still gets a complete analysis.md.
- **Tier source.** `finishAnalyze` sets `m.tier` from `unsupported.json` on BOTH paths (the brief lists
  "tier from unsupported.json" in the shared tail, and `analyzer.agent.md` already claimed the
  orchestrator reads it from there). For the samples this changes nothing (MockRunner already set the same
  value); for a live analyzer that wrote one tier to the manifest and another to `unsupported.json`, the
  file now wins.
- **`analysis/` emptied at the start of a batched analyze**, so a fragment from an earlier plan (other
  ids, more batches) can never be verified or stitched. `seams.json` is deleted before each
  `check_seams.py` call for the same reason (W1's pattern for `validation_workflow.json`).
- **A fragment without a rankable tier fails that batch's verify** (retried once), so the stitch only
  ever sees usable fragments; the stitch still refuses a non-JSON fragment with exit 1 (model-written
  content it checks, like a bad tier) rather than 2.
- **`ctx.batch` is exactly `{ id, segments }`** — `batches.json`'s `waves`/`estimate_chars` are stripped.
- **`check_seams.py` is not added to any role's scripts.** The orchestrator runs it; the analyzer is told
  it will, and where the report is (`segments/seams.json`, readable) — on its retry it can read the
  mismatch that failed attempt 1.

## Brief corrections

None to the tests. One interpretation to flag: controller note 1 says "byte-identical script-call
sequence (pinned)", while the brief itself adds `plan_batches.py` (before the analyzer) and `check_seams.py`
(in verify) to every workflow's analyze. I kept the pinned SQL-sequence test otherwise byte-identical and
inserted exactly those two entries (renaming the test to say so), as Task F did for `prompt_context.py`.
If "byte-identical" was meant literally, the single-call path would have to skip both scripts, which
contradicts the brief's first stages test ("`check_seams.py [wf]` ran inside verify, `plan_batches.py …`
ran before the analyzer").

## Files changed

`scripts/check_seams.py` (new), `scripts/plan_batches.py` (new), `scripts/stitch_analysis.py` (new),
`scripts/prompt_context.py`, `orchestrator/{stages,policy,hooks,runner,types,cli}.ts`,
`orchestrator.config.json`, `orchestrator/test/{fakes,stages.test,policy.test,runner.test}.ts`,
`.github/agents/analyzer.agent.md`, `tests/test_check_seams.py` (new), `tests/test_plan_batches.py` (new),
`tests/test_stitch_analysis.py` (new), `tests/test_prompt_context.py`, `tests/test_agents_config.py`,
`docs/reference/large-workflows.md`.

## Self-review findings (fixed before reporting)

- `ctx.batch` first carried the whole `batches.json` entry (caught by the stages test) → now `{id, segments}`.
- The stitch's first version treated a non-JSON fragment as a crash (exit 2) → a refusal (exit 1); a
  `continue` there skipped the text list → restructured.
- The batch verify accepted a fragment with no tier (the fake stitch then silently defaulted to T1) → a
  rankable tier is now part of the batch's required output.
- Hygiene: no machine path, login name or scratch pointer in any changed file (grep over the diff).

## Things W4 and G must know

**W4 (compaction memory aid; runner/hooks/policy/stages + analyzer.agent.md + large-workflows.md):**
- `hooksFor(role, wf, env, segment?, dbt?, batch?)` — the sixth positional parameter is new; add W4's
  after it (or switch to an options object) and keep `batch` flowing from `CopilotRunner.run`
  (`ctx?.batch`). `onSessionStart`'s `additionalContext` now ends with an optional `Batch …` line.
- A batched analyzer's lane is ONLY its contracts and `analysis/<id>.{md,unsupported.json}`
  (`analyzerBatchLanes` in `policy.ts`, chosen before today's analyzer lanes). If W4 adds a
  `notes/analyzer.md` lane, add it to BOTH the unbatched lanes and `analyzerBatchLanes`, or a batched
  analyzer will be denied its notes file. Audit lines carry `"batch": "<id>"` in a batched session.
- Every batch is its own session: a compaction can happen per batch; the durable record of a batch is its
  contracts plus its fragment — the natural "re-read" targets for W4's nudge. `analysis/` is deleted at the
  start of a batched analyze (`analyzeInBatches`) — do not put notes under it.
- `analyzer.agent.md` step 7 is the batch/seam section (phase-2 marker present in Outputs and step 7).
  `large-workflows.md` §"Batched analysis and seams" sits between §"Segment size" and §"The chain test";
  its closing paragraph says a real context overflow is not detected by these scripts — W4's compaction
  events are the first thing that would observe it.
- Fakes: scenarios `batched` (one batch per wave), `stitch-fails`, `seam-mismatch:<seg>`; `calls.tasks[]`
  now has `batch`. `seamIntoSeg02(env, 1|2)` in `stages.test.ts` wraps `check_seams.py` for tests whose
  scenario string is already taken.

**G (offline run of all seven samples, committed `workflows/`):**
- Every sample is ONE batch under the default budget (estimates 1,120–2,690 characters; wf_0005 1,120),
  so the analyze stage runs one analyzer call as before, plus two script calls: `plan_batches.py <wf>
  --budget-chars 60000` and, in the verify callback, `check_seams.py <wf>` (not for T3 wf_0005, whose verify
  returns before contracts). Each committed T1/T2 workflow will newly carry `segments/batches.json` and
  `segments/seams.json` (`"ok": true`); wf_0005 carries `batches.json` only. Neither contains a path.
- `analysis.md` / `unsupported.json` for the samples are still the analyzer's (MockRunner's canned) files,
  not stitched; no `analysis/` folder appears.
- `m.tier` is now read from `unsupported.json` after the analyzer (same values as before for the samples).
- A pin that every committed workflow's `seams.json` is `ok` and `batches.json` has one batch would be
  cheap in `tests/test_committed_workflows.py`.

## Concerns

- The character budget is uncalibrated (as the doc says); the estimate leaves out the batch-only
  headings/fences, the producer contracts and the task text, so a batch near the budget renders slightly
  over its estimate and is truncated (detail first) by `prompt_context.py`.
- Nothing here has run against a real model: whether a real analyzer copies producer contracts faithfully
  in batch 2 is exactly what `check_seams.py` exists to catch, but only a live run will say how often.

## Verification (final, on the committed tree `eed25e8`)

```
$ fnm exec --using=22 npm.cmd test
# tests 274  # pass 274  # fail 0  # skipped 0          (baseline 256, +18; the real-Python integration run ran)
$ fnm exec --using=22 node.exe …/typescript/bin/tsc --noEmit -p .
(no output — clean)
$ python -m pytest tests/test_agents_config.py tests/test_prompt_context.py tests/test_committed_workflows.py
133 passed
$ python -m pytest                                        (whole suite)
1628 passed in 374.19s (0:06:14)                          (baseline 1576, +52; no skipped/warning/error/fail in the output)
```

(The whole-suite run was on the tree before two doc-only edits — a docstring in `prompt_context.py`, a
comment in `types.ts`, one sentence in `large-workflows.md`; tsc, node and the three pytest files above
were re-run after them.)

## Commits

`eed25e8` feat: deterministic seam check in analyze; the analyzer runs batch by batch above a character
budget and its fragments are stitched. The six `wip:` commits made along the way (`6400e47` RED tests,
`12d24cb` scripts, `a2cbcf7` orchestrator, `8919891` docs, `423a613` self-review fixes, one doc touch-up)
were squashed into it with `git reset --soft 3baf4b8`; the tree is identical (checked).

## Follow-up

Coordinator rulings after review (W2 approved). Commit `e0a21a9` wip: follow-up — deny Snowflake flags to
agents; master.sql body in $$; batched crash-resume test. Every new test was run RED first.

### (1) No agent points a script at a Snowflake account

`orchestrator/policy.ts`: `SCRIPT_BACKEND_FLAGS = ["backend", "connection", "sandbox-database"]` and
`isScriptBackendFlag(token)` — a `--name[=value]` token (any case) whose name is a non-empty prefix of
one of the three, i.e. every spelling argparse accepts (`--backend x`, `--backend=x`, `--back`, `--conn`,
`--sand`, `--b`, …; an ambiguous prefix is refused as well — fail-closed). Checked in `decideShell` for
every allow-listed script call, right after the `--root` rule, whatever the script (the flags do not
exist on this base except `validate_workflow.py --backend duckdb`, which no role may run): reason
`script-backend: <script> may not be pointed at a Snowflake account from an agent session`. No existing
flag of any script (`--batch`, `--budget-chars`, `--check`, `--contract`, `--set`, `--segments`, …) is a
prefix of the three names, so no documented call is affected.

Tests: `policy.test.ts` "script-backend: --backend, --connection and --sandbox-database are denied in
every spelling" (21 spellings × 7 role/script calls × two command shapes, exact reason) — RED before the
change (`not ok 1`); "script-backend: an ordinary script call is still allowed" (validate_segment /
validate_snowpark / validate_dbt / compare / compile_check `--target snowpark` / target_check / intake_prompt
/ parse `--check`, and `--set backend`, a value that only reads like the flag). `tests/test_agents_config.py`
`test_no_agent_file_shows_a_script_call_with_a_backend_flag` (mirrors the `--root` pin).

### (2) `procs/master.sql`'s body in `$$`

`masterSql` now emits `AS\n$$\nBEGIN … END;\n$$;\n` with the `-- Generated by …` header unchanged, the
shape of every segment's `proc.sql`. Pinned byte for byte by `stages.test.ts` "masterSql wraps its
Scripting body in $$ like every segment procedure" against `orchestrator/test/fixtures/master_wf_0003.sql`
(RED before the change); the two existing masterSql tests and the real-Python integration run still pass.
The committed `workflows/*/procs/master.sql` are unchanged (Task G refreshes them).

**Ruling correction (proc_runner).** The ruling asks that `lib/proc_runner.parse_proc` / `run_proc` "still
accept" the master. They never did, and cannot without weakening contract C4: (a) the old, undelimited
master is refused by `parse_proc` with `procedure … has no $$-quoted body` (its body is located only between
`$$`); (b) `_SCRIPTING_KEYWORDS` contains `CALL` on purpose — C4 keeps the local runner to plain SQL
statements and forbids one procedure calling another — and the master is the one procedure made of nothing
but `CALL`s; (c) `run_proc` executes statements on the local backend, which has no `MIG_WORK.<WF>_<SEG>`
procedures to CALL. Nothing in `scripts/` reads `master.sql` (grep). Smallest correction: the test pins what
is true and what the `$$` change is for — `tests/test_proc_runner.py::test_the_generated_master_is_a_dollar_quoted_procedure_the_runner_reads_and_refuses_to_run`
parses the SAME fixture file: the undelimited form fails at "no $$-quoted body"; the new form gets past body
location and is refused only by the C4 subset at its first `CALL`; `run_proc` refuses it before a single
statement reaches the backend. `proc_runner.py` is unchanged. If a consumer (P2's `deploy.py`?) needs to
parse the master's header, that is a new, header-only entry point — not a relaxation of C4.

### (3) Batched crash-resume

`stages.test.ts` "a crash after batch 1 of 3 resumes with a full batched re-run from batch_01": a three-wave
batched workflow (fakes: new `waves?: number` option, seg_03 canned like seg_01/02); the analyzer throws at
`batch_02`; on disk analyze is unfinished, `analysis/batch_01.md` survived, nothing was stitched. The plain
re-run: `analysis/` is empty when the first analyzer call starts (checked from inside the runner), the
analyzer runs `batch_01, batch_02, batch_03`, the script calls are exactly segment → target_check →
plan_batches → (prompt_context --batch, check_seams --segments) × 3 → stitch, and the workflow ends analyze
`DONE` (no reason, tier T1, stitched from 3 batches, three contracts) and translate `VALIDATED` with all three
segments PASS. RED before the fake supported three waves (`Missing expected rejection`). The full re-run is
kept; no mid-batch resume was added.

### Verification (follow-up)

```
$ npm test          # tests 278  # pass 278  # fail 0  # skipped 0   (was 274; +4)
$ tsc --noEmit -p . (clean)
$ pytest tests/test_agents_config.py tests/test_proc_runner.py   81 passed
$ pytest            (whole suite)  1630 passed in 364.03s (0:06:04)   (was 1628; +2; no skipped/warning/error/fail in the output)
```

### Things G and P2 must know (additions)

- **G**: regenerated `workflows/*/procs/master.sql` will differ from the committed ones by the `$$` line
  before `BEGIN` and the `$$;` line after `END;`, nothing else.
- **P2**: `--backend`, `--connection`, `--sandbox-database` (and their abbreviations) are denied in every
  agent script call; only the orchestrator/humans may pass them. `parse_proc` still refuses the master
  (C4 `CALL`), so a deploy path must run `master.sql` as DDL text, not through `proc_runner`.
