"""The per-run validation sandbox on a real account (plan Task P2).

A validation on `--backend snowflake` runs in ONE sandbox database the human names
(`--sandbox-database DB` or `MIG_SANDBOX_DATABASE`). `SnowflakeSandbox.fresh()` opens a connection
by name and replaces the run's schemas in that database -- `MIG_GOLDEN` (raw golden inputs),
`MIG_WORK` (work streams, targets, procedures), `MIG_COMPARE` (golden outputs for `compare.py`) and
the golden view schema `MIG_GOLDEN_<WF>_<SET>` -- with `CREATE OR REPLACE SCHEMA`, which DESTROYS
whatever they held. So a database is accepted only when `orchestrator.config.json`
(`policy.sandboxDatabases`, read from `--root`) lists it, and that is checked before any connection
is opened (`sandbox_for`). `MIG_WORK` is shared by everything in the database: run one validation
at a time per sandbox database.

Nothing here has run against a real Snowflake account (tests use `tests/fake_snowflake.py`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

from .backend import SnowflakeBackend, ident
from .snowflake_conn import SANDBOX_DB_ENV, ConnectionRefused, connection_name

#: The schemas every fresh sandbox replaces, before the extras (a golden view schema).
SANDBOX_SCHEMAS = ("MIG_GOLDEN", "MIG_WORK", "MIG_COMPARE")
POLICY_FILE = "orchestrator.config.json"

#: `ident(name)` -- a plain, unquoted, upper-case Snowflake identifier or `ValueError` -- is
#: `lib.backend.ident`, shared with every place that splices a name into SQL (fix round 1, C2).
__all__ = ["SANDBOX_SCHEMAS", "SnowflakeSandbox", "ident", "sandbox_database", "sandbox_for", "allowed_databases"]


class SnowflakeSandbox:
    """One sandbox database reached through one named connection. Nothing connects until `fresh()`."""

    def __init__(self, connection_name: str, database: str):
        self.connection_name = connection_name
        self.database = ident(database, "sandbox database")

    def fresh(self, extra_schemas: Sequence[str] = ()) -> SnowflakeBackend:
        """A new connection with `USE DATABASE <db>` and every sandbox schema (plus `extra_schemas`)
        replaced -- empty -- in it. The caller closes the backend."""
        schemas = list(dict.fromkeys([*SANDBOX_SCHEMAS,
                                      *(ident(schema, "sandbox schema") for schema in extra_schemas)]))
        backend = SnowflakeBackend(connection_name=self.connection_name)
        try:
            backend.execute(f"USE DATABASE {self.database}")
            for schema in schemas:
                backend.execute(f"CREATE OR REPLACE SCHEMA {self.database}.{schema}")
        except BaseException:
            backend.close()
            raise
        return backend


def allowed_databases(root: str | os.PathLike) -> list[str]:
    """`policy.sandboxDatabases` from `<root>/orchestrator.config.json`; `ConnectionRefused` if the
    file is missing or lists none (then no database may be used as a sandbox)."""
    path = Path(root) / POLICY_FILE
    if not path.is_file():
        raise ConnectionRefused(f"no {POLICY_FILE} at {path}: its policy.sandboxDatabases lists the only "
                                f"databases a validation may use as its sandbox")
    policy = (json.loads(path.read_text(encoding="utf-8")).get("policy") or {})
    return [str(database).upper() for database in policy.get("sandboxDatabases") or []]


def sandbox_database(root: str | os.PathLike, explicit: str | None) -> str:
    """The sandbox database: the argument, else `MIG_SANDBOX_DATABASE`; a plain identifier; and
    listed in the policy. Every refusal is a `ConnectionRefused` (a usage error) raised before
    anything connects."""
    database = explicit or os.environ.get(SANDBOX_DB_ENV)
    if not database:
        raise ConnectionRefused(f"no sandbox database named: pass --sandbox-database DB or set {SANDBOX_DB_ENV}")
    try:
        ident(database, "sandbox database")
    except ValueError as exc:
        raise ConnectionRefused(str(exc)) from None
    allowed = allowed_databases(root)
    if database not in allowed:
        raise ConnectionRefused(
            f"{database} is not a sandbox database: {POLICY_FILE} policy.sandboxDatabases lists "
            f"{', '.join(allowed) or 'none'}. A validation replaces the MIG_GOLDEN, MIG_WORK and MIG_COMPARE "
            f"schemas of the database it is given (CREATE OR REPLACE SCHEMA), so only a listed one is used")
    return database


def sandbox_for(root: str | os.PathLike, connection: str | None = None,
                database: str | None = None) -> SnowflakeSandbox:
    """The sandbox a `--backend snowflake` validation runs in, every name checked, nothing opened."""
    return SnowflakeSandbox(connection_name(connection), sandbox_database(root, database))
