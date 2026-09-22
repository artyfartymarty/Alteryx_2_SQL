"""Fix round 4 (a regression from round 3, found by the round-2+3 re-review): "conflict amnesia".

Round 3 made a no-op resume idempotent by short-circuiting an answer whose FQN already matches the
entry in `intake/mappings.yaml`. But the short-circuit `continue`d BEFORE `promote()`, the only
place that compares an answer with `mappings/global.yaml`. So a genuine cross-workflow conflict was
reported `NEEDS_HUMAN` on the run that first wrote the entry, and the very next identical resume --
nothing changed, nobody reconciled anything -- reported `READY`.

Coordinator ruling: intake STATUS is a pure function of the files. The global-mapping consistency
check runs for every mapped answer on every run, whether or not the local entry changed; a
disagreement stays `NEEDS_HUMAN` until a human reconciles it; FQN comparison is case-insensitive.
"""
from __future__ import annotations

from pathlib import Path

import intake_prompt as ip
import intake_touchpoints as tpx
from dev import build_samples
from lib import io
from lib.paths import Repo
from tests.helpers import copy_pristine_mappings_and_catalog

ROOT = Path(__file__).parents[1]
KEY = "sales/orders.yxdb"


def _repo(tmp_path, wf_id="wf_0001"):
    copy_pristine_mappings_and_catalog(tmp_path)
    repo = Repo(tmp_path)
    build_samples.build(repo, ROOT / "samples", wf_id)
    return repo


def scripted(answers):
    it = iter(answers)
    return lambda prompt: next(it)


def _map_all_three(repo):
    tpx.run(repo, "wf_0001")
    ask = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "",
                    "ANALYTICS.CURATED.EXCLUDED_ORDERS", ""])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "READY"


def _resume(repo, said=None):
    out = (lambda s: said.append(s)) if said is not None else (lambda s: None)
    return ip.run(repo, "wf_0001", interactive=False, ask=None, out=out, user="wf_owner")


def _claim_key_elsewhere(repo, fqn):
    """A colleague's workflow claims the same normalized key for a different table."""
    g = io.read_yaml(repo.global_mappings)
    g["sources"][KEY]["snowflake"] = fqn
    io.write_yaml(repo.global_mappings, g)


def _status(repo):
    return io.read_json(repo.wf("wf_0001", "manifest.json"))["status"]["intake"]


def test_a_global_conflict_is_not_forgotten_on_a_no_op_resume(tmp_path):
    repo = _repo(tmp_path)
    _map_all_three(repo)
    local_fqn = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))["sources"][KEY]["snowflake"]
    _claim_key_elsewhere(repo, "SOMEOTHER.SCHEMA.TABLE")

    # Every resume sees the same files, so every resume must give the same status.
    for n in range(3):
        assert _resume(repo) == "NEEDS_HUMAN", f"resume #{n + 1} forgot the conflict"
        assert _status(repo) == "NEEDS_HUMAN"

    # Neither side is silently overwritten: the human decides which one is right.
    assert io.read_yaml(repo.global_mappings)["sources"][KEY]["snowflake"] == "SOMEOTHER.SCHEMA.TABLE"
    assert io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))["sources"][KEY]["snowflake"] == local_fqn


def test_the_conflict_is_named_in_open_questions_on_every_resume(tmp_path):
    repo = _repo(tmp_path)
    _map_all_three(repo)
    _claim_key_elsewhere(repo, "SOMEOTHER.SCHEMA.TABLE")

    for _ in range(2):
        _resume(repo)
        text = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
        assert "CONFLICT" in text and "SOMEOTHER.SCHEMA.TABLE" in text


def test_reconciling_global_clears_the_conflict(tmp_path):
    repo = _repo(tmp_path)
    _map_all_three(repo)
    local_fqn = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))["sources"][KEY]["snowflake"]
    _claim_key_elsewhere(repo, "SOMEOTHER.SCHEMA.TABLE")
    assert _resume(repo) == "NEEDS_HUMAN"

    _claim_key_elsewhere(repo, local_fqn)          # the human settles it in global.yaml
    assert _resume(repo) == "READY"
    assert _resume(repo) == "READY"


def test_a_case_only_difference_with_global_is_not_a_conflict(tmp_path):
    repo = _repo(tmp_path)
    _map_all_three(repo)
    local_fqn = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))["sources"][KEY]["snowflake"]
    _claim_key_elsewhere(repo, local_fqn.lower())

    assert _resume(repo) == "READY"


def test_the_consistency_check_keeps_a_no_op_resume_byte_identical(tmp_path):
    """Round 3's guarantee still holds with the check running on every answer."""
    repo = _repo(tmp_path)
    _map_all_three(repo)
    paths = [repo.wf("wf_0001", "intake", "mappings.yaml"),
             repo.wf("wf_0001", "intake", "open_questions.md"), repo.global_mappings]
    assert _resume(repo) == "READY"
    before = [p.read_bytes() for p in paths]

    for _ in range(3):
        assert _resume(repo) == "READY"
        assert [p.read_bytes() for p in paths] == before
