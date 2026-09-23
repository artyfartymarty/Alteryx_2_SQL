# Task D — fix round 1 (the pre-existing gaps you found, plus the review's minors)

The review APPROVED Task D (no Critical, no Important) and confirmed G1–G3 are real with probes. Apply every item,
RED-first, then node, tsc, `tests/test_agents_config.py`, and the whole pytest suite.

## G1 — a write call's content is not a path (`orchestrator/policy.ts:63` `PATH_ARG_KEYS`)
`file_text` matches `/…file…/` and its CONTENT is judged as a path (probe: `create` with `path` in the dbt lane and
`file_text: "select 1 as ID"` → denied "translator may not write select 1 as id"). This would block every write in the
live test. RULING: the argument keys `file_text`, `content`, `new_str`, `old_str`, `text`, `insert_line` (and
`old_string`/`new_string` if the scanner sees them) are CONTENT and are never path-scanned; every other key, above all the
real `path`, stays scanned exactly as today. Tests: the probe above is allowed; a `create` whose `path` leaves the lane is
still denied even when its `file_text` names a lane path; an unknown key holding a path is still scanned.

## G2 — a workflow script may only name the session's own workflow (`decideShell`, policy.ts ~932-976)
`validate_dbt.py wf_0002` runs in a wf_0001 session. RULING: for every `scripts/*.py` on the allow-lists whose first
positional argument is a workflow id (`compile_check`, `validate_segment`, `validate_snowpark`, `validate_dbt`,
`render_snowpark`, `target_check`, and any other that takes `<wf_id>` first — read each argparse), the first bare
(non-flag) token after the script path must equal the session's workflow id, else deny with reason
`cross-workflow: <script> <token> in a <session wf> session`. The check fires only when a bare token is present at that
position (the reviewer's caveat: `scripts/compare.py` takes only `--flag value` arguments and must keep working). Tests:
the probe above denied; the same-workflow call allowed; a `compare.py --…` call unaffected.

## G3 — switching output kind removes the other kind's artefact (`stages.ts:859-861`)
The procedures branch writes `procs/master.sql` but leaves a stale `procs/README.md`; the dbt branch already removes
`master.sql`. RULING: the procedures branch removes `procs/README.md` (`rm … {force: true}`) before writing
`master.sql`. Test: a stale `README.md` is gone after a procedures run.

## Minors
- M1 `migrateDbt` (`stages.ts:673,757`): `failing` ("Failing models: …") is never cleared; clear it whenever an iteration
  ends in a compile failure or a reviewer BLOCK, so the fixer never reads a two-iteration-old model list. Test.
- M2 `DBT_EXECUTABLE` (`policy.ts:752`): also match `dbt.cmd` / `dbt.bat` so the specific dbt denial reason is used.
  Test.
- M3 `dbtModelName` (`stages.ts:639-643`): throw on an output with neither `table` nor `logical` (mirror
  `dbt_project.model_name`'s KeyError) instead of returning the string "undefined". Test.
- M4 (runner.ts replay duplication): leave as is.

## Report
Append "## Fix round 1" to `task-D-report.md`. Commit as `wip: fix round 1 (G1, G2, G3, M1-M3) — <what>`. Same worktree
and rules as before.
