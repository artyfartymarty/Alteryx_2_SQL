"""A DuckDB-backed Snowflake double for the `--backend snowflake` tests (plan Task P2).

Nothing in this repository has ever connected to a real Snowflake account. Every Snowflake-path
test patches `FakeConnectorModule` in as `snowflake.connector` (and, where a Snowpark session is
needed, `lib.snowflake_conn.snowpark_session` to build a `FakeSession`), so what the tests prove is
that the pipeline sends the statements it should, in the order it should, through a named
connection only -- and that those statements compute the same thing on the DuckDB double that the
local path computes. What a real account does with them is not proven here (see
`docs/reference/snowflake-backend.md`, "What has never been exercised").

`FakeConnectorModule` -- the `snowflake.connector` stand-in. `connect(**kwargs)` records `kwargs`
in `connect_calls` and returns a `FakeConnection`. One module is one ACCOUNT: every connection it
hands out shares one in-memory DuckDB, so a second connection sees -- and a `CREATE OR REPLACE
SCHEMA` on it destroys -- what the first created, as on a real account.

Every statement a cursor is given is recorded verbatim, with its params, in the module's
`statements` (`[(sql, params)]`; an `executemany` records its whole list of rows once). Then:

* `USE DATABASE X` sets the connection's current database (Snowflake keeps it per session);
* `CREATE [OR REPLACE] SCHEMA [IF NOT EXISTS] [DB.]S` creates -- or drops with everything in it and
  recreates -- DuckDB schema `DB__S` (the flattening `lib.backend.local_name` uses);
* `CREATE [OR REPLACE] PROCEDURE <name>(…)` stores the text under its three-part name (the schema
  must exist); `CALL <name>(%s, …)` runs the stored text through `lib.proc_runner.run_proc` with the
  bound params -- the statements inside a procedure run server-side, so they are NOT recorded;
* `DESCRIBE TABLE <name>` is answered from `information_schema.columns` in the connector's row shape
  (name, type, kind, null?, …), and a missing table raises errno 2003 like Snowflake;
* anything else: `%s` becomes `?`, every two-part table name is qualified with the current database
  (a session without one raises, as Snowflake does), and the statement is translated through
  `DuckDBBackend.translate` (sqlglot) and run on the shared DuckDB. Schemas are never created
  implicitly: writing into one that does not exist fails, as it would on Snowflake.

`FakeSession` -- the Snowpark stand-in: a real Local Testing Framework session underneath, a record
of the connection name it was built for, and `sql()` statements recorded instead of executed.
Bridged to a `FakeConnectorModule` (`account=`), a migrated procedure's two I/O calls --
`session.table(name).to_pandas()` and `session.create_dataframe(…).write.mode("overwrite")
.save_as_table(name)` -- read and write that account's tables instead, so a Snowpark segment in a
Snowflake chain shares ONE engine with the SQL segments, as it would on a real account.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import duckdb
import snowflake.connector as _real_connector   # the real package, cached BEFORE any test patches it:
import snowflake.snowpark  # noqa: F401          # Snowpark imports connector submodules as it loads
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from lib.backend import BackendError, DuckDBBackend
from lib.proc_runner import ProcError, parse_proc, run_proc
from lib.types_map import alteryx_to_snowflake, snowpark_to_alteryx

_NAME = r'("?[A-Za-z_][A-Za-z0-9_$]*"?(?:\."?[A-Za-z_][A-Za-z0-9_$]*"?){0,2})'
_USE_DATABASE = re.compile(r"^USE\s+DATABASE\s+" + _NAME + r"$", re.IGNORECASE)
_SCHEMA_DDL = re.compile(r"^CREATE\s+(?P<replace>OR\s+REPLACE\s+)?SCHEMA\s+(?P<ine>IF\s+NOT\s+EXISTS\s+)?"
                         + _NAME + r"$", re.IGNORECASE)
_CREATE_PROCEDURE = re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\s+" + _NAME + r"\s*\(",
                               re.IGNORECASE)
_CALL = re.compile(r"^CALL\s+" + _NAME + r"\s*\((?P<args>.*)\)$", re.IGNORECASE | re.DOTALL)
_DESCRIBE = re.compile(r"^DESC(?:RIBE)?\s+TABLE\s+" + _NAME + r"$", re.IGNORECASE)

_DESCRIBE_COLUMNS = ("name", "type", "kind", "null?", "default", "primary key", "unique key", "check",
                     "expression", "comment", "policy name", "privacy domain")


class FakeError(Exception):
    """`snowflake.connector.errors.Error`'s stand-in: a message, an `errno` and a `sqlstate`."""

    def __init__(self, message: str, errno: int | None = None, sqlstate: str | None = None):
        super().__init__(message)
        self.errno = errno
        self.sqlstate = sqlstate


class FakeProgrammingError(FakeError):
    """`snowflake.connector.errors.ProgrammingError`'s stand-in (compilation errors, missing objects)."""


def _missing(what: str) -> FakeProgrammingError:
    return FakeProgrammingError(f"SQL compilation error:\n{what} does not exist or not authorized.",
                                errno=2003, sqlstate="42S02")


def _unquote(part: str) -> str:
    return part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part.upper()


def _parts(name: str) -> list[str]:
    return [_unquote(part.strip()) for part in name.split(".")]


def _strip_comments(sql: str) -> str:
    """The statement without its leading `--` comment lines and trailing `;` (for classification only)."""
    lines = sql.strip().splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    return "\n".join(lines).strip().rstrip(";").strip()


class _Session:
    """What a statement runs in: the current database (a connection's, or a procedure caller's)."""

    def __init__(self, database: str | None = None):
        self.database = database


class FakeAccount:
    """The one engine every connection of a `FakeConnectorModule` shares."""

    def __init__(self):
        self.engine = DuckDBBackend()                   # translate() and the shared DuckDB
        self.procedures: dict[str, str] = {}
        self.fail: dict[str, str] = {}                  # substring of a statement -> error message

    # --- names ---

    def _database(self, session) -> str:
        if not session.database:
            raise FakeProgrammingError("Cannot perform operation. This session does not have a current "
                                       "database. Call 'USE DATABASE', or use a qualified name.",
                                       errno=90105, sqlstate="22000")
        return session.database

    def qualified(self, name: str, session) -> tuple[str, str, str]:
        parts = _parts(name)
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        if len(parts) == 2:
            return self._database(session), parts[0], parts[1]
        raise FakeProgrammingError(f"the fake qualifies two- and three-part names only: {name}")

    def _schema_exists(self, flat: str) -> bool:
        return bool(self.engine._con.execute(
            "SELECT 1 FROM information_schema.schemata WHERE upper(schema_name) = upper(?)", [flat]).fetchall())

    # --- statements ---

    def run(self, sql: str, session, params=None) -> tuple[list[tuple], list[tuple] | None]:
        for marker, message in self.fail.items():
            if marker in sql:
                raise FakeProgrammingError(message, errno=100132, sqlstate="P0000")
        text = _strip_comments(sql)
        if match := _USE_DATABASE.match(text):
            session.database = _parts(match.group(1))[0]
            return [], None
        if match := _SCHEMA_DDL.match(text):
            return self._schema(match, session)
        if match := _CREATE_PROCEDURE.match(text):
            database, schema, name = self.qualified(match.group(1), session)
            if not self._schema_exists(f"{database}__{schema}"):
                raise _missing(f"Schema '{database}.{schema}'")
            self.procedures[f"{database}.{schema}.{name}"] = sql
            return [(f"Function {name} successfully created.",)], [("status",)]
        if match := _CALL.match(text):
            return self._call(match, session, list(params or []))
        if match := _DESCRIBE.match(text):
            return self._describe(match.group(1), session)
        return self.run_generic(sql, session, params)

    def _schema(self, match, session):
        parts = _parts(match.group(3))
        database, schema = (parts[0], parts[1]) if len(parts) == 2 else (self._database(session), parts[0])
        flat = f"{database}__{schema}"
        con = self.engine._con
        if match.group("replace"):
            con.execute(f'DROP SCHEMA IF EXISTS "{flat}" CASCADE')
            prefix = f"{database}.{schema}.".upper()
            self.procedures = {key: text for key, text in self.procedures.items()
                               if not key.upper().startswith(prefix)}
            con.execute(f'CREATE SCHEMA "{flat}"')
        elif match.group("ine"):
            con.execute(f'CREATE SCHEMA IF NOT EXISTS "{flat}"')
        else:
            try:
                con.execute(f'CREATE SCHEMA "{flat}"')
            except duckdb.Error as exc:
                raise FakeProgrammingError(str(exc), errno=2002) from None
        return [(f"Schema {schema} successfully created.",)], [("status",)]

    def _call(self, match, session, params: list):
        database, schema, name = self.qualified(match.group(1), session)
        key = f"{database}.{schema}.{name}"
        text = self.procedures.get(key)
        if text is None:
            raise _missing(f"Unknown user-defined procedure {key}")
        try:
            proc = parse_proc(text)
            if proc.language != "SQL":
                raise FakeProgrammingError(f"the fake cannot run a {proc.language} procedure: {key}")
            if match.group("args").count("%s") != len(params):
                raise FakeProgrammingError(f"{len(params)} params bound to {match.group('args')!r}")
            run_proc(_ProcBackend(self, session), text, dict(zip(proc.params, params)))
        except (ProcError, BackendError, duckdb.Error) as exc:
            raise FakeProgrammingError(f"Uncaught exception of type 'STATEMENT_ERROR' on line 0 of "
                                       f"{key}: {exc}", errno=100132, sqlstate="P0000") from None
        return [("OK",)], [(name,)]

    def _describe(self, name: str, session):
        database, schema, table = self.qualified(name, session)
        rows = self.engine._con.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE upper(table_schema) = upper(?) AND upper(table_name) = upper(?) ORDER BY ordinal_position",
            [f"{database}__{schema}", table]).fetchall()
        if not rows:
            raise _missing(f"Table '{database}.{schema}.{table}'")
        return ([(column, str(kind).upper(), "COLUMN", "Y" if nullable == "YES" else "N", None, "N", "N",
                  None, None, None, None, None) for column, kind, nullable in rows],
                [(label,) for label in _DESCRIBE_COLUMNS])

    def to_duckdb(self, sql: str, session, *, bound: bool) -> str:
        if bound:
            sql = sql.replace("%s", "?")
        try:
            tree = sqlglot.parse_one(sql, read="snowflake")
        except SqlglotError as exc:
            raise FakeProgrammingError(f"SQL compilation error: {exc}", errno=1003, sqlstate="42000") from None
        for table in tree.find_all(exp.Table):
            if table.args.get("db") is not None and table.args.get("catalog") is None:
                table.set("catalog", exp.to_identifier(self._database(session)))
        try:
            return self.engine.translate(tree.sql(dialect="snowflake"))
        except BackendError as exc:
            raise FakeProgrammingError(f"SQL compilation error: {exc}", errno=1003, sqlstate="42000") from None

    def run_generic(self, sql: str, session, params=None):
        duck = self.to_duckdb(sql, session, bound=params is not None)
        try:
            cursor = self.engine._con.execute(duck, params) if params is not None else self.engine._con.execute(duck)
        except duckdb.Error as exc:
            text = str(exc)
            errno = 2003 if "does not exist" in text else 100000
            raise FakeProgrammingError(text, errno=errno, sqlstate="42S02" if errno == 2003 else "22000") from None
        description = [(column[0],) for column in cursor.description] if cursor.description else None
        return (cursor.fetchall() if description else []), description

    def run_many(self, sql: str, session, rows: list[list]) -> None:
        for marker, message in self.fail.items():
            if marker in sql:
                raise FakeProgrammingError(message, errno=100132, sqlstate="P0000")
        duck = self.to_duckdb(sql, session, bound=True)
        try:
            self.engine._con.executemany(duck, rows)
        except duckdb.Error as exc:
            raise FakeProgrammingError(str(exc), errno=100000, sqlstate="22000") from None


class _ProcBackend:
    """What `run_proc` executes a stored procedure's statements through: the caller's session, unrecorded."""

    def __init__(self, account: FakeAccount, session):
        self.account = account
        self.session = session

    def execute(self, sql: str) -> None:
        self.account.run_generic(sql, self.session)


class FakeCursor:
    def __init__(self, connection: "FakeConnection"):
        self.connection = connection
        self.description = None
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def close(self) -> None:
        pass

    def execute(self, sql: str, params=None):
        module = self.connection.module
        module.statements.append((sql, None if params is None else list(params)))
        self._rows, self.description = module.account.run(sql, self.connection, params)
        return self

    def executemany(self, sql: str, seq_of_params):
        module = self.connection.module
        rows = [list(params) for params in seq_of_params]
        module.statements.append((sql, rows))
        module.account.run_many(sql, self.connection, rows)
        return self

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, module: "FakeConnectorModule", kwargs: dict):
        self.module = module
        self.kwargs = kwargs
        self.database: str | None = None
        self.closed = False

    def cursor(self) -> FakeCursor:
        if self.closed:
            raise FakeError("Connection is closed", errno=250002)
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


class FakeConnectorModule:
    """Patched in as `sys.modules["snowflake.connector"]`: one fake account, every call recorded.

    It keeps the real package's `__path__`, and hands any attribute it does not define to the real
    package, so the Snowpark Local Testing Framework (which `FakeSession` runs on, and which imports
    connector submodules lazily) still finds them; `connect` is only ever the fake's."""

    paramstyle = "pyformat"
    errors = SimpleNamespace(Error=FakeError, ProgrammingError=FakeProgrammingError, DatabaseError=FakeError)
    __path__ = _real_connector.__path__

    def __getattr__(self, name):
        return getattr(_real_connector, name)

    def __init__(self):
        self.account = FakeAccount()
        self.connect_calls: list[dict] = []
        self.connections: list[FakeConnection] = []
        self.statements: list[tuple[str, list | None]] = []

    def connect(self, **kwargs) -> FakeConnection:
        self.connect_calls.append(dict(kwargs))
        connection = FakeConnection(self, dict(kwargs))
        self.connections.append(connection)
        return connection

    # --- helpers for tests: straight into the account, never recorded ---

    def sql(self) -> list[str]:
        return [sql for sql, _ in self.statements]

    def run(self, sql: str) -> list[tuple]:
        """One statement straight into the account (fully qualified names; setup only)."""
        return self.account.run(sql, _Session())[0]

    def load(self, fqn: str, table: dict) -> None:
        """A typed_csv table into the account as `fqn` (three-part), as `SnowflakeBackend.load_table` would."""
        session = _Session()
        columns = ", ".join(f'"{field["name"]}" {alteryx_to_snowflake(field)}' for field in table["fields"])
        self.account.run_generic(f"CREATE OR REPLACE TABLE {fqn} ({columns})", session)
        if table["rows"]:
            placeholders = ", ".join(["%s"] * len(table["fields"]))
            self.account.run_many(f"INSERT INTO {fqn} VALUES ({placeholders})", session,
                                  [list(row) for row in table["rows"]])

    def rows(self, fqn: str) -> list[tuple]:
        return self.account.run_generic(f"SELECT * FROM {fqn}", _Session())[0]

    def exists(self, fqn: str) -> bool:
        try:
            self.account._describe(fqn, _Session())
        except FakeProgrammingError:
            return False
        return True

    def frame(self, fqn: str):
        """`fqn`'s rows as a pandas DataFrame (what a real session's `to_pandas()` hands a procedure)."""
        return self.engine_con().execute(self.account.to_duckdb(f"SELECT * FROM {fqn}", _Session(),
                                                               bound=False)).df()

    def engine_con(self):
        return self.account.engine._con


# --- Snowpark ---------------------------------------------------------------------------------------


class _Collected:
    def collect(self) -> list:
        return []


class _AccountTable:
    """`session.table(name)` on a bridged session: the account's table, readable with `to_pandas()`."""

    def __init__(self, account: FakeConnectorModule, fqn: str):
        if not account.exists(fqn):
            raise FakeProgrammingError(f"Table '{fqn}' does not exist or not authorized.", errno=2003)
        self._account = account
        self._fqn = fqn

    def to_pandas(self):
        return self._account.frame(self._fqn)


class _BridgedWriter:
    def __init__(self, session: "FakeSession", frame):
        self._session = session
        self._frame = frame
        self._mode = "errorifexists"

    def mode(self, save_mode: str) -> "_BridgedWriter":
        self._mode = save_mode
        return self

    def save_as_table(self, name, **_ignored) -> None:
        if self._mode != "overwrite":
            raise NotImplementedError(f"the fake bridge writes mode('overwrite') only, not {self._mode!r}")
        fields = [{"name": field.name, **snowpark_to_alteryx(field.datatype)} for field in self._frame.schema.fields]
        rows = [list(row) for row in self._frame.collect()]
        self._session.account.load(self._session.qualify(name), {"fields": fields, "rows": rows})


class _BridgedFrame:
    def __init__(self, session: "FakeSession", frame):
        self._session = session
        self._frame = frame

    @property
    def write(self) -> _BridgedWriter:
        return _BridgedWriter(self._session, self._frame)

    def __getattr__(self, name):
        return getattr(self._frame, name)


class FakeSession:
    """A Snowpark session double built for `connection_name`: a real Local Testing Framework session
    underneath (everything not overridden here goes to it), `sql()` recorded instead of executed, and
    -- with `account` -- a migrated procedure's table I/O bridged to that fake account."""

    def __init__(self, connection_name: str, account: FakeConnectorModule | None = None):
        from snowflake.snowpark import Session  # noqa: PLC0415
        self.connection_name = connection_name
        self.account = account
        self.statements: list[str] = []
        self.database: str | None = None
        self.closed = False
        self._inner = Session.builder.configs({"local_testing": True}).create()

    def sql(self, query: str, params=None) -> _Collected:
        self.statements.append(query)
        if match := _USE_DATABASE.match(_strip_comments(query)):
            self.database = _parts(match.group(1))[0]
        return _Collected()

    def qualify(self, name: str) -> str:
        parts = _parts(name)
        if len(parts) == 2:
            if not self.database:
                raise FakeProgrammingError("This session does not have a current database.", errno=90105)
            parts = [self.database, *parts]
        return ".".join(parts)

    def table(self, name):
        if self.account is None:
            return self._inner.table(name)
        return _AccountTable(self.account, self.qualify(name))

    def create_dataframe(self, data, schema=None, **kwargs):
        frame = self._inner.create_dataframe(data, schema=schema, **kwargs)
        return frame if self.account is None else _BridgedFrame(self, frame)

    def close(self) -> None:
        self.closed = True
        self._inner.close()

    def __getattr__(self, name):
        return getattr(self._inner, name)
