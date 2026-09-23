# Build reports — 2026-09-22 output-targets plan, phase 2

Copied from the git-ignored build workspace when the branch was handed over, so the references in
the rulings file and the plan annotations resolve on a fresh clone.

- `ledger.md` — the coordinator's running ledger: every task event, review outcome, fix round,
  ruling and carried-over observation, in order. The rulings alone are in
  `../../rulings/2026-09-22-output-targets-phase2-rulings.md`.
- `task-<id>-report.md` — each implementer's own report (with its fix-round sections): what was
  built, RED/GREEN evidence, brief corrections, concerns. Task ids are the plan's letters (A–H,
  P1–P5, W1–W4) plus two tasks the coordinator added during execution: C4V (the documented
  `IDENTIFIER` form for every SQL procedure, with its own brief `task-C4V-brief.md`) and N1 (the
  orchestrator creates the notes directory, brief `task-N1-brief.md`). `task-DOCS-*` is the docs
  pass that wrote the rulings file.
- `task-<id>-fix<n>.md` — the coordinator's rulings handed to a fix round.
- `final-review-notes.md` — the observations carried into the final whole-branch review;
  `final-review-brief.md` and `final-review-report.md` — that review's brief and report;
  `final-fix-wave.md` — the rulings on it; `final-fix-report.md` — the fix wave's own report;
  `final-fix-rereview.md` — the adversarial re-review of the fix wave.

These are working records written for the coordinator, not polished documentation. Paths of the
PC the build ran on appear with the user name replaced by `<user>`, the session's scratch directory
by `<scratchpad>` (and its other session directories by `<session-temp>` / `<session-tasks>`), and
agent ids by `<agent>`. Nothing described in them ran on Snowflake or Alteryx: SQL procedures ran on
the DuckDB double, Snowpark procedures in the Snowpark Local Testing Framework, and dbt projects on
dbt-duckdb. The only real model sessions were the local BYOK live tests in `docs/live-smoke-test.md`.
