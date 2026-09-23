"""Checks on the committed `workflows/` tree -- the worked examples from the offline end-to-end
run (plan Task 17, re-run for six samples by output-targets Task 6B and for all seven by phase-2
Task G; README §6's command sequence). Unlike every other test file here, this one asserts against
the REAL, git-tracked `workflows/` directory at the repo root, not a `tmp_path` fixture: these are the
actual worked examples a reader of this repo opens, and they must stay correct as later tasks
touch the scripts that produced them.

Nothing here has run on Snowflake or Alteryx (repo-wide honesty rule): `workflows/` is the
`--runner mock --no-interactive` offline run's own output, replaying hand-written canned
artifacts (Task 13) and running every deterministic script for real against DuckDB.

The last section is the one exception to "only `workflows/`": the hand-off scan for machine
paths, login names and scratchpad references runs over every shipped tree, because the final
whole-branch review (I6) found two such references in `scripts/` and `tests/`, where no test was
looking.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import render_snowpark
from lib import dbt_project, io

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "workflows"

EXPECTED_TERMINAL = {
    "wf_0001": "VALIDATED",
    "wf_0002": "VALIDATED",
    "wf_0003": "VALIDATED",
    "wf_0004": "VALIDATED",
    "wf_0005": "MANUAL",
    "wf_0006": "VALIDATED",
    "wf_0007": "VALIDATED",
}
WORKFLOW_IDS = sorted(EXPECTED_TERMINAL)
VALIDATED_WORKFLOW_IDS = [wf for wf, terminal in EXPECTED_TERMINAL.items() if terminal == "VALIDATED"]

#: The committed workflows whose output kind is `dbt`: one project under `dbt/`, no procedure.
DBT_WORKFLOW_IDS = ["wf_0007"]

VALID_VERDICTS = {"PASS", "PASS_WITH_ACCEPTED_DIFF"}

# `sql` is the HIGHEST target and `manual` the lowest -- the analyzer may only move a segment DOWN
# this ladder (output-targets design §3.2; the same table as `orchestrator/stages.ts`'s
# `TARGET_RANK`, which is what actually enforced it during the run that produced this tree).
TARGET_RANK = {"manual": 0, "snowpark": 1, "sql": 2}

# `segments/targets.json.output_kind`, mirrored into the manifest by the orchestrator
# (docs/reference/output-targets.md §1). wf_0007 is the committed `dbt` workflow (DBT_WORKFLOW_IDS);
# every other one is `procedures`.
OUTPUT_KINDS = {"procedures", "dbt"}

# An absolute path of *this* PC -- never legitimate in a committed artifact (task-17-addendum.md).
_ABS_PATH_RE = re.compile(r"[a-zA-Z]:[\\/]Users[\\/]", re.IGNORECASE)


def _require_workflows_dir() -> None:
    if not WORKFLOWS.is_dir():
        pytest.fail("workflows/ is missing: run the offline sequence in README §6 before "
                     "running this test (scripts/dev/build_samples.py seed, orchestrate.ts twice "
                     "with scripts/dev/answer_samples.py in between)")


@pytest.fixture(autouse=True, scope="module")
def _workflows_dir_must_exist():
    _require_workflows_dir()


def _manifest(wf: str) -> dict:
    return json.loads((WORKFLOWS / wf / "manifest.json").read_text(encoding="utf-8"))


def _json(wf: str, *parts: str) -> dict:
    return json.loads((WORKFLOWS / wf / Path(*parts)).read_text(encoding="utf-8"))


def _segment_ids(wf: str) -> list[str]:
    """Every segment the run actually produced, in wave order (`segments/order.json`)."""
    return [seg for wave in _json(wf, "segments", "order.json") for seg in wave]


def _snowpark_runtime() -> str:
    """`program.snowpark_runtime` exactly as `render_snowpark.py` resolves it, from the same
    `mappings/global.yaml` the offline run read."""
    program = (io.read_yaml(ROOT / "mappings" / "global.yaml") or {}).get("program") or {}
    return str(program.get("snowpark_runtime") or "3.11")


def _tracked(*trees: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", *trees], cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _tracked_workflow_files() -> list[str]:
    return _tracked("workflows")


def _read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None  # a binary artifact (e.g. a generated .yxdb): not this check's concern


# --- terminal states (README §6: wf_0001-4, wf_0006 and wf_0007 VALIDATED, wf_0005 MANUAL) -----

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


# --- output targets: targets.json, output_kind, and the per-target artefacts --------------------

@pytest.mark.parametrize("wf", WORKFLOW_IDS)
def test_every_committed_workflow_has_a_targets_json(wf):
    """`scripts/target_check.py` runs in EVERY analyze, before the analyzer agent, and always
    writes `segments/targets.json` (output-targets design §3.1 -- it is written even on exit 1,
    the `unknown` nodes case, precisely so the analyzer can see them). A committed workflow
    without one was produced by a pipeline that predates the target decision."""
    targets = _json(wf, "segments", "targets.json")
    assert targets["output_kind"] in OUTPUT_KINDS, targets
    assert targets["preference"] in OUTPUT_KINDS, targets
    proposals = targets["segments"]
    assert set(proposals) == set(_segment_ids(wf)), (
        f"{wf}: targets.json proposes for {sorted(proposals)} but segments/order.json has "
        f"{_segment_ids(wf)}")
    assert all(target in TARGET_RANK for target in proposals.values()), proposals


@pytest.mark.parametrize("wf", WORKFLOW_IDS)
def test_every_committed_workflow_records_the_decided_output_kind(wf):
    """Design §3.3: `targets.json.output_kind` is MIRRORED into `manifest.json.output_kind`
    (`orchestrator/stages.ts`'s `stageAnalyze`). That holds for every workflow the analyzer
    finished, tier-T3 `wf_0005` included -- it has no `contract.json` for §3.2's lower-only check
    to run against, but `target_check.py` wrote its `targets.json` like any other workflow's, and
    the kind it decided is the kind the manifest records."""
    manifest = _manifest(wf)
    proposed = _json(wf, "segments", "targets.json")["output_kind"]
    assert manifest["output_kind"] in OUTPUT_KINDS
    if manifest["tier"] == "T3":
        assert manifest["output_kind"] == proposed, f"{wf}: T3 mirrors targets.json verbatim"
        return
    # §3.2's one adjustment: a `dbt` proposal survives into the manifest only while every contract
    # is still plain `sql`, because a segment lowered to Snowpark makes the workflow non-dbt.
    contracts = [json.loads(p.read_text(encoding="utf-8")).get("target")
                 for p in sorted((WORKFLOWS / wf / "segments").glob("*/contract.json"))]
    expected = "dbt" if proposed == "dbt" and all(t == "sql" for t in contracts) else "procedures"
    assert manifest["output_kind"] == expected, (
        f"{wf}: manifest says {manifest['output_kind']!r}; targets.json proposed {proposed!r} and "
        f"the contracts are {contracts}")


@pytest.mark.parametrize("wf", WORKFLOW_IDS)
def test_no_committed_contract_raises_its_proposed_target(wf):
    """Design §3.2: the analyzer may **lower** a target (`sql` → `snowpark`, or either →
    `manual`) and may never raise one. Every committed contract is therefore at or below its
    `targets.json` proposal on the `sql > snowpark > manual` ladder."""
    proposals = _json(wf, "segments", "targets.json")["segments"]
    checked = 0
    for seg in _segment_ids(wf):
        contract_path = WORKFLOWS / wf / "segments" / seg / "contract.json"
        if not contract_path.is_file():
            continue  # tier T3 stops at MANUAL, so the analyzer never wrote a contract for it
        target = json.loads(contract_path.read_text(encoding="utf-8")).get("target")
        assert target in TARGET_RANK, f"{wf}/{seg}: contract.json target {target!r}"
        assert TARGET_RANK[target] <= TARGET_RANK[proposals[seg]], (
            f"{wf}/{seg}: contract raised target_check's {proposals[seg]!r} to {target!r}")
        checked += 1
    if _manifest(wf).get("tier") != "T3":
        assert checked == len(_segment_ids(wf)), f"{wf}: {checked} contracts for its segments"


def test_a_committed_snowpark_segment_carries_proc_py_its_rendered_proc_sql_and_a_snowpark_report():
    """A `snowpark` segment's source of truth is `proc.py`; `proc.sql` is only the `LANGUAGE
    PYTHON` wrapper `render_snowpark.render` produces from it, and the validator that judged it is
    `scripts/validate_snowpark.py`, which is the only thing in this repo that writes
    `"target": "snowpark"` into a validation report (`validate_snowpark.validate_snowpark`'s
    `extra=`). Re-rendering here catches a `proc.py` that was edited without re-running
    `render_snowpark.py` -- the committed wrapper would otherwise deploy a stale body."""
    runtime = _snowpark_runtime()
    checked = 0
    for contract_path in sorted(WORKFLOWS.glob("*/segments/*/contract.json")):
        if json.loads(contract_path.read_text(encoding="utf-8")).get("target") != "snowpark":
            continue
        seg_dir = contract_path.parent
        wf_id, seg = seg_dir.parents[1].name, seg_dir.name
        proc_py = seg_dir / "proc.py"
        assert proc_py.is_file(), f"{wf_id}/{seg}: target snowpark but no proc.py"
        assert (seg_dir / "proc.sql").read_text(encoding="utf-8") == \
            render_snowpark.render(proc_py.read_text(encoding="utf-8"), wf_id, seg, runtime), (
            f"{wf_id}/{seg}: proc.sql is not render_snowpark.render(proc.py, …) for runtime "
            f"{runtime}; re-run `python scripts/render_snowpark.py {wf_id} {seg}`")
        assert json.loads((seg_dir / "compile_check.json").read_text(encoding="utf-8")) \
            .get("target") == "snowpark", f"{wf_id}/{seg}: compile_check.json is not a snowpark one"
        assert json.loads((seg_dir / "validation.json").read_text(encoding="utf-8")) \
            .get("target") == "snowpark", (
            f"{wf_id}/{seg}: validation.json has no \"target\": \"snowpark\", so it was not "
            f"written by scripts/validate_snowpark.py")
        checked += 1
    assert checked, "no committed segment has target snowpark -- wf_0006/seg_02 should"


def test_a_committed_sql_segment_s_validation_report_carries_no_target_key():
    """`scripts/validate_segment.py` calls the shared `lib.validation.write_reports` with no
    `extra=`, so a SQL segment's report has no `target` key at all -- unlike
    `validate_snowpark.py`'s. That absence is what distinguishes the two validators on disk, so a
    `target` key appearing on a SQL segment would mean the wrong validator served it.

    Procedures workflows only: a dbt workflow's contracts say `sql` too (the lower-only rule keeps
    them there), but `validate_dbt.py` judged them and stamps `"target": "dbt"` -- pinned by the dbt
    test below."""
    checked = 0
    for contract_path in sorted(WORKFLOWS.glob("*/segments/*/contract.json")):
        if json.loads(contract_path.read_text(encoding="utf-8")).get("target") != "sql":
            continue
        if _manifest(contract_path.parents[2].name).get("output_kind") == "dbt":
            continue
        seg_dir = contract_path.parent
        assert not (seg_dir / "proc.py").exists(), \
            f"{seg_dir.parents[1].name}/{seg_dir.name}: a sql segment has a proc.py beside it"
        report = json.loads((seg_dir / "validation.json").read_text(encoding="utf-8"))
        assert "target" not in report, (
            f"{seg_dir.parents[1].name}/{seg_dir.name}: validation.json carries "
            f"target={report['target']!r}, so validate_segment.py did not write it")
        checked += 1
    assert checked, "no committed segment has target sql"


def _dbt_readme(wf_id: str) -> str:
    """`orchestrator/stages.ts`'s `dbtReadme(wfId)` -- the text the orchestrator writes to a dbt
    workflow's `procs/README.md` in place of `master.sql` (the node suite pins the TS side)."""
    return "\n".join([
        f"# {wf_id} — deploy as a dbt project",
        "",
        "Generated by orchestrate.ts. This workflow's output kind is `dbt`: there is no master.sql and no per-segment",
        "procedure. Nothing in this repository has run it against Snowflake.",
        "",
        "```",
        f"dbt run --project-dir workflows/{wf_id}/dbt --profiles-dir workflows/{wf_id}/dbt --target snowflake "
        "--vars '{\"src_schema\": \"<SRC>\", \"tgt_schema\": \"<TGT>\"}'",
        "```",
        "",
        "<SRC> is the schema that holds the mapped source tables under their logical names; <TGT> is where the models",
        f"are written. Every connection value comes from the SNOWFLAKE_* variables named in workflows/{wf_id}/dbt/profiles.yml.",
        "dbt-snowflake must be installed first; it is not part of requirements.txt.",
        "",
    ])


@pytest.mark.parametrize("wf", DBT_WORKFLOW_IDS)
def test_a_committed_dbt_workflow_is_a_project_with_a_readme_and_no_procedures(wf):
    """Design §4.3: a `dbt` workflow is translated ONCE, as one project under `workflows/<wf>/dbt/`,
    checked by `compile_check.py <wf> --target dbt` (`dbt/compile_check.json`), reviewed once
    (`dbt/review.json`) and run per golden set by `validate_dbt.py`, which still writes every
    segment's `validation.json` -- stamped `"target": "dbt"` -- so per-segment statuses are recorded.
    In place of `procs/master.sql` it gets `procs/README.md` with the §4.3 `dbt run` command. No
    segment has a procedure: a `proc.sql` or `proc.py` here would be an artefact nothing validated."""
    wf_dir = WORKFLOWS / wf
    manifest = _manifest(wf)
    assert manifest["output_kind"] == "dbt"
    assert manifest["output_target"] == "dbt"

    project = wf_dir / "dbt"
    for rel in (*dbt_project.PROJECT_FILES, "translation_notes.md"):
        assert (project / rel).is_file(), f"{wf}: dbt/{rel} is missing"
    compile_check = _json(wf, "dbt", "compile_check.json")
    assert compile_check["target"] == "dbt" and compile_check["status"] == "OK", compile_check
    assert _json(wf, "dbt", "review.json")["verdict"] == "PASS"
    assert (project / "profiles.yml").read_text(encoding="utf-8") == dbt_project.PROFILES_TEMPLATE

    readme = (wf_dir / "procs" / "README.md").read_text(encoding="utf-8")
    assert readme == _dbt_readme(wf)
    assert (f"dbt run --project-dir workflows/{wf}/dbt --profiles-dir workflows/{wf}/dbt --target snowflake "
            "--vars '{\"src_schema\": \"<SRC>\", \"tgt_schema\": \"<TGT>\"}'") in readme
    assert not (wf_dir / "procs" / "master.sql").exists()
    assert not list(wf_dir.glob("segments/*/proc.sql")), f"{wf}: a dbt workflow has a proc.sql"
    assert not list(wf_dir.glob("segments/*/proc.py")), f"{wf}: a dbt workflow has a proc.py"

    segments = _segment_ids(wf)
    assert segments
    for seg in segments:
        report = _json(wf, "segments", seg, "validation.json")
        assert report.get("target") == "dbt", f"{wf}/{seg}: validation.json was not written by validate_dbt.py"
        assert report["verdict"] in VALID_VERDICTS, f"{wf}/{seg}: {report['verdict']!r}"
    assert set(manifest["segment_status"]) == set(segments)


# --- every committed validation report is a real pass, not a fabricated one ---------------------

def test_every_committed_validation_json_carries_a_passing_verdict():
    checked = 0
    for path in sorted(WORKFLOWS.glob("*/segments/*/validation*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        verdict = report.get("verdict")
        assert verdict in VALID_VERDICTS, f"{path.relative_to(ROOT)}: verdict {verdict!r}"
        checked += 1
    assert checked > 0, "no validation*.json files found under workflows/ -- was Step 1 ever run?"


@pytest.mark.parametrize("wf", VALIDATED_WORKFLOW_IDS)
def test_every_validated_workflow_has_a_passing_chain_report(wf):
    """Task W1 made the stitched whole the VALIDATED gate: `validate_workflow.py` (procedures) or
    `validate_dbt.py` (dbt) runs every segment on its upstream's ACTUAL output and writes
    `validation_workflow.json` plus one `validation_workflow.<set>.json` per golden set. A committed
    VALIDATED workflow therefore carries a passing chain report with no divergence, and the chain,
    run twice from fresh backends (the second time with every input presented in reverse order), was
    idempotent. `deploy.py` refuses a workflow without it."""
    report = _json(wf, "validation_workflow.json")
    assert report["workflow"] == wf
    assert report["verdict"] in VALID_VERDICTS, report["verdict"]
    assert report["first_divergence"] is None, report["first_divergence"]
    assert report["divergence_kind"] is None, report["divergence_kind"]
    assert report["idempotent"] is True
    sets = _manifest(wf)["golden_sets"]
    assert set(report["sets"]) == set(sets)
    for golden_set in sets:
        per_set = _json(wf, f"validation_workflow.{golden_set}.json")
        assert per_set["golden_set"] == golden_set
        assert per_set["verdict"] in VALID_VERDICTS, f"{wf} {golden_set}: {per_set['verdict']!r}"
        assert per_set["first_divergence"] is None
    on_disk = sorted(p.name for p in (WORKFLOWS / wf).glob("validation_workflow.*.json"))
    assert on_disk == sorted(f"validation_workflow.{s}.json" for s in sets), on_disk
    # `validate_dbt.py` stamps its chain report like its segment reports; `validate_workflow.py` does not.
    expected_target = "dbt" if wf in DBT_WORKFLOW_IDS else None
    assert report.get("target") == expected_target, report.get("target")


@pytest.mark.parametrize("wf", WORKFLOW_IDS)
def test_every_committed_workflow_has_a_batch_plan_and_translated_ones_have_clean_seams(wf):
    """Task W2: every analyze runs `plan_batches.py` (`segments/batches.json`) before the analyzer,
    and -- once the analyzer has written contracts -- `check_seams.py` (`segments/seams.json`). Every
    sample is far under the default character budget, so it is ONE batch holding every segment (one
    analyzer call, as before W2). A tier-T3 workflow has no contracts, so its seams are never
    checked; every other workflow's seams are clean."""
    batches = _json(wf, "segments", "batches.json")["batches"]
    assert len(batches) == 1, batches
    assert batches[0]["segments"] == _segment_ids(wf)
    if _manifest(wf)["tier"] == "T3":
        return
    seams = _json(wf, "segments", "seams.json")
    assert seams["ok"] is True, seams
    assert all(seam["status"] == "ok" for seam in seams["seams"]), seams["seams"]


@pytest.mark.parametrize("wf", [wf for wf in VALIDATED_WORKFLOW_IDS if wf not in DBT_WORKFLOW_IDS])
def test_a_committed_master_procedure_quotes_its_scripting_body(wf):
    """`procs/master.sql` wraps its Snowflake Scripting body in `$$ … $$`, the shape of every
    segment's `proc.sql`: Snowflake CLI, SnowSQL and the Python connector do not parse an
    undelimited Scripting block (Task W2's follow-up; nothing here has run it on Snowflake)."""
    text = (WORKFLOWS / wf / "procs" / "master.sql").read_text(encoding="utf-8")
    assert "\nAS\n$$\nBEGIN\n" in text, text
    assert text.endswith("END;\n$$;\n"), text
    for seg in _segment_ids(wf):
        assert f"CALL MIG_WORK.{wf.upper().replace('_', '')}_{seg.upper()}(" in text, seg


# --- nothing machine-local or volatile-by-design is tracked --------------------------------------

def test_no_duckdb_or_audit_log_files_are_tracked():
    """Nor anything a dbt run leaves behind: `validate_dbt.py`'s per-set sandboxes
    (`dbt_sandbox_<set>.duckdb`), dbt's logs (`dbt/logs/`) and its compiled output (`dbt/target/`)
    are machine-local by-products, git-ignored, and never part of the worked example."""
    tracked = _tracked_workflow_files()
    assert tracked, "workflows/ has nothing tracked in git -- was it committed?"
    for path in tracked:
        lower = path.lower()
        assert not lower.endswith(".duckdb"), path
        assert not lower.endswith(".duckdb.wal"), path
        assert not lower.endswith("audit.jsonl"), path
        assert "__pycache__" not in lower, path
        assert "/dbt/logs/" not in lower, path
        assert "/dbt/target/" not in lower, path
        assert "dbt_sandbox_" not in lower, path


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

#: Login names to ban beyond the machine running the suite: a comma-separated list in this
#: environment variable. The list lives OUTSIDE the repository on purpose -- a committed list of
#: build machines' login names (phase 1's final review M10) would itself publish the very names
#: this check protects, so every machine guards its own login (`_login_names`) and a CI job or a
#: maintainer can add the names of other build machines here.
LOGIN_NAMES_ENV = "MIGRATION_BANNED_LOGIN_NAMES"


def _login_names() -> set[str]:
    """The login names no shipped file may contain: whoever runs the suite (`getpass.getuser()`,
    the home directory's name, `USERNAME`/`USER`) plus `LOGIN_NAMES_ENV`, each kept only when it
    is specific enough to mean anything (a name like `user` or `build` would match half the
    English in the tree)."""
    candidates = {getpass.getuser(), Path.home().name, os.environ.get("USERNAME", ""),
                  os.environ.get("USER", "")}
    candidates |= set(os.environ.get(LOGIN_NAMES_ENV, "").split(","))
    return {name.strip() for name in candidates
            if len(name.strip()) >= 4 and name.strip().lower() not in _GENERIC_LOGINS}


def test_the_login_probe_honours_the_environment_list(monkeypatch):
    monkeypatch.setenv(LOGIN_NAMES_ENV, "someone-else, zz, qwertyuiop ")
    names = {name.lower() for name in _login_names()}
    assert {"someone-else", "qwertyuiop"} <= names
    assert "zz" not in names                       # too short to mean anything


def test_only_the_fixture_owner_name_appears_never_the_os_login_name():
    """`wf_owner` is every `samples/wf_000N/sample.json`'s hand-authored fixture value for the
    workflow owner (asserted on by name in
    tests/test_samples_wellformed.py::test_sample_json_has_the_planned_keys) -- it flows verbatim
    into `manifest.json["source"]["owner"]`, `open_questions.md`'s header and the generated docs
    for every one of these seven workflows, on every machine, and is not the leak this check exists
    to catch.

    A build machine's login name is a different thing entirely: it must never appear, because it
    would mean a non-interactive resume (the orchestrator's own `--no-interactive` calls, and this
    offline sample run) silently baked whoever's account happened to run it into a committed
    artifact -- exactly the `intake/mappings.yaml["confirmed_by"]` defect Task 17 found and fixed
    in `scripts/intake_prompt.py`.

    The names come from `_login_names`: the login of whoever runs the suite, plus any listed in
    `LOGIN_NAMES_ENV` -- never from a committed list, which would publish them."""
    names = _login_names()
    tracked = _tracked_workflow_files()
    assert tracked
    for rel in tracked:
        text = _read_text_or_none(ROOT / rel)
        if text is None:
            continue
        for name in sorted(names):
            assert name.lower() not in text.lower(), f"{rel} leaks the login name {name!r}"


# --- the same hand-off scan, over EVERY tracked file (final review I6; round 2 R3) ---------------

#: Spec §1's hand-off bar is "no machine paths", and it is not a property of `workflows/` alone:
#: the final whole-branch review found two `scratchpad/…` pointers in `scripts/lib/` and `tests/`,
#: which no test was looking at. Round 2 found the replacement scan had the same shape of hole --
#: it named five trees, so root-level files, `catalog/`, `cookbook/`, `mappings/`, `snowflake/`
#: and `.github/` were unscanned. The scans now walk `git ls-files` whole: a tree that does not
#: exist yet cannot be forgotten.

#: The user segment of an absolute path on somebody's PC: `C:\\Users\\<name>`, `C:/Users/<name>`,
#: or Git Bash's `/c/Users/<name>`. Doubled separators are matched too: a path quoted inside JSON
#: in a build report arrives as `C:\\\\Users\\\\<name>`.
_USER_PATH_RE = re.compile(r"(?:[a-zA-Z]:[\\/]+|/c/)Users[\\/]+([^\\/\s\"'`,;)*]*)", re.IGNORECASE)

#: The only user segments a committed file may carry. A redaction (`<user>`, `<you>`), an ellipsis
#: standing in for the rest of the path, the character class of a grep recipe, or the plainly
#: fictional account a unit test invents. Any other segment is somebody's real login name.
_PLACEHOLDER_USER_SEGMENTS = {"<user>", "<you>", "<name>", "\u2026", "...", "[a-za-z]",
                              "nobody", "someone", ""}

#: Sentence punctuation is stripped off the captured segment before it is judged. This is what
#: admits README §6's two documented Git-Bash path explanations -- "`pwd` prints /c/Users/…" and
#: "the drive-letter form C:/Users/…." -- whose second one ends the sentence, so the regex
#: captures `….` rather than `…`. It weakens nothing: a real login name with a full stop after it
#: is still a real login name.
_SEGMENT_PUNCTUATION = ".,;:)"

#: `scratchpad` is a directory on the machine an agent happened to run on; a shipped file that
#: points at one is a dead pointer the moment it is read anywhere else. The build reports are the
#: deliberate exception: their subject IS how and where the work ran, and saying "every run
#: happened in a scratch root outside the repo" is the evidence, not a leak. They carry no real
#: path -- the scan above still holds them to `<user>`.
_SCRATCHPAD_EXEMPT = ("docs/superpowers/build-reports/", "docs/live-smoke-test.md")

#: This file is where the checks themselves live, so it is the one file that must spell out the
#: word `scratchpad` it bans everywhere else. It spells out no login name, so the login scan covers
#: it too.
_CHECKS_OWN_FILE = f"tests/{Path(__file__).name}"


def _hand_off_files() -> list[str]:
    """Every tracked file in the repository. Binary files are skipped where they are read."""
    tracked = _tracked()
    assert len(tracked) > 500, f"git ls-files returned only {len(tracked)} files"
    return tracked


def test_no_shipped_file_carries_a_real_machine_path():
    offenders = []
    for rel in _hand_off_files():
        text = _read_text_or_none(ROOT / rel)
        if text is None:
            continue
        for match in _USER_PATH_RE.finditer(text):
            segment = match.group(1).rstrip(_SEGMENT_PUNCTUATION).lower()
            if segment not in _PLACEHOLDER_USER_SEGMENTS:
                offenders.append(f"{rel}: {match.group(0)!r}")
    assert not offenders, "absolute paths of a real machine are committed:\n" + "\n".join(offenders)


def test_no_shipped_file_carries_a_build_machine_login_name():
    offenders = []
    names = _login_names()
    for rel in _hand_off_files():
        text = _read_text_or_none(ROOT / rel)
        if text is None:
            continue
        for name in sorted(names):
            if name.lower() in text.lower():
                offenders.append(f"{rel}: {name}")
    assert not offenders, "a build machine's login name is committed:\n" + "\n".join(offenders)


def test_no_shipped_file_points_at_a_scratchpad():
    offenders = []
    for rel in _hand_off_files():
        if rel.startswith(_SCRATCHPAD_EXEMPT) or rel == _CHECKS_OWN_FILE:
            continue
        text = _read_text_or_none(ROOT / rel)
        if text is None:
            continue
        if "scratchpad" in text.lower():
            offenders.append(rel)
    assert not offenders, ("these files point at a scratchpad directory that exists on no other "
                           "machine:\n" + "\n".join(offenders))
