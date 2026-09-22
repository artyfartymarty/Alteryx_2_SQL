"""The restricted stored-procedure shape (plan contract C4) parsed, bound and executed locally."""
import pytest

from lib.backend import DuckDBBackend
from lib.proc_runner import parse_proc, bind, run_proc, ProcError

PROC = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data; note the semicolon in this comment; and 'a quote
  t1_input AS (SELECT ID, NOTE FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ITEMS')),
  -- tool 2: Filter, True branch
  t2_filter AS (SELECT * FROM t1_input WHERE NOTE <> 'x;y' AND NOTE <> ':SRC_DB')
  SELECT ID, NOTE FROM t2_filter;
  INSERT INTO IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.ITEMS_OUT') SELECT ID, NOTE FROM MIG_WORK.WF0009_SEG_01_OUT;
  RETURN 'OK';
END;
$$;"""
ARGS = {"SRC_DB": "MIGDB", "SRC_SCHEMA": "MIG_GOLDEN_WF0009_NORMAL", "TGT_DB": "MIGDB", "TGT_SCHEMA": "MIG_WORK", "RUN_ID": "r1"}
F = [{"name": "ID", "type": "Int32", "size": 4, "scale": None}, {"name": "NOTE", "type": "V_String", "size": 20, "scale": None}]

def test_parse_proc():
    p = parse_proc(PROC)
    assert p.name == "MIG_WORK.WF0009_SEG_01" and p.params == ["SRC_DB", "SRC_SCHEMA", "TGT_DB", "TGT_SCHEMA", "RUN_ID"]
    assert p.execute_as == "CALLER" and p.session == {"TIMEZONE": "America/New_York", "WEEK_START": "1"} and len(p.statements) == 2

def test_bind_leaves_string_literals_alone():
    out = bind("SELECT ':SRC_DB' AS s FROM IDENTIFIER(:src_db || '.' || :SRC_SCHEMA || '.T')", ARGS)
    assert "':SRC_DB'" in out and "MIGDB.MIG_GOLDEN_WF0009_NORMAL.T" in out and "IDENTIFIER" not in out

def test_run_proc_end_to_end():
    b = DuckDBBackend()
    b.load_table("MIG_GOLDEN.WF0009_NORMAL_IN_1", {"fields": F, "rows": [[1, "keep"], [2, "x;y"], [3, None]]})
    b.create_view("MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS", "MIG_GOLDEN.WF0009_NORMAL_IN_1")
    b.load_table("MIGDB.MIG_WORK.ITEMS_OUT", {"fields": F, "rows": []})
    run_proc(b, PROC, ARGS)
    assert b.query("SELECT ID FROM MIGDB.MIG_WORK.ITEMS_OUT")[1] == [(1,)]

@pytest.mark.parametrize("stmt", ["LET x := 1", "EXECUTE IMMEDIATE 'select 1'", "FOR r IN c DO NULL; END FOR"])
def test_scripting_outside_the_subset_is_rejected(stmt):
    with pytest.raises(ProcError, match="supported procedure subset"): parse_proc(PROC.replace("RETURN 'OK';", stmt + ";\n  RETURN 'OK';"))


# --- further coverage for behaviour the brief describes in prose ------------------------------

@pytest.mark.parametrize("stmt", ["DECLARE x INT", "IF (x = 1) THEN NULL", "WHILE (x) DO NULL",
                                  "CALL MIG_WORK.OTHER_PROC()"])
def test_every_scripting_keyword_is_rejected(stmt):
    with pytest.raises(ProcError, match="supported procedure subset"):
        parse_proc(PROC.replace("RETURN 'OK';", stmt + ";\n  RETURN 'OK';"))


def test_comments_stay_attached_to_the_statement_they_precede():
    p = parse_proc(PROC)
    assert p.statements[0].startswith("CREATE OR REPLACE TRANSIENT TABLE")
    assert "-- tool 1: Input Data; note the semicolon in this comment; and 'a quote" in p.statements[0]
    assert p.statements[1].startswith("INSERT INTO")


def test_a_leading_comment_before_a_statement_is_kept_and_does_not_hide_the_keyword():
    proc = PROC.replace("  INSERT INTO", "  -- tool 9: Output Data; write it\n  INSERT INTO")
    p = parse_proc(proc)
    assert p.statements[1].startswith("-- tool 9: Output Data; write it")
    assert len(p.statements) == 2


def test_execute_as_owner_is_read_from_the_header():
    p = parse_proc(PROC.replace("EXECUTE AS CALLER", "EXECUTE AS OWNER"))
    assert p.execute_as == "OWNER"


def test_bind_is_case_insensitive_and_quotes_the_value():
    assert bind("SELECT :run_id, :RUN_ID", ARGS) == "SELECT 'r1', 'r1'"


def test_bind_doubles_single_quotes_inside_a_value():
    assert bind("SELECT :RUN_ID", {"RUN_ID": "it's"}) == "SELECT 'it''s'"


def test_bind_leaves_double_colon_casts_alone():
    assert bind("SELECT ID::SRC_DB_TYPE, :RUN_ID", ARGS) == "SELECT ID::SRC_DB_TYPE, 'r1'"


def test_bind_leaves_parameters_inside_comments_alone():
    out = bind("-- reads :SRC_DB\nSELECT :RUN_ID /* not :SRC_DB */", ARGS)
    assert out == "-- reads :SRC_DB\nSELECT 'r1' /* not :SRC_DB */"


def test_bind_rejects_an_identifier_that_is_not_folded_literals():
    with pytest.raises(ProcError, match="IDENTIFIER"):
        bind("SELECT * FROM IDENTIFIER(TABLE_NAME_COLUMN)", ARGS)


def test_run_proc_reports_a_missing_argument():
    with pytest.raises(ProcError, match="TGT_SCHEMA"):
        run_proc(DuckDBBackend(), PROC, {k: v for k, v in ARGS.items() if k != "TGT_SCHEMA"})


def test_run_proc_returns_the_parsed_procedure():
    b = DuckDBBackend()
    b.load_table("MIG_GOLDEN.WF0009_NORMAL_IN_1", {"fields": F, "rows": []})
    b.create_view("MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS", "MIG_GOLDEN.WF0009_NORMAL_IN_1")
    b.load_table("MIGDB.MIG_WORK.ITEMS_OUT", {"fields": F, "rows": []})
    info = run_proc(b, PROC, ARGS)
    assert info.name == "MIG_WORK.WF0009_SEG_01" and info.session["WEEK_START"] == "1"


def test_a_procedure_without_a_dollar_quoted_body_is_rejected():
    with pytest.raises(ProcError, match=r"\$\$"):
        parse_proc("CREATE OR REPLACE PROCEDURE MIG_WORK.P() RETURNS STRING LANGUAGE SQL AS 'x';")
