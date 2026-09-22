"""SQL backends: one interface, a local DuckDB double and an unexercised Snowflake adapter.

There is no Snowflake account on this machine, so every parity test in the project runs
Snowflake-dialect SQL through `DuckDBBackend`: sqlglot parses the statement with the Snowflake
parser, a few AST transforms bridge what sqlglot cannot express, and DuckDB executes the result.
Nothing in this repo has ever run against a real Snowflake account, `SnowflakeBackend` included.

Two deliberate differences from Snowflake that callers must know about:

* **`VARCHAR(n)` length is not enforced.** DuckDB accepts `VARCHAR(20)` and stores longer strings
  unchanged. Snowflake would raise on overflow; the local runtime cannot reproduce that, so a
  Select-tool truncation bug will not surface here as an error. (`compare.py`'s `TRUNCATION` diff
  class catches it in the data instead.)
* **Identifier case.** Snowflake upper-cases unquoted identifiers; DuckDB preserves the case as
  written and matches case-insensitively. `table_columns` reports names exactly as DuckDB has
  them, so callers compare column names case-insensitively.

Three-part names are flattened: `FIN.RAW.GL` becomes DuckDB's `FIN__RAW.GL`, because DuckDB's
catalogs are attached database files rather than namespaces inside one connection.
"""
from __future__ import annotations

from typing import Any

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

from .types_map import alteryx_to_snowflake

# The single local catalog that stands in for the Snowflake account's database (contract C3).
SANDBOX_DB = "MIGDB"


class BackendError(Exception):
    """A statement could not be translated or executed. Carries the offending SQL."""


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _split_identifiers(fqn: str) -> list[str]:
    """Splits a possibly-quoted FQN on the dots that separate identifiers."""
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    for char in fqn:
        if char == '"':
            quoted = not quoted
        elif char == "." and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts]


def local_name(fqn: str) -> tuple[str, str]:
    """A Snowflake FQN → the (schema, table) DuckDB holds it under.

    "A.B.C" → ("A__B", "C"); "B.C" → ("B", "C"); "C" → ("main", "C"). Double quotes are stripped.
    """
    parts = _split_identifiers(fqn)
    if len(parts) == 1:
        return "main", parts[0]
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 3:
        return f"{parts[0]}__{parts[1]}", parts[2]
    raise BackendError(f"not a Snowflake table name: {fqn!r}")


# --- Snowflake → DuckDB AST transforms -------------------------------------------------------
#
# Everything here exists because sqlglot 30 cannot express the construct in DuckDB on its own.
# Generation runs with unsupported_level=RAISE, so anything sqlglot would silently drop becomes a
# BackendError instead of quietly wrong SQL.

# Snowflake date/time format elements → strftime, longest first so MMMM beats MM.
_FORMAT_TOKENS = {
    "YYYY": "%Y", "YY": "%y",
    "MMMM": "%B", "MON": "%b", "MM": "%m",
    "DD": "%d", "DY": "%a",
    "HH24": "%H", "HH12": "%I", "HH": "%H",
    "MI": "%M", "SS": "%S",
    "FF9": "%f", "FF6": "%f", "FF3": "%g", "FF": "%f",
    "AM": "%p", "PM": "%p",
}
_FORMAT_ORDER = tuple(sorted(_FORMAT_TOKENS, key=len, reverse=True))


def _strftime_format(snowflake_format: str) -> str:
    """'YYYY-MM-DD HH24:MI:SS' → '%Y-%m-%d %H:%M:%S'.

    Raises BackendError on an element the mapping does not cover rather than emitting a format
    that would silently produce different text from Snowflake's.
    """
    upper = snowflake_format.upper()
    out: list[str] = []
    index = 0
    while index < len(snowflake_format):
        for token in _FORMAT_ORDER:
            if upper.startswith(token, index):
                out.append(_FORMAT_TOKENS[token])
                index += len(token)
                break
        else:
            char = snowflake_format[index]
            if char.isalnum():
                raise BackendError(
                    f"unsupported TO_CHAR/TO_VARCHAR format element {char!r} in "
                    f"{snowflake_format!r}; add it to _FORMAT_TOKENS in scripts/lib/backend.py "
                    f"with the strftime element that matches Snowflake's output")
            out.append("%%" if char == "%" else char)
            index += 1
    return "".join(out)


def _rewrite_functions(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.ToNumber) and node.args.get("safe"):
        # sqlglot renders TRY_TO_NUMBER as a plain CAST for DuckDB, which raises instead of
        # returning NULL on unparseable input — the whole point of the TRY_ form.
        precision = node.args.get("precision")
        scale = node.args.get("scale")
        data_type = exp.DataType.build(
            f"DECIMAL({precision.name if precision else 38},{scale.name if scale else 0})")
        return exp.TryCast(this=node.this, to=data_type)
    if isinstance(node, exp.ToChar):
        # sqlglot drops the format argument for DuckDB (CAST(x AS TEXT)); STRFTIME keeps it.
        fmt = node.args.get("format")
        if fmt is not None:
            return exp.Anonymous(this="STRFTIME",
                                 expressions=[node.this, exp.Literal.string(_strftime_format(fmt.name))])
    return node


def _drop_transient(tree: exp.Expression) -> None:
    """TRANSIENT is a Snowflake storage class with no DuckDB equivalent and no effect on results."""
    for create in tree.find_all(exp.Create):
        properties = create.args.get("properties")
        if properties is None:
            continue
        kept = [p for p in properties.expressions if not isinstance(p, exp.TransientProperty)]
        if len(kept) != len(properties.expressions):
            create.set("properties", exp.Properties(expressions=kept) if kept else None)


def _flatten_catalogs(tree: exp.Expression) -> None:
    """`CATALOG.SCHEMA.TABLE` → `CATALOG__SCHEMA.TABLE`.

    Only catalog-qualified references are touched, and such a reference can never be a CTE: SQL
    has no syntax for qualifying a CTE name. So a CTE that happens to share a table's name needs
    no special case — a bare `GL` is left exactly as written, and `FIN.RAW.GL` in the same
    statement still becomes `FIN__RAW.GL`.
    """
    for table in tree.find_all(exp.Table):
        catalog = table.args.get("catalog")
        if catalog is None:
            continue
        schema = table.args.get("db")
        if schema is None:
            raise BackendError(f"table {table.sql()} has a catalog but no schema")
        table.set("db", exp.to_identifier(f"{catalog.name}__{schema.name}",
                                          quoted=bool(catalog.quoted or schema.quoted)))
        table.set("catalog", None)


def _target_schemas(tree: exp.Expression) -> list[str]:
    """The local schemas a statement writes into, for CREATE SCHEMA IF NOT EXISTS."""
    targets: list[exp.Expression] = []
    for create in tree.find_all(exp.Create):
        if str(create.args.get("kind") or "").upper() not in ("TABLE", "VIEW"):
            continue
        target = create.this
        targets.append(target.this if isinstance(target, exp.Schema) else target)
    for merge in tree.find_all(exp.Merge):
        targets.append(merge.this)
    schemas = []
    for target in targets:
        if isinstance(target, exp.Table):
            schema = target.args.get("db")
            if schema is not None:
                schemas.append(schema.name)
    return schemas


class DuckDBBackend:
    """Executes Snowflake-dialect SQL on DuckDB. `db_path` ":memory:" keeps it per-process."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._con = duckdb.connect(db_path)

    # --- translation ---

    def translate(self, sql: str) -> str:
        """One Snowflake statement → one DuckDB statement."""
        return self._prepare(sql)[1]

    def _prepare(self, sql: str) -> tuple[exp.Expression, str]:
        try:
            tree = sqlglot.parse_one(sql, read="snowflake")
        except SqlglotError as exc:
            raise BackendError(f"cannot parse as Snowflake SQL: {exc}{_context(sql)}") from exc
        if tree is None:
            raise BackendError(f"no statement to run{_context(sql)}")
        try:
            tree = tree.transform(_rewrite_functions, copy=False)
            _drop_transient(tree)
            _flatten_catalogs(tree)
            return tree, tree.sql(dialect="duckdb", unsupported_level=ErrorLevel.RAISE)
        except BackendError as exc:
            raise BackendError(f"{exc}{_context(sql)}") from exc
        except SqlglotError as exc:
            raise BackendError(f"cannot express in DuckDB SQL: {exc}{_context(sql)}") from exc

    # --- statements ---

    def execute(self, sql: str) -> None:
        tree, duckdb_sql = self._prepare(sql)
        for schema in _target_schemas(tree):
            self._create_schema(schema)
        self._run(duckdb_sql, sql)

    def query(self, sql: str) -> tuple[list[str], list[tuple]]:
        cursor = self._run(self._prepare(sql)[1], sql)
        return [column[0] for column in cursor.description], cursor.fetchall()

    def _run(self, duckdb_sql: str, original: str):
        try:
            return self._con.execute(duckdb_sql)
        except duckdb.Error as exc:
            raise BackendError(f"{exc}{_context(original, duckdb_sql)}") from exc

    def _create_schema(self, schema: str) -> None:
        self._con.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote(schema)}")

    # --- data ---

    def load_table(self, fqn: str, table: dict) -> None:
        """A typed_csv Table → CREATE OR REPLACE TABLE with typed columns, then its rows.

        Columns are declared with `alteryx_to_snowflake` types and translated like any other
        statement, so a FixedDecimal(19,2) really is DECIMAL(19,2) here. ISO date, time and
        timestamp strings (contract C1) are cast by DuckDB on insert.
        """
        fields = table["fields"]
        columns = ", ".join(f"{_quote(field['name'])} {alteryx_to_snowflake(field)}" for field in fields)
        self.execute(f"CREATE OR REPLACE TABLE {fqn} ({columns})")
        rows = table["rows"]
        if not rows:
            return
        schema, name = local_name(fqn)
        placeholders = ", ".join(["?"] * len(fields))
        statement = f"INSERT INTO {_quote(schema)}.{_quote(name)} VALUES ({placeholders})"
        try:
            self._con.executemany(statement, [list(row) for row in rows])
        except duckdb.Error as exc:
            raise BackendError(f"cannot load {len(rows)} rows into {fqn}: {exc}") from exc

    def create_view(self, view_fqn: str, target_fqn: str) -> None:
        self.execute(f"CREATE OR REPLACE VIEW {view_fqn} AS SELECT * FROM {target_fqn}")

    # --- catalog ---

    def table_exists(self, fqn: str) -> bool:
        schema, name = local_name(fqn)
        rows = self._con.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE upper(table_schema) = upper(?) AND upper(table_name) = upper(?)",
            [schema, name]).fetchall()
        return bool(rows)

    def table_columns(self, fqn: str) -> list[dict]:
        schema, name = local_name(fqn)
        rows = self._con.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE upper(table_schema) = upper(?) AND upper(table_name) = upper(?) "
            "ORDER BY ordinal_position", [schema, name]).fetchall()
        return [{"name": name_, "type": str(type_).upper(), "nullable": nullable == "YES"}
                for name_, type_, nullable in rows]

    def close(self) -> None:
        self._con.close()


class SnowflakeBackend:
    """The same interface against a real account. Never run: no account exists in this project.

    Kept so `compare.py`, `compile_check.py` and the orchestrator are written against the seam
    rather than against DuckDB. It is a thin pass-through — the SQL is already Snowflake dialect —
    and it should be treated as unverified code until someone runs it on an account.
    """

    def __init__(self, **connect_args: Any):
        try:
            import snowflake.connector  # noqa: PLC0415  (lazy: the package is optional)
        except ImportError as exc:
            raise BackendError("snowflake-connector-python is not installed") from exc
        self._con = snowflake.connector.connect(**connect_args)

    def translate(self, sql: str) -> str:
        return sql

    def execute(self, sql: str) -> None:
        with self._con.cursor() as cursor:
            cursor.execute(sql)

    def query(self, sql: str) -> tuple[list[str], list[tuple]]:
        with self._con.cursor() as cursor:
            cursor.execute(sql)
            return [d[0] for d in cursor.description], list(cursor.fetchall())

    def load_table(self, fqn: str, table: dict) -> None:
        fields = table["fields"]
        columns = ", ".join(f"{_quote(f['name'])} {alteryx_to_snowflake(f)}" for f in fields)
        self.execute(f"CREATE OR REPLACE TABLE {fqn} ({columns})")
        if not table["rows"]:
            return
        # The connector's default paramstyle is pyformat; unverified, like the rest of this class.
        placeholders = ", ".join(["%s"] * len(fields))
        with self._con.cursor() as cursor:
            cursor.executemany(f"INSERT INTO {fqn} VALUES ({placeholders})",
                               [list(row) for row in table["rows"]])

    def create_view(self, view_fqn: str, target_fqn: str) -> None:
        self.execute(f"CREATE OR REPLACE VIEW {view_fqn} AS SELECT * FROM {target_fqn}")

    def table_exists(self, fqn: str) -> bool:
        return bool(self.table_columns(fqn))

    def table_columns(self, fqn: str) -> list[dict]:
        _, rows = self.query(f"DESCRIBE TABLE {fqn}")
        return [{"name": r[0], "type": str(r[1]).upper(), "nullable": str(r[3]).upper() == "Y"}
                for r in rows]

    def close(self) -> None:
        self._con.close()


def get_backend(kind: str = "duckdb", **kwargs: Any):
    if kind == "duckdb":
        return DuckDBBackend(**kwargs)
    if kind == "snowflake":
        return SnowflakeBackend(**kwargs)
    raise BackendError(f"unknown backend kind {kind!r}: expected 'duckdb' or 'snowflake'")


def _context(snowflake_sql: str, duckdb_sql: str | None = None) -> str:
    text = f"\n--- statement ---\n{snowflake_sql.strip()}"
    if duckdb_sql is not None and duckdb_sql.strip() != snowflake_sql.strip():
        text += f"\n--- as DuckDB ---\n{duckdb_sql.strip()}"
    return text
