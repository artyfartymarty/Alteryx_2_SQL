"""A keyless stream is classified, not just counted (plan task 7b).

Every `edge` golden set in this program carries byte-identical duplicate rows on purpose, so many
streams must declare `keys: []`. These tests pin what `compare.py` does with such a stream: the
surplus rows of each side are fetched, paired by nearest match, and then run through the same
classification the keyed path uses -- while the verdict rule stays exactly as strict as it was.
"""
import copy, json, random

import pytest

from lib.backend import DuckDBBackend
import compare as cmp

FIELDS = [{"name": "ORDER_ID", "type": "Int32", "size": 4, "scale": None},
          {"name": "CUSTOMER", "type": "V_String", "size": 20, "scale": None},
          {"name": "REGION", "type": "V_String", "size": 10, "scale": None},
          {"name": "QTY", "type": "Int32", "size": 4, "scale": None},
          {"name": "TOTAL_NET", "type": "Double", "size": 8, "scale": None}]
#: 103 appears twice, byte for byte, which is exactly why this stream can declare no keys.
ROWS = [[101, "ALEXANDER", "EMEA", 2, 100.0],
        [102, "BO", None, 1, 250.0],
        [103, "CHANDRA", "APAC", 3, 75.5],
        [103, "CHANDRA", "APAC", 3, 75.5],
        [104, "DANA", None, 4, 12.25]]
_TYPES = {"Double": "FLOAT", "Int32": "NUMBER(38,0)"}
CONTRACT = {"segment": "seg_01", "tolerances": {},
            "output": {"table": "MIG_WORK.ACT", "stream": "3_F", "kind": "work", "keys": [],
                       "columns": [{"name": f["name"], "type": _TYPES.get(f["type"], "VARCHAR"),
                                    "nullable": True} for f in FIELDS]}}
TOL = {"float_abs": 1e-6, "float_rel": 1e-9, "timestamp_precision": "milliseconds",
       "rounding": {"abs": 0.01}}
NAMES = [f["name"] for f in FIELDS]


def run_tables(expected, actual, contract=CONTRACT, fields=FIELDS, **kw):
    b = DuckDBBackend()
    b.load_table("MIG_COMPARE.EXP", {"fields": fields, "rows": expected})
    b.load_table("MIG_WORK.ACT", {"fields": fields, "rows": actual})
    return cmp.compare(b, "MIG_COMPARE.EXP", "MIG_WORK.ACT", contract, TOL, **kw)


def run(actual_rows, expected_rows=None, **kw):
    return run_tables(ROWS if expected_rows is None else expected_rows, actual_rows, **kw)


def edit(index, column, value, rows=None):
    rows = copy.deepcopy(ROWS if rows is None else rows)
    rows[index][NAMES.index(column)] = value
    return rows


def shapes(report):
    """(class, columns, count, scope) per cluster -- the shorthand these tests assert on."""
    return [(c["class"], c["columns"], c["count"], c["scope"]) for c in report["diff_clusters"]]


def approval(cluster_class, columns):
    return [{"segment": "seg_01", "class": cluster_class, "columns": columns,
             "approver": "wf_owner", "date": "2026-09-18"}]


def unknowns(report):
    return [c for c in report["diff_clusters"] if c["class"] == "UNKNOWN"]


# --- nothing to explain ------------------------------------------------------------------------


def test_identical_keyless_tables_with_duplicate_rows_pass():
    r = run(copy.deepcopy(ROWS))
    assert (r["verdict"], r["diff_clusters"], r["needs_human"]) == ("PASS", [], False)
    assert r["checks"]["set_diff"] == {"only_expected": 0, "only_actual": 0}
    assert r["normalizations_applied"] == []


# --- rows nothing could be paired with: row-presence clusters, as on the keyed path -------------


def test_a_duplicate_row_lost_in_actual_is_a_logic_row_cluster():
    # 103 is in expected twice and in actual once: one surplus copy, and nothing to pair it with.
    r = run([row for i, row in enumerate(copy.deepcopy(ROWS)) if i != 3])
    assert r["verdict"] == "FAIL" and r["needs_human"] is False
    assert r["checks"]["set_diff"] == {"only_expected": 1, "only_actual": 0}
    assert shapes(r) == [("LOGIC", [], 1, "rows")]
    assert r["diff_clusters"][0]["note"] == "rows only in expected"
    assert r["diff_clusters"][0]["example_rows"][0]["key"] == {}


def test_missing_null_region_rows_are_null_semantics_and_need_no_human():
    # The two NULL-REGION rows are dropped: REGION is NULL in every missing row and is not NULL
    # everywhere in the golden data, so it is the column that explains them.
    r = run([row for row in copy.deepcopy(ROWS) if row[2] is not None])
    assert shapes(r) == [("NULL_SEMANTICS", ["REGION"], 2, "rows")]
    assert r["diff_clusters"][0]["note"] == "rows only in expected"
    assert r["verdict"] == "FAIL" and r["needs_human"] is False
    # The whole-table aggregates moved with those rows; on a keyless stream the row cluster is
    # what accounts for them, so no synthetic UNKNOWN is invented.
    assert r["checks"]["aggregates"] == "FAIL" and unknowns(r) == []


def test_unrelated_rows_are_not_paired():
    # One row missing, one unrelated row added: they share too few columns to be a pair.
    actual = [row for row in copy.deepcopy(ROWS) if row[0] != 101] + [[201, "ZOE", "AMER", 9, 999.0]]
    r = run(actual)
    assert shapes(r) == [("LOGIC", [], 1, "rows"), ("LOGIC", [], 1, "rows")]
    assert [c["note"] for c in r["diff_clusters"]] == ["rows only in actual", "rows only in expected"]
    assert r["checks"]["column_mismatches"] == {} and r["needs_human"] is False


# --- rows that can be paired: the keyed path's own classification -------------------------------


def test_a_string_left_untruncated_is_a_truncation_cluster():
    r = run(edit(0, "CUSTOMER", "ALEXA"))
    assert shapes(r) == [("TRUNCATION", ["CUSTOMER"], 1, "columns")]
    c = r["diff_clusters"][0]
    assert c["paired_by"] == "nearest_match" and c["note"] == "no keys: rows paired by nearest match"
    assert c["example_rows"] == [{"key": {}, "expected": {"CUSTOMER": "ALEXANDER"},
                                  "actual": {"CUSTOMER": "ALEXA"}}]
    assert r["checks"]["column_mismatches"] == {"CUSTOMER": 1} and r["needs_human"] is False


def test_a_case_only_difference_keeps_its_hint():
    r = run(edit(0, "CUSTOMER", "Alexander"))
    assert shapes(r) == [("LOGIC", ["CUSTOMER"], 1, "columns")]
    assert r["diff_clusters"][0]["hint"] == "case_only"
    assert r["diff_clusters"][0]["paired_by"] == "nearest_match"


def test_a_money_column_half_a_cent_out_is_rounding_and_can_be_accepted():
    rows = edit(0, "TOTAL_NET", 100.005)
    rows = edit(1, "TOTAL_NET", 250.005, rows)
    rows = edit(4, "TOTAL_NET", 12.255, rows)
    r = run(rows)
    assert shapes(r) == [("ROUNDING", ["TOTAL_NET"], 3, "columns")]
    assert r["verdict"] == "FAIL" and r["checks"]["column_mismatches"] == {"TOTAL_NET": 3}
    assert r["checks"]["set_diff"] == {"only_expected": 3, "only_actual": 3}
    accepted = run(rows, accepted_classes=["ROUNDING"], approvals=approval("ROUNDING", ["TOTAL_NET"]))
    assert accepted["verdict"] == "PASS_WITH_ACCEPTED_DIFF" and accepted["needs_human"] is False
    assert run(rows, accepted_classes=["ROUNDING"])["verdict"] == "FAIL"   # nobody signed


def test_float_noise_inside_the_tolerance_is_a_match_and_is_reported():
    r = run(edit(0, "TOTAL_NET", 100.000000000001))
    assert (r["verdict"], r["diff_clusters"], r["needs_human"]) == ("PASS", [], False)
    assert r["checks"]["set_diff"] == {"only_expected": 0, "only_actual": 0}
    assert [entry for entry in r["normalizations_applied"] if "matched within tolerance" in entry]


def test_a_truncated_pair_and_a_missing_row_are_both_reported():
    rows = edit(0, "CUSTOMER", "ALEXA")
    del rows[3]                                     # and the second copy of 103 is simply gone
    r = run(rows)
    assert shapes(r) == [("TRUNCATION", ["CUSTOMER"], 1, "columns"), ("LOGIC", [], 1, "rows")]
    # No pair matched within tolerance, so set_diff is exactly the SQL multiset difference.
    assert r["checks"]["set_diff"] == {"only_expected": 2, "only_actual": 1}
    assert r["checks"]["counts"] == {"expected": 5, "actual": 4, "verdict": "FAIL"}
    assert r["needs_human"] is False and unknowns(r) == []


ORDER_FIELDS = [{"name": "RECORD_ID", "type": "Int32", "size": 4, "scale": None},
                {"name": "NAME", "type": "V_String", "size": 20, "scale": None},
                {"name": "AMOUNT", "type": "Double", "size": 8, "scale": None}]
ORDER_CONTRACT = {"segment": "seg_01", "tolerances": {},
                  "ordering": {"keys": [], "alteryx_deterministic": True,
                               "order_dependent_columns": ["RECORD_ID"]},
                  "output": {"table": "MIG_WORK.ACT", "stream": "3_F", "kind": "work", "keys": [],
                             "columns": [{"name": "RECORD_ID", "type": "NUMBER(38,0)", "nullable": True},
                                         {"name": "NAME", "type": "VARCHAR", "nullable": True},
                                         {"name": "AMOUNT", "type": "FLOAT", "nullable": True}]}}


def test_the_same_rows_numbered_in_another_order_are_an_ordering_cluster():
    expected = [[1, "A", 10.0], [2, "B", 20.0], [3, "C", 30.0]]
    actual = [[2, "A", 10.0], [3, "B", 20.0], [1, "C", 30.0]]
    r = run_tables(expected, actual, ORDER_CONTRACT, fields=ORDER_FIELDS)
    assert shapes(r) == [("ORDERING", ["RECORD_ID"], 3, "columns")]
    assert r["verdict"] == "FAIL" and r["needs_human"] is False
    accepted = run_tables(expected, actual, ORDER_CONTRACT, fields=ORDER_FIELDS,
                          accepted_classes=["ORDERING"], approvals=approval("ORDERING", ["RECORD_ID"]))
    assert accepted["verdict"] == "PASS_WITH_ACCEPTED_DIFF"


# --- the bound, and determinism ------------------------------------------------------------------


def test_more_surplus_rows_than_max_diff_rows_is_truncated_and_never_passes():
    actual = [row for i, row in enumerate(copy.deepcopy(ROWS)) if i not in (3, 4)]
    r = run(actual, max_diff_rows=1)
    assert r["truncated"] is True and r["verdict"] == "FAIL"
    # Even with every class accepted and signed off, a diff cut short cannot pass.
    signed = run(actual, max_diff_rows=1, accepted_classes=["LOGIC", "NULL_SEMANTICS", "UNKNOWN"],
                 approvals=approval("LOGIC", NAMES) + approval("NULL_SEMANTICS", NAMES)
                 + approval("UNKNOWN", NAMES))
    assert signed["truncated"] is True and signed["verdict"] == "FAIL"


def test_rows_past_the_pairing_budget_stay_unpaired(monkeypatch):
    # Pairing is quadratic, so it is bounded. The bound can only make a report stricter: the rows
    # it does not reach are reported as missing and extra, never quietly forgiven.
    monkeypatch.setattr(cmp, "_MAX_PAIRED_ROWS", 1)
    rows = edit(0, "TOTAL_NET", 100.005)
    rows = edit(2, "TOTAL_NET", 75.505, rows)
    r = run(rows, accepted_classes=["ROUNDING"], approvals=approval("ROUNDING", ["TOTAL_NET"]))
    assert shapes(r) == [("ROUNDING", ["TOTAL_NET"], 1, "columns"),
                         ("LOGIC", [], 1, "rows"), ("LOGIC", [], 1, "rows")]
    assert r["verdict"] == "FAIL" and r["truncated"] is False
    # Without the bound both rows pair, and then the approval really does cover everything.
    monkeypatch.undo()
    whole = run(rows, accepted_classes=["ROUNDING"], approvals=approval("ROUNDING", ["TOTAL_NET"]))
    assert shapes(whole) == [("ROUNDING", ["TOTAL_NET"], 2, "columns")]
    assert whole["verdict"] == "PASS_WITH_ACCEPTED_DIFF"


ONE_FIELD = [{"name": "TOTAL_NET", "type": "Double", "size": 8, "scale": None}]
ONE_CONTRACT = {"segment": "seg_01", "tolerances": {},
                "output": {"table": "MIG_WORK.ACT", "stream": "3_F", "kind": "work", "keys": [],
                           "columns": [{"name": "TOTAL_NET", "type": "FLOAT", "nullable": True}]}}


def test_a_one_column_table_is_never_paired():
    # With a single column "nearest" would mean nothing: any two rows are either the same row or
    # entirely different, so both stay unpaired and neither is classified as a value difference.
    r = run_tables([[100.0]], [[100.000000000001]], ONE_CONTRACT, fields=ONE_FIELD)
    assert shapes(r) == [("LOGIC", [], 1, "rows"), ("LOGIC", [], 1, "rows")]
    assert r["verdict"] == "FAIL" and r["checks"]["column_mismatches"] == {}


def _without_runtime(report):
    report.pop("runtime_ms")
    return json.dumps(report, sort_keys=True)


#: (name, expected, actual) for the determinism sweep: a value diff beside a missing row, a shape
#: full of NULLs and byte-identical duplicates, and one that is nothing but duplicates.
SHUFFLE_SHAPES = [
    ("value diff and a missing row", ROWS, edit(0, "CUSTOMER", "ALEXA")[:4]),
    ("nulls and duplicates", ROWS, [[101, "ALEXANDER", None, 2, 100.0], [102, "BO", None, 1, 250.0],
                                    [103, "CHANDRA", "APAC", 3, 75.5], [104, "DANA", None, 4, 12.25],
                                    [104, "DANA", None, 4, 12.25]]),
    ("only duplicates", [[101, "ALEXANDER", "EMEA", 2, 100.0]] * 5,
     [[101, "ALEXANDER", "EMEA", 2, 100.0]] * 3),
]


@pytest.mark.parametrize("name,expected,actual", SHUFFLE_SHAPES, ids=[s[0] for s in SHUFFLE_SHAPES])
def test_the_report_does_not_depend_on_how_the_rows_were_stored(name, expected, actual):
    # Every number below comes off rows a SQL ORDER BY put in order, so the physical order they
    # were written in may not reach the report -- not through the pairing, not through set_diff,
    # not through the example rows.
    base = _without_runtime(run_tables(expected, actual))
    assert base == _without_runtime(run_tables(expected, actual))          # twice on the same input
    for seed in range(6):
        shuffled_expected, shuffled_actual = copy.deepcopy(expected), copy.deepcopy(actual)
        random.Random(seed).shuffle(shuffled_expected)
        random.Random(seed + 100).shuffle(shuffled_actual)
        assert _without_runtime(run_tables(shuffled_expected, shuffled_actual)) == base, seed


# --- fix round 1: a cluster about rows can never be approved -------------------------------------

#: The NULL-REGION rows are still there, but three of this stream's five columns were rewritten in
#: them -- more than the pairing threshold allows -- so they are re-told as rows missing on one
#: side and rows added on the other, named by the column that is NULL in all of them.
REWRITTEN_EXPECTED = [[101, "ALEX", "EMEA", 2, 100.0], [201, "BO", None, 1, 10.0],
                      [202, "CHAN", None, 1, 20.0], [203, "DANA", None, 1, 30.0]]
REWRITTEN_ACTUAL = [[101, "ALEX", "EMEA", 2, 100.0], [201, "BOB", None, 7, 1000.0],
                    [202, "CHANG", None, 7, 2000.0], [203, "DANNY", None, 7, 3000.0]]


@pytest.mark.parametrize("columns", [["REGION"], []])
def test_an_approval_can_never_cover_a_row_presence_cluster(columns):
    # An approval record pins a class and some columns; nothing in it pins WHICH rows, or how
    # many. So it may never speak for rows that are simply gone or simply extra -- here, for a
    # value regression in three columns that the pairing threshold refused to pair.
    r = run_tables(REWRITTEN_EXPECTED, REWRITTEN_ACTUAL,
                   accepted_classes=["NULL_SEMANTICS"],
                   approvals=approval("NULL_SEMANTICS", columns))
    assert shapes(r) == [("NULL_SEMANTICS", ["REGION"], 3, "rows"),
                         ("NULL_SEMANTICS", ["REGION"], 3, "rows")]
    assert r["checks"]["aggregates"] == "FAIL"      # QTY and TOTAL_NET moved, and nothing names them
    assert r["verdict"] == "FAIL"


def test_an_approval_for_missing_rows_does_not_cover_them_coming_back_wrong():
    # The two-run story. Run 1: the NULL-REGION rows really are dropped, and a human signs
    # NULL_SEMANTICS / REGION for this segment. Run 2, later: the rows are back, with every value
    # in them wrong. The same signature must not carry over.
    signed = {"accepted_classes": ["NULL_SEMANTICS"],
              "approvals": approval("NULL_SEMANTICS", ["REGION"])}
    dropped = run_tables(REWRITTEN_EXPECTED, REWRITTEN_EXPECTED[:1], **signed)
    assert shapes(dropped) == [("NULL_SEMANTICS", ["REGION"], 3, "rows")]
    assert dropped["verdict"] == "FAIL"
    rewritten = run_tables(REWRITTEN_EXPECTED, REWRITTEN_ACTUAL, **signed)
    assert rewritten["verdict"] == "FAIL"


def test_keyed_rows_on_one_side_are_not_approvable_either():
    # The ruling is not keyless-only: the keyed path's row-presence and duplicate-key clusters
    # carry the same "which rows?" problem, and lose the same privilege.
    missing = run_tables(KEYED_ROWS, KEYED_ROWS[:-1], KEYED_CONTRACT,
                         accepted_classes=["NULL_SEMANTICS"],
                         approvals=approval("NULL_SEMANTICS", ["REGION"]))
    assert shapes(missing) == [("NULL_SEMANTICS", ["REGION"], 1, "rows")]
    assert missing["verdict"] == "FAIL"
    duplicated = run_tables(KEYED_ROWS, KEYED_ROWS + KEYED_ROWS[:1], KEYED_CONTRACT,
                            accepted_classes=["LOGIC"], approvals=approval("LOGIC", ["ORDER_ID"]))
    assert [(c["class"], c["scope"], c["note"]) for c in duplicated["diff_clusters"]] == [
        ("LOGIC", "rows", "duplicate keys in actual")]
    assert duplicated["verdict"] == "FAIL"


def test_the_pairing_budget_can_never_turn_a_fail_into_a_pass(monkeypatch):
    # Starving the budget re-tells a one-column value difference as rows missing and rows added.
    # That must not be the looser report: the row clusters it produces are unapprovable.
    expected = [[100 + i, f"CUST{i:03d}", "EMEA", 2, 10.0 + i] for i in range(6)]
    actual = [[row[0], row[1], row[2], 9, row[4]] for row in expected]
    signed = {"accepted_classes": ["LOGIC"], "approvals": approval("LOGIC", [])}
    paired = run_tables(expected, actual, **signed)
    assert shapes(paired) == [("LOGIC", ["QTY"], 6, "columns")] and paired["verdict"] == "FAIL"
    monkeypatch.setattr(cmp, "_MAX_PAIRED_ROWS", 0)
    starved = run_tables(expected, actual, **signed)
    assert shapes(starved) == [("LOGIC", [], 6, "rows"), ("LOGIC", [], 6, "rows")]
    assert starved["verdict"] == "FAIL"


def test_a_value_cluster_is_never_the_sole_witness_for_a_count_difference():
    # The pigeonhole invariant the keyless value cluster's `sides` rests on: a pair consumes one
    # surplus row from each side, so if the row counts differ at all, the heavier side keeps
    # unpaired rows and a row cluster is always there beside the value cluster -- and that row
    # cluster cannot be approved, so a count difference can never end in a PASS.
    rows = edit(0, "TOTAL_NET", 100.005)[:4]                # one value diff, and 104 is gone
    r = run(rows, accepted_classes=["ROUNDING"], approvals=approval("ROUNDING", ["TOTAL_NET"]))
    assert r["checks"]["counts"] == {"expected": 5, "actual": 4, "verdict": "FAIL"}
    assert shapes(r) == [("NULL_SEMANTICS", ["REGION"], 1, "rows"),
                         ("ROUNDING", ["TOTAL_NET"], 1, "columns")]
    assert r["verdict"] == "FAIL"


# --- fix round 1: a pair needs at least half of the columns equal ---------------------------------


FOUR_FIELDS = [{"name": "CUST_ID", "type": "Int32", "size": 4, "scale": None},
               {"name": "NAME", "type": "V_String", "size": 20, "scale": None},
               {"name": "CITY", "type": "V_String", "size": 20, "scale": None},
               {"name": "TIER", "type": "V_String", "size": 4, "scale": None}]
FOUR_CONTRACT = {"segment": "seg_01", "tolerances": {},
                 "output": {"table": "MIG_WORK.ACT", "stream": "2_Output", "kind": "work",
                            "keys": [],
                            "columns": [{"name": "CUST_ID", "type": "NUMBER(38,0)", "nullable": True},
                                        {"name": "NAME", "type": "VARCHAR", "nullable": True},
                                        {"name": "CITY", "type": "VARCHAR", "nullable": True},
                                        {"name": "TIER", "type": "VARCHAR", "nullable": True}]}}


def test_two_of_four_columns_rewritten_still_pairs():
    # Half the columns equal is enough. Under the old threshold these rows were re-told as three
    # missing and three added, which named no column at all and hid the change behind a cluster
    # shaped like "rows vanished".
    expected = [[1, "ALICE", "PARIS", "GOLD"], [2, "BRIAN", "TOKYO", "GOLD"],
                [3, "CAROL", "MILAN", "GOLD"]]
    actual = [[1, "ALICF", "PARIT", "GOLD"], [2, "BRIAO", "TOKYP", "GOLD"],
              [3, "CAROM", "MILAO", "GOLD"]]
    r = run_tables(expected, actual, FOUR_CONTRACT, fields=FOUR_FIELDS,
                   accepted_classes=["LOGIC"], approvals=approval("LOGIC", []))
    assert shapes(r) == [("LOGIC", ["CITY", "NAME"], 3, "columns")]
    assert r["diff_clusters"][0]["paired_by"] == "nearest_match"
    # The approval names no column, so it cannot cover a cluster that names two.
    assert r["verdict"] == "FAIL"
    assert run_tables(expected, actual, FOUR_CONTRACT, fields=FOUR_FIELDS,
                      accepted_classes=["LOGIC"],
                      approvals=approval("LOGIC", ["NAME", "CITY"]))["verdict"] \
        == "PASS_WITH_ACCEPTED_DIFF"


def test_more_than_half_the_columns_rewritten_does_not_pair():
    expected = [[1, "ALICE", "PARIS", "GOLD"]]
    actual = [[1, "ALICF", "PARIT", "GOLM"]]              # three of four columns differ
    r = run_tables(expected, actual, FOUR_CONTRACT, fields=FOUR_FIELDS)
    assert shapes(r) == [("LOGIC", [], 1, "rows"), ("LOGIC", [], 1, "rows")]


# F15 (coordinator ruling): a `test_an_accepted_pair_always_has_a_column_in_common` used to live
# here, but it only restated `differing <= n // 2 < n` as arithmetic on plain integers -- it never
# called into `compare.py` at all, so it could not have failed however `_pair_surplus` behaved.
# The two tests directly above already pin the real property behaviourally, against the actual
# pairing code: `test_two_of_four_columns_rewritten_still_pairs` shows a pair differing in exactly
# half its columns (2 of 4) IS accepted, and its cluster names only the differing two (`CITY`,
# `NAME`) -- proving the other two (`CUST_ID`, `TIER`) are the "column in common" the deleted test
# could only assert about numbers; `test_more_than_half_the_columns_rewritten_does_not_pair` shows
# crossing that threshold (3 of 4 differing) is refused and falls back to row-presence clusters
# instead. Removed rather than kept alongside them.


# --- rule 6 is keyless-only: the keyed path's accounting is untouched ----------------------------


KEYED_ROWS = [row for row in copy.deepcopy(ROWS) if row != [103, "CHANDRA", "APAC", 3, 75.5]]
KEYED_CONTRACT = copy.deepcopy(CONTRACT)
KEYED_CONTRACT["output"]["keys"] = ["ORDER_ID"]


def _aggregate_failure_on_qty(self):
    return "FAIL", {"QTY": {"sum": {"expected": 1, "actual": 2}}}


def test_a_keyed_row_cluster_still_cannot_account_for_a_moved_aggregate(monkeypatch):
    monkeypatch.setattr(cmp._Comparison, "_aggregate_differences", _aggregate_failure_on_qty)
    r = run_tables(KEYED_ROWS, KEYED_ROWS[:-1], KEYED_CONTRACT)
    assert r["verdict"] == "FAIL" and r["needs_human"] is True
    assert [(c["columns"], c["hint"]) for c in unknowns(r)] == [
        (["QTY"], "aggregates failed with no row-level difference")]


def test_a_keyless_row_cluster_accounts_for_the_aggregates_the_missing_rows_moved(monkeypatch):
    monkeypatch.setattr(cmp._Comparison, "_aggregate_differences", _aggregate_failure_on_qty)
    r = run_tables(KEYED_ROWS, KEYED_ROWS[:-1], CONTRACT)
    assert r["verdict"] == "FAIL" and r["needs_human"] is False
    assert unknowns(r) == [] and [c["scope"] for c in r["diff_clusters"]] == ["rows"]


def test_a_keyless_value_cluster_does_not_excuse_an_aggregate_on_another_column(monkeypatch):
    # Rule 6(b) is about rows that moved. With every surplus row paired there is no row cluster,
    # so an aggregate on a column no cluster names is still unexplained.
    monkeypatch.setattr(cmp._Comparison, "_aggregate_differences", _aggregate_failure_on_qty)
    r = run(edit(0, "CUSTOMER", "ALEXA"))
    assert r["verdict"] == "FAIL" and r["needs_human"] is True
    assert [(c["columns"], c["hint"]) for c in unknowns(r)] == [
        (["QTY"], "aggregates failed with no row-level difference")]


def test_a_keyless_stream_never_passes_on_a_difference_it_wrote_down():
    for name, report in _every_keyless_scenario():
        if report["verdict"] == "PASS":
            assert report["truncated"] is False, name
            for check, value in report["checks"].items():
                assert not cmp._check_failed(check, value), (name, check, value)
        if report["verdict"].startswith("PASS"):
            assert report["diff_clusters"] == [] or all(
                c["scope"] != "synthetic" for c in report["diff_clusters"]), name


def _every_keyless_scenario():
    yield "identical", run(copy.deepcopy(ROWS))
    yield "tolerance match", run(edit(0, "TOTAL_NET", 100.000000000001))
    yield "truncation", run(edit(0, "CUSTOMER", "ALEXA"))
    yield "case only", run(edit(0, "CUSTOMER", "Alexander"))
    yield "missing rows", run([row for row in copy.deepcopy(ROWS) if row[2] is not None])
    yield "unrelated rows", run([row for row in copy.deepcopy(ROWS) if row[0] != 101]
                                + [[201, "ZOE", "AMER", 9, 999.0]])
    yield "truncated", run(copy.deepcopy(ROWS)[:2], max_diff_rows=1)
    yield "approved rounding", run(edit(0, "TOTAL_NET", 100.005), accepted_classes=["ROUNDING"],
                                   approvals=approval("ROUNDING", ["TOTAL_NET"]))
    yield "empty", run_tables([], [])
