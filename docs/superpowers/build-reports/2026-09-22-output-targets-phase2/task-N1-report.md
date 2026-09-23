# Task N1 report — the orchestrator creates the notes directory before a notes role's session

Worktree: `.worktrees/p2-N1`, branch `wt/p2-N1`, base `41f4016`.

## What changed

1. **`orchestrator/runner.ts`** (`CopilotRunner.run`): right after building the `done()` closure
   and before `let session;` (i.e. before `createSession` is ever called), the run now does:
   ```ts
   if (NOTES_ROLES.includes(role)) {
     try {
       await mkdir(wfDir(this.root, wf.id, "notes"), { recursive: true });
     } catch (error) {
       env.log(
         `${wf.id}: ${role} could not create the notes directory: ${redact(errorText(error)).slice(0, AUDIT_ARG_LIMIT)}`,
       );
     }
   }
   ```
   The path is built only from `this.root`, the fixed `"workflows"` segment, `wf.id` and the fixed
   `"notes"` segment (`wfDir`, already imported) — never from task text. A failure is caught, logged
   through `env.log` (naming the workflow and role, `redact`ed and bounded to `AUDIT_ARG_LIMIT` like
   every other logged error text), and the session still runs — nothing throws out of `run`. `mkdir`,
   `path`, `NOTES_ROLES`, `redact`, `errorText`, `AUDIT_ARG_LIMIT` and `wfDir` were all already
   imported in this file, so no import list changed. `MockRunner` is untouched.

2. **`orchestrator/stages.ts`** (`notesInstruction`): kept the existing sentence and appended fixed
   text, as a new sentence:
   > "... the durable record stays in the contract and the files you write. The directory already
   > exists; write the file with your file-writing tool; do not create directories."

   Still part of the INSTRUCTIONS, before any fenced inline context (`inlineContext`'s block is
   still appended after `notesInstruction(...)` in every call site — `stageIntake`, `stageAnalyze`,
   `analyzeInBatches`, `migrateSegment`'s fixer branch, `migrateDbt`'s fixer branch). No workflow
   text is interpolated into it; the sentence names only the fixed path via `notesPath`.

3. **`orchestrator/hooks.ts`**: updated the doc comment above `notesPath` to say `CopilotRunner.run`
   now creates the notes directory before the session starts, and why (no role's policy lane allows
   creating a directory itself). `notesReminder` itself is unchanged — no test needed the same
   sentence there (the reminder only fires mid-session, after the directory already exists).

4. **`orchestrator/policy.ts`**: **not changed**, per requirement 5 — directory creation by an
   agent stays denied for every role. One new pinning test added instead (below).

5. **`docs/reference/large-workflows.md`**: the "**The notes file.**" paragraph now says
   `CopilotRunner.run` creates `workflows/<wf>/notes/` itself before the session (naming Task N1 and
   the live evidence — PowerShell `New-Item`/`md`, a `python -c` makedirs call), that a create
   failure is logged and the session still runs, quotes the new fixed sentence in full, and adds
   that directory creation by an agent stays denied for every role. `docs/live-smoke-test.md` and
   `docs/spec/**` were not touched, per the brief. No other README or `docs/` sentence describes the
   notes files (`docs/reference/output-targets.md`'s "notes" hits are all `translation_notes.md`,
   unrelated; `README.md` has none). The `.github/agents/*.agent.md` files and
   `tests/test_agents_config.py`'s `test_intake_analyzer_and_fixer_keep_notes` describe the agents'
   own view of the notes file, not the orchestrator's directory-creation mechanism, and are outside
   the brief's "README or docs/" scope — left untouched; that Python test only asserts substrings
   ("notes/<role>.md", "the durable record stays in the contract and the files"), so it stays green
   unaffected.

## A test the change required updating (not a brief correction — a corollary of requirement 3)

`orchestrator/test/stages.test.ts`'s "a chain-triggered fixer round points at
validation_workflow.json's first_divergence and expects the segment's own PASS" test asserted the
ordinary fixer task text with a `$`-anchored regex ending exactly at "...the files you write.".
Requirement 3 says `notesInstruction` "keeps its current sentence and adds" more fixed text after
it, so that anchor necessarily breaks once the new sentence is appended. Updated the regex to expect
the fixer task to end with "...the files you write. The directory already exists; write the file
with your file-writing tool; do not create directories." instead. No other test asserted an exact
end-of-string match on this text (grepped for `write\.$` project-wide in `orchestrator/test/`).

## Tests written (TDD)

New/extended tests, following the brief's list:

- `orchestrator/test/runner.test.ts` — new "Task N1" section at the end of the file:
  - one test per role (`intake`, `analyzer`, `fixer`) asserting, **inside the fake client's
    `createSession`**, that `existsSync(<root>/workflows/wf_0001/notes)` is already `true` — not
    checked after `run()` returns, so the test actually pins the *ordering*, not just the end state;
  - one test per role (`translator`, `reviewer`, `validator`, `documenter`) asserting no notes
    directory exists after `run()`;
  - one test running `intake` twice and asserting both succeed and the directory still exists
    (idempotent);
  - one test that puts a **file** at `workflows/wf_0001/notes` first (so `mkdir(..., {recursive:
    true})` fails), then asserts the session still returns `ok: true` and a log line names both
    `wf_0001` and `analyzer` and mentions "notes".
- `orchestrator/test/stages.test.ts`:
  - extended "the intake, analyzer and fixer tasks name their notes file" to also assert the new
    fixed sentence is present in all three task forms, and (for intake and analyzer, which get an
    inline-context block) that it appears **before** `"## Inline context"` in the task string
    (`indexOf` comparison);
  - extended "a batched analyzer names its notes file in every batch" (renamed to also mention the
    directory-exists sentence) the same way, per batch;
  - extended "Task W4: a dbt fixer names its own notes file too" to assert the sentence is present
    in the dbt-scope fixer form too;
  - fixed the `$`-anchored regex above.
- `orchestrator/test/policy.test.ts` — one new test, "Task N1: intake may not create the notes
  directory itself, by PowerShell New-Item or python -c", pinning requirement 5: `decide("intake",
  "wf_0001", "powershell", { command: "New-Item -ItemType Directory -Path workflows/wf_0001/notes
  -Force", description: "..." })` is denied (reason: "may not run this command", the same catch-all
  `decideShell` already gives any unrecognized command), and a `powershell` call whose `command` is
  `.venv\Scripts\python.exe -c "__import__('os').makedirs('workflows/wf_0001/notes')"` is denied
  (reason: "interpreter flag -c is never allowed" — `INTERPRETER_FLAGS` already includes `-c`, so
  this was already denied and stays denied; the test pins that this remains true even though intake
  now finds the directory already there and has no more reason to try). I deliberately kept this
  `python -c` command free of a semicolon: with one (e.g. `import os; os.makedirs(...)`, closer to
  the live evidence's literal shape), `policy.ts`'s `SHELL_METACHARACTERS` check (which runs before
  the interpreter-flag check) denies it first for a different, still-correct reason ("shell
  metacharacter in command"); either way it is denied, but the semicolon-free form exercises the
  more specific, already-established `/interpreter flag/` assertion style used elsewhere in this
  test file (see the existing tests at policy.test.ts:894-895).

## RED evidence

Before implementing (only the new/changed assertions failing; 253/262 passing on the same three
files beforehand):

```
"/c/Users/<user>/AppData/Local/Microsoft/WinGet/Links/fnm.exe" exec --using=22 node.exe \
  --experimental-strip-types --test orchestrator/test/runner.test.ts orchestrator/test/stages.test.ts orchestrator/test/policy.test.ts
```
```
not ok 130 - Task N1: the notes directory exists before createSession is called, for intake
not ok 131 - Task N1: the notes directory exists before createSession is called, for analyzer
not ok 132 - Task N1: the notes directory exists before createSession is called, for fixer
not ok 137 - Task N1: running a notes role's session twice is idempotent
not ok 138 - Task N1: a notes directory create failure is logged (naming the workflow and role), and the session still runs
not ok 200 - the intake, analyzer and fixer tasks name their notes file
not ok 201 - a batched analyzer names its notes file, and the directory-exists sentence, in every batch
not ok 214 - Task W4: a dbt fixer names its own notes file too
not ok 243 - a chain-triggered fixer round points at validation_workflow.json's first_divergence and expects the segment's own PASS
# tests 262
# pass 253
# fail 9
```
Example failure (the `createSession`-timing test, before `runner.ts` created anything):
```
AssertionError [ERR_ASSERTION]: intake: the notes directory must exist before createSession
```
The policy test ("Task N1: intake may not create...") was already green pre-implementation, as
expected — it pins existing `policy.ts` behaviour that the brief says must NOT change.

## GREEN evidence

After implementing (same three files):
```
1..262
# tests 262
# suites 0
# pass 262
# fail 0
# cancelled 0
# skipped 0
```

Full node suite (`npm test`, all files):
```
1..331
# tests 331
# pass 331
# fail 0
# cancelled 0
# skipped 0
```

tsc (`--noEmit -p .`): no output, exit 0.

`git status --porcelain` after the node/tsc runs, before pytest: only the 7 files this task touched
(`docs/reference/large-workflows.md`, `orchestrator/hooks.ts`, `orchestrator/runner.ts`,
`orchestrator/stages.ts`, and the three test files) — no untracked or modified `workflows/` trees.

Whole pytest suite (`.venv/Scripts/python.exe -m pytest`, no `-q`, from the worktree root):
```
1934 passed in 451.00s (0:07:30)
[exited with code 0]
```
0 skipped. `git status --porcelain` afterward: still only the same 7 files listed under "Files
changed" below — no committed `workflows/` tree changed (`tests/test_committed_workflows.py` is
part of this run and passed).

## Files changed

- `orchestrator/runner.ts`
- `orchestrator/stages.ts`
- `orchestrator/hooks.ts`
- `docs/reference/large-workflows.md`
- `orchestrator/test/runner.test.ts`
- `orchestrator/test/stages.test.ts`
- `orchestrator/test/policy.test.ts`

## Self-review

- Requirement 1 (directory creation site, path construction, failure handling): done as specified;
  placed before `createSession`, path built only from fixed segments, failure caught and logged,
  never throws.
- Requirement 2 (`MockRunner` unchanged, committed `workflows/` trees untouched): confirmed by
  `git status` staying clean of any `workflows/**` change after both the node suite and (pending)
  `tests/test_committed_workflows.py`.
- Requirement 3 (`notesInstruction` fixed text, position before the fence, no workflow text):
  done; verified in tests by `indexOf` ordering against the fake `"## Inline context"` marker.
- Requirement 4 (`notesReminder` unchanged unless a test needs it): left unchanged; no test needed
  it, since the reminder only fires mid-session, well after the directory is known to exist.
- Requirement 5 (policy unchanged, one pinning test): confirmed `policy.ts` has a zero-line diff;
  added the one test.
- Docs: updated the `notesPath` comment and the one `docs/reference/` paragraph that describes the
  notes files; left `docs/live-smoke-test.md` and `docs/spec/**` untouched as instructed.

## Concerns

None. The one thing worth flagging for the reviewer: I updated one pre-existing test's `$`-anchored
regex assertion (`stages.test.ts`, the chain-triggered-fixer-round test) because requirement 3
necessarily invalidates any exact end-of-string match on the old sentence — this is a mechanical
consequence of the brief's own instruction, not a disagreement with it, but flagging it explicitly
since the brief didn't call it out by name.
