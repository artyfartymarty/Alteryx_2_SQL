"""The interactive intake `output_target` question (design §3.3): asked once, only when
`mappings/global.yaml` has no `program.output_target` AND the manifest has none either -- the
program-level questions already exist for target database/schema, this is one more. Task F, brief
Step 1 (test file `tests/test_intake_output_target.py`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import intake_prompt as ip
import intake_touchpoints as tpx
from dev import build_samples
from lib import io
from lib.paths import Repo
from tests.helpers import copy_pristine_mappings_and_catalog

ROOT = Path(__file__).parents[1]
WF = "wf_0001"


def scripted(answers):
    it = iter(answers)
    said = []
    return (lambda prompt: (said.append(prompt), next(it))[1]), said


def _repo_without_program_output_target(root: Path) -> Repo:
    """The fixture `tests/test_intake_prompt.py::repo` builds, with `program.output_target`
    then removed from the copied `mappings/global.yaml` -- through `io.write_global_mappings`,
    which only ever preserves an EXISTING file's preamble verbatim, so the file is removed first:
    with no file on disk to preserve, `write_global_mappings` writes `doc` (minus the key) in full.
    """
    copy_pristine_mappings_and_catalog(root)
    repo = Repo(root)
    build_samples.build(repo, ROOT / "samples", WF)
    doc = io.read_yaml(repo.global_mappings)
    del doc["program"]["output_target"]
    repo.global_mappings.unlink()
    io.write_global_mappings(repo.global_mappings, doc)
    return repo


@pytest.fixture
def repo(tmp_path):
    r = _repo_without_program_output_target(tmp_path)
    tpx.run(r, WF)
    return r


def test_the_question_is_asked_first_when_global_yaml_has_no_value(repo):
    ask, said = scripted(["dbt", "n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(repo, WF, interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert "Output target" in said[0]
    assert io.load_manifest(repo, WF)["output_target"] == "dbt"


def test_enter_takes_procedures(repo):
    ask, _ = scripted(["", "n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(repo, WF, interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert io.load_manifest(repo, WF)["output_target"] == "procedures"


def test_an_invalid_answer_is_asked_again_once(tmp_path):
    # Two independent repos: once the first bullet's answer is recorded, the question would no
    # longer be asked at all in a second run against the SAME repo (manifest.output_target set) --
    # a fresh repo per scenario is the only way to exercise both retry outcomes.
    ok = _repo_without_program_output_target(tmp_path / "ok")
    tpx.run(ok, WF)
    ask, _ = scripted(["sql", "dbt", "n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(ok, WF, interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert io.load_manifest(ok, WF)["output_target"] == "dbt", "a second, valid answer is accepted"

    bad = _repo_without_program_output_target(tmp_path / "bad")
    tpx.run(bad, WF)
    out: list[str] = []
    ask, _ = scripted(["x", "y", "n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(bad, WF, interactive=True, ask=ask, out=out.append, user="wf_owner")
    assert "output_target" not in io.load_manifest(bad, WF)
    assert any("no output target recorded" in line for line in out)


def test_never_asked_when_global_yaml_has_a_value(tmp_path):
    copy_pristine_mappings_and_catalog(tmp_path)
    r = Repo(tmp_path)
    build_samples.build(r, ROOT / "samples", WF)
    tpx.run(r, WF)
    ask, said = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(r, WF, interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert not any("Output target" in s for s in said)
    assert "output_target" not in io.load_manifest(r, WF), "nothing asked, so nothing recorded"


def test_never_asked_when_the_manifest_already_has_one(repo):
    m = io.load_manifest(repo, WF)
    m["output_target"] = "dbt"
    io.save_manifest(repo, m)
    ask, said = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(repo, WF, interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert not any("Output target" in s for s in said)
    assert io.load_manifest(repo, WF)["output_target"] == "dbt"


def test_never_asked_non_interactively(repo):
    ip.run(repo, WF, interactive=False, user="wf_owner")
    assert "output_target" not in io.load_manifest(repo, WF)


def test_a_closed_stdin_is_no_answer(repo):
    def ask(_prompt):
        raise EOFError

    status = ip.run(repo, WF, interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert "output_target" not in io.load_manifest(repo, WF)
    assert status == "WAITING_FOR_ANSWERS", "the closed stdin reached prompt_touchpoints too"
