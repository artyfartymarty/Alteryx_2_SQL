"""One failing case per structural invariant (program spec §6.3 plus the plan's 7 and 8).

Every case starts from the same hand-built minimal dag, breaks exactly one thing, and asserts
that `invariants.check` complains about that one thing.
"""
from __future__ import annotations

import copy

import pytest

import invariants

# A two-input join feeding an output: the smallest dag that exercises anchors, joins and types.
XML = """<AlteryxDocument yxmdVer="2023.1"><Nodes>
<Node ToolID="1"><GuiSettings Plugin="AlteryxBasePluginsGui.DbFileInput.DbFileInput"/></Node>
<Node ToolID="2"><GuiSettings Plugin="AlteryxBasePluginsGui.DbFileInput.DbFileInput"/></Node>
<Node ToolID="3"><GuiSettings Plugin="AlteryxBasePluginsGui.Join.Join"/></Node>
<Node ToolID="4"><GuiSettings Plugin="AlteryxBasePluginsGui.DbFileOutput.DbFileOutput"/></Node>
</Nodes></AlteryxDocument>"""


def node(tool_id, ntype, **extra):
    n = {"tool_id": tool_id, "type": ntype, "plugin": None, "container_id": None, "config": {},
         "raw_config": None, "annotation": None, "in_anchors": [], "out_anchors": [], "meta": {}}
    n.update(extra)
    return n


def edge(src, src_anchor, dst, dst_anchor, dst_order=1):
    return {"src": src, "src_anchor": src_anchor, "dst": dst, "dst_anchor": dst_anchor,
            "dst_order": dst_order, "wireless": False}


def dag():
    return {"workflow": "wf_0000", "yxmd_version": "2023.1", "engine": "E1", "constants": {},
            "file_kind": "yxmd", "source_file": "t.yxmd",
            "nodes": [node("1", "input", config={"source": "a.yxdb"}, out_anchors=["Output"]),
                      node("2", "input", config={"source": "b.yxdb"}, out_anchors=["Output"]),
                      node("3", "join", in_anchors=["Left", "Right"], out_anchors=["L", "J", "R"]),
                      node("4", "output", config={"source": "out.yxdb"}, in_anchors=["Input"])],
            "edges": [edge("1", "Output", "3", "Left"), edge("2", "Output", "3", "Right"),
                      edge("3", "J", "4", "Input")]}


def test_a_well_formed_dag_has_no_errors():
    assert invariants.check(XML, dag()) == []


def test_tool_ids_are_compared_as_strings():
    """The parser writes string ids; a dag whose ids came back as ints must still line up."""
    d = dag()
    for n in d["nodes"]:
        n["tool_id"] = int(n["tool_id"])
    for e in d["edges"]:
        e["src"], e["dst"] = int(e["src"]), int(e["dst"])
    assert invariants.check(XML, d) == []


def test_1_a_node_in_the_xml_must_be_in_the_dag():
    d = dag()
    d["nodes"] = [n for n in d["nodes"] if n["tool_id"] != "2"]
    d["edges"] = [e for e in d["edges"] if e["src"] != "2"]
    errs = invariants.check(XML, d)
    assert len(errs) == 2 and "nodes in XML but not in dag: ['2']" in errs[0]
    assert "join tool 3" in errs[1]  # losing the input also loses the Right inbound edge


def test_2_an_edge_must_resolve_to_a_known_node():
    d = dag()
    d["edges"].append(edge("3", "L", "99", "Input"))
    errs = invariants.check(XML, d)
    assert len(errs) == 1 and "references unknown node on dst" in errs[0]


def test_2_an_edge_must_use_an_anchor_the_tool_has():
    d = dag()
    d["edges"][2]["src_anchor"] = "Output"
    errs = invariants.check(XML, d)
    assert len(errs) == 1 and "uses unknown anchor Output on tool 3" in errs[0]


def test_3_every_node_has_a_type():
    d = dag()
    d["nodes"][2]["type"] = None
    errs = invariants.check(XML, d)
    assert any("tool 3 has no type" in e for e in errs)


def test_4_a_macro_node_has_a_path_or_is_flagged_unresolved():
    d = dag()
    d["nodes"][2] = node("3", "macro", in_anchors=["Input1"], out_anchors=["L", "J", "R"])
    errs = invariants.check(XML, d)
    assert len(errs) == 1 and "macro tool 3 has no path" in errs[0]
    d["nodes"][2]["unresolved"] = True
    assert invariants.check(XML, d) == []


def test_5_input_and_output_tools_keep_a_source():
    d = dag()
    d["nodes"][0]["config"] = {"source": None}
    assert invariants.check(XML, d) == ["input tool 1 has empty source/target"]
    d = dag()
    d["nodes"][3]["config"] = {}
    assert invariants.check(XML, d) == ["output tool 4 has empty source/target"]


def test_6_a_cycle_outside_a_macro_is_an_error():
    d = dag()
    d["nodes"].append(node("5", "formula", in_anchors=["Input"], out_anchors=["Output"]))
    d["nodes"].append(node("6", "formula", in_anchors=["Input"], out_anchors=["Output"]))
    d["edges"].append(edge("5", "Output", "6", "Input"))
    d["edges"].append(edge("6", "Output", "5", "Input"))
    errs = invariants.check(XML, d)
    assert "cycle detected outside a macro" in errs


def test_7_a_join_needs_exactly_one_left_and_one_right():
    d = dag()
    d["edges"] = [e for e in d["edges"] if e["dst_anchor"] != "Right"]
    errs = invariants.check(XML, d)
    assert len(errs) == 1 and "join tool 3 has 0 inbound edges on Right" in errs[0]
    d = dag()
    d["edges"].append(edge("2", "Output", "3", "Left"))
    assert "join tool 3 has 2 inbound edges on Left" in invariants.check(XML, d)[0]


def test_8_unexplained_unknown_tools_may_not_exceed_a_tenth_of_the_data_nodes():
    d = dag()
    d["nodes"][2] = node("3", "unknown", in_anchors=["Left", "Right"], out_anchors=["L", "J", "R"])
    errs = invariants.check(XML, d)
    assert len(errs) == 1 and "unknown" in errs[0] and "3" in errs[0]
    # an extension that explains the tool clears it
    d["nodes"][2]["behavior"] = "appears to join on ACCT"
    assert invariants.check(XML, d) == []


def test_8_ignores_nodes_that_carry_no_data():
    """container/comment/interface/action nodes are not data nodes, so they never dilute the share."""
    d = dag()
    d["nodes"][2] = node("3", "unknown", in_anchors=["Left", "Right"], out_anchors=["L", "J", "R"])
    d["nodes"][2]["behavior"] = "explained"
    d["nodes"] += [node(str(i), "container") for i in range(10, 20)]
    assert invariants.check(XML, d) == []


def test_a_dag_with_no_data_nodes_does_not_divide_by_zero():
    d = dag()
    d["nodes"] = [node("1", "container")]
    d["edges"] = []
    assert invariants.check('<Node ToolID="1">', d) == []
