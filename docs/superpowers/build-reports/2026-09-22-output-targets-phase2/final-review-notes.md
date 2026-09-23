# Notes for the phase-2 final whole-branch review

Branch `feat/output-targets-phase2`, base `main@c0c02a2`. Spec `docs/superpowers/specs/2026-09-22-output-targets-design.md`;
plan `docs/superpowers/plans/2026-09-22-output-targets-phase2.md`; ledger `progress.md` beside this file.

## Carry-over observations from task reviews (not acted on; the final reviewer decides if any is load-bearing)
1. W3: `find_split` splits only at single-edge bridges, so a heavy node inside a diamond (two branches reconverging) cannot be
   isolated; the group is kept whole with an accurate "stays above its size cap" warning. Pre-existing algorithm; the character
   budget makes it newly reachable at realistic scale. Worth a sentence in `docs/reference/large-workflows.md` known limits.
2. W3: a group stuck over its cap gets the same warning once per outer iteration of step 6 (pre-existing duplication).
3. P3: `scripts/survey_corpus.py::_safe_extract` duplicates `scripts/parse.py::_unzip_packages`' zip-slip guard; a shared helper would keep future hardening in one place.
4. Scale: `segment.segment()` took ~29 s on a synthetic 3000-tool workflow (parse 0.04 s, classification < 0.1 s). A one-off per workflow, but a large real corpus survey multiplies it; P4 must list it in `docs/production-backlog.md`.
5. B: `validate_dbt._load_sandbox` leaves a partial sandbox file if `load_set` raises (git-ignored; swept by the next run).
6. B: `validation.combine`'s R-B1 disambiguation keys by tool_id, so two colliding outputs with the SAME tool_id (a contract-invariant violation) would still collide.
7. A (found by C): `compile_check_dbt` raises a raw FileNotFoundError when `intake/mappings.yaml` is missing; the CLI still exits 2, but the library call's contract is undocumented.
8. C: the broken wf_0007 `attainment_history.sql` (merge key narrowed) is non-deterministic on dbt-duckdb; only its stable fields are recorded.

9. docs/handoff-production.md Rung 3 "Before you start" reuses rung 1's run root, but §1.3's Verify says every run root built before §1.3 is stale (harmless: rung 3 makes no hosted calls). From the P4 fix-2 re-review.
10. (controller, from the P5 review) translateDbt's chain gate ignores needs_human (stages.ts ~1049) unlike chainCheck's M6 — fold into the fix wave.
