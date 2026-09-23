"""Task W3, Step 1 (size-aware segmentation): tool count alone under-weights a handful of huge-
config tools (a Formula with hundreds of expressions), so every group is also kept under a
prompt-size *character* budget. The first six tests are the brief's own, verbatim by name
(task-W3-brief.md); the rest cover the CLI flag and the `--max-prompt-chars` -> manifest ->
`global.yaml` -> default resolution order in prose but not directly in the brief's own list.

Nothing here has run against real Snowflake or Alteryx; `prompt_chars` is a structural character
count, not a real token count (module docstring of `scripts/segment.py`).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import segment
from lib import io as lib_io
from lib.paths import Repo

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "workflows"


def owner(result):
    return {t: s for s, ts in result["segments"].items() for t in ts}


def _heavy_formula(tid: str, expr_len: int, container_id: str | None = None) -> dict:
    """A `formula` node whose single expression is `expr_len` characters -- the brief's own shape
    for a Formula tool with an enormous config."""
    return {
        "tool_id": tid, "type": "formula", "container_id": container_id,
        "config": {"formulas": [{"field": "X", "expression": "a" * expr_len,
                                  "type": "String", "size": expr_len}]},
    }


def _chain_dag(expr_len: int = 20000, n_formulas: int = 6) -> dict:
    """`input -> n_formulas heavy formula tools -> output`, the brief's own linear-chain shape."""
    nodes = [{"tool_id": "1", "type": "input", "container_id": None, "config": {}}]
    nodes += [_heavy_formula(str(i), expr_len) for i in range(2, 2 + n_formulas)]
    last = 2 + n_formulas
    nodes.append({"tool_id": str(last), "type": "output", "container_id": None, "config": {}})
    edges = [{"src": str(i), "src_anchor": "Output", "dst": str(i + 1), "dst_anchor": "Input"}
             for i in range(1, last)]
    return {"workflow": "x", "nodes": nodes, "edges": edges}


# --- brief Step 1, verbatim ---

def test_prompt_chars_is_the_compact_json_of_type_config_and_meta():
    node = {
        "tool_id": "1", "type": "formula", "container_id": None,
        "config": {"formulas": [{"field": "X", "expression": "[A]+[B]", "type": "Double", "size": 8}]},
        "meta": {"Output": [{"name": "X", "type": "Double", "size": 8, "scale": None}]},
        "annotation": "not counted", "raw_config": "<Configuration>not counted either</Configuration>",
    }
    expected = len(json.dumps(
        {"type": "formula",
         "config": {"formulas": [{"field": "X", "expression": "[A]+[B]", "type": "Double", "size": 8}]},
         "meta": {"Output": [{"name": "X", "type": "Double", "size": 8, "scale": None}]}},
        sort_keys=True, separators=(",", ":")))
    assert segment.prompt_chars(node) == expected
    # annotation/raw_config genuinely excluded, not just coincidentally equal
    node["annotation"] = "x" * 10000
    node["raw_config"] = "y" * 10000
    assert segment.prompt_chars(node) == expected


def test_a_group_under_max_tools_but_over_the_prompt_budget_is_split():
    dag = _chain_dag(expr_len=20000, n_formulas=6)
    nodes_by_id = {n["tool_id"]: n for n in dag["nodes"]}

    r = segment.segment(dag, min_tools=1, max_tools=40, max_prompt_chars=45000)

    assert len(r["segments"]) > 1  # 6 * ~20000 chars would be one seg_01 on tool count alone
    for members in r["segments"].values():
        total = sum(segment.prompt_chars(nodes_by_id[t]) for t in members)
        assert total <= 45000 or len(members) == 1
        assert len(members) < 40


def test_merging_never_crosses_the_prompt_budget():
    """Two undersized neighbours (one tool each, well under `min_tools`), separated only by a
    Tool Container boundary (a soft cut, so they start as two DSU groups but are still eligible
    neighbours for step 5's merge) -- ordinarily step 5 would merge them, but their combined
    character estimate is over budget, so they must stay apart."""
    node1 = _heavy_formula("1", 30000, container_id="100")
    node2 = _heavy_formula("2", 30000, container_id="200")
    dag = {
        "workflow": "x",
        "nodes": [
            {"tool_id": "100", "type": "container", "container_id": None},
            {"tool_id": "200", "type": "container", "container_id": None},
            node1, node2,
        ],
        "edges": [{"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input"}],
    }
    each = segment.prompt_chars(node1)
    budget = each + 10  # room for one tool alone, nowhere near room for both

    r = segment.segment(dag, min_tools=5, max_tools=40, max_prompt_chars=budget)

    o = owner(r)
    assert o["1"] != o["2"]
    # sanity: without the budget (plenty of room), step 5 really would merge these two
    merged = segment.segment(dag, min_tools=5, max_tools=40, max_prompt_chars=each * 10)
    assert owner(merged)["1"] == owner(merged)["2"]


def test_a_single_tool_over_budget_stands_alone_with_a_warning():
    node = _heavy_formula("1", 70000)
    dag = {"workflow": "x", "nodes": [node], "edges": []}

    r = segment.segment(dag, min_tools=1, max_tools=40)  # max_prompt_chars defaults to 60000

    assert r["segments"] == {"seg_01": ["1"]}
    n = segment.prompt_chars(node)
    assert n > segment.DEFAULT_MAX_PROMPT_CHARS
    expected_warning = f"tool 1 alone is estimated at {n} characters, over segmentation.max_prompt_chars 60000"
    assert expected_warning in r["warnings"]


def test_prompt_size_splitting_is_deterministic():
    dag = _chain_dag(expr_len=20000, n_formulas=6)
    r1 = segment.segment(dag, min_tools=1, max_tools=40, max_prompt_chars=45000)
    r2 = segment.segment(dag, min_tools=1, max_tools=40, max_prompt_chars=45000)
    assert r1 == r2


COMMITTED_WFS = sorted(p.parents[1].name for p in WORKFLOWS.glob("*/segments/order.json")) \
    if WORKFLOWS.is_dir() else []


@pytest.mark.parametrize("wf", COMMITTED_WFS)
def test_every_committed_sample_segments_exactly_as_before(wf):
    dag = lib_io.read_json(WORKFLOWS / wf / "parsed" / "dag.json")
    manifest = lib_io.read_json(WORKFLOWS / wf / "manifest.json")
    params = dict(manifest.get("segmentation") or {})

    result = segment.segment(dag, **params)

    committed_order = lib_io.read_json(WORKFLOWS / wf / "segments" / "order.json")
    assert result["order"] == committed_order

    for seg, members in result["segments"].items():
        committed_dag = lib_io.read_json(WORKFLOWS / wf / "segments" / seg / "dag.json")
        committed_ids = [n["tool_id"] for n in committed_dag["nodes"]]
        assert members == committed_ids, f"{wf} {seg}: node ids changed"
    assert set(result["segments"]) == {seg for wave in committed_order for seg in wave}


# --- additional coverage: prose-described behaviour the brief's own six tests don't exercise ---

def test_default_max_prompt_chars_is_60000():
    assert segment.DEFAULT_MAX_PROMPT_CHARS == 60000


def test_run_resolves_max_prompt_chars_cli_then_manifest_then_global_then_default(tmp_path):
    repo = Repo(tmp_path)
    lib_io.write_json(repo.wf("wf_test", "parsed", "dag.json"), {
        "workflow": "wf_test",
        "nodes": [{"tool_id": "1", "type": "formula", "container_id": None, "config": {}}],
        "edges": [],
    })

    # nothing set anywhere: segment()'s own default
    result = segment.run(repo, "wf_test", min_tools=1)
    assert result["params"]["max_prompt_chars"] == segment.DEFAULT_MAX_PROMPT_CHARS

    # global.yaml sets it
    lib_io.write_yaml(repo.global_mappings, {"segmentation": {"max_prompt_chars": 10000}})
    result = segment.run(repo, "wf_test", min_tools=1)
    assert result["params"]["max_prompt_chars"] == 10000

    # manifest.segmentation overrides global.yaml
    lib_io.write_json(repo.wf("wf_test", "manifest.json"),
                      {"id": "wf_test", "segmentation": {"min_tools": 1, "max_prompt_chars": 20000}})
    result = segment.run(repo, "wf_test")
    assert result["params"]["max_prompt_chars"] == 20000

    # a CLI override beats both
    result = segment.run(repo, "wf_test", min_tools=1, max_prompt_chars=30000)
    assert result["params"]["max_prompt_chars"] == 30000


def test_cli_accepts_max_prompt_chars_flag(tmp_path):
    repo = Repo(tmp_path)
    lib_io.write_json(repo.wf("wf_test", "parsed", "dag.json"), {
        "workflow": "wf_test",
        "nodes": [{"tool_id": "1", "type": "formula", "container_id": None, "config": {}}],
        "edges": [],
    })

    exit_code = segment.main(["wf_test", "--min-tools", "1", "--max-prompt-chars", "12345",
                              "--root", str(tmp_path)])

    assert exit_code == 0
    result = lib_io.read_json(repo.wf("wf_test", "segments", "segmentation.json"))
    assert result["params"]["max_prompt_chars"] == 12345


# --- additional coverage (review follow-up): the two "stays above its size cap" sub-branches that
# name a character estimate -- character-only, and tool-count-and-characters combined. Both are an
# ordering-protected chain (like test_segment.py's own test_unsplittable_oversized_group_is_kept_
# and_warned), so find_split has no legal bridge regardless of size; only the *reason* named in the
# warning differs, and that's what these pin (scripts/segment.py's split loop, ~lines 324-338).

def test_character_only_unsplittable_group_is_kept_and_warned_with_the_character_estimate():
    """`sort -> unique`: step 3 protects the only edge (unique is order-dependent, sort is its
    anchor), so step 6 can never split this group no matter its size. Padded config pushes it over
    a 15000-character budget while its tool count (2) stays nowhere near `max_tools`, so the
    "stays above its size cap" warning must name characters only -- no tool-count cap was ever
    crossed."""
    nodes = [
        {"tool_id": "1", "type": "sort", "container_id": None,
         "config": {"fields": [{"field": "ACCT", "order": "asc"}], "pad": "a" * 12000}},
        {"tool_id": "2", "type": "unique", "container_id": None,
         "config": {"fields": ["ACCT"], "pad": "a" * 10000}},
    ]
    edges = [{"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input"}]
    dag = {"workflow": "x", "nodes": nodes, "edges": edges}
    total_chars = sum(segment.prompt_chars(n) for n in nodes)
    assert total_chars > 15000  # sanity: the padding really does cross the budget

    r = segment.segment(dag, min_tools=1, max_tools=40, max_prompt_chars=15000)

    o = owner(r)
    assert o["1"] == o["2"] and len(r["segments"]) == 1
    assert any(f"{total_chars} characters > max_prompt_chars 15000): no splittable bridge remains" in w
               for w in r["warnings"])
    # and never the tool-count phrasing -- only 2 tools, nowhere near max_tools
    assert not any("2 > " in w for w in r["warnings"])


def test_combined_unsplittable_group_is_kept_and_warned_with_both_estimates():
    """`sort -> record_id x7` (8 tools): the whole chain is ordering-protected exactly like
    test_segment.py's own 7-tool version of this shape, but here it is over budget on BOTH
    `max_tools` (8 > 3) and characters (padded past a 5000-character budget), so the warning must
    name both reasons, not just the first one found."""
    nodes = [{"tool_id": "1", "type": "sort", "container_id": None,
              "config": {"fields": [{"field": "ACCT", "order": "asc"}], "pad": "a" * 1000}}]
    nodes += [{"tool_id": str(i), "type": "record_id", "container_id": None,
               "config": {"field": "RecordID", "start": 1, "type": "Int32", "position": "first",
                          "pad": "a" * 1000}}
              for i in range(2, 9)]
    edges = [{"src": str(i), "src_anchor": "Output", "dst": str(i + 1), "dst_anchor": "Input"}
             for i in range(1, 8)]
    dag = {"workflow": "x", "nodes": nodes, "edges": edges}
    total_chars = sum(segment.prompt_chars(n) for n in nodes)
    assert total_chars > 5000  # sanity: the padding really does cross the budget

    r = segment.segment(dag, min_tools=1, max_tools=3, max_prompt_chars=5000)

    o = owner(r)
    assert len({o[str(i)] for i in range(1, 9)}) == 1  # the whole chain stayed one segment
    assert any(f"8 > 3 and {total_chars} characters > max_prompt_chars 5000): "
               "no splittable bridge remains" in w
               for w in r["warnings"])
