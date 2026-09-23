# Docs pass (part 1 of 2) — the phase-2 rulings file and the plan annotations

**Worktree:** `.worktrees/p2-docs` on `wt/p2-docs`, from the integration head `a7aa602`. A fix wave is running
concurrently in `.worktrees/p2-ffix` (it touches `scripts/`, `orchestrator/`, `tests/`, `cookbook/dbt.md`,
`docs/reference/*`, `.github/agents/*`, `docs/handoff-production.md`, `docs/production-backlog.md`) — do NOT touch any
of those. You touch only the two files below.

The build of phase 2 recorded every coordinator decision in a git-ignored ledger. On a fresh clone that ledger does not
exist, so the decisions must be persisted, exactly as phase 1 did.

## 1. Create `docs/superpowers/rulings/2026-09-22-output-targets-phase2-rulings.md`

Source: the ledger `C:\Users\<user>\Desktop\Alteryx to Snowflake\.superpowers\sdd\2026-09-22-output-targets-phase2\progress.md`
(read it whole), plus the fix-round files beside it (`task-*-fix*.md`, `final-fix-wave.md`) where a ledger line points
at one for the detail of a ruling. Model: the phase-1 file `docs/superpowers/rulings/2026-09-22-output-targets-rulings.md`
— read it first and follow its shape exactly: a header paragraph (what this is; format "what — why — cost if wrong";
"a later ruling that says SUPERSEDES replaces the earlier one it names"; which plan and spec; the honesty paragraph that
nothing ran on Snowflake or Alteryx), then numbered rulings grouped under `## ` headings: Process (pre-flight scan,
concurrency cap, worktrees, deviations DV1-DV7, R-H1 …), then one section per task in the order the tasks were
built (A, W3, P3, B, E, D, C, P1, F, W1, W2, P2, W4, C4V, G, P4, H, N1, P5), then "Final whole-branch review — the
one fix wave". Each entry keeps the ledger's substance: what was decided, why, what it costs if wrong. Tighten wording,
never change meaning; where the ledger gives no cost, write "cost if wrong: not recorded". Controller-added tasks (C4V,
N1) say so and why they were added. The last section ends with the fix-wave rulings as listed in `final-fix-wave.md`
(the fix wave's own outcome is appended later by the controller — leave a final line
`<!-- fix-wave outcome: appended by the controller -->`).

Hygiene (tests enforce it; run them): no login name, no machine path (`C:\Users\…`, `/c/Users/…`), no temp-directory
path, and NOT the word "scratchpad" anywhere in the file — the ledger has all of these; describe such a place as "a
scratch directory outside the repository". No agent ids. Refer to review packages and reports by what they are, not by
file name.

## 2. Annotate `docs/superpowers/plans/2026-09-22-output-targets-phase2.md` where rulings superseded its text

Phase 1's plan shows the form (`docs/superpowers/plans/2026-09-22-output-targets-phase1.md`, lines ~599, ~894, ~1143):
a blockquote `> **Superseded during execution (rulings, see the phase's rulings file):** …` placed immediately before
the superseded text, saying what replaced it in one to three sentences, and naming the ruling number(s) in the new rulings
file. Do not rewrite or delete plan text. At minimum annotate: every DV ruling's target; the W1 reversal ruling(s);
Task C4V's replacement of the `IDENTIFIER(:A || '.' || …)` form wherever the plan shows it; Task W4's notes-sentence text
(N1 changed it: the orchestrator creates the notes directory and the instruction says so); anything P4's fix rounds
changed (GitHub opt-in, set_models routing every role); the P5 widths/diagram rulings only if the plan's P5 text is
contradicted. Also add a short note under the plan's header pointing at the rulings file and saying that C4V and N1 were
added during execution.

## Checks and commit
Run `"C:/Users/<user>/Desktop/Alteryx to Snowflake/.venv/Scripts/python.exe" -m pytest tests/test_committed_workflows.py tests/test_handoff_production.py`
from the worktree root (the machine-path, login-name and scratchpad scans live there). Commit as
`docs: phase-2 rulings persisted from the build ledger; the plan marks what they superseded`. Write a short report to
`task-DOCS-report.md` beside this brief (ruling count per section; which plan passages you annotated; anything in the
ledger you could not place).
