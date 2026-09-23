import json
from pathlib import Path
import pytest
from lib import io
from validate_dbt import validate_dbt
from validate_segment import validate_segment
from validate_snowpark import validate_snowpark
from tests.helpers import overlay_dbt_project, prepare_workflow
SAMPLES = Path(__file__).parents[1] / "samples"
pytestmark = pytest.mark.e2e

def _validator(target):
    """The validator a segment's output target calls for: a Snowpark Python procedure runs in the
    Snowpark Local Testing Framework, a SQL one on the DuckDB double. Both take (repo, wf, seg,
    sets, proc_path=...) and return the same report shape, which is what lets this file dispatch."""
    return validate_snowpark if target == "snowpark" else validate_segment

def validate(repo, wf, seg, sets=None, proc_path=None):
    """Validate one segment through the validator its own contract.json asks for (plan contract
    C5's `target`; absent means `sql`, exactly as compile_check.py --target auto reads it)."""
    target = io.read_json(repo.seg(wf, seg, "contract.json")).get("target") or "sql"
    return _validator(target)(repo, wf, seg, sets, proc_path=proc_path)

def _is_dbt(wf):
    """A dbt sample (output_kind dbt, design §4.3) carries one project for the whole workflow
    instead of a procedure per segment, and validate_dbt.py judges every segment from one run."""
    return (SAMPLES / wf / "canned" / "dbt").is_dir()

@pytest.mark.parametrize("wf", ["wf_0001", "wf_0002", "wf_0003", "wf_0004", "wf_0006", "wf_0007"])
def test_hand_migration_passes_every_golden_set(tmp_path, wf):
    repo = prepare_workflow(tmp_path, wf)
    if _is_dbt(wf):
        reports = validate_dbt(repo, wf)
    else:
        reports = {seg: validate(repo, wf, seg)
                   for wave in io.read_json(repo.wf(wf, "segments", "order.json")) for seg in wave}
    for seg, r in reports.items():
        assert r["sets"] == {"normal": "PASS", "period_end": "PASS", "empty": "PASS", "edge": "PASS"}, json.dumps(r, indent=1, default=str)[:3000]
        assert r["idempotent"] is True and r["diff_clusters"] == []

def broken_cases():
    for wf_dir in sorted(SAMPLES.glob("wf_*")):
        f = wf_dir / "broken_sql" / "broken.json"
        for case in (json.loads(f.read_text(encoding="utf-8")) if f.exists() else []):
            yield pytest.param(wf_dir.name, case, id=f"{wf_dir.name}-{case['file']}")

@pytest.mark.parametrize("wf,case", list(broken_cases()))
def test_broken_migration_fails_with_the_right_class(tmp_path, wf, case):
    repo = prepare_workflow(tmp_path, wf)
    if case["target"] == "dbt":
        variant = SAMPLES / wf / "broken_sql" / case["file"]
        project = overlay_dbt_project(repo, wf, tmp_path / "broken_project",
                                      {case["file"].removeprefix("dbt/"): variant})
        r = validate_dbt(repo, wf, [case["golden_set"]], project_dir=project)[case["segment"]]
        # A wrong model is the fixer's to repair, never a human escalation (plan Task C Step 3).
        assert r["needs_human"] is False, json.dumps(r, indent=1, default=str)[:3000]
    else:
        r = _validator(case["target"])(repo, wf, case["segment"], [case["golden_set"]],
                                       proc_path=SAMPLES / wf / "broken_sql" / case["segment"] / case["file"])
    assert r["verdict"] == "FAIL"
    hits = [c for c in r["diff_clusters"] if c["class"] == case["expect"]["class"] and set(case["expect"]["columns"]) <= set(c["columns"])]
    assert hits, json.dumps(r["diff_clusters"], indent=1, default=str)[:3000]
