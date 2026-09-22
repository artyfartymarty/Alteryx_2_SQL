import copy, json, subprocess, sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from lib.backend import DuckDBBackend
from lib import typed_csv
import compare as cmp

FIELDS = [{"name": "ACCT", "type": "V_String", "size": 20, "scale": None}, {"name": "PERIOD", "type": "V_String", "size": 7, "scale": None},
          {"name": "REGION", "type": "V_String", "size": 10, "scale": None}, {"name": "AMOUNT", "type": "Double", "size": 8, "scale": None},
          {"name": "NAME", "type": "V_String", "size": 50, "scale": None}, {"name": "RID", "type": "Int32", "size": 4, "scale": None}]
ROWS = [["A1", "2026-08", "EMEA", 100.0, "Alexander", 1], ["A2", "2026-08", None, 250.005, "Bo", 2], ["A3", "2026-08", "EMEA", -0.0, "Chandrasekhar", 3]]
CONTRACT = {"segment": "seg_01", "output": {"table": "MIG_WORK.ACT", "stream": "9_Output", "kind": "work", "keys": ["ACCT", "PERIOD"],
            "columns": [{"name": f["name"], "type": "FLOAT" if f["type"] == "Double" else "NUMBER(38,0)" if f["type"] == "Int32" else "VARCHAR", "nullable": True} for f in FIELDS]},
            "ordering": {"keys": ["ACCT"], "alteryx_deterministic": True, "order_dependent_columns": ["RID"]}, "tolerances": {}}
TOL = {"float_abs": 1e-6, "float_rel": 1e-9, "timestamp_precision": "milliseconds", "rounding": {"abs": 0.01}}

def run(actual_rows, contract=CONTRACT, fields=FIELDS, **kw):
    b = DuckDBBackend()
    b.load_table("MIG_COMPARE.EXP", {"fields": FIELDS, "rows": ROWS}); b.load_table("MIG_WORK.ACT", {"fields": fields, "rows": actual_rows})
    return cmp.compare(b, "MIG_COMPARE.EXP", "MIG_WORK.ACT", contract, TOL, **kw)
def edit(i, col, value):
    rows = copy.deepcopy(ROWS); rows[i][[f["name"] for f in FIELDS].index(col)] = value; return rows

def test_identical_passes():
    r = run(ROWS)
    assert r["verdict"] == "PASS" and r["diff_clusters"] == [] and r["checks"]["counts"] == {"expected": 3, "actual": 3, "verdict": "PASS"}
def test_float_noise_inside_tolerance_passes(): assert run(edit(0, "AMOUNT", 100.0000000001))["verdict"] == "PASS"
def test_rounding():
    r = run(edit(1, "AMOUNT", 250.01)); c = r["diff_clusters"][0]
    assert r["verdict"] == "FAIL" and (c["class"], c["columns"], c["count"]) == ("ROUNDING", ["AMOUNT"], 1)
    assert c["example_rows"][0]["key"] == {"ACCT": "A2", "PERIOD": "2026-08"}
def test_rounding_can_be_accepted_with_an_approval():
    appr = [{"segment": "seg_01", "class": "ROUNDING", "columns": ["AMOUNT"], "approver": "wf_owner", "date": "2026-09-18"}]
    assert run(edit(1, "AMOUNT", 250.01), accepted_classes=["ROUNDING"], approvals=appr)["verdict"] == "PASS_WITH_ACCEPTED_DIFF"
    assert run(edit(1, "AMOUNT", 250.01), accepted_classes=["ROUNDING"])["verdict"] == "FAIL"          # class accepted, nobody signed
def test_logic(): assert run(edit(0, "AMOUNT", 175.0))["diff_clusters"][0]["class"] == "LOGIC"
def test_truncation(): assert run(edit(2, "NAME", "Chandrasek"))["diff_clusters"][0]["class"] == "TRUNCATION"
def test_null_semantics_value(): assert run(edit(0, "REGION", None))["diff_clusters"][0]["class"] == "NULL_SEMANTICS"
def test_null_semantics_missing_rows():
    r = run([x for x in ROWS if x[2] is not None]); c = r["diff_clusters"][0]
    assert r["checks"]["set_diff"] == {"only_expected": 1, "only_actual": 0} and (c["class"], c["columns"]) == ("NULL_SEMANTICS", ["REGION"])
def test_ordering():
    rows = copy.deepcopy(ROWS); rows[0][5], rows[1][5] = 2, 1
    assert run(rows)["diff_clusters"][0]["class"] == "ORDERING"
def test_whitespace_hint():
    c = run(edit(1, "NAME", "Bo "))["diff_clusters"][0]; assert c["class"] == "LOGIC" and c["hint"] == "whitespace_only"
def test_schema_failure_stops_early():
    fields = [dict(f, type="V_String") if f["name"] == "AMOUNT" else f for f in FIELDS]
    r = run([[*x[:3], str(x[3]), *x[4:]] for x in ROWS], fields=fields)
    assert r["checks"]["schema"] == "FAIL" and r["checks"]["counts"] == "SKIPPED" and r["diff_clusters"][0]["class"] == "TYPE"
def test_duplicate_keys_in_expected_need_a_human():
    b = DuckDBBackend(); b.load_table("MIG_COMPARE.EXP", {"fields": FIELDS, "rows": ROWS + [ROWS[0]]}); b.load_table("MIG_WORK.ACT", {"fields": FIELDS, "rows": ROWS})
    r = cmp.compare(b, "MIG_COMPARE.EXP", "MIG_WORK.ACT", CONTRACT, TOL)
    assert r["needs_human"] is True and r["diff_clusters"][0]["class"] == "GOLDEN_DATA"
def test_no_keys_uses_row_multiset():
    c = copy.deepcopy(CONTRACT); c["output"]["keys"] = []
    assert run(ROWS[::-1], contract=c)["verdict"] == "PASS"
    # The counts are still a row multiset, but since task 7b the surplus rows are fetched, paired
    # by nearest match and classified; tests/test_compare_keyless.py covers that path in full.
    x = run(edit(0, "AMOUNT", 1.0), contract=c)["diff_clusters"][0]
    assert (x["class"], x["columns"], x["paired_by"]) == ("LOGIC", ["AMOUNT"], "nearest_match")
def test_normalization_is_opt_in_and_reported():
    c = copy.deepcopy(CONTRACT); c["normalizations"] = ["trim:NAME"]
    r = run(edit(1, "NAME", "Bo "), contract=c); assert r["verdict"] == "PASS" and r["normalizations_applied"] == ["trim:NAME"]
def test_suspect_cte_from_segment_dag():
    dag = {"nodes": [{"tool_id": "4", "type": "formula", "config": {"formulas": [{"field": "AMOUNT", "expression": "1"}]}},
                     {"tool_id": "3", "type": "filter", "config": {"expression": "x"}}], "edges": [{"src": "3", "src_anchor": "T", "dst": "4", "dst_anchor": "Input"}]}
    assert run(edit(0, "AMOUNT", 175.0), segment_dag=dag)["diff_clusters"][0]["suspect_cte"] == "t4_formula"
    assert run(ROWS[:2], segment_dag=dag)["diff_clusters"][0]["suspect_cte"] == "t3_filter"
def test_cli_exit_codes(tmp_path):
    typed_csv.write_table(tmp_path / "exp.csv", {"fields": FIELDS, "rows": ROWS}); (tmp_path / "c.json").write_text(json.dumps(CONTRACT))
    db = tmp_path / "s.duckdb"; b = DuckDBBackend(str(db)); b.load_table("MIG_WORK.ACT", {"fields": FIELDS, "rows": edit(0, "AMOUNT", 1.0)}); b.close()
    script = Path(cmp.__file__)
    args = [sys.executable, str(script), "--expected", str(tmp_path / "exp.csv"), "--actual", "MIG_WORK.ACT", "--contract", str(tmp_path / "c.json"),
            "--out", str(tmp_path / "v.json"), "--db", str(db)]
    assert subprocess.run(args).returncode == 1 and json.loads((tmp_path / "v.json").read_text())["verdict"] == "FAIL"
    assert subprocess.run(args[:-1] + [str(tmp_path / "missing.duckdb.nope" / "x")]).returncode == 2


# --- behaviour the brief describes in prose but does not test -------------------------------

def run_tables(expected, actual, contract, **kw):
    """Like `run`, but both sides are given as typed_csv tables."""
    b = DuckDBBackend()
    b.load_table("MIG_COMPARE.EXP", expected); b.load_table("MIG_WORK.ACT", actual)
    return cmp.compare(b, "MIG_COMPARE.EXP", "MIG_WORK.ACT", contract, TOL, **kw)


def test_report_follows_the_validation_schema():
    r = run(ROWS)
    assert set(r) == {"segment", "golden_set", "verdict", "checks", "diff_clusters", "normalizations_applied",
                      "idempotent", "runtime_ms", "credits", "needs_human", "truncated"}
    assert (r["segment"], r["golden_set"], r["idempotent"], r["credits"]) == ("seg_01", None, None, None)
    assert r["needs_human"] is False and r["truncated"] is False and isinstance(r["runtime_ms"], int)
    assert r["checks"]["schema"] == "PASS" and r["checks"]["aggregates"] == "PASS"
    assert r["checks"]["column_mismatches"] == {} and r["normalizations_applied"] == []
    json.dumps(r)                      # the report is what --out writes: it must be JSON already


def test_two_runs_differ_only_in_runtime_ms():
    rows = edit(0, "AMOUNT", 175.0)
    first, second = run(rows), run(rows)
    assert first.pop("runtime_ms") >= 0 and second.pop("runtime_ms") >= 0
    assert json.dumps(first) == json.dumps(second)


def test_output_selects_the_stream_being_compared():
    narrow_fields = [f for f in FIELDS if f["name"] in ("ACCT", "PERIOD", "REGION")]
    narrow = {"table": "MIG_WORK.ACT", "stream": "31_U", "kind": "work", "keys": ["ACCT", "PERIOD"],
              "columns": [{"name": n, "type": "VARCHAR", "nullable": True} for n in ("ACCT", "PERIOD", "REGION")]}
    c = copy.deepcopy(CONTRACT); c["outputs"] = [c["output"], narrow]
    expected = {"fields": narrow_fields, "rows": [x[:3] for x in ROWS]}
    actual = {"fields": narrow_fields, "rows": [["A1", "2026-08", "APAC"]] + [x[:3] for x in ROWS[1:]]}
    # Without `output` the six-column default stream would not even match these tables' schema.
    assert run_tables(expected, actual, c, output=narrow)["diff_clusters"][0]["columns"] == ["REGION"]


def test_select_output_picks_the_stream_or_the_default():
    c = copy.deepcopy(CONTRACT)
    other = dict(c["output"], stream="31_U", table="MIG_WORK.ACT_U")
    c["outputs"] = [c["output"], other]
    assert cmp.select_output(c)["stream"] == "9_Output"
    assert cmp.select_output(c, "31_U")["table"] == "MIG_WORK.ACT_U"
    with pytest.raises(ValueError):
        cmp.select_output(c, "no_such_stream")


def test_contract_tolerance_overrides_the_global_float_tolerance():
    c = copy.deepcopy(CONTRACT); c["tolerances"] = {"AMOUNT": {"float_abs": 1.0}}
    assert run(edit(0, "AMOUNT", 100.5), contract=c)["verdict"] == "PASS"
    assert run(edit(0, "AMOUNT", 100.5))["verdict"] == "FAIL"


def test_contract_tolerance_also_applies_to_number_columns():
    c = copy.deepcopy(CONTRACT); c["tolerances"] = {"RID": {"float_abs": 1.5}}
    assert run(edit(0, "RID", 2), contract=c)["verdict"] == "PASS"
    assert run(edit(0, "RID", 2))["diff_clusters"][0]["class"] == "LOGIC"   # NUMBER is exact by default


TS_FIELDS = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
             {"name": "POSTED", "type": "DateTime", "size": 8, "scale": None}]
TS_CONTRACT = {"segment": "seg_01", "tolerances": {},
               "output": {"table": "MIG_WORK.ACT", "stream": "9_Output", "kind": "work", "keys": ["ID"],
                          "columns": [{"name": "ID", "type": "NUMBER(38,0)", "nullable": False},
                                      {"name": "POSTED", "type": "TIMESTAMP_NTZ", "nullable": True}]}}


def test_timestamp_precision_truncates_below_the_declared_precision():
    expected = {"fields": TS_FIELDS, "rows": [[1, datetime(2026, 8, 1, 10, 0, 0, 123000)]]}
    below = {"fields": TS_FIELDS, "rows": [[1, datetime(2026, 8, 1, 10, 0, 0, 123400)]]}
    above = {"fields": TS_FIELDS, "rows": [[1, datetime(2026, 8, 1, 10, 0, 0, 124000)]]}
    assert run_tables(expected, below, TS_CONTRACT)["verdict"] == "PASS"
    assert run_tables(expected, above, TS_CONTRACT)["diff_clusters"][0]["class"] == "LOGIC"


def test_duplicate_keys_only_in_actual_are_a_logic_diff():
    r = run(ROWS + [ROWS[0]])
    c = r["diff_clusters"][0]
    assert (c["class"], c["note"], r["needs_human"]) == ("LOGIC", "duplicate keys in actual", False)


def test_max_diff_rows_caps_the_pulled_rows_and_says_so():
    rows = edit(0, "AMOUNT", 175.0); rows[1][3] = 300.0
    capped = run(rows, max_diff_rows=1)
    assert capped["truncated"] is True and capped["diff_clusters"][0]["count"] == 1
    assert run(rows)["truncated"] is False and run(rows)["diff_clusters"][0]["count"] == 2


def test_columns_that_fail_the_same_way_form_one_cluster():
    rows = edit(0, "AMOUNT", 175.0); rows[0][2], rows[2][2] = "APAC", "AMER"
    clusters = run(rows)["diff_clusters"]
    assert len(clusters) == 1
    c = clusters[0]
    assert (c["class"], c["columns"], c["count"]) == ("LOGIC", ["AMOUNT", "REGION"], 2)
    assert [row["key"]["ACCT"] for row in c["example_rows"]] == ["A1", "A3"]     # key order
    assert c["example_rows"][0]["expected"] == {"AMOUNT": 100.0, "REGION": "EMEA"}
    assert c["example_rows"][1]["expected"] == {"REGION": "EMEA"}                # only what differs


def test_a_different_hint_makes_a_different_cluster():
    rows = edit(0, "AMOUNT", 175.0); rows[1][4] = "Bo "
    assert [(c["class"], c["columns"], c.get("hint")) for c in run(rows)["diff_clusters"]] == [
        ("LOGIC", ["AMOUNT"], None), ("LOGIC", ["NAME"], "whitespace_only")]


def test_rows_missing_on_both_sides_are_two_clusters_in_a_stable_order():
    r = run(copy.deepcopy(ROWS)[:2] + [["A4", "2026-08", "EMEA", 5.0, "Dana", 4]])
    assert r["checks"]["set_diff"] == {"only_expected": 1, "only_actual": 1}
    assert [(c["class"], c["note"]) for c in r["diff_clusters"]] == [
        ("LOGIC", "rows only in actual"), ("LOGIC", "rows only in expected")]


DEC_FIELDS = [{"name": "ACCT", "type": "V_String", "size": 20, "scale": None},
              {"name": "AMOUNT", "type": "FixedDecimal", "size": 19, "scale": 2}]
DEC_CONTRACT = {"segment": "seg_01", "tolerances": {},
                "output": {"table": "MIG_WORK.ACT", "stream": "9_Output", "kind": "work", "keys": ["ACCT"],
                           "columns": [{"name": "ACCT", "type": "VARCHAR", "nullable": False},
                                       {"name": "AMOUNT", "type": "NUMBER(19,2)", "nullable": True}]}}


def test_a_threshold_is_where_the_tolerance_says_it_is_not_where_binary_floats_put_it():
    # Two exact cents apart: 250.02 - 250.01 is 0.010000000000019 as a float, which would fall
    # outside a 0.01 threshold that the values are exactly on.
    expected = {"fields": DEC_FIELDS, "rows": [["A1", Decimal("250.01")]]}
    actual = {"fields": DEC_FIELDS, "rows": [["A1", Decimal("250.02")]]}
    c = run_tables(expected, actual, DEC_CONTRACT)["diff_clusters"][0]
    assert (c["class"], c["count"], c["example_rows"][0]["actual"]) == ("ROUNDING", 1, {"AMOUNT": 250.02})
    tolerant = copy.deepcopy(DEC_CONTRACT); tolerant["tolerances"] = {"AMOUNT": {"float_abs": 0.01}}
    assert run_tables(expected, actual, tolerant)["verdict"] == "PASS"
    tighter = copy.deepcopy(DEC_CONTRACT); tighter["tolerances"] = {"AMOUNT": {"float_abs": 0.009}}
    assert run_tables(expected, actual, tighter)["verdict"] == "FAIL"


def test_case_only_hint():
    c = run(edit(1, "NAME", "BO"))["diff_clusters"][0]
    assert (c["class"], c["hint"]) == ("LOGIC", "case_only")


def test_type_class_for_equal_values_written_differently():
    # Not a prefix either way, so TRUNCATION cannot claim it first: the values only differ in notation.
    expected = {"fields": FIELDS, "rows": [["A1", "2026-08", "EMEA", 1.0, "0100", 1]]}
    actual = {"fields": FIELDS, "rows": [["A1", "2026-08", "EMEA", 1.0, "100", 1]]}
    c = run_tables(expected, actual, CONTRACT)["diff_clusters"][0]
    assert (c["class"], c["columns"]) == ("TYPE", ["NAME"])


def test_schema_failure_lists_missing_and_extra_columns():
    fields = [f for f in FIELDS if f["name"] != "NAME"] + [{"name": "EXTRA", "type": "V_String", "size": 5, "scale": None}]
    r = run([[x[0], x[1], x[2], x[3], x[5], "z"] for x in ROWS], fields=fields)
    c = r["diff_clusters"][0]
    assert r["checks"]["schema"] == "FAIL" and c["class"] == "TYPE" and c["columns"] == ["EXTRA", "NAME"]
    assert "NAME" in c["note"] and "EXTRA" in c["note"]


def test_a_column_that_is_all_null_in_expected_does_not_explain_missing_rows():
    all_null = [[x[0], x[1], None, x[3], x[4], x[5]] for x in ROWS]
    r = run_tables({"fields": FIELDS, "rows": all_null}, {"fields": FIELDS, "rows": all_null[:2]}, CONTRACT)
    c = r["diff_clusters"][0]
    assert (c["class"], c["columns"]) == ("LOGIC", [])


def test_per_column_counts_and_aggregate_mismatches_are_reported():
    r = run(edit(0, "REGION", None))
    assert r["checks"]["column_counts"]["REGION"] == {"nulls": {"expected": 1, "actual": 2},
                                                      "distinct": {"expected": 1, "actual": 1}}
    assert r["checks"]["column_mismatches"] == {"REGION": 1}
    rounded = run(edit(1, "AMOUNT", 250.01))
    assert rounded["checks"]["aggregates"] == "FAIL"
    assert set(rounded["checks"]["aggregate_mismatches"]["AMOUNT"]) == {"sum", "max", "avg"}
    assert rounded["checks"]["aggregate_mismatches"]["AMOUNT"]["max"] == {"expected": 250.005, "actual": 250.01}


def test_an_unknown_normalization_is_refused():
    c = copy.deepcopy(CONTRACT); c["normalizations"] = ["lower:NAME"]
    with pytest.raises(ValueError):
        run(ROWS, contract=c)


def test_a_missing_table_is_an_error():
    b = DuckDBBackend(); b.load_table("MIG_COMPARE.EXP", {"fields": FIELDS, "rows": ROWS})
    with pytest.raises(ValueError):
        cmp.compare(b, "MIG_COMPARE.EXP", "MIG_WORK.NOPE", CONTRACT, TOL)


# --- fix round 1: a PASS verdict may never contradict the report's own checks ----------------

BIG_FIELDS = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
              {"name": "AMOUNT", "type": "Double", "size": 8, "scale": None}]
BIG_CONTRACT = {"segment": "seg_01", "tolerances": {},
                "output": {"table": "MIG_WORK.ACT", "stream": "9_Output", "kind": "work", "keys": ["ID"],
                           "columns": [{"name": "ID", "type": "NUMBER(38,0)", "nullable": True},
                                       {"name": "AMOUNT", "type": "FLOAT", "nullable": True}]}}


def run_noisy(noise, rows=10000, **kw):
    """Compares two `rows`-row tables where every AMOUNT carries the same one-directional noise.

    The rows are made with SQL: pushing 20,000 of them through `load_table` takes half a minute,
    and what this fixture is for is the arithmetic, not the loading.
    """
    side = round(rows ** 0.5)
    b = DuckDBBackend()
    b.load_table("MIG_COMPARE.NUMS", {"fields": [{"name": "N", "type": "Int32", "size": 4, "scale": None}],
                                      "rows": [[i] for i in range(side)]})
    for table, amount in (("MIG_COMPARE.EXP", 1.0), ("MIG_WORK.ACT", 1.0 + noise)):
        b.load_table(table, {"fields": BIG_FIELDS, "rows": []})
        b.execute(f"INSERT INTO {table} SELECT A.N * {side} + B.N, {amount!r} "
                  f"FROM MIG_COMPARE.NUMS A, MIG_COMPARE.NUMS B")
    return cmp.compare(b, "MIG_COMPARE.EXP", "MIG_WORK.ACT", BIG_CONTRACT, TOL, **kw)


def test_per_row_noise_within_tolerance_does_not_accumulate_into_a_sum_failure():
    # 10,000 rows each 5e-7 out against float_abs=1e-6: every row passes, and the sum is allowed
    # to drift by rows * float_abs, so the report says PASS without contradicting itself.
    r = run_noisy(5e-7)
    assert r["checks"]["counts"]["expected"] == 10000 and r["verdict"] == "PASS"
    assert r["checks"]["aggregates"] == "PASS" and r["checks"]["aggregate_mismatches"] == {}
    assert r["checks"]["column_mismatches"] == {} and r["truncated"] is False


def test_per_row_noise_outside_tolerance_is_a_failure():
    r = run_noisy(5e-6)
    assert r["verdict"] == "FAIL" and r["diff_clusters"][0]["columns"] == ["AMOUNT"]
    assert r["checks"]["aggregates"] == "FAIL"


def test_noise_rows_cannot_crowd_out_a_real_difference():
    # 20 keys whose only difference is inside tolerance sort before the one key that is really
    # wrong. Pulling them would fill max_diff_rows and hide the real difference entirely.
    fields = [{"name": "ACCT", "type": "V_String", "size": 20, "scale": None},
              {"name": "AMOUNT", "type": "Double", "size": 8, "scale": None}]
    contract = {"segment": "seg_01", "tolerances": {},
                "output": {"table": "MIG_WORK.ACT", "stream": "9_Output", "kind": "work", "keys": ["ACCT"],
                           "columns": [{"name": "ACCT", "type": "VARCHAR", "nullable": True},
                                       {"name": "AMOUNT", "type": "FLOAT", "nullable": True}]}}
    expected = {"fields": fields, "rows": [[f"A{i:04d}", 1.0] for i in range(20)] + [["Z0001", 10.0]]}
    actual = {"fields": fields, "rows": [[f"A{i:04d}", 1.0 + 1e-9] for i in range(20)] + [["Z0001", 99.0]]}
    r = run_tables(expected, actual, contract, max_diff_rows=5)
    assert r["truncated"] is False and r["verdict"] == "FAIL"
    c = r["diff_clusters"][0]
    assert (c["class"], c["columns"], c["count"]) == ("LOGIC", ["AMOUNT"], 1)
    assert c["example_rows"][0]["key"] == {"ACCT": "Z0001"}


def test_the_sql_pre_filter_never_excludes_a_row_the_exact_test_would_flag():
    # Found by searching near the boundary: these two values differ by 0.10000000000000001 as
    # exact decimals, which is over a float_abs of 0.1 and so a real difference — but subtracting
    # them in binary gives exactly 0.1, so a pre-filter of `> 0.1` would drop the row before the
    # exact test in Python ever saw it. The margin on the SQL threshold is what stops that.
    mine, theirs = 0.19999999999999965, 0.09999999999999964
    assert cmp._abs_difference(mine, theirs) > 0.1      # the exact judge: this is a difference
    assert abs(mine - theirs) <= 0.1                    # binary subtraction: this is not one
    contract = copy.deepcopy(BIG_CONTRACT); contract["tolerances"] = {"AMOUNT": {"float_abs": 0.1}}
    r = run_tables({"fields": BIG_FIELDS, "rows": [[1, mine]]},
                   {"fields": BIG_FIELDS, "rows": [[1, theirs]]}, contract)
    assert r["checks"]["column_mismatches"] == {"AMOUNT": 1} and r["verdict"] == "FAIL"
    c = r["diff_clusters"][0]
    assert (c["class"], c["columns"], c["count"]) == ("LOGIC", ["AMOUNT"], 1)


def test_the_pre_filter_margin_is_far_wider_than_float_subtraction_error():
    # Half an ulp of a result near the tolerance is ~1e-16 relative; the margin is 1e-9 relative.
    assert cmp._PRE_FILTER_MARGIN == 1e-9


def test_a_failing_check_with_no_cluster_is_never_a_pass(monkeypatch):
    # No arithmetic reaches this state any more, so the synthesis path is forced directly.
    monkeypatch.setattr(cmp._Comparison, "_aggregate_differences",
                        lambda self: ("FAIL", {"AMOUNT": {"sum": {"expected": 1, "actual": 2}}}))
    r = run(ROWS)
    assert r["verdict"] == "FAIL" and r["needs_human"] is True
    c = r["diff_clusters"][0]
    assert (c["class"], c["columns"], c["example_rows"], c["suspect_cte"]) == ("UNKNOWN", ["AMOUNT"], [], None)
    assert c["hint"] == "aggregates failed with no row-level difference"


def test_a_truncated_diff_with_no_surviving_difference_is_still_a_failure():
    # Timestamps below the declared precision are pulled but are not differences, so they can
    # still fill max_diff_rows; nothing survives into a cluster and the cap must speak for itself.
    expected = {"fields": TS_FIELDS, "rows": [[1, datetime(2026, 8, 1, 10, 0, 0, 123000)],
                                              [2, datetime(2026, 8, 2, 11, 0, 0, 123000)]]}
    actual = {"fields": TS_FIELDS, "rows": [[1, datetime(2026, 8, 1, 10, 0, 0, 123400)],
                                            [2, datetime(2026, 8, 2, 11, 0, 0, 123400)]]}
    r = run_tables(expected, actual, TS_CONTRACT, max_diff_rows=1)
    assert (r["truncated"], r["verdict"], r["needs_human"]) == (True, "FAIL", True)
    c = r["diff_clusters"][0]
    assert c["class"] == "UNKNOWN" and c["hint"].startswith("diff truncated at max_diff_rows")


def test_a_truncated_diff_is_a_failure_even_when_every_cluster_is_approved():
    rows = edit(1, "AMOUNT", 250.01); rows[0][3] = 100.005
    approvals = [{"segment": "seg_01", "class": "ROUNDING", "columns": ["AMOUNT"], "approver": "wf_owner",
                  "date": "2026-09-18"}]
    whole = run(rows, accepted_classes=["ROUNDING"], approvals=approvals)
    assert whole["verdict"] == "PASS_WITH_ACCEPTED_DIFF" and whole["truncated"] is False
    capped = run(rows, accepted_classes=["ROUNDING"], approvals=approvals, max_diff_rows=1)
    assert capped["truncated"] is True and capped["verdict"] == "FAIL"


def test_an_accepted_diff_may_leave_a_failing_check_only_when_a_cluster_covers_it():
    approvals = [{"segment": "seg_01", "class": "ROUNDING", "columns": ["AMOUNT"], "approver": "wf_owner",
                  "date": "2026-09-18"}]
    r = run(edit(1, "AMOUNT", 250.01), accepted_classes=["ROUNDING"], approvals=approvals)
    assert r["verdict"] == "PASS_WITH_ACCEPTED_DIFF" and r["checks"]["aggregates"] == "FAIL"
    covered = {name for cluster in r["diff_clusters"] for name in cluster["columns"]}
    assert set(r["checks"]["aggregate_mismatches"]) <= covered


# --- fix round 2: one cluster may only account for what it actually explains -----------------

REPRO_FIELDS = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
                {"name": "Z", "type": "Double", "size": 8, "scale": None}]
REPRO_CONTRACT = {"segment": "seg_01", "tolerances": {},
                  "output": {"table": "MIG_WORK.ACT", "stream": "9_Output", "kind": "work", "keys": ["ID"],
                             "columns": [{"name": "ID", "type": "NUMBER(38,0)", "nullable": True},
                                         {"name": "Z", "type": "FLOAT", "nullable": True}]}}


def approval(cluster_class, columns):
    return [{"segment": "seg_01", "class": cluster_class, "columns": columns,
             "approver": "wf_owner", "date": "2026-09-18"}]


def repro(push, finding="duplicate"):
    """The re-reviewer's input: 200 rows of +/-1e9 that cancel, pushed by `push`, plus the one
    real finding on key 201 -- either a duplicated key (a cluster about *rows*, which fix round 1
    made unapprovable) or a value difference (a cluster about *columns*, the only kind an
    approval can cover)."""
    expected = [[i, 1e9] for i in range(1, 101)] + [[i, -1e9] for i in range(101, 201)] + [[201, 0.0]]
    tail = [[201, 0.0], [201, 0.0]] if finding == "duplicate" else [[201, 0.5]]
    actual = [[i, z + push] for i, z in expected[:200]] + tail
    return {"fields": REPRO_FIELDS, "rows": expected}, {"fields": REPRO_FIELDS, "rows": actual}


def test_the_repro_passes_for_the_right_reason_under_the_corrected_sum_bound():
    # Every row's Z is inside its OWN declared tolerance: float_rel * 1e9 == 1.0, and the push is
    # 0.99. So the sum is entitled to drift by n*float_abs + float_rel*SUM(ABS(Z)) ~ 200, not by
    # the ~2e-7 the old formula allowed once the large values cancelled to a sum of 0. The 198 is
    # declared noise, not a difference; the only real finding is key 201's Z, and that is
    # approved. If the owner thinks +/-0.99 on 1e9 matters, they tighten float_rel.
    r = run_tables(*repro(0.99, "value"), contract=REPRO_CONTRACT,
                   accepted_classes=["LOGIC"], approvals=approval("LOGIC", ["Z"]))
    assert r["checks"]["aggregates"] == "PASS" and r["checks"]["aggregate_mismatches"] == {}
    assert r["checks"]["aggregates_rows"] == {"rows": 201, "verdict": "PASS"}
    assert r["checks"]["column_mismatches"] == {"Z": 1}
    assert [(c["class"], c["columns"], c["scope"]) for c in r["diff_clusters"]] == [("LOGIC", ["Z"], "columns")]
    assert r["verdict"] == "PASS_WITH_ACCEPTED_DIFF" and r["needs_human"] is False


def test_the_same_repro_with_a_duplicated_key_cannot_be_approved():
    # Same arithmetic, but the finding is about rows, so no signature reaches it (fix round 1).
    r = run_tables(*repro(0.99), contract=REPRO_CONTRACT,
                   accepted_classes=["LOGIC"], approvals=approval("LOGIC", ["ID"]))
    assert r["checks"]["aggregates"] == "PASS" and r["checks"]["aggregate_mismatches"] == {}
    assert [(c["class"], c["note"]) for c in r["diff_clusters"]] == [("LOGIC", "duplicate keys in actual")]
    assert r["verdict"] == "FAIL" and r["needs_human"] is False


def test_an_unrelated_row_cluster_cannot_excuse_a_real_difference_on_another_column():
    # Same shape, but Z is pushed past its own tolerance, so per-row clusters do form.
    r = run_tables(*repro(2.0), contract=REPRO_CONTRACT,
                   accepted_classes=["LOGIC"], approvals=approval("LOGIC", ["ID"]))
    assert r["verdict"] == "FAIL"
    assert ("LOGIC", ["Z"]) in [(c["class"], c["columns"]) for c in r["diff_clusters"]]
    assert r["checks"]["column_mismatches"] == {"Z": 200}


def test_an_approved_row_cluster_cannot_excuse_an_aggregate_failure_elsewhere(monkeypatch):
    # The direct regression for the bypass: an aggregate failure on Z with no cluster listing Z,
    # and an approved duplicate-key cluster on ID sitting in the report next to it.
    monkeypatch.setattr(cmp._Comparison, "_aggregate_differences",
                        lambda self: ("FAIL", {"Z": {"sum": {"expected": 0.0, "actual": 198.0}}}))
    r = run_tables(*repro(0.99), contract=REPRO_CONTRACT,
                   accepted_classes=["LOGIC"], approvals=approval("LOGIC", ["ID"]))
    assert r["verdict"] == "FAIL" and r["needs_human"] is True
    synthesized = [c for c in r["diff_clusters"] if c["class"] == "UNKNOWN"]
    assert [(c["columns"], c["hint"]) for c in synthesized] == [
        (["Z"], "aggregates failed with no row-level difference")]


def test_moved_rows_do_not_disturb_aggregates():
    expected = {"fields": REPRO_FIELDS, "rows": [[1, 10.0], [2, 20.0], [3, 30.0]]}
    actual = {"fields": REPRO_FIELDS, "rows": [[1, 10.0], [2, 20.0]]}
    r = run_tables(expected, actual, REPRO_CONTRACT,
                   accepted_classes=["LOGIC"], approvals=approval("LOGIC", ["Z"]))
    # The aggregates cover the comparable set (keys 1 and 2), so the missing row cannot move them;
    # it is reported once, by set_diff and its own cluster. Since fix round 1 that cluster is
    # about rows, so the approval cannot reach it and the verdict stays FAIL either way.
    assert r["checks"]["aggregates"] == "PASS" and r["checks"]["aggregates_rows"]["rows"] == 2
    assert r["checks"]["set_diff"] == {"only_expected": 1, "only_actual": 0}
    assert r["verdict"] == "FAIL"
    assert run_tables(expected, actual, REPRO_CONTRACT)["verdict"] == "FAIL"
    # The same shape with the row PRESENT but wrong is a cluster about a column, which a signature
    # can cover -- and the aggregates then cover all three keys and see the difference.
    changed = {"fields": REPRO_FIELDS, "rows": [[1, 10.0], [2, 20.0], [3, 31.0]]}
    covered = run_tables(expected, changed, REPRO_CONTRACT,
                         accepted_classes=["LOGIC"], approvals=approval("LOGIC", ["Z"]))
    assert covered["checks"]["aggregates_rows"]["rows"] == 3
    assert covered["checks"]["aggregates"] == "FAIL" and covered["checks"]["set_diff"] == {
        "only_expected": 0, "only_actual": 0}
    assert covered["verdict"] == "PASS_WITH_ACCEPTED_DIFF"


def test_cancelling_magnitudes_with_noise_inside_the_declared_tolerance_pass():
    rows = [[i, 1e9] for i in range(1, 101)] + [[i, -1e9] for i in range(101, 201)]
    expected = {"fields": REPRO_FIELDS, "rows": rows}
    inside = {"fields": REPRO_FIELDS, "rows": [[i, z + 0.99] for i, z in rows]}
    outside = {"fields": REPRO_FIELDS, "rows": [[i, z + 2.0] for i, z in rows]}
    assert run_tables(expected, inside, REPRO_CONTRACT)["verdict"] == "PASS"
    r = run_tables(expected, outside, REPRO_CONTRACT)
    assert r["verdict"] == "FAIL" and r["diff_clusters"][0]["columns"] == ["Z"]


def test_no_comparable_rows_at_all_is_itself_a_failure():
    expected = {"fields": REPRO_FIELDS, "rows": [[1, 10.0], [2, 20.0]]}
    actual = {"fields": REPRO_FIELDS, "rows": [[3, 10.0], [4, 20.0]]}
    r = run_tables(expected, actual, REPRO_CONTRACT,
                   accepted_classes=["LOGIC"], approvals=approval("LOGIC", []))
    # Both row-presence clusters are approved, so without this check the report would pass while
    # having compared nothing at all.
    assert r["checks"]["aggregates_rows"] == {"rows": 0, "verdict": "FAIL"}
    assert r["verdict"] == "FAIL" and r["needs_human"] is True


def test_two_empty_tables_compare_clean():
    # The `empty` golden set is one of the four the program defines, and DuckDB's COUNT_IF is
    # NULL over an empty set where Snowflake's is 0.
    empty = {"fields": REPRO_FIELDS, "rows": []}
    r = run_tables(empty, empty, REPRO_CONTRACT)
    assert r["verdict"] == "PASS" and r["diff_clusters"] == []
    assert r["checks"]["counts"] == {"expected": 0, "actual": 0, "verdict": "PASS"}
    assert r["checks"]["aggregates_rows"] == {"rows": 0, "verdict": "PASS"}


def test_every_cluster_says_what_it_can_account_for():
    scoped = lambda report: {(c["class"], c["scope"]) for c in report["diff_clusters"]}
    assert scoped(run(edit(0, "AMOUNT", 175.0))) == {("LOGIC", "columns")}
    assert scoped(run(ROWS[:2])) == {("LOGIC", "rows")}
    assert scoped(run(ROWS + [ROWS[0]])) == {("LOGIC", "rows")}
    schema = run([[*x[:3], str(x[3]), *x[4:]] for x in ROWS],
                 fields=[dict(f, type="V_String") if f["name"] == "AMOUNT" else f for f in FIELDS])
    assert scoped(schema) == {("TYPE", "schema")}


def _unaccounted(report):
    """The property, read only off the report: what has this report failed to account for?

    Deliberately written without any of compare.py's own helpers — a verdict that starts with
    PASS must leave this empty. Under PASS_WITH_ACCEPTED_DIFF every cluster is approved by
    definition of that verdict, so "listed by a cluster" and "listed by an approved cluster" are
    the same set there.
    """
    checks, clusters = report["checks"], report["diff_clusters"]
    covered = {name for c in clusters if c["scope"] == "columns" for name in c["columns"]}
    rows = [c for c in clusters if c["scope"] == "rows"]
    missing = []
    if report["truncated"]:
        missing.append("truncated")
    if checks["schema"] != "PASS":
        missing.append("schema")
        return missing                      # every other check is SKIPPED behind it
    if checks["nullability"] != "PASS" and not any(c["scope"] == "columns" for c in clusters):
        missing.append("nullability")
    if checks["aggregates"] != "PASS":
        missing += [f"aggregates:{n}" for n in checks["aggregate_mismatches"] if n not in covered]
    missing += [f"column_mismatches:{n}" for n in checks["column_mismatches"] if n not in covered]
    if checks["counts"]["verdict"] != "PASS" and not rows:
        missing.append("counts")
    if (checks["set_diff"]["only_expected"] or checks["set_diff"]["only_actual"]) and not rows:
        missing.append("set_diff")
    if checks["aggregates_rows"]["verdict"] != "PASS":
        missing.append("aggregates_rows")
    return missing


def _every_scenario():
    """(name, report) for every shape of run this script can produce."""
    no_keys = copy.deepcopy(CONTRACT); no_keys["output"]["keys"] = []
    duplicated = DuckDBBackend()
    duplicated.load_table("MIG_COMPARE.EXP", {"fields": FIELDS, "rows": ROWS + [ROWS[0]]})
    duplicated.load_table("MIG_WORK.ACT", {"fields": FIELDS, "rows": ROWS})
    yield "identical", run(ROWS)
    yield "float noise", run(edit(0, "AMOUNT", 100.0000000001))
    yield "rounding", run(edit(1, "AMOUNT", 250.01))
    yield "logic", run(edit(0, "AMOUNT", 175.0))
    yield "truncation", run(edit(2, "NAME", "Chandrasek"))
    yield "null semantics", run(edit(0, "REGION", None))
    yield "ordering", run([[*x[:5], {1: 2, 2: 1}.get(x[5], x[5])] for x in ROWS])
    yield "type", run_tables({"fields": FIELDS, "rows": [["A1", "2026-08", "EMEA", 1.0, "0100", 1]]},
                             {"fields": FIELDS, "rows": [["A1", "2026-08", "EMEA", 1.0, "100", 1]]}, CONTRACT)
    yield "schema", run([[*x[:3], str(x[3]), *x[4:]] for x in ROWS],
                        fields=[dict(f, type="V_String") if f["name"] == "AMOUNT" else f for f in FIELDS])
    yield "no keys, equal", run(ROWS[::-1], contract=no_keys)
    yield "no keys, different", run(edit(0, "AMOUNT", 1.0), contract=no_keys)
    yield "duplicate keys", cmp.compare(duplicated, "MIG_COMPARE.EXP", "MIG_WORK.ACT", CONTRACT, TOL)
    yield "rows only in expected", run(ROWS[:2])
    yield "rows only in actual", run(ROWS + [["A4", "2026-08", "EMEA", 5.0, "Dana", 4]])
    yield "count only", run(ROWS + [ROWS[0]])
    yield "aggregate drift", run_noisy(5e-7)
    yield "truncated", run(edit(0, "AMOUNT", 175.0), max_diff_rows=0)
    # …and the shapes that are allowed to end in PASS_WITH_ACCEPTED_DIFF
    yield "approved rounding", run(edit(1, "AMOUNT", 250.01), accepted_classes=["ROUNDING"],
                                   approvals=approval("ROUNDING", ["AMOUNT"]))
    yield "approved value diff beside cancelling magnitudes", run_tables(
        *repro(0.99, "value"), contract=REPRO_CONTRACT, accepted_classes=["LOGIC"],
        approvals=approval("LOGIC", ["Z"]))
    # …and the two shapes a signature may never reach, because they are about rows (fix round 1)
    yield "signed duplicate key", run_tables(*repro(0.99), contract=REPRO_CONTRACT,
                                             accepted_classes=["LOGIC"],
                                             approvals=approval("LOGIC", ["ID"]))
    yield "signed missing rows", run_tables(
        {"fields": REPRO_FIELDS, "rows": [[1, 10.0], [2, 20.0], [3, 30.0]]},
        {"fields": REPRO_FIELDS, "rows": [[1, 10.0], [2, 20.0]]}, REPRO_CONTRACT,
        accepted_classes=["LOGIC"], approvals=approval("LOGIC", ["Z"]))
    yield "approved cluster, unrelated aggregate", run_tables(
        *repro(2.0), contract=REPRO_CONTRACT, accepted_classes=["LOGIC"],
        approvals=approval("LOGIC", ["ID"]))
    yield "nothing comparable", run_tables(
        {"fields": REPRO_FIELDS, "rows": [[1, 10.0]]}, {"fields": REPRO_FIELDS, "rows": [[2, 10.0]]},
        REPRO_CONTRACT, accepted_classes=["LOGIC"], approvals=approval("LOGIC", []))


def test_a_pass_verdict_always_means_every_check_passed():
    seen = set()
    for name, report in _every_scenario():
        seen.add(report["verdict"])
        if report["verdict"] == "PASS":
            assert report["truncated"] is False, name
            for check, value in report["checks"].items():
                assert not cmp._check_failed(check, value), (name, check, value)
        elif report["verdict"] == "PASS_WITH_ACCEPTED_DIFF":
            assert _unaccounted(report) == [], name
        assert _unaccounted(report) == [] or report["verdict"] == "FAIL", name
    assert {"PASS", "PASS_WITH_ACCEPTED_DIFF", "FAIL"} <= seen   # all three arms were exercised


def test_every_checks_entry_is_classified_as_a_verdict_or_as_detail():
    # The guard can only be mechanical if a new `checks` entry has to declare which it is.
    for check, value in run(ROWS)["checks"].items():
        cmp._check_failed(check, value)
    with pytest.raises(ValueError):
        cmp._check_failed("something_new", {"whatever": 1})


# --- fix round 1: nullability, checked on the data (program spec §9.2) ------------------------

NN_FIELDS = [{"name": "ACCT", "type": "V_String", "size": 20, "scale": None},
             {"name": "REGION", "type": "V_String", "size": 10, "scale": None}]
NN_CONTRACT = {"segment": "seg_01", "tolerances": {},
               "output": {"table": "MIG_WORK.ACT", "stream": "9_Output", "kind": "work", "keys": ["ACCT"],
                          "columns": [{"name": "ACCT", "type": "VARCHAR", "nullable": False},
                                      {"name": "REGION", "type": "VARCHAR", "nullable": False}]}}


def test_a_null_in_a_non_nullable_column_of_actual_is_a_null_semantics_diff():
    expected = {"fields": NN_FIELDS, "rows": [["A1", "EMEA"], ["A2", "APAC"]]}
    actual = {"fields": NN_FIELDS, "rows": [["A1", None], ["A2", None]]}
    r = run_tables(expected, actual, NN_CONTRACT)
    assert r["checks"]["nullability"] == "FAIL" and r["verdict"] == "FAIL"
    c = [x for x in r["diff_clusters"] if x.get("note") == "NULL in a column the contract calls NOT NULL"][0]
    assert (c["class"], c["columns"], c["count"]) == ("NULL_SEMANTICS", ["REGION"], 2)
    assert c["example_rows"][0]["key"] == {"ACCT": "A1"} and c["example_rows"][0]["actual"] == {"REGION": None}


def test_a_null_in_a_non_nullable_column_of_expected_needs_a_human():
    expected = {"fields": NN_FIELDS, "rows": [["A1", None]]}
    actual = {"fields": NN_FIELDS, "rows": [["A1", None]]}
    r = run_tables(expected, actual, NN_CONTRACT)
    assert r["checks"]["nullability"] == "FAIL" and r["verdict"] == "FAIL" and r["needs_human"] is True
    c = r["diff_clusters"][0]
    assert (c["class"], c["columns"], c["count"]) == ("GOLDEN_DATA", ["REGION"], 1)


def test_nulls_in_a_nullable_column_are_not_a_nullability_failure():
    nullable = copy.deepcopy(NN_CONTRACT)
    nullable["output"]["columns"][1]["nullable"] = True
    rows = [["A1", None], ["A2", "APAC"]]
    r = run_tables({"fields": NN_FIELDS, "rows": rows}, {"fields": NN_FIELDS, "rows": rows}, nullable)
    assert r["checks"]["nullability"] == "PASS" and r["verdict"] == "PASS"


# --- F4 (coordinator ruling): GOLDEN_DATA and UNKNOWN are never approvable, at any scope --------
#
# `_approved` already refused any cluster whose `scope` isn't `"columns"`. But
# `_nullability_clusters` writes a real, scope="columns" GOLDEN_DATA cluster (a NOT NULL violation
# sitting in the *golden* data itself, not the translation) -- a class no human signature can be
# allowed to wave through, because the golden data is what every other check is judged against; if
# it is wrong, no amount of "approving" the difference makes the comparison meaningful again. The
# same must hold for UNKNOWN even though no code path currently produces one at column scope (the
# ones `_account_for_every_check` synthesizes are scope="synthetic") -- this is defense in depth
# against a future classifier change that might.

def test_golden_data_is_never_approvable_even_with_a_matching_column_approval():
    # Both sides hold the same NULL, so this raises GOLDEN_DATA (expected) AND NULL_SEMANTICS
    # (actual) side by side (both scope="columns", both column REGION) -- approving BOTH isolates
    # the one under test: if GOLDEN_DATA could be signed off, every cluster here would be approved
    # and the verdict would be PASS_WITH_ACCEPTED_DIFF; it must stay FAIL regardless.
    expected = {"fields": NN_FIELDS, "rows": [["A1", None]]}
    actual = {"fields": NN_FIELDS, "rows": [["A1", None]]}
    appr = [{"segment": "seg_01", "class": "GOLDEN_DATA", "columns": ["REGION"], "approver": "wf_owner",
             "date": "2026-09-18"},
            {"segment": "seg_01", "class": "NULL_SEMANTICS", "columns": ["REGION"], "approver": "wf_owner",
             "date": "2026-09-18"}]
    r = run_tables(expected, actual, NN_CONTRACT,
                   accepted_classes=["GOLDEN_DATA", "NULL_SEMANTICS"], approvals=appr)
    assert r["verdict"] == "FAIL" and r["needs_human"] is True
    classes = {c["class"] for c in r["diff_clusters"]}
    assert classes == {"GOLDEN_DATA", "NULL_SEMANTICS"}


def test_unknown_is_never_approvable_even_at_column_scope_with_a_matching_approval():
    # No real path in compare.py produces a column-scope UNKNOWN cluster today -- pinned directly
    # against `_approved` rather than through `compare()`, as the defense-in-depth case above says.
    cluster = {"class": "UNKNOWN", "scope": "columns", "columns": ["AMOUNT"]}
    approvals = [{"segment": "seg_01", "class": "UNKNOWN", "columns": ["AMOUNT"], "approver": "wf_owner",
                  "date": "2026-09-18"}]
    assert cmp._approved(cluster, "seg_01", approvals) is False


def test_ordering_can_still_be_accepted_with_an_approval():
    """ROUNDING already has a passing-approval regression (`test_rounding_can_be_accepted_with_an_
    approval`); this is ORDERING's, so F4's GOLDEN_DATA/UNKNOWN carve-out is proven not to have
    widened into blocking every class."""
    rows = copy.deepcopy(ROWS); rows[0][5], rows[1][5] = 2, 1
    appr = [{"segment": "seg_01", "class": "ORDERING", "columns": ["RID"], "approver": "wf_owner",
             "date": "2026-09-18"}]
    r = run(rows, accepted_classes=["ORDERING"], approvals=appr)
    assert r["diff_clusters"][0]["class"] == "ORDERING"
    assert r["verdict"] == "PASS_WITH_ACCEPTED_DIFF"


def test_cli_passes_and_writes_a_report(tmp_path):
    typed_csv.write_table(tmp_path / "exp.csv", {"fields": FIELDS, "rows": ROWS})
    (tmp_path / "c.json").write_text(json.dumps(CONTRACT))
    db = tmp_path / "s.duckdb"; b = DuckDBBackend(str(db)); b.load_table("MIG_WORK.ACT", {"fields": FIELDS, "rows": ROWS}); b.close()
    out = tmp_path / "v.json"
    args = [sys.executable, str(Path(cmp.__file__)), "--expected", str(tmp_path / "exp.csv"), "--actual", "MIG_WORK.ACT",
            "--contract", str(tmp_path / "c.json"), "--out", str(out), "--db", str(db), "--golden-set", "normal"]
    assert subprocess.run(args).returncode == 0
    report = json.loads(out.read_text())
    assert (report["verdict"], report["golden_set"], report["diff_clusters"]) == ("PASS", "normal", [])
    assert out.read_text(encoding="utf-8").endswith("}\n")
