# Task 6A — fix round 1 (rulings from the task review of f9b30dc)

The reviewer verified: rank direction (sql highest) correct; exact reason strings; `escalate` precedence; contract re-read per iteration; render after every write; SQL script calls byte-identical; MockRunner never writes both artefacts, deterministic variant sort; ROLE_SCRIPTS and lanes; nine canned contracts; docs and agent frontmatter; RED-first. Five findings remain. Apply every ruling, RED-first where a test is named, then run node (expect 170 + new), tsc, and `tests/test_agents_config.py`.

## C1 — `target_check.py` exit 1 must reach the analyzer (spec §3.1)
`orchestrator/stages.ts:307-308` routes `!targets.ok` (exit 1 AND 2) to `scriptError`. RULING: only `targets.code === 2` is `script-error`; exit 1 (targets.json written, the workflow has `unknown` nodes) logs one line (`target_check: unknown nodes in <wf>; the analyzer decides`) and continues to the analyzer exactly as exit 0 does. Split the parameterised test at `orchestrator/test/stages.test.ts:689-701`: exit 2 keeps its assertions; exit 1 asserts the analyzer DID run and no `script-error` reason was recorded. Extend the fake `target_check.py` so a scenario can make it exit 1 while still writing `targets.json`.

## I1 — a contract lowered to `manual` never reaches the translator
`orchestrator/stages.ts:435` collapses any non-snowpark target to SQL. RULING: `migrateSegment` hard-stops when `contract.target === "manual"`: no translator dispatch, the segment's verdict is `NEEDS_HUMAN` with reason `manual-segment` (the same segment-level park path `script-error` uses), and the workflow's translate status follows the existing rules for a parked segment. The tier-T3 gate stays as it is (the analyzer still owns `unsupported.json`); this is the code-level guard at the point of harm. Test: a scenario whose seg_02 contract says `manual` → translator never called for seg_02, segment parked with that reason, seg_01 still migrates.

## I2 — a failure before review must be told to the fixer
`orchestrator/stages.ts:440-446`: after a render exit 1 or a compile-check failure, the next fixer task text points at `validation.json` / `review.json`, which do not exist yet, and never carries the diagnosis. RULING: when the previous iteration ended in render exit 1 or a compile failure (either target), the fixer task text gains one sentence naming the script and the trimmed diagnosis, e.g. `The previous attempt failed before review: scripts/render_snowpark.py said: <stderr trimmed>` or `… scripts/compile_check.py failed; read compile_check.json.` Bound the quoted text to 500 characters and pass it through the same redaction `runner.ts` applies to `AgentResult.detail` (export/reuse that helper; do not duplicate it). Script CALLS for SQL segments stay byte-identical (the pinned test must still pass); only the task text changes. Test: render exit 1 on iteration 1 → iteration 2's fixer task text contains the script name and the stderr text; a compile failure → the text names `compile_check.json`.

## M1 — translator §4.2 copy must be verbatim
`.github/agents/translator.agent.md:44` dropped the parenthetical `(checked by compile_check.py --target snowpark, §5.1)`. Restore it so the Rules bullet list is byte-identical to the spec's (diff it).

## M2 — no third reason format
`orchestrator/stages.ts:279`: `target-mismatch: <seg> has no proposal in targets.json` is not one of the spec's two formats. RULING: a segment with no proposal (missing/unreadable `targets.json`, or a contract naming a segment `targets.json` does not list) parks with `target-missing: <seg>`; the detail goes to the log line only. Adjust the test that pins the old wording if one exists.

## Report
Append "## Fix round 1" to `task-6a-report.md` (commit, counts, concerns). Commit as `wip: fix round 1 (C1, I1, I2, M1, M2) — <what>`. Do not touch `workflows/`, `tests/test_committed_workflows.py`, or `samples/`. No subagents. Never git stash / checkout -- / reset --hard.
