# Task 15 Step 5 — Orchestrator Integration

Worktree: `.worktrees/task-15-int`, branch `wt/task-15-int`, based on `e0564ac`, merged with
`feat/pipeline-m0-m2` (`f34d4de`, Task 13b's `samples/wf_0003..wf_0005/canned/**` and
`tests/test_canned_artifacts.py`) at `c97147b`.

**Status: DONE.**

## Commits

```
a33638c wip: wf_0005 parser-recovery artifacts and the tests that run them        (13b, pre-existing)
f34d4de feat: hand-migrated procedures, contracts, broken variants and canned outputs for wf_0003-wf_0005  (13b, pre-existing)
7cf0c9e wip: answer_samples.py + needs_human-wins-over-verdict orchestrator fix
e867e5d wip: real end-to-end integration test for wf_0001
c97147b merge: feat/pipeline-m0-m2 (Task 13b canned artifacts for wf_0003-wf_0005)
93de6e7 wip: tier T3 short-circuits analyze before the per-segment contract check
cb6b49c wip: a present-but-unparsable orchestrator.config.json is a usage error
4f868e5 wip: a T3 workflow at MANUAL stays that way on a later run (golden was not gated)
1f74fe9 feat: offline end-to-end run — answer_samples, integration test, orchestrator fixes
```

The final commit is an empty marker with the required message, not a squash: squashing past the
merge commit would either discard the record of merging Task 13b's independently-authored work or
fold it into this task's own diff, neither of which is right for a two-source branch. Every wip
commit carries its own RED/GREEN evidence in its message.

## Test counts

- Node: **85/85 passing**, ~4.4s total — `fnm exec --using=22 node.exe --experimental-strip-types
  --test orchestrator/test/*.test.ts`. Includes the real-Python `integration.test.ts`
  (~4.05s of that total on its own — parse, intake, segment, simulate, compile-check and
  validate-segment all run for real against DuckDB, twice, plus a no-op third pass).
- `tsc --noEmit -p .`: clean.
- Pytest: **956/956 passing**, ~72s, no skips, no warnings (936 baseline this task started from +
  9 new `test_answer_samples.py` cases + 11 new from 13b's `test_canned_artifacts.py` additions
  brought in by the merge).
- No `workflows/` directory ever appeared in this worktree's git tree (`git status --porcelain`
  clean throughout; every real run happened in scratch roots under the session's scratchpad
  directory).

## Five-row terminal-state table (fresh scratch root, all five samples together)

Sequence: seed all five → run 1 (parks) → `answer_samples.py` (bulk) → run 2 (terminal) → run 3
(no-op). Full command transcript below the table.

| workflow | tier | parse | intake | analyze | golden | translate | document | pr | segments |
|---|---|---|---|---|---|---|---|---|---|
| wf_0001 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | DONE | unset (no gh) | seg_01: PASS |
| wf_0002 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | DONE | unset (no gh) | seg_01/02/03: PASS |
| wf_0003 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | DONE | unset (no gh) | seg_01/02: PASS |
| wf_0004 | T1 | PARSED | READY | DONE | DONE | **VALIDATED** | DONE | unset (no gh) | seg_01/02/03: PASS |
| wf_0005 | T3 | PARSED | READY | DONE | *(never runs — T3)* | **MANUAL** | *(never runs — T3)* | *(never runs — T3)* | none (no contracts by design) |

wf_0005's manifest also carries `reasons.translate = "tier-T3"` and `parse.attempts = 2`
(one failed real parse, one parser-recovery round, one successful re-parse — well within
`maxParseRecovery: 2`). wf_0001–0004: `golden_sets = [normal, period_end, empty, edge]`,
`docs/migration.md` and `procs/master.sql` written.

**Idempotency (run 3):** exit 0, identical file set for all five workflows (322 files), every file
byte-identical except each workflow's own `manifest.json`, which differs only in `updated_at`
(the state machine's `finally` block always re-stamps and re-saves the manifest even when no
stage actually ran). Confirmed with `stat` mtimes and a full JSON diff ignoring `updated_at`. Before
the fix below, run 3 was **not** a no-op for wf_0005 — see Defect 3.

### Command transcript (fresh scratch root, redacted paths)

```
$ python scripts/dev/build_samples.py seed --root <scratch>
wf_0001: seeded (AMP)   wf_0002: seeded (E1)   wf_0003: seeded (AMP)
wf_0004: seeded (E1)    wf_0005: seeded (E1)

$ node --experimental-strip-types orchestrate.ts --root <scratch> --runner mock --no-interactive
orchestrate: ... workflows=5
wf_0001..wf_0005: waiting for answers — gh not installed, answer the boxes in .../open_questions.md
wf_0001  parse=PARSED intake=WAITING_FOR_ANSWERS
wf_0002  parse=PARSED intake=WAITING_FOR_ANSWERS
wf_0003  parse=PARSED intake=WAITING_FOR_ANSWERS
wf_0004  parse=PARSED intake=WAITING_FOR_ANSWERS
wf_0005  parse=PARSED intake=WAITING_FOR_ANSWERS
(exit 0)

$ python scripts/dev/answer_samples.py --root <scratch>
wf_0001: 3 answered, 0 unanswered   wf_0002: 3 answered, 0 unanswered
wf_0003: 2 answered, 0 unanswered   wf_0004: 3 answered, 0 unanswered
wf_0005: 2 answered, 0 unanswered
(exit 0)

$ node --experimental-strip-types orchestrate.ts --root <scratch> --runner mock --no-interactive
wf_0005: tier T3 — translation stays manual (see unsupported.json)
wf_0001  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0002  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0003  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0004  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0005  parse=PARSED intake=READY analyze=DONE translate=MANUAL
(exit 0)

$ node --experimental-strip-types orchestrate.ts --root <scratch> --runner mock --no-interactive
wf_0001  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0002  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0003  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0004  parse=PARSED intake=READY analyze=DONE golden=DONE translate=VALIDATED document=DONE
wf_0005  parse=PARSED intake=READY analyze=DONE translate=MANUAL
(exit 0 — no-op)
```

## wf_0005 parser-recovery replay — verified for real, not a mock shortcut

Ran in an isolated single-workflow scratch root, with `scripts/parsers/ext/acme_dedupe.py` and
`tests/parser_corpus/acme_dedupe/**` confirmed **absent** before the run:

1. `parse.py --check` (real): `INVARIANT_VIOLATION` — `unknown tools without a behavior: 1 of 4
   data nodes (max 10%): ['2']`.
2. `parser-recovery` agent (MockRunner): copies `samples/wf_0005/canned/parser-recovery/**` — the
   canned source tree has exactly three files/subtrees: `parsed/parse_diagnosis.md` →
   `workflows/wf_0005/parsed/`, `scripts/parsers/ext/acme_dedupe.py` → `scripts/parsers/ext/`,
   `tests/parser_corpus/acme_dedupe/{README.md,fragment.yxmd,test_acme_dedupe.py}` →
   `tests/parser_corpus/`. These are exactly the three roots the policy allows for this role, and
   nothing else — confirmed by listing the canned source tree itself, since the replay is a
   verbatim copy of it.
3. `parse.py --check` re-run (real, second attempt): `RECOVERED`, clean.
4. **Direct proof the extension is what fixed it** (not the mock, not a coincidence): with the
   extension in place, `python scripts/parse.py wf_0005 --check` → `RECOVERED`/`PARSED`, exit 0.
   Moved `scripts/parsers/ext/acme_dedupe.py` aside and re-ran the identical command →
   `INVARIANT_VIOLATION`, exit 1, same "unknown tools" error as the very first attempt. Restored
   the file and re-ran → `PARSED`, exit 0 again.
5. `manifest.json.parse = {"attempts": 2, "extensions": []}` — one real failure, one recovery
   round, one real success. `maxParseRecovery: 2` was never exhausted (it only needed 1 round).

## Defects found this session (with file:line, evidence, fix)

### 1. `orchestrator/stages.ts` — `needs_human` ignored when verdict started with PASS
**Found in the first pass of this task**, before wf_0005 work began.
Before: `migrateSegment` checked `verdict.startsWith("PASS")` and returned before ever looking at
`validation.needs_human` — a `PASS_WITH_ACCEPTED_DIFF` report that also said `needs_human: true`
would be marked validated (coordinator ruling 4 in the task-15-int brief).
RED: new test `"needs_human wins even when the verdict itself starts with PASS"`
(`orchestrator/test/stages.test.ts`), backed by a new `needs-human-pass:<seg>` scenario in
`fakes.ts`, failed — expected `NEEDS_HUMAN`, got `VALIDATED`.
Fix: `orchestrator/stages.ts:344` now checks `validation.needs_human` **before**
`verdict.startsWith("PASS")` (line 348).

### 2. `scripts/dev/answer_samples.py` — missing `sys.path` bootstrap
Every other `scripts/dev/*.py` (`build_samples.py`, `alteryx_sim.py`) has
`if __package__ in (None, ""): sys.path.insert(0, ...)`, because Python only auto-adds a script's
*own* directory to `sys.path`, not its parent. I forgot it when writing this new script. Pytest
never caught it (its own `pythonpath = ["scripts", "."]` masks the gap); the real subprocess call
inside `integration.test.ts` did: `ModuleNotFoundError: No module named 'lib'`.
Fix: `scripts/dev/answer_samples.py:44-45`, matching the established pattern.

### 3. `orchestrator/stages.ts::stageAnalyze` — tier decision came after the per-segment contract check
**13b's reported defect, confirmed.** `stageAnalyze`'s `runAgent` verify callback required a
`contract.json` for every segment in `order.json` before ever checking tier. A T3 workflow is
never assigned per-segment contracts at all (`samples/wf_0005/canned/` has no `segments/` tree),
so it would escalate to `NEEDS_HUMAN` at analyze instead of reaching `MANUAL`.
`docs/spec/01-copilot-setup.md` §3 confirms the ruling: the state diagram draws
`analyze --> MANUAL: tier T3` as a **direct** transition, entirely bypassing
`analyze --> golden: contracts written`; its own `migrateWorkflow` pseudocode sets
`status.analyze = "DONE"` unconditionally right after the analyzer runs and checks tier only
afterward — there is no "verify contracts" step in the spec's skeleton at all.
RED: `orchestrator/test/fakes.ts`'s wf_0005 fixture was unrealistically writing a canned
`segments/seg_01/contract.json` even for the T3 case (masking the bug). Removed it for T3, matching
the real sample. With that fixture fix alone, the existing test
`"a recovered T3 workflow ends MANUAL without translating"` failed against the old `stages.ts`
(`m.status.translate` was `undefined`, not `"MANUAL"`, because analyze escalated to `NEEDS_HUMAN`
first via a "missing-output" retry).
Fix: the verify callback (`orchestrator/stages.ts:231`) now reads `unsupported.json`'s own `tier`
field first and returns `true` immediately when it is `"T3"`, before ever checking for
`contract.json` files. The MANUAL branch (line 244) now also records
`reasons.translate = "tier-T3"` so a human reading the manifest knows why.
Verified against the real wf_0005 sample end to end (see table above and the recovery-replay
section).

### 4. `orchestrator/stages.ts::shouldRun` — a T3 workflow's `golden` stage was never gated (found by re-running the real wf_0005 three times)
**Not reported by 13b — found during this task's own verification of Defect 3's fix**, by literally
re-running the real wf_0005 in a scratch root a third time to check idempotency, per the brief's own
"re-run ALL FIVE... run 3 no-op" requirement. Run 1 parked correctly; run 2 correctly reached
`translate=MANUAL`; run 3 — which should have been a pure no-op — instead ran
`scripts/dev/alteryx_sim.py` and set `status.golden = "BLOCKED"` (reason `"no-golden-sets"`),
flipping the overall exit code from 0 to 1 (`golden: BLOCKED` is in `STATUS_NEEDS_A_HUMAN`).
Cause: `stageAnalyze`'s T3 branch returns `"stop"` the moment it sets `translate = "MANUAL"`, so on
the *first* pass the outer `STAGES` loop breaks before ever reaching `golden`'s `shouldRun` check.
But `golden`'s own status is never set to anything for a T3 workflow, so on a *second*
`migrateWorkflow` call — which restarts the loop from the top and skips `analyze` (already `DONE`)
— `shouldRun(m, "golden")` sees an unset status and looks exactly like a stage that simply hasn't
run yet. `docs/spec/01-copilot-setup.md`'s state diagram draws `MANUAL` as a dead end with no
outgoing edge at all: `golden`/`document`/`pr` never apply to a T3 workflow, on the first pass or
any later one.
RED: new test `"re-running a T3 workflow already at MANUAL does nothing (golden is never
applicable)"` (`orchestrator/test/stages.test.ts`) called `migrateWorkflow` twice for wf_0005 and
failed on the second call (`golden` was `"DONE"`, not `undefined` — even the fake's
always-succeeding `alteryx_sim.py` handler proved `golden` ran at all, which is the actual defect
regardless of its outcome).
Fix: `orchestrator/stages.ts:438-441` — `shouldRun` now refuses `golden`/`document`/`pr` outright
whenever `m.tier === "T3"`, before ever consulting `TERMINAL_GOOD`. Re-verified against the real
wf_0005 sample: a fresh three-run sequence now ends with run 3 fully clean (see the five-row table
above; `wf_0005.status.golden` stays unset across all three runs, `alteryx_sim.py` is never
invoked).

### 5. `orchestrator/cli.ts::loadConfig` — a present-but-unparsable config silently degraded to defaults
Found while hand-writing a scratch `orchestrator.config.json` via a bash heredoc, which silently
collapsed `\\` to `\`, producing invalid JSON. The old `loadConfig` used `readJsonOr`, which treats
"file absent" and "file present but unparsable" identically — both silently fall back to
`DEFAULT_CONFIG`. The corrupted config quietly degraded to the relative default
`.venv/Scripts/python.exe`, which then failed every `env.py()` spawn with `ENOENT`, which
`stageParse` misreported as a `parser-recovery missing-output` escalation. Nothing in that trail
mentioned "config" or "JSON" at all — a confusing trap for whoever hits it next.
RED: new test `"loadConfig treats a PRESENT but unparsable config file as a usage error, not a
silent default"` (`orchestrator/test/cli.test.ts`) wrote a syntactically-broken config and a
top-level JSON array, asserting `loadConfig` rejects with a `UsageError` naming the file; it failed
("Missing expected rejection") against the old `readJsonOr`-based `loadConfig`. Also reconfirms the
"absent file → defaults" contract is unchanged.
Fix: `orchestrator/cli.ts:120-139` (`readConfigFile`) reads and `JSON.parse`s the file itself.
`ENOENT` still falls back to `{}` (defaults); any other read failure, a JSON syntax error, or a
non-object/array top-level value throws `UsageError` (class at line 46), which `main()`'s existing
top-level `catch` already turns into exit 2 — no change needed there.

### Incidental, harmless, pre-existing (fixed while touching that line)
`orchestrator/test/fakes.ts` had a raw NUL byte embedded in a string literal in the
`validate_segment.py` fake (confirmed present in `e0564ac` via `git show`, i.e. before this task
touched the file). Never a functional bug — V8 accepts a literal NUL inside a string — but it made
the file look "binary" to `grep`. Replaced with an ordinary space.

## Command sequence for Task 17

```bash
# 1. Seed every sample workflow (repo root)
.venv/Scripts/python.exe scripts/dev/build_samples.py seed --root .

# 2. First pass: parks every workflow at WAITING_FOR_ANSWERS (wf_0005's parser-recovery replay
#    also happens in this same pass — no separate/extra pass is needed for it)
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root . --runner mock --no-interactive

# 3. Feed each sample's recorded answers into manifest.answers
.venv/Scripts/python.exe scripts/dev/answer_samples.py --root .

# 4. Second pass: reaches the terminal states (wf_0001-0004 VALIDATED, wf_0005 MANUAL)
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root . --runner mock --no-interactive

# 5. (optional) confirm idempotency — a third run should change nothing but each manifest's updated_at
fnm exec --using=22 node.exe --experimental-strip-types orchestrate.ts --root . --runner mock --no-interactive

git add workflows/
```

**wf_0005 needs no extra pass.** Its parser-recovery replay (parse fails → `parser-recovery` agent
→ re-parse succeeds) all happens automatically within step 2's single `migrateWorkflow` call, the
same as every other stage transition; step 4 then carries it from `READY` intake straight to
`MANUAL` in one more pass, exactly like the other four reach `VALIDATED`. Five workflows, two
orchestrator passes, one `answer_samples.py` call in between — no per-workflow special-casing
needed from the caller.

`npm test` (`fnm exec --using=22 npm.cmd test`) already includes the real wf_0001 end-to-end check
automatically (`orchestrator/test/integration.test.ts`, ~4.05s of the ~4.4s total node suite time);
it skips itself if `.venv/Scripts/python.exe` isn't found at either the current worktree's own path
or the shared checkout's fixed path.

## Notes for Task 16 (CopilotRunner — recorded, not touched)

- `onPermissionRequest: () => ({ kind: "approve-once" })` always approves; since `policy.decide`
  only ever returns `allow`/`deny` (never `ask`), it's unverified whether the live SDK ever calls
  this hook at all when `onPreToolUse` already decided. Worth confirming against a real session.
- Timeout classification (`orchestrator/runner.ts`, `CopilotRunner.run`'s catch block) matches
  `/timed?\s?out|timeout/i` against the raw thrown error's message; if the SDK's real timeout text
  doesn't match, a timeout gets classified as generic `"error"` instead of `"timeout"` — and
  `"error"` gets no retry while `"timeout"` gets one (`RETRY_ONCE` in `stages.ts`).
- `budgets.maxToolCallsPerWorkflow` is only checked between agent invocations (top of `runAgent`),
  never mid-session — a single very chatty live session could blow far past budget before the next
  check catches it.
- The T3/MANUAL dead-end (Defect 3 and 4 above) only exists for the **mock** and **stages.ts**
  paths right now; once live sessions are wired up, double-check that a live `analyzer` agent
  really does write `unsupported.json`'s `tier` field (and `manifest.json`'s own `tier`) before
  finishing, since `stageAnalyze`'s verify callback now depends on `unsupported.json` being present
  and correct at that exact point, not just eventually.

## Files changed

- `scripts/lib/sample_answers.py` (new) — `answer_for`, shared by `tests/helpers.py` and the new
  script.
- `scripts/dev/answer_samples.py` (new) + `tests/test_answer_samples.py` (new, 9 cases).
- `tests/helpers.py` — imports `answer_for` instead of a private copy.
- `orchestrator/stages.ts` — Defects 1, 3, 4.
- `orchestrator/cli.ts` — Defect 5.
- `orchestrator/test/stages.test.ts`, `orchestrator/test/fakes.ts`, `orchestrator/test/cli.test.ts`
  — new/updated tests for the above.
- `orchestrator/test/integration.test.ts` (new) — real wf_0001 end-to-end.

## Self-review

- Every defect above has RED-then-GREEN evidence in the commit that fixed it; none were patched
  around in TypeScript when the root cause was actually a Python script (Defect 2 was the one
  Python-side bug, fixed at its source with the same one-line pattern every sibling script uses).
- Defect 4 was not something 13b or the coordinator asked me to look for specifically — it surfaced
  only because I insisted on running the real wf_0005 three times end to end rather than trusting
  the two-run fake-based unit tests alone. Recorded as a reminder that "idempotent re-run" claims
  need a real third run against real data, not just a second one.
- I did not attempt to fix or relax the CopilotRunner concerns listed for Task 16; they are
  unverified against a live SDK session and out of this task's scope.

## Fix round 1

Worktree: `.worktrees/task-15-fix`, branch `wt/task-15-fix`, based on `2033036` (this task's own
merge commit above). Scope: the reviewer's reproduction of the "escalated workflows are re-run
forever" defect, plus two minors (`answer_samples.py` orphaned answer keys, a stale
`wf_0005` fixture file).

**Status: DONE.**

### The bug, and the ruling

`shouldRun(m, stage)` was `!TERMINAL_GOOD[stage].includes(status)`, and `TERMINAL_GOOD` only
lists success statuses. So a stage at `NEEDS_HUMAN` / `QUARANTINED` / (golden's) `BLOCKED` looked
exactly like a stage that "hasn't run yet" to every later `migrateWorkflow` call: a second plain
run re-invoked the translator/reviewer/validator (+ `compile_check.py`/`validate_segment.py`) for
a `NEEDS_HUMAN` workflow, and parser-recovery ×2 + `parse.py` ×3 for a `QUARANTINED` one, on every
run, forever — with the live runner this re-burns paid sessions on the whole escalated backlog
every invocation.

Coordinator ruling: these three statuses are **PARKED**, not "not started" and not "done". A plain
re-run must not retry the stage, must not run any later stage of the same workflow, must log one
line naming the workflow/stage/status/reason/resume-command, and only `--from-stage <stage>`
reopens it. `WAITING_FOR_ANSWERS` is explicitly excluded (intake's deterministic re-merge loop);
`MANUAL` and the success statuses are unaffected.

### What changed

- `orchestrator/stages.ts`:
  - `ESCALATED = ["NEEDS_HUMAN", "QUARANTINED", "BLOCKED"]` and `isParked(status)` (lines
    454–457) — deliberately status-only, not per-stage like `TERMINAL_GOOD`: `NEEDS_HUMAN` can
    land on intake/analyze/translate/document, `QUARANTINED` only on parse, `BLOCKED` on intake or
    golden, and all of them mean the same thing wherever they land.
  - `shouldRun` (line 460) now returns `false` for a parked status before consulting
    `TERMINAL_GOOD`.
  - `logParked` (line 478) — the one resume line: `` `${m.id}: ${stage} is parked at ${status}
    ${reason ? `(${reason})` : ""} — resume with --from-stage ${stage} --only ${m.id}` ``, reading
    `m.reasons?.[stage]` without mutating it (unlike the `reasons()` helper elsewhere in the file,
    which lazily creates `m.reasons = {}` — that would have been an unwanted manifest write for a
    workflow that never had a reason recorded).
  - `plannedStages` (line 487) now `break`s on an already-parked stage instead of walking past it,
    so `--dry-run` shows "would run nothing" for a parked workflow.
  - `migrateWorkflow`'s stage loop (line 519) checks `isParked` **before** `shouldRun` and
    `break`s the whole loop (not just skips the one stage) — this is what stops every later stage
    from running too, since without the `break` a parked `translate` would still let `document`'s
    own `shouldRun` (status `undefined`, not in `TERMINAL_GOOD.document`) return `true`.
  - Comment blocks above the file header and above `TERMINAL_GOOD` rewritten to describe parking
    truthfully instead of the old blanket "every stage is idempotent" claim.
- `docs/spec/01-copilot-setup.md` line 497 — the "the orchestrator skips any stage already
  `DONE`/`PASSED`" sentence rewritten to describe the three-way split (success / parked /
  `WAITING_FOR_ANSWERS`) and the resume command.
- `scripts/dev/answer_samples.py` — `_orphaned_answer_keys` (line 72) computes
  `sample.json["answers"]` keys matching no touchpoint's `key` or `tool_id` at all (any
  touchpoint, blocking or not, resolved or not — a key that matches a non-blocking/already-
  resolved touchpoint is a legitimate match, not an orphan). `answer_workflow` (line 90) returns
  it as `result["orphaned"]`; `main()` (line 173) prints one `WARNING` line per orphaned key to
  stderr per workflow, exit code untouched. Docstring updated.
- `orchestrator/test/fakes.ts` line 172 — `canned("docs", "migration.md")` moved inside the
  `if (!t3)` block: the real `samples/wf_0005/canned/` has no `docs/` tree at all (verified with
  `find samples/wf_0005/canned -type f`), and `document` is `NOT_APPLICABLE_FOR_T3` anyway, so no
  existing assertion touched this file for wf_0005 — moving it weakens nothing.
- Tests: 7 new cases in `orchestrator/test/stages.test.ts`, 1 new in `orchestrator/test/cli.test.ts`,
  2 new in `tests/test_answer_samples.py`.

### RED evidence

Before touching `stages.ts`, ran the 7 new `stages.test.ts` cases against the unmodified code
(`node --test orchestrator/test/stages.test.ts`):

```
not ok - needs_human keeps a workflow parked: a second run makes no new calls and logs the resume line
  no new agent calls on the second run: 8 !== 5
not ok - a quarantined workflow stays parked: a second run makes no new parse/parser-recovery calls
  no new parser-recovery calls on the second run: 4 !== 2
not ok - a golden BLOCKED workflow stays parked on a second run
  the simulator is not re-run: 2 !== 1
not ok - --from-stage on a later stage does not skip past an earlier parked stage
  golden's own script did not re-run either: 2 !== 1
not ok - plannedStages for a parked workflow is empty
  actual: ['translate', 'document', 'pr'], expected: []
# tests 33, pass 28, fail 5
```

(The 2 remaining new tests in that file — `--from-stage translate` reopening a `NEEDS_HUMAN`
workflow, and `WAITING_FOR_ANSWERS` still re-running intake's scripts every pass — passed even
against the buggy code, since they exercise behaviour the bug never touched: `--from-stage`
already cleared the target stage's status regardless of the `TERMINAL_GOOD` bug, and
`WAITING_FOR_ANSWERS` was never in `TERMINAL_GOOD` to begin with. Kept anyway as the
positive/negative pins the brief asked for. The new `cli.test.ts` exit-code-stability case also
passed unmodified, for the reason given below under "The exit-code rule".)

For `answer_samples.py`, ran the new orphan-key test against the unmodified script
(`pytest tests/test_answer_samples.py -q`):

```
FAILED tests/test_answer_samples.py::test_an_answer_key_matching_no_touchpoint_is_warned_about_but_does_not_fail
  AssertionError: assert 'this_key_matches_nothing' in ''
  +  where '' = CaptureResult(out='wf_0001: 3 answered, 0 unanswered\n', err='').err
```

### GREEN evidence

After the `stages.ts` fix:

```
node --test orchestrator/test/*.test.ts
# tests 93
# pass 93
# fail 0
```

After the `answer_samples.py` fix:

```
.venv/Scripts/python.exe -m pytest tests/test_answer_samples.py -q
...........                                                              [100%]
(11 passed)
```

`tsc --noEmit -p .` — clean, no output, exit 0.

Full suite: `.venv/Scripts/python.exe -m pytest` prints only progress dots in this environment
(pytest 9.1.1 here never writes its final `N passed in Ys` summary line to stdout/stderr, even for
a two-test file outside the repo's own config — reproduced and not chased further since it's
orthogonal to this task). Used `--junitxml` for a reliable count instead:
`tests="959" errors="0" failures="0" skipped="0"` (957 baseline + 2 new). Re-ran the same suite
with `-W error::DeprecationWarning -W error::UserWarning` to rule out a hidden warnings-summary
section being swallowed by the same quirk: still `tests="959" errors="0" failures="0"`, so nothing
is warning either.

### The exit-code rule (found, not invented)

`orchestrator/cli.ts`: `STATUS_NEEDS_A_HUMAN = ["NEEDS_HUMAN", "QUARANTINED", "BLOCKED"]` (line
48); `main()` filters `finished` manifests whose `status` object has any value in that list into
`stuck`, and returns `stuck.length > 0 ? 1 : 0` (lines 296–298). This reads `manifest.status`
directly, not "did a stage just run" — so as long as parking leaves the escalated status exactly
where it was (which it does: parking is a **no-op** on the stage's own status), the exit code was
*already* stable across repeated runs, bug or no bug. Added
`"exit code stays stable across repeated runs of a parked workflow"` in `cli.test.ts` to pin this
down explicitly (both `never-fixed:seg_01` → `NEEDS_HUMAN` and `recovery-fails` → `QUARANTINED`,
two `main()` calls each, same exit code both times) — it passed even before the `stages.ts` fix,
which is itself evidence the exit-code contract was never the broken part; only what got
re-invoked on the way to computing it was.

### `--from-stage` past an already-parked earlier stage

Chose: **the earlier parked stage still blocks, full stop** — I did not add any special-case code
for this; it falls out of how the loop is already structured. `migrateWorkflow`'s `for (const
stage of STAGES)` loop always starts at `STAGES[0]` ("parse") regardless of `opts.fromStage`;
`clearFromStage` only deletes the status of the *named* stage and everything after it in `STAGES`
order, never anything before it. So `--from-stage document` on a workflow parked at `golden`
clears `document`/`pr` (already `undefined` — they were never reached) but leaves
`status.golden = "BLOCKED"` untouched; the loop reaches `golden` first, sees it's still parked,
logs the resume line and stops — `document` is cleared but never attempted. Verified this with
`"--from-stage on a later stage does not skip past an earlier parked stage"` in
`stages.test.ts`: after `--from-stage document`, `status.golden` is still `"BLOCKED"`, `
status.document` is still `undefined`, and neither `calls.roles.length` nor the `alteryx_sim.py`
call count moved. I considered instead making `--from-stage` raise a usage error when it names a
stage after one that's still parked ("you probably meant `--from-stage golden`") — decided against
it: the ruling only asked that the earlier stage "must still block", not that the flag combination
itself be refused, and refusing it would need a second read of the manifest before `parseArgs`
even runs (today `--from-stage` is validated as one of the six `Stage` names with no manifest
in scope at all), which is a bigger change than this defect calls for. Silently blocking is also
what the *existing* `--from-stage` on a *finished* workflow already does one stage earlier in the
same loop (nothing new to explain to an operator).

### Vocabulary swept for other dead ends

Per the brief, checked every status in `scripts/lib/vocab.py`'s `STATUSES` / `docs/spec/02-schemas-reference.md`'s
vocabulary line against the whole repo (`grep -rn "PENDING" --include=*.py --include=*.ts .`):
`PENDING` is declared in the vocabulary but **no script or the orchestrator ever assigns it** to
`manifest.status.<stage>` — it exists only as a schema entry and in `tests/test_foundations.py`'s
own copy of the vocabulary list. It is not a dead end in practice today because nothing produces
it; if something ever did, it would behave like "not started" (shouldRun → true), not like a park,
since it isn't in `ESCALATED`. Flagging this rather than guessing, per the brief's instruction.

### Manifest-write check

Per the ruling ("ideally nothing but `updated_at`"): for a parked workflow, the loop `break`s
before calling the mid-loop `saveManifest` (that only happens inside the `shouldRun` branch, which
a parked stage never enters), so the *only* write is the unconditional one in `migrateWorkflow`'s
`finally` block — and `saveManifest` (`orchestrator/manifest.ts` line 57) unconditionally sets
`manifest.updated_at = new Date().toISOString()` before writing. `logParked` reads
`m.reasons?.[stage]` (optional chaining, no mutation) rather than calling the file's `reasons(m)`
helper, which does `m.reasons ??= {}` and would otherwise add an empty `reasons: {}` key to a
manifest that never had one. Confirmed by inspection; no test asserts manifest-diff byte-for-byte
beyond what the existing `stages.test.ts` assertions already check (status fields unchanged), but
this was verified by reading the code path, not run against real on-disk output for this
specific case.

### Counts

- Node: 85 → 93 passing (7 new in `stages.test.ts`, 1 new in `cli.test.ts`), 0 failing, `tsc
  --noEmit` clean.
- Python: 957 → 959 passing (2 new in `tests/test_answer_samples.py`), 0 failed, 0 errors, 0
  skipped (`--junitxml`, since this pytest/environment combination doesn't print a final summary
  line to the terminal — see GREEN evidence above).

### Self-review

- Every new test asserts on call *counts* (roles/py arrays), not just final status, so a fix that
  merely re-labelled the status without stopping re-invocation would still fail them.
- Did not touch `orchestrator/policy.ts` or anything under `.worktrees/task-16` per the brief's
  isolation instruction.
- The `--from-stage`-past-a-parked-stage behaviour was a judgment call (see above); flagging it
  explicitly in case the coordinator wants the alternative (a usage error) instead.
- `WAITING_FOR_ANSWERS` and `MANUAL` behaviour is unchanged by construction (`isParked` doesn't
  list them) — did not weaken or duplicate the existing tests for those paths, only added one new
  explicit test pinning `WAITING_FOR_ANSWERS`'s script-count-increases-on-every-run behaviour,
  which was previously only implied by `"unanswered intake parks the workflow and a later run
  resumes it"`.
