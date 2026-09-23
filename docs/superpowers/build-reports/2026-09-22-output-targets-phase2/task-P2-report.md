# Task P2 report — a real Snowflake backend for the validators, and `deploy.py`

Worktree `.worktrees/p2-P2`, branch `wt/p2-P2`, base `3baf4b8` (W1 merged). Nothing in this task connected to a
real Snowflake account, read a real `connections.toml` or touched the network: every Snowflake-path test drives
`tests/fake_snowflake.py`, a DuckDB-backed connector double patched in as `snowflake.connector`, and a Snowpark
session double built on the Local Testing Framework.

## What was built

**`scripts/lib/snowflake_conn.py`** (new). `CONNECTION_ENV = "MIG_SNOWFLAKE_CONNECTION"`,
`SANDBOX_DB_ENV = "MIG_SANDBOX_DATABASE"`; `ConnectionRefused(ValueError)`; `connection_name(explicit)` (argument,
else the env var, else `ConnectionRefused` naming `MIG_SNOWFLAKE_CONNECTION` and `connections.toml`; the name must
be a plain word `[A-Za-z0-9_][A-Za-z0-9_.-]*`, so `key=value` smuggling is refused; the connector's own
default-connection variable is NOT consulted); `connect(name)` = `snowflake.connector.connect(connection_name=name)`
and nothing else; `snowpark_session(name)` = `Session.builder.config("connection_name", name).create()` (errors
re-raised redacted, never chained); `redact(text)` (masks the value of any `…password…`/`passcode`/`passphrase`/
`…token…`/`secret`/`private_key…` key written `k=v`, `k: v`, `"k": "v"`, quoted or not, and every PEM
`PRIVATE KEY` block, cut-off blocks included); `scrubbed(exc) -> BackendError` (redacted `"<Type>: <text>"`,
`errno`/`sqlstate` kept); `require_dbt_snowflake()` (`DbtUnavailable` naming dbt-snowflake when
`dbt.adapters.snowflake` is not importable). Both modules are imported lazily: the DuckDB path never imports them.

**`scripts/lib/snowflake_sandbox.py`** (new). `SANDBOX_SCHEMAS = ("MIG_GOLDEN", "MIG_WORK", "MIG_COMPARE")`;
`ident(name)` (`^[A-Z_][A-Z0-9_$]*$` or `ValueError`); `SnowflakeSandbox(connection_name, database)` (database
`ident`-checked; nothing connects) with `fresh(extra_schemas=()) -> SnowflakeBackend`: extras `ident`-checked
BEFORE connecting, then a NEW connection by name, `USE DATABASE <db>`, `CREATE OR REPLACE SCHEMA <db>.<s>` for the
three and the extras (closed on failure). Controller ruling (1): `sandbox_database(root, explicit)` (argument, else
`MIG_SANDBOX_DATABASE`) must be a plain identifier AND listed in `<root>/orchestrator.config.json`
`policy.sandboxDatabases` (missing file → refused) — every refusal a `ConnectionRefused` usage error raised before
anything connects; `sandbox_for(root, connection, database)` resolves both names, opens nothing.

**`scripts/lib/backend.py`**. `SnowflakeBackend(connection_name=None, **refused)`: any credential keyword
(`password`, `passcode`, `token`, `private_key*`, `oauth_token`) → `BackendError("passwords are never passed as
arguments; use a named connection")`; any other keyword → `BackendError` (a name is the only way in); then the
connector import check (the existing `test_snowflake_backend_fails_clearly_without_connector` still passes), then
`snowflake_conn.connection_name` / `connect`. Every connector call goes through one `_cursor_call`; any exception
becomes a `BackendError` with `redact`ed text plus the statement context, raised `from None` (the unredacted
original is never chained, so no traceback prints it), `errno`/`sqlstate` kept. `call_procedure(proc_sql, args)`:
`parse_proc` (a `ProcError` for a non-C4 text or a missing argument, before anything is sent), executes the DDL
verbatim, then `CALL <name>(%s, …)` with the args in the procedure's parameter order (= C4's
`SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID`). `load_table`'s `executemany` goes through the same wrapper.
`table_exists` returns `False` on error 002003 / SQLSTATE `42S02` from `DESCRIBE TABLE` (it used to raise for a
missing table on a real account). The class docstring no longer claims only "never run"; it says what the tests
drive it through.

**`scripts/load_golden.py`**. `load_set(…, *, database=SANDBOX_DB, reverse=False)`: the view schema, the
`targets_before` tables and the returned `SRC_DB`/`TGT_DB` use `database`; `reverse` inserts every raw input and
every `targets_before` table in reversed row order (ruling 2). Defaults are the old behaviour byte for byte.

**`scripts/lib/validation.py`**. `actual_table(…, database=SANDBOX_DB)`; `BACKENDS = ("duckdb", "snowflake")`;
`add_backend_args(parser)` (`--backend`, `--connection NAME`, `--sandbox-database DB` — no credential option exists,
so argparse refuses `--password`); `snowflake_target(repo, backend, connection, sandbox_database)` → `None` for
duckdb (a Snowflake option WITHOUT `--backend snowflake` is a `ValueError` usage error), else the checked, unopened
`SnowflakeSandbox` (lazy import); `snapshot(backend, tables)` / `diverging_tables(first, second, tables)` (the
idempotency comparison, taken from the first run BEFORE the second run's fresh sandbox replaces it);
`error_text(backend, text)` / `print_crash(backend)` (redacted on the Snowflake path, untouched locally).

**The four validators** (each: `build_parser()`, `--backend duckdb|snowflake`, `--connection`,
`--sandbox-database`; the sandbox/policy check runs right after the stale-report clearing, before any
prerequisite and before anything connects; usage-error text and crash tracebacks redacted on the Snowflake path):

- `validate_segment.py`: `_run_one_set(…, backend_factory=DuckDBBackend, database=SANDBOX_DB)`; for Snowflake the
  factory is `partial(sandbox.fresh, [golden_view_schema(wf, set)])`; `_load_and_run` calls
  `backend.call_procedure` for a `SnowflakeBackend`, `run_proc` otherwise; `load_set(…, database=db)`,
  `actual_table(…, database=db)`; idempotency compares `snapshot`s (first taken before the second `fresh()`).
- `validate_snowpark.py`: `load_set_snowpark(…, *, database=SANDBOX_DB)`; `_prepare_schemas(session, db, schemas)`
  (`USE DATABASE`, then `session.sql("CREATE OR REPLACE SCHEMA …").collect()`, schemas `ident`-checked);
  `_prepare_snowflake_run` (session from `snowflake_conn.snowpark_session(name)` per run, golden set and
  intermediates loaded in the sandbox; usage errors stay usage errors, anything else re-raised redacted); a
  `run()` failure's report text is redacted; the first run's outputs are snapshotted (`_snapshot`, a `Counter` per
  table) before the second run is prepared. Read-back and judging are unchanged.
- `validate_dbt.py`: `_run_project_on_snowflake` — `sandbox.fresh([view schema])`, `load_set(…, database=db,
  reverse=<re-run>)`, `run_dbt("run", project, vars={"src_schema": golden_view_schema(…), "tgt_schema":
  "MIG_WORK"}, duckdb_path=None, target="snowflake", extra_env={"SNOWFLAKE_DATABASE": db})`; the `DbtResult`'s
  output and messages redacted, the per-set log written from the redacted output; tables read through the live
  `SnowflakeBackend` (`_reading` opens a DuckDB file or passes a live backend through); `require_dbt_snowflake()`
  before anything runs; `model_relation(…, database)`. Still writes `validation_workflow*.json` in the same call.
- `validate_workflow.py`: `BACKENDS = v.BACKENDS`; `_run_chain_on_snowflake` — ONE `fresh()` per chain pass, SQL
  segments through `call_procedure` (`ProcError`/`BackendError` → `ChainError`), a Snowpark segment in
  `snowflake_conn.snowpark_session(name)` with `USE DATABASE <db>` only (its upstream tables are already there; no
  hand-off; a `run()` failure → `ChainError` with redacted text); `_idempotency_on_snowflake` snapshots before the
  re-run, whose `load_set` INSERTs the raw inputs and `targets_before` reversed (ruling 2); the `_present`/`_reverse`
  machinery is DuckDB-only. A dbt workflow delegates with the same three options.

**`scripts/deploy.py`** (new). `deploy.py <wf> --database DB --schema SCHEMA [--src-schema S] [--connection NAME]
[--execute] [--root .]`. Dry run by default (prints, connects to nothing). Refuses (exit 1) when
`status.translate != "VALIDATED"`, and — ruling (4) — when `validation_workflow.json` is missing or not `PASS*`.
Procedures: `USE DATABASE DB;`, `CREATE SCHEMA IF NOT EXISTS MIG_WORK;`, every `segments/<seg>/proc.sql` in
`order.json` order and `procs/master.sql`, each under `-- workflows/<wf>/…`, then
`-- CALL MIG_WORK.<WF>_MASTER('<SRC_DB>', '<SRC_SCHEMA>', 'DB', 'SCHEMA', '<run id>');` (`--src-schema S` → `'DB', 'S'`).
`--execute` runs exactly those statements (texts verbatim) through `SnowflakeBackend(connection_name=…)`, stops at
the first failure (exit 1, file named, redacted), closes the connection; a connection that cannot be opened is a
one-line redacted exit 2. dbt: the `dbt run` line of `procs/README.md` with `<SRC>`/`<TGT>` filled, printed as
`SNOWFLAKE_DATABASE=DB dbt run …`; `--execute` needs `--src-schema`, checks `require_dbt_snowflake()` (exit 2
naming dbt-snowflake), runs `run_dbt("run", project, vars=…, target="snowflake", duckdb_path=None,
extra_env={"SNOWFLAKE_DATABASE": DB})`; a failed run is exit 1 with a redacted tail. Names are `ident`-checked
(exit 2); `--password` is refused by argparse.

**Docs.** `docs/reference/snowflake-backend.md` (new): named connections and redaction, two placeholder
`connections.toml` entries (key pair, SSO), the sandbox layout and ruling (1), "one validation at a time per
sandbox database", what each validator does on Snowflake, the reversed-INSERT re-run and why it is weaker,
`deploy.py`, the role and grants, every command, and §7 "What has never been exercised".
`docs/reference/large-workflows.md`: the `--backend` sentence names `snowflake`, and one new paragraph "Row order on
`--backend snowflake`" (ruling 2) right after "Row order" — two localized edits (W2 also owns this file this wave).

## Tests (written first; RED below)

- `tests/fake_snowflake.py` (new): `FakeConnectorModule` (one account = one shared in-memory DuckDB; `connect`
  records kwargs; every cursor statement + params recorded verbatim; `USE DATABASE` per connection; schema DDL
  (`OR REPLACE` drops with everything in it, procedures included); `CREATE PROCEDURE` stored by three-part name
  (schema must exist); `CALL` runs the stored text through `run_proc` with the bound params (statements inside
  unrecorded); `DESCRIBE TABLE` from `information_schema.columns` in the connector's row shape, 002003 for a
  missing table; everything else `%s`→`?`, two-part names qualified with the current database (no database →
  error 90105, as Snowflake), `DuckDBBackend.translate`, no implicit schema creation; `fail[<substring>]` injects a
  connector error). `FakeSession(connection_name, account=None)`: a real local-testing session underneath, `sql()`
  recorded; bridged to an account, `table(name).to_pandas()` and `create_dataframe(…).write.mode("overwrite")
  .save_as_table(name)` read/write that account (see Design decisions).
- `tests/test_snowflake_conn.py` (13): the brief's four, plus: a connection name that is not a plain name is
  refused; `snowpark_session` builds from `config("connection_name", NAME)` only; a connector error is redacted,
  unchained, `errno` kept; the backend opens by name (env var) and runs statements, `table_exists` false on 2003;
  `call_procedure` sends the DDL then `CALL …(%s×5)` in C4 order whatever the dict order, a missing argument is a
  `ProcError`, a failing CALL a redacted `BackendError`; `ident`; `fresh()` statements exactly and a replaced
  schema loses its table, bad extras refused before connecting; the policy (listed / not listed / not an identifier
  / env vars / no config file) with no connection opened; `require_dbt_snowflake`.
- `tests/test_snowflake_validators.py` (15): the brief's six, plus: a sandbox database outside the policy (and a
  lower-case one) refused with exit 2 before anything connects, for all four validators (stale chain report gone);
  Snowflake usage errors (unprepared workflow, no connection name, Snowflake options without `--backend snowflake`)
  exit 2 and connect nothing; a connector error in a CALL is a redacted domain FAIL (exit 1); a Snowflake crash
  prints a redacted traceback (exit 2); a Snowpark segment that raises in a Snowflake chain is a redacted `boundary`
  at that segment; ruling (2)'s test: the chain re-run's INSERTs of the raw inputs are the first pass's reversed.
- `tests/test_deploy.py` (13): the brief's five, plus: missing / FAIL chain report refused (exit 1, nothing
  connects); usage errors (unprepared workflow, injected / lower-case identifiers, `A.B` source schema,
  `--password`, a missing `proc.sql`) exit 2; a statement failing on execute is exit 1, file named, redacted,
  stopped there, connection closed; a connection that cannot be opened is a one-line redacted exit 2; the example
  CALL uses `--src-schema`; dbt `--execute` goes through `run_dbt(target="snowflake")` exactly, and a failed run
  is exit 1 redacted; dbt `--execute` names dbt-snowflake when missing and requires `--src-schema`.

### TDD evidence

RED (tests and fake written, no implementation; `wip:` commit `62f8cce`):
```
$ .venv/Scripts/python.exe -m pytest tests/test_snowflake_conn.py tests/test_snowflake_validators.py tests/test_deploy.py
E   ImportError: cannot import name 'snowflake_conn' from 'lib' (…/scripts/lib/__init__.py)     (×2)
E   ModuleNotFoundError: No module named 'deploy'
3 errors in 0.28s
```
Expected: none of the modules existed. Mid-way RED (after `snowflake_conn`/`snowflake_sandbox`/`validate_segment`
only): `module 'validate_snowpark' has no attribute 'build_parser'`; `argument --backend: invalid choice:
'snowflake' (choose from duckdb)` (validate_workflow); then both Snowpark tests `'snowflake.connector' is not a
package` — a fake problem, fixed in the fake (Design decisions). The two tests added after the first GREEN:
`test_a_connection_that_cannot_be_opened_is_exit_2_with_the_error_redacted` was run RED against `deploy.py` without
its `except BackendError` clause (`assert 'Traceback' not in "Traceback (…"`), then GREEN;
`test_a_snowpark_segment_that_raises_in_a_snowflake_chain_is_a_redacted_boundary` passed on first run (a pin of
behaviour already written).

GREEN: see "Verification".

## Brief corrections

1. **"one `CALL MIG_WORK.WF0001_SEG_01(…)`"** — the first golden set always runs the procedure twice (the judged
   run and the idempotency re-run, each in its own fresh sandbox), so exactly one CALL is impossible. The test
   asserts the recorded CALLs are exactly two, both that statement with those params, and that each pass starts
   with the five sandbox statements.
2. **"`snowpark_session` … called once per run"** — read as one session per procedure run (session-per-run, as the
   local path): `["sandbox", "sandbox"]` for one golden set.
3. **`parser.parse_args(["--password", "x"])`** alone would exit 2 for the missing positionals whatever argparse
   thought of `--password`. The test passes the positionals and the Snowflake options too and asserts
   `unrecognized arguments: --password` (also `--token`, `--private-key-file`), via each validator's new
   `build_parser()`.
4. **Deploy dry-run "exactly the three proc.sql files in order then master.sql"** — the printed script also carries
   `USE DATABASE`, `CREATE SCHEMA IF NOT EXISTS MIG_WORK` and the commented CALL the brief lists for procedures; the
   test pins the WHOLE output byte for byte (files in order, each under its `-- workflows/wf_0006/…` line).
5. **`test_a_dbt_workflow_prints_its_run_command`** — `workflows/wf_0007` built with `prepare_workflow` +
   `validate_dbt(["normal"])` + manifest `output_kind: dbt`, `status.translate: VALIDATED`, as allowed; the fixture
   also writes `procs/README.md` with the orchestrator's `dbtReadme` text (copied from `stages.ts`), since only the
   orchestrator writes it. `wf_0006`'s fixture runs `validate_workflow` on the copy: the committed tree predates W1
   and has no `validation_workflow.json`, which ruling (4) makes mandatory.

## Design decisions not spelled out in the brief

- **One engine in the chain test needs a bridged session double.** A plain local-testing `FakeSession` has its own
  catalog: the Snowpark segment of `wf_0006` could not see `seg_01`'s table in the fake account, and its output could
  not reach `seg_03`. `FakeSession(name, account=fake)` bridges exactly the two I/O calls a migrated procedure uses
  (`table().to_pandas()` reads the account; `create_dataframe(…).write.mode("overwrite").save_as_table()` writes it,
  typed through `types_map.snowpark_to_alteryx` → `alteryx_to_snowflake`, never through `lib.handoff`); anything
  else raises. So the chain test really runs seg_01 → seg_02 → seg_03 in one fake account and PASSes.
- **The fake keeps the real connector's `__path__` and delegates unknown attributes to it**, and imports the real
  `snowflake.connector` and `snowflake.snowpark` before any test patches: Snowpark imports connector submodules
  lazily, and a non-package stand-in made that fail. `connect` is only ever the fake's.
- **`fresh()` opens a new connection each time** (mirrors a fresh `DuckDBBackend()` per run; the caller closes it).
  Consequence documented: a validation opens one connection per fresh sandbox and one Snowpark session per run.
- **The idempotency snapshot is taken before the second run on BOTH paths** (`snapshot`/`diverging_tables`,
  `validate_snowpark._snapshot`): the local result is identical (two independent in-memory backends/sessions), and
  one code path serves both. The existing suites pin the local reports.
- **`load_set(…, reverse=)`** is the seam for ruling (2) (not in the brief's interface list); the chain and dbt
  re-runs on Snowflake use it; `validate_segment`/`validate_snowpark` never reversed locally and do not on
  Snowflake either (their reports must equal the local ones).
- **Any connector exception becomes a `BackendError`**, not only `snowflake.connector.errors.Error`: the redaction
  guarantee must not depend on the exception class. Consequence documented in §7: a dropped connection inside a
  CALL reads as a domain FAIL.
- **A Snowflake option without `--backend snowflake` is a usage error**, so nobody believes a local run happened on
  the account.
- **deploy's `--src-schema` is one identifier in `--database`** (the `MIG_SRC_<WF>` schema `gen_source_views.py`
  generates lives in the target database); `A.B` is refused.
- **deploy exit codes**: a missing chain report is exit 1 (refused), like a FAIL one — the workflow exists, it is
  just not deployable.

## Verification

On the tree committed as `e075fdf` (identical to the tree the suites ran on):

```
$ .venv/Scripts/python.exe -m pytest tests/test_snowflake_conn.py tests/test_snowflake_validators.py tests/test_deploy.py \
    tests/test_validate_segment.py tests/test_validate_segment_fix_round_1.py tests/test_validate_snowpark.py \
    tests/test_validate_dbt.py tests/test_validate_workflow.py tests/test_backend.py tests/test_load_golden.py
250 passed in 132.77s (0:02:12)
$ .venv/Scripts/python.exe -m pytest
1617 passed in 390.82s (0:06:30)
```
Baseline 1576 passed / 0 skipped; +41 new tests (13 + 15 + 13); 0 skipped; output pristine (no warnings line).
No TypeScript file changed (node/tsc not re-run).

## Files changed

`scripts/lib/snowflake_conn.py` (new), `scripts/lib/snowflake_sandbox.py` (new), `scripts/deploy.py` (new),
`scripts/lib/backend.py`, `scripts/lib/validation.py`, `scripts/load_golden.py`, `scripts/validate_segment.py`,
`scripts/validate_snowpark.py`, `scripts/validate_dbt.py`, `scripts/validate_workflow.py`,
`tests/fake_snowflake.py` (new), `tests/test_snowflake_conn.py` (new), `tests/test_snowflake_validators.py` (new),
`tests/test_deploy.py` (new), `docs/reference/snowflake-backend.md` (new), `docs/reference/large-workflows.md`
(two localized edits; outside the brief's file list, required by ruling 2).

## Self-review findings

- Hand-off hygiene: the one path in a committed file is `C:\\Users\\<you>\\…` (a placeholder the scan admits); no
  login name, no scratch pointer (the full suite's three scans ran on the final tree).
- `compare.py` untouched; no agent file, allow-list or orchestrator file touched (ruling 6).
- DuckDB path: no report changes (the existing validator suites pass unchanged), and
  `test_the_duckdb_default_is_unchanged` proves in a subprocess that `validate_segment`'s default run imports no
  `snowflake.*` submodule and no `lib.snowflake_*` module (the bare `snowflake` namespace is pre-loaded by the venv's
  snowpark `.pth`, verified).
- Fixed during self-review: `deploy.py` first required a connection name for a dbt `--execute` (dbt reads the
  profile's env vars, not a connection name); a dead stub class in a test; a mis-merged `except` line.

## Concerns

1. **Agents are not denied the Snowflake flags.** `orchestrator/policy.ts` lets the validator role pass any flag token
   to `validate_segment.py`/`validate_snowpark.py`/`validate_dbt.py` and denies only `--root`. On a machine with a
   `connections.toml` entry and a listed sandbox database, an agent adding `--backend snowflake --connection X`
   would reach the account. Ruling (6) said "add nothing to agent allow-lists" (done), but a DENY for `--backend`,
   `--connection`, `--sandbox-database` belongs in the policy (W2 owns `policy.ts` this wave). Documented in
   `snowflake-backend.md` §7.
2. **`procs/master.sql` has no `$$` around its `BEGIN … END;` body.** Snowflake documents string-literal delimiters
   as required for Snowflake Scripting in some clients; `deploy.py --execute` sends it verbatim and may fail there on
   a real account (the orchestrator writes master.sql; not changed here). Documented.
3. **C4's `IDENTIFIER(:A || '.' || :B || '.X')`** has only ever been folded by `proc_runner.bind`; whether Snowflake
   Scripting accepts a concatenation inside `IDENTIFIER` is unverified and is the first thing a real CALL will test.
4. `docs/reference/large-workflows.md` is also W2's this wave: my two edits are localized (the `--backend` sentence
   and one new paragraph after "Row order").
5. The chain test's bridge is the fake's, not Snowpark's: it proves the Snowflake chain wires a Snowpark segment
   into the same sandbox without a hand-off; it cannot prove real Snowpark's `to_pandas()` dtypes (documented).

## Things G and P4 must know

**Commands a human runs** (all documented in `docs/reference/snowflake-backend.md` §6):
```bash
# once: an entry in YOUR connections.toml (key pair or SSO, never a password); the sandbox database listed in
# orchestrator.config.json policy.sandboxDatabases (the default list is ["MIGDB"])
export MIG_SNOWFLAKE_CONNECTION=migration_sandbox MIG_SANDBOX_DATABASE=MIGDB_SANDBOX
.venv/Scripts/python.exe scripts/validate_segment.py  <wf> <seg> --backend snowflake [--set normal]
.venv/Scripts/python.exe scripts/validate_snowpark.py <wf> <seg> --backend snowflake [--set normal]
.venv/Scripts/python.exe scripts/validate_workflow.py <wf> --backend snowflake [--set normal]
# dbt also needs dbt-snowflake installed and the profile's SNOWFLAKE_ACCOUNT/USER/ROLE/WAREHOUSE/AUTHENTICATOR/
# PRIVATE_KEY_PATH in the environment (the validator sets SNOWFLAKE_DATABASE itself)
.venv/Scripts/python.exe scripts/validate_dbt.py <wf> --backend snowflake [--set normal]
.venv/Scripts/python.exe scripts/gen_source_views.py <wf>
.venv/Scripts/python.exe scripts/deploy.py <wf> --database ANALYTICS --schema CURATED --src-schema MIG_SRC_<WF>
.venv/Scripts/python.exe scripts/deploy.py <wf> --database ANALYTICS --schema CURATED --src-schema MIG_SRC_<WF> \
    --connection <deploying entry> --execute
```
`--connection`/`--sandbox-database` override the two variables. One validation at a time per sandbox database.

**Grants** (§5 of the doc, illustrative names): validation role — `USAGE` on the warehouse, `USAGE, CREATE SCHEMA`
on the sandbox database (it then owns the schemas it replaces, hence CREATE TABLE/VIEW/PROCEDURE in them).
Deploy/run role (procedures are `EXECUTE AS CALLER`) — `USAGE` on the warehouse, `USAGE, CREATE SCHEMA` on the
target database (`MIG_WORK`, `MIG_SRC_<WF>`), `USAGE, CREATE TABLE` on the target schema, `USAGE` + `SELECT` on
every mapped source; ownership (or MERGE/INSERT privileges) on pre-existing targets; the Anaconda terms accepted
by an org admin for a Snowpark segment's `LANGUAGE PYTHON` wrapper.

**What was never exercised** (§7 of the doc): any real connection or `connections.toml`; the C4 procedure bodies
on Snowflake (`IDENTIFIER(concat)`, `ALTER SESSION` in a caller's-rights procedure); `master.sql` without `$$`
through `cursor.execute`; creating the Snowpark wrapper (no validator CALLs it — validators run `proc.py`);
real `DESCRIBE TABLE` type names reaching `compare.py`; real Snowpark `to_pandas()` dtypes; mixed-case quoted
golden column names; client-side `executemany` loading at scale; `table_exists`'s 002003 test vs a missing
privilege; dbt-snowflake and the profile's `snowflake` output (key pair via env vars); connection failures
mid-CALL reading as FAIL; concurrency (documented, not enforced); credits (`null`); the reversed-INSERT order
perturbation (weaker than the local reversal).

**For G:** `deploy.py` accepts a workflow only with `status.translate: VALIDATED` AND a `PASS*`
`validation_workflow.json` — commit it for every VALIDATED workflow (W1 already says G's run writes it). A dbt
workflow also needs its `procs/README.md` committed for `deploy.py` to print its command. `tests/test_deploy.py`
builds `wf_0007` itself and regenerates `wf_0006`'s chain report on a copy; once G commits both, those fixtures
could read the committed trees instead (optional; the tests do not depend on the committed state).

**For P4:** link `docs/reference/snowflake-backend.md` from the hand-off guide; first-week backlog candidates —
the policy deny for `--backend`/`--connection`/`--sandbox-database` (concern 1), `$$` around `master.sql`'s body
(concern 2), creating and listing a sandbox database, installing dbt-snowflake, the Anaconda terms, and the first
real `validate_segment --backend snowflake` on `wf_0001` as the smoke test of everything in §7.

## Commits

`e075fdf` feat: --backend snowflake for every validator through named connections only; deploy.py prints or
executes a validated workflow's DDL. (The `wip:` commits made along the way — `62f8cce` tests (RED), `1daf802`
validators, `3ed7ca2` deploy.py, `b0ae1fc` docs + the deploy connection-failure fix — were squashed into it with
`git reset --soft 3baf4b8`; the tree is the one the suites ran on.)

## Fix round 1

Rulings in `task-P2-fix1.md` (review of `e075fdf`). Every item was tested first; `wip:` commit `cfe3821` held the failing
tests, then everything was squashed into one commit (see "Commit" below).

### What changed

- **C1: `redact` masks an unquoted secret to the end of its line** (`scripts/lib/snowflake_conn.py`). The key list is
  now `password`, `passwd`, `pwd`, `passcode`, `passphrase`, `token`, `secret`, `private_key…` (as a substring:
  `SNOWFLAKE_PASSWORD`, `private_key_file_pwd`, `oauth_token` all match). After the key (`k=v`, `k: v`, `"k": "v"`),
  a quoted value is masked up to its closing quote, as before. An UNQUOTED value is masked up to the end of its line
  (`[^\r\n]*`), so a space, `,`, `;` or `&` no longer ends it. The separator may no longer run over a newline
  (`[ \t]*`), so other lines are untouched. PEM blocks are handled as before. A consequence in `backend.py`:
  `SnowflakeBackend._error` used to append its context ("cannot open the named connection 'prod'") AFTER the
  connector text. On the key's line that context would now be masked, so it goes before as `lead=`. The statement
  context already starts on its own line. Every other composed message (`ChainError`, deploy's "deploy stopped at
  …", "dbt run exited …", `scrubbed`) already puts the connector text last.
- **C2: every name built from workflow data is checked as an identifier, on both backends, before anything runs.**
  - `lib/backend.py` gains `ident(name, what)` (`^[A-Z_][A-Z0-9_$]*$`, the rule intake's `_LOGICAL_RE` already
    applies to a `logical`) and `qualified(name, what)` (`SCHEMA.OBJECT` or `DB.SCHEMA.OBJECT` of plain identifiers).
    Both raise a `ValueError` that names the value. They live in `lib/backend.py` so the DuckDB path uses them
    without importing a `lib.snowflake_*` module (ruling 3's isolation test still passes).
    `snowflake_sandbox.ident` is now that same function.
  - `load_golden.load_set` composes and checks every name before it loads anything (`_names`): the database, the
    golden view schema, every `<WF>_<SET>_IN_<tool id>`, and every `logical` (source and output).
    `check_names(repo, wf, sets, database=…)` does the same with nothing created.
    `load_intermediate` checks the contract `table` it is given.
  - `lib.validation.actual_table` checks a target's `logical` (`ident`) and a work output's `table` (`qualified`).
    `check_contract_names(wf, seg, contract, database)` checks every output's actual table and every upstream
    input's `table`.
  - Each validator runs `check_contract_names` and `check_names` in its prerequisites, before a backend, sandbox,
    session or dbt run exists. These are `validate_segment`, `validate_snowpark`, `validate_dbt._prerequisites`
    (every segment) and `validate_workflow._prerequisites` (every segment). A bad name is a usage error (exit 2).
    Nothing is executed on either backend and no report is written.
  - Backstops in `SnowflakeBackend`:
    - `load_table`, `create_view` (both names) and `table_columns`/`table_exists` check with `qualified`.
    - `call_procedure` refuses a procedure name that is not plain identifiers with a `ProcError`, before anything is
      sent. The name is part of the artefact under test, so it is a domain FAIL like any other C4 violation.
    - `validate_snowpark.load_set_snowpark` and `_load_intermediates` check the names they pass to `save_as_table`.
  - Column types. Both backends' `load_table` now use one `column_definitions(fields)`. It refuses a `size`/`scale`
    that is not an integer, because they are spliced into `VARCHAR(<size>)` / `NUMBER(<size>,<scale>)`. Column names
    stay quoted, with any `"` doubled.
- **I1: nothing changed.** `proc_runner.py` is untouched.
- **I2** (`docs/reference/snowflake-backend.md` §4): "**`--execute` overwrites.**" Every `proc.sql`/`master.sql` is a
  `CREATE OR REPLACE PROCEDURE`, so a re-deploy silently replaces the previous version and keeps no history. The doc
  says to review the dry run first, and to keep the previous DDL
  (`SELECT GET_DDL('PROCEDURE', 'DB.MIG_WORK.<WF>_<SEG>(VARCHAR, …)')`).
- **M1** (§5): the deploying role gets CREATE PROCEDURE in `MIG_WORK` by owning the schema when its own
  `CREATE SCHEMA IF NOT EXISTS` created it. Otherwise it needs
  `GRANT USAGE, CREATE PROCEDURE ON SCHEMA ANALYTICS.MIG_WORK TO ROLE MIGRATION_DEPLOYER;`, plus ownership of any
  procedure another role created. §1 now states the end-of-line redaction rule and the identifier rule.
- **Coverage: the chain report is the same locally and on the fake.**
  `test_the_chain_report_on_snowflake_equals_the_local_one` compares `validation_workflow.json` and
  `validation_workflow.normal.json` for wf_0006, all keys except `runtime_ms`. The Snowpark segment runs through the
  bridged session. This test passed on first run: it pins behaviour, it does not fix anything.
  **dbt gets no such test, on purpose.** On the fake, dbt-snowflake is played by a recorded `run_dbt` that writes each
  model's table FROM ITS GOLDEN FILE. Comparing that against the local run (real dbt-duckdb models) would compare
  golden data with the models' output, not the two backends. That the Snowflake dbt path judges, reports and writes
  the chain report the same way is already covered by `test_validate_dbt_on_snowflake_uses_the_snowflake_target`.
- **M2: no change**, as ruled.

### Every SQL-building site and how it is protected

On the Snowflake path, text sent through the connector or a Snowpark session:

| Site | What is spliced | Protection |
|---|---|---|
| `SnowflakeSandbox.fresh` | `USE DATABASE {db}`, `CREATE OR REPLACE SCHEMA {db}.{schema}` | `db` checked with `ident` at construction, after the policy check. The schemas are the constants plus the golden view schema, checked with `ident` before connecting |
| `SnowflakeBackend.load_table` | `CREATE OR REPLACE TABLE {fqn} ({columns})`, `INSERT INTO {fqn} VALUES (%s…)` | `qualified(fqn)`; columns from `column_definitions` (names quoted, `"` doubled; size and scale must be integers); values bound (`%s`) |
| `SnowflakeBackend.create_view` | `CREATE OR REPLACE VIEW {view} AS SELECT * FROM {target}` | `qualified` on both names |
| `SnowflakeBackend.table_columns` / `table_exists` | `DESCRIBE TABLE {fqn}` | `qualified(fqn)` |
| `SnowflakeBackend.call_procedure` | the procedure DDL, `CALL {name}(%s…)` | The DDL is the artefact under test and is sent verbatim, by design. `name` checked with `qualified`, else `ProcError`. Arguments bound |
| `lib.validation.ordered_rows` / `snapshot` | `SELECT * FROM {table} ORDER BY 1..n` | Callers pass only checked names: `actual_table` results, contract tables, chain relations |
| `compare.compare` (unchanged) | expected/actual table names; column names | Expected is `MIG_COMPARE.EXPECTED_<n>` / `CHAIN_EXPECTED_<n>` (constants and an int). Actual comes from `actual_table` (checked). Columns go through `compare._identifier`, which quotes any name that is not safe |
| `load_golden.load_set` | `MIG_GOLDEN.<WF>_<SET>_IN_<tool id>`, `{db}.MIG_GOLDEN_<WF>_<SET>.{logical}`, `{db}.MIG_WORK.{logical}` | Every component checked with `ident` in `_names` before anything is loaded, plus `check_names` in every validator's prerequisites |
| `load_golden.load_intermediate` | contract input `table` | `qualified`, plus `check_contract_names` beforehand |
| `validate_snowpark._prepare_schemas` | `session.sql("USE DATABASE {db}")`, `CREATE OR REPLACE SCHEMA {db}.{s}` | `db` checked with `ident`; schemas checked with `ident` (they were already) |
| `validate_snowpark.load_set_snowpark` / `_load_intermediates` | `save_as_table("{db}.{view schema}.{logical}")`, `"{db}.MIG_WORK.{logical}"`, the contract `table` | `ident` / `qualified` at the site, plus the prerequisites |
| `validate_snowpark` read-back | `session.table(actual)` | `actual_table` (checked) |
| `validate_workflow._run_snowpark_segment_on_snowflake` | `session.sql("USE DATABASE {db}")` | `db` checked with `ident` |
| `validate_dbt._run_project_on_snowflake` | `--vars {"src_schema": <view schema>, "tgt_schema": "MIG_WORK"}`, `SNOWFLAKE_DATABASE` | view schema checked (`check_names`); database checked with `ident`; relations from `model_relation` built out of checked `logical`/`table` |
| `deploy.py` | `USE DATABASE {DB}`, `CREATE SCHEMA IF NOT EXISTS MIG_WORK`, proc/master texts; the dbt command's `<SRC>`/`<TGT>`, `SNOWFLAKE_DATABASE={DB}` | `DB`, `--schema` and `--src-schema` checked with `ident`. The proc/master texts are the validated artefacts, sent verbatim by design. The example `CALL …<WF>_MASTER(…)` is a SQL comment; the workflow id is already restricted by `Repo` |

On the local (DuckDB / Local Testing) path, the same composed names come from the same checked functions:

| Site | What is spliced | Protection |
|---|---|---|
| `DuckDBBackend.load_table` / `create_view` | the names above | Composition sites checked as above; `column_definitions` for types. There is no separate backstop in `DuckDBBackend` itself: it is used everywhere with test names, and nothing it is given here comes unchecked |
| `validate_workflow._run_snowpark_segment` | `{SRC_DB}.{SRC_SCHEMA}.{logical}` (raw inputs, re-run) | `logical` from the mappings, checked with `ident` in `_prerequisites` |
| `validate_workflow` | `DROP TABLE IF EXISTS {fqn}` | `actual_table` (checked) |
| `validate_workflow._run_sql_segment` | `SELECT * FROM {fqn}`, `_reverse(fqn)` | contract input tables and actual tables (checked in `_prerequisites`) |
| `lib.validation.reverse_physical_order` / `_is_view` | `CREATE OR REPLACE TABLE {t} AS …`, `DROP VIEW {t}` / literals | Tables from `load_set`'s `loaded` list or the contract (checked). `_is_view` quotes its literals |
| `lib.handoff` | `SELECT * FROM {fqn}`, `save_as_table(fqn)`, `session.table(fqn)` | fqns from `actual_table` or contract inputs (checked in the chain's `_prerequisites`) |
| `validate_dbt` (local) | `--vars` from `local_vars(golden_view_schema(…))` | view schema checked (`check_names`) |

**Stream names** never reach SQL. They are file-name parts (`golden/intermediates/<seg>/<set>/<stream>.csv`), which
`Repo._validate_part` already restricts. **Workflow and segment ids** reach SQL only through `wf_token` in composed
names, which are checked as above (a hyphenated id makes an invalid identifier and is refused). They also appear in
`RUN_ID`, which is always a bound value (Snowflake `%s`) or a quoted literal (`proc_runner.bind`).
`load_golden.py`'s own CLI keeps its old classification: a bad name is a `ValueError`, which that CLI has always
reported as exit 1 ("the mappings do not satisfy contract C6").

### Tests (written first; RED in `cfe3821`)

- `tests/test_snowflake_conn.py`:
  - `test_redact_masks_an_unquoted_secret_to_the_end_of_its_line`, one case each: the two ruled shapes, a Windows key
    path with spaces, a value followed by `,` / `;` / `&`, and `passwd:`.
  - `test_redact_touches_only_the_line_that_carries_the_secret` (LF and CRLF).
  - `test_the_backends_refuse_a_name_or_a_type_size_that_would_rewrite_their_sql`: six bad names against
    `load_table`/`create_view` (both arguments)/`table_columns`; a non-integer size on both backends; a quoted
    procedure name. No statement is sent.
  - `test_redact_masks_passwords_tokens_and_keys` was rewritten with one shape per line. Its old single-line input
    expected `user=ann` to survive after `password=hunter2 ` on the same line, which the C1 ruling now masks by design.
- `tests/test_snowflake_validators.py`:
  - `test_names_from_workflow_data_are_refused_before_anything_runs[duckdb|snowflake]`, on wf_0001, run through
    `validate_segment`. Cases: the reviewer's crafted `logical`, a lower-case, a dotted and a quoted source logical, a
    tool id `1 --`, an output logical `SALES_SUMMARY; DROP TABLE X`, and a contract target logical `sales_summary`.
    Each run exits 2 with the value in stderr and writes no report. No `DuckDBBackend` execute/query/load_table/
    create_view call is made (spied), and on the fake no statement and no connection.
  - `test_every_validator_checks_names_before_it_connects`: the crafted logical through `validate_snowpark`
    (Snowflake), `validate_workflow` (both backends) and `validate_dbt` (both backends; `run_dbt` fails the test if
    called). A crafted contract work table `MIG_WORK.WF0006_SEG_01_OUT; DROP TABLE X` through `validate_segment` and
    `validate_workflow` on both backends. Nothing is executed, no session is built, nothing connects.
  - `test_the_chain_report_on_snowflake_equals_the_local_one`.

RED (`cfe3821`: tests only, code unchanged):
```
FAILED tests/test_snowflake_conn.py::test_redact_masks_an_unquoted_secret_to_the_end_of_its_line[passphrase]
   AssertionError: ('Secret', 'connect failed: private_key_file_pwd=<redacted> Secret Passphrase end')
FAILED …[password]    ('secret end', 'password=<redacted> secret end')
FAILED …[key-path]    ('rsa key.p8', '250001: private_key_file=<redacted> keys\\rsa key.p8 could not be read')
FAILED …[comma]  ('cd', 'password=<redacted>,cd')      FAILED …[semicolon]  ('cd', 'token=<redacted>;cd')
FAILED …[ampersand]  ('ab', 'pwd=ab&cd next=1')        FAILED …[passwd]  ('ab', 'passwd: ab cd')
FAILED tests/test_snowflake_conn.py::test_redact_touches_only_the_line_that_carries_the_secret
FAILED tests/test_snowflake_conn.py::test_the_backends_refuse_a_name_or_a_type_size_that_would_rewrite_their_sql
   BackendError: FakeProgrammingError: Catalog Error: Schema with name SB__MIG_WORK does not exist!
   --- statement ---  CREATE OR REPLACE TABLE MIG_WORK.X AS SELECT 1 -- ("ID" NUMBER(38,0))     (the crafted name WAS sent)
FAILED tests/test_snowflake_validators.py::test_names_from_workflow_data_are_refused_before_anything_runs[duckdb]
   Failed: DID NOT RAISE SystemExit
FAILED …[snowflake]   Failed: DID NOT RAISE SystemExit
FAILED tests/test_snowflake_validators.py::test_every_validator_checks_names_before_it_connects
   Failed: DID NOT RAISE SystemExit
```
The rewritten single-shape redact test and the chain-equality test passed on the old code: both pin behaviour.

GREEN, on the tree committed below:
```
$ .venv/Scripts/python.exe -m pytest tests/test_snowflake_conn.py tests/test_snowflake_validators.py tests/test_deploy.py
54 passed in 16.37s
$ .venv/Scripts/python.exe -m pytest tests/test_snowflake_conn.py tests/test_snowflake_validators.py tests/test_deploy.py \
    tests/test_validate_segment.py tests/test_validate_segment_fix_round_1.py tests/test_validate_snowpark.py \
    tests/test_validate_dbt.py tests/test_validate_workflow.py tests/test_backend.py tests/test_load_golden.py
263 passed in 134.20s (0:02:14)
$ .venv/Scripts/python.exe -m pytest
1630 passed in 386.34s (0:06:26)          (1617 + 13; 0 skipped; no warnings)
```

### Files changed this round

`scripts/lib/backend.py`, `scripts/lib/snowflake_conn.py`, `scripts/lib/snowflake_sandbox.py`,
`scripts/lib/validation.py`, `scripts/load_golden.py`, `scripts/validate_segment.py`, `scripts/validate_snowpark.py`,
`scripts/validate_dbt.py`, `scripts/validate_workflow.py`, `docs/reference/snowflake-backend.md`,
`tests/test_snowflake_conn.py`, `tests/test_snowflake_validators.py`. `proc_runner.py` is not among them (I1).

### Things C4V must know

- `SnowflakeBackend.call_procedure` relies on four things from `parse_proc(proc_sql)`:
  - `.name`: spliced into `CALL {name}(…)`, and must now pass `qualified`, meaning `MIG_WORK.<NAME>` in plain
    upper-case identifiers.
  - `.params`, in declaration order: `CALL` binds its arguments in that order.
  - `parse_proc` raises `ProcError` for text outside C4, before anything is sent.
  - The DDL itself is sent verbatim. The body is never split or bound here, so a `LET` form needs no change on the
    Snowflake path, only in `parse_proc`'s acceptance of it.
- `tests/fake_snowflake.py` runs a stored procedure's `CALL` through `parse_proc` (`.language`, `.params`) and
  `run_proc(backend, text, dict(zip(params, bound values)))`. Once `proc_runner` accepts and runs the `LET … :=
  …; IDENTIFIER(:VAR)` form, the fake runs it too. The fake gives `run_proc` a backend with only `execute(sql)`.

### Concerns

- The C2 checks are deliberately strict: upper-case plain identifiers, the rule intake already enforces for `logical`.
  A hand-edited `mappings.yaml` or contract with a lower-case or quoted name now stops every validator with exit 2
  where the DuckDB path used to accept it. That is the ruling; it is noted because it changes what the local path
  accepts.
- The concerns from the first round still stand: the agent policy does not deny the Snowflake options, `master.sql`
  has no `$$`, and `IDENTIFIER(concat)` has never run on Snowflake (C4V addresses the last).

### Commit

`db8c199` wip: fix round 1 (C1, C2, I2, M1): redact an unquoted secret to the end of its line; every name composed
from workflow data identifier-checked before anything runs, on both backends; the deploy doc says --execute
overwrites; chain report equality local vs Snowflake. The round's own `wip:` commits (`cfe3821` RED, `c8437f4`
GREEN) were squashed into it with `git reset --soft e075fdf`. The tree is the one the suites ran on.

- **Follow-up (URL userinfo)** `1ffccda`: `redact` also masks the password in `scheme://user:pass@host`, keeping the scheme, the user and the host (`_URL_USERINFO`). Test `test_redact_masks_the_password_in_url_userinfo` covers a Snowflake URL, an https URL, a URL with no password (unchanged), a port that is not a password (unchanged) and a URL inside a longer message. It failed first on 3 of its 5 cases (the other two are the unchanged ones). After the change: `tests/test_snowflake_conn.py` 27 passed, Step 4 set 268 passed.
