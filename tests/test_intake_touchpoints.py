"""`intake_touchpoints.py`: enumerating a workflow's external touchpoints and scoring Snowflake
candidates for them (plan task 10; contracts C6/C8). `tests/test_intake_prompt.py` already covers
wf_0001 end-to-end (yxdb header reading, candidate ranking including the naming fallback, two
outputs); this file covers what the brief describes in prose and doesn't test there: `normalize_key`
directly, a DB input's `table`/fields/top candidate, a DB output's `update_insert` write mode with
both SQL hooks, a macro and workflow constants listed non-blocking, and an unsupported workflow's
`manual`/unknown tools listed without blocking intake.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import intake_touchpoints as tpx
from dev import build_samples
from lib.paths import Repo
from tests.helpers import copy_pristine_mappings_and_catalog

ROOT = Path(__file__).parents[1]


def _repo(tmp_path, wf_id):
    copy_pristine_mappings_and_catalog(tmp_path)
    repo = Repo(tmp_path)
    build_samples.build(repo, ROOT / "samples", wf_id)
    return repo


def by_key(touchpoints, key):
    return next(t for t in touchpoints if t["key"] == key)


def by_kind(touchpoints, kind):
    return [t for t in touchpoints if t["kind"] == kind]


# --- normalize_key: contract C8, directly ---------------------------------------------------------

def test_normalize_key_file_source_keeps_last_two_components():
    assert tpx.normalize_key({"format": "yxdb", "source": r"C:\data\sales\orders.yxdb"}) \
        == "sales/orders.yxdb"


def test_normalize_key_unc_source_strips_the_leading_double_backslash():
    assert tpx.normalize_key({"format": "yxdb", "source": r"\\fileserver\crm\customers.yxdb"}) \
        == "crm/customers.yxdb"


def test_normalize_key_db_source_is_the_bare_alias():
    assert tpx.normalize_key({"format": "db", "alias": "prod_fin", "source": "<scrubbed:prod_fin>"}) \
        == "alias:prod_fin"


def test_normalize_key_spec_worked_example():
    # program spec §5.6's own example.
    assert tpx.normalize_key({"format": "yxdb", "source": r"\\fin\gl_2024.yxdb"}) == "fin/gl_2024.yxdb"


# --- wf_0003: DB input, DB output with update_insert and both SQL hooks --------------------------

def test_db_input_table_from_query_fields_from_meta_top_candidate(tmp_path):
    repo = _repo(tmp_path, "wf_0003")
    tps = tpx.run(repo, "wf_0003")
    t = by_key(tps, "alias:prod_fin")
    assert t["kind"] == "input" and t["blocking"] is True
    assert t["table"] == "dbo.GL_LEDGER"
    assert t["field_source"] == "meta"
    assert set(t["fields"]) == {"ACCT", "PERIOD", "POSTED", "AMOUNT", "REGION", "ENTRY_ID"}
    assert t["candidates"][0]["snowflake"] == "FINANCE.RAW.GL_LEDGER"
    assert t["candidates"][0]["matched"] == 6 and t["candidates"][0]["of"] == 6


def test_db_output_update_insert_carries_keys_and_both_sql_hooks(tmp_path):
    repo = _repo(tmp_path, "wf_0003")
    tps = tpx.run(repo, "wf_0003")
    t = by_key(tps, "alias:prod_fin/dbo.gl_summary")
    assert t["kind"] == "output" and t["blocking"] is True
    assert t["table"] == "dbo.GL_SUMMARY"
    assert t["write_mode"] == "update_insert"
    assert t["keys"] == ["ACCT", "PERIOD"]
    assert t["pre_sql"] and "DELETE FROM dbo.GL_SUMMARY" in t["pre_sql"]
    assert t["post_sql"] and "UPDATE dbo.GL_SUMMARY" in t["post_sql"]
    assert t["candidates"][0]["snowflake"] == "ANALYTICS.CURATED.GL_SUMMARY"


def test_workflow_constants_are_listed_non_blocking(tmp_path):
    repo = _repo(tmp_path, "wf_0003")
    tps = tpx.run(repo, "wf_0003")
    constants = by_kind(tps, "constant")
    assert {t["key"]: t["source"] for t in constants} == {
        "User.Region": "EMEA", "User.PeriodEnd": "2026-08-31"}
    assert all(t["blocking"] is False for t in constants)
    assert all(t["candidates"] == [] and t["resolved"] is None for t in constants)


# --- wf_0004: a macro, listed non-blocking --------------------------------------------------------

def test_macro_is_listed_non_blocking(tmp_path):
    repo = _repo(tmp_path, "wf_0004")
    tps = tpx.run(repo, "wf_0004")
    macros = by_kind(tps, "macro")
    assert len(macros) == 1
    m = macros[0]
    assert m["tool_id"] == "2" and m["blocking"] is False
    assert m["source"] == "Supporting_Macros/clean_codes.yxmc"
    assert m["key"] == "Supporting_Macros/clean_codes.yxmc"


# --- wf_0005: manual (run_command) and unknown tools, without blocking intake --------------------

def test_manual_and_unknown_tools_are_listed_without_blocking(tmp_path):
    repo = _repo(tmp_path, "wf_0005")
    tps = tpx.run(repo, "wf_0005")
    manual = by_kind(tps, "manual")
    # tool 2 (AcmeAnalytics.Dedupe.DedupeTool, unknown) and tool 3 (run_command)
    assert {t["tool_id"] for t in manual} == {"2", "3"}
    assert all(t["blocking"] is False for t in manual)
    run_cmd = next(t for t in manual if t["tool_id"] == "3")
    assert run_cmd["source"] == r"C:\scripts\notify.bat"
    unknown_tool = next(t for t in manual if t["tool_id"] == "2")
    assert unknown_tool["source"] == "AcmeAnalytics.Dedupe.DedupeTool"
    # the input and the one output still block intake as usual
    kinds = {t["tool_id"]: t["kind"] for t in tps}
    assert kinds["1"] == "input" and kinds["4"] == "output"
    assert next(t for t in tps if t["tool_id"] == "1")["blocking"] is True


# --- find_yxdb ---------------------------------------------------------------------------------------

def test_find_yxdb_falls_back_to_the_seeded_source_data_dir(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    found = tpx.find_yxdb(repo, "wf_0001", r"C:\data\sales\orders.yxdb")
    assert found is not None and found.name == "orders.yxdb"
    assert found == repo.wf("wf_0001", "source", "data", "orders.yxdb")


def test_find_yxdb_returns_none_for_a_file_that_is_nowhere(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    assert tpx.find_yxdb(repo, "wf_0001", r"C:\data\sales\does_not_exist.yxdb") is None


def test_find_yxdb_checks_extra_dirs_in_order(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    (d2 / "elsewhere.yxdb").write_bytes(b"not a real yxdb, existence is all that's checked")
    found = tpx.find_yxdb(repo, "wf_0001", r"C:\data\sales\elsewhere.yxdb", extra_dirs=[d1, d2])
    assert found == d2 / "elsewhere.yxdb"


# --- propose_candidates: unit-level, independent of any workflow ---------------------------------

def test_propose_candidates_keeps_only_half_overlap_and_appends_naming():
    catalog = [
        {"database": "SALES", "schema": "RAW", "table": "ORDERS", "column": c,
         "row_count": 100}
        for c in ("ORDER_ID", "CUSTOMER", "REGION", "AMOUNT_TXT", "QTY", "ORDER_DATE", "STATUS")
    ] + [
        {"database": "REF", "schema": "RAW", "table": "STATUS_CODES", "column": c, "row_count": 5}
        for c in ("STATUS", "CODE")
    ]
    tp = {"kind": "input", "source": r"C:\data\sales\orders.yxdb", "table": None,
          "fields": ["ORDER_ID", "CUSTOMER", "REGION", "AMOUNT_TXT", "QTY", "ORDER_DATE", "STATUS"]}
    program = {"target_database": "ANALYTICS", "raw_schema": "RAW", "target_schema": "CURATED"}
    candidates = tpx.propose_candidates(tp, catalog, program)
    # STATUS_CODES only matches 1/7 fields (< 0.5 overlap) so it never appears at all.
    assert [c["snowflake"] for c in candidates] == ["SALES.RAW.ORDERS", "ANALYTICS.RAW.ORDERS"]
    assert candidates[0]["matched"] == 7 and candidates[0]["missing"] == []
    assert candidates[-1]["basis"] == "naming"


def test_propose_candidates_returns_only_naming_when_no_catalog_table_matches():
    tp = {"kind": "output", "source": r"C:\data\out\sales_summary.yxdb", "table": None,
          "fields": ["REGION", "SIZE_BAND", "TOTAL_NET"]}
    program = {"target_database": "ANALYTICS", "raw_schema": "RAW", "target_schema": "CURATED"}
    candidates = tpx.propose_candidates(tp, [], program)
    assert candidates == [{"snowflake": "ANALYTICS.CURATED.SALES_SUMMARY", "basis": "naming"}]


# --- every sample's own "answers" map resolves against the touchpoints intake actually computes --
#
# `samples/wf_000N/sample.json["answers"]` is a hand-written fixture, not something any production
# code reads; this is a cross-check that it hasn't drifted from what `enumerate_touchpoints` (and
# thus a real intake session) would actually key its questions by. Per the coordinator's ruling on
# wf_0002/wf_0003 (a DB alias can serve many tables, and `sample.json`'s simple answers map has no
# room for the `/<table>` suffix a DB *output*'s real touchpoint key carries), an answer key is
# allowed to be either the touchpoint's normalized `key` (contract C8) or its bare `tool_id`.

def _resolve_sample_answer_key(touchpoints, key):
    for t in touchpoints:
        if t["key"] == key or t.get("tool_id") == key:
            return t
    return None


@pytest.mark.parametrize("wf_id", ["wf_0001", "wf_0002", "wf_0003", "wf_0004", "wf_0005"])
def test_every_sample_answer_key_resolves_to_a_blocking_touchpoint(tmp_path, wf_id):
    repo = _repo(tmp_path, wf_id)
    touchpoints = tpx.run(repo, wf_id)
    sample = json.loads((ROOT / "samples" / wf_id / "sample.json").read_text(encoding="utf-8"))
    for key in sample["answers"]:
        t = _resolve_sample_answer_key(touchpoints, key)
        assert t is not None, f"{wf_id}: sample answer key {key!r} matches no touchpoint key or tool id"
        assert t["kind"] in ("input", "output") and t["blocking"] is True, (wf_id, key, t["kind"])


# --- `parameter` touchpoints: no sample exercises a top-level (non-macro) Question tool, so this
# is a small synthetic-dag unit test standing in for that coverage gap (accepted per the
# coordinator's review of task 10). A macro's own interface tools live in its private `sub_dag` and
# never produce a workflow-level touchpoint; only a top-level `interface` node -- an Analytic App's
# own parameter -- does.

def test_parameter_touchpoint_from_a_top_level_interface_tool(tmp_path):
    repo = _repo(tmp_path, "wf_0001")  # any built repo works; enumerate_touchpoints takes dag directly
    dag = {
        "workflow": "wf_0001", "constants": {},
        "nodes": [
            {"tool_id": "50", "type": "interface",
             "plugin": "AlteryxGuiToolkit.Questions.NumericUpDown", "container_id": None,
             "annotation": "Minimum quantity", "in_anchors": [], "out_anchors": [], "meta": {},
             "config": {"name": "MinQty", "type": "NumericUpDown", "default": "1"}},
        ],
        "edges": [],
    }
    touchpoints = tpx.enumerate_touchpoints(repo, "wf_0001", dag)
    assert len(touchpoints) == 1
    t = touchpoints[0]
    assert t["id"] == "Q1" and t["kind"] == "parameter" and t["tool_id"] == "50"
    assert t["blocking"] is False and t["resolved"] is None
    assert t["key"] == "MinQty" and t["source"] == "1" and t["format"] == "NumericUpDown"
    assert t["annotation"] == "Minimum quantity"
