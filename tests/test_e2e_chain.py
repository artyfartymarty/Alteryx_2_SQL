"""Every committed sample's hand migration, stitched: the chain test end to end (plan Task W1).

`tests/test_e2e_parity.py` validates each canned segment in isolation, fed golden intermediates;
this file runs each procedure sample's segments as ONE chain -- raw golden inputs in, every segment
on its upstream segments' actual output, a Snowpark seam crossed through `lib/handoff.py` -- and
the dbt sample through its own `dbt run`, which already is the chain. A FAIL here is a real
composition finding about a canned sample, never something to tune away. Nothing here has run on
Snowflake: SQL runs on the DuckDB double, Snowpark in the Local Testing Framework, dbt on dbt-duckdb.
"""
from __future__ import annotations

import json

import pytest

from lib.io import read_json, write_json
from tests.helpers import prepare_workflow
from validate_dbt import validate_dbt
from validate_workflow import validate_workflow

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("wf", ["wf_0001", "wf_0002", "wf_0003", "wf_0004", "wf_0006"])
def test_every_procedure_sample_passes_as_a_chain(tmp_path, wf):
    repo = prepare_workflow(tmp_path, wf)

    r = validate_workflow(repo, wf)

    detail = json.dumps(r, indent=1, default=str)[:3000]
    assert r["sets"] and all(verdict.startswith("PASS") for verdict in r["sets"].values()), detail
    assert r["first_divergence"] is None and r["divergence_kind"] is None, detail
    assert r["idempotent"] is True, detail
    assert read_json(repo.wf(wf, "validation_workflow.json")) == r


def test_the_dbt_sample_writes_its_chain_report_from_its_own_run(tmp_path):
    wf = "wf_0007"
    repo = prepare_workflow(tmp_path, wf)

    validate_dbt(repo, wf)

    report = read_json(repo.wf(wf, "validation_workflow.json"))
    detail = json.dumps(report, indent=1, default=str)[:3000]
    assert report["verdict"] == "PASS", detail
    assert {"segment": "seg_01", "stream": "3_J", "verdict": "PASS"} in report["boundaries"], detail
    assert {f["output"] for f in report["finals"]} == {"REGION_ATTAINMENT", "ATTAINMENT_HISTORY"}
    assert report["idempotent"] is True and report["first_divergence"] is None

    # validate_workflow delegates a dbt workflow to validate_dbt. The orchestrator's analyze stage is
    # what records output_kind in the manifest (prepare_workflow does not run it), so record it here.
    manifest = read_json(repo.wf(wf, "manifest.json"))
    manifest["output_kind"] = "dbt"
    write_json(repo.wf(wf, "manifest.json"), manifest)
    assert validate_workflow(repo, wf)["verdict"] == report["verdict"]
