"""Dry-running a segment's procedure against empty tables shaped like its contract."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lib.io import read_json
from lib.paths import Repo
import compile_check as cc

WF, SEG = "wf_0009", "seg_01"

PROC = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data
  t1_input AS (SELECT ID, NOTE FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ITEMS')),
  -- tool 2: Filter, True branch
  t2_filter AS (SELECT * FROM t1_input WHERE NOTE IS NOT NULL)
  SELECT ID, NOTE FROM t2_filter;
  INSERT INTO IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.ITEMS_OUT') SELECT ID, NOTE FROM MIG_WORK.WF0009_SEG_01_OUT;
  RETURN 'OK';
END;
$$;"""

COLUMNS = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": True},
           {"name": "NOTE", "type": "VARCHAR", "nullable": True}]
CONTRACT = {
    "segment": SEG,
    "inputs": [{"logical": "ITEMS", "columns": COLUMNS, "keys": ["ID"]}],
    "outputs": [
        {"stream": "2_True", "table": "MIG_WORK.WF0009_SEG_01_OUT", "kind": "work", "logical": None,
         "columns": COLUMNS, "keys": ["ID"]},
        {"stream": "2_True", "table": None, "kind": "target", "logical": "ITEMS_OUT", "tool_id": "7",
         "columns": COLUMNS, "keys": ["ID"]},
    ],
    "row_relation": "filter",
    "ordering": {"keys": ["ID"], "alteryx_deterministic": True},
    "tolerances": {},
}
CONTRACT["output"] = CONTRACT["outputs"][0]


def build(tmp_path, proc=PROC, contract=None):
    repo = Repo(tmp_path)
    path = repo.seg(WF, SEG, "proc.sql")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(proc, encoding="utf-8", newline="\n")
    (path.parent / "contract.json").write_text(
        json.dumps(contract if contract is not None else CONTRACT, indent=2) + "\n", encoding="utf-8", newline="\n")
    return repo


def test_a_matching_procedure_compiles(tmp_path):
    repo = build(tmp_path)
    report = cc.compile_check(repo, WF, SEG)
    assert report == {"status": "OK", "errors": [], "statements": 2}
    assert read_json(repo.seg(WF, SEG, "compile_check.json")) == report


def test_an_output_column_the_procedure_never_produces_is_an_error(tmp_path):
    contract = copy.deepcopy(CONTRACT)
    contract["outputs"][0]["columns"][0]["name"] = "IDENT"
    contract["output"] = contract["outputs"][0]
    report = cc.compile_check(build(tmp_path, contract=contract), WF, SEG)
    assert report["status"] == "ERROR"
    assert any("IDENT" in e and "MIG_WORK.WF0009_SEG_01_OUT" in e for e in report["errors"])


def test_sql_that_is_not_snowflake_is_an_error(tmp_path):
    report = cc.compile_check(build(tmp_path, proc=PROC.replace("SELECT ID, NOTE FROM t2_filter",
                                                                "SELEC ID, NOTE FROM t2_filter")), WF, SEG)
    assert report["status"] == "ERROR" and any("SELEC" in e for e in report["errors"])


def test_a_table_the_contract_never_mapped_is_an_error(tmp_path):
    report = cc.compile_check(build(tmp_path, proc=PROC.replace(".ITEMS'", ".UNMAPPED'")), WF, SEG)
    assert report["status"] == "ERROR" and any("UNMAPPED" in e for e in report["errors"])


def test_a_missing_work_output_table_is_an_error(tmp_path):
    proc = PROC.replace("MIG_WORK.WF0009_SEG_01_OUT AS", "MIG_WORK.WF0009_SEG_01_OTHER AS", 1)
    report = cc.compile_check(build(tmp_path, proc=proc), WF, SEG)
    assert report["status"] == "ERROR" and any("MIG_WORK.WF0009_SEG_01_OUT" in e for e in report["errors"])


def test_a_procedure_named_for_another_segment_is_an_error(tmp_path):
    report = cc.compile_check(build(tmp_path, proc=PROC.replace("MIG_WORK.WF0009_SEG_01(",
                                                                "MIG_WORK.WF0009_SEG_02(", 1)), WF, SEG)
    assert report["status"] == "ERROR"
    assert any("MIG_WORK.WF0009_SEG_02" in e and "MIG_WORK.WF0009_SEG_01" in e for e in report["errors"])


@pytest.mark.parametrize("params", [
    "SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING",          # RUN_ID missing
    "SRC_DB STRING, TGT_DB STRING, SRC_SCHEMA STRING, TGT_SCHEMA STRING, RUN_ID STRING",  # order
    "SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING, EXTRA STRING",
])
def test_a_signature_that_is_not_contract_c4_is_an_error(tmp_path, params):
    original = "SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING"
    report = cc.compile_check(build(tmp_path, proc=PROC.replace(original, params, 1)), WF, SEG)
    assert report["status"] == "ERROR"
    assert any("SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID" in e for e in report["errors"])


def test_execute_as_owner_is_an_error(tmp_path):
    report = cc.compile_check(build(tmp_path, proc=PROC.replace("EXECUTE AS CALLER", "EXECUTE AS OWNER")), WF, SEG)
    assert report["status"] == "ERROR"
    assert any("EXECUTE AS OWNER found" in e and "EXECUTE AS CALLER" in e for e in report["errors"])


def test_a_missing_execute_as_clause_is_an_error(tmp_path):
    report = cc.compile_check(build(tmp_path, proc=PROC.replace(" EXECUTE AS CALLER", "")), WF, SEG)
    assert report["status"] == "ERROR"
    assert any("no EXECUTE AS clause" in e and "EXECUTE AS CALLER" in e for e in report["errors"])


def test_scripting_outside_the_subset_is_an_error(tmp_path):
    report = cc.compile_check(build(tmp_path, proc=PROC.replace("RETURN 'OK';", "LET x := 1;\n  RETURN 'OK';")), WF, SEG)
    assert report["status"] == "ERROR" and any("supported procedure subset" in e for e in report["errors"])


def run_cli(tmp_path):
    return subprocess.run([sys.executable, str(Path(cc.__file__)), WF, SEG, "--root", str(tmp_path)],
                          capture_output=True, text=True)


def test_cli_exits_zero_when_the_procedure_compiles(tmp_path):
    build(tmp_path)
    done = run_cli(tmp_path)
    assert done.returncode == 0 and "OK" in done.stdout


def test_cli_exits_one_on_a_compile_error(tmp_path):
    build(tmp_path, proc=PROC.replace(".ITEMS'", ".UNMAPPED'"))
    done = run_cli(tmp_path)
    assert done.returncode == 1 and "UNMAPPED" in done.stdout + done.stderr
    assert read_json(Repo(tmp_path).seg(WF, SEG, "compile_check.json"))["status"] == "ERROR"


def test_cli_exits_two_when_there_is_nothing_to_check(tmp_path):
    assert run_cli(tmp_path).returncode == 2


def test_cli_unexpected_exception_exits_2(tmp_path, monkeypatch, capsys):
    """implementer-rules.md's CLI EXIT CODES note: any unexpected exception must
    traceback.print_exc() and exit 2 -- domain failures (a compile ERROR) still exit 1, usage
    errors (nothing to check) still exit 2 via FileNotFoundError, but a crash from somewhere else
    in compile_check() must never let a traceback escape with exit 1 or a bare stack trace."""
    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(cc, "compile_check", boom)
    assert cc.main([WF, SEG, "--root", str(tmp_path)]) == 2
    assert "RuntimeError" in capsys.readouterr().err
