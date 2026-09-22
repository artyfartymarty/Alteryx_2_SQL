from decimal import Decimal
import pytest
from dev.alteryx_sim import simulate, UnsupportedTool

def F(name, type="V_String", size=50, scale=None): return {"name": name, "type": type, "size": size, "scale": scale}
def node(tid, type, config=None, **kw): return {"tool_id": tid, "type": type, "config": config or {}, "container_id": None, "meta": {}, **kw}
def edge(s, sa, d, da="Input", order=1): return {"src": s, "src_anchor": sa, "dst": d, "dst_anchor": da, "dst_order": order}
def dag(nodes, edges, constants=None): return {"workflow": "t", "nodes": nodes, "edges": edges, "constants": constants or {}}
def table(fields, rows): return {"fields": fields, "rows": rows}
def names(t): return [f["name"] for f in t["fields"]]

def test_filter_sends_null_to_false():
    d = dag([node("1", "input"), node("2", "filter", {"expression": '[R] != "WEST"'})], [edge("1", "Output", "2")])
    r = simulate(d, {"1": table([F("R")], [["EAST"], ["WEST"], [None]])})
    assert r.streams["2_T"]["rows"] == [["EAST"]] and r.streams["2_F"]["rows"] == [["WEST"], [None]]

def test_select_truncates_renames_and_keeps_unknown():
    cfg = {"fields": [{"name": "C", "selected": True, "rename": None, "type": "String", "size": 3},
                      {"name": "S", "selected": True, "rename": "STATUS", "type": None, "size": None},
                      {"name": "X", "selected": False, "rename": None, "type": None, "size": None}], "unknown_selected": True}
    d = dag([node("1", "input"), node("2", "select", cfg)], [edge("1", "Output", "2")])
    out = simulate(d, {"1": table([F("C"), F("S"), F("X"), F("Z")], [["abcdef", "ok", "drop", "z"]])}).streams["2_Output"]
    assert names(out) == ["C", "STATUS", "Z"] and out["rows"] == [["abc", "ok", "z"]] and out["fields"][0]["type"] == "String"

def test_formula_is_sequential():
    cfg = {"formulas": [{"field": "A", "expression": "[A] + 1", "type": "Int32", "size": 4},
                        {"field": "B", "expression": "[A] * 10", "type": "Int32", "size": 4}]}
    d = dag([node("1", "input"), node("2", "formula", cfg)], [edge("1", "Output", "2")])
    assert simulate(d, {"1": table([F("A", "Int32", 4)], [[1]])}).streams["2_Output"]["rows"] == [[2, 20]]

def test_join_outputs_and_right_prefix():
    cfg = {"keys": [{"left": "ID", "right": "ID"}], "select": {"fields": [], "unknown_selected": True}}
    d = dag([node("1", "input"), node("2", "input"), node("3", "join", cfg)], [edge("1", "Output", "3", "Left"), edge("2", "Output", "3", "Right")])
    r = simulate(d, {"1": table([F("ID", "Int32", 4), F("N")], [[1, "a"], [2, "b"], [None, "n"]]),
                     "2": table([F("ID", "Int32", 4), F("V")], [[1, "x"], [1, "y"], [3, "z"], [None, "q"]])})
    assert names(r.streams["3_J"]) == ["ID", "N", "Right_ID", "V"]
    assert r.streams["3_J"]["rows"] == [[1, "a", 1, "x"], [1, "a", 1, "y"]]
    assert r.streams["3_L"]["rows"] == [[2, "b"], [None, "n"]] and r.streams["3_R"]["rows"] == [[3, "z"], [None, "q"]]

def test_union_by_name_fills_missing_with_null():
    d = dag([node("1", "input"), node("2", "input"), node("3", "union", {"mode": "name"})],
            [edge("1", "Output", "3", order=1), edge("2", "Output", "3", order=2)])
    out = simulate(d, {"1": table([F("A"), F("B")], [["a", "b"]]), "2": table([F("B"), F("C")], [["b2", "c2"]])}).streams["3_Output"]
    assert names(out) == ["A", "B", "C"] and out["rows"] == [["a", "b", None], [None, "b2", "c2"]]

def test_summarize_counts_nulls_and_orders_groups():
    cfg = {"fields": [{"field": "G", "action": "GroupBy", "rename": "G", "separator": None}, {"field": "V", "action": "Sum", "rename": "S", "separator": None},
                      {"field": "V", "action": "Count", "rename": "N", "separator": None}, {"field": "V", "action": "CountNonNull", "rename": "NN", "separator": None},
                      {"field": "T", "action": "Last", "rename": "LAST_T", "separator": None}, {"field": "T", "action": "Concat", "rename": "CAT", "separator": "|"}]}
    d = dag([node("1", "input"), node("2", "summarize", cfg)], [edge("1", "Output", "2")])
    rows = [["b", 1.0, "x"], ["a", None, "y"], ["b", 2.5, None], [None, 4.0, "z"]]
    out = simulate(d, {"1": table([F("G"), F("V", "Double", 8), F("T")], rows)}).streams["2_Output"]
    assert out["rows"] == [[None, 4.0, 1, 1, "z", "z"], ["a", None, 1, 0, "y", "y"], ["b", 3.5, 2, 2, None, "x"]]

def test_sort_unique_multirow_recordid_chain():
    nodes = [node("1", "input"), node("2", "sort", {"fields": [{"field": "ACCT", "order": "asc"}, {"field": "DAY", "order": "asc"}, {"field": "ENTRY", "order": "desc"}]}),
             node("3", "unique", {"fields": ["ACCT", "DAY"]}),
             node("4", "multi_row_formula", {"field": "RUN", "update_existing": False, "type": "Double", "size": 8, "num_rows": 1,
                                             "expression": "[Row-1:RUN] + [AMT]", "group_by": ["ACCT"], "unknown_rows": "zero"}),
             node("5", "record_id", {"field": "RecordID", "start": 1, "type": "Int32", "position": "first"})]
    edges = [edge("1", "Output", "2"), edge("2", "Output", "3"), edge("3", "U", "4"), edge("4", "Output", "5")]
    rows = [["B", "d1", 1, 5.0], ["A", "d1", 1, 10.0], ["A", "d1", 2, 11.0], ["A", "d2", 3, 1.5]]
    r = simulate(dag(nodes, edges), {"1": table([F("ACCT"), F("DAY"), F("ENTRY", "Int32", 4), F("AMT", "Double", 8)], rows)})
    assert r.streams["5_Output"]["rows"] == [[1, "A", "d1", 2, 11.0, 11.0], [2, "A", "d2", 3, 1.5, 12.5], [3, "B", "d1", 1, 5.0, 5.0]]
    assert r.streams["3_D"]["rows"] == [["A", "d1", 1, 10.0]]

def test_cross_tab_frozen_headers_and_transpose():
    ct = node("2", "cross_tab", {"group_by": ["SKU"], "header_field": "WH", "data_field": "QTY", "methods": ["Sum"]},
              meta={"Output": [F("SKU"), F("EAST", "Double", 8), F("NORTH", "Double", 8), F("WEST", "Double", 8)]})
    tp = node("3", "transpose", {"key_fields": ["SKU"], "data_fields": ["EAST", "NORTH", "WEST"]})
    d = dag([node("1", "input"), ct, tp], [edge("1", "Output", "2"), edge("2", "Output", "3")])
    r = simulate(d, {"1": table([F("SKU"), F("WH"), F("QTY", "Int32", 4)], [["B-2", "WEST", 1], ["A-1", "EAST", 2], ["A-1", "EAST", 3], ["A-1", "WEST", 4]])})
    assert r.streams["2_Output"]["rows"] == [["A-1", 5.0, None, 4.0], ["B-2", None, None, 1.0]]
    assert r.streams["3_Output"]["rows"][:3] == [["A-1", "EAST", 5.0], ["A-1", "NORTH", None], ["A-1", "WEST", 4.0]]

def test_regex_parse_and_datetime_and_cleansing():
    rx = node("2", "regex", {"field": "SKU", "expression": "^([A-Z]+)-(\\d+)$", "case_insensitive": False, "method": "parse", "replace": None,
                             "copy_unmatched": False, "output_fields": [F("FAMILY", size=20), F("ITEM_NO", size=10)], "match_field": None})
    dt = node("3", "datetime", {"direction": "to_datetime", "field": "P", "format": "%d/%m/%Y", "out_field": "P_DT"})
    dc = node("4", "data_cleansing", {"fields": ["N"], "replace_null_strings_blank": True, "replace_null_numeric_zero": False, "trim_whitespace": True,
                                      "remove_tabs_linebreaks_dupspaces": False, "remove_all_whitespace": False, "remove_letters": False,
                                      "remove_numbers": False, "remove_punctuation": False, "modify_case": "upper"})
    d = dag([node("1", "input"), rx, dt, dc], [edge("1", "Output", "2"), edge("2", "Output", "3"), edge("3", "Output", "4")])
    out = simulate(d, {"1": table([F("SKU"), F("P"), F("N")], [["AB-12", "29/02/2024", "  zoë "], ["bad", "31/02/2026", None]])}).streams["4_Output"]
    assert names(out) == ["SKU", "P", "N", "FAMILY", "ITEM_NO", "P_DT"]
    assert out["rows"] == [["AB-12", "29/02/2024", "ZOË", "AB", "12", "2024-02-29 00:00:00"], ["bad", "31/02/2026", "", None, None, None]]

def test_macro_substitutes_question_values():
    sub = dag([node("1", "macro_input"), node("4", "filter", {"expression": "[QTY] >= [%Question.MinQty%]"}), node("5", "macro_output")],
              [edge("1", "Output", "4"), edge("4", "T", "5")])
    m = node("2", "macro", {"values": {"MinQty": "2"}}, sub_dag=sub, interface=[{"name": "MinQty", "type": "NumericUpDown", "default": "0"}], macro_path="m.yxmc")
    d = dag([node("1", "input"), m], [edge("1", "Output", "2", "Input1")])
    assert simulate(d, {"1": table([F("QTY", "Int32", 4)], [[1], [2], [None]])}).streams["2_Output5"]["rows"] == [[2]]

def test_update_insert_with_pre_and_post_sql():
    out = node("2", "output", {"source": "<scrubbed:fin>", "format": "db", "alias": "fin", "table": "dbo.GL_SUMMARY", "write_mode": "update_insert",
                               "keys": ["ACCT"], "pre_sql": "DELETE FROM dbo.GL_SUMMARY WHERE ACCT = 'OLD'",
                               "post_sql": "UPDATE dbo.GL_SUMMARY SET LOADED_FLAG = 'Y' WHERE LOADED_FLAG IS NULL"})
    d = dag([node("1", "input"), out], [edge("1", "Output", "2")])
    before = table([F("ACCT"), F("TOTAL", "FixedDecimal", 19, 2), F("LOADED_FLAG", size=1)],
                   [["A", Decimal("1.00"), "Y"], ["OLD", Decimal("9.00"), "Y"], ["K", Decimal("5.00"), "N"]])
    r = simulate(d, {"1": table([F("ACCT"), F("TOTAL", "FixedDecimal", 19, 2)], [["A", Decimal("2.50")], ["NEW", Decimal("7.00")]])},
                 targets_before={"GL_SUMMARY": before}, logical_by_tool={"2": "GL_SUMMARY"})
    assert r.outputs["2"]["rows"] == [["A", Decimal("2.50"), "Y"], ["K", Decimal("5.00"), "N"], ["NEW", Decimal("7.00"), "Y"]]

def test_unknown_tool_is_refused():
    d = dag([node("1", "input"), node("2", "unknown")], [edge("1", "Output", "2")])
    with pytest.raises(UnsupportedTool, match="2"): simulate(d, {"1": table([F("A")], [])})


# --- behaviour the dag contract and the brief state in prose but do not test ---

def test_input_takes_its_field_list_from_meta_output():
    n = node("1", "input", meta={"Output": [F("A", "Int32", 4), F("B", "Double", 8)]})
    d = dag([n, node("2", "block_until_done")], [edge("1", "Output", "2")])
    r = simulate(d, {"1": table([F("A"), F("B")], [["7", "1.5"]])})
    assert r.streams["1_Output"]["fields"] == [F("A", "Int32", 4), F("B", "Double", 8)]
    assert r.streams["1_Output"]["rows"] == [[7, 1.5]]
    # block_until_done is a pass-through on every one of its anchors.
    assert r.streams["2_Output1"]["rows"] == [[7, 1.5]] and r.streams["2_Output3"]["rows"] == [[7, 1.5]]

def test_sort_null_placement_and_stability():
    cfg = {"fields": [{"field": "A", "order": "asc"}]}
    d = dag([node("1", "input"), node("2", "sort", cfg)], [edge("1", "Output", "2")])
    rows = [["b", 1], [None, 2], ["a", 3], [None, 4], ["a", 5]]
    src = table([F("A"), F("N", "Int32", 4)], rows)
    assert simulate(d, {"1": src}).streams["2_Output"]["rows"] == [[None, 2], [None, 4], ["a", 3], ["a", 5], ["b", 1]]
    cfg["fields"][0]["order"] = "desc"
    assert simulate(d, {"1": src}).streams["2_Output"]["rows"] == [["b", 1], ["a", 3], ["a", 5], [None, 2], [None, 4]]

def test_sample_modes_keep_incoming_order():
    src = table([F("G"), F("N", "Int32", 4)], [["a", 1], ["b", 2], ["a", 3], ["a", 4], ["b", 5]])
    def sampled(cfg):
        d = dag([node("1", "input"), node("2", "sample", cfg)], [edge("1", "Output", "2")])
        return simulate(d, {"1": src}).streams["2_Output"]["rows"]
    assert sampled({"mode": "first", "n": 2, "group_by": ["G"]}) == [["a", 1], ["b", 2], ["a", 3], ["b", 5]]
    assert sampled({"mode": "last", "n": 1, "group_by": ["G"]}) == [["a", 4], ["b", 5]]
    assert sampled({"mode": "skip", "n": 1, "group_by": ["G"]}) == [["a", 3], ["a", 4], ["b", 5]]
    assert sampled({"mode": "one_in_n", "n": 2, "group_by": []}) == [["a", 1], ["a", 3], ["b", 5]]

def test_record_id_last_position_and_union_by_position():
    d = dag([node("1", "input"), node("2", "input"), node("3", "union", {"mode": "position"}),
             node("4", "record_id", {"field": "RID", "start": 10, "type": "Int32", "position": "last"})],
            [edge("1", "Output", "3", order=1), edge("2", "Output", "3", order=2), edge("3", "Output", "4")])
    out = simulate(d, {"1": table([F("A"), F("B")], [["a", "b"]]), "2": table([F("X"), F("Y")], [["x", "y"]])}).streams["4_Output"]
    assert names(out) == ["A", "B", "RID"] and out["rows"] == [["a", "b", 10], ["x", "y", 11]]

def test_regex_replace_and_match_methods():
    rep = node("2", "regex", {"field": "S", "expression": "^([a-z]+)(\\d+)$", "case_insensitive": True, "method": "replace",
                              "replace": "$2-$1", "copy_unmatched": True, "output_fields": [], "match_field": None})
    keep = node("3", "regex", {"field": "S", "expression": "^\\d", "case_insensitive": False, "method": "match", "replace": None,
                               "copy_unmatched": False, "output_fields": [], "match_field": "HIT"})
    d = dag([node("1", "input"), rep, keep], [edge("1", "Output", "2"), edge("2", "Output", "3")])
    out = simulate(d, {"1": table([F("S")], [["AB12"], ["nope!"]])}).streams["3_Output"]
    assert names(out) == ["S", "HIT"] and out["rows"] == [["12-AB", True], ["nope!", False]]

def test_regex_replace_without_copy_unmatched_nulls_the_field():
    rep = node("2", "regex", {"field": "S", "expression": "^(\\d+)$", "case_insensitive": False, "method": "replace",
                              "replace": "n$1", "copy_unmatched": False, "output_fields": [], "match_field": None})
    d = dag([node("1", "input"), rep], [edge("1", "Output", "2")])
    assert simulate(d, {"1": table([F("S")], [["12"], ["x"]])}).streams["2_Output"]["rows"] == [["n12"], [None]]

def test_data_cleansing_options():
    cfg = {"fields": ["S", "N"], "replace_null_strings_blank": False, "replace_null_numeric_zero": True, "trim_whitespace": True,
           "remove_tabs_linebreaks_dupspaces": True, "remove_all_whitespace": False, "remove_letters": False,
           "remove_numbers": True, "remove_punctuation": True, "modify_case": "title"}
    d = dag([node("1", "input"), node("2", "data_cleansing", cfg)], [edge("1", "Output", "2")])
    src = table([F("S"), F("N", "Int32", 4)], [["  a\tb1,  c  ", None], [None, 3]])
    assert simulate(d, {"1": src}).streams["2_Output"]["rows"] == [["A B C", 0], [None, 3]]

def test_summarize_other_actions():
    cfg = {"fields": [{"field": "G", "action": "GroupBy", "rename": "G", "separator": None},
                      {"field": "V", "action": "Min", "rename": "MN", "separator": None},
                      {"field": "V", "action": "Max", "rename": "MX", "separator": None},
                      {"field": "V", "action": "Avg", "rename": "AV", "separator": None},
                      {"field": "V", "action": "CountDistinct", "rename": "CD", "separator": None},
                      {"field": "V", "action": "First", "rename": "F1", "separator": None},
                      {"field": "T", "action": "Concat", "rename": "CAT", "separator": "|"}]}
    d = dag([node("1", "input"), node("2", "summarize", cfg)], [edge("1", "Output", "2")])
    rows = [["a", 3.0, "p"], ["a", 1.0, None], ["a", 3.0, "q"]]
    out = simulate(d, {"1": table([F("G"), F("V", "Double", 8), F("T")], rows)}).streams["2_Output"]
    assert out["rows"] == [["a", 1.0, 3.0, pytest.approx(2.3333333333333335), 2, 3.0, "p|q"]]
    assert [f["type"] for f in out["fields"]] == ["V_String", "Double", "Double", "Double", "Int64", "Double", "V_String"]

def test_summarize_sum_keeps_fixed_decimal():
    cfg = {"fields": [{"field": "G", "action": "GroupBy", "rename": "G", "separator": None},
                      {"field": "V", "action": "Sum", "rename": "S", "separator": None}]}
    d = dag([node("1", "input"), node("2", "summarize", cfg)], [edge("1", "Output", "2")])
    src = table([F("G"), F("V", "FixedDecimal", 19, 2)], [["a", Decimal("0.10")], ["a", Decimal("0.20")]])
    out = simulate(d, {"1": src}).streams["2_Output"]
    assert out["rows"] == [["a", Decimal("0.30")]] and out["fields"][1]["type"] == "FixedDecimal"

def test_append_write_mode_adds_to_target_state():
    out = node("2", "output", {"source": "<scrubbed:dw>", "format": "db", "alias": "dw", "table": "dbo.FACT", "write_mode": "append",
                               "keys": [], "pre_sql": None, "post_sql": None})
    d = dag([node("1", "input"), out], [edge("1", "Output", "2")])
    before = table([F("K"), F("V", "Int32", 4)], [["old", 1]])
    r = simulate(d, {"1": table([F("K"), F("V", "Int32", 4)], [["new", 2]])},
                 targets_before={"FACT": before}, logical_by_tool={"2": "FACT"})
    assert r.outputs["2"]["rows"] == [["new", 2], ["old", 1]]

def test_file_output_is_the_incoming_table():
    out = node("2", "output", {"source": "C:\\out\\x.yxdb", "format": "yxdb", "alias": None, "table": None,
                               "write_mode": "overwrite", "keys": [], "pre_sql": None, "post_sql": None})
    d = dag([node("1", "input"), out], [edge("1", "Output", "2")])
    r = simulate(d, {"1": table([F("A")], [["a"]])})
    assert r.outputs["2"]["rows"] == [["a"]] and "2_Output" not in r.streams

def test_run_command_and_unresolved_macro_are_refused():
    d = dag([node("1", "input"), node("2", "run_command", {"command": "x.bat", "args": None})], [edge("1", "Output", "2")])
    with pytest.raises(UnsupportedTool, match="2"): simulate(d, {"1": table([F("A")], [])})
    m = node("3", "macro", {"values": {}}, sub_dag=None, unresolved=True, interface=[], macro_path="gone.yxmc")
    d2 = dag([node("1", "input"), m], [edge("1", "Output", "3", "Input1")])
    with pytest.raises(UnsupportedTool, match="3"): simulate(d2, {"1": table([F("A")], [])})


# --- run(): golden files for a workflow laid out per contracts C2 and C7 ---

def _write_workflow(tmp_path):
    from lib.io import write_json
    from lib.typed_csv import write_table
    nodes = [node("1", "input", {"source": "in.yxdb", "format": "yxdb"}),
             node("2", "filter", {"expression": "[N] > 1"}),
             node("3", "formula", {"formulas": [{"field": "D", "expression": "[N] * 2", "type": "Int32", "size": 4}]}),
             node("4", "output", {"source": "out.yxdb", "format": "yxdb", "alias": None, "table": None,
                                  "write_mode": "overwrite", "keys": [], "pre_sql": None, "post_sql": None})]
    edges = [edge("1", "Output", "2"), edge("2", "T", "3"), edge("3", "Output", "4")]
    wf = tmp_path / "workflows" / "wf_0001"
    write_json(wf / "parsed" / "dag.json", dag(nodes, edges))
    write_json(wf / "segments" / "seg_01" / "dag.json",
               {"workflow": "wf_0001", "segment": "seg_01", "nodes": nodes[:2], "edges": [edges[0]],
                "inbound": [], "outbound": [{**edges[1], "to_segment": "seg_02"}]})
    write_json(wf / "segments" / "seg_02" / "dag.json",
               {"workflow": "wf_0001", "segment": "seg_02", "nodes": nodes[2:], "edges": [edges[2]],
                "inbound": [{**edges[1], "from_segment": "seg_01"}],
                "outbound": [{**edges[2], "to_segment": None}]})
    for name, rows in (("normal", [[1], [2], [3]]), ("empty", [])):
        write_table(wf / "golden" / "inputs" / name / "1.csv", table([F("N", "Int32", 4)], rows))
    return wf

def test_run_writes_intermediates_outputs_and_manifest(tmp_path):
    from lib.io import read_json
    from lib.paths import Repo
    from lib.typed_csv import read_table
    from dev.alteryx_sim import run
    wf = _write_workflow(tmp_path)
    assert run(Repo(tmp_path), "wf_0001", ["normal", "empty", "edge"], {"1": "IN", "4": "OUT"}) == ["normal", "empty"]
    mid = read_table(wf / "golden" / "intermediates" / "seg_01" / "normal" / "2_T.csv")
    assert mid["rows"] == [[2], [3]]
    out = read_table(wf / "golden" / "outputs" / "normal" / "4.csv")
    assert names(out) == ["N", "D"] and out["rows"] == [[2, 4], [3, 6]]
    # seg_02's only outbound stream feeds an Output tool inside seg_02, so it has no intermediate.
    assert not (wf / "golden" / "intermediates" / "seg_02").exists()
    assert read_table(wf / "golden" / "outputs" / "empty" / "4.csv")["rows"] == []
    assert read_json(wf / "manifest.json")["golden_sets"] == ["normal", "empty"]

def test_run_writes_nothing_when_a_tool_is_unsupported(tmp_path):
    from lib.io import read_json
    from lib.paths import Repo
    from dev.alteryx_sim import run
    wf = _write_workflow(tmp_path)
    d = read_json(wf / "parsed" / "dag.json")
    d["nodes"][1] = node("2", "run_command", {"command": "x.bat", "args": None})
    from lib.io import write_json
    write_json(wf / "parsed" / "dag.json", d)
    assert run(Repo(tmp_path), "wf_0001", ["normal"], {}) == []
    assert not (wf / "golden" / "outputs").exists() and not (wf / "golden" / "intermediates").exists()
    assert read_json(wf / "manifest.json")["golden_sets"] == []
