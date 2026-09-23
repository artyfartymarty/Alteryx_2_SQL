# Task W4 report — compaction memory aid

Worktree `.worktrees/p2-W4`, branch `wt/p2-W4`, base `5078be1`. Status: DONE.

## What I implemented

### 1. `orchestrator/hooks.ts` — the shared constants and `HookState`/`recordMetrics` extension

Chose `hooks.ts` (not `runner.ts`, despite the brief's illustrative snippet placing the code
there) as the home for `NOTES_ROLES`, `notesPath` and `notesReminder`, because `hooks.ts` is
already the one file both `runner.ts` (imports `hooksFor`, `recordMetrics`, …) and `stages.ts`
(imports `auditArgs`) depend on, with no dependency the other way — `stages.ts` currently has
*zero* dependency on `runner.ts`, and adding one just for a path-building helper would invert
that layering. This way `stages.ts` needs no new import edge into the concrete runner
implementation.

- `HookState` gains `compactions: number` and `peakInputTokens: number`, initialised to `0` in
  `hooksFor`.
- `NOTES_ROLES: Role[] = ["intake", "analyzer", "fixer"]`.
- `notesPath(wfId, role) = workflows/${wfId}/notes/${role}.md`.
- `notesReminder(role, wfId)`: the fixed reminder text, built from `notesPath`.
- `recordMetrics` now also accumulates `compactions` (like `toolCalls`) and keeps
  `peakInputTokens` as a **maximum** across sessions of the same role (like a high-water mark,
  never summed) — both on the same `previous ?? {}` / idempotent-per-session pattern the existing
  `toolCalls`/`lastMs` fields already use.

### 2. `orchestrator/runner.ts` — `CopilotRunner.run`'s event subscriptions

Right after `createSession`, aliased to a `const activeSession = session` (a plain `let session`
captured inside the two `.on(...)` closures would carry TypeScript's widened "maybe still
undefined" type, since those closures run later, asynchronously — a `const` alias sidesteps that
without an `!` assertion):

- `session.on("session.compaction_complete", …)`: a failed compaction (`!event.data.success`) is
  logged and returns; a successful one increments `state.compactions`, logs it, and — only for a
  `NOTES_ROLES` role — fires `activeSession.send({ prompt: notesReminder(role, wf.id), mode:
  "immediate" })`, fire-and-forget (`void … .catch(() => undefined)`), exactly as the brief's
  pseudocode does.
- `session.on("assistant.usage", …)`: keeps the maximum `event.data.inputTokens` seen this
  session in `state.peakInputTokens`.
- Both unsubscribe functions are collected in `unsubscribe: (() => void)[]`, declared before the
  `try`, and every one is called in the same `finally` block that already disconnects the session
  and calls `recordMetrics` — before `disconnect()`, so it runs on every exit path (normal
  return, thrown error, timeout) the `catch`/`finally` structure already covers.

Verified every SDK fact against `node_modules/@github/copilot-sdk/dist/*.d.ts` before writing
code (see "SDK facts verified" below) — all of spike fact S5 checked out exactly as written, no
brief correction needed.

### 3. `orchestrator/policy.ts` — the notes write lane

Touched only the write-lane part, per controller note 1 (C4V owns the SQL-judge section
`SqlRules`/`CONTRACT_IDENTIFIER`, untouched here):

- `writeLanes`'s non-dbt switch: `intake` gains `${wf}/notes/intake\.md$`; `analyzer`'s unbatched
  case gains `${wf}/notes/analyzer\.md$`; `translator` and `fixer` are now two separate `case`
  labels (previously one, sharing the same array) so only `fixer` gains
  `${wf}/notes/fixer\.md$` — `translator` is unaffected and is not in `NOTES_ROLES`.
- `analyzerBatchLanes` (Task W2's batch-narrowed analyzer lane) gains the same
  `${wf}/notes/analyzer\.md$` lane unconditionally, so the analyzer's notes file is writable in
  **both** the unbatched and every batched call, per the controller note.
- The `dbtProject` switch's `translator`/`fixer` case is likewise split: `fixer` keeps the
  translator's dbt lanes and gains `${wf}/notes/fixer\.md$`; `translator`'s dbt lanes are
  unchanged. The notes path carries no segment or dbt scope, so it is the same lane in every mode
  the fixer runs — Task D's dbt narrowing and Task P2's `--backend`/`--connection`/
  `--sandbox-database` denials are untouched.

### 4. `orchestrator/stages.ts` — the task-text sentence

A new `notesInstruction(role, wfId)` helper builds the brief's exact sentence (` Keep your
decisions and open items in workflows/<id>/notes/<role>.md as you go; the durable record stays in
the contract and the files you write.`) via `notesPath` (imported from `hooks.ts` alongside the
existing `auditArgs`). Appended in five places, all of them ending the **instructions**, always
immediately before any fenced inline context is concatenated on (Task F's fence: the sentence
never lands inside the data the model cannot escape):

- `stageIntake`'s task, before `+ context`.
- `stageAnalyze`'s single-call analyzer task, before `+ context`.
- `analyzeInBatches`'s per-batch analyzer task, before `+ context` (every batch, per controller
  note 3's spirit for notes-in-batches).
- `migrateSegment`'s fixer task (iteration > 0), at the very end — after `failedBeforeReview` and
  the Snowpark `pythonNote`, and after `opts.repairTask` when the chain-check round (Task W1)
  overrides the default repair sentence, since that is still a form of "the fixer task".
- `migrateDbt`'s fixer task (iteration > 0), at the very end — after `failing`/
  `failedBeforeReview`.

`translator`'s task text (iteration 0 in both `migrateSegment` and `migrateDbt`) is untouched:
`translator` is not in `NOTES_ROLES`.

### 5. `.github/agents/{intake,analyzer,fixer}.agent.md`

Each names `workflows/<id>/notes/<role>.md` as an Outputs bullet (fixer, which has no Outputs
heading, gets a short paragraph after its "Rules:" line instead — **without touching that
verbatim line itself**, since `tests/test_agents_config.py::test_unamended_agents_keep_their_spec_bodies`
pins it byte for byte), repeats the "durable record stays in the contract and the files you
write" sentence, and says when to re-read it (after a context compaction). Each role's "Rules"
section is updated to admit the new write target (intake's "Do not modify anything outside …"
line; analyzer's "Read-only except …" line, both the single-call and batched forms; fixer's new
paragraph says it applies "in every form of your task: one segment, a Snowpark segment, or the
whole dbt project"). All new text is marked `<!-- amended: output targets phase 2 -->`.

### 6. `tests/test_agents_config.py`

`test_intake_analyzer_and_fixer_keep_notes`: for each of the three roles, the (whitespace-squeezed)
body names `notes/<role>.md`, carries "the durable record stays in the contract and the files",
and carries the phase-2 marker.

### 7. `docs/reference/large-workflows.md`

New `## Compaction` section, placed between "Batched analysis and seams" and "The chain test"
(the seams section's own closing paragraph — "a batch that still overflows a real context is not
detected by these scripts" — now points forward to it). Covers: the SDK events (spike fact S5,
cited by file:line); the notes file per role and which lane opens it (including the batched and
dbt-scope narrowings); the reminder, its `mode: "immediate"` delivery and what it deliberately
never contains; the two metrics and that `peakInputTokens` is what the character budgets
elsewhere on the page are meant to be calibrated against (Task H); and, explicitly, "What is and
is not guaranteed" — the code is exercised by `runner.test.ts` against a fake session, not a real
one; `MockRunner` fires none of this and a canned run's metrics carry neither field; whether the
events arrive on the hosted profile/BYOK, and whether a model actually re-reads the notes file
when told to, are both unverified until a live run. The intro paragraph's summary sentence was
updated to mention it.

## SDK facts verified (spike fact S5, before writing any code)

All checked directly against `node_modules/@github/copilot-sdk/dist/*.d.ts` in the repo root
(worktree has no `node_modules`; Node resolves upward, and so did I):

- `dist/generated/session-events.d.ts`: `CompactionCompleteData.success: boolean` (required, not
  optional — matches the brief's `data.success`), `preCompactionTokens?: number`,
  `tokensRemoved?: number`; `AssistantUsageData.inputTokens?: number`; `CompactionCompleteEvent`'s
  `type: "session.compaction_complete"`, `AssistantUsageEvent`'s `type: "assistant.usage"`.
- `dist/session.d.ts:190`: `on<K extends SessionEventType>(eventType: K, handler:
  TypedSessionEventHandler<K>): () => void`.
- `dist/types.d.ts:2791`: `MessageOptions.mode?: "enqueue" | "immediate"` (inside the same
  interface as `prompt`, confirmed by reading the surrounding lines, not just the grep hit).
- `dist/types.d.ts:1907` / `:1588`: `contextTier?: ContextTier`, `ContextTier = "default" |
  "long_context"` (P1's existing code, re-confirmed unrelated to this task but touched the same
  `createSession` call site).
- `dist/session.d.ts:142-143`: `send(prompt: string): Promise<string>` / `send(options:
  MessageOptions): Promise<string>` — confirms `session.send({ prompt, mode })` is a real,
  correctly-typed call, not something invented for the test fake.
- `dist/types.d.ts:2819`: `TypedSessionEventHandler<T> = (event: SessionEventPayload<T>) =>
  void` — synchronous return type, which is why the compaction handler fires
  `void activeSession.send(...).catch(...)` fire-and-forget rather than awaiting it.

No spike-fact correction was needed; every cited name, line and shape matched.

## TDD evidence

Every new test was written before its implementation, in the same "wip" increment; RED was
re-verified afterward (see below) rather than captured live test-by-test, because the four
TypeScript files change together (a test exercising the reminder needs `NOTES_ROLES` to exist to
even import cleanly). Method: after the implementation was committed, I temporarily overwrote the
four implementation files (`hooks.ts`, `runner.ts`, `policy.ts`, `stages.ts`) with their
pre-W4 content read via `git show 5078be1:<path>` (never `git checkout --`, `stash` or `reset
--hard`, per the project rule), leaving the new test files in place, re-ran the affected suites,
then restored the new implementation from the working tree's own git-tracked content and
confirmed `git status`/`git diff` were empty (byte-identical restore).

**RED** (old implementation, new tests):

```
$ node --experimental-strip-types --test orchestrator/test/runner.test.ts
SyntaxError: The requested module '../hooks.ts' does not provide an export named 'NOTES_ROLES'
# tests 1  # pass 0  # fail 1        (the whole file fails to import)

$ node --experimental-strip-types --test orchestrator/test/hooks.test.ts orchestrator/test/policy.test.ts orchestrator/test/stages.test.ts
not ok - intake, analyzer and fixer may write their own notes file
not ok - the analyzer's notes file is writable in a batch too, alongside (never instead of) its batch lanes
not ok - the fixer's notes file is writable in dbt scope too; the translator's is never opened at all
not ok - a prompt_context failure is logged and the agent still runs without the block
not ok - the intake, analyzer and fixer tasks name their notes file
not ok - a batched analyzer names its notes file in every batch
not ok - Task W4: a dbt fixer names its own notes file too
not ok - a chain-triggered fixer round points at validation_workflow.json's first_divergence and expects the segment's own PASS
# tests 216  # pass 207  # fail 9
```

(The "chain-triggered fixer" and "prompt_context failure" failures are the two pre-existing tests
I updated because the new trailing sentence changed a pinned ending / added a new assertion —
listed under "Files changed" / "Brief corrections" below, not new tests, but their new
assertions are exactly what failed against the old code, confirming they exercise the new
behaviour.)

```
$ .venv/Scripts/python.exe -m pytest tests/test_agents_config.py::test_intake_analyzer_and_fixer_keep_notes -v
FAILED — AssertionError: intake  (assert 'notes/intake.md' in <body>)
```

**GREEN** (restored implementation): see "Final verification" below — every one of the tests
listed above passes.

## Files changed

`orchestrator/hooks.ts`, `orchestrator/runner.ts`, `orchestrator/policy.ts`,
`orchestrator/stages.ts`, `orchestrator/test/runner.test.ts`, `orchestrator/test/policy.test.ts`,
`orchestrator/test/stages.test.ts`, `orchestrator/test/hooks.test.ts` (see "Self-review" —
necessary consequence of `HookState` gaining two required fields), `.github/agents/intake.agent.md`,
`.github/agents/analyzer.agent.md`, `.github/agents/fixer.agent.md`, `tests/test_agents_config.py`,
`docs/reference/large-workflows.md`.

## Brief corrections

None to the tests as specified. One necessary, in-scope addition beyond the brief's own file
list: `orchestrator/test/hooks.test.ts` (not named in the brief's file list) needed a two-field
addition to one hand-built `HookState` literal (`recordMetrics adds to (not replaces) a role's
prior toolCalls...` test) once `HookState` gained `compactions`/`peakInputTokens` as *required*
fields — matching the existing convention (every other `HookState` field is required, none
optional). Without it, `tsc --noEmit` fails on that one file. Fixed by adding
`compactions: 0, peakInputTokens: 0` to the literal; no behavioural change to that test.

I also updated two pre-existing pinned assertions in `orchestrator/test/stages.test.ts` that the
new trailing sentence necessarily changes the shape of:
- `"a chain-triggered fixer round points at validation_workflow.json's first_divergence…"`: the
  final `assert.match(…, /change only what their diagnosis points at\.$/)` (anchored to the very
  end of the string) is now two assertions — one for the phrase, one anchored to the new,
  genuinely-last sentence (`workflows/wf_0001/notes/fixer.md as you go; … you write.$`).
- `"a prompt_context failure is logged and the agent still runs without the block"`: gained two
  new assertions (not a changed one) confirming the notes sentence survives even when the inline
  context block itself failed to render — since it is added to the instructions, before `+
  context`, not by the (possibly-empty) context block itself.

## Self-review notes

- **Where `NOTES_ROLES`/`notesPath`/`notesReminder` live.** The brief's Step 2 code block shows
  them written immediately above the `CopilotRunner.run` edit, which reads as "put them in
  `runner.ts`". I put them in `hooks.ts` instead (see "What I implemented" §1) because `stages.ts`
  has no existing dependency on `runner.ts` and I judged adding one, purely for a
  string-template helper, to invert the codebase's existing layering (the "outer loop" depending
  on the concrete `AgentRunner` implementation it is supposed to be abstracted from via
  `Env.runner`). `hooks.ts` is the one file already common to both. I flag this as a design
  decision, not a silent test change — every test asserts against the *behaviour*
  (`notesPath`/`notesReminder`/`NOTES_ROLES` importable and correct), and I additionally import
  them in `runner.test.ts` from `../hooks.ts` and unit-test them directly there, so the "Produces"
  interface contract is still exercised regardless of which file hosts them.
- **`recordingClient` and `fakeClient` needed a `.on()` stub.** Because `CopilotRunner.run` now
  calls `session.on(...)` unconditionally right after `createSession`, on *every* run through
  `CopilotRunner` — not just the new compaction tests — the existing `recordingClient` helper
  (used by the two per-role context-tier/effort tests) would have thrown `session.on is not a
  function` on every run. Caught this by actually running the suite (not just eyeballing the
  diff); fixed by adding a no-op `on()`/`send()` to `recordingClient`'s returned session, and
  extending `fakeClient`'s session object with real `on`/`send`/`handlerCount` implementations
  (a `Map<string, Set<handler>>`, so unsubscription is provably a real deletion, not a no-op —
  the "handlers are unsubscribed" test reads the `Set`'s size directly).
- **Fixer's dbt-scope notes lane** is not explicitly asked for by the brief's step 2 prose (which
  only calls out the analyzer's batched/unbatched split by name), but I judged it necessary for
  consistency: the notes path carries no segment or dbt scope, so a fixer told (by its own task
  text, in `migrateDbt`) to write `notes/fixer.md` would otherwise be silently denied whenever the
  workflow's `output_kind` is `dbt`. Covered by a new policy test and a new stages-level test
  (`"Task W4: a dbt fixer names its own notes file too"`).
- **`peakInputTokens` starts at `0` for every session**, including one that never received an
  `assistant.usage` event (the overwhelming majority of tests, and of `MockRunner` runs, which
  never call `recordMetrics` at all). `Math.max(priorPeak, 0)` is a safe no-op when there was
  nothing to record — tokens are never negative, so `0` is a correct "nothing observed" default,
  and it matches the existing `toolCalls: 0` default's shape.
- Ran a final honesty check: nothing in the new doc section or agent-file text claims a live
  compaction event has ever been observed; every claim about what the SDK *does* is attributed to
  the `.d.ts` files (spike fact S5) or to code in this repo, not to a live run.

## Final verification

```
$ fnm exec --using=22 npm.cmd test
# tests 291  # pass 291  # fail 0  # skipped 0   (baseline 278, +13)

$ fnm exec --using=22 node.exe …/typescript/bin/tsc --noEmit -p .
(no output — clean)

$ .venv/Scripts/python.exe -m pytest tests/test_agents_config.py tests/test_committed_workflows.py
104 passed

$ .venv/Scripts/python.exe -m pytest        (whole suite)
1690 passed in 405.47s (0:06:45)             (baseline 1689, +1 — the one new agent-config test;
                                               no skipped/warning/error/fail in the output)
```

## Commits

`55b86e1` feat: notes files for intake, analyzer and fixer; a re-read nudge after every context
compaction; compactions and peak input tokens in metrics — the two `wip:` commits made along the
way (orchestrator/hooks/runner/policy/stages + their tests; then the agent docs, python test and
reference doc) were squashed into it with `git reset --soft 5078be1`; the tree is identical
(checked via `git status`/`git diff` before and after).

## Concerns

- None that block merging. The usual, already-flagged-in-the-doc caveat applies: nothing here has
  run against a real Copilot session, so whether `session.compaction_complete` /
  `assistant.usage` actually arrive in the shapes assumed (and whether a model told to re-read its
  notes file actually does) is unverified until Task H's live run — this is stated explicitly in
  `docs/reference/large-workflows.md`'s new section, not just left implicit.

## Things G, P4 and H must know

**G (offline run of all seven samples, committed `workflows/`):**
- `MockRunner` (the offline replay path `G` uses) fires no `session.compaction_complete` or
  `assistant.usage` events and creates no notes file — a regenerated sample's
  `manifest.metrics.<role>` will **not** gain `compactions` or `peakInputTokens` keys at all,
  since `MockRunner.run` never calls `recordMetrics` (only `CopilotRunner`'s own code path and
  `hooks.ts`'s `onSessionEnd` do, and only the live `CopilotRunner` is wired to the SDK events in
  the first place). No committed sample's files change shape because of this task.
- `.github/agents/{intake,analyzer,fixer}.agent.md` changed (new Outputs bullets / Rules-section
  wording, new fixer paragraph) — if `G`'s offline run or any doc regeneration diffs agent files
  against a stored copy, expect exactly these three files to differ by the notes-related
  additions, each marked `<!-- amended: output targets phase 2 -->`.

**P4 (production hand-off doc / backlog):**
- `manifest.metrics.<role>` gains two more fields wherever a real (`CopilotRunner`) session ran:
  `compactions` (int, cumulative) and `peakInputTokens` (int, a running maximum). Both default to
  `0` when nothing of that kind happened in a session — they are not `undefined`/absent once any
  session for that role has run through `CopilotRunner`, matching `toolCalls`'s existing shape.
- If P4's hand-off doc enumerates what `manifest.metrics` contains per role, these two fields
  belong in that enumeration now.
- Nothing here has been compared against the real Copilot billing/usage page (same honesty rule
  as everywhere else in this repo) — `peakInputTokens` is a raw SDK-reported number, not
  independently verified.

**H (bounded live test):**
- This is explicitly what Task H is meant to exercise for real: `docs/live-smoke-test.md`'s
  "Telemetry — to verify on a live run" section (already written by a prior task) should be
  checked/extended to also ask, for a workflow long enough to trigger at least one compaction:
  (1) does `session.compaction_complete` actually fire, with `data.success` and (when present)
  `data.preCompactionTokens`/`data.tokensRemoved` in the shapes assumed here; (2) does the
  `mode: "immediate"` follow-up actually reach the model before its next turn, and does the model
  visibly act on it (re-read the notes file) rather than just acknowledging the nudge; (3) how
  `manifest.metrics.<role>.peakInputTokens` compares to the character-based budgets this repo
  uses elsewhere (`PROMPT_CONTEXT_CHARS` = 16 000 chars ≈ 4 000 tokens; the analyzer's batch
  budget = 60 000 chars ≈ 15 000 tokens) — this is the calibration point `docs/reference/large-workflows.md`'s
  new section and the plan's own risk table (`| Prompt-size estimate inaccuracy … |`) both point
  at H for.
- `docs/reference/large-workflows.md`'s new "Compaction" section states explicitly, in its own
  words, what remains unverified — H's own report should either confirm or correct each claim
  there, the same way earlier tasks' live evidence (task-16-report.md) corrected assumptions
  about `onErrorOccurred`'s error shape.

## Fix round 1

Coordinator review, one Critical (RULED), two Minors. Commit `108c439`. Every new test was run
RED first.

### Critical — `mergeMetrics` protected only `toolCalls`, not `compactions`/`peakInputTokens`

`orchestrator/manifest.ts`'s `mergeMetrics` (used by `reloadManifest`, called throughout
`stages.ts` mid-stage — e.g. `analyzeInBatches` after each batch, `stageParse` between recovery
attempts) took `Math.max(disk, memory)` for `toolCalls` only (the F12 fix from an earlier round);
`compactions` and `peakInputTokens` — both added to `hooks.ts`'s `recordMetrics` by this task —
fell through to the function's default "disk wins" behaviour for every other field. Since
`CopilotRunner.run`'s `finally` block can record metrics in memory well before anything saves them
(the same race F12 already documented for `toolCalls`), a mid-stage reload could silently LOWER
either field back to a stale disk snapshot: exactly the shape `analyzeInBatches`'s own
`reloadManifest` call, right after every batch, produces whenever an earlier batch's session saved
first and a later batch's session (not yet saved) recorded more. The reviewer reproduced this
against the real `reloadManifest` (`compactions 3→1`, `peakInputTokens 9000→2000`).

**Fix.** `mergeMetrics` now maxes THREE fields per role (`toolCalls`, `compactions`,
`peakInputTokens`) via one `MAX_MERGED_METRICS` list, rather than special-casing `toolCalls` alone
— every other field of a role's metrics (`lastMs`, …) still takes disk's value unconditionally, as
before.

- **RED**: added three tests before touching `manifest.ts` — two in `orchestrator/test/manifest.test.ts`
  (`"reloadManifest never erases a higher in-memory compactions count with a stale disk copy for
  the same role"`, `"…peakInputTokens…"`, using the reviewer's own repro numbers verbatim) plus a
  `"reloadManifest still takes disk's compactions/peakInputTokens when disk is AHEAD"` sibling
  mirroring the existing `toolCalls` coverage; one in `orchestrator/test/stages.test.ts`
  (`"Task W4 fix round 1: a mid-stage reload between batches keeps the higher
  compactions/peakInputTokens, not the stale disk copy"`) that drives the REAL `analyzeInBatches`
  code path: a custom `env.runner` wrapper (matching the existing "crash after batch 1 of 3" test's
  pattern) sets `wf.metrics.analyzer` and calls the real `saveManifest` after batch_01 (a low
  snapshot), then sets a higher one in memory only after batch_02 — `analyzeInBatches`'s own
  `reloadManifest`, right after batch_02's call returns, is what the test exercises. Ran
  `node --experimental-strip-types --test orchestrator/test/manifest.test.ts orchestrator/test/stages.test.ts`
  against the pre-fix `manifest.ts` (restored via `git show 108c439^:orchestrator/manifest.ts`,
  same technique as the first RED pass — never `git checkout --`/`stash`/`reset --hard`): all
  three failed (`not ok`), `# tests 134 # pass 131 # fail 3`, then the working implementation was
  restored and verified byte-identical (`git diff` empty).
- **GREEN**: see "Final verification" below.

### Minor — the fire-and-forget reminder send swallowed a failure silently

`runner.ts`'s `void activeSession.send(...).catch(() => undefined)` dropped a failed send with no
trace. Now `.catch((error) => env.log(...))` logs `<wf>: <role> notes reminder failed to send:
<reason>` (via the existing `errorText` helper, already imported). New
`orchestrator/test/runner.test.ts` test gives the fake session a `sendFails` scenario flag (its
`send()` throws); RED before the change (`assert.ok(logs.some(...))` failed — nothing was logged);
GREEN after. A `setImmediate` flush after `run()` returns makes the assertion deterministic against
the fire-and-forget promise's microtask-queued `.catch()`.

### Minor — explicit deny tests for validator/documenter against a notes path

`orchestrator/test/policy.test.ts` gained `"Task W4 fix round 1: validator and documenter, which
keep no notes file, may not write any notes path"` — four `deny(...)` assertions. These were
already correctly refused by the existing lanes (neither role's `writeLanes` case matches
`notes/`), so this is coverage, not a behaviour change; confirmed still green with no `policy.ts`
edit.

### `docs/reference/large-workflows.md`

Re-read the "Metrics" and "What is and is not guaranteed" paragraphs of the new "Compaction"
section against the fix: both already state the INTENDED behaviour (`compactions` "never lost"
across a crashed or retried session; `peakInputTokens` "never lowered" by a later session) as
something this code does, which — before this fix round — was not actually true across a
mid-stage reload. The fix makes both statements true end-to-end; no wording in the doc overclaimed
anything beyond what is now the case, so no edit was needed.

### Verification (fix round 1)

```
$ fnm exec --using=22 npm.cmd test
# tests 297  # pass 297  # fail 0  # skipped 0   (was 291; +6: 3 manifest.test.ts,
                                                    1 stages.test.ts, 1 runner.test.ts,
                                                    1 policy.test.ts)
$ fnm exec --using=22 node.exe …/typescript/bin/tsc --noEmit -p .
(no output — clean)
$ .venv/Scripts/python.exe -m pytest tests/test_agents_config.py
60 passed                                        (unchanged; no Python touched this round, so
                                                    the whole suite was not re-run, per the
                                                    coordinator's own instruction)
```

### Files changed (this round)

`orchestrator/manifest.ts`, `orchestrator/runner.ts`, `orchestrator/test/manifest.test.ts`,
`orchestrator/test/policy.test.ts`, `orchestrator/test/runner.test.ts`,
`orchestrator/test/stages.test.ts`.
