"""Segmenter tests: deterministic DAG cuts into `segments/` (plan task 5).

The first six tests are the brief's Step 1 verbatim. The rest cover behaviour the brief describes
in prose but doesn't test directly: successful merging across a soft cut (wf_0002's own README says
`min_tools: 3` collapses it to one segment), join-input protection actually changing which bridge is
chosen, and `run()`'s file outputs, manifest wiring, idempotency and stale-directory cleanup.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import parse
import segment
from lib import io as lib_io
from lib.paths import Repo

SAMPLES = Path(__file__).parents[1] / "samples"


def seg_of(wf, name, **kw):
    dag, _ = parse.parse_file(SAMPLES / wf / "source" / name)
    return dag, segment.segment(dag, **kw)


def owner(result):
    return {t: s for s, ts in result["segments"].items() for t in ts}


# --- brief Step 1, verbatim ---

def test_small_workflow_collapses_to_one_segment_by_default():
    _, r = seg_of("wf_0001", "sales_summary.yxmd")
    assert list(r["segments"]) == ["seg_01"] and r["order"] == [["seg_01"]]


def test_containers_cut_and_waves_are_topological():
    _, r = seg_of("wf_0002", "customer_orders.yxmd", min_tools=2)
    o = owner(r)
    assert o["1"] == o["2"] and o["3"] == o["4"] and o["1"] != o["3"]
    assert o["5"] not in (o["1"], o["3"]) and len({o[t] for t in ("5", "6", "7", "8", "9", "10")}) == 1
    assert r["order"] == [sorted([o["1"], o["3"]]), [o["5"]]]                # two prep segments in parallel, then the join
    assert "11" in r["segments"][o["9"]]                                     # browse rides with its upstream


def test_ordering_chain_is_never_split():
    _, r = seg_of("wf_0003", "gl_period_close.yxmd", min_tools=2, max_tools=4)
    o = owner(r)
    assert len({o[t] for t in ("4", "5", "6", "7")}) == 1
    assert len({o[t] for t in ("9",)} | {o["6"]}) == 1                       # summarize Last depends on the same order


def test_macro_is_its_own_segment():
    _, r = seg_of("wf_0004", "inventory.yxmd", min_tools=10)
    assert ["2"] in r["segments"].values()


def test_segment_dag_lists_inbound_and_outbound():
    dag, r = seg_of("wf_0004", "inventory.yxmd", min_tools=10)
    o = owner(r); seg = o["2"]
    sd = segment.build_segment_dag(dag, seg, r["segments"][seg], o)
    assert sd["inbound"][0]["src"] == "1" and sd["inbound"][0]["from_segment"] == o["1"]
    assert sd["outbound"][0]["dst"] == "3" and sd["outbound"][0]["to_segment"] == o["3"] and sd["edges"] == []


def test_oversized_group_is_split_at_a_bridge():
    nodes = [{"tool_id": str(i), "type": "formula", "container_id": None} for i in range(1, 31)]
    edges = [{"src": str(i), "src_anchor": "Output", "dst": str(i + 1), "dst_anchor": "Input"} for i in range(1, 30)]
    r = segment.segment({"workflow": "x", "nodes": nodes, "edges": edges}, min_tools=5, max_tools=40, formula_heavy_cap=20)
    assert len(r["segments"]) == 2 and all(len(v) <= 20 for v in r["segments"].values())


# --- additional coverage: prose-described behaviour the brief's own tests don't exercise ---

def test_undersized_containers_merge_across_soft_cuts():
    """wf_0002's README: at min_tools 3 (the other samples' default) both 2-tool prep containers
    fall below the floor and merge into the join's group, collapsing the workflow to one segment.
    None of the brief's six tests ever produces a successful merge, so this is the only coverage
    for step 5's actual merge path (as opposed to "undersized but no eligible neighbour")."""
    _, r = seg_of("wf_0002", "customer_orders.yxmd", min_tools=3)
    assert list(r["segments"]) == ["seg_01"] and r["order"] == [["seg_01"]]


def test_join_inputs_are_never_a_split_point():
    """A join whose two inputs (2->4 and 3->4) tie for the best-balanced bridge with a valid,
    unprotected edge (4->5) elsewhere in the group: splitting must prefer the unprotected edge
    and keep the join with both its inputs, proving step 4's protection actually excludes
    candidates rather than merely never being exercised."""
    nodes = [
        {"tool_id": "1", "type": "formula", "container_id": None},
        {"tool_id": "2", "type": "formula", "container_id": None},
        {"tool_id": "3", "type": "formula", "container_id": None},
        {"tool_id": "4", "type": "join", "container_id": None},
        {"tool_id": "5", "type": "formula", "container_id": None},
        {"tool_id": "6", "type": "formula", "container_id": None},
    ]
    edges = [
        {"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input"},
        {"src": "2", "src_anchor": "Output", "dst": "4", "dst_anchor": "Left"},
        {"src": "3", "src_anchor": "Output", "dst": "4", "dst_anchor": "Right"},
        {"src": "4", "src_anchor": "Join", "dst": "5", "dst_anchor": "Input"},
        {"src": "5", "src_anchor": "Output", "dst": "6", "dst_anchor": "Input"},
    ]
    r = segment.segment({"workflow": "x", "nodes": nodes, "edges": edges}, min_tools=1, max_tools=4)
    o = owner(r)
    assert o["1"] == o["2"] == o["3"] == o["4"]
    assert len(r["segments"]) == 2


def test_unsplittable_oversized_group_is_kept_and_warned():
    """The whole group is one ordering-protected chain (a sort feeding a record_id): step 6 can
    find no legal bridge, so it must keep the oversized group intact and say why, rather than
    silently violating the size cap or crashing."""
    nodes = ([{"tool_id": "1", "type": "sort", "container_id": None}] +
             [{"tool_id": str(i), "type": "record_id", "container_id": None} for i in range(2, 8)])
    edges = [{"src": str(i), "src_anchor": "Output", "dst": str(i + 1), "dst_anchor": "Input"} for i in range(1, 7)]
    r = segment.segment({"workflow": "x", "nodes": nodes, "edges": edges}, min_tools=1, max_tools=3)
    o = owner(r)
    assert len({o[str(i)] for i in range(1, 8)}) == 1
    assert any("no splittable bridge" in w for w in r["warnings"])


# --- run(): file outputs, manifest, idempotency, stale-directory cleanup ---

SIMPLE_DAG = {
    "workflow": "wf_test",
    "nodes": [
        {"tool_id": "1", "type": "input", "container_id": None},
        {"tool_id": "2", "type": "formula", "container_id": None},
        {"tool_id": "3", "type": "output", "container_id": None},
    ],
    "edges": [
        {"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input", "dst_order": 1, "wireless": False},
        {"src": "2", "src_anchor": "Output", "dst": "3", "dst_anchor": "Input", "dst_order": 1, "wireless": False},
    ],
}


def test_run_writes_segment_files_and_manifest(tmp_path):
    repo = Repo(tmp_path)
    lib_io.write_json(repo.wf("wf_test", "parsed", "dag.json"), SIMPLE_DAG)

    result = segment.run(repo, "wf_test", min_tools=1, max_tools=40)

    assert result["segments"] == {"seg_01": ["1", "2", "3"]}
    seg_dag = lib_io.read_json(repo.seg("wf_test", "seg_01", "dag.json"))
    assert seg_dag["segment"] == "seg_01" and [n["tool_id"] for n in seg_dag["nodes"]] == ["1", "2", "3"]
    assert lib_io.read_json(repo.wf("wf_test", "segments", "order.json")) == [["seg_01"]]
    assert lib_io.read_json(repo.wf("wf_test", "segments", "segmentation.json"))["segments"] == result["segments"]
    manifest = lib_io.read_json(repo.wf("wf_test", "manifest.json"))
    assert manifest["segments"] == ["seg_01"]


def test_run_is_idempotent_byte_identical(tmp_path):
    repo = Repo(tmp_path)
    lib_io.write_json(repo.wf("wf_test", "parsed", "dag.json"), SIMPLE_DAG)

    segment.run(repo, "wf_test", min_tools=1)
    first_segmentation = repo.wf("wf_test", "segments", "segmentation.json").read_bytes()
    first_seg_dag = repo.seg("wf_test", "seg_01", "dag.json").read_bytes()

    segment.run(repo, "wf_test", min_tools=1)
    assert repo.wf("wf_test", "segments", "segmentation.json").read_bytes() == first_segmentation
    assert repo.seg("wf_test", "seg_01", "dag.json").read_bytes() == first_seg_dag


def test_run_reads_manifest_segmentation_and_cli_overrides_win(tmp_path):
    repo = Repo(tmp_path)
    lib_io.write_json(repo.wf("wf_test", "parsed", "dag.json"), SIMPLE_DAG)
    lib_io.write_json(repo.wf("wf_test", "manifest.json"),
                      {"id": "wf_test", "segmentation": {"min_tools": 1, "max_tools": 2}})

    from_manifest = segment.run(repo, "wf_test")
    assert from_manifest["params"]["max_tools"] == 2

    overridden = segment.run(repo, "wf_test", max_tools=40)
    assert overridden["params"]["max_tools"] == 40


def test_run_removes_empty_stale_dirs_but_warns_for_dirs_with_extra_files(tmp_path):
    repo = Repo(tmp_path)
    lib_io.write_json(repo.wf("wf_test", "parsed", "dag.json"), SIMPLE_DAG)
    segment.run(repo, "wf_test", min_tools=1, max_tools=40)

    stale_only_dag = repo.seg("wf_test", "seg_02")
    stale_only_dag.mkdir(parents=True)
    (stale_only_dag / "dag.json").write_text("{}", encoding="utf-8")

    stale_with_extra = repo.seg("wf_test", "seg_03")
    stale_with_extra.mkdir(parents=True)
    (stale_with_extra / "dag.json").write_text("{}", encoding="utf-8")
    (stale_with_extra / "contract.json").write_text("{}", encoding="utf-8")

    result = segment.run(repo, "wf_test", min_tools=1, max_tools=40)

    assert not stale_only_dag.exists()
    assert stale_with_extra.exists() and (stale_with_extra / "contract.json").exists()
    assert any("seg_03" in w for w in result["warnings"])


# --- main(): exit codes (fix round 1) ---

# Two Tool Containers whose tools cross each other in both directions: 1->3 and 2->4 are the
# containers' own internal wiring (each container stays one group), but 1->2 and 4->3 cross in
# opposite directions, so the *group*-level graph has a 2-cycle even though the tool-level graph
# (invariants.py's own acyclicity check) is perfectly fine -- 3 and 4 are sinks, so there's no path
# back to 1 or 2. This is the "group graph has a cycle" case step 7 falls back on.
CYCLIC_GROUP_DAG = {
    "workflow": "wf_cyclic",
    "nodes": [
        {"tool_id": "100", "type": "container", "container_id": None},
        {"tool_id": "200", "type": "container", "container_id": None},
        {"tool_id": "1", "type": "formula", "container_id": "100"},
        {"tool_id": "3", "type": "formula", "container_id": "100"},
        {"tool_id": "2", "type": "formula", "container_id": "200"},
        {"tool_id": "4", "type": "formula", "container_id": "200"},
    ],
    "edges": [
        {"src": "1", "src_anchor": "Output", "dst": "3", "dst_anchor": "Input", "dst_order": 1, "wireless": False},
        {"src": "2", "src_anchor": "Output", "dst": "4", "dst_anchor": "Input", "dst_order": 1, "wireless": False},
        {"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input", "dst_order": 1, "wireless": False},
        {"src": "4", "src_anchor": "Output", "dst": "3", "dst_anchor": "Input", "dst_order": 1, "wireless": False},
    ],
}


def test_main_on_never_parsed_workflow_exits_2_and_writes_nothing(tmp_path):
    with pytest.raises(SystemExit) as exc:
        segment.main(["wf_absent", "--root", str(tmp_path)])
    assert exc.value.code == 2
    assert not (tmp_path / "workflows" / "wf_absent").exists()


def test_main_with_no_arguments_exits_2():
    with pytest.raises(SystemExit) as exc:
        segment.main([])
    assert exc.value.code == 2


def test_main_on_parsed_sample_exits_0_and_writes_artifacts(tmp_path):
    repo = Repo(tmp_path)
    lib_io.write_json(repo.wf("wf_test", "parsed", "dag.json"), SIMPLE_DAG)

    assert segment.main(["wf_test", "--root", str(tmp_path)]) == 0

    assert repo.seg("wf_test", "seg_01", "dag.json").exists()
    assert repo.wf("wf_test", "segments", "order.json").exists()
    assert repo.wf("wf_test", "segments", "segmentation.json").exists()


def test_main_exits_2_on_unexpected_exception(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(segment, "run", boom)
    assert segment.main(["wf_test", "--root", str(tmp_path)]) == 2


def test_main_exits_0_when_only_warnings_are_produced(tmp_path):
    """An all-ordering-protected chain that step 6 cannot legally split: a real warning, but a
    valid segmentation (see test_unsplittable_oversized_group_is_kept_and_warned) -- not a failure."""
    nodes = ([{"tool_id": "1", "type": "sort", "container_id": None}] +
             [{"tool_id": str(i), "type": "record_id", "container_id": None} for i in range(2, 8)])
    edges = [{"src": str(i), "src_anchor": "Output", "dst": str(i + 1), "dst_anchor": "Input"} for i in range(1, 7)]
    repo = Repo(tmp_path)
    lib_io.write_json(repo.wf("wf_chain", "parsed", "dag.json"), {"workflow": "wf_chain", "nodes": nodes, "edges": edges})

    exit_code = segment.main(["wf_chain", "--min-tools", "1", "--max-tools", "3", "--root", str(tmp_path)])

    result = lib_io.read_json(repo.wf("wf_chain", "segments", "segmentation.json"))
    assert result["warnings"] and exit_code == 0


def test_main_exits_1_when_the_group_graph_has_a_cycle(tmp_path):
    repo = Repo(tmp_path)
    lib_io.write_json(repo.wf("wf_cyclic", "parsed", "dag.json"), CYCLIC_GROUP_DAG)

    exit_code = segment.main(["wf_cyclic", "--min-tools", "1", "--root", str(tmp_path)])

    result = lib_io.read_json(repo.wf("wf_cyclic", "segments", "segmentation.json"))
    assert any("cycle" in w for w in result["warnings"]) and exit_code == 1
