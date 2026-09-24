"""A declared key that is not unique in the expected rows falls back to the keyless comparison
(live hardening, Task L11).

Since Task L3 the analyzer declares every output's `keys` -- a judgment field -- and real Alteryx
outputs can repeat an id. The live evidence: wf_0001's excluded-orders stream `3_F` was declared
`keys: ["ORDER_ID"]`, the `edge` golden set legitimately holds two identical rows with `ORDER_ID`
103, and the translation reproduced them exactly -- yet the keyed comparison called the duplicate a
`GOLDEN_DATA` defect with `needs_human` and parked a correct translation. Now such a stream is
compared exactly as if it declared no keys (the multiset / nearest-match path, its tolerances, its
checks, its approvals), and the report carries `checks.keys_not_unique`, an advisory that is never
a failure by itself, plus a note in `normalizations_applied` saying the contract's keys should be
revisited.
"""
from __future__ import annotations

import copy
import json
import shutil

import compare as cmp
import validate_segment as vs
from lib import typed_csv
from lib.backend import DuckDBBackend
from lib.io import read_json, write_json
from lib.paths import Repo
from tests.helpers import ROOT, copy_pristine_mappings_and_catalog

TOL = {"float_abs": 1e-6, "float_rel": 1e-9, "timestamp_precision": "milliseconds",
       "rounding": {"abs": 0.01}}

FIELDS = [{"name": "ORDER_ID", "type": "Int32", "size": 4, "scale": None},
          {"name": "CUSTOMER", "type": "V_String", "size": 20, "scale": None},
          {"name": "REGION", "type": "V_String", "size": 10, "scale": None},
          {"name": "QTY", "type": "Int32", "size": 4, "scale": None},
          {"name": "TOTAL_NET", "type": "Double", "size": 8, "scale": None}]
NAMES = [f["name"] for f in FIELDS]
#: 103 twice, byte for byte: a real Alteryx output may repeat an id.
ROWS = [[101, "ALEXANDER", "EMEA", 2, 100.0],
        [102, "BO", None, 1, 250.0],
        [103, "CHANDRA", "APAC", 3, 75.5],
        [103, "CHANDRA", "APAC", 3, 75.5],
        [104, "DANA", None, 4, 12.25]]
#: The same stream with every id once.
UNIQUE_ROWS = [row for i, row in enumerate(ROWS) if i != 3]
_TYPES = {"Double": "FLOAT", "Int32": "NUMBER(38,0)"}


def contract(keys):
    return {"segment": "seg_01", "tolerances": {},
            "output": {"table": "MIG_WORK.ACT", "stream": "3_F", "kind": "target", "keys": list(keys),
                       "columns": [{"name": f["name"], "type": _TYPES.get(f["type"], "VARCHAR"),
                                    "nullable": True} for f in FIELDS]}}


KEYED, KEYLESS = contract(["ORDER_ID"]), contract([])


def run_tables(expected, actual, contract_, fields=FIELDS, **kw):
    b = DuckDBBackend()
    b.load_table("MIG_COMPARE.EXP", {"fields": fields, "rows": expected})
    b.load_table("MIG_WORK.ACT", {"fields": fields, "rows": actual})
    return cmp.compare(b, "MIG_COMPARE.EXP", "MIG_WORK.ACT", contract_, TOL, **kw)


def edit(index, column, value, rows=ROWS):
    rows = copy.deepcopy(rows)
    rows[index][NAMES.index(column)] = value
    return rows


def shapes(report):
    return [(c["class"], c["columns"], c["count"], c["scope"]) for c in report["diff_clusters"]]


def approval(cluster_class, columns):
    return [{"segment": "seg_01", "class": cluster_class, "columns": columns,
             "approver": "wf_owner", "date": "2026-09-23"}]


def advisory_note(report):
    return [entry for entry in report["normalizations_applied"] if entry.startswith("keys not unique")]


def as_keyless(report):
    """The report with the advisory and its note taken out, and no runtime: what a stream that
    declared no keys at all reports for the same rows."""
    report = copy.deepcopy(report)
    report.pop("runtime_ms")
    report["checks"].pop("keys_not_unique", None)
    report["normalizations_applied"] = [entry for entry in report["normalizations_applied"]
                                        if not entry.startswith("keys not unique")]
    return json.dumps(report, sort_keys=True)


def without_runtime(report):
    report = copy.deepcopy(report)
    report.pop("runtime_ms")
    return json.dumps(report, sort_keys=True)


# --- the live shape --------------------------------------------------------------------------------

#: The committed `edge` golden output of wf_0001's `3_F` stream (tool 8, EXCLUDED_ORDERS): a NULL row
#: and two identical rows with ORDER_ID 103 -- exactly the rows the live run's report was about.
LIVE_TABLE = typed_csv.read_table(ROOT / "workflows" / "wf_0001" / "golden" / "outputs" / "edge" / "8.csv")
#: The live run's contract entry for `3_F` (its columns are the committed contract's, verbatim) with
#: the key the analyzer model declared.
LIVE_OUTPUT = dict(next(o for o in read_json(ROOT / "workflows" / "wf_0001" / "segments" / "seg_01"
                                            / "contract.json")["outputs"] if o["stream"] == "3_F"),
                   keys=["ORDER_ID"])
LIVE_CONTRACT = {"segment": "seg_01", "tolerances": {}, "outputs": [LIVE_OUTPUT], "output": LIVE_OUTPUT}


def test_the_live_shape_passes_with_an_advisory():
    fields, rows = LIVE_TABLE["fields"], LIVE_TABLE["rows"]
    assert sum(1 for row in rows if row[2] == 103) == 2         # the premise: 103 twice, in the golden data
    r = run_tables(rows, copy.deepcopy(rows), LIVE_CONTRACT, fields=fields)
    assert (r["verdict"], r["diff_clusters"], r["needs_human"]) == ("PASS", [], False)
    assert r["checks"]["keys_not_unique"] == {"keys": ["ORDER_ID"], "duplicate_groups": 1,
                                              "examples": [{"key": {"ORDER_ID": 103}, "rows": 2}]}
    assert r["checks"]["set_diff"] == {"only_expected": 0, "only_actual": 0}
    # Keyless: the aggregates cover every row, not a comparable set of keys seen exactly once.
    assert r["checks"]["aggregates_rows"] == {"rows": 3, "verdict": "PASS"}
    assert advisory_note(r) == ["keys not unique: 3_F declares keys [ORDER_ID] but 1 key value "
                                "repeats in expected; compared without keys -- revisit the "
                                "contract's keys"]
    # An advisory is never a failing check: the verdict guard reads it as detail.
    for name, value in r["checks"].items():
        assert not cmp._check_failed(name, value), (name, value)


def test_the_live_shape_is_compared_exactly_as_a_stream_with_no_keys():
    fields, rows = LIVE_TABLE["fields"], LIVE_TABLE["rows"]
    keyless_output = dict(LIVE_OUTPUT, keys=[])
    keyless = {"segment": "seg_01", "tolerances": {}, "outputs": [keyless_output], "output": keyless_output}
    assert as_keyless(run_tables(rows, rows, LIVE_CONTRACT, fields=fields)) \
        == without_runtime(run_tables(rows, rows, keyless, fields=fields))


def test_the_live_segment_validates_through_validate_segment(tmp_path):
    # The whole live story on the committed wf_0001: the only change is the key the model declared
    # for 3_F. Before Task L11 this was a FAIL with GOLDEN_DATA + LOGIC and needs_human.
    copy_pristine_mappings_and_catalog(tmp_path)
    shutil.copytree(ROOT / "workflows" / "wf_0001", tmp_path / "workflows" / "wf_0001")
    repo = Repo(tmp_path)
    path = repo.seg("wf_0001", "seg_01", "contract.json")
    live = read_json(path)
    for output in live["outputs"]:
        if output["stream"] == "3_F":
            output["keys"] = ["ORDER_ID"]
    write_json(path, live)

    report = vs.validate_segment(repo, "wf_0001", "seg_01", ["edge"])
    assert (report["verdict"], report["needs_human"], report["diff_clusters"]) == ("PASS", False, [])
    edge = read_json(repo.seg("wf_0001", "seg_01", "validation.edge.json"))
    assert edge["checks"]["3_F:target"]["keys_not_unique"] == {
        "keys": ["ORDER_ID"], "duplicate_groups": 1, "examples": [{"key": {"ORDER_ID": 103}, "rows": 2}]}
    assert "keys_not_unique" not in edge["checks"]["6_Output:target"]      # a unique key: keyed as before
    assert advisory_note(edge) and not any(c["class"] == "GOLDEN_DATA" for c in edge["diff_clusters"])


# --- a real difference on the fallback: the keyless path's own clusters ---------------------------


def test_a_duplicate_with_a_value_changed_in_actual_fails_with_keyless_clusters():
    actual = edit(3, "TOTAL_NET", 80.0)                        # one of the two 103 rows is wrong
    r = run_tables(ROWS, actual, KEYED)
    assert r["verdict"] == "FAIL" and r["needs_human"] is False
    assert shapes(r) == [("LOGIC", ["TOTAL_NET"], 1, "columns")]
    c = r["diff_clusters"][0]
    assert c["paired_by"] == "nearest_match" and c["example_rows"][0]["key"] == {}
    assert c["example_rows"][0]["expected"] == {"TOTAL_NET": 75.5}
    assert c["example_rows"][0]["actual"] == {"TOTAL_NET": 80.0}
    assert r["checks"]["keys_not_unique"]["duplicate_groups"] == 1
    assert as_keyless(r) == without_runtime(run_tables(ROWS, actual, KEYLESS))


def test_a_lost_duplicate_is_a_row_cluster_never_golden_data():
    r = run_tables(ROWS, UNIQUE_ROWS, KEYED)
    assert shapes(r) == [("LOGIC", [], 1, "rows")]
    assert r["diff_clusters"][0]["note"] == "rows only in expected"
    assert r["checks"]["set_diff"] == {"only_expected": 1, "only_actual": 0}
    assert r["verdict"] == "FAIL" and r["needs_human"] is False
    assert as_keyless(r) == without_runtime(run_tables(ROWS, UNIQUE_ROWS, KEYLESS))


def test_a_needs_human_on_the_fallback_is_only_the_keyless_paths_own(monkeypatch):
    # The fallback itself never asks for a human; a check nothing accounts for still does, exactly
    # as on a keyless stream.
    monkeypatch.setattr(cmp._Comparison, "_aggregate_differences",
                        lambda self: ("FAIL", {"QTY": {"sum": {"expected": 1, "actual": 2}}}))
    r = run_tables(ROWS, copy.deepcopy(ROWS), KEYED)
    assert r["verdict"] == "FAIL" and r["needs_human"] is True
    assert [(c["class"], c["scope"]) for c in r["diff_clusters"]] == [("UNKNOWN", "synthetic")]
    assert "keys_not_unique" in r["checks"]


# --- approvals on the fallback work as on a keyless stream -----------------------------------------


def test_an_approval_covers_a_column_cluster_on_the_fallback_as_on_a_keyless_stream():
    rows = edit(0, "TOTAL_NET", 100.005)
    signed = {"accepted_classes": ["ROUNDING"], "approvals": approval("ROUNDING", ["TOTAL_NET"])}
    for contract_ in (KEYED, KEYLESS):
        assert run_tables(ROWS, rows, contract_, **signed)["verdict"] == "PASS_WITH_ACCEPTED_DIFF"
        assert run_tables(ROWS, rows, contract_, accepted_classes=["ROUNDING"])["verdict"] == "FAIL"
    r = run_tables(ROWS, rows, KEYED, **signed)
    assert shapes(r) == [("ROUNDING", ["TOTAL_NET"], 1, "columns")] and r["needs_human"] is False
    assert as_keyless(r) == without_runtime(run_tables(ROWS, rows, KEYLESS, **signed))


def test_an_approval_never_covers_a_row_cluster_on_the_fallback():
    signed = {"accepted_classes": ["LOGIC"], "approvals": approval("LOGIC", NAMES)}
    assert run_tables(ROWS, UNIQUE_ROWS, KEYED, **signed)["verdict"] == "FAIL"


# --- what does not change ---------------------------------------------------------------------------


def test_duplicates_only_in_actual_stay_a_logic_cluster():
    r = run_tables(UNIQUE_ROWS, ROWS, KEYED)
    assert [(c["class"], c["scope"], c["note"]) for c in r["diff_clusters"]] == [
        ("LOGIC", "rows", "duplicate keys in actual")]
    assert r["diff_clusters"][0]["example_rows"] == [
        {"key": {"ORDER_ID": 103}, "expected": None, "actual": {"rows": 2}}]
    assert r["verdict"] == "FAIL" and r["needs_human"] is False
    assert "keys_not_unique" not in r["checks"] and advisory_note(r) == []


def test_a_unique_key_keeps_the_keyed_comparison():
    r = run_tables(UNIQUE_ROWS, edit(0, "TOTAL_NET", 175.0, UNIQUE_ROWS), KEYED)
    assert shapes(r) == [("LOGIC", ["TOTAL_NET"], 1, "columns")]
    c = r["diff_clusters"][0]
    assert "paired_by" not in c and c["example_rows"][0]["key"] == {"ORDER_ID": 101}
    assert "keys_not_unique" not in r["checks"] and r["normalizations_applied"] == []
    same = run_tables(UNIQUE_ROWS, copy.deepcopy(UNIQUE_ROWS), KEYED)
    assert (same["verdict"], same["diff_clusters"]) == ("PASS", [])
    assert same["checks"]["aggregates_rows"] == {"rows": 4, "verdict": "PASS"}
    assert "keys_not_unique" not in same["checks"]


def test_a_golden_null_in_a_not_null_column_is_an_advisory_on_the_fallback_too():
    # Both contract claims the golden rows contradict (a repeating key, a NULL in a NOT NULL
    # column) are advisories; identical rows pass.
    not_null = copy.deepcopy(KEYED)
    not_null["output"]["columns"][NAMES.index("REGION")]["nullable"] = False
    r = run_tables(ROWS, copy.deepcopy(ROWS), not_null)
    assert "keys_not_unique" in r["checks"]
    assert r["checks"]["nullability_contract"]["columns"] == ["REGION"]
    assert r["verdict"] == "PASS" and r["needs_human"] is False


# --- the advisory itself --------------------------------------------------------------------------


def test_the_advisory_counts_every_repeated_key_and_samples_them_in_key_order():
    rows = ROWS + [[101, "ALEXANDER", "EMEA", 2, 100.0], [104, "DANA", None, 4, 12.25],
                   [104, "DANA", None, 4, 12.25]]
    r = run_tables(rows, copy.deepcopy(rows), KEYED, sample_rows=2)
    assert r["verdict"] == "PASS"
    assert r["checks"]["keys_not_unique"] == {
        "keys": ["ORDER_ID"], "duplicate_groups": 3,
        "examples": [{"key": {"ORDER_ID": 101}, "rows": 2}, {"key": {"ORDER_ID": 103}, "rows": 2}]}
    assert advisory_note(r) == ["keys not unique: 3_F declares keys [ORDER_ID] but 3 key values "
                                "repeat in expected; compared without keys -- revisit the "
                                "contract's keys"]


def test_a_compound_key_is_unique_only_as_a_whole():
    # (ORDER_ID, CUSTOMER) repeats only for the byte-identical 103 rows, and without them not at all.
    compound = contract(["ORDER_ID", "CUSTOMER"])
    r = run_tables(ROWS, copy.deepcopy(ROWS), compound)
    assert r["checks"]["keys_not_unique"] == {
        "keys": ["ORDER_ID", "CUSTOMER"], "duplicate_groups": 1,
        "examples": [{"key": {"ORDER_ID": 103, "CUSTOMER": "CHANDRA"}, "rows": 2}]}
    unique = run_tables(UNIQUE_ROWS, copy.deepcopy(UNIQUE_ROWS), compound)
    assert "keys_not_unique" not in unique["checks"]


def test_the_advisory_is_detail_for_the_verdict_guard():
    assert cmp._check_failed("keys_not_unique", {"keys": ["ORDER_ID"], "duplicate_groups": 1,
                                                 "examples": []}) is False


def test_a_merge_target_with_repeating_keys_needs_a_human():
    # A merge target's keys are its MERGE keys; a MERGE on a repeating key fails on Snowflake, so
    # the fallback's PASS is not enough there.
    merge = copy.deepcopy(KEYED)
    merge["output"]["write_mode"] = "update_insert"
    r = run_tables(ROWS, copy.deepcopy(ROWS), merge)
    assert r["needs_human"] is True
    assert "MERGE" in r["checks"]["keys_not_unique"]["note"]
