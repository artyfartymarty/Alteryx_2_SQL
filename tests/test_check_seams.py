"""`check_seams.py` (Task W2): every inter-segment stream agrees between its producer's contract
`outputs[]` entry and its consumer's contract `inputs[]` entry -- same columns in order, same type
FAMILY (`lib.types_map.type_family`, the one `compare.py` judges schemas with), same nullability,
same keys, the same table, exactly one producer, no table produced twice. Checked by code, never by
a model: the analyzer stage runs it in its verify callback and parks `seam-mismatch: <producer>->
<consumer> <stream>` after one analyzer retry.

The hand-built workflows below are written directly as contracts plus `segments/order.json`, so
each test states exactly the seam it is about. Nothing here has run on Snowflake or Alteryx.
"""
from __future__ import annotations

import copy
import json

import pytest

import check_seams as cs
from lib.io import read_json, write_json
from lib.paths import Repo
from tests.helpers import prepare_workflow

WF = "wf_0100"
T01 = "MIG_WORK.WF0100_SEG_01_OUT"
T02 = "MIG_WORK.WF0100_SEG_02_OUT"


def col(name: str, type_: str = "VARCHAR(20)", nullable: bool = True) -> dict:
    return {"name": name, "type": type_, "nullable": nullable}


COLUMNS = [col("ACCT"), col("AMOUNT", "FLOAT"), col("PERIOD", "DATE", False)]


def work(stream: str, table: str, columns: list[dict], keys=("ACCT",)) -> dict:
    return {"stream": stream, "kind": "work", "table": table, "logical": None,
            "columns": copy.deepcopy(columns), "keys": list(keys)}


def stream_input(stream: str, frm: str, table: str, columns: list[dict], keys=("ACCT",)) -> dict:
    return {"stream": stream, "from": frm, "table": table, "columns": copy.deepcopy(columns), "keys": list(keys)}


def contract(seg: str, inputs=(), outputs=()) -> dict:
    return {"segment": seg, "target": "sql", "inputs": list(inputs), "outputs": list(outputs)}


def source_input(logical: str = "ORDERS") -> dict:
    return {"logical": logical, "tool_id": "1", "columns": copy.deepcopy(COLUMNS), "keys": []}


def target_output(stream: str) -> dict:
    return {"stream": stream, "kind": "target", "logical": "ORDERS_OUT", "tool_id": "9",
            "columns": copy.deepcopy(COLUMNS), "keys": ["ACCT"]}


def write_workflow(repo: Repo, order: list[list[str]], contracts: dict[str, dict]) -> None:
    write_json(repo.wf(WF, "segments", "order.json"), order)
    for seg, body in contracts.items():
        write_json(repo.seg(WF, seg, "contract.json"), body)


def two_segments(consumed: dict | None = None) -> dict[str, dict]:
    """seg_01 writes work stream 2_T to T01; seg_02 reads it (`consumed` replaces that input)."""
    return {
        "seg_01": contract("seg_01", [source_input()], [work("2_T", T01, COLUMNS)]),
        "seg_02": contract("seg_02", [consumed or stream_input("2_T", "seg_01", T01, COLUMNS)],
                           [target_output("5_Output")]),
    }


def three_segments() -> dict[str, dict]:
    """seg_01 -> 2_T -> seg_02 -> 3_J -> seg_03, one segment per wave."""
    return {
        "seg_01": contract("seg_01", [source_input()], [work("2_T", T01, COLUMNS)]),
        "seg_02": contract("seg_02", [stream_input("2_T", "seg_01", T01, COLUMNS)], [work("3_J", T02, COLUMNS)]),
        "seg_03": contract("seg_03", [stream_input("3_J", "seg_02", T02, COLUMNS)], [target_output("5_Output")]),
    }


@pytest.fixture
def repo(tmp_path) -> Repo:
    return Repo(tmp_path)


def _run(repo: Repo, *extra: str) -> int:
    return cs.main([WF, *extra, "--root", str(repo.root)])


# --- the brief's tests --------------------------------------------------------------------------

def test_matching_seams_are_ok(repo, capsys):
    write_workflow(repo, [["seg_01"], ["seg_02"], ["seg_03"]], three_segments())

    result = cs.check_seams(repo, WF)

    assert result["ok"] is True
    assert result["duplicates"] == []
    assert [(s["producer"], s["consumer"], s["stream"], s["table"], s["status"], s["problems"])
            for s in result["seams"]] == [
        ("seg_01", "seg_02", "2_T", T01, "ok", []),
        ("seg_02", "seg_03", "3_J", T02, "ok", []),
    ]
    assert read_json(repo.wf(WF, "segments", "seams.json")) == result
    assert _run(repo) == 0
    assert capsys.readouterr().err == ""


def test_a_type_family_change_across_a_seam_is_a_mismatch(repo, capsys):
    consumed = stream_input("2_T", "seg_01", T01, [col("ACCT"), col("AMOUNT", "NUMBER(38,0)"),
                                                    col("PERIOD", "DATE", False)])
    write_workflow(repo, [["seg_01"], ["seg_02"]], two_segments(consumed))

    result = cs.check_seams(repo, WF)

    assert result["ok"] is False
    [seam] = result["seams"]
    assert (seam["producer"], seam["consumer"], seam["stream"], seam["status"]) == ("seg_01", "seg_02", "2_T", "mismatch")
    [problem] = seam["problems"]
    assert "AMOUNT" in problem and "(float)" in problem and "(number)" in problem, problem

    assert _run(repo) == 1
    assert capsys.readouterr().err.splitlines()[0] == "seam-mismatch: seg_01->seg_02 2_T"


def test_a_width_or_precision_change_is_not_a_mismatch(repo):
    """Controller note 2: the FAMILY is compared, exactly as `compare.py` does, so a harmless
    precision or length change across a seam is not a mismatch."""
    produced = [col("ACCT", "VARCHAR(20)"), col("AMOUNT", "NUMBER(19,2)"), col("PERIOD", "DATE", False)]
    consumed = [col("acct", "VARCHAR"), col("AMOUNT", "DECIMAL(38,0)"), col("PERIOD", "DATE", False)]
    contracts = {
        "seg_01": contract("seg_01", [source_input()], [work("2_T", T01, produced)]),
        "seg_02": contract("seg_02", [stream_input("2_T", "seg_01", T01, consumed)]),
    }
    write_workflow(repo, [["seg_01"], ["seg_02"]], contracts)
    assert cs.check_seams(repo, WF)["ok"] is True


def _changed(kind: str) -> dict:
    """The consumer's input for `two_segments`, wrong in exactly one way."""
    entry = stream_input("2_T", "seg_01", T01, COLUMNS)
    if kind == "nullability":
        entry["columns"][2]["nullable"] = True
    elif kind == "keys":
        entry["keys"] = ["ACCT", "PERIOD"]
    elif kind == "order":
        entry["columns"] = [entry["columns"][1], entry["columns"][0], entry["columns"][2]]
    elif kind == "table":
        entry["table"] = f"{T01}_2_T"
    return entry


@pytest.mark.parametrize("kind, named", [
    ("nullability", ["PERIOD", "nullable"]),
    ("keys", ["keys", "PERIOD"]),
    ("order", ["columns", "AMOUNT", "ACCT"]),
    ("table", [f"{T01}_2_T", T01]),
])
def test_nullability_keys_order_and_table_mismatches_are_each_named(repo, kind, named):
    write_workflow(repo, [["seg_01"], ["seg_02"]], two_segments(_changed(kind)))

    [seam] = cs.check_seams(repo, WF)["seams"]

    assert (seam["producer"], seam["consumer"], seam["status"]) == ("seg_01", "seg_02", "mismatch")
    assert len(seam["problems"]) == 1, seam["problems"]
    for token in named:
        assert token in seam["problems"][0], (token, seam["problems"])


def test_a_consumer_without_a_producer_and_a_stream_produced_twice_are_refused(repo, capsys):
    # (a) seg_02 reads a stream no segment writes at all
    orphan = stream_input("9_X", "seg_01", "MIG_WORK.WF0100_SEG_01_OUT_9_X", COLUMNS)
    write_workflow(repo, [["seg_01"], ["seg_02"]], two_segments(orphan))
    result = cs.check_seams(repo, WF)
    assert result["ok"] is False
    [seam] = result["seams"]
    assert seam["status"] == "mismatch"
    assert any("no segment produces" in problem for problem in seam["problems"]), seam["problems"]
    assert _run(repo) == 1
    assert capsys.readouterr().err.splitlines()[0] == "seam-mismatch: seg_01->seg_02 9_X"

    # (b) seg_01 and seg_02 both write T01, which seg_03 reads
    contracts = {
        "seg_01": contract("seg_01", [source_input()], [work("2_T", T01, COLUMNS)]),
        "seg_02": contract("seg_02", [source_input("OTHER")], [work("2_T", T01, COLUMNS)]),
        "seg_03": contract("seg_03", [stream_input("2_T", "seg_01", T01, COLUMNS)]),
    }
    write_workflow(repo, [["seg_01", "seg_02"], ["seg_03"]], contracts)
    result = cs.check_seams(repo, WF)
    assert result["ok"] is False
    assert result["duplicates"] == [{"table": T01, "producers": ["seg_01", "seg_02"]}]
    [seam] = result["seams"]
    assert seam["status"] == "mismatch"
    assert any("seg_01" in p and "seg_02" in p for p in seam["problems"]), seam["problems"]
    assert _run(repo) == 1


def test_segments_restricts_the_check_to_those_consumers(repo, capsys):
    contracts = three_segments()
    contracts["seg_03"]["inputs"][0]["columns"][1]["type"] = "BOOLEAN"   # seg_03's input is wrong
    write_workflow(repo, [["seg_01"], ["seg_02"], ["seg_03"]], contracts)

    assert _run(repo, "--segments", "seg_02") == 0
    only = read_json(repo.wf(WF, "segments", "seams.json"))
    assert [s["consumer"] for s in only["seams"]] == ["seg_02"]
    assert only["ok"] is True

    assert _run(repo, "--segments", "seg_02,seg_03") == 1
    assert capsys.readouterr().err.splitlines()[0] == "seam-mismatch: seg_02->seg_03 3_J"
    assert cs.check_seams(repo, WF, ["seg_03"])["ok"] is False


# --- further behaviour the brief states in prose ----------------------------------------------

def test_every_mismatch_is_reported_not_just_the_first(repo, capsys):
    """Controller note 2: every problem of every seam, not the first one found."""
    contracts = three_segments()
    contracts["seg_02"]["inputs"][0]["columns"][0]["type"] = "NUMBER(38,0)"   # ACCT: string -> number
    contracts["seg_02"]["inputs"][0]["columns"][2]["nullable"] = True          # PERIOD nullability
    contracts["seg_03"]["inputs"][0]["keys"] = []                              # keys dropped
    write_workflow(repo, [["seg_01"], ["seg_02"], ["seg_03"]], contracts)

    result = cs.check_seams(repo, WF)

    assert [s["status"] for s in result["seams"]] == ["mismatch", "mismatch"]
    assert len(result["seams"][0]["problems"]) == 2, result["seams"][0]["problems"]
    assert len(result["seams"][1]["problems"]) == 1, result["seams"][1]["problems"]
    assert _run(repo) == 1
    err = capsys.readouterr().err
    assert err.splitlines()[0] == "seam-mismatch: seg_01->seg_02 2_T"
    assert "seg_02->seg_03 3_J" in err
    for problem in result["seams"][0]["problems"] + result["seams"][1]["problems"]:
        assert problem in err


def test_a_stream_the_dag_carries_but_no_contract_declares_is_a_mismatch(repo):
    """"Every inter-segment stream": the segment sub-DAGs (contract C7) say which streams cross a
    cut; a consumer whose contract forgets one has not agreed with its producer at all."""
    contracts = two_segments()
    contracts["seg_02"]["inputs"] = [source_input("OTHER")]      # the 2_T input is simply missing
    write_workflow(repo, [["seg_01"], ["seg_02"]], contracts)
    write_json(repo.seg(WF, "seg_02", "dag.json"), {
        "segment": "seg_02", "nodes": [{"tool_id": "3", "type": "select"}], "edges": [],
        "inbound": [{"src": "2", "src_anchor": "T", "dst": "3", "dst_anchor": "Input", "from_segment": "seg_01"}],
        "outbound": [],
    })

    result = cs.check_seams(repo, WF)

    assert result["ok"] is False
    [seam] = result["seams"]
    assert (seam["producer"], seam["consumer"], seam["stream"], seam["status"]) == ("seg_01", "seg_02", "2_T", "mismatch")
    assert "declares no input" in seam["problems"][0], seam["problems"]


def test_a_producer_that_runs_after_its_consumer_is_a_mismatch(repo):
    contracts = two_segments()
    write_workflow(repo, [["seg_02"], ["seg_01"]], contracts)      # the consumer's wave comes first
    [seam] = cs.check_seams(repo, WF)["seams"]
    assert seam["status"] == "mismatch"
    assert any("wave" in problem for problem in seam["problems"]), seam["problems"]


def test_cli_usage_errors_write_nothing(repo, capsys):
    # a workflow that has not been prepared: no segments/order.json
    with pytest.raises(SystemExit) as exc:
        _run(repo)
    assert exc.value.code == 2
    assert "order.json" in capsys.readouterr().err
    assert not repo.wf(WF, "segments", "seams.json").exists()

    write_workflow(repo, [["seg_01"], ["seg_02"]], two_segments())
    with pytest.raises(SystemExit) as exc:
        _run(repo, "--segments", "seg_09")
    assert exc.value.code == 2
    assert "seg_09" in capsys.readouterr().err
    assert not repo.wf(WF, "segments", "seams.json").exists()

    # a contract that is not JSON is a crash (exit 2), never a domain verdict
    repo.seg(WF, "seg_02", "contract.json").write_text("{not json", encoding="utf-8")
    assert _run(repo) == 2
    assert not repo.wf(WF, "segments", "seams.json").exists()


# --- the committed samples ----------------------------------------------------------------------

@pytest.mark.parametrize("wf", ["wf_0002", "wf_0003", "wf_0004", "wf_0006", "wf_0007"])
def test_every_committed_sample_has_clean_seams(tmp_path, wf):
    repo = prepare_workflow(tmp_path, wf)
    result = cs.check_seams(repo, wf)
    assert result["ok"] is True, json.dumps(result, indent=2)
    assert result["seams"], "every one of these samples has at least one seam"
