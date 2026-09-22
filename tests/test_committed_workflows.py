"""Checks on the committed `workflows/` tree -- the worked examples from the offline end-to-end
run (plan Task 17; `task-15-int-report.md`'s command sequence; coordinator notes in
`task-17-addendum.md`). Unlike every other test file here, this one asserts against the REAL,
git-tracked `workflows/` directory at the repo root, not a `tmp_path` fixture: these are the
actual worked examples a reader of this repo opens, and they must stay correct as later tasks
touch the scripts that produced them.

Nothing here has run on Snowflake or Alteryx (repo-wide honesty rule): `workflows/` is the
`--runner mock --no-interactive` offline run's own output, replaying hand-written canned
artifacts (Task 13) and running every deterministic script for real against DuckDB.
"""
from __future__ import annotations

import getpass
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "workflows"

EXPECTED_TERMINAL = {
    "wf_0001": "VALIDATED",
    "wf_0002": "VALIDATED",
    "wf_0003": "VALIDATED",
    "wf_0004": "VALIDATED",
    "wf_0005": "MANUAL",
}
WORKFLOW_IDS = sorted(EXPECTED_TERMINAL)
VALIDATED_WORKFLOW_IDS = [wf for wf, terminal in EXPECTED_TERMINAL.items() if terminal == "VALIDATED"]

VALID_VERDICTS = {"PASS", "PASS_WITH_ACCEPTED_DIFF"}

# An absolute path of *this* PC -- never legitimate in a committed artifact (task-17-addendum.md).
_ABS_PATH_RE = re.compile(r"[a-zA-Z]:[\\/]Users[\\/]", re.IGNORECASE)


def _require_workflows_dir() -> None:
    if not WORKFLOWS.is_dir():
        pytest.fail("workflows/ is missing: run the Task 17 offline sequence from "
                     "task-15-int-report.md's 'Command sequence for Task 17' before running this "
                     "test (scripts/dev/build_samples.py seed, orchestrate.ts twice with "
                     "scripts/dev/answer_samples.py in between)")


@pytest.fixture(autouse=True, scope="module")
def _workflows_dir_must_exist():
    _require_workflows_dir()


def _manifest(wf: str) -> dict:
    return json.loads((WORKFLOWS / wf / "manifest.json").read_text(encoding="utf-8"))


def _tracked_workflow_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "workflows"], cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None  # a binary artifact (e.g. a generated .yxdb): not this check's concern


# --- terminal states (task-17-addendum.md: wf_0001-0004 VALIDATED, wf_0005 MANUAL) --------------

@pytest.mark.parametrize("wf", WORKFLOW_IDS)
def test_manifest_reaches_the_expected_terminal_state(wf):
    manifest = _manifest(wf)
    assert manifest["id"] == wf
    assert manifest["status"]["translate"] == EXPECTED_TERMINAL[wf]
    assert manifest["status"]["parse"] in ("PARSED", "RECOVERED")
    assert manifest["status"]["intake"] == "READY"
    assert manifest["status"]["analyze"] == "DONE"


@pytest.mark.parametrize("wf", VALIDATED_WORKFLOW_IDS)
def test_a_validated_workflow_also_finished_golden_and_document(wf):
    manifest = _manifest(wf)
    assert manifest["status"]["golden"] == "DONE"
    assert manifest["status"]["document"] == "DONE"
    assert manifest["tier"] in ("T1", "T2")
    assert manifest["golden_sets"], wf


@pytest.mark.parametrize("wf", VALIDATED_WORKFLOW_IDS)
def test_a_validated_workflow_has_every_segment_passing(wf):
    segments = _manifest(wf).get("segment_status") or {}
    assert segments, f"{wf} has no segment_status at all"
    assert all(v == "PASS" for v in segments.values()), segments


def test_the_manual_t3_workflow_never_reached_golden_or_document():
    """docs/spec/01-copilot-setup.md's state diagram draws `analyze --> MANUAL: tier T3` as a dead
    end: golden/document/pr never apply, and `orchestrator/stages.ts`'s `shouldRun` refuses them
    outright for a T3 workflow (task-15-int-report.md, Defect 4)."""
    manifest = _manifest("wf_0005")
    assert manifest["tier"] == "T3"
    assert "golden" not in manifest["status"]
    assert "document" not in manifest["status"]
    assert manifest["status"].get("translate") == "MANUAL"
    assert manifest["reasons"]["translate"] == "tier-T3"


# --- every committed validation report is a real pass, not a fabricated one ---------------------

def test_every_committed_validation_json_carries_a_passing_verdict():
    checked = 0
    for path in sorted(WORKFLOWS.glob("*/segments/*/validation*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        verdict = report.get("verdict")
        assert verdict in VALID_VERDICTS, f"{path.relative_to(ROOT)}: verdict {verdict!r}"
        checked += 1
    assert checked > 0, "no validation*.json files found under workflows/ -- was Step 1 ever run?"


# --- nothing machine-local or volatile-by-design is tracked --------------------------------------

def test_no_duckdb_or_audit_log_files_are_tracked():
    tracked = _tracked_workflow_files()
    assert tracked, "workflows/ has nothing tracked in git -- was it committed?"
    for path in tracked:
        lower = path.lower()
        assert not lower.endswith(".duckdb"), path
        assert not lower.endswith(".duckdb.wal"), path
        assert not lower.endswith("audit.jsonl"), path
        assert "__pycache__" not in lower, path


# --- no absolute path or OS login name of this PC leaked into a committed artifact ---------------

def test_no_absolute_path_of_this_pc_is_committed_anywhere_under_workflows():
    tracked = _tracked_workflow_files()
    assert tracked
    for rel in tracked:
        text = _read_text_or_none(ROOT / rel)
        if text is None:
            continue
        assert not _ABS_PATH_RE.search(text), f"{rel} contains an absolute path of this PC"


_GENERIC_LOGINS = {"user", "admin", "root", "runner", "build", "test", "guest", "owner"}


def test_only_the_fixture_owner_name_appears_never_the_os_login_name():
    """`wf_owner` is every `samples/wf_000N/sample.json`'s hand-authored fixture value for the
    workflow owner (asserted on by name in
    tests/test_samples_wellformed.py::test_sample_json_has_the_planned_keys) -- it flows verbatim
    into `manifest.json["source"]["owner"]`, `open_questions.md`'s header and the generated docs
    for every one of these five workflows, on every machine, and is not the leak this check exists
    to catch.

    The OS login name (`getpass.getuser()`) is a different thing entirely: it must never appear,
    because it would mean a non-interactive resume (the orchestrator's own `--no-interactive`
    calls, and this offline sample run) silently baked whoever's account happened to run it into a
    committed artifact -- exactly the `intake/mappings.yaml["confirmed_by"]` defect Task 17 found
    and fixed in `scripts/intake_prompt.py`. The probe is the login name of whoever runs the suite;
    a name too short or too generic to mean anything skips instead of guessing."""
    login = getpass.getuser()
    if len(login) < 4 or login.lower() in _GENERIC_LOGINS:
        pytest.skip(f"OS login name {login!r} is too generic to probe for")
    tracked = _tracked_workflow_files()
    assert tracked
    for rel in tracked:
        text = _read_text_or_none(ROOT / rel)
        if text is None:
            continue
        assert login.lower() not in text.lower(), f"{rel} leaks the OS login name {login!r}"
