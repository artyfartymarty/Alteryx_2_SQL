"""`stitch_analysis.py` (Task W2): a batched analyzer writes one fragment per batch
(`analysis/<batch>.md` and `analysis/<batch>.unsupported.json`); the script -- never an agent --
stitches them into `analysis.md` and `unsupported.json` in segment order, and refuses (exit 1,
nothing written) anything but every segment exactly once.
"""
from __future__ import annotations

import pytest

import stitch_analysis as sa
from lib.io import read_json, write_json
from lib.paths import Repo

WF = "wf_0300"
ORDER = [["seg_01", "seg_02"], ["seg_03"]]
BATCHES = [
    {"id": "batch_01", "waves": [0], "segments": ["seg_01", "seg_02"], "estimate_chars": 100},
    {"id": "batch_02", "waves": [1], "segments": ["seg_03"], "estimate_chars": 80},
]
FRAGMENT_1 = "### seg_01\nEvery tool is SQL.\n\n### seg_02\nA Filter and a Select.\n"
FRAGMENT_2 = "### seg_03\nThe Join reads 2_T from seg_01 and 4_O from seg_02.\n"


def unsupported(tier: str, unsupported_tools=(), unknown=()) -> dict:
    return {"tier": tier, "unsupported": list(unsupported_tools), "unknown": list(unknown)}


def write_batched(repo: Repo, batches=BATCHES, fragments=None, unsupported_by_batch=None) -> None:
    write_json(repo.wf(WF, "segments", "order.json"), ORDER)
    write_json(repo.wf(WF, "segments", "batches.json"),
               {"budget_chars": 200, "estimate_note": "…", "batches": batches, "warnings": []})
    fragments = {"batch_01": FRAGMENT_1, "batch_02": FRAGMENT_2} if fragments is None else fragments
    unsupported_by_batch = unsupported_by_batch or {"batch_01": unsupported("T1"), "batch_02": unsupported("T1")}
    for batch_id, text in fragments.items():
        path = repo.wf(WF, "analysis", f"{batch_id}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for batch_id, body in unsupported_by_batch.items():
        write_json(repo.wf(WF, "analysis", f"{batch_id}.unsupported.json"), body)


@pytest.fixture
def repo(tmp_path) -> Repo:
    return Repo(tmp_path)


def _run(repo: Repo) -> int:
    return sa.main([WF, "--root", str(repo.root)])


def test_every_segment_appears_exactly_once_in_segment_order(repo):
    write_batched(repo)
    assert _run(repo) == 0

    text = repo.wf(WF, "analysis.md").read_text(encoding="utf-8")
    assert text.startswith(f"# {WF} analysis (stitched from 2 batches by scripts/stitch_analysis.py)\n")
    first = text.index("## batch_01: segments seg_01, seg_02")
    second = text.index("## batch_02: segments seg_03")
    assert first < text.index(FRAGMENT_1) < second < text.index(FRAGMENT_2)
    assert text.count("## batch_") == 2
    assert text.endswith("\n") and not text.endswith("\n\n\n")


def test_a_segment_in_two_batches_is_refused(repo, capsys):
    batches = [dict(BATCHES[0]), {**BATCHES[1], "segments": ["seg_02", "seg_03"]}]
    write_batched(repo, batches=batches)

    assert _run(repo) == 1
    assert "seg_02" in capsys.readouterr().err
    assert not repo.wf(WF, "analysis.md").exists()
    assert not repo.wf(WF, "unsupported.json").exists()


def test_a_missing_fragment_is_refused(repo, capsys):
    write_batched(repo, fragments={"batch_01": FRAGMENT_1})
    assert _run(repo) == 1
    assert "analysis/batch_02.md" in capsys.readouterr().err
    assert not repo.wf(WF, "analysis.md").exists()
    assert not repo.wf(WF, "unsupported.json").exists()


@pytest.mark.parametrize("change, named", [
    ("missing-segment", "seg_03"),
    ("unknown-segment", "seg_09"),
    ("out-of-order", "order"),
    ("empty-fragment", "analysis/batch_02.md"),
    ("missing-unsupported", "analysis/batch_02.unsupported.json"),
    ("bad-tier", "tier"),
])
def test_anything_but_every_segment_exactly_once_is_refused(repo, capsys, change, named):
    batches = [dict(b) for b in BATCHES]
    fragments = {"batch_01": FRAGMENT_1, "batch_02": FRAGMENT_2}
    unsupported_by_batch = {"batch_01": unsupported("T1"), "batch_02": unsupported("T1")}
    if change == "missing-segment":
        batches[1]["segments"] = []
    elif change == "unknown-segment":
        batches[1]["segments"] = ["seg_03", "seg_09"]
    elif change == "out-of-order":
        batches = [{**batches[1], "id": "batch_01"}, {**batches[0], "id": "batch_02"}]
    elif change == "empty-fragment":
        fragments["batch_02"] = "  \n"
    elif change == "missing-unsupported":
        del unsupported_by_batch["batch_02"]
    elif change == "bad-tier":
        unsupported_by_batch["batch_02"] = unsupported("T9")
    write_batched(repo, batches=batches, fragments=fragments, unsupported_by_batch=unsupported_by_batch)

    assert _run(repo) == 1
    assert named in capsys.readouterr().err
    assert not repo.wf(WF, "analysis.md").exists()


def test_unsupported_json_takes_the_highest_tier_and_every_tool_once(repo):
    manual = {"tool_id": "7", "type": "run_command", "class": "manual", "reason": "shells out", "blocks_migration": True}
    unknown_tool = {"tool_id": "12", "type": "unknown", "class": "unknown", "reason": "vendor", "blocks_migration": True}
    unknown_entry = {"tool_id": "12", "plugin": "Vendor.X", "confidence": 0.5, "behavior": "unclear"}
    write_batched(repo, unsupported_by_batch={
        "batch_01": unsupported("T2", [manual]),
        # batch_02 repeats tool 7 (e.g. it reads that segment's stream) and adds tool 12
        "batch_02": unsupported("T3", [manual, unknown_tool], [unknown_entry]),
    })

    assert _run(repo) == 0

    merged = read_json(repo.wf(WF, "unsupported.json"))
    assert merged["tier"] == "T3"
    assert merged["unsupported"] == [manual, unknown_tool]
    assert merged["unknown"] == [unknown_entry]

    write_batched(repo, unsupported_by_batch={"batch_01": unsupported("T1"), "batch_02": unsupported("T2")})
    assert _run(repo) == 0
    assert read_json(repo.wf(WF, "unsupported.json")) == unsupported("T2")


def test_stitching_is_deterministic(repo):
    write_batched(repo)
    assert _run(repo) == 0
    first = (repo.wf(WF, "analysis.md").read_bytes(), repo.wf(WF, "unsupported.json").read_bytes())
    assert _run(repo) == 0
    assert (repo.wf(WF, "analysis.md").read_bytes(), repo.wf(WF, "unsupported.json").read_bytes()) == first


def test_stitch_returns_what_it_wrote(repo):
    write_batched(repo)
    result = sa.stitch(repo, WF)
    assert result == {"batches": ["batch_01", "batch_02"], "segments": ["seg_01", "seg_02", "seg_03"], "tier": "T1"}


def test_cli_usage_errors_write_nothing(repo, capsys):
    # a workflow that has not been prepared (no order.json, no batches.json)
    with pytest.raises(SystemExit) as exc:
        _run(repo)
    assert exc.value.code == 2
    assert "order.json" in capsys.readouterr().err

    write_json(repo.wf(WF, "segments", "order.json"), ORDER)
    with pytest.raises(SystemExit) as exc:
        _run(repo)
    assert exc.value.code == 2
    assert "batches.json" in capsys.readouterr().err
    assert not repo.wf(WF, "analysis.md").exists()

    # a batch plan that is not JSON is a crash (exit 2), never a verdict
    write_batched(repo)
    repo.wf(WF, "segments", "batches.json").write_text("{nope", encoding="utf-8")
    assert _run(repo) == 2
    assert not repo.wf(WF, "analysis.md").exists()


def test_a_fragment_that_is_not_json_is_refused(repo, capsys):
    """A fragment is model-written: one the stitch cannot read is refused like a missing one."""
    write_batched(repo)
    repo.wf(WF, "analysis", "batch_02.unsupported.json").write_text("{nope", encoding="utf-8")
    assert _run(repo) == 1
    assert "analysis/batch_02.unsupported.json is not JSON" in capsys.readouterr().err
    assert not repo.wf(WF, "analysis.md").exists()
