"""The one typed hand-off between the DuckDB double and a local Snowpark session (plan Task W1).

A mixed SQL/Snowpark chain crosses engines at every Snowpark seam; `scripts/lib/handoff.py` is the
only code that moves a table across, and it reads the REAL schema on both sides -- DuckDB's catalog
going in, Snowpark's `StructType` coming back -- never a contract's declaration (phase-1 ruling 16).
Nothing here has run on Snowflake: the Snowpark side is the Local Testing Framework.
"""
from __future__ import annotations

import datetime as dt
import inspect
from decimal import Decimal

import pytest

import validate_snowpark as vsp
from lib import handoff
from lib.backend import DuckDBBackend
from lib.types_map import duckdb_to_alteryx, type_family
from lib.validation import ordered_rows

FQN = "MIG_WORK.HANDOFF_IN"

ROWS = [
    "(1, 10.25, 1.5, 'a', TRUE, '2026-01-31', '2026-01-31 12:34:56')",
    "(NULL, 3.10, 2.25, 'b', FALSE, '2026-02-28', '2026-02-28 00:00:01')",
    "(3, NULL, -0.5, 'c', TRUE, '2026-03-31', '2026-03-31 23:59:59')",
    "(4, 7.00, NULL, 'd', FALSE, '2026-04-30', '2026-04-30 08:00:00')",
    "(5, 1.01, 9.75, NULL, TRUE, '2026-05-31', '2026-05-31 09:30:00')",
    "(6, 2.02, 0.125, 'f', NULL, '2026-06-30', '2026-06-30 10:15:00')",
    "(7, 3.03, 4.5, 'g', TRUE, NULL, '2026-07-31 11:45:00')",
    "(8, 4.04, 5.5, 'h', FALSE, '2026-08-31', NULL)",
]


def _families(backend, fqn):
    return [(column["name"].upper(), type_family(column["type"])) for column in backend.table_columns(fqn)]


def _source_backend() -> DuckDBBackend:
    backend = DuckDBBackend()
    backend.execute(f"CREATE TABLE {FQN} (N BIGINT, D DECIMAL(19,2), F DOUBLE, S VARCHAR, B BOOLEAN, "
                    f"DT DATE, TS TIMESTAMP)")
    backend.execute(f"INSERT INTO {FQN} VALUES {', '.join(ROWS)}")
    return backend


def test_round_trip_duckdb_snowpark_duckdb_keeps_types_and_values():
    source = _source_backend()
    session = vsp.new_local_session()
    fresh = DuckDBBackend()
    try:
        assert [c["type"] for c in source.table_columns(FQN)] == [
            "BIGINT", "DECIMAL(19,2)", "DOUBLE", "VARCHAR", "BOOLEAN", "DATE", "TIMESTAMP"]

        table = handoff.table_from_backend(source, FQN)
        handoff.load_into_snowpark(session, FQN, table)
        back = handoff.table_from_snowpark(session, FQN)
        assert back is not None
        handoff.load_into_backend(fresh, FQN, back)

        assert _families(fresh, FQN) == _families(source, FQN)
        assert ordered_rows(fresh, FQN) == ordered_rows(source, FQN)
        # every column carries a NULL somewhere, and it survived as a NULL, not a zero or "nan"
        assert all(any(row[i] is None for row in ordered_rows(fresh, FQN)) for i in range(7))
    finally:
        session.close()
        source.close()
        fresh.close()


def test_table_from_backend_reads_the_real_duckdb_schema_and_typed_csv_values():
    source = _source_backend()
    try:
        table = handoff.table_from_backend(source, FQN)
    finally:
        source.close()
    assert [(f["name"], f["type"], f["size"], f["scale"]) for f in table["fields"]] == [
        ("N", "Int64", None, None), ("D", "FixedDecimal", 19, 2), ("F", "Double", None, None),
        ("S", "V_String", None, None), ("B", "Bool", None, None), ("DT", "Date", None, None),
        ("TS", "DateTime", None, None)]
    first = table["rows"][0]
    assert first == [1, Decimal("10.25"), 1.5, "a", True, "2026-01-31", "2026-01-31 12:34:56"]
    assert all(isinstance(value, str) for value in first[5:])      # ISO text, as typed_csv holds dates


def test_the_real_schema_wins_over_any_declaration():
    from snowflake.snowpark.types import StringType, StructField, StructType

    session = vsp.new_local_session()
    backend = DuckDBBackend()
    try:
        # A column every contract would call a number, written by the procedure as a string.
        session.create_dataframe([["1"], ["2"]], schema=StructType([StructField("AMOUNT", StringType())])) \
            .write.mode("overwrite").save_as_table("MIG_WORK.SNOWPARK_OUT")
        table = handoff.table_from_snowpark(session, "MIG_WORK.SNOWPARK_OUT")
        assert table["fields"][0]["type"] == "V_String"
        handoff.load_into_backend(backend, "MIG_WORK.SNOWPARK_OUT", table)
        assert _families(backend, "MIG_WORK.SNOWPARK_OUT") == [("AMOUNT", "string")]
        assert ordered_rows(backend, "MIG_WORK.SNOWPARK_OUT") == [("1",), ("2",)]
    finally:
        session.close()
        backend.close()


def test_an_unmapped_duckdb_type_is_a_handoff_error():
    backend = DuckDBBackend()
    try:
        backend.execute("CREATE TABLE MIG_WORK.BLOBS (ID BIGINT, PAYLOAD BINARY)")
        assert backend.table_columns("MIG_WORK.BLOBS")[1]["type"] == "BLOB"
        with pytest.raises(handoff.HandoffError, match=r"MIG_WORK\.BLOBS\.PAYLOAD"):
            handoff.table_from_backend(backend, "MIG_WORK.BLOBS")
    finally:
        backend.close()


def test_a_table_that_is_not_there_is_a_handoff_error_not_an_empty_table():
    backend = DuckDBBackend()
    try:
        with pytest.raises(handoff.HandoffError, match="MIG_WORK.NOWHERE"):
            handoff.table_from_backend(backend, "MIG_WORK.NOWHERE")
    finally:
        backend.close()


def test_a_missing_snowpark_table_reads_back_as_none():
    session = vsp.new_local_session()
    try:
        assert handoff.table_from_snowpark(session, "MIG_WORK.NOWHERE") is None
    finally:
        session.close()


def test_no_helper_takes_a_contract():
    for helper in (handoff.table_from_backend, handoff.table_from_snowpark,
                   handoff.load_into_backend, handoff.load_into_snowpark):
        assert "contract" not in inspect.signature(helper).parameters, helper.__name__


def test_handoff_error_is_a_value_error_and_read_back_error_is_one():
    assert issubclass(handoff.HandoffError, ValueError)
    assert issubclass(vsp.ReadBackError, handoff.HandoffError)
    # validate_snowpark keeps its old names, now the hand-off's own functions
    assert vsp._read_back is handoff.table_from_snowpark
    assert vsp._save is handoff.load_into_snowpark
    assert vsp.new_local_session is vsp._session
    assert vsp.load_module is vsp._load_module


@pytest.mark.parametrize("duckdb_type,expected", [
    ("BIGINT", ("Int64", None, None)), ("INTEGER", ("Int64", None, None)),
    ("SMALLINT", ("Int64", None, None)), ("TINYINT", ("Int64", None, None)),
    ("HUGEINT", ("Int64", None, None)), ("UBIGINT", ("Int64", None, None)),
    ("UINTEGER", ("Int64", None, None)), ("USMALLINT", ("Int64", None, None)),
    ("UTINYINT", ("Int64", None, None)),
    ("DECIMAL(19,2)", ("FixedDecimal", 19, 2)), ("DECIMAL(38, 0)", ("FixedDecimal", 38, 0)),
    ("DOUBLE", ("Double", None, None)), ("FLOAT", ("Double", None, None)), ("REAL", ("Double", None, None)),
    ("VARCHAR", ("V_String", None, None)), ("VARCHAR(254)", ("V_String", None, None)),
    ("BOOLEAN", ("Bool", None, None)), ("DATE", ("Date", None, None)),
    ("TIMESTAMP", ("DateTime", None, None)), ("TIMESTAMP WITH TIME ZONE", ("DateTime", None, None)),
    ("TIMESTAMP_NS", ("DateTime", None, None)), ("TIME", ("Time", None, None)),
    ("decimal(10,4)", ("FixedDecimal", 10, 4)),
])
def test_duckdb_to_alteryx_maps_every_listed_type(duckdb_type, expected):
    mapped = duckdb_to_alteryx(duckdb_type)
    assert (mapped["type"], mapped["size"], mapped["scale"]) == expected


@pytest.mark.parametrize("duckdb_type", ["BLOB", "INTERVAL", "UUID", "INTEGER[]", "STRUCT(A INTEGER)",
                                         "MAP(VARCHAR, INTEGER)", "TIME WITH TIME ZONE", "BIT", ""])
def test_duckdb_to_alteryx_refuses_anything_else(duckdb_type):
    with pytest.raises(ValueError):
        duckdb_to_alteryx(duckdb_type)


def test_a_time_value_crosses_as_iso_text():
    backend = DuckDBBackend()
    try:
        backend.execute("CREATE TABLE MIG_WORK.TIMES (T TIME)")
        backend.execute("INSERT INTO MIG_WORK.TIMES VALUES ('12:34:56'), (NULL)")
        table = handoff.table_from_backend(backend, "MIG_WORK.TIMES")
    finally:
        backend.close()
    assert table["fields"][0]["type"] == "Time"
    assert sorted(table["rows"], key=lambda row: row[0] is None) == [["12:34:56"], [None]]
    assert dt.time.fromisoformat(table["rows"][0][0]) == dt.time(12, 34, 56)
