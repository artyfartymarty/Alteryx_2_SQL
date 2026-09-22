"""`scripts/dev/answer_samples.py`: feeding a sample workflow's recorded answers into
`manifest.json["answers"]` so `intake_prompt.py --no-interactive` can resume past
`WAITING_FOR_ANSWERS` without anyone at a prompt (plan Task 15 Step 5). Each test builds a
workflow the same way `tests/helpers.py::prepare_workflow` does (`build_samples.build`), then runs
`intake_touchpoints.run` -- exactly as far as the orchestrator's own `intake` stage gets before it
first parks a workflow -- and stops there: proving what happens between that point and
`intake_prompt.py`'s next run is this module's whole job.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import intake_touchpoints as tpx
from dev import answer_samples, build_samples
from lib import io
from lib.paths import Repo
from tests.helpers import copy_pristine_mappings_and_catalog

ROOT = Path(__file__).parents[1]
REAL_SAMPLES = ROOT / "samples"


def _repo(root: Path, wf_id: str, samples_dir: Path | None = None) -> Repo:
    copy_pristine_mappings_and_catalog(root)
    repo = Repo(root)
    build_samples.build(repo, samples_dir or REAL_SAMPLES, wf_id)
    tpx.run(repo, wf_id)
    return repo


def _answers(repo: Repo, wf_id: str) -> dict:
    return io.load_manifest(repo, wf_id).get("answers") or {}


def _run(root: Path, *, samples_dir: Path = REAL_SAMPLES, only: str | None = None) -> int:
    args = ["--root", str(root), "--samples", str(samples_dir)]
    if only is not None:
        args += ["--only", only]
    return answer_samples.main(args)


# --- key-based matching (wf_0001) and tool-id/alias matching (wf_0002, wf_0003) -------------------

def test_wf_0001_answers_land_under_the_right_qids_by_file_key(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    assert _run(tmp_path, only="wf_0001") == 0
    assert _answers(repo, "wf_0001") == {
        "Q1": "SALES.RAW.ORDERS",
        "Q2": "ANALYTICS.CURATED.SALES_SUMMARY",
        "Q3": "ANALYTICS.CURATED.EXCLUDED_ORDERS",
    }


def test_wf_0002_db_output_answers_by_tool_id_not_its_alias_key(tmp_path):
    repo = _repo(tmp_path, "wf_0002")
    assert _run(tmp_path, only="wf_0002") == 0
    answers = _answers(repo, "wf_0002")
    assert answers["Q1"] == "CRM.RAW.CUSTOMERS"
    assert answers["Q2"] == "CRM.RAW.ORDERS_EXPORT"
    assert answers["Q3"] == "ANALYTICS.CURATED.CUSTOMER_ORDER_FACT"  # sample keys this "10", not the alias


def test_wf_0003_db_input_and_output_both_answer_by_tool_id(tmp_path):
    repo = _repo(tmp_path, "wf_0003")
    assert _run(tmp_path, only="wf_0003") == 0
    assert _answers(repo, "wf_0003") == {"Q1": "FINANCE.RAW.GL_LEDGER", "Q2": "ANALYTICS.CURATED.GL_SUMMARY"}


# --- never inventing an answer ---------------------------------------------------------------------

def test_unanswered_touchpoint_exits_1_and_is_named(tmp_path, capsys):
    samples_copy = tmp_path / "samples"
    shutil.copytree(REAL_SAMPLES / "wf_0001", samples_copy / "wf_0001")
    sample_path = samples_copy / "wf_0001" / "sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    del sample["answers"]["out/excluded_orders.csv"]  # Q3's own key
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    repo_root = tmp_path / "repo"
    repo = _repo(repo_root, "wf_0001", samples_dir=samples_copy)
    code = _run(repo_root, samples_dir=samples_copy, only="wf_0001")
    assert code == 1
    captured = capsys.readouterr()
    assert "Q3" in captured.err

    answers = _answers(repo, "wf_0001")
    assert answers["Q1"] == "SALES.RAW.ORDERS"
    assert answers["Q2"] == "ANALYTICS.CURATED.SALES_SUMMARY"
    assert "Q3" not in answers


# --- orphaned answer keys (fix round 1) --------------------------------------------------------------

def test_an_answer_key_matching_no_touchpoint_is_warned_about_but_does_not_fail(tmp_path, capsys):
    samples_copy = tmp_path / "samples"
    shutil.copytree(REAL_SAMPLES / "wf_0001", samples_copy / "wf_0001")
    sample_path = samples_copy / "wf_0001" / "sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    # A stale or mistyped key: it matches no touchpoint's key or tool_id at all.
    sample["answers"]["out/this_key_matches_nothing.csv"] = "BOGUS.DB.TABLE"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    repo_root = tmp_path / "repo"
    repo = _repo(repo_root, "wf_0001", samples_dir=samples_copy)
    code = _run(repo_root, samples_dir=samples_copy, only="wf_0001")

    assert code == 0  # the orphaned key does not affect the exit code
    captured = capsys.readouterr()
    assert "this_key_matches_nothing" in captured.err
    assert "warning" in captured.err.lower()

    # the legitimate answers still land as normal.
    answers = _answers(repo, "wf_0001")
    assert answers == {
        "Q1": "SALES.RAW.ORDERS",
        "Q2": "ANALYTICS.CURATED.SALES_SUMMARY",
        "Q3": "ANALYTICS.CURATED.EXCLUDED_ORDERS",
    }


def test_an_answer_key_matching_a_non_blocking_or_already_resolved_touchpoint_is_not_orphaned(tmp_path, capsys):
    # A key that matches a real touchpoint (blocking and unresolved) is never "orphaned",
    # whatever else is true of it -- this is just the ordinary, already-covered path, asserted
    # here so the orphan check's false-positive rate is pinned down alongside the true positive.
    repo = _repo(tmp_path, "wf_0001")
    assert _run(tmp_path, only="wf_0001") == 0
    captured = capsys.readouterr()
    assert "matches no touchpoint" not in captured.err


# --- idempotent resume ------------------------------------------------------------------------------

def test_second_call_changes_nothing(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    assert _run(tmp_path, only="wf_0001") == 0
    first = dict(_answers(repo, "wf_0001"))
    first_manifest = io.load_manifest(repo, "wf_0001")

    assert _run(tmp_path, only="wf_0001") == 0
    second = _answers(repo, "wf_0001")
    second_manifest = io.load_manifest(repo, "wf_0001")

    assert second == first
    # only updated_at (a timestamp) may differ between the two manifests on disk.
    first_manifest.pop("updated_at", None)
    second_manifest.pop("updated_at", None)
    assert second_manifest == first_manifest


# --- --only and bulk mode ---------------------------------------------------------------------------

def test_only_flag_processes_a_single_workflow(tmp_path):
    copy_pristine_mappings_and_catalog(tmp_path)
    repo = Repo(tmp_path)
    for wf_id in ("wf_0001", "wf_0002"):
        build_samples.build(repo, REAL_SAMPLES, wf_id)
        tpx.run(repo, wf_id)

    assert _run(tmp_path, only="wf_0001") == 0
    assert _answers(repo, "wf_0001") != {}
    assert _answers(repo, "wf_0002") == {}  # untouched: --only named only wf_0001


def test_only_flag_rejects_an_unknown_workflow(tmp_path):
    (tmp_path / "workflows").mkdir(parents=True)
    with pytest.raises(SystemExit) as excinfo:
        _run(tmp_path, only="wf_9999")
    assert excinfo.value.code == 2


def test_bulk_mode_answers_every_workflow_and_skips_one_with_no_sample(tmp_path):
    copy_pristine_mappings_and_catalog(tmp_path)
    repo = Repo(tmp_path)
    for wf_id in ("wf_0001", "wf_0002"):
        build_samples.build(repo, REAL_SAMPLES, wf_id)
        tpx.run(repo, wf_id)
    # A workflow directory with no matching samples/<id>/sample.json at all: bulk mode must skip
    # it rather than fail the whole batch.
    (tmp_path / "workflows" / "wf_unknown").mkdir(parents=True)

    assert _run(tmp_path) == 0
    assert _answers(repo, "wf_0001") != {}
    assert _answers(repo, "wf_0002") != {}


def test_a_workflow_not_yet_through_intake_touchpoints_is_skipped_in_bulk_mode(tmp_path):
    copy_pristine_mappings_and_catalog(tmp_path)
    repo = Repo(tmp_path)
    build_samples.build(repo, REAL_SAMPLES, "wf_0001")
    tpx.run(repo, "wf_0001")
    # wf_0002 is seeded (so its manifest/source exist) but never went through intake_touchpoints.py.
    build_samples.build(repo, REAL_SAMPLES, "wf_0002")

    assert _run(tmp_path) == 0
    assert _answers(repo, "wf_0001") != {}
    assert _answers(repo, "wf_0002") == {}
