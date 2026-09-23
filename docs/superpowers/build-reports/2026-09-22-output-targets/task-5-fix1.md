# Task 5 — fix round 1 (rulings from the task review of fcb6285)

The reviewer reproduced everything runnable (segmentation, compile_check ×3, render byte-identity,
zero rule violations, all validators × all golden sets PASS + idempotent, both broken variants with
the recorded clusters, 181 / 1182 tests) and approved the task. Three items remain. Apply each.

## I1 — the README must disclose the script divergence (controller ruling R1)
`samples/wf_0006/README.md` (and `canned/segments/seg_02/translation_notes.md`, one line) must
state that the `.yxmd`'s Python-tool script is `tests/test_alteryx_sim_python.py::SCRIPT` with its
LAST THREE LINES changed (explicit output column list + `float64` casts) so the `empty` golden set
keeps its schema, and that Task 1's test constant was deliberately left unchanged.

## I2 — the NULL `CAP` / NULL `CANCELLED` notes are wrong; parity must be loud, not silent
The reviewer verified on both engines: (a) a NULL `CAP` never raises anywhere — `float(nan)` is
`nan`, `min(x, nan)` is `x`, so the cap is silently NOT applied, identically on both sides (parity
holds, the note is wrong about "aborts"); (b) a NULL `CANCELLED` is ASYMMETRIC: the Alteryx
simulator's nullable `boolean` column makes `bool(pd.NA)` raise, while the Snowpark Local Testing
Framework's `to_pandas()` yields `None` and `bool(None)` is `False`, so `proc.py` silently treats
the row as not cancelled. RULING: a migration must not turn a loud failure into silent output.
1. `proc.py`: before `bool(row["CANCELLED"])`, `if pd.isna(row["CANCELLED"]): raise ValueError(
   f"CANCELLED is NULL for {customer} {row['PERIOD']}")` (the Alteryx script fails on that row; the
   procedure fails the same way; `validate_snowpark` reports it as a FAIL if a golden set ever
   carries one). Re-render `proc.sql`; re-run the rules, compile_check and every validator; the
   broken `.py` variant is derived from the canned `proc.py`, so regenerate it the same way (keep its
   header) and confirm `broken.json`'s observed row is unchanged.
2. Correct the four notes (`seg_02/contract.json` note, `seg_02/translation_notes.md`,
   `canned/analysis.md`, `canned/docs/migration.md`): NULL `CAP` → the cap is silently not applied on
   both sides (parity holds; a data-quality risk to name in the migration doc); NULL `CANCELLED` →
   both fail loudly on that row; add that real Alteryx's typing of a NULL Bool field in the Python
   tool is UNVERIFIED (the simulator's nullable dtype is an assumption) — same register as spec §9.
3. Do NOT add a golden row with a NULL `CANCELLED` (the simulator cannot build it); say so in the
   README's golden-set section.

## M1 — no `__pycache__` under `samples/` (controller ruling on your concern 4)
`scripts/validate_snowpark.py::_load_module` imports the procedure in place, so the e2e suite writes
`samples/wf_0006/broken_sql/seg_02/__pycache__/`. Set `sys.dont_write_bytecode = True` around the
`exec_module` call and restore the previous value in a `finally`. RED-first test in
`tests/test_validate_snowpark.py`: after `validate_snowpark(..., proc_path=<tmp>/x.py)`, no
`__pycache__` exists beside `x.py`. Delete the stray directory from your worktree if present (it is
git-ignored, so nothing to commit there).

## Report
Append "## Fix round 1" to `task-5-report.md` (commit, what changed, the re-run validator results,
counts). Commit as `wip: fix round 1 (I1, I2, M1) — <what>`. No subagents. Never git stash /
checkout -- / reset --hard. Do not touch other worktrees.
