### Task N1: The orchestrator creates the notes directory before a notes role's session (controller-added, from the third live test)

**Tier:** standard · **Base:** integration head 41f4016 · **Worktree:** `.worktrees/p2-N1` on `wt/p2-N1`

**Why (live evidence, `docs/live-smoke-test.md` "Third live test", 2026-09-23):** intake, analyzer and fixer are told
to keep `workflows/<wf>/notes/<role>.md` (Task W4, `notesInstruction` in `orchestrator/stages.ts`, `notesPath` in
`orchestrator/hooks.ts`). In all three live intake sessions the model first tried to CREATE the `notes/` directory
itself: PowerShell `New-Item`/`md`/`Test-Path`, `.venv\Scripts\python.exe -c "…makedirs…"`, and once a `create` of a
small Python script that would make it. The policy correctly denied every one (the intake role's allow-list has no
directory creation and no arbitrary interpreter call), which cost tool calls in every run. The fix is on the
orchestrator side: the directory exists before the session starts, and the instruction says so. No policy lane is
widened.

**Binding requirements:**
1. `CopilotRunner.run` (`orchestrator/runner.ts`) creates `<root>/workflows/<wf.id>/notes/` (recursive, idempotent)
   BEFORE `createSession` whenever `NOTES_ROLES.includes(role)`; for every other role it creates nothing. The path is
   built from `this.root`, the fixed `workflows` segment, `wf.id` and the fixed `notes` segment — never from task text.
   A failure to create it is logged through `env.log` (naming the workflow and role, text redacted like the other log
   lines) and the session still runs; it never throws out of `run`.
2. `MockRunner` is unchanged (it writes canned files and keeps no notes); the committed `workflows/` trees must not
   change (git tracks no empty directory, but confirm `git status` is clean after the node suite and after
   `tests/test_committed_workflows.py`).
3. `notesInstruction` (`orchestrator/stages.ts`) keeps its current sentence and adds, as fixed text: the directory
   already exists; write the file with your file-writing tool; do not create directories. It stays part of the
   INSTRUCTIONS, before any fenced inline context (Task F's rule: the data fence never carries an instruction), and
   still contains no workflow-authored text other than the path from `notesPath`.
4. `notesReminder` (`orchestrator/hooks.ts`, the post-compaction re-read message) is unchanged unless a test shows it
   needs the same sentence; if you change it, keep it fixed text.
5. The policy (`orchestrator/policy.ts`) is NOT changed: the notes lanes stay exactly as they are, and directory
   creation by an agent stays denied. Add one policy test that pins this: for intake, a PowerShell `New-Item` of
   `workflows/wf_0001/notes` and a `python -c` makedirs call are both denied (use the real tool names the SDK sends:
   `powershell` with `{command, description}`).

**Tests (node, `orchestrator/test/runner.test.ts` and `stages.test.ts`, with the existing fake client/env patterns):**
- for each of intake, analyzer and fixer: when the fake client's `createSession` is called, the directory
  `<root>/workflows/<wf>/notes` already exists (assert inside the fake `createSession`, not after `run` returns);
- for translator, reviewer, validator and documenter: no `notes` directory exists after `run`;
- running `run` twice for the same notes role succeeds (idempotent);
- a mkdir failure (e.g. a FILE already sitting at `workflows/<wf>/notes`) is logged, and the session still runs;
- every form of the intake, analyzer (batched included) and fixer task text contains the new fixed sentence, before
  the inline-context fence;
- the policy deny test from requirement 5.
Write the tests first and record the RED run in your report.

**Docs:** update the comment near `notesPath` in `orchestrator/hooks.ts`, and any README or `docs/` sentence that
describes the notes files (grep for `notes/`), to say the orchestrator creates the directory. Do not edit
`docs/live-smoke-test.md` (a historical record) or anything under `docs/spec/**`.

**Acceptance:** node suite, tsc and the whole pytest suite green, 0 skipped; `git status` clean apart from your
commits. Commit as `feat: the orchestrator creates the notes directory before a notes role's session; the
instruction says it exists`.
