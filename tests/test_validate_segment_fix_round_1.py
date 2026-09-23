"""Task 14 fix round 1: the reviewer's findings K1, K2, C3, C4 and I5.

A self-contained wf_0009-shaped fixture, independent of tests/test_validate_segment.py's own
`build()` (never edited here) -- this file needs an extra AMOUNT column (for an approvable
ROUNDING diff and for a non-deterministic-but-tolerant repro), multiple golden sets, and
mappings/global.yaml / manifest.accepted_diffs, none of which the original fixture needs.

    K1 -- top-level verdict is the WORST across every processed golden set, not "the first
          non-passing set"; the body is the first set (in processing order) with that worst
          verdict; needs_human is true if ANY set's report says so.
    K2 -- a failed idempotency check FAILS validation (it no longer just sets a bit nobody reads).
    C3 -- an empty/missing contract.outputs[] is a usage error (ValueError), not a vacuous PASS.
    C4 -- a declared output table the procedure never created is a domain FAIL naming the table
          (not compare.py's bare ValueError leaking out as a usage error), and any invocation
          -- successful or not -- first deletes every validation*.json already on disk, so a
          usage error or crash never leaves a stale report behind (also I5: a golden set no
          longer requested loses its validation.<set>.json too).
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
     {"name": "NOTE", "type": "V_String", "size": 20, "scale": None},
     {"name": "AMOUNT", "type": "Double", "size": 8, "scale": None}]

COLUMNS = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": True},
           {"name": "NOTE", "type": "VARCHAR", "nullable": True},
           {"name": "AMOUNT", "type": "FLOAT", "nullable": True}]

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

WORK_TABLE = "MIG_WORK.WF0009_SEG_01_OUT"
TARGET_TABLE = "MIGDB.MIG_WORK.ITEMS_OUT"

PROC = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';
  LET ITEMS_OUT_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.ITEMS_OUT';
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data
  t1_input AS (SELECT ID, NOTE, AMOUNT FROM IDENTIFIER(:ITEMS_SRC)),
  -- tool 2: Filter, True branch
  t2_filter AS (SELECT ID, NOTE, AMOUNT FROM t1_input WHERE NOTE = 'keep')
  SELECT ID, NOTE, AMOUNT FROM t2_filter;
  CREATE OR REPLACE TABLE IDENTIFIER(:ITEMS_OUT_TGT) AS
  SELECT ID, NOTE, AMOUNT FROM MIG_WORK.WF0009_SEG_01_OUT;
  RETURN 'OK';
END;
$$;"""

# C4(a): the procedure creates the work table but never creates the declared target table.
PROC_SKIP_TARGET = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS
  WITH
  t1_input AS (SELECT ID, NOTE, AMOUNT FROM IDENTIFIER(:ITEMS_SRC)),
  t2_filter AS (SELECT ID, NOTE, AMOUNT FROM t1_input WHERE NOTE = 'keep')
  SELECT ID, NOTE, AMOUNT FROM t2_filter;
  RETURN 'OK';
END;
$$;"""

# K2: the AMOUNT column is a fresh random draw every run -- non-deterministic -- but the contract
# gives AMOUNT a tolerance wide enough that compare() alone, against a single fixed golden value,
# passes regardless of which value in [1, 1000000] came out.
PROC_RANDOM_AMOUNT = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';
  LET ITEMS_OUT_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.ITEMS_OUT';
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS
  WITH
  t1_input AS (SELECT ID, NOTE, CAST(UNIFORM(1, 1000000, RANDOM()) AS DOUBLE) AS AMOUNT
               FROM IDENTIFIER(:ITEMS_SRC)),
  t2_filter AS (SELECT ID, NOTE, AMOUNT FROM t1_input WHERE NOTE = 'keep')
  SELECT ID, NOTE, AMOUNT FROM t2_filter;
  CREATE OR REPLACE TABLE IDENTIFIER(:ITEMS_OUT_TGT) AS
  SELECT ID, NOTE, AMOUNT FROM MIG_WORK.WF0009_SEG_01_OUT;
  RETURN 'OK';
END;
$$;"""

TOLERANT_CONTRACT = {**CONTRACT, "tolerances": {"AMOUNT": {"float_abs": 2000000}}}
TOLERANT_CONTRACT["output"] = TOLERANT_CONTRACT["outputs"][0]

MAPPINGS = {
    "sources": {"sales/items.yxdb": {"snowflake": "SALES.RAW.ITEMS", "logical": "ITEMS",
                                     "tool_ids": ["1"], "confirmed_by": "test"}},
    "outputs": {"out/items_out.yxdb": {"snowflake": "ANALYTICS.CURATED.ITEMS_OUT",
                                       "logical": "ITEMS_OUT", "mode": "overwrite", "keys": ["ID"],
                                       "tool_ids": ["3"]}},
}


def build(tmp_path, *, proc=PROC, contract=None, golden_sets=("normal",), accepted_diffs=None,
         accepted_diff_classes=None):
    repo = Repo(tmp_path)
    manifest = {"id": WF, "golden_sets": list(golden_sets)}
    if accepted_diffs is not None:
        manifest["accepted_diffs"] = accepted_diffs
    write_json(repo.wf(WF, "manifest.json"), manifest)
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), MAPPINGS)
    if accepted_diff_classes is not None:
        write_yaml(repo.global_mappings, {"accepted_diff_classes": list(accepted_diff_classes)})

    write_json(repo.seg(WF, SEG, "dag.json"), DAG)
    write_json(repo.seg(WF, SEG, "contract.json"), contract if contract is not None else CONTRACT)
    path = repo.seg(WF, SEG, "proc.sql")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(proc, encoding="utf-8", newline="\n")
    return repo


def write_golden_set(repo, golden_set, *, items_rows, work_rows, target_rows=None):
    typed_csv.write_table(repo.wf(WF, "golden", "inputs", golden_set, "1.csv"),
                          {"fields": F, "rows": items_rows})
    typed_csv.write_table(repo.wf(WF, "golden", "intermediates", SEG, golden_set, "2_T.csv"),
                          {"fields": F, "rows": work_rows})
    typed_csv.write_table(repo.wf(WF, "golden", "outputs", golden_set, "3.csv"),
                          {"fields": F, "rows": target_rows if target_rows is not None else work_rows})


def run_cli(tmp_path, seg=SEG, *extra):
    return subprocess.run(
        [sys.executable, str(Path(vs.__file__)), WF, seg, *extra, "--root", str(tmp_path)],
        capture_output=True, text=True)


# ============================================================================================
# K1 -- top-level verdict is the worst across every processed set, not "first non-passing"
# ============================================================================================

def test_k1_aggregate_sets_worst_wins_body_and_needs_human_is_any_of():
    """Unit-tests the aggregation directly (brief: "if awkward to construct with real data,
    unit-test the aggregation helper directly") -- in particular the case real compare() output
    can't easily produce: an earlier set has needs_human True, but the set with the worst verdict
    (chosen as the body) itself says needs_human False. The aggregate must still be True."""
    reports = {
        "normal": {"verdict": "PASS", "needs_human": False, "marker": "normal-body"},
        "period_end": {"verdict": "PASS_WITH_ACCEPTED_DIFF", "needs_human": True,
                       "marker": "period_end-body"},
        "edge": {"verdict": "FAIL", "needs_human": False, "marker": "edge-body"},
    }
    result = vs._aggregate_sets(["normal", "period_end", "edge"], reports)

    assert result["verdict"] == "FAIL"
    assert result["marker"] == "edge-body"          # body = the first set with the worst verdict
    assert result["needs_human"] is True            # any-of, even though the FAIL set said False
    assert result["sets"] == {"normal": "PASS", "period_end": "PASS_WITH_ACCEPTED_DIFF",
                              "edge": "FAIL"}


def test_k1_aggregate_sets_picks_the_first_set_with_the_worst_verdict():
    """Two FAIL sets: the body must be the FIRST one in processing order, not the last."""
    reports = {
        "normal": {"verdict": "FAIL", "needs_human": False, "marker": "normal-body"},
        "edge": {"verdict": "FAIL", "needs_human": False, "marker": "edge-body"},
    }
    result = vs._aggregate_sets(["normal", "edge"], reports)
    assert result["marker"] == "normal-body"


def test_k1_end_to_end_worst_of_all_sets_beats_first_non_passing(tmp_path):
    """A real run: "normal" is PASS_WITH_ACCEPTED_DIFF (an approved ROUNDING diff), "edge" is a
    genuine unapproved FAIL. The pre-fix rule ("first non-passing set, else the last") would
    already stop at "normal" and report PASS_WITH_ACCEPTED_DIFF at the top level -- exit 0. The
    fix must report the worst across both: FAIL, with "edge"'s own report as the body, exit 1.
    """
    approvals = [{"segment": SEG, "class": "ROUNDING", "columns": ["AMOUNT"], "approver": "test",
                 "date": "2026-09-19"}]
    repo = build(tmp_path, golden_sets=("normal", "edge"), accepted_diffs=approvals,
                accepted_diff_classes=["ROUNDING"])
    # "normal": actual AMOUNT 250.005 vs golden 250.01 -- diff 0.005 <= rounding.abs 0.01 ->
    # ROUNDING, approved for seg_01/AMOUNT -> PASS_WITH_ACCEPTED_DIFF.
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 250.005], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 250.01]])
    # "edge": actual AMOUNT 100.0 vs golden 175.0 -- diff 75, far outside rounding tolerance ->
    # LOGIC, not an accepted class -> FAIL.
    write_golden_set(repo, "edge", items_rows=[[1, "keep", 100.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 175.0]])

    report = vs.validate_segment(repo, WF, SEG)

    assert report["sets"] == {"normal": "PASS_WITH_ACCEPTED_DIFF", "edge": "FAIL"}
    assert report["verdict"] == "FAIL"
    assert report["golden_set"] == "edge"
    assert any(cluster["class"] == "LOGIC" for cluster in report["diff_clusters"])
    assert read_json(repo.seg(WF, SEG, "validation.json"))["verdict"] == "FAIL"

    done = run_cli(tmp_path)
    assert done.returncode == 1


# ============================================================================================
# K2 -- a failed idempotency check FAILS validation
# ============================================================================================

def test_k2_nondeterministic_column_fails_validation_even_though_compare_alone_would_pass(tmp_path):
    """The reviewer's repro: a UNIFORM(..., RANDOM()) column whose contract tolerance is wide
    enough that compare() alone -- comparing one run's actual value against the fixed golden
    value -- passes regardless of which random value came out. Idempotency must catch what
    compare() structurally cannot: the two runs disagree with each other."""
    repo = build(tmp_path, proc=PROC_RANDOM_AMOUNT, contract=TOLERANT_CONTRACT)
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])

    report = vs.validate_segment(repo, WF, SEG)

    assert report["idempotent"] is False
    assert report["idempotency_diff"], "expected the diverging table name(s) to be listed"
    assert any(WORK_TABLE in name for name in report["idempotency_diff"])
    assert report["verdict"] == "FAIL"

    done = run_cli(tmp_path)
    assert done.returncode == 1


def test_k2_deterministic_procedure_has_empty_idempotency_diff(tmp_path):
    repo = build(tmp_path, proc=PROC, contract=CONTRACT)
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])

    report = vs.validate_segment(repo, WF, SEG)

    assert report["idempotent"] is True
    assert report["idempotency_diff"] == []
    assert report["verdict"] == "PASS"


# ============================================================================================
# C3 -- empty/missing contract.outputs[] is a usage error
# ============================================================================================

def test_c3_empty_outputs_list_is_a_value_error(tmp_path):
    # Golden data is present and would let the run otherwise succeed -- proving the ValueError is
    # about the empty outputs[], not an incidental missing-fixture error.
    contract = dict(CONTRACT, outputs=[])
    repo = build(tmp_path, contract=contract)
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])
    with pytest.raises(ValueError):
        vs.validate_segment(repo, WF, SEG)
    assert not repo.seg(WF, SEG, "validation.json").exists()


def test_c3_missing_outputs_key_is_a_value_error(tmp_path):
    contract = {k: v for k, v in CONTRACT.items() if k != "outputs"}
    repo = build(tmp_path, contract=contract)
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])
    with pytest.raises(ValueError):
        vs.validate_segment(repo, WF, SEG)
    assert not repo.seg(WF, SEG, "validation.json").exists()


def test_c3_cli_exits_two_and_writes_nothing(tmp_path):
    contract = dict(CONTRACT, outputs=[])
    repo = build(tmp_path, contract=contract)
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])
    done = run_cli(tmp_path)
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert not repo.seg(WF, SEG, "validation.json").exists()


# ============================================================================================
# C4(a) -- a declared output table the procedure never created is a domain FAIL naming the table
# ============================================================================================

def test_c4a_missing_output_table_is_a_domain_fail_naming_the_table(tmp_path):
    repo = build(tmp_path, proc=PROC_SKIP_TARGET)
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])

    report = vs.validate_segment(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    assert "error" in report and "ITEMS_OUT" in report["error"]
    on_disk = read_json(repo.seg(WF, SEG, "validation.json"))
    assert on_disk["verdict"] == "FAIL" and "ITEMS_OUT" in on_disk["error"]


def test_c4a_cli_exits_one_and_writes_a_report(tmp_path):
    repo = build(tmp_path, proc=PROC_SKIP_TARGET)
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])

    done = run_cli(tmp_path)

    assert done.returncode == 1
    assert repo.seg(WF, SEG, "validation.json").exists()


# ============================================================================================
# C4(b) / I5 -- every invocation clears stale validation*.json first
# ============================================================================================

def test_c4b_a_later_usage_error_leaves_no_report_behind(tmp_path):
    repo = build(tmp_path)
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])

    first = vs.validate_segment(repo, WF, SEG)
    assert first["verdict"] == "PASS"
    assert repo.seg(WF, SEG, "validation.json").exists()
    assert repo.seg(WF, SEG, "validation.normal.json").exists()

    repo.wf(WF, "golden", "outputs", "normal", "3.csv").unlink()  # break a prerequisite
    with pytest.raises(FileNotFoundError):
        vs.validate_segment(repo, WF, SEG)

    assert not repo.seg(WF, SEG, "validation.json").exists()
    assert not repo.seg(WF, SEG, "validation.normal.json").exists()
    assert list(repo.seg(WF, SEG).glob("validation*.json")) == []


def test_i5_a_golden_set_no_longer_requested_loses_its_report(tmp_path):
    repo = build(tmp_path, golden_sets=("normal", "edge"))
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])
    write_golden_set(repo, "edge", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])

    vs.validate_segment(repo, WF, SEG, ["normal", "edge"])
    assert repo.seg(WF, SEG, "validation.edge.json").exists()
    assert repo.seg(WF, SEG, "validation.normal.json").exists()

    vs.validate_segment(repo, WF, SEG, ["normal"])
    assert not repo.seg(WF, SEG, "validation.edge.json").exists()
    assert repo.seg(WF, SEG, "validation.normal.json").exists()
    assert repo.seg(WF, SEG, "validation.json").exists()


def test_unknown_segment_directory_absent_is_still_a_clean_exit_two(tmp_path):
    build(tmp_path)
    done = run_cli(tmp_path, "seg_absent")
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert not Repo(tmp_path).seg(WF, "seg_absent", "validation.json").exists()


# ============================================================================================
# Minor -- the merged `checks` key shape: "<stream>:<kind>", value exactly what compare produced
# ============================================================================================

def test_checks_key_shape_is_stream_colon_kind(tmp_path):
    repo = build(tmp_path)
    write_golden_set(repo, "normal", items_rows=[[1, "keep", 1.0], [2, "drop", 1.0]],
                     work_rows=[[1, "keep", 1.0]])

    report = vs.validate_segment(repo, WF, SEG)

    assert set(report["checks"]) == {"2_T:work", "2_T:target"}
    for key in ("2_T:work", "2_T:target"):
        assert report["checks"][key]["schema"] == "PASS"
        assert report["checks"][key]["counts"] == {"expected": 1, "actual": 1, "verdict": "PASS"}
