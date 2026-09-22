"""SQL runtime: Snowflake dialect in, DuckDB out (plan task 6, contracts C1/C3).

Nothing here has run against a real Snowflake account. "Works" means: sqlglot's Snowflake
parser accepts the statement and DuckDB produces the value we hand-computed.
"""
import logging
from decimal import Decimal

import pytest

from lib.backend import DuckDBBackend, BackendError, local_name, get_backend
from lib.types_map import alteryx_to_snowflake, type_family

T = {"fields": [{"name": "ACCT", "type": "V_String", "size": 20, "scale": None},
                {"name": "AMT", "type": "FixedDecimal", "size": 19, "scale": 2},
                {"name": "D", "type": "Date", "size": 10, "scale": None}],
     "rows": [["A1", __import__("decimal").Decimal("10.50"), "2024-02-29"], ["A2", None, None]]}

def test_names(): assert local_name("FIN.RAW.GL") == ("FIN__RAW", "GL") and local_name('MIG_WORK."T"') == ("MIG_WORK", "T")
def test_types():
    assert alteryx_to_snowflake(T["fields"][1]) == "NUMBER(19,2)" and alteryx_to_snowflake({"type": "String", "size": 10}) == "VARCHAR(10)"
    assert type_family("NUMBER(19,2)") == "number" and type_family("TIMESTAMP_NTZ") == "timestamp" and type_family("VARCHAR(10)") == "string"

def test_three_part_names_transient_and_snowflake_functions():
    b = DuckDBBackend()
    b.load_table("MIGDB.MIG_GOLDEN.X", T)
    b.execute("""CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.OUT AS
                 WITH t1_input AS (SELECT ACCT, AMT, D FROM MIGDB.MIG_GOLDEN.X)
                 SELECT ACCT, IFF(AMT IS NULL, 0, AMT) AS AMT, TRY_TO_NUMBER('abc') AS BAD, DATEADD(day, 1, D) AS NEXT_D
                 FROM t1_input QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCT ORDER BY AMT) = 1""")
    cols, rows = b.query("SELECT ACCT, AMT, BAD, NEXT_D FROM MIG_WORK.OUT ORDER BY ACCT")
    assert [c.upper() for c in cols] == ["ACCT", "AMT", "BAD", "NEXT_D"]
    assert rows[0][0] == "A1" and float(rows[0][1]) == 10.5 and rows[0][2] is None and str(rows[0][3])[:10] == "2024-03-01"
    assert float(rows[1][1]) == 0 and [c["name"].upper() for c in b.table_columns("MIG_WORK.OUT")] == cols and b.table_exists("MIG_WORK.OUT")

def test_merge_runs():
    b = DuckDBBackend(); b.load_table("MIGDB.MIG_WORK.TGT", T)
    b.load_table("MIG_WORK.SRC", {"fields": T["fields"], "rows": [["A2", 5, "2026-01-01"], ["A3", 7, None]]})
    b.execute("""MERGE INTO MIGDB.MIG_WORK.TGT t USING MIG_WORK.SRC s ON t.ACCT = s.ACCT
                 WHEN MATCHED THEN UPDATE SET AMT = s.AMT, D = s.D WHEN NOT MATCHED THEN INSERT (ACCT, AMT, D) VALUES (s.ACCT, s.AMT, s.D)""")
    assert b.query("SELECT COUNT(*), SUM(AMT) FROM MIGDB.MIG_WORK.TGT")[1][0] == (3, __import__("decimal").Decimal("22.50"))

def test_bad_sql_names_the_statement():
    with pytest.raises(BackendError, match="SELEC"): DuckDBBackend().execute("SELEC 1")
def test_snowflake_backend_fails_clearly_without_connector():
    with pytest.raises(BackendError, match="snowflake-connector-python"): get_backend("snowflake")


# --- further coverage for behaviour the brief describes in prose ------------------------------

S = {"fields": [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
                {"name": "GRP", "type": "V_String", "size": 10, "scale": None},
                {"name": "TXT", "type": "String", "size": 20, "scale": None},
                {"name": "AMT", "type": "FixedDecimal", "size": 19, "scale": 2},
                {"name": "D", "type": "Date", "size": 10, "scale": None},
                {"name": "TS", "type": "DateTime", "size": 19, "scale": None},
                {"name": "OK", "type": "Bool", "size": 1, "scale": None}],
     "rows": [[1, "G1", "ab-12", Decimal("10.50"), "2024-02-29", "2024-02-29 13:45:01", True],
              [2, "G1", "cd-34", Decimal("2.25"), "2024-03-01", "2024-03-01 00:00:00", False],
              [3, "G2", None, None, None, None, None]]}


@pytest.fixture
def src():
    b = DuckDBBackend()
    b.load_table("MIGDB.MIG_GOLDEN.S", S)
    yield b
    b.close()


def scalar(backend, expression, where="ID = 1"):
    return backend.query(f"SELECT {expression} AS V FROM MIGDB.MIG_GOLDEN.S WHERE {where}")[1][0][0]


@pytest.mark.parametrize("expression,expected", [
    ("IFF(AMT IS NULL, 0, AMT)", Decimal("10.50")),
    ("TRY_TO_NUMBER('abc')", None),
    ("TRY_TO_NUMBER('12.75', 10, 2)", Decimal("12.75")),
    ("TRY_TO_DOUBLE('abc')", None),
    ("TRY_TO_DOUBLE('1.5')", 1.5),
    ("TO_VARCHAR(D, 'YYYY-MM-DD')", "2024-02-29"),
    ("TO_VARCHAR(D)", "2024-02-29"),
    ("TO_CHAR(TS, 'YYYY-MM-DD HH24:MI:SS')", "2024-02-29 13:45:01"),
    ("LEFT(TXT, 2)", "ab"),
    ("LPAD(TXT, 7, '0')", "00ab-12"),
    ("UPPER(TRIM('  ab  '))", "AB"),
    (r"REGEXP_REPLACE(TXT, '([a-z]+)-([0-9]+)', '\\2-\\1')", "12-ab"),
    ("REGEXP_SUBSTR(TXT, '([a-z]+)-([0-9]+)', 1, 1, 'e', 2)", "12"),
    ("DATEDIFF(day, D, DATE '2024-03-10')", 10),
    ("NULLIF(ID, 1)", None),
    ("COALESCE(TO_VARCHAR(TRY_TO_DATE('29/02/2024', 'DD/MM/YYYY'), 'YYYY-MM-DD'), 'bad')", "2024-02-29"),
    ("COALESCE(TO_VARCHAR(TRY_TO_DATE('nonsense', 'DD/MM/YYYY'), 'YYYY-MM-DD'), 'bad')", "bad"),
])
def test_scalar_snowflake_functions(src, expression, expected):
    assert scalar(src, expression) == expected


def test_dateadd_on_a_date(src):
    assert str(scalar(src, "DATEADD(day, 1, D)"))[:10] == "2024-03-01"


DUPES = {"fields": [{"name": "K", "type": "V_String", "size": 4, "scale": None},
                    {"name": "SEQ", "type": "Int32", "size": 4, "scale": None},
                    {"name": "VAL", "type": "V_String", "size": 20, "scale": None}],
         "rows": [["A", 1, "a-first"], ["A", 3, "a-last"], ["A", 2, "a-middle"],
                  ["B", 5, "b-only"], ["C", 2, "c-low"], ["C", 9, "c-high"]]}


@pytest.mark.parametrize("direction,expected", [
    ("DESC", [("A", 3, "a-last"), ("B", 5, "b-only"), ("C", 9, "c-high")]),
    ("ASC", [("A", 1, "a-first"), ("B", 5, "b-only"), ("C", 2, "c-low")]),
])
def test_qualify_row_number_keeps_one_row_per_partition(src, direction, expected):
    # Six rows in, three out, and which three depends on the ORDER BY direction: if QUALIFY were
    # dropped in translation this returns all six and the equality fails either way round.
    src.load_table("MIGDB.MIG_GOLDEN.DUPES", DUPES)
    rows = src.query(f"""SELECT K, SEQ, VAL FROM MIGDB.MIG_GOLDEN.DUPES
                         QUALIFY ROW_NUMBER() OVER (PARTITION BY K ORDER BY SEQ {direction}) = 1
                         ORDER BY K""")[1]
    assert rows == expected


@pytest.mark.parametrize("value,expected", [("10.567", "10.57"), ("2.675", "2.68"), ("-2.675", "-2.68"),
                                            ("1.005", "1.01"), ("-1.005", "-1.01"), ("8.835", "8.84")])
def test_round_on_an_exact_number_goes_half_away_from_zero(src, value, expected):
    # CAST to NUMBER first is the pattern translated procedures must use: ROUND then sees an exact
    # decimal and DuckDB breaks ties away from zero, which is Snowflake's documented default
    # rounding mode. (Not verified on a Snowflake account — see the module docstring.)
    # 1.005 is the case that separates the two tie rules: half-away gives 1.01, banker's rounding
    # gives 1.00. 2.675 and 8.835 agree under both, so neither could carry this test alone.
    assert scalar(src, f"ROUND(CAST({value} AS NUMBER(38,10)), 2)") == Decimal(expected)


def test_round_without_the_cast_rounds_the_stored_binary_value_instead():
    # The contrast that makes the cast worth writing. Snowflake's FLOAT is an 8-byte binary float
    # and sqlglot maps it to DuckDB's DOUBLE, so the value rounded is the nearest double, not the
    # decimal someone typed: 1.005 is stored as 1.00499999999999989…, and ROUND returns 1.0 where
    # the exact-decimal form returns 1.01. Without the cast the storage type alone can manufacture
    # a ROUNDING parity diff. 2.675 happens to agree here, which is why it cannot be the only case.
    b = DuckDBBackend()
    assert b.query("SELECT ROUND(1.005::FLOAT, 2), ROUND(CAST(1.005 AS NUMBER(38,10)), 2)")[1] == \
        [(1.0, Decimal("1.01"))]
    assert b.query("SELECT ROUND(2.675::FLOAT, 2), ROUND(-2.675::FLOAT, 2)")[1] == [(2.68, -2.68)]


def test_running_sum_window(src):
    rows = src.query("""SELECT ID, SUM(AMT) OVER (PARTITION BY GRP ORDER BY ID
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS RUNNING
                        FROM MIGDB.MIG_GOLDEN.S ORDER BY ID""")[1]
    assert [r[1] for r in rows] == [Decimal("10.50"), Decimal("12.75"), None]


def test_listagg_count_if_and_is_distinct_from(src):
    cols, rows = src.query("""SELECT LISTAGG(GRP, '|') WITHIN GROUP (ORDER BY ID) AS ALL_GRP,
                                     COUNT_IF(AMT > 5) AS BIG,
                                     COUNT_IF(AMT IS DISTINCT FROM 10.50) AS NOT_TEN
                              FROM MIGDB.MIG_GOLDEN.S""")
    assert [c.upper() for c in cols] == ["ALL_GRP", "BIG", "NOT_TEN"]
    assert rows == [("G1|G1|G2", 1, 2)]


def test_union_all_keeps_duplicates(src):
    assert len(src.query("""SELECT GRP FROM MIGDB.MIG_GOLDEN.S UNION ALL
                            SELECT GRP FROM MIGDB.MIG_GOLDEN.S""")[1]) == 6


def test_insert_select_update_and_delete(src):
    src.execute("CREATE OR REPLACE TRANSIENT TABLE MIGDB.MIG_WORK.MIRROR AS SELECT ID, GRP, AMT FROM MIGDB.MIG_GOLDEN.S")
    src.execute("INSERT INTO MIGDB.MIG_WORK.MIRROR (ID, GRP, AMT) SELECT ID + 10, GRP, AMT FROM MIGDB.MIG_GOLDEN.S WHERE ID = 1")
    src.execute("UPDATE MIGDB.MIG_WORK.MIRROR SET GRP = 'G9' WHERE ID = 11")
    src.execute("DELETE FROM MIGDB.MIG_WORK.MIRROR WHERE AMT IS NULL")
    assert src.query("SELECT ID, GRP FROM MIGDB.MIG_WORK.MIRROR ORDER BY ID")[1] == [(1, "G1"), (2, "G1"), (11, "G9")]


def test_translate_flattens_three_part_names_but_leaves_cte_references_alone():
    duck = DuckDBBackend().translate(
        "WITH t1_input AS (SELECT a FROM FIN.RAW.GL) SELECT a FROM t1_input")
    assert "FIN__RAW.GL" in duck and "FROM t1_input" in duck


def test_a_qualified_table_is_flattened_even_when_a_cte_shares_its_name():
    # A reference with a db or catalog qualifier can never be a CTE: SQL has no syntax to qualify
    # one. So FIN.RAW.GL is the real table no matter what the CTEs are called.
    duck = DuckDBBackend().translate("WITH GL AS (SELECT 1 AS a) SELECT a FROM FIN.RAW.GL")
    assert "FIN__RAW.GL" in duck


def test_a_cte_and_a_qualified_table_of_the_same_name_read_different_data():
    b = DuckDBBackend()
    b.load_table("FIN.RAW.GL", {"fields": [{"name": "A", "type": "Int32", "size": 4, "scale": None}],
                                "rows": [[100], [200]]})
    rows = b.query("""WITH GL AS (SELECT 1 AS A UNION ALL SELECT 2 AS A)
                      SELECT (SELECT SUM(A) FROM GL) AS FROM_CTE,
                             (SELECT SUM(A) FROM FIN.RAW.GL) AS FROM_TABLE""")[1]
    assert rows == [(3, 300)]


def test_translate_emits_no_sqlglot_warnings(caplog):
    with caplog.at_level(logging.WARNING, logger="sqlglot"):
        DuckDBBackend().translate(
            "CREATE OR REPLACE TRANSIENT TABLE A.B.T AS SELECT TO_CHAR(D, 'YYYY-MM-DD') AS S FROM A.B.U")
    assert caplog.records == []


def test_unsupported_to_char_format_is_reported_not_silently_dropped():
    with pytest.raises(BackendError, match="TO_CHAR/TO_VARCHAR format"):
        DuckDBBackend().translate("SELECT TO_CHAR(D, 'QQ') FROM T")


def test_load_table_creates_typed_columns(src):
    types = {c["name"].upper(): c["type"] for c in src.table_columns("MIGDB.MIG_GOLDEN.S")}
    assert types == {"ID": "DECIMAL(38,0)", "GRP": "VARCHAR", "TXT": "VARCHAR", "AMT": "DECIMAL(19,2)",
                     "D": "DATE", "TS": "TIMESTAMP", "OK": "BOOLEAN"}


def test_create_view_and_table_exists(src):
    src.create_view("MIGDB.MIG_GOLDEN_WF0001_NORMAL.ITEMS", "MIGDB.MIG_GOLDEN.S")
    assert src.table_exists("MIGDB.MIG_GOLDEN_WF0001_NORMAL.ITEMS")
    assert not src.table_exists("MIGDB.MIG_GOLDEN_WF0001_NORMAL.NOPE")
    assert src.query("SELECT COUNT(*) FROM MIGDB.MIG_GOLDEN_WF0001_NORMAL.ITEMS")[1] == [(3,)]


def test_empty_table_loads_and_stays_empty():
    b = DuckDBBackend()
    b.load_table("MIG_WORK.EMPTY", {"fields": S["fields"], "rows": []})
    assert b.table_exists("MIG_WORK.EMPTY") and b.query("SELECT COUNT(*) FROM MIG_WORK.EMPTY")[1] == [(0,)]


def test_unknown_backend_kind_is_rejected():
    with pytest.raises(BackendError, match="unknown backend"): get_backend("oracle")


def test_type_families_cover_every_alteryx_type():
    families = {f["type"]: type_family(alteryx_to_snowflake(dict(f, size=f.get("size", 10), scale=f.get("scale"))))
                for f in [{"type": t, "size": 10, "scale": 2} for t in
                          ["Byte", "Int16", "Int32", "Int64", "FixedDecimal", "Float", "Double", "String",
                           "WString", "V_String", "V_WString", "Bool", "Date", "Time", "DateTime", "Blob"]]}
    assert families == {"Byte": "number", "Int16": "number", "Int32": "number", "Int64": "number",
                        "FixedDecimal": "number", "Float": "float", "Double": "float", "String": "string",
                        "WString": "string", "V_String": "string", "V_WString": "string", "Bool": "bool",
                        "Date": "date", "Time": "time", "DateTime": "timestamp", "Blob": "binary"}


def test_unknown_alteryx_type_is_not_guessed():
    with pytest.raises(ValueError, match="SpatialObj"): alteryx_to_snowflake({"type": "SpatialObj", "size": 0})
