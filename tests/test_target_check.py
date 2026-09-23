"""target_check.py: deterministic target proposal per segment and workflow output kind."""
from __future__ import annotations

import pytest

from lib.io import read_json, write_json, write_yaml
from lib.paths import Repo
from parsers import plugin_map
import target_check as tc

WF = "wf_0009"


def _dag(nodes):
    return {"workflow": WF, "nodes": [{"tool_id": i, "type": t, "config": c} for i, t, c in nodes], "edges": []}


def _seg(repo, seg, nodes):
    write_json(repo.seg(WF, seg, "dag.json"), {"workflow": WF, "segment": seg,
               "nodes": [{"tool_id": i, "type": t, "config": c} for i, t, c in nodes], "edges": [], "inbound": [], "outbound": []})


def build(tmp_path, *, nodes, segments, outputs):
    repo = Repo(tmp_path)
    write_json(repo.wf(WF, "parsed", "dag.json"), _dag(nodes))
    write_json(repo.wf(WF, "segments", "order.json"), [[s] for s in segments])
    for seg in segments:
        _seg(repo, seg, [n for n in nodes if n[0] in segments[seg]])
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), {"sources": {}, "outputs": outputs})
    return repo


PLAIN = [("1", "input", {}), ("2", "filter", {}), ("5", "output", {"pre_sql": None, "post_sql": None})]
OUT_OVERWRITE = {"out/a.yxdb": {"snowflake": "A.B.C", "logical": "C", "mode": "overwrite", "keys": [], "tool_ids": ["5"]}}


def test_node_class_table():
    assert plugin_map.node_class("python") == "snowpark"
    assert plugin_map.node_class("run_command") == "manual"
    assert plugin_map.node_class("filter") == "sql"
    assert plugin_map.node_class("unknown") == "unknown"
    assert plugin_map.node_class("wat") == "unknown"


def test_all_sql_procedures_preference(tmp_path):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    result = tc.target_check(repo, WF, "procedures")
    assert result["segments"] == {"seg_01": "sql"}
    assert result["output_kind"] == "procedures"
    assert result["reason"] == "preference procedures"
    assert result["dbt_blockers"] == []
    assert read_json(repo.wf(WF, "segments", "targets.json")) == result


def test_python_node_makes_its_segment_snowpark_and_blocks_dbt(tmp_path):
    nodes = PLAIN + [("3", "python", {"script": "pass"})]
    repo = build(tmp_path, nodes=nodes, segments={"seg_01": {"1", "2"}, "seg_02": {"3"}, "seg_03": {"5"}}, outputs=OUT_OVERWRITE)
    result = tc.target_check(repo, WF, "dbt")
    assert result["segments"] == {"seg_01": "sql", "seg_02": "snowpark", "seg_03": "sql"}
    assert result["nodes"] == {"3": "snowpark"}
    assert result["output_kind"] == "procedures"
    assert result["dbt_blockers"] == [{"segment": "seg_02", "kind": "snowpark_segment"}]
    assert result["reason"].startswith("dbt refused: ")


def test_dbt_granted_when_preferred_and_feasible(tmp_path):
    outputs = {**OUT_OVERWRITE, "out/b.yxdb": {"snowflake": "A.B.D", "logical": "D", "mode": "merge", "keys": ["ID"], "tool_ids": ["5"]}}
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=outputs)
    result = tc.target_check(repo, WF, "dbt")
    assert result["output_kind"] == "dbt"
    assert result["reason"] == "preference dbt; every segment is sql and every output is dbt-expressible"


def test_every_dbt_blocker(tmp_path):
    cases = [
        ({"out/a.yxdb": {"snowflake": "A.B.C", "logical": "C", "mode": "merge", "keys": [], "tool_ids": ["5"]}}, PLAIN, "merge_without_keys"),
        ({"out/a.yxdb": {"snowflake": "A.B.C", "logical": "C", "mode": "update_only", "keys": [], "tool_ids": ["5"]}}, PLAIN, "write_mode_unsupported"),
        (OUT_OVERWRITE, [("1", "input", {}), ("5", "output", {"pre_sql": "DELETE FROM X; DELETE FROM Y", "post_sql": None})], "presql_not_plain"),
        (OUT_OVERWRITE, [("1", "input", {}), ("5", "output", {"pre_sql": None, "post_sql": "CREATE TABLE Z AS SELECT 1"})], "postsql_not_plain"),
        (OUT_OVERWRITE, PLAIN + [("9", "run_command", {})], "manual_node"),
        (OUT_OVERWRITE, PLAIN + [("9", "unknown", {})], "unknown_node"),
        ({}, PLAIN, "no_outputs"),
    ]
    for outputs, nodes, kind in cases:
        repo = build(tmp_path / kind, nodes=nodes, segments={"seg_01": {n[0] for n in nodes}}, outputs=outputs)
        result = tc.target_check(repo, WF, "dbt")
        assert result["output_kind"] == "procedures", kind
        assert kind in [b["kind"] for b in result["dbt_blockers"]], kind


def test_cli_exit_codes(tmp_path, capsys):
    repo = build(tmp_path, nodes=PLAIN + [("9", "unknown", {})], segments={"seg_01": {"1", "2", "5", "9"}}, outputs=OUT_OVERWRITE)
    assert tc.main([WF, "--root", str(tmp_path)]) == 1, "unknown nodes: written, exit 1"
    assert read_json(repo.wf(WF, "segments", "targets.json"))["nodes"]["9"] == "unknown"
    assert tc.main(["wf_0404", "--root", str(tmp_path)]) == 2
    assert "wf_0404" in capsys.readouterr().err


# --- coordinator ruling: --prefer auto (default) resolves manifest.json, then mappings/global.yaml
# program.output_target, then falls back to "procedures". Explicit --prefer procedures|dbt still
# overrides both. PLAIN + OUT_OVERWRITE alone has zero dbt_blockers (single overwrite output, no
# manual/unknown nodes, no pre/post sql), so whichever way "auto" resolves, output_kind tracks it
# directly and isn't itself in question here -- only which preference gets picked.

def test_prefer_auto_resolves_manifest_over_global(tmp_path):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    write_json(repo.wf(WF, "manifest.json"), {"id": WF, "output_target": "dbt"})
    write_yaml(repo.global_mappings, {"program": {"output_target": "procedures"}})

    result = tc.target_check(repo, WF, "auto")

    assert result["preference"] == "dbt"
    assert result["output_kind"] == "dbt"
    assert result["reason"] == "preference dbt; every segment is sql and every output is dbt-expressible"


def test_prefer_auto_resolves_global_when_manifest_has_no_output_target(tmp_path):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    write_json(repo.wf(WF, "manifest.json"), {"id": WF})  # manifest exists but carries no key
    write_yaml(repo.global_mappings, {"program": {"output_target": "dbt"}})

    result = tc.target_check(repo, WF, "auto")

    assert result["preference"] == "dbt"
    assert result["output_kind"] == "dbt"


def test_prefer_auto_defaults_to_procedures_when_absent_everywhere(tmp_path):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    # no manifest.json, no mappings/global.yaml at all under this tmp_path root

    result = tc.target_check(repo, WF, "auto")

    assert result["preference"] == "procedures"
    assert result["output_kind"] == "procedures"
    assert result["reason"] == "preference procedures"


def test_cli_prefer_defaults_to_auto(tmp_path):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    write_json(repo.wf(WF, "manifest.json"), {"id": WF, "output_target": "dbt"})

    assert tc.main([WF, "--root", str(tmp_path)]) == 0

    assert read_json(repo.wf(WF, "segments", "targets.json"))["preference"] == "dbt"


def test_cli_explicit_prefer_overrides_auto_resolution(tmp_path):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    write_json(repo.wf(WF, "manifest.json"), {"id": WF, "output_target": "dbt"})

    assert tc.main([WF, "--prefer", "procedures", "--root", str(tmp_path)]) == 0

    assert read_json(repo.wf(WF, "segments", "targets.json"))["preference"] == "procedures"


# --- Fix round 1 (coordinator ruling): a resolved `auto` value outside {"procedures", "dbt"} is a
# USAGE error -- it must never reach targets.json, where it would sit next to a reason that
# silently assumed "procedures" (spec §1: the decision is "always recorded with its reason").

def test_prefer_auto_manifest_invalid_value_raises_naming_manifest_and_value(tmp_path):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    write_json(repo.wf(WF, "manifest.json"), {"id": WF, "output_target": "DBT"})

    with pytest.raises(ValueError, match="manifest.json") as exc:
        tc.target_check(repo, WF, "auto")

    assert "DBT" in str(exc.value)
    assert not repo.wf(WF, "segments", "targets.json").is_file()


def test_prefer_auto_global_invalid_value_raises_naming_global_and_value(tmp_path):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    write_yaml(repo.global_mappings, {"program": {"output_target": "Dbt"}})

    with pytest.raises(ValueError, match="global.yaml") as exc:
        tc.target_check(repo, WF, "auto")

    assert "Dbt" in str(exc.value)
    assert not repo.wf(WF, "segments", "targets.json").is_file()


def test_cli_prefer_auto_invalid_manifest_value_exits_2_and_writes_nothing(tmp_path, capsys):
    repo = build(tmp_path, nodes=PLAIN, segments={"seg_01": {"1", "2", "5"}}, outputs=OUT_OVERWRITE)
    write_json(repo.wf(WF, "manifest.json"), {"id": WF, "output_target": "DBT"})

    rc = tc.main([WF, "--root", str(tmp_path)])

    assert rc == 2
    err = capsys.readouterr().err
    assert "DBT" in err
    assert not repo.wf(WF, "segments", "targets.json").is_file()


# --- final fix wave F4 (review I3): classification descends into a macro's sub_dag -------------
# `plugin_map` maps the type `macro` to `sql`, and nothing used to look inside one. A workflow
# whose single macro held a Python tool, a Run Command and an unknown tool was therefore proposed
# `sql`, with `dbt_blockers: []` and exit 0 -- all three of `snowpark_segment`, `manual_node` and
# `unknown_node` missed, and `manifest.output_kind` then mirrored as `dbt`. The macro's own node
# stays `sql`; what is INSIDE it decides.


def _sub(nodes):
    return {"workflow": WF, "file_kind": "yxmc",
            "nodes": [n if isinstance(n, dict) else {"tool_id": n[0], "type": n[1], "config": n[2]} for n in nodes],
            "edges": []}


def _macro(tool_id, sub_nodes=None, *, unresolved=False):
    """A macro node in the shape `scripts/parse.py` writes it: `sub_dag` is the macro parsed
    recursively against the same dag contract, or `None` when the `.yxmc` could not be resolved."""
    return {"tool_id": tool_id, "type": "macro", "config": {"values": {}},
            "macro_path": "m.yxmc", "unresolved": unresolved, "interface": [],
            "sub_dag": None if unresolved else _sub(sub_nodes or [])}


def build_with_macro(tmp_path, macro_node, outputs=OUT_OVERWRITE):
    """PLAIN in seg_01, one macro node in seg_02 -- the shape `workflows/wf_0004` really has."""
    repo = Repo(tmp_path)
    plain = [{"tool_id": i, "type": t, "config": c} for i, t, c in PLAIN]
    write_json(repo.wf(WF, "parsed", "dag.json"), {"workflow": WF, "nodes": plain + [macro_node], "edges": []})
    write_json(repo.wf(WF, "segments", "order.json"), [["seg_01"], ["seg_02"]])
    for seg, nodes in (("seg_01", plain), ("seg_02", [macro_node])):
        write_json(repo.seg(WF, seg, "dag.json"), {"workflow": WF, "segment": seg, "nodes": nodes,
                                                   "edges": [], "inbound": [], "outbound": []})
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), {"sources": {}, "outputs": outputs})
    return repo


def test_the_macro_node_itself_is_still_sql(tmp_path):
    """`plugin_map.node_class` is unchanged: a RESOLVED macro's own node carries no
    transformation of its own, and the sub-DAG is what decides."""
    assert plugin_map.node_class("macro") == "sql"


def test_a_macro_hiding_a_python_tool_makes_its_segment_snowpark(tmp_path):
    """The reviewer's synthetic probe, reproduced: python + run_command + unknown inside one
    macro, with the workflow preferring dbt."""
    macro = _macro("9", [("1", "macro_input", {}), ("3", "python", {"script": "pass"}),
                         ("4", "run_command", {}), ("6", "unknown", {}), ("7", "macro_output", {})])
    repo = build_with_macro(tmp_path, macro)

    result = tc.target_check(repo, WF, "dbt")

    assert result["segments"] == {"seg_01": "sql", "seg_02": "snowpark"}
    assert result["nodes"] == {"9/3": "snowpark", "9/4": "manual", "9/6": "unknown"}
    assert result["output_kind"] == "procedures"
    kinds = {(b["kind"], b.get("tool_id")) for b in result["dbt_blockers"]}
    assert ("snowpark_segment", None) in kinds
    assert ("manual_node", "9/4") in kinds
    assert ("unknown_node", "9/6") in kinds


def test_a_macro_hiding_an_unknown_tool_exits_1(tmp_path):
    macro = _macro("9", [("6", "unknown", {})])
    repo = build_with_macro(tmp_path, macro)

    assert tc.main([WF, "--root", str(tmp_path)]) == 1
    assert read_json(repo.wf(WF, "segments", "targets.json"))["nodes"] == {"9/6": "unknown"}


def test_an_unresolved_macro_is_unknown_not_sql(tmp_path):
    """`sub_dag: null` means the `.yxmc` was never found, so nothing at all is known about what
    the node does -- the one honest classification is `unknown`."""
    repo = build_with_macro(tmp_path, _macro("9", unresolved=True))

    assert tc.main([WF, "--root", str(tmp_path)]) == 1

    result = read_json(repo.wf(WF, "segments", "targets.json"))
    assert result["nodes"] == {"9": "unknown"}
    assert {"segment": "seg_02", "kind": "unknown_node", "tool_id": "9"} in result["dbt_blockers"]


def test_a_macro_inside_a_macro_is_reached_under_its_full_path(tmp_path):
    inner = _macro("8", [("3", "python", {"script": "pass"})])
    repo = build_with_macro(tmp_path, _macro("9", [inner]))

    result = tc.target_check(repo, WF, "dbt")

    assert result["nodes"] == {"9/8/3": "snowpark"}
    assert result["segments"]["seg_02"] == "snowpark"


def test_a_macro_of_ordinary_tools_changes_nothing(tmp_path):
    """The committed `workflows/wf_0004` shape: a resolved macro whose sub-DAG is all `sql`
    tools. Descending must leave its proposal exactly as it was."""
    macro = _macro("9", [("1", "macro_input", {}), ("2", "regex", {}), ("3", "formula", {}),
                         ("4", "filter", {}), ("5", "macro_output", {}), ("10", "interface", {})])
    repo = build_with_macro(tmp_path, macro)

    result = tc.target_check(repo, WF, "dbt")

    assert result["segments"] == {"seg_01": "sql", "seg_02": "sql"}
    assert result["nodes"] == {}
    assert result["dbt_blockers"] == []
    assert result["output_kind"] == "dbt"


def test_an_output_tool_inside_a_macro_is_still_held_to_the_plain_sql_rule(tmp_path):
    macro = _macro("9", [("8", "output", {"pre_sql": "DELETE FROM X; DELETE FROM Y", "post_sql": None})])
    repo = build_with_macro(tmp_path, macro)

    result = tc.target_check(repo, WF, "dbt")

    assert {"segment": "seg_02", "kind": "presql_not_plain", "tool_id": "9/8"} in result["dbt_blockers"]


# --- the committed dbt sample (plan Task C) ---------------------------------------------------------


def test_the_wf_0007_sample_is_proposed_as_dbt(tmp_path):
    from tests.helpers import prepare_workflow
    repo = prepare_workflow(tmp_path, "wf_0007")
    result = tc.target_check(repo, "wf_0007", "auto")
    assert result["preference"] == "dbt"
    assert result["output_kind"] == "dbt" and result["dbt_blockers"] == []
    assert result["segments"] == {"seg_01": "sql", "seg_02": "sql"}


def test_the_wf_0007_canned_analysis_quotes_the_real_targets_json_reason(tmp_path):
    """The canned analysis.md is what the analyzer would write after reading targets.json, so the
    reason it quotes for `output_kind: dbt` must be the one target_check.py actually writes -- and
    the CLI's default `--prefer auto` must land on the same proposal as the function."""
    from pathlib import Path

    from tests.helpers import prepare_workflow
    repo = prepare_workflow(tmp_path, "wf_0007")
    assert tc.main(["wf_0007", "--root", str(tmp_path)]) == 0
    written = read_json(repo.wf("wf_0007", "segments", "targets.json"))
    assert written["output_kind"] == "dbt" and written["dbt_blockers"] == []
    analysis = (Path(__file__).parents[1] / "samples" / "wf_0007" / "canned" / "analysis.md")
    assert written["reason"] in analysis.read_text(encoding="utf-8")
