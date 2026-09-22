"""Segment validation driver tests (plan task 14).

A self-contained, hand-built workflow (`wf_0009`) -- never the canned hand-migrations task 13
writes (see `tests/test_e2e_parity.py` for those). `seg_01` filters `ITEMS` and writes both a work
table (stream `2_T`) and a target `ITEMS_OUT`; `seg_02` (added only where needed) consumes `seg_01`'s
golden intermediate directly, proving a downstream segment never needs its upstream's procedure to
have actually run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import validate_segment as vs
from lib import typed_csv
from lib.io import read_json, write_json, write_yaml
from lib.paths import Repo

WF = "wf_0009"
SEG = "seg_01"

F = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
     {"name": "NOTE", "type": "V_String", "size": 20, "scale": None}]

COLUMNS = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": True},
           {"name": "NOTE", "type": "VARCHAR", "nullable": True}]

CONTRACT = {
    "segment": SEG,
    "inputs": [{"logical": "ITEMS", "columns": COLUMNS, "keys": ["ID"]}],
    "outputs": [
        {"stream": "2_T", "table": "MIG_WORK.WF0009_SEG_01_OUT", "kind": "work", "logical": None,
         "columns": COLUMNS, "keys": ["ID"]},
        {"stream": "2_T", "table": None, "kind": "target", "logical": "ITEMS_OUT", "tool_id": "3",
         "columns": COLUMNS, "keys": ["ID"]},
    ],
    "row_relation": "filter",
    "ordering": {"keys": ["ID"], "alteryx_deterministic": True},
    "tolerances": {},
}
CONTRACT["output"] = CONTRACT["outputs"][0]

DAG = {
    "workflow": WF, "segment": SEG,
    "nodes": [
        {"tool_id": "1", "type": "input", "config": {}},
        {"tool_id": "2", "type": "filter", "config": {}},
        {"tool_id": "3", "type": "output", "config": {}},
    ],
    "edges": [
        {"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input"},
        {"src": "2", "src_anchor": "True", "dst": "3", "dst_anchor": "Input"},
    ],
    "inbound": [], "outbound": [],
}

PROC = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data
  t1_input AS (SELECT ID, NOTE FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ITEMS')),
  -- tool 2: Filter, True branch
  t2_filter AS (SELECT ID, NOTE FROM t1_input WHERE NOTE = 'keep')
  SELECT ID, NOTE FROM t2_filter;
  CREATE OR REPLACE TABLE IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.ITEMS_OUT') AS
  SELECT ID, NOTE FROM MIG_WORK.WF0009_SEG_01_OUT;
  RETURN 'OK';
END;
$$;"""

PROC_WRONG_FILTER = PROC.replace("WHERE NOTE = 'keep'", "WHERE NOTE = 'drop'")

PROC_SYNTAX_ERROR = PROC.replace("SELECT ID, NOTE FROM t2_filter", "SELEC ID, NOTE FROM t2_filter")

PROC_RANDOM = PROC.replace(
    "SELECT ID, NOTE FROM t1_input WHERE NOTE = 'keep'",
    "SELECT ID, NOTE || '_' || CAST(UNIFORM(1, 1000000, RANDOM()) AS VARCHAR) AS NOTE "
    "FROM t1_input WHERE NOTE = 'keep'")

MAPPINGS = {
    "sources": {"sales/items.yxdb": {"snowflake": "SALES.RAW.ITEMS", "logical": "ITEMS",
                                     "tool_ids": ["1"], "confirmed_by": "test"}},
    "outputs": {"out/items_out.yxdb": {"snowflake": "ANALYTICS.CURATED.ITEMS_OUT",
                                       "logical": "ITEMS_OUT", "mode": "overwrite", "keys": ["ID"],
                                       "tool_ids": ["3"]}},
}


def build(tmp_path, *, proc=PROC, contract=None, golden_sets=("normal",)):
    repo = Repo(tmp_path)
    write_json(repo.wf(WF, "manifest.json"), {"id": WF, "golden_sets": list(golden_sets)})
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), MAPPINGS)

    typed_csv.write_table(repo.wf(WF, "golden", "inputs", "normal", "1.csv"),
                          {"fields": F, "rows": [[1, "keep"], [2, "drop"]]})
    typed_csv.write_table(repo.wf(WF, "golden", "intermediates", SEG, "normal", "2_T.csv"),
                          {"fields": F, "rows": [[1, "keep"]]})
    typed_csv.write_table(repo.wf(WF, "golden", "outputs", "normal", "3.csv"),
                          {"fields": F, "rows": [[1, "keep"]]})

    write_json(repo.seg(WF, SEG, "dag.json"), DAG)
    write_json(repo.seg(WF, SEG, "contract.json"), contract if contract is not None else CONTRACT)
    path = repo.seg(WF, SEG, "proc.sql")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(proc, encoding="utf-8", newline="\n")
    return repo


# --- the correct procedure: PASS, idempotent, both files written --------------------------------

def test_correct_procedure_passes_and_is_idempotent(tmp_path):
    repo = build(tmp_path)
    report = vs.validate_segment(repo, WF, SEG)

    assert report["sets"] == {"normal": "PASS"}
    assert report["idempotent"] is True
    assert read_json(repo.seg(WF, SEG, "validation.json"))["sets"] == {"normal": "PASS"}
    assert read_json(repo.seg(WF, SEG, "validation.normal.json"))["verdict"] == "PASS"


# --- a wrong filter: FAIL with a cluster carrying stream -----------------------------------------

def test_wrong_filter_fails_with_a_cluster_carrying_stream(tmp_path):
    repo = build(tmp_path, proc=PROC_WRONG_FILTER)
    report = vs.validate_segment(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    assert report["diff_clusters"]
    assert all(cluster["stream"] == "2_T" for cluster in report["diff_clusters"])


# --- a SQL syntax error: a domain FAIL, still written, still reported ----------------------------

def test_sql_syntax_error_is_a_domain_fail(tmp_path):
    repo = build(tmp_path, proc=PROC_SYNTAX_ERROR)
    report = vs.validate_segment(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    assert report["error"]
    assert report["diff_clusters"] == []
    assert read_json(repo.seg(WF, SEG, "validation.json"))["error"]


# --- non-determinism -------------------------------------------------------------------------

def test_random_column_makes_the_segment_not_idempotent(tmp_path):
    repo = build(tmp_path, proc=PROC_RANDOM)
    report = vs.validate_segment(repo, WF, SEG)

    assert report["idempotent"] is False


# --- --proc override --------------------------------------------------------------------------

def test_proc_argument_overrides_the_segment_file(tmp_path):
    repo = build(tmp_path, proc=PROC_WRONG_FILTER)  # the segment's own file would FAIL
    override = tmp_path / "override_proc.sql"
    override.write_text(PROC, encoding="utf-8", newline="\n")

    report = vs.validate_segment(repo, WF, SEG, proc_path=override)

    assert report["sets"] == {"normal": "PASS"}


# --- usage errors: nothing written ----------------------------------------------------------------

def test_unknown_segment_raises_and_writes_nothing(tmp_path):
    repo = build(tmp_path)
    with pytest.raises(FileNotFoundError):
        vs.validate_segment(repo, WF, "seg_99")
    assert not repo.seg(WF, "seg_99", "validation.json").exists()


def test_missing_golden_file_raises_and_writes_nothing(tmp_path):
    repo = build(tmp_path)
    repo.wf(WF, "golden", "outputs", "normal", "3.csv").unlink()
    with pytest.raises(FileNotFoundError):
        vs.validate_segment(repo, WF, SEG)
    assert not repo.seg(WF, SEG, "validation.json").exists()


def test_no_golden_sets_available_raises_value_error(tmp_path):
    repo = build(tmp_path, golden_sets=())
    with pytest.raises(ValueError):
        vs.validate_segment(repo, WF, SEG)
    assert not repo.seg(WF, SEG, "validation.json").exists()


# --- two-segment upstream-intermediate case -----------------------------------------------------

SEG2 = "seg_02"
CONTRACT2 = {
    "segment": SEG2,
    "inputs": [{"from": "seg_01", "stream": "2_T", "table": "MIG_WORK.WF0009_SEG_01_OUT",
               "columns": COLUMNS, "keys": ["ID"]}],
    "outputs": [{"stream": "4_Output", "table": "MIG_WORK.WF0009_SEG_02_OUT", "kind": "work",
                "logical": None, "columns": COLUMNS, "keys": ["ID"]}],
    "row_relation": "1:1",
    "ordering": {"keys": ["ID"], "alteryx_deterministic": True},
    "tolerances": {},
}
CONTRACT2["output"] = CONTRACT2["outputs"][0]

PROC2 = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_02(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_02_OUT AS
  SELECT ID, NOTE FROM MIG_WORK.WF0009_SEG_01_OUT;
  RETURN 'OK';
END;
$$;"""


def test_upstream_intermediate_feeds_the_next_segment_without_running_its_procedure(tmp_path):
    repo = build(tmp_path)   # seg_01's own proc.sql exists, but seg_02 must never need it
    typed_csv.write_table(repo.wf(WF, "golden", "intermediates", SEG2, "normal", "4_Output.csv"),
                          {"fields": F, "rows": [[1, "keep"]]})
    write_json(repo.seg(WF, SEG2, "contract.json"), CONTRACT2)
    repo.seg(WF, SEG2, "proc.sql").write_text(PROC2, encoding="utf-8", newline="\n")

    # Prove seg_01's own procedure is never consulted: replace it with one that would raise if run.
    repo.seg(WF, SEG, "proc.sql").write_text(PROC_SYNTAX_ERROR, encoding="utf-8", newline="\n")

    report = vs.validate_segment(repo, WF, SEG2)

    assert report["sets"] == {"normal": "PASS"}


# --- CLI exit codes (implementer-rules.md "CLI EXIT CODES") -------------------------------------

def run_cli(tmp_path, seg=SEG, *extra):
    return subprocess.run(
        [sys.executable, str(Path(vs.__file__)), WF, seg, *extra, "--root", str(tmp_path)],
        capture_output=True, text=True)


def test_cli_exits_zero_on_pass(tmp_path):
    build(tmp_path)
    done = run_cli(tmp_path)
    assert done.returncode == 0 and "PASS" in done.stdout


def test_cli_exits_one_on_fail(tmp_path):
    build(tmp_path, proc=PROC_WRONG_FILTER)
    done = run_cli(tmp_path)
    assert done.returncode == 1


def test_cli_exits_two_on_unknown_segment(tmp_path):
    build(tmp_path)
    done = run_cli(tmp_path, "seg_99")
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert not Repo(tmp_path).seg(WF, "seg_99", "validation.json").exists()


def test_cli_exits_two_and_writes_nothing_when_a_golden_file_is_missing(tmp_path):
    repo = build(tmp_path)
    repo.wf(WF, "golden", "outputs", "normal", "3.csv").unlink()
    done = run_cli(tmp_path)
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert not repo.seg(WF, SEG, "validation.json").exists()


def test_cli_proc_flag_overrides_the_segment_file(tmp_path):
    build(tmp_path, proc=PROC_WRONG_FILTER)
    override = tmp_path / "override_proc.sql"
    override.write_text(PROC, encoding="utf-8", newline="\n")
    done = run_cli(tmp_path, SEG, "--proc", str(override))
    assert done.returncode == 0 and "PASS" in done.stdout


def test_cli_set_flag_overrides_the_manifest_default(tmp_path):
    build(tmp_path, golden_sets=("nonexistent",))  # "normal"'s golden files exist on disk anyway
    done = run_cli(tmp_path, SEG, "--set", "normal")
    assert done.returncode == 0
    assert "{'normal': 'PASS'}" in done.stdout


def test_cli_without_set_flag_uses_the_manifest_default(tmp_path):
    build(tmp_path, golden_sets=("nonexistent",))
    done = run_cli(tmp_path)
    assert done.returncode == 2 and "Traceback" not in done.stderr


def test_main_returns_two_on_an_unexpected_exception(tmp_path, monkeypatch):
    build(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")
    monkeypatch.setattr(vs, "validate_segment", boom)

    rc = vs.main([WF, SEG, "--root", str(tmp_path)])
    assert rc == 2
