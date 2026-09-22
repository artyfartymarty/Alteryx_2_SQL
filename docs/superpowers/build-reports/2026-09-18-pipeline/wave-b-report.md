# Wave B report — final-review fix round (TypeScript orchestrator)

Worktree: `.worktrees/wave-b`, branch `wt/wave-b`, based on `1228ed3`.
Scope: `orchestrator/**` (code, tests, `orchestrator/POLICY.md`) and `orchestrate.ts` (untouched —
nothing in scope required editing it). No model server or live Copilot session was started;
`--runner mock` (the default) and the real Python venv (integration test only) were the only
things executed.

Baseline confirmed before starting: 115/115 node tests passing, `tsc --noEmit` clean.
Final: 144/144 node tests passing, `tsc --noEmit` clean, integration test green, worktree clean
except the intended diff.

Commands used throughout (from the worktree root):
```
"C:\Users\<user>\AppData\Local\Microsoft\WinGet\Links\fnm.exe" exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts
"C:\Users\<user>\AppData\Local\Microsoft\WinGet\Links\fnm.exe" exec --using=22 node.exe "C:\Users\<user>\Desktop\Alteryx to Snowflake\node_modules\typescript\bin\tsc" --noEmit -p .
```

RED evidence method: rather than writing tests before any code existed (several items touch the
same functions), each item's new tests were run once against the code as it stood at `HEAD`
(`git show HEAD:orchestrator/<file>.ts` copied over the working file, tests run, then the new
file restored from the in-progress edit — never `git stash`/`git checkout --`/`git reset --hard`,
per the hard rule). Every RED run is quoted below.

---

## F7 — anchor the rate-limit regex; context-overflow wins on a shared signature

**Files:** `orchestrator/hooks.ts:24` (`RATE_LIMIT`), `orchestrator/hooks.ts:190-196`
(`onErrorOccurred`), `orchestrator/runner.ts:308-319` (`CopilotRunner.run`'s catch block).

**Fix:**
- `RATE_LIMIT` changed from `/429|rate.?limit|quota/i` to `/(?<!\d)429(?!\d)|rate.?limit|quota/i`
  — `429` must now be a standalone number, so it no longer matches inside `42901`, `14290`, or
  `"prompt_tokens":42900`.
- **Precedence, in both places `RATE_LIMIT`/`CONTEXT_OVERFLOW` are judged together:**
  `contextOverflow` is now computed first, and `rateLimited` is only set when `!contextOverflow`.
  This was necessary in addition to anchoring: a message can legitimately carry a standalone `429`
  *and* the context-overflow phrase in the same text (e.g. `"429: the request exceeds the
  available context size (32768 tokens)"`), and the ruling requires context-overflow to win
  whenever both signatures are present, because the two errors have different retry policies
  (`RETRY_ONCE` never retries context-overflow; rate-limit backs off and retries up to
  `MAX_RATE_LIMIT_RETRIES` times).

**RED** (`node -e` against the pre-fix regex/logic, run before editing):
```
"request (42901 tokens) exceeds the available context size (32768 tokens)"
  OLD rateLimited=true  contextOverflow(as old code computes it)=false
"request (14290 tokens) exceeds the available context size (8192 tokens)"
  OLD rateLimited=true  contextOverflow(as old code computes it)=false
"429: the request exceeds the available context size (32768 tokens)"
  OLD rateLimited=true  contextOverflow(as old code computes it)=false
```
All three were misclassified as rate-limit under the old code — exactly the bug described.

**GREEN:** `orchestrator/test/hooks.test.ts` (4 new tests: regex-level anchoring for the exact
live strings named in the brief, real rate-limit strings still match, `onErrorOccurred`
classifies the exact token-count messages as context-overflow, and a message carrying both
signatures classifies as context-overflow) and `orchestrator/test/runner.test.ts` (2 new tests at
the `CopilotRunner.run` catch-block level, same precedence). All existing d1/d3 tests (which
already covered rate-limit-only and context-overflow-only cases) still pass unchanged.

No behaviour left to choose — the brief's fix and test list were unambiguous.

---

## F8 (RULING) — `--from-stage` grants a fresh tool-call budget

**Files:** `orchestrator/stages.ts:540-558` (`clearFromStage`, signature changed to take `env` for
logging), `orchestrator/stages.ts:590` (call site in `migrateWorkflow`).

**Fix:** `clearFromStage` now resets every role's `metrics.<role>.toolCalls` to `0` (keeping
`lastMs`, per the ruling) whenever `--from-stage` is given and the workflow's cumulative spend
(`toolCallsUsed`) was greater than zero, and logs one line:
`"<id>: --from-stage <stage> reset the tool-call budget (was <n>, now 0)"`. A plain re-run (no
`--from-stage`) is untouched — it still re-parks at the same budget-exceeded NEEDS_HUMAN, since
`clearFromStage` returns immediately when `from` is undefined.

**Behaviour chosen:** the reset is skipped (no metrics mutation, no log line) when the workflow
was never over budget, so a routine `--from-stage translate` on a healthy workflow does not spam
a "budget reset" line for zero calls — tested explicitly (`F8: a --from-stage on a workflow that
was never over budget logs nothing about a reset`).

**RED** (old `clearFromStage(m, from)` — no `env` param, no budget logic — run before editing):
```
F8: a plain re-run of an over-budget workflow still parks; --from-stage grants a fresh budget and lets agents run again
  AssertionError: the reopened workflow gets past intake (still NEEDS_HUMAN)
F8: metrics after the reopened run count only the NEW calls, not the stale spend --from-stage reset
  AssertionError: the stale role's toolCalls was reset to 0 — 401 !== 0
```

**GREEN:** `orchestrator/test/stages.test.ts`, 3 new tests (over-budget + plain re-run still
parks; `--from-stage` reopens and resets; a healthy workflow's `--from-stage` logs nothing about a
reset). 43/43 in `stages.test.ts`.

---

## F11 (RULING) — crash window + no re-translation of PASSed segments

**Files:** `orchestrator/stages.ts:290-490` (`migrateSegment`, `stageTranslate`),
`orchestrator/stages.ts:540-553` (`clearFromStage`'s `segment_status` clearing).

**Fix, three parts:**
1. In `stageTranslate`, `m.status.translate = "NEEDS_HUMAN"` (and `reasons(m).translate`) is now
   set *before* the `saveManifest` call that records that wave's `segment_status` (previously the
   save happened first, then status was set only after — the exact crash window the brief
   describes). One `saveManifest` call now commits both together.
2. Each wave's segment list is now mapped through a check: if `segment_status[segment]` already
   starts with `PASS`, `migrateSegment` is never called for it — no agent, no script, `proc.sql`
   untouched, the recorded verdict is kept verbatim. This is what lets a resumed multi-wave
   translate skip already-completed waves.
3. `clearFromStage` now deletes `m.segment_status` entirely whenever the stages it reopens include
   `"translate"` (i.e. `--from-stage translate` or any earlier stage) — per the ruling, an
   explicit reopen redoes every segment deliberately, including ones that previously passed.

**Behaviour chosen for the crash-window case** (a manifest with `segment_status[seg] =
"NEEDS_HUMAN"` but `status.translate` unset): `stageTranslate` now checks, before touching any
wave, whether any segment in `order.json` is already recorded `NEEDS_HUMAN`; if so it sets
`status.translate = "NEEDS_HUMAN"` with a reason `"<seg>: needs_human (recorded)"` and stops
immediately — **no agent is invoked for that segment or any other segment in the workflow**. This
was a judgment call the ruling left open ("decide ... say what you implemented"): the alternative
(retry that one segment fresh) risked re-running a segment a human may already be looking at
under the escalation; treating it as an immediate park, reopenable only by `--from-stage`, matches
how every other recorded escalation in this codebase already behaves.

**RED** (old code — no skip logic, no reordered save, no `segment_status` clearing on
`--from-stage`, no recorded-NEEDS_HUMAN check):
```
F11: resuming mid-translate after wave 1 passed ... AssertionError: 2 !== 1 (translator ran twice — seg_01 re-translated)
F11 (crash window): a segment recorded NEEDS_HUMAN with status.translate unset parks the stage ...
  AssertionError: expected 'NEEDS_HUMAN', actual 'VALIDATED' (the old code silently re-ran and passed the segment again)
```
(The third new F11 test, `--from-stage translate clears segment_status`, happened to also pass
against old code — unsurprising, since old code redid *everything* unconditionally on
`--from-stage` for lack of any skip logic at all; it is still valuable as a locked-in regression
test against a future, more selective implementation.)

**GREEN:** `orchestrator/test/stages.test.ts`, 3 new tests, plus `orchestrator/test/fakes.ts` was
extended with a `twoWaves` fixture option (writes `order.json = [["seg_01"],["seg_02"]]` and seeds
canned artifacts for `seg_02`) to make a genuine two-wave scenario testable — this is new fixture
surface, not production code, but is called out since it changes shared test infrastructure.
The existing `integration.test.ts` run-3 no-op assertions (proc.sql/validation.json mtimes
unchanged) also continue to pass unaffected, end to end, against the real Python scripts.

---

## F12 (RULING) — loud corrupt manifests, atomic saves, metrics-merge hardening

**Files:** `orchestrator/manifest.ts` (`loadManifest:94-121`, `CorruptManifestError:85-92`,
`writeJson:60-76` + `renameOver:48-58`, `mergeMetrics:139-154`, `reloadManifest:156-179`),
`orchestrator/cli.ts:256-270` (tier-filter tolerance), `orchestrator/cli.ts:301-318` (per-workflow
catch in the pool loop).

**Fix:**
- `loadManifest` now distinguishes ENOENT (returns `emptyManifest`, unchanged) from every other
  failure reading or parsing the file — a JSON syntax error, or JSON that parses to a non-object
  (array/string/number) — which now throws `CorruptManifestError`, naming the file path and the
  underlying cause. Nothing is written; the corrupt file is left exactly as found.
- `cli.ts`'s `main` catches this (and any other exception) *per workflow*, inside the pool
  worker, instead of letting one workflow's failure propagate through `Promise.all` and abort the
  whole run: the failing workflow is logged (`orchestrate: <id>: <message>`) and recorded, every
  other workflow in the same run still completes and is reported, and the run exits **2** if any
  workflow failed this way (consistent with the existing "usage error or unexpected failure"
  band, not a new exit-code meaning).
- The `--tier` pre-filter step (which also calls `loadManifest` for every candidate) tolerates a
  corrupt manifest by leaving that workflow selected (so its *real* error surfaces later, against
  its own id, inside the per-workflow catch above) rather than aborting tier selection for
  everyone.
- `writeJson` (which `saveManifest` is built on) now writes to a temp file in the same directory
  and renames over the destination, with a short retry loop (5 attempts, exponential-ish backoff)
  absorbing Windows' transient `EPERM`/`EBUSY`/`EACCES` on rename-over-an-existing-file.
- `reloadManifest`'s metrics merge (previously blind disk-wins per role) now takes the **max**
  `toolCalls` per role between memory and disk (`mergeMetrics`), since `toolCalls` is
  monotonically additive and a smaller number can never be "fresher" than a larger one.

**Reviewer's `reloadManifest` concern — investigated, not reachable in today's call graph, fixed
anyway:** I traced every `reloadManifest` call site in `stages.ts`. Each one either runs before
that role's agent has executed at all (e.g. `stageParse`'s post-script reload), or immediately
after a **successful** `runAgent` call, whose `onSessionEnd` hook (see `hooks.ts:196-201`) already
called `saveManifest` before `runAgent` returned — so disk is never stale relative to memory at
the point of any real `reloadManifest` call. Every escalation path (`escalate(...)`) returns
immediately without calling `reloadManifest` afterward, and `migrateWorkflow`'s own per-stage
`saveManifest` runs before the *next* stage could reach a `reloadManifest` call. I could not
construct a path where blind disk-wins would actually erase a fresher count today. I hardened
`mergeMetrics` anyway (per "fix if real ... if not real, explain" — a `Math.max` per role is
strictly safer than disk-wins in every case, costs nothing, and closes the hazard against a future
stage or retry path that does introduce the race) and added direct unit tests against
`reloadManifest` in isolation proving both directions (memory ahead survives; disk ahead is still
taken; a role only on disk still merges in).

**RED:**
- `manifest.test.ts` run against `git show HEAD:orchestrator/manifest.ts` failed to even load —
  `SyntaxError: The requested module '../manifest.ts' does not provide an export named
  'CorruptManifestError'` — the whole feature did not exist.
- `cli.test.ts`'s new F12 test run against `git show HEAD:orchestrator/cli.ts`:
  ```
  AssertionError: wf_0001's translate=VALIDATED line was never printed
  ```
  (the corrupt `wf_0002` manifest aborted the whole `main()` call before `wf_0001` — the only
  other workflow in the run — ever got reported, confirming the "one corrupt file kills every
  workflow" defect.)

**GREEN:** new file `orchestrator/test/manifest.test.ts` (9 tests: ENOENT vs corrupt, corrupt for
one workflow doesn't block another via direct `loadManifest` calls, atomic-write leaves no temp
file and survives a second save, and the three `mergeMetrics` directions) plus one new test in
`orchestrator/test/cli.test.ts` (multi-workflow run, one corrupt, exit 2, file path named, the
healthy sibling still completes and is reported). `orchestrator/test/fakes.ts` gained an exported
`seedWorkflow` helper (extracted from `makeEnv`) so a test can add a second workflow directory
into an existing fake's root.

---

## F13 — carry the failing segment + cause into `reasons.translate`

**File:** `orchestrator/stages.ts:300-390` (`SegmentOutcome`, `agentFailureReason`,
`migrateSegment`), `orchestrator/stages.ts:429-436` (`stageTranslate`'s failure branch).

**Fix:** `migrateSegment` now returns `{ verdict, reason? }` instead of a bare string. `reason` is
populated at every point the old code silently escalated:
- an agent (translator/fixer/reviewer/validator) failing outright → `"<role> <error>"`, or just
  `"budget"` when the tool-call budget stopped it (matches the brief's own examples verbatim:
  `seg_03: budget`);
- `compile_check.py` exiting 2 → `"script-error"` (consistent with the existing stage-level
  convention);
- a validation report with `needs_human: true` → `"needs_human"`;
- the fix loop exhausting every iteration → the *last* thing that actually happened:
  `"validation FAIL after N iterations"`, `"reviewer BLOCK after N iterations"`, or `"compile
  check failed after N iterations"`, tracked in a `lastReason` variable updated each iteration.

`stageTranslate` joins every failed segment's `"<segment>: <reason>"` with `"; "` into
`reasons.translate` (a wave can have more than one segment; every one that ended `NEEDS_HUMAN` is
named, not just the first).

**RED** (old `migrateSegment` returning a bare string, no reason ever recorded):
```
F13: exhausting the fix loop on validation FAIL records which segment and why
  input did not match /seg_01: validation FAIL after 3 iterations/. Input: ''
F13: a needs_human validation report records 'needs_human' against its segment
  input did not match /seg_01: needs_human/. Input: ''
F13: a tool-call budget hit mid-segment records 'budget' against its segment, not a role name
  input did not match /seg_01: budget/. Input: ''
```
`reasons.translate` was simply never set on the old code path — confirmed empty, matching the
brief's "the parked log line and cli summary print `{}`" observation.

**GREEN:** `orchestrator/test/stages.test.ts`, 3 new tests covering the three example shapes named
in the brief (`validation FAIL after N iterations`, `needs_human`, `budget`); `reviewer BLOCK` and
`compile check failed` reasons are exercised indirectly by the existing `compile-fails` and
fix-loop scenario tests (their segment_status/status assertions were unaffected, confirming no
regression, though I did not add a standalone assertion on the exact `reviewer BLOCK` string since
no fixture drives a segment to exhaust iterations purely on repeated BLOCK verdicts without also
matching one of the three already-tested shapes).

---

## F10 — correct golden/Alteryx instructions

**File:** `orchestrator/stages.ts:262-289` (`stageGolden`).

**Fix:** Read `scripts/inject_outputs.py`'s docstring and `argparse` definition in the main repo
(read-only, as instructed). Confirmed: `--capture-dir` is `required=True` on *every* invocation;
the plain form instruments the workflow and writes `capture_map.json` plus prints the
`AlteryxEngineCmd` command line; only a second invocation with `--import-set <name>` reads the
`.yxdb` captures and writes golden CSVs under `golden/` — captures never land there from the first
form alone. The log message now spells out both commands with their real flags and what each one
does, instead of one under-specified sentence.

**Behaviour chosen:** the example golden-set name in the second command is `"normal"` — not
specified by the ruling, but it matches the set names already used elsewhere in this codebase
(`scripts/dev/alteryx_sim.py`'s own default sets, and the fixture data in `fakes.ts`), and the
message says `<capture-dir>` as a placeholder rather than inventing a real path, since the real
path is operator-chosen.

**RED** (old one-line message, run before editing):
```
The input did not match the regular expression /--capture-dir/. Input:
'wf_0001: golden data must come from Alteryx — run "python scripts/inject_outputs.py wf_0001", execute the instrumented copy with AlteryxEngineCmd, put the captures under workflows/wf_0001/golden/, then re-run'
```

**GREEN:** `orchestrator/test/stages.test.ts`, 1 new test asserting the message contains both
`--capture-dir` and `--import-set`, and specifically that both full commands
(`inject_outputs.py wf_0001 --capture-dir <dir>` and `... --capture-dir <dir> --import-set
<name>`) appear — i.e. exactly the two-step contract the real script's parser requires.

---

## F16-policy (RULING) — allow both venv Python spellings

**Files:** `orchestrator/policy.ts` (no change needed), `orchestrator/POLICY.md:54-67`.

**Finding:** `policy.ts`'s existing `PYTHON_EXES` already listed `.venv/scripts/python.exe` and
`.venv/bin/python` (lower-cased, forward-slash) alongside bare `python`/`python3`/`py`, and
`decideShell`'s `normalizeToken` (backslash→forward-slash, strip leading `./`, lower-case) already
normalizes a literal Windows `.venv\Scripts\python.exe` shell-tool argument to the exact same
string before comparing against that list. This was confirmed by tracing the code by hand and by
running new tests directly against the current `policy.ts` with **no source change** — they passed
immediately (not a false green: I additionally reproduced the earlier F7/F8/F10/F11/F12/F13 RED
failures successfully with this same swap-and-restore technique elsewhere in this task, so the
technique itself is proven to detect a real gap when one exists).

**Tests added anyway** (regression protection, since nothing enforced this shape before): both
spellings, forward and back slash, for a representative script per role that has one
(`compile_check.py` for translator/fixer, `validate_segment.py` and `compare.py` for validator,
`intake_touchpoints.py` for intake, `segment.py` for analyzer, `parse.py` and the `-m pytest`
invocation for parser-recovery); confirmation that bare `python scripts/…` still works; and
confirmation that every existing denial still holds through the new spellings (wrong role's
script, a different interpreter's absolute path, `-c`, `-m` for a non-parser-recovery role or a
non-corpus target, path traversal in an argument, a shell metacharacter).

**Decision documented in `POLICY.md`:** bare `python` **stays allowed** — it was the working,
documented form before agents were told to prefer the venv path, allowing it costs nothing (it is
still exactly one interpreter invocation subject to every other check), and narrowing it would
only break an agent that has not yet picked up the new instruction. `POLICY.md` §4 now names
`PYTHON_EXES`'s exact spellings and states this decision explicitly.

---

## Files changed

```
orchestrator/POLICY.md
orchestrator/cli.ts
orchestrator/hooks.ts
orchestrator/manifest.ts
orchestrator/runner.ts
orchestrator/stages.ts
orchestrator/test/cli.test.ts
orchestrator/test/fakes.ts          (new: seedWorkflow export, twoWaves fixture option)
orchestrator/test/hooks.test.ts
orchestrator/test/manifest.test.ts  (new file)
orchestrator/test/policy.test.ts
orchestrator/test/runner.test.ts
orchestrator/test/stages.test.ts
```

## Node test counts

Before: 115/115 passing. After: 144/144 passing (+29: hooks +4, runner +2, stages +10,
manifest +9 new file, cli +1, policy +3). `tsc --noEmit -p .` clean before and after. Integration
test (`orchestrator/test/integration.test.ts`, real venv Python + mock agents) green, not skipped
— the shared venv at the primary checkout's path was found and used.

## Self-review / concerns

- `stageTranslate`'s "recorded NEEDS_HUMAN parks the stage" check (F11 crash-window branch) scans
  *every* segment in `order.json` on every plain run, which is a cheap `Array.filter` over an
  already-small in-memory list — no meaningful cost added.
- `agentFailureReason`'s `"<role> <error>"` shape (F13) is a plain string, not machine-parsed
  anywhere else in the codebase; if a future task wants to parse `reasons.translate`
  programmatically (rather than just display it), it may be worth a structured shape instead of a
  joined string. Left as a string because that is what `reasons.<stage>` already is everywhere
  else (`reasons.analyze`, `reasons.golden`, etc.), and the brief's own examples are strings.
- The `mergeMetrics` hardening in F12 is defensive, not fixing an observed failure — flagged
  explicitly above rather than claimed as a real bugfix.

## Nothing surprising

The two most interesting findings were negative results: F16 required no source change (already
correct), and the F12 `reloadManifest` metrics race, while a real hazard *in isolation* and worth
closing, was not reachable through any actual call path in the current stage functions.
