# Task DOCS — report (part 1 of 2: rulings file + plan annotations)

## Ruling count per section (120 numbered rulings total)

| Section | Rulings |
|---|---|
| Process | 1-16 (16) |
| Task A | 17-20 (4) |
| Task W3 | 21-23 (3) |
| Task P3 | 24-26 (3) |
| Task B | 27-28 (2) |
| Task E | 29-30 (2) |
| Task D | 31-36 (6) |
| Task C | 37-40 (4) |
| Task P1 | 41-43 (3) |
| Task F | 44-46 (3) |
| Task W1 | 47-56 (10) |
| Task W2 | 57-59 (3) |
| Task P2 | 60-70 (11) |
| Task C4V | 71-78 (8) |
| Task W4 | 79-80 (2) |
| Task G | 81-82 (2) |
| Task P4 | 83-91 (9, plus one unnumbered scheduling ruling before B1) |
| Task H | 92-96 (5) |
| Task N1 | 97-99 (3) |
| Task P5 | 100-103 (4) |
| Final whole-branch review | 104-120 (17, incl. C1.1-C1.9, N-dbt, M1-M3, the hand-off-guide ruling and the parked carry-overs) |

Task sections follow the build order the brief specified: A, W3, P3, B, E, D, C, P1, F, W1, W2, P2, W4,
C4V, G, P4, H, N1, P5, then the final whole-branch review. C4V and N1 are labelled controller-added,
each with a one-line "why" under its section heading, per the brief.

The file ends with `<!-- fix-wave outcome: appended by the controller -->`, left for the controller to
fill in after the fix wave (`.worktrees/p2-ffix`, running concurrently) reports its own result.

## Plan passages annotated (`docs/superpowers/plans/2026-09-22-output-targets-phase2.md`)

Six `> **Superseded during execution (rulings, see the phase's rulings file):** …` blockquotes were
added, all as pure insertions (the diff is `+46` lines, `-0`; no plan text was rewritten or deleted):

1. A short note under the plan's own header, pointing at the new rulings file and naming C4V and N1 as
   controller-added tasks.
2. Immediately after the "Spike-forced deviations" table (DV1-DV7): the table's own "Spec text" column
   is the superseded text (the design spec itself is never edited); the note points at rulings 5-11.
3. Task A's `**Interfaces:**` heading, before the `PROJECT_FILES`/named-checks list: the final
   whole-branch review's Critical finding (dbt's closed-surface promise was not actually enforced) and
   the six new named checks the fix wave added, pointing at rulings C1.1-C1.9.
4. Task W1 Step 3, before `lib/handoff.py`'s description: the plan's own `validation.ordered_rows`
   (sorted-by-every-column) hand-off was superseded by physical-arrival-order-then-reversed, pointing at
   rulings 52, 53, 55 — this is "the W1 reversal ruling(s)" the brief asked for.
5. Task W1 Step 5, before the `translateDbt` sentence: the dbt path's missing `needs_human` check,
   closed by ruling N-dbt (ruling 115).
6. Task W4's `notesInstruction` sentence: superseded by Task N1's change (the orchestrator now creates
   the directory first, and the sentence says so), pointing at rulings 93 and 97-99.
7. Task P1, before the `set_models.py` CLI line: Task P4's B1 finding (deep-merge left five roles on
   the placeholder model) superseded the "sets every place consistently" claim, pointing at ruling 83.

(That is seven insertion points across six numbered items above — item 1 is the header note, items 2-7
are the six body blockquotes the diff shows.)

## What the brief's "at minimum" list did NOT get a plan annotation, and why

- **Task C4V's `IDENTIFIER(:A || '.' || …)` form "wherever the plan shows it":** searched the whole
  plan file for `IDENTIFIER`, for the three-part concatenation pattern, and for `SRC_DB`/`TGT_DB`
  literal text — the plan text never quotes a full SQL procedure containing that form (Task C's SQL
  procedures are described by shape and file list, not inlined as code). There is nothing to annotate.
- **P4's GitHub opt-in fix (B3):** searched the plan for `gh`, `GitHub`, `github`, `issue`, `pull
  request` — the plan never describes GitHub issue/PR creation at all; that behaviour predates this
  plan (it is already opt-in, fixed code, in the current `orchestrator/cli.ts`/`types.ts`/`stages.ts`)
  and Task P4 only discovered and fixed it while writing the hand-off guide. Nothing in this plan's own
  text asserted the old always-on behaviour, so there is no passage to mark superseded.
- **P5 widths/diagram rulings:** the brief said to annotate these "only if the plan's P5 text is
  contradicted." It is not: Step 2 already asked for the Snowpark/compile-check detail and the width
  reduction the review's fix round added — the review found the first draft hadn't fully delivered what
  the plan's own Step 2 asked for, then fixed it to match. That is an implementation gap closed against
  the plan, not the plan being wrong, so no annotation was added.

## Checks

`.venv/Scripts/python.exe -m pytest tests/test_committed_workflows.py tests/test_handoff_production.py`
from the worktree root: **105 passed**, 0 skipped (includes the three hand-off hygiene scans — machine
path, login name, scratchpad — run over every tracked file, both new/changed files included).

## Anything in the ledger this agent could not place

Nothing load-bearing. Two minor housekeeping lines in the ledger (the `.worktrees/p2-A` directory stuck
open by a process; a stray empty `tests/test_snowflake_conn.py` found and removed during Task P2's
merge) were left out of the rulings file — they are operational narration, not decisions on the
coordinator's own authority, and phase-1's rulings file has the same shape (decisions only).
