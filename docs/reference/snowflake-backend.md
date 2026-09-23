# The Snowflake backend: validating and deploying on a real account

**Nothing in this repository has run against a real Snowflake account.** Everything on this page is
code with fake-backed tests: `tests/fake_snowflake.py` is a DuckDB-backed stand-in patched in as
`snowflake.connector` (plus a Snowpark session double built on the Local Testing Framework), so the
tests prove which statements the pipeline sends, in which order, through which connection — and
that those statements compute on the DuckDB double what the local path computes. What a real
account does with them is not proven; §7 lists every piece that has never been exercised. The
first run on the company's account is the test.

Every validator keeps DuckDB as its default. `--backend snowflake` is selected only explicitly and
is run by a **human**: the orchestrator never passes a Snowflake option to anything it runs or asks
an agent to run, and nothing Snowflake is imported or constructed on the default path
(`tests/test_snowflake_validators.py::test_the_duckdb_default_is_unchanged`). The orchestrator's
policy denies an agent `--root`, `--backend`, `--connection` and `--sandbox-database` in every
script call, argparse abbreviations included (`script-root` / `script-backend` in
`orchestrator/policy.ts`; the production procedure is `docs/handoff-production.md` §2).

## 1. Named connections only

The pipeline reaches an account through the **name** of an entry in your own `connections.toml` and
nothing else: `snowflake.connector.connect(connection_name=NAME)` and
`Session.builder.config("connection_name", NAME).create()` (`scripts/lib/snowflake_conn.py`). The
name comes from `--connection NAME`, else the environment variable `MIG_SNOWFLAKE_CONNECTION`, else
the run stops with a usage error (exit 2) before anything connects. The connector's own
default-connection setting is deliberately not consulted: which account a run touches is always an
explicit choice.

- No password, token or private key is ever accepted as an argument: every script's argparse
  refuses `--password` (and any other option it does not define), and `SnowflakeBackend(password=…)`
  raises `passwords are never passed as arguments; use a named connection`. Any connection
  parameter other than the name is refused the same way.
- Nothing credential-shaped is ever written to this repository, a report or a log. Every error text
  that comes back from the connector, a Snowpark session or dbt goes through
  `snowflake_conn.redact` before it reaches `validation*.json`, a dbt log or stderr, and a wrapped
  error is never chained to the unredacted original. After a credential key (`password`, `passwd`,
  `pwd`, `passcode`, `passphrase`, `token`, `secret`, `private_key…`, written `k=v`, `k: v` or
  `"k": "v"`) a quoted value is masked up to its closing quote and an unquoted one to the END OF ITS
  LINE — a passphrase or a key path may contain spaces, and a connector message may carry anything
  after it; other lines are untouched. PEM private-key blocks are removed.
- Every object name the pipeline builds from workflow data — a mapping's `logical`, a tool id, a
  golden set, a contract's `table` — must be a plain upper-case identifier (`[A-Z_][A-Z0-9_$]*`, the
  rule intake already applies to a `logical`); a validator checks all of them before it opens a
  backend on either engine, and refuses anything else as a usage error (exit 2) that names the value,
  so no such value can rewrite a statement.

Your `connections.toml` lives on your machine, outside every repository (the connector reads
`~/.snowflake/connections.toml` by default, or the directory `SNOWFLAKE_HOME` names — see the
connector's documentation for your OS). Use key-pair or SSO authentication; never a password in any
file. Two illustrative entries (names and values are placeholders):

```toml
[migration_sandbox]                  # key pair: no prompt, suitable for repeated validation runs
account = "<org>-<account>"
user = "<SERVICE_USER>"
role = "MIGRATION_VALIDATOR"
warehouse = "MIGRATION_WH"
authenticator = "SNOWFLAKE_JWT"
private_key_file = "C:\\Users\\<you>\\.snowflake\\keys\\migration_rsa_key.p8"

[migration_deploy_sso]               # SSO through the browser
account = "<org>-<account>"
user = "<you>@<company>"
role = "MIGRATION_DEPLOYER"
warehouse = "MIGRATION_WH"
authenticator = "externalbrowser"
```

A validation opens one connection per fresh sandbox (two for the first golden set, one per further
set) and, for a Snowpark segment, one Snowpark session per run; with SSO, check the connector's
ID-token caching before a long run so it does not prompt every time.

## 2. The sandbox

A validation on `--backend snowflake` runs in ONE sandbox database you name with
`--sandbox-database DB` (or `MIG_SANDBOX_DATABASE`). `scripts/lib/snowflake_sandbox.py`'s
`SnowflakeSandbox.fresh()` opens a connection by name, runs `USE DATABASE DB`, and replaces the
run's schemas with `CREATE OR REPLACE SCHEMA`:

| Schema | Holds |
|---|---|
| `DB.MIG_GOLDEN` | the raw golden inputs, `<WF>_<SET>_IN_<tool id>` |
| `DB.MIG_GOLDEN_<WF>_<SET>` | the golden view schema: one view per source under its logical name (the procedures' `SRC_SCHEMA`) |
| `DB.MIG_WORK` | work streams, targets (with their `targets_before` state), the procedures under test, dbt's models |
| `DB.MIG_COMPARE` | each golden output, loaded for `compare.py` |

`CREATE OR REPLACE SCHEMA` **destroys** whatever those schemas held. So a database is accepted as a
sandbox only when `orchestrator.config.json` (read from `--root`) lists it under
`policy.sandboxDatabases`; any other name — or a name that is not a plain upper-case identifier, or
no `orchestrator.config.json` at all — is a usage error (exit 2) raised before any connection is
opened. Add your sandbox database to that list before the first run. (The same list also bounds the
databases the orchestrator lets an agent's SQL reference.)

**One validation at a time per sandbox database.** `MIG_WORK` (and every other sandbox schema) is
shared by everything that runs in the database, and each fresh sandbox replaces them — nothing
locks the database, so two validations in the same one destroy each other's state. Use a second
sandbox database (listed in the policy) to run two at once.

The judged run and the idempotency re-run of the first golden set share the sandbox database, so a
validator snapshots the first run's outputs (`lib.validation.snapshot`) before the second fresh
sandbox replaces them, and compares the two as row multisets exactly as the local path does.

## 3. What each validator does on `--backend snowflake`

The report is the local report: the same `compare.py`, the same keys, `credits` still `null` (no
credit usage is read back).

- **`validate_segment.py`** — per run a fresh sandbox; the golden set loaded by
  `load_golden.load_set(…, database=DB)`; upstream golden intermediates as locally; then
  `SnowflakeBackend.call_procedure`: the segment's `proc.sql` exactly as written (the
  `CREATE OR REPLACE PROCEDURE`), then `CALL MIG_WORK.<WF>_<SEG>(%s, %s, %s, %s, %s)` with
  `DB, MIG_GOLDEN_<WF>_<SET>, DB, MIG_WORK, validate_<wf>_<seg>_<set>` (contract C4's order); every
  output judged in the sandbox. A failed `CALL` is a domain FAIL (exit 1), as a local `ProcError` is.
- **`validate_snowpark.py`** — per run a session from `snowflake_conn.snowpark_session(NAME)`; the
  sandbox schemas replaced through that session (`USE DATABASE DB`, `CREATE OR REPLACE SCHEMA …`),
  the golden set saved into it; `proc.py`'s `run()` called; the outputs read back and judged
  locally with `compare.py`, as the local path does.
- **`validate_dbt.py`** — per golden set a fresh sandbox loaded as above, then
  `run_dbt("run", …, target="snowflake", vars={"src_schema": "MIG_GOLDEN_<WF>_<SET>",
  "tgt_schema": "MIG_WORK"}, duckdb_path=None, extra_env={"SNOWFLAKE_DATABASE": DB})` — the
  profile's `snowflake` output, the schemas unflattened — and the models read back through
  `SnowflakeBackend`. dbt does not read `connections.toml`: the profile reads `SNOWFLAKE_ACCOUNT`,
  `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_AUTHENTICATOR` (default
  `externalbrowser`) and `SNOWFLAKE_PRIVATE_KEY_PATH` from your environment (set them to match your
  connection entry); the validator sets `SNOWFLAKE_DATABASE` itself. `dbt-snowflake` must be
  installed beside the interpreter (it is not in `requirements.txt`); without it the run stops with
  a usage error naming it. dbt's output is redacted before the per-set log
  (`dbt/logs/validate_<set>.log`) is written.
- **`validate_workflow.py`** — per chain pass ONE fresh sandbox; every segment in wave order in it: a
  SQL segment's procedure created and CALLed, a Snowpark segment's `run()` in a session built from
  the same connection name, `USE DATABASE DB`, where its upstream tables already are. There is one
  engine, so nothing crosses a seam and `lib/handoff.py` is not used. A dbt workflow delegates to
  `validate_dbt.py` with the same options.

**Row order on the idempotency re-run.** Locally the re-run reverses the physical order of every
input (`rowid` on the DuckDB double; see `docs/reference/large-workflows.md`, "Row order"). A real
account has no physical order to reverse, so on `--backend snowflake` the re-run perturbs the only
order it controls: the raw golden inputs and every `targets_before` table are INSERTed in
**reversed** order (chain and dbt re-runs;
`tests/test_snowflake_validators.py::test_the_snowflake_rerun_loads_raw_inputs_in_reversed_insert_order`).
That is weaker than the local reversal: Snowflake promises no scan order either way — it may return
the rows in the same order whatever order they were inserted in — and nothing is re-presented
between two segments. An order-dependent procedure that the local chain catches may pass here;
treat the local result as the order check and the Snowflake run as the engine check.

## 4. `deploy.py`

```bash
.venv/Scripts/python.exe scripts/deploy.py <wf> --database DB --schema SCHEMA [--src-schema S] \
    [--connection NAME] [--execute] [--root .]
```

Dry run by default: it prints the deployment and connects to nothing. It refuses (exit 1) a
workflow whose `manifest.status.translate` is not `VALIDATED`, and one whose chain report
`validation_workflow.json` is missing or not `PASS*` — the chain test is the VALIDATED gate, and a
deployment never bypasses it.

- **Procedures**: `USE DATABASE DB`, `CREATE SCHEMA IF NOT EXISTS MIG_WORK`, every
  `segments/<seg>/proc.sql` in `order.json` order, `procs/master.sql`, each printed under a
  `-- workflows/<wf>/…` line; then a commented example
  `CALL MIG_WORK.<WF>_MASTER('<SRC_DB>', '<SRC_SCHEMA>', 'DB', 'SCHEMA', '<run id>')`. The source
  schema is the one `scripts/gen_source_views.py` generates (`<target_database>.MIG_SRC_<WF>`: one
  view per source under its logical name); `--src-schema S` fills it in (in `DB`). `--execute` runs
  exactly those statements through `SnowflakeBackend(connection_name=…)`, stopping at the first that
  fails (exit 1, the failing file named, the error redacted). It never replaces a schema:
  `CREATE SCHEMA IF NOT EXISTS` keeps whatever `MIG_WORK` already holds, and nothing is created in
  `MIG_GOLDEN`/`MIG_COMPARE`. The target schema `SCHEMA` must exist before the workflow runs.
  **`--execute` overwrites.** Every `proc.sql` and `master.sql` is a `CREATE OR REPLACE PROCEDURE`:
  re-deploying a workflow silently replaces the previous version of each of its procedures, and no
  history is kept. Review the dry run before `--execute`, and keep the previous DDL first — e.g.
  `SELECT GET_DDL('PROCEDURE', 'DB.MIG_WORK.<WF>_<SEG>(VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR)')`
  for each procedure the workflow already has.
- **dbt**: the one command `procs/README.md` gives, `<SRC>`/`<TGT>` filled from
  `--src-schema`/`--schema`, prefixed with `SNOWFLAKE_DATABASE=DB`. `--execute` (needs
  `--src-schema`) runs `run_dbt("run", …, target="snowflake", duckdb_path=None,
  extra_env={"SNOWFLAKE_DATABASE": DB})`; the other `SNOWFLAKE_*` variables come from your
  environment, and `dbt-snowflake` must be installed (exit 2 naming it otherwise).

## 5. The role and the grants

Illustrative names; an administrator adapts them. The validation role owns the schemas it creates
in the sandbox database, which gives it CREATE TABLE / VIEW / PROCEDURE in them.

```sql
-- validation (the sandbox database is listed in orchestrator.config.json policy.sandboxDatabases)
CREATE DATABASE MIGDB_SANDBOX;
CREATE ROLE MIGRATION_VALIDATOR;
GRANT USAGE ON WAREHOUSE MIGRATION_WH TO ROLE MIGRATION_VALIDATOR;
GRANT USAGE, CREATE SCHEMA ON DATABASE MIGDB_SANDBOX TO ROLE MIGRATION_VALIDATOR;

-- deployment and a real run (procedures run EXECUTE AS CALLER: the caller's role needs all of it)
CREATE ROLE MIGRATION_DEPLOYER;
GRANT USAGE ON WAREHOUSE MIGRATION_WH TO ROLE MIGRATION_DEPLOYER;
GRANT USAGE, CREATE SCHEMA ON DATABASE ANALYTICS TO ROLE MIGRATION_DEPLOYER;      -- MIG_WORK, MIG_SRC_<WF>
GRANT USAGE, CREATE TABLE ON SCHEMA ANALYTICS.CURATED TO ROLE MIGRATION_DEPLOYER; -- the targets
GRANT USAGE ON DATABASE <SOURCE_DB> TO ROLE MIGRATION_DEPLOYER;                    -- every mapped source
GRANT USAGE ON SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA> TO ROLE MIGRATION_DEPLOYER;
GRANT SELECT ON ALL TABLES IN SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA> TO ROLE MIGRATION_DEPLOYER;
```

As with the validation role, the deploying role gets CREATE PROCEDURE in `MIG_WORK` from owning the
schema — which it does when its own `CREATE SCHEMA IF NOT EXISTS MIG_WORK` created it. When another
role created `ANALYTICS.MIG_WORK`, grant it explicitly:
`GRANT USAGE, CREATE PROCEDURE ON SCHEMA ANALYTICS.MIG_WORK TO ROLE MIGRATION_DEPLOYER;` (and, to
replace a procedure another role created, ownership of that procedure). If a target already exists
and is owned by another role, the deploying role needs ownership (or the privileges the procedure's
`CREATE OR REPLACE TABLE` / `MERGE` / `INSERT` needs) on it. A
Snowpark segment deploys as a `LANGUAGE PYTHON` procedure with `PACKAGES = (…)`: the account's
Anaconda terms must have been accepted by an organisation administrator first.

## 6. The commands a human runs

```bash
# once: your connections.toml entry (§1); your sandbox database in orchestrator.config.json (§2)
export MIG_SNOWFLAKE_CONNECTION=migration_sandbox MIG_SANDBOX_DATABASE=MIGDB_SANDBOX

# validation on the account (same reports as the local run, written under workflows/<wf>/)
.venv/Scripts/python.exe scripts/validate_segment.py  <wf> <seg> --backend snowflake [--set normal]
.venv/Scripts/python.exe scripts/validate_snowpark.py <wf> <seg> --backend snowflake [--set normal]
.venv/Scripts/python.exe scripts/validate_workflow.py <wf> --backend snowflake [--set normal]
# dbt: also the profile's SNOWFLAKE_* variables (§3), and dbt-snowflake installed
.venv/Scripts/python.exe scripts/validate_dbt.py <wf> --backend snowflake [--set normal]

# deployment: print, read, then execute through a deploying connection
.venv/Scripts/python.exe scripts/gen_source_views.py <wf>        # the MIG_SRC_<WF> views (DDL text)
.venv/Scripts/python.exe scripts/deploy.py <wf> --database ANALYTICS --schema CURATED --src-schema MIG_SRC_<WF>
.venv/Scripts/python.exe scripts/deploy.py <wf> --database ANALYTICS --schema CURATED --src-schema MIG_SRC_<WF> \
    --connection migration_deploy_sso --execute
```

`--connection NAME` and `--sandbox-database DB` override the two variables per run. Exit codes are
every script's: 0 PASS / done, 1 FAIL / refused, 2 usage error or crash (with a redacted traceback).

## 7. What has never been exercised

Everything below is untested against a real account; the first run will tell.

- **Any connection at all.** `connect(connection_name=…)` and the Snowpark builder have only ever
  been called on fakes; no `connections.toml` has been read by this repository's tests.
- **The procedure bodies on Snowflake.** Contract C4's procedures have only run through
  `lib/proc_runner.py` on DuckDB. They are written in Snowflake's DOCUMENTED form (Task C4V): each
  mapped table's name is built with `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA ||
  '.<LOGICAL>';` (the arguments named without a colon, as Snowflake's expression syntax has them)
  and referenced as `IDENTIFIER(:<LOGICAL>_SRC)` — `IDENTIFIER(` takes one value in Snowflake's
  grammar, never an expression. Whether Snowflake Scripting accepts them as written — that `LET`
  form, and `ALTER SESSION SET …` inside a caller's-rights procedure — is unverified: the first
  real-account run (the hand-off's verification ladder) confirms it.
- **`procs/master.sql` through `cursor.execute`.** Its body is `$$`-delimited, like every segment
  procedure's (since Task W2's follow-up: `orchestrator/stages.ts` writes it that way, pinned by
  `orchestrator/test/fixtures/master_wf_0003.sql`). Creating it has only ever run on the fake; the
  first real-account run of `deploy.py --execute` confirms Snowflake accepts it as written. Its body
  is nothing but `CALL`s in wave order, which the local runner refuses by design (contract C4's
  segments never `CALL`) — `validate_workflow.py` runs the segments itself.
- **The deployed Snowpark wrapper.** No validator CALLs a `LANGUAGE PYTHON` wrapper `proc.sql`: the
  validators run `proc.py`'s `run()` in a session. Creating the wrapper (runtime version, packages,
  Anaconda terms) is exercised only by `deploy.py --execute`.
- **Type names.** On the fake, `DESCRIBE TABLE` answers with DuckDB's type names; a real account
  answers `NUMBER(38,0)`, `VARCHAR(16777216)`, `TIMESTAMP_NTZ(9)`, … `compare.py`'s type families
  list Snowflake's names, but no comparison has seen them from a real `DESCRIBE`.
- **Snowpark's `to_pandas()` on a real account.** The read-back of a Snowpark segment's outputs was
  written for the Local Testing Framework's pandas quirks; a real session's dtypes (e.g. a scaled
  `NUMBER`) may differ. A read-back that fails is reported as a missing table.
- **Identifier case.** Golden tables are created with quoted column names; a mixed-case golden field
  name would become a case-sensitive column on Snowflake, which an unquoted reference in a
  procedure would not find. DuckDB matches case-insensitively and cannot show this.
- **Loading.** Golden rows are inserted with the connector's client-side `executemany` binding
  (pyformat), date and time values as ISO text; no `PUT`/`COPY`, and no measurement of how that
  scales for a large golden set.
- **Missing-table detection.** `SnowflakeBackend.table_exists` treats error 002003 / SQLSTATE `42S02`
  from `DESCRIBE TABLE` as "does not exist"; a missing privilege may surface the same way.
- **dbt-snowflake.** Not installed here; the profile's `snowflake` output (Task A) has never run,
  including key-pair authentication through its environment variables.
- **Failures mid-run.** Every connector error inside a `CALL` becomes a domain FAIL in the report
  (as a local SQL error does), so a dropped connection mid-validation reads as a FAIL; read the
  report's `error` before handing it to a fixer.
- **Concurrency.** "One validation at a time per sandbox database" is documented, not enforced.
- **Agents.** The orchestrator's shell policy denies `--backend`, `--connection` and
  `--sandbox-database` (and `--root`) in every agent script call, in every spelling
  (`orchestrator/test/policy.test.ts`, "script-backend: --backend, --connection and
  --sandbox-database are denied in every spelling"). That is proven against the policy function
  only; no live session has tried one.
- **Credits.** Reports keep `credits: null`; nothing reads query history.
- **Order perturbation.** See §3: reversed INSERT order is a best-effort perturbation, weaker than
  the local reversal.
