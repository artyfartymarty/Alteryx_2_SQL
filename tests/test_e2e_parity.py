import json
from pathlib import Path
import pytest
from lib import io
from validate_segment import validate_segment
from tests.helpers import prepare_workflow
SAMPLES = Path(__file__).parents[1] / "samples"
pytestmark = pytest.mark.e2e

@pytest.mark.parametrize("wf", ["wf_0001", "wf_0002", "wf_0003", "wf_0004"])
def test_hand_migration_passes_every_golden_set(tmp_path, wf):
    repo = prepare_workflow(tmp_path, wf)
    for wave in io.read_json(repo.wf(wf, "segments", "order.json")):
        for seg in wave:
            r = validate_segment(repo, wf, seg)
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
    r = validate_segment(repo, wf, case["segment"], [case["golden_set"]], proc_path=SAMPLES / wf / "broken_sql" / case["segment"] / case["file"])
    assert r["verdict"] == "FAIL"
    hits = [c for c in r["diff_clusters"] if c["class"] == case["expect"]["class"] and set(case["expect"]["columns"]) <= set(c["columns"])]
    assert hits, json.dumps(r["diff_clusters"], indent=1, default=str)[:3000]
