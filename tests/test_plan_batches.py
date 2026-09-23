"""`plan_batches.py` (Task W2): consecutive waves grouped so that each analyzer call's rendered
context fits a character budget. A pure function of the workflow's files -- the same inputs give the
same `segments/batches.json` byte for byte -- and a small workflow stays ONE call: every committed
sample is one batch under the default budget.

Characters are an estimate, not tokens (roughly characters / 4); nothing here has run against a
hosted model.
"""
from __future__ import annotations

import pytest

import plan_batches as pb
import prompt_context as pc
import segment
import target_check
from dev import build_samples
from lib.io import read_json, write_json
from lib.paths import Repo
from tests.helpers import SAMPLES, copy_pristine_mappings_and_catalog, prepare_workflow

WF = "wf_0200"


def five_wave_workflow(repo: Repo, wf: str = WF, widths=(40, 60, 80, 100, 120)) -> None:
    """tool 1 -> tool 2 -> … -> tool 5, one tool per segment, one segment per wave. Tool N's single
    output column is named with `widths[N-1]` characters, so each segment's detail size differs and
    is known from `prompt_context.segment_detail_chars` itself."""
    nodes = [
        {"tool_id": str(i), "type": "formula", "annotation": None, "container_id": None,
         "in_anchors": [] if i == 1 else ["Input"], "out_anchors": ["Output"], "config": {},
         "meta": {"Output": [{"name": "C" * width, "type": "V_String", "size": 10, "scale": None}]}}
        for i, width in enumerate(widths, 1)
    ]
    edges = [{"src": str(i), "src_anchor": "Output", "dst": str(i + 1), "dst_anchor": "Input", "dst_order": 1}
             for i in range(1, len(widths))]
    dag = {"workflow": wf, "nodes": nodes, "edges": edges}
    write_json(repo.wf(wf, "parsed", "dag.json"), dag)
    segments = {f"seg_{i:02d}": [str(i)] for i in range(1, len(widths) + 1)}
    owner = {tool: seg for seg, members in segments.items() for tool in members}
    for seg, members in segments.items():
        write_json(repo.seg(wf, seg, "dag.json"), segment.build_segment_dag(dag, seg, members, owner))
    write_json(repo.wf(wf, "segments", "order.json"), [[seg] for seg in segments])


def sizes(repo: Repo, wf: str = WF) -> tuple[int, dict[str, int]]:
    order = read_json(repo.wf(wf, "segments", "order.json"))
    return (len(pc.render_global(repo, wf)),
            {seg: pc.segment_detail_chars(repo, wf, seg) for wave in order for seg in wave})


def assert_well_formed(result: dict, order: list[list[str]]) -> None:
    """Batches are consecutive wave ranges in order, and every segment is in exactly one."""
    waves = [w for batch in result["batches"] for w in batch["waves"]]
    assert waves == list(range(len(order))), waves
    for n, batch in enumerate(result["batches"], 1):
        assert batch["id"] == f"batch_{n:02d}"
        assert batch["segments"] == [s for w in batch["waves"] for s in order[w]]
    flat = [s for batch in result["batches"] for s in batch["segments"]]
    assert flat == [s for wave in order for s in wave]


@pytest.fixture
def repo(tmp_path) -> Repo:
    repo = Repo(tmp_path)
    five_wave_workflow(repo)
    return repo


def test_a_generous_budget_is_one_batch(repo):
    result = pb.plan_batches(repo, WF, 10**9)
    order = read_json(repo.wf(WF, "segments", "order.json"))
    assert_well_formed(result, order)
    [batch] = result["batches"]
    assert batch["waves"] == [0, 1, 2, 3, 4]
    base, size = sizes(repo)
    assert batch["estimate_chars"] == base + sum(size.values())
    assert result["warnings"] == []
    assert result["budget_chars"] == 10**9
    assert "characters" in result["estimate_note"] and "tokens" in result["estimate_note"]
    assert read_json(repo.wf(WF, "segments", "batches.json")) == result


def test_a_small_budget_cuts_at_wave_boundaries_in_order(repo):
    base, size = sizes(repo)
    s = [size[f"seg_{i:02d}"] for i in range(1, 6)]
    assert len(set(s)) == 5, f"the fixture's segments must differ in size: {s}"
    budget = base + s[0] + s[1]              # waves 1-2 fit exactly; 3 does not join them

    result = pb.plan_batches(repo, WF, budget)

    order = read_json(repo.wf(WF, "segments", "order.json"))
    assert_well_formed(result, order)
    assert result["batches"][0]["waves"] == [0, 1]
    for batch in result["batches"]:
        assert batch["estimate_chars"] == base + sum(size[seg] for seg in batch["segments"])
        assert batch["estimate_chars"] <= budget or len(batch["waves"]) == 1
    # greedy: a batch closes only when the next wave would push it over the budget
    for batch, following in zip(result["batches"], result["batches"][1:]):
        assert batch["estimate_chars"] + size[following["segments"][0]] > budget
    assert result["warnings"] == []


def test_a_wave_over_budget_is_its_own_batch_with_a_warning(tmp_path):
    repo = Repo(tmp_path)
    five_wave_workflow(repo, widths=(40, 60, 4000, 100, 120))
    base, size = sizes(repo)
    budget = base + size["seg_01"] + size["seg_02"] + size["seg_04"] + size["seg_05"]
    assert base + size["seg_03"] > budget

    result = pb.plan_batches(repo, WF, budget)

    assert [b["waves"] for b in result["batches"]] == [[0, 1], [2], [3, 4]]
    assert result["batches"][1]["estimate_chars"] > budget
    [warning] = result["warnings"]
    assert "wave 3" in warning and "seg_03" in warning and str(budget) in warning


def test_batches_are_deterministic(repo):
    base, size = sizes(repo)
    budget = base + max(size.values()) + 1
    pb.plan_batches(repo, WF, budget)
    first = repo.wf(WF, "segments", "batches.json").read_bytes()
    pb.plan_batches(repo, WF, budget)
    assert repo.wf(WF, "segments", "batches.json").read_bytes() == first
    assert len(read_json(repo.wf(WF, "segments", "batches.json"))["batches"]) > 1


def test_cli(repo, capsys):
    root = str(repo.root)
    assert pb.main([WF, "--budget-chars", "1000000", "--root", root]) == 0
    assert "1 batch" in capsys.readouterr().out
    assert pb.DEFAULT_ANALYZER_BUDGET_CHARS == 60000

    # a workflow that has not been prepared: nothing to plan, nothing written
    with pytest.raises(SystemExit) as exc:
        pb.main(["wf_0404", "--budget-chars", "1000", "--root", root])
    assert exc.value.code == 2
    assert "wf_0404" in capsys.readouterr().err
    assert not repo.wf("wf_0404", "segments", "batches.json").exists()

    with pytest.raises(SystemExit) as exc:
        pb.main([WF, "--budget-chars", "0", "--root", root])
    assert exc.value.code == 2


# --- the committed samples: extra sessions cost more, so a small workflow stays one call ----------

def _segmented_sample(tmp_path, wf: str) -> Repo:
    """Every sample as the analyzer stage sees it: parsed, segmented, `targets.json` written.
    wf_0005 has no canned segments (it is tier T3) and its unknown vendor plugin trips the parse
    invariants, so `prepare_workflow` would skip it -- it is built and segmented directly instead."""
    if wf == "wf_0005":
        repo = Repo(tmp_path)
        copy_pristine_mappings_and_catalog(tmp_path)
        build_samples.build(repo, SAMPLES, wf)
    else:
        repo = prepare_workflow(tmp_path, wf)
    segment.run(repo, wf)
    target_check.target_check(repo, wf, "auto")
    return repo


@pytest.mark.parametrize("wf", ["wf_0001", "wf_0002", "wf_0003", "wf_0004", "wf_0005", "wf_0006", "wf_0007"])
def test_every_committed_sample_is_one_batch_under_the_default_budget(tmp_path, wf):
    repo = _segmented_sample(tmp_path, wf)
    result = pb.plan_batches(repo, wf, pb.DEFAULT_ANALYZER_BUDGET_CHARS)
    assert len(result["batches"]) == 1, result
    assert result["warnings"] == []
    order = read_json(repo.wf(wf, "segments", "order.json"))
    assert_well_formed(result, order)
