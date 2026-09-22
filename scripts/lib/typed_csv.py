"""Typed CSV read/write (plan contract C1).

Golden data is `<name>.csv` plus a `<name>.schema.json` sidecar: header row, `,` delimiter,
RFC 4180 quoting, NULL is the two characters `\\N`, empty string is empty. Schema is a list of
Alteryx field descriptors: [{"name", "type", "size", "scale"}].

Table = {"fields": [field, …], "rows": [[value, …], …]}, values are Python
None | bool | int | float | decimal.Decimal | str (dates stay ISO strings).
"""
from __future__ import annotations

import csv
import math
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from .io import read_json, write_json

NULL_TOKEN = "\\N"

_FLOAT_TYPES = {"Float", "Double"}
_DECIMAL_TYPES = {"FixedDecimal"}
_BOOL_TYPES = {"Bool"}


def _is_int_type(alteryx_type: str) -> bool:
    # Alteryx integer types: Byte, Int16, Int32, Int64.
    return alteryx_type == "Byte" or alteryx_type.startswith("Int")


def parse_value(text: str, alteryx_type: str) -> Any:
    """CSV cell text -> typed Python value, per the field's Alteryx type."""
    if text == NULL_TOKEN:
        return None
    if _is_int_type(alteryx_type):
        return int(text)
    if alteryx_type in _FLOAT_TYPES:
        return float(text)
    if alteryx_type in _DECIMAL_TYPES:
        return Decimal(text)
    if alteryx_type in _BOOL_TYPES:
        return text == "true"
    return text


def _format_float(value: float) -> str:
    """Plain decimal notation, never scientific (contract C1), exact round-trip via float().

    repr(value) is the shortest decimal string that round-trips to value; routing it through
    Decimal and formatting with 'f' re-renders those same digits without an exponent, so no
    float noise is introduced and no precision is lost.
    """
    if not math.isfinite(value):
        raise ValueError(f"cannot write non-finite float in plain decimal notation: {value!r}")
    text = format(Decimal(repr(value)), "f")
    if "." not in text:
        text += ".0"
    return text


def format_value(value: Any, alteryx_type: str) -> str:
    """Typed Python value -> CSV cell text, per the field's Alteryx type."""
    if value is None:
        return NULL_TOKEN
    if alteryx_type in _BOOL_TYPES:
        return "true" if value else "false"
    if alteryx_type in _FLOAT_TYPES:
        return _format_float(value)
    return str(value)


def _schema_path(csv_path: Path) -> Path:
    return csv_path.parent / (csv_path.stem + ".schema.json")


def read_table(csv_path: str | os.PathLike) -> dict:
    csv_path = Path(csv_path)
    fields = read_json(_schema_path(csv_path))
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # header row: field names only, typing comes from the schema sidecar
        rows = [
            [parse_value(text, field["type"]) for text, field in zip(raw_row, fields)]
            for raw_row in reader
        ]
    return {"fields": fields, "rows": rows}


def write_table(csv_path: str | os.PathLike, table: dict) -> None:
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = table["fields"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow([field["name"] for field in fields])
        for row in table["rows"]:
            writer.writerow([format_value(value, field["type"]) for value, field in zip(row, fields)])
    write_json(_schema_path(csv_path), fields)
