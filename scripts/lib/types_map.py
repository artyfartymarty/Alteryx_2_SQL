"""Alteryx field types → Snowflake column types, and the coarse families parity checks compare on.

The mapping is the one the cookbook documents for hand-written procedures, so a golden CSV loaded
into the sandbox has the same column type the translated procedure will meet on Snowflake. Nothing
here has been checked against a live Snowflake account.

`type_family` deliberately collapses widths and precisions: `compare.py` treats
`NUMBER(19,2)` and `NUMBER(38,0)` as the same family so a harmless precision change is not
reported as a schema failure, while `VARCHAR` vs `NUMBER` still is.
"""
from __future__ import annotations

import re

# Alteryx integer types all become NUMBER(38,0): Alteryx widths are storage hints, and Snowflake's
# NUMBER is one physical type regardless of the declared precision.
_INTEGER_TYPES = frozenset({"Byte", "Int16", "Int32", "Int64"})

_FIXED_TYPES = {
    "Float": "FLOAT",
    "Double": "FLOAT",
    "Bool": "BOOLEAN",
    "Date": "DATE",
    "Time": "TIME",
    "DateTime": "TIMESTAMP_NTZ",
    "Blob": "BINARY",
    "V_String": "VARCHAR",
    "V_WString": "VARCHAR",
}

# Sized strings keep their declared length. DuckDB does not enforce it (see backend.py), so a
# length overflow that Snowflake would reject cannot be reproduced locally.
_SIZED_STRING_TYPES = frozenset({"String", "WString"})

_FAMILIES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"NUMBER", "NUMERIC", "DECIMAL", "DEC", "INT", "INTEGER", "BIGINT", "SMALLINT",
                "TINYINT", "BYTEINT"}), "number"),
    (frozenset({"FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL"}), "float"),
    (frozenset({"VARCHAR", "CHAR", "CHARACTER", "STRING", "TEXT", "NVARCHAR", "NCHAR"}), "string"),
    (frozenset({"DATE"}), "date"),
    (frozenset({"TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "DATETIME"}), "timestamp"),
    (frozenset({"TIME"}), "time"),
    (frozenset({"BOOLEAN", "BOOL"}), "bool"),
    (frozenset({"BINARY", "VARBINARY", "BLOB", "BYTEA"}), "binary"),
)


def alteryx_to_snowflake(field: dict) -> str:
    """{"name", "type", "size", "scale"} → a Snowflake column type.

    Raises ValueError for a type the mapping does not cover: the pipeline never guesses a type.
    """
    alteryx_type = field["type"]
    if alteryx_type in _INTEGER_TYPES:
        return "NUMBER(38,0)"
    if alteryx_type == "FixedDecimal":
        return f"NUMBER({field['size']},{field.get('scale') or 0})"
    if alteryx_type in _SIZED_STRING_TYPES:
        return f"VARCHAR({field['size']})"
    if alteryx_type in _FIXED_TYPES:
        return _FIXED_TYPES[alteryx_type]
    raise ValueError(f"no Snowflake type mapping for Alteryx type {alteryx_type!r} "
                     f"(field {field.get('name', '?')!r})")


def type_family(sql_type: str) -> str:
    """A Snowflake (or DuckDB) column type → its comparison family, or "other" if unrecognised."""
    base = re.sub(r"\(.*", "", str(sql_type)).strip().upper()
    for names, family in _FAMILIES:
        if base in names:
            return family
    return "other"
