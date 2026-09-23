# Phase-2 final whole-branch review — brief

**Range:** `c0c02a2` (main, end of phase 1) .. `fab6d60` (`feat/output-targets-phase2`, every phase-2 task merged except
P5, the README §1 Mermaid redraw, which is in flight and reviewed on its own). 64 commits; 465 files, of which 303 are
generated `workflows/**` trees and `samples/**` fixtures and the rest code, tests and docs (~22k lines).
**Checkout:** the detached worktree `C:\Users\<user>\Desktop\Alteryx to Snowflake\.worktrees\p2-final` at `fab6d60`.
Run everything there. It is read-only for you: no commits, no edits to tracked files, never `git stash` /
`checkout --` / `reset --hard`. Scratch files go under
`<scratchpad>\p2-final\`.

**Authority:** spec `docs/superpowers/specs/2026-09-22-output-targets-design.md`; plan
`docs/superpowers/plans/2026-09-22-output-targets-phase2.md` (its header, Global Constraints, scope additions P and W, and
the "Risks and mitigations" register; parts of it were superseded by rulings); the ledger
`.superpowers/sdd/2026-09-22-output-targets-phase2/progress.md` (in the MAIN checkout, not the worktree: every
`Ruling:` line there is binding and supersedes plan text it names — read it before judging a deviation). Carry-over
observations from the task reviews: `final-review-notes.md` beside this brief.

## What phase 2 built (one line each)
- A/B/C/D: a third output target, a dbt project (dbt-duckdb locally): `compile_check.py --target dbt`,
  `scripts/validate_dbt.py` (two fresh sandboxes, the second with reversed input order), sample `wf_0007`, orchestrator
  dispatch by `manifest.output_kind`, policy lane `dbt/**`.
- E: `cookbook/snowpark.md`, `cookbook/dbt.md` with a harness. F: `scripts/prompt_context.py` (fenced, escaped,
  budgeted inline context for intake/analyzer) + the interactive `output_target` question + phase-1 residual Snowpark refusals.
- W1: `scripts/validate_workflow.py` — the chained whole-workflow test (each segment consumes its upstream's ACTUAL
  output; typed DuckDB⇄Snowpark hand-off `scripts/lib/handoff.py`; `first_divergence`; one fixer round on a boundary
  divergence; `chain-drift` parks; VALIDATED only on a chain PASS).
- W2: `plan_batches.py` / batched analyzer / `stitch_analysis.py` / `check_seams.py` (`seam-mismatch` parks).
  W3: size-aware segmentation (`max_prompt_chars`). W4: notes files + a re-read after SDK compaction + metrics.
- C4V (controller-added): every SQL procedure uses Snowflake's DOCUMENTED form (`LET X_SRC VARCHAR := SRC_DB || '.' ||
  SRC_SCHEMA || '.X';` then `IDENTIFIER(:X_SRC)`), and the policy's SQL judge was hardened (flat bodies, exact header,
  RETURN literal, no body CALL, external COPY/STAGE/GET/PUT denied, `_SRC`/`_TGT` roles).
- P1: per-role model config, `scripts/dev/set_models.py`, `orchestrate.ts --check-models`. P2: `--backend snowflake`
  for the validators via named connections only, `scripts/deploy.py` (dry-run default; refuses unless VALIDATED with a
  chain PASS). P3: `scripts/survey_corpus.py`. P4: `docs/handoff-production.md` (an agent's guide to wiring GitHub
  Copilot Enterprise models, real Alteryx and real Snowflake) + `docs/production-backlog.md` + four production fixes
  (every role routes to `--default`; `--import-set` records the golden set; GitHub OPT-IN; `--help` in cp1252).
- G: the offline run refreshed for all seven samples (committed `workflows/`). H: a live test at 262k context
  (`docs/live-smoke-test.md` "Third live test"). N1: the orchestrator creates `workflows/<wf>/notes/`.

## How to review (in passes — say which passes you completed)
Each task was already reviewed in isolation, often twice. Your value is what no task review could see. Priority order:

1. **Seams between tasks.** Trace these end to end through the real code:
   - dbt × W1: does a dbt workflow get a chain test, or is its VALIDATED gate something else? Is that consistent in
     `stages.ts`, `deploy.py`, the docs and the committed `workflows/wf_0007`?
   - P2 × W1/B: `--backend snowflake` through `validate_workflow.py`, `validate_segment.py`, `validate_snowpark.py`,
     `validate_dbt.py`: every path uses the named connection, the sandbox database must be in
     `policy.sandboxDatabases`, credentials never reach a log, report, manifest or exception text.
   - C4V × P2 × deploy: `SnowflakeBackend.call_procedure`, `proc_runner.parse_proc` and `deploy.py` all accept the
     documented LET form and agree on the header; the master procedure's `$$` body.
   - W2 × F × W4 × N1 × policy: batched analyzer lanes, notes lanes, inline-context fence, compaction reminder.
   - P1 × runner: the model, `reasoningEffort` and `contextTier` each role's session really receives.
   - P4's B3 (GitHub opt-in): no path invokes `gh` when disabled.
2. **False PASS.** Anywhere a verdict, status or report can say PASS / VALIDATED / DONE without the comparison having
   run on the data it names (empty inputs, a vanished output, a skipped set, a crash-resume, a stale report file).
3. **Security of the agent sandbox.** `orchestrator/policy.ts` as a whole after W2, D, C4V, N1: any lane an agent can
   use to write outside its workflow, run SQL against a non-sandbox database, exfiltrate, or run an arbitrary
   interpreter. Try concrete tool calls against `decide()` (a probe script is fine; the SDK's real tool names are
   `view`, `create` {path, file_text}, `powershell` {command}, `grep`, `glob`, `ask_user`, `task`).
4. **Docs vs code.** `docs/handoff-production.md`, `docs/production-backlog.md`, README, `docs/reference/*`: every
   command, flag, path and claim is true of `fab6d60`; nothing says anything ran on real Snowflake or Alteryx.
   (`tests/test_handoff_production.py` pins names; you check meaning.)
5. **The carry-over notes** in `final-review-notes.md`: say for each whether it is load-bearing.

## Commands (from the worktree root)
- node: `"/c/Users/<user>/AppData/Local/Microsoft/WinGet/Links/fnm.exe" exec --using=22 npm.cmd test`
- tsc: `"/c/Users/<user>/AppData/Local/Microsoft/WinGet/Links/fnm.exe" exec --using=22 node.exe "C:/Users/<user>/Desktop/Alteryx to Snowflake/node_modules/typescript/bin/tsc" --noEmit -p .`
- pytest: `"C:/Users/<user>/Desktop/Alteryx to Snowflake/.venv/Scripts/python.exe" -m pytest <paths>` (the whole suite
  takes ~7 minutes and passed at fab6d60: 1934 passed, 0 skipped; node 331; tsc clean — rerun only what you need).
- Diffs: `git diff c0c02a2..fab6d60 -- <path>`; `git log --oneline c0c02a2..fab6d60`.

## Report
Write the full report to `C:\Users\<user>\Desktop\Alteryx to Snowflake\.superpowers\sdd\2026-09-22-output-targets-phase2\final-review-report.md`:
passes completed; findings ranked Critical / Important / Minor, each with file:line, a concrete failure scenario
(inputs → wrong result), and how you verified it (ran it, or read it); a verdict on each carry-over note; strengths;
an overall verdict (ready to merge / ready after fixes / not ready). Return only the verdict, the finding counts by
severity, and the one-line title of each Critical and Important finding.
