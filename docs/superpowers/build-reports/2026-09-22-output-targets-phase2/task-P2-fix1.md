# Task P2 — fix round 1 (rulings on the task review of e075fdf)

The review confirmed the architecture (policy check → ident() → connection order in every entry point, proven with
`fake.connect_calls == []`), zero credential-argument surface (incl. deploy.py and prefixes), the fake's fidelity, the
DuckDB-default import isolation (subprocess sys.modules test), and every controller ruling. Two Critical safety gaps and
a few smaller items remain. Apply all, RED-first with the exact shapes that exposed them.

## C1 — redact() must mask an unquoted secret that contains spaces (`scripts/lib/snowflake_conn.py:35-37`)
`redact("connect failed: private_key_file_pwd=My Secret Passphrase end")` leaks "Secret Passphrase end";
`password=hunter2 secret end` leaks "secret end". RULING: after a secret key (`password`, `passwd`, `pwd`, `token`,
`private_key…`, `passphrase`, `secret`, `authenticator` values that are not a mode word? — keep to the credential keys),
an UNQUOTED value is redacted to the end of the line (conservative: a connector message may carry anything after it);
quoted values keep their current handling; PEM blocks unchanged. Tests: the two shapes above, a Windows key path with a
space, a value followed by `,`/`;`/`&`, and a multi-line message where only the secret's line is affected.

## C2 — every name derived from workflow data is identifier-checked before it reaches SQL (both backends)
`logical` (from `intake/mappings.yaml`) and `tool_id` flow unchecked into object names at `scripts/load_golden.py:64,71,81`,
`scripts/lib/validation.py:144` (`actual_table`), `scripts/validate_snowpark.py:104,108` (`_save`) — the reviewer made a
crafted `logical` redefine a golden view (`… ORDERS AS SELECT ID AS X FROM MIG_WORK.LEFTOVER --`). RULING: validate every
composed identifier component with `snowflake_sandbox.ident()` (or a shared equivalent in `lib/`) at EVERY interpolation
site on BOTH backends — `logical`, `tool_id`-derived suffixes, stream names, workflow/segment ids, schema names — raising a
usage error (exit 2, nothing executed) that names the offending value. Grep the Snowflake path for every f-string/format
that builds SQL text and list each site in the report with how it is protected. Tests: the reviewer's crafted `logical`
on both backends (refused before any statement is sent/executed), a lower-case logical, a dotted logical, a quote.

## I1 — coupling with the C4V task (informational for you)
`SnowflakeBackend.call_procedure` uses `proc_runner.parse_proc`, whose scanner refuses `LET`. The parallel C4V task
changes `proc_runner` to accept the documented `LET <VAR> VARCHAR := …; IDENTIFIER(:<VAR>)` form; do not change
`proc_runner.py` yourself. If you rely on anything else in parse_proc's output, say so in "Things C4V must know".

## I2 — the deploy doc says plainly that --execute overwrites
`docs/reference/snowflake-backend.md` §4: one explicit sentence — `CREATE OR REPLACE PROCEDURE` means re-deploying a
workflow silently replaces the previous version of its procedures with no history kept; review the dry run before
`--execute`; and suggest the human keep the previous DDL (e.g. `GET_DDL`) before a redeploy.

## Minors
- M1 §5: the deployer paragraph says, as the validator paragraph does, that CREATE PROCEDURE in `MIG_WORK` comes from
  owning the schema, and names the explicit `GRANT CREATE PROCEDURE ON SCHEMA …` needed when another role created it.
- Coverage: add the local-vs-Snowflake report equality test (minus runtime) for the chain on wf_0006 (as done for
  validate_segment/validate_snowpark); for dbt, add it only if the fake makes it meaningful, else say why not.
- M2 (missing-in-both counts as diverging): no change.

## Report
Append "## Fix round 1" to `task-P2-report.md` (incl. the list of every SQL-building site and its protection).
Commit as `wip: fix round 1 (C1, C2, I2, M1) — <what>`. Same worktree and rules as before.
