"""Print -- or, with --execute, run -- a VALIDATED workflow's deployment to Snowflake (plan Task P2).

    python scripts/deploy.py <wf_id> --database DB --schema SCHEMA [--src-schema S]
        [--connection NAME] [--execute] [--root .]

**Dry run by default.** Without `--execute` this prints the deployment and connects to nothing. A
procedures workflow deploys as `USE DATABASE DB`, `CREATE SCHEMA IF NOT EXISTS MIG_WORK`, every
`segments/<seg>/proc.sql` in `segments/order.json` order and `procs/master.sql` -- each printed
under a `-- workflows/<wf>/…` line naming its file -- then a commented example
`CALL MIG_WORK.<WF>_MASTER('<SRC_DB>', '<SRC_SCHEMA>', 'DB', 'SCHEMA', '<run id>')` (the source schema
is the one `scripts/gen_source_views.py` generates; `--src-schema` fills it in, in DB). A dbt workflow
deploys with the one command its `procs/README.md` gives, `<SRC>`/`<TGT>` filled from `--src-schema`
and `--schema`, run with `SNOWFLAKE_DATABASE=DB`.

**`--execute`** runs exactly those statements, in that order, through
`SnowflakeBackend(connection_name=…)` -- a NAMED connection (`--connection` or
`MIG_SNOWFLAKE_CONNECTION`), never a password -- stopping at the first that fails; for a dbt workflow
it runs `dbt_project.run_dbt("run", …, target="snowflake")` (dbt-snowflake must be installed beside
this interpreter). A deployment never touches a validation sandbox: it replaces no schema
(`CREATE SCHEMA IF NOT EXISTS` keeps whatever `MIG_WORK` already holds) and creates nothing in
`MIG_GOLDEN`/`MIG_COMPARE`.

**Only a VALIDATED workflow deploys.** `manifest.status.translate` must be `VALIDATED` and the chain
report `validation_workflow.json` (Task W1's gate) must exist with a `PASS*` verdict; otherwise the
workflow is refused (exit 1) before anything connects.

Exit codes: 0 printed / executed; 1 refused (not VALIDATED, chain not PASS) or a statement / dbt
run failed on the account; 2 a usage error (workflow not prepared, a file missing, a name that is
not a plain identifier, no connection name for `--execute`, dbt-snowflake missing, a connection
that cannot be opened) or any other crash. Every error text from the account is redacted.

Nothing here has run against a real Snowflake account: the tests drive `--execute` through a
DuckDB-backed fake connector (`tests/fake_snowflake.py`). See `docs/reference/snowflake-backend.md`.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback
from typing import Sequence

from lib import dbt_project, snowflake_conn
from lib.backend import BackendError, SnowflakeBackend
from lib.dbt_project import DbtUnavailable
from lib.io import read_json
from lib.paths import Repo, add_root_arg, wf_token
from lib.snowflake_sandbox import ident

WORK_SCHEMA = "MIG_WORK"
_DBT_COMMAND = re.compile(r"^dbt run .*$", re.MULTILINE)


class Refused(Exception):
    """The workflow may not be deployed, or the deployment failed on the account: exit 1."""


# --- what gets deployed -----------------------------------------------------------------------------


def _gate(repo: Repo, wf_id: str) -> dict:
    """The manifest of a workflow that may deploy; `Refused` for one that may not."""
    manifest_path = repo.wf(wf_id, "manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no manifest.json to deploy from: {manifest_path}")
    manifest = read_json(manifest_path)
    translate = (manifest.get("status") or {}).get("translate")
    if translate != "VALIDATED":
        raise Refused(f"status.translate is {translate!r}, not 'VALIDATED': only a validated workflow deploys")
    chain_path = repo.wf(wf_id, "validation_workflow.json")
    if not chain_path.is_file():
        raise Refused("no validation_workflow.json: the chain test (scripts/validate_workflow.py) is the "
                      "VALIDATED gate, and this workflow has no report from it")
    verdict = str(read_json(chain_path).get("verdict"))
    if not verdict.startswith("PASS"):
        raise Refused(f"validation_workflow.json says {verdict}: the chain did not pass")
    return manifest


def procedure_statements(repo: Repo, wf_id: str, database: str) -> list[tuple[str | None, str]]:
    """`(file label or None, statement)` in deployment order; `FileNotFoundError` for a missing file."""
    order_path = repo.wf(wf_id, "segments", "order.json")
    if not order_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no segments/order.json: {order_path}")
    statements: list[tuple[str | None, str]] = [
        (None, f"USE DATABASE {database}"), (None, f"CREATE SCHEMA IF NOT EXISTS {WORK_SCHEMA}")]
    for seg in [seg for wave in read_json(order_path) for seg in wave]:
        path = repo.seg(wf_id, seg, "proc.sql")
        if not path.is_file():
            raise FileNotFoundError(f"{wf_id}/{seg} has no proc.sql to deploy: {path}")
        statements.append((f"workflows/{wf_id}/segments/{seg}/proc.sql", path.read_text(encoding="utf-8")))
    master = repo.wf(wf_id, "procs", "master.sql")
    if not master.is_file():
        raise FileNotFoundError(f"{wf_id} has no procs/master.sql to deploy: {master}")
    statements.append((f"workflows/{wf_id}/procs/master.sql", master.read_text(encoding="utf-8")))
    return statements


def procedure_script(wf_id: str, statements: list[tuple[str | None, str]], database: str, schema: str,
                     src_schema: str | None, heading: str) -> str:
    lines = [f"-- deploy.py {wf_id}: {heading}"]
    for label, sql in statements:
        if label is None:
            lines.append(f"{sql};")
        else:
            lines.extend(["", f"-- {label}", sql.rstrip()])
    source = f"'{database}', '{src_schema}'" if src_schema else "'<SRC_DB>', '<SRC_SCHEMA>'"
    lines.extend(["", f"-- Run the workflow (targets land in {database}.{schema}; SRC is the schema of source views",
                  "-- scripts/gen_source_views.py generates):",
                  f"-- CALL {WORK_SCHEMA}.{wf_token(wf_id)}_MASTER({source}, '{database}', '{schema}', '<run id>');"])
    return "\n".join(lines) + "\n"


def dbt_command(repo: Repo, wf_id: str, src_schema: str | None, schema: str) -> str:
    """The `dbt run` line of `procs/README.md` with `<SRC>`/`<TGT>` filled in (`<SRC>` stays when no
    `--src-schema` was given)."""
    readme = repo.wf(wf_id, "procs", "README.md")
    if not readme.is_file():
        raise FileNotFoundError(f"{wf_id} is a dbt workflow with no procs/README.md (the orchestrator writes "
                                f"it once translate is VALIDATED): {readme}")
    match = _DBT_COMMAND.search(readme.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"{wf_id}: procs/README.md has no `dbt run` command line: {readme}")
    return match.group(0).strip().replace("<SRC>", src_schema or "<SRC>").replace("<TGT>", schema)


# --- running it ---------------------------------------------------------------------------------------


def _execute_procedures(statements: list[tuple[str | None, str]], connection: str) -> int:
    backend = SnowflakeBackend(connection_name=connection)   # BackendError: cannot open -> exit 2
    try:
        for label, sql in statements:
            try:
                backend.execute(sql)
            except BackendError as exc:
                raise Refused(f"deploy stopped at {label or sql}: {exc}") from None
    finally:
        backend.close()
    return len(statements)


def _execute_dbt(repo: Repo, wf_id: str, database: str, schema: str, src_schema: str | None) -> None:
    if not src_schema:
        raise ValueError("--src-schema is required to --execute a dbt workflow (the schema its sources "
                         "are read from, in --database)")
    project = dbt_project.project_dir(repo, wf_id)
    if not (project / "dbt_project.yml").is_file():
        raise FileNotFoundError(f"{wf_id} has no dbt project to deploy: {project / 'dbt_project.yml'}")
    snowflake_conn.require_dbt_snowflake()                    # DbtUnavailable naming it -> exit 2
    result = dbt_project.run_dbt("run", project, vars={"src_schema": src_schema, "tgt_schema": schema},
                                 target="snowflake", duckdb_path=None,
                                 extra_env={"SNOWFLAKE_DATABASE": database})
    if result.code != 0:
        raise Refused(f"dbt run exited {result.code}: {dbt_project.bounded(dbt_project.tail(result.output))}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0006")
    parser.add_argument("--database", required=True, metavar="DB", help="the database to deploy into")
    parser.add_argument("--schema", required=True, metavar="SCHEMA",
                        help="the schema the workflow's targets are written to (TGT_SCHEMA / dbt tgt_schema)")
    parser.add_argument("--src-schema", default=None, metavar="S",
                        help="the schema in DB its sources are read from (SRC_SCHEMA / dbt src_schema)")
    parser.add_argument("--connection", default=None, metavar="NAME",
                        help="--execute: the connections.toml entry to use (default: $MIG_SNOWFLAKE_CONNECTION)")
    parser.add_argument("--execute", action="store_true",
                        help="run the deployment on the account (default: print it and connect to nothing)")
    add_root_arg(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = Repo(args.root)
    try:
        database, schema = ident(args.database), ident(args.schema)
        src_schema = ident(args.src_schema) if args.src_schema is not None else None
        manifest = _gate(repo, args.wf_id)
        dry_run = "dry run -- printed only, nothing was executed"
        if manifest.get("output_kind") == "dbt":
            command = dbt_command(repo, args.wf_id, src_schema, schema)
            heading = "executing dbt run on the profile's snowflake output" if args.execute else dry_run
            print(f"# deploy.py {args.wf_id}: {heading}\n"
                  f"# workflows/{args.wf_id}/procs/README.md, with <SRC> and <TGT> filled in; the profile reads the\n"
                  f"# database from SNOWFLAKE_DATABASE:\n"
                  f"SNOWFLAKE_DATABASE={database} {command}")
            if args.execute:
                _execute_dbt(repo, args.wf_id, database, schema, src_schema)
                print(f"# dbt run completed on {database}.{schema}")
            return 0
        statements = procedure_statements(repo, args.wf_id, database)
        connection = snowflake_conn.connection_name(args.connection) if args.execute else None
        heading = f"executing through the named connection {connection!r}" if args.execute else dry_run
        sys.stdout.write(procedure_script(args.wf_id, statements, database, schema, src_schema, heading))
        if args.execute:
            count = _execute_procedures(statements, connection)
            print(f"-- executed {count} statements through the named connection {connection!r}")
        return 0
    except Refused as exc:
        print(f"{args.wf_id}: {snowflake_conn.redact(str(exc))}", file=sys.stderr)
        return 1
    except BackendError as exc:  # only opening the connection can get here: statements become Refused
        print(f"{args.wf_id}: {snowflake_conn.redact(str(exc))}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError, DbtUnavailable) as exc:
        parser.error(snowflake_conn.redact(str(exc)))       # exit 2: a usage error; nothing was deployed
    except Exception:            # noqa: BLE001 -- exit 2 is "anything unexpected"; never a bare traceback exit 1
        sys.stderr.write(snowflake_conn.redact(traceback.format_exc()))
        return 2


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
