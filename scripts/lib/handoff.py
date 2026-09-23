"""The one typed hand-off of a table between the DuckDB double and a local Snowpark session.

A workflow whose segments mix SQL and Snowpark procedures crosses engines at every Snowpark seam
when it is run as one chain (`scripts/validate_workflow.py`): the upstream SQL segment's actual
output lives in a `DuckDBBackend`, the Snowpark segment reads it from a Local Testing Framework
`Session`, and whatever the Snowpark segment writes has to come back for the next SQL segment and
for `compare.py`. This module is the only code that moves a table across, in either direction, and
it reads the REAL schema on both sides -- DuckDB's own catalog going in
(`types_map.duckdb_to_alteryx`), Snowpark's own `StructType` coming back
(`types_map.snowpark_to_alteryx`). No helper here takes a contract: coercing a table to what a
contract declares would hide exactly the type drift a chain test exists to catch (phase-1 ruling 16).

A table crosses as a `typed_csv` table (`{"fields", "rows"}`, contract C1): `Decimal` kept, dates
and times as the ISO text `typed_csv` holds, booleans/ints/floats/strings as they are. The two
Snowpark-side helpers are `validate_snowpark.py`'s former `_read_back` and `_save`, moved here
verbatim (`validate_snowpark` re-exports them under their old names).

Nothing here has run against a real Snowflake account: the Snowpark side is the Local Testing
Framework, and its pandas round trip has quirks `_coerce` undoes (see `table_from_snowpark`).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from .types_map import alteryx_to_snowpark, duckdb_to_alteryx, snowpark_to_alteryx


class HandoffError(ValueError):
    """A table cannot cross the engine seam as a typed table: a column whose type has no Alteryx
    counterpart, a cell its own column type cannot represent, or a table that is not there. A
    domain failure of whatever produced the table -- never a usage error about a prerequisite."""


class ReadBackError(HandoffError):
    """A cell in the actual table (or a whole column's own Snowpark type) cannot be represented as
    a `typed_csv` value (task-4 fix round 1, finding I3): a domain failure -- the procedure's own
    output is broken in a way `compare.py` was never meant to see -- not a usage error about a
    missing prerequisite. Carries enough to name exactly where. (`validate_snowpark.ReadBackError`
    is this class.)"""

    def __init__(self, table: str, column: str, row_index: int | None, reason: str):
        where = f"row {row_index}" if row_index is not None else "its column type"
        super().__init__(f"{table}.{column} ({where}): cannot read back ({reason})")
        self.table = table
        self.column = column
        self.row_index = row_index
        self.reason = reason


# --- DuckDB -> typed table -> DuckDB ------------------------------------------------------------


def _typed_value(value):
    """A DuckDB cell -> the `typed_csv` value: `Decimal`, `bool`, `int`, `float` and `str` as they
    are; a date as `YYYY-MM-DD`, a timestamp as `YYYY-MM-DD HH:MM:SS[.ffffff]` and a time as
    `HH:MM:SS[.ffffff]` -- the ISO text `typed_csv` reads and writes (and `_to_snowpark` parses)."""
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    return value


def table_from_backend(backend, fqn: str) -> dict:
    """`fqn` as it really is in `backend`: its fields from the backend's own catalog
    (`table_columns`, mapped by `types_map.duckdb_to_alteryx`) and its rows in the backend's own
    physical order -- never sorted: a sort would hand a downstream segment an order production never
    promises and hide a procedure that depends on its input's order (Task W1 fix round 1, I2; the
    chain's idempotency re-run reverses what it hands over instead). Raises `HandoffError` for a
    table that does not exist or a column type with no Alteryx counterpart, naming `<fqn>.<column>`."""
    columns = backend.table_columns(fqn)
    if not columns:
        raise HandoffError(f"{fqn} does not exist to hand off")
    fields = []
    for column in columns:
        try:
            mapped = duckdb_to_alteryx(column["type"])
        except ValueError as exc:
            raise HandoffError(f"{fqn}.{column['name']}: {exc}") from exc
        fields.append({"name": column["name"], **mapped})
    rows = [[_typed_value(value) for value in row] for row in backend.query(f"SELECT * FROM {fqn}")[1]]
    return {"fields": fields, "rows": rows}


def load_into_backend(backend, fqn: str, table: dict) -> None:
    """A typed table into `backend` as `fqn` (`CREATE OR REPLACE`), with the column types the
    table's own fields map to (`DuckDBBackend.load_table`)."""
    backend.load_table(fqn, table)


# --- typed table -> Snowpark -> typed table (moved verbatim from validate_snowpark.py) -------------


def load_into_snowpark(session, fqn: str, table: dict) -> None:
    from snowflake.snowpark.types import StructField, StructType  # noqa: PLC0415  (lazy)
    schema = StructType([StructField(f["name"], alteryx_to_snowpark(f), True) for f in table["fields"]])
    rows = [list(_to_snowpark(value, field) for value, field in zip(row, table["fields"]))
            for row in table["rows"]]
    session.create_dataframe(rows, schema=schema).write.mode("overwrite").save_as_table(fqn)


def _to_snowpark(value, field):
    """typed_csv values -> what create_dataframe accepts: ISO strings become date/datetime objects."""
    if value is None:
        return None
    if field["type"] == "Date":
        return dt.date.fromisoformat(value)
    if field["type"] == "DateTime":
        return dt.datetime.fromisoformat(value)
    return value


def _coerce(value, alteryx_type: str):
    """A `session.table(...).to_pandas()` cell -> a `typed_csv` value, per the REAL column's
    Alteryx type (from `types_map.snowpark_to_alteryx`, never the contract's declared type -- see
    the module docstring): NaN/NaT/None -> None; a NULL-upcasted float back to `int` for an Int64
    column; a `Decimal` kept as-is; `Timestamp`/`date`/`time` -> the same ISO text `typed_csv`
    reads and writes; a numpy scalar -> the plain Python type underneath it. Raises `ValueError`/
    `TypeError`/`decimal.InvalidOperation`/`OverflowError` for a value its own column type cannot
    represent -- `table_from_snowpark` turns that into a `ReadBackError` naming exactly where."""
    if value is None:
        return None
    import pandas as pd  # noqa: PLC0415  (lazy: see module docstring; already loaded by to_pandas())
    if pd.isna(value):
        return None
    if alteryx_type == "Int64":
        return int(value)
    if alteryx_type == "FixedDecimal":
        return value if isinstance(value, Decimal) else Decimal(str(value))
    if alteryx_type == "Double":
        return float(value)
    if alteryx_type == "Bool":
        return bool(value)
    if alteryx_type == "Date":
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if alteryx_type == "DateTime":
        return value.isoformat(sep=" ") if hasattr(value, "isoformat") else str(value)
    if alteryx_type == "Time":
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if hasattr(value, "item"):  # a numpy scalar (e.g. numpy.str_) with no more specific handling above
        return value.item()
    return value


def table_from_snowpark(session, fqn: str) -> dict | None:
    """The actual table's real schema AND rows, both read from Snowpark itself -- never from the
    contract (task-4 fix round 1, findings C1/C2): comparing against the contract's declared
    columns only ever confirms what the contract already expected, so the schema `compare.py`
    checks has to come from what the procedure actually wrote, in its own column order, including
    a column the contract never declared and omitting one the handler failed to write. Returns
    `None` if the table doesn't exist (or any other error reading it back). Raises
    `ReadBackError` for a column whose Snowpark type has no Alteryx counterpart
    (`types_map.snowpark_to_alteryx`), or for a cell that column's own (real) type cannot
    represent as a `typed_csv` value -- both domain failures, never a usage error (finding I3).

    Local testing's pandas round trip has quirks that would otherwise look like data bugs: a
    nullable integer column that contains a NULL comes back as `float64` (NaN for the null, `1.0`
    for a real value); a `DecimalType` column as `decimal.Decimal`; a `TimestampType` column as
    `pandas.Timestamp` (or `NaT`); a `DateType` column as `datetime.date`. `_coerce` undoes all of
    that, per column."""
    try:
        table = session.table(fqn)
        schema_fields = list(table.schema.fields)
        pdf = table.to_pandas()
    except Exception:
        return None

    fields: list[dict] = []
    alteryx_types: list[str] = []
    for struct_field in schema_fields:
        try:
            alteryx = snowpark_to_alteryx(struct_field.datatype)
        except ValueError as exc:
            raise ReadBackError(fqn, struct_field.name, None, str(exc)) from exc
        fields.append({"name": struct_field.name, **alteryx})
        alteryx_types.append(alteryx["type"])

    names = [struct_field.name for struct_field in schema_fields]
    rows = []
    for row_index, record in enumerate(pdf.to_dict("records")):
        row = []
        for name, alteryx_type in zip(names, alteryx_types):
            try:
                row.append(_coerce(record.get(name), alteryx_type))
            except (ValueError, TypeError, InvalidOperation, OverflowError) as exc:
                raise ReadBackError(fqn, name, row_index, str(exc)) from exc
        rows.append(row)
    return {"fields": fields, "rows": rows}
