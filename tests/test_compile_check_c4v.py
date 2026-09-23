"""Contract C4's documented table-reference form in `compile_check.py` (Task C4V).

Snowflake documents `IDENTIFIER( { string_literal | session_variable | bind_variable |
snowflake_scripting_variable } )`: a single value, not an expression. Two named checks keep every
procedure on that form -- `c4:identifier_expression` (an `IDENTIFIER(…)` whose argument is not one
`:<VAR>` a `LET` of the procedure declares, one string literal or one session variable) and
`c4:let_form` (a `LET` that is not `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA ||
'.<LOGICAL>'` or its `TGT` twin). The expression form these procedures used before is refused
here, while the local runner can still fold it -- the one test below that proves both at once is
why no committed artefact can carry it. Nothing here has run on a real Snowflake account; the first
real-account run confirms the documented form.
"""
import json

import pytest

import compile_check as cc
from lib.backend import DuckDBBackend
from lib.paths import Repo
from lib.proc_runner import run_proc
from tests.test_compile_check import CONTRACT, SEG, WF

PROC = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;
  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';
  LET ITEMS_OUT_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.ITEMS_OUT';
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data
  t1_input AS (SELECT ID, NOTE FROM IDENTIFIER(:ITEMS_SRC)),
  -- tool 2: Filter, True branch
  t2_filter AS (SELECT * FROM t1_input WHERE NOTE IS NOT NULL)
  SELECT ID, NOTE FROM t2_filter;
  INSERT INTO IDENTIFIER(:ITEMS_OUT_TGT) SELECT ID, NOTE FROM MIG_WORK.WF0009_SEG_01_OUT;
  RETURN 'OK';
END;
$$;"""
SOURCE_LET = "  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';\n"

#: The expression form every procedure used before Task C4V -- undocumented in Snowflake's grammar.
OLD_SOURCE = "IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ITEMS')"
OLD_TARGET = "IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.ITEMS_OUT')"


def build(tmp_path, proc=PROC):
    repo = Repo(tmp_path)
    path = repo.seg(WF, SEG, "proc.sql")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(proc, encoding="utf-8", newline="\n")
    (path.parent / "contract.json").write_text(json.dumps(CONTRACT, indent=2) + "\n", encoding="utf-8",
                                               newline="\n")
    return repo


def _named(report, check):
    return [error for error in report["errors"] if error.startswith(f"{check}: ")]


def test_a_procedure_in_the_documented_form_compiles(tmp_path):
    assert cc.compile_check(build(tmp_path), WF, SEG) == {"status": "OK", "errors": [], "statements": 2}


# --- c4:identifier_expression ---------------------------------------------------------------------

@pytest.mark.parametrize("argument", [
    ":SRC_DB || '.' || :SRC_SCHEMA || '.ITEMS'",   # the pre-C4V form
    "CONCAT(:SRC_DB, '.', :SRC_SCHEMA, '.ITEMS')",
    ":ITEMS_SRC || ''",
    "UPPER(:ITEMS_SRC)",
    "'MIGDB.' || :SRC_SCHEMA || '.ITEMS'",
])
def test_an_expression_inside_identifier_is_refused(tmp_path, argument):
    report = cc.compile_check(build(tmp_path, PROC.replace("IDENTIFIER(:ITEMS_SRC)",
                                                           f"IDENTIFIER({argument})")), WF, SEG)
    assert report["status"] == "ERROR"
    errors = _named(report, "c4:identifier_expression")
    assert len(errors) == 1 and f"IDENTIFIER({argument})" in errors[0], report
    assert "LET" in errors[0]  # it says what to write instead
    assert report["errors"] == errors  # nothing downstream of a refused shape is reported


def test_identifier_of_a_parameter_rather_than_a_let_variable_is_refused(tmp_path):
    report = cc.compile_check(build(tmp_path, PROC.replace("IDENTIFIER(:ITEMS_SRC)", "IDENTIFIER(:SRC_DB)")),
                              WF, SEG)
    errors = _named(report, "c4:identifier_expression")
    assert len(errors) == 1 and "IDENTIFIER(:SRC_DB)" in errors[0] and "LET" in errors[0], report


@pytest.mark.parametrize("argument", ["'MIGDB.MIG_COMPILE.ITEMS'", "$ITEMS_TABLE"])
def test_a_string_literal_or_a_session_variable_is_a_documented_argument(tmp_path, argument):
    report = cc.compile_check(build(tmp_path, PROC.replace("IDENTIFIER(:ITEMS_SRC)",
                                                           f"IDENTIFIER({argument})")), WF, SEG)
    assert not [error for error in report["errors"] if error.startswith("c4:")], report


def test_the_local_runner_still_folds_the_old_form_but_compile_check_refuses_it(tmp_path):
    """The one place the pre-C4V form is still accepted is `proc_runner.bind`'s fold, and this is
    the test that keeps that acceptance harmless: the same procedure runs on the DuckDB double and
    is refused by `compile_check.py`, which every translated and canned procedure goes through."""
    old = (PROC.replace(SOURCE_LET, "")
           .replace("  LET ITEMS_OUT_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.ITEMS_OUT';\n", "")
           .replace("IDENTIFIER(:ITEMS_SRC)", OLD_SOURCE)
           .replace("IDENTIFIER(:ITEMS_OUT_TGT)", OLD_TARGET))
    assert "LET" not in old

    backend = DuckDBBackend()
    fields = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
              {"name": "NOTE", "type": "V_String", "size": 20, "scale": None}]
    backend.load_table("MIGDB.MIG_GOLDEN_X.ITEMS", {"fields": fields, "rows": [[1, "a"]]})
    backend.load_table("MIGDB.MIG_WORK.ITEMS_OUT", {"fields": fields, "rows": []})
    run_proc(backend, old, {"SRC_DB": "MIGDB", "SRC_SCHEMA": "MIG_GOLDEN_X", "TGT_DB": "MIGDB",
                            "TGT_SCHEMA": "MIG_WORK", "RUN_ID": "r1"})
    assert backend.query("SELECT ID FROM MIGDB.MIG_WORK.ITEMS_OUT")[1] == [(1,)]

    report = cc.compile_check(build(tmp_path, old), WF, SEG)
    assert report["status"] == "ERROR"
    errors = _named(report, "c4:identifier_expression")
    assert [OLD_SOURCE in error for error in errors] == [True, False]
    assert [OLD_TARGET in error for error in errors] == [False, True]


# --- c4:let_form ------------------------------------------------------------------------------------

@pytest.mark.parametrize("let_statement", [
    "LET ITEMS VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS'",          # no _SRC suffix
    "LET ITEMS_TGT VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS'",      # the wrong suffix
    "LET OTHER_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS'",      # another logical's name
    "LET items_src VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS'",      # not upper case
    "LET ITEMS_SRC VARCHAR := SRC_DB || '.' || TGT_SCHEMA || '.ITEMS'",      # SRC and TGT mixed
    "LET ITEMS_SRC VARCHAR := SRC_DB || '.' || RUN_ID || '.ITEMS'",          # not the schema
    "LET ITEMS_SRC VARCHAR := SRC_DB || SRC_SCHEMA || '.ITEMS'",             # a part missing
    "LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.' || 'ITEMS'",  # a part split
    "LET ITEMS_SRC VARCHAR := 'MIGDB.MIG_COMPILE.ITEMS'",                      # a literal name
    "LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.items'",      # not a logical name
    "LET ITEMS_SRC NUMBER := 1",                                               # the runner's refusal
    "LET ITEMS_SRC VARCHAR := UPPER(SRC_DB)",                                 # the runner's refusal
])
def test_a_let_that_does_not_match_the_rule_is_refused(tmp_path, let_statement):
    report = cc.compile_check(build(tmp_path, PROC.replace(SOURCE_LET, f"  {let_statement};\n")), WF, SEG)
    assert report["status"] == "ERROR"
    errors = _named(report, "c4:let_form")
    assert errors and let_statement in errors[0], report


@pytest.mark.parametrize("let_statement", [
    "LET ITEMS_SRC VARCHAR := :SRC_DB || '.' || :SRC_SCHEMA || '.ITEMS'",
    "LET ITEMS_SRC VARCHAR := SRC_DB || '.' || :SRC_SCHEMA || '.ITEMS'",
])
def test_a_colon_inside_a_let_is_a_named_refusal(tmp_path, let_statement):
    """Fix round 1: inside a LET the procedure's arguments are named without a colon (Snowflake's
    documented expression syntax); the colon belongs to IDENTIFIER(:<VAR>) inside a SQL statement."""
    report = cc.compile_check(build(tmp_path, PROC.replace(SOURCE_LET, f"  {let_statement};\n")), WF, SEG)
    assert report["status"] == "ERROR"
    errors = _named(report, "c4:let_form")
    assert len(errors) == 1 and "without a colon" in errors[0] and let_statement in errors[0], report


def test_every_documented_let_names_its_logical_and_its_side(tmp_path):
    """`<LOGICAL>_SRC` for a source, `<LOGICAL>_TGT` for a target: a logical that is both gets both."""
    proc = PROC.replace(SOURCE_LET, SOURCE_LET
                        + "  LET ITEMS_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.ITEMS';\n")
    assert cc.compile_check(build(tmp_path, proc), WF, SEG)["status"] == "OK"


# --- fix round 2: RETURN, comments inside a LET, the exact header ------------------------------------

def _with_return(ret: str) -> str:
    return PROC.replace("  RETURN 'OK';", f"  {ret};")


@pytest.mark.parametrize("ret", [
    "RETURN 'OK' || RUN_ID",
    "RETURN (SELECT COUNT(*) FROM MIG_WORK.WF0009_SEG_01_OUT)",
    f"RETURN (SELECT COUNT(*) FROM {OLD_SOURCE})",   # the old IDENTIFIER form, hidden in a RETURN
])
def test_a_return_that_is_not_one_string_literal_is_refused(tmp_path, ret):
    report = cc.compile_check(build(tmp_path, _with_return(ret)), WF, SEG)
    errors = _named(report, "c4:return_form")
    assert report["status"] == "ERROR" and len(errors) == 1 and ret in errors[0], report


def test_a_return_with_a_trailing_comment_is_still_one_string_literal(tmp_path):
    assert cc.compile_check(build(tmp_path, _with_return("RETURN 'OK' /* done */")), WF, SEG)["status"] == "OK"


def test_a_return_before_the_last_statement_is_refused(tmp_path):
    """Snowflake stops at RETURN; the local double runs every statement. A statement after a RETURN
    would be validated here and never run there."""
    proc = PROC.replace("  RETURN 'OK';\n", "").replace(
        "  INSERT INTO IDENTIFIER(:ITEMS_OUT_TGT)", "  RETURN 'OK';\n  INSERT INTO IDENTIFIER(:ITEMS_OUT_TGT)")
    errors = _named(cc.compile_check(build(tmp_path, proc), WF, SEG), "c4:return_form")
    assert len(errors) == 1 and "last" in errors[0]


def test_two_returns_are_refused(tmp_path):
    errors = _named(cc.compile_check(build(tmp_path, _with_return("RETURN 'OK';\n  RETURN 'again'")), WF, SEG),
                    "c4:return_form")
    assert errors and "one RETURN" in errors[0]


@pytest.mark.parametrize("let_statement", [
    "LET ITEMS_SRC /* c */ VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS'",
    "LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS' -- c\n",
])
def test_a_comment_inside_a_let_is_a_let_form_refusal(tmp_path, let_statement):
    report = cc.compile_check(build(tmp_path, PROC.replace(SOURCE_LET, f"  {let_statement};\n")), WF, SEG)
    errors = _named(report, "c4:let_form")
    assert errors and "comment inside a LET" in errors[0], report


@pytest.mark.parametrize("declaration", ["SRC_DB VARCHAR", "SRC_DB STRING DEFAULT 'FINANCE'", "SRC_DB TEXT"])
def test_a_parameter_that_is_not_exactly_name_string_is_refused(tmp_path, declaration):
    """Contract C4's header is exactly `(SRC_DB STRING, …, RUN_ID STRING)`; the policy's SQL judge
    denies any other, and compile_check agrees (types compared case-insensitively)."""
    report = cc.compile_check(build(tmp_path, PROC.replace("SRC_DB STRING,", f"{declaration},", 1)), WF, SEG)
    assert report["status"] == "ERROR"
    assert any("STRING" in error and declaration in error for error in report["errors"]), report


def test_a_lower_case_header_is_still_contract_c4(tmp_path):
    header = "(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)"
    assert cc.compile_check(build(tmp_path, PROC.replace(header, header.lower())), WF, SEG)["status"] == "OK"


# --- fix round 3: c4:identifier_role -- a _SRC name is only read, a _TGT name only written ---------

def _with_statement(statement: str) -> str:
    return PROC.replace("  RETURN 'OK';", f"  {statement};\n  RETURN 'OK';")


@pytest.mark.parametrize("statement", [
    "CREATE OR REPLACE TABLE IDENTIFIER(:ITEMS_SRC) AS SELECT 1 AS ID",
    "INSERT INTO IDENTIFIER(:ITEMS_SRC) SELECT ID, NOTE FROM MIG_WORK.WF0009_SEG_01_OUT",
    "MERGE INTO IDENTIFIER(:ITEMS_SRC) t USING MIG_WORK.WF0009_SEG_01_OUT s ON t.ID = s.ID WHEN MATCHED THEN DELETE",
    "UPDATE IDENTIFIER(:ITEMS_SRC) SET NOTE = 'x'",
    "DELETE FROM IDENTIFIER(:ITEMS_SRC) WHERE ID = 1",
    "TRUNCATE TABLE IDENTIFIER(:ITEMS_SRC)",
])
def test_a_src_name_used_as_a_write_target_is_refused(tmp_path, statement):
    report = cc.compile_check(build(tmp_path, _with_statement(statement)), WF, SEG)
    errors = _named(report, "c4:identifier_role")
    assert report["status"] == "ERROR" and len(errors) == 1, report
    assert "IDENTIFIER(:ITEMS_SRC)" in errors[0] and "only ever read" in errors[0]


@pytest.mark.parametrize("statement", [
    "CREATE OR REPLACE TABLE MIG_WORK.X AS SELECT ID FROM IDENTIFIER(:ITEMS_OUT_TGT)",
    "CREATE OR REPLACE TABLE MIG_WORK.X AS SELECT a.ID FROM MIG_WORK.WF0009_SEG_01_OUT a "
    "JOIN IDENTIFIER(:ITEMS_OUT_TGT) b ON a.ID = b.ID",
    "MERGE INTO MIG_WORK.WF0009_SEG_01_OUT t USING IDENTIFIER(:ITEMS_OUT_TGT) s ON t.ID = s.ID "
    "WHEN MATCHED THEN DELETE",
])
def test_a_tgt_name_read_as_a_source_is_refused(tmp_path, statement):
    report = cc.compile_check(build(tmp_path, _with_statement(statement)), WF, SEG)
    errors = _named(report, "c4:identifier_role")
    assert report["status"] == "ERROR" and len(errors) == 1, report
    assert "IDENTIFIER(:ITEMS_OUT_TGT)" in errors[0] and "only ever written" in errors[0]


def test_a_tgt_name_may_be_deleted_from_and_updated(tmp_path):
    """A PreSQL `DELETE FROM` and a PostSQL `UPDATE` of the target (wf_0003's shape) write it."""
    proc = _with_statement("DELETE FROM IDENTIFIER(:ITEMS_OUT_TGT) WHERE ID IS NULL").replace(
        "  RETURN 'OK';", "  UPDATE IDENTIFIER(:ITEMS_OUT_TGT) SET NOTE = 'x' WHERE NOTE IS NULL;\n  RETURN 'OK';")
    assert cc.compile_check(build(tmp_path, proc), WF, SEG)["status"] == "OK"


def test_the_cli_exits_1_on_a_c4_refusal(tmp_path, capsys):
    build(tmp_path, PROC.replace("IDENTIFIER(:ITEMS_SRC)", OLD_SOURCE))
    assert cc.main([WF, SEG, "--root", str(tmp_path)]) == 1
    assert "c4:identifier_expression" in capsys.readouterr().err
