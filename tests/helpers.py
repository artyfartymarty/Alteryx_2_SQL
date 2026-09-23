"""Shared test fixtures (plan task 14). `prepare_workflow` builds one of the seed samples all the
way to a `READY` intake and copies its hand-migrated (canned) segment artifacts into place, so an
end-to-end test can call `validate_segment` against something a translator actually produced.

Nothing here has run against a real Snowflake account or Alteryx: `build_samples.build` chains the
parser, segmenter and Alteryx simulator (all local doubles), and intake is driven with the sample's
own recorded answers rather than a human at a prompt.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import intake_prompt
import intake_touchpoints
from dev import build_samples
from lib import io
from lib.paths import Repo
from lib.sample_answers import answer_for as _answer_for

ROOT = Path(__file__).parents[1]
SAMPLES = ROOT / "samples"

# intake_prompt._default_write_mode's own mapping (program spec §5.4 / plan contract C6), kept here
# rather than imported so this helper does not reach into that module's private names: an answer
# with no recorded write_mode falls back to "overwrite", same as an ordinary interactive default.
_MODE_DEFAULTS = {"update_insert": "merge", "truncate_append": "overwrite"}


def _write_mode_for(touchpoint: dict) -> str:
    mode = touchpoint.get("write_mode")
    return _MODE_DEFAULTS.get(mode, mode or "overwrite")


def copy_pristine_mappings_and_catalog(dest_root: Path) -> None:
    """Copies the real `mappings/` and `catalog/` directories into `dest_root`, then blanks the
    copied `mappings/global.yaml`'s `sources`/`outputs` back to `{}` -- every other key (`program`,
    `session`, `tolerances`, `accepted_diff_classes`, and their comments) is kept exactly as
    written.

    F1b (coordinator ruling): a test's fixture must never read *program state* off the live tree,
    only its schema/shape -- `mappings/global.yaml` at the repo root is supposed to stay pristine
    (`tests/test_foundations.py::test_global_yaml_matches_program_spec_plus_task_additions` pins
    that down), but nothing before this helper existed *proved* a test's fixture was pristine
    independently of that ever holding true; a real run in the repo root, or a bug in
    `intake_prompt.py`'s promotion, could otherwise leave the live file mutated and every one of
    this suite's `shutil.copytree(ROOT / "mappings", ...)` call sites would silently start every
    test from whatever was polluted onto it. This is the ONE place that copytree happens now --
    every other site in the suite calls this helper instead.
    """
    shutil.copytree(ROOT / "mappings", dest_root / "mappings")
    shutil.copytree(ROOT / "catalog", dest_root / "catalog")
    global_path = dest_root / "mappings" / "global.yaml"
    doc = io.read_yaml(global_path) or {}
    doc["sources"] = {}
    doc["outputs"] = {}
    io.write_global_mappings(global_path, doc)


def prepare_workflow(tmp_path: Path, wf_id: str) -> Repo:
    """Builds `wf_id` from `samples/<wf_id>/` into a fresh `Repo` under `tmp_path`, resolves intake
    with the sample's own answers, and copies its canned `contract.json`/`proc.sql` -- plus
    `proc.py` where the segment has one -- into every segment `segments/order.json` names. Skips
    the test outright when `samples/<wf_id>/canned/segments/` does not exist yet (plan Task 13
    writes it).

    `proc.py` is the source of truth of a `snowpark` segment (`contract.json`'s `target`), and
    `proc.sql` there is only the wrapper `render_snowpark.py` produces from it; a `sql` segment has
    no `proc.py` at all, so that one file is copied when it exists rather than required.

    A dbt sample (`samples/<wf_id>/canned/dbt/` exists: `output_kind: dbt`, design §4.3) has no
    procedure per segment at all: only each segment's `contract.json` is copied, and the whole
    canned project goes to `workflows/<wf_id>/dbt/`, exactly where a translator writes it.
    """
    canned_dir = SAMPLES / wf_id / "canned" / "segments"
    if not canned_dir.is_dir():
        pytest.skip(f"samples/{wf_id}/canned not written yet (plan Task 13)")
    dbt_canned = SAMPLES / wf_id / "canned" / "dbt"

    repo = Repo(tmp_path)
    copy_pristine_mappings_and_catalog(tmp_path)
    build_samples.build(repo, SAMPLES, wf_id)

    touchpoints = intake_touchpoints.run(repo, wf_id)
    sample = io.read_json(SAMPLES / wf_id / "sample.json")
    raw_answers = sample.get("answers") or {}

    answers: list[dict] = []
    for touchpoint in touchpoints:
        if not touchpoint["blocking"] or touchpoint["resolved"] is not None:
            continue
        snowflake = _answer_for(touchpoint, raw_answers)
        if snowflake is None:
            continue
        answer = {"id": touchpoint["id"], "action": "map", "snowflake": snowflake}
        if touchpoint["kind"] == "output":
            answer["write_mode"] = _write_mode_for(touchpoint)
            answer["keys"] = list(touchpoint.get("keys") or [])
        answers.append(answer)

    result = intake_prompt.apply_answers(repo, wf_id, touchpoints, answers, "sample")
    assert result["status"] == "READY", (
        f"{wf_id}: intake did not reach READY from sample.json's answers "
        f"(status {result['status']}, conflicts {result['conflicts']})")

    order = io.read_json(repo.wf(wf_id, "segments", "order.json"))
    names = ("contract.json",) if dbt_canned.is_dir() else ("contract.json", "proc.sql", "proc.py")
    for wave in order:
        for seg in wave:
            seg_canned = canned_dir / seg
            for name in names:
                source = seg_canned / name
                if not source.is_file():
                    if name == "proc.py":
                        continue  # a sql segment has none; proc.sql is the whole translation
                    raise FileNotFoundError(
                        f"samples/{wf_id}/canned/segments/{seg}/{name} is missing")
                destination = repo.seg(wf_id, seg, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(source, destination)
    if dbt_canned.is_dir():
        shutil.copytree(dbt_canned, repo.wf(wf_id, "dbt"))

    return repo


def overlay_dbt_project(repo: Repo, wf_id: str, dest: Path, replacements: dict[str, Path]) -> Path:
    """A copy of the workflow's dbt project at `dest` with some files replaced -- how a broken
    dbt variant is validated without touching the project under test."""
    shutil.copytree(repo.wf(wf_id, "dbt"), dest)
    for relative, source in replacements.items():
        shutil.copy(source, dest / relative)
    return dest
