"""Coordinator ruling (post-review of task 10's real transcript): an output touchpoint must never
quietly default onto one of this workflow's own inputs, or onto the raw landing schema -- column
overlap alone can't tell an output from an input that happens to share its columns, and an output
usually *is* a reshaping of some input. This is a dangerous default the original brief didn't guard
against: pressing Enter on wf_0001's Q3 (`excluded_orders.csv`) would otherwise have mapped that
OUTPUT, write mode `overwrite`, onto `SALES.RAW.ORDERS` -- the workflow's own INPUT.
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


@pytest.fixture
def repo(tmp_path):
    copy_pristine_mappings_and_catalog(tmp_path)
    r = Repo(tmp_path)
    build_samples.build(r, ROOT / "samples", "wf_0001")
    return r


def _repo_for(tmp_path, wf_id):
    copy_pristine_mappings_and_catalog(tmp_path)
    r = Repo(tmp_path)
    build_samples.build(r, ROOT / "samples", wf_id)
    return r


def scripted(answers):
    it = iter(answers)
    said = []
    return (lambda prompt: (said.append(prompt), next(it))[1]), said


def _q3(touchpoints):
    return next(t for t in touchpoints if t["key"] == "out/excluded_orders.csv")


# --- candidates never include a source table or the raw schema ---------------------------------

def test_wf_0001_output_candidates_exclude_the_workflow_s_own_source(repo):
    tps = tpx.run(repo, "wf_0001")
    q3 = _q3(tps)
    names = [c["snowflake"] for c in q3["candidates"]]
    assert "SALES.RAW.ORDERS" not in names
    assert "SALES.RAW.ORDERS_ARCHIVE" not in names
    # Only the naming-convention candidate is left (nothing else in the catalog is a non-RAW,
    # non-source match for excluded_orders.csv's 7 fields).
    assert names == ["ANALYTICS.CURATED.EXCLUDED_ORDERS"]
    assert q3["candidates"][0]["basis"] == "naming"


def test_enter_on_q3_defers_rather_than_accepting_a_source(repo):
    tpx.run(repo, "wf_0001")
    ask, said = scripted(["n", "", "", ""])  # opening, Q1 accept, Q2 defer(no candidates->defer), Q3 defer
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert "out/excluded_orders.csv" not in (m.get("outputs") or {})
    assert status == "WAITING_FOR_ANSWERS"


# --- an output whose only column-backed candidate is a FULL match in a non-raw schema IS accepted

def test_full_match_output_candidate_in_a_non_raw_schema_is_accepted_by_enter(tmp_path):
    repo3 = _repo_for(tmp_path, "wf_0003")
    tps = tpx.run(repo3, "wf_0003")
    q2 = next(t for t in tps if t["key"] == "alias:prod_fin/dbo.gl_summary")
    assert q2["candidates"][0] == {
        "snowflake": "ANALYTICS.CURATED.GL_SUMMARY", "matched": 6, "of": 6, "missing": [],
        "score": q2["candidates"][0]["score"], "row_count": q2["candidates"][0]["row_count"],
        "basis": "columns",
    }
    # n(opening: no other yxdb files -- wf_0003 has none at all, DB in and out) / ""(Q1 accept) /
    # ""(Q2 accept, full match, non-raw schema) / ""(Q2 write mode -> default "merge",
    # update_insert) / ""(Q2 merge keys -> default [ACCT, PERIOD]).
    ask, said = scripted(["n", "", "", "", ""])
    status = ip.run(repo3, "wf_0003", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "READY"
    m = io.read_yaml(repo3.wf("wf_0003", "intake", "mappings.yaml"))
    assert m["outputs"]["alias:prod_fin/dbo.gl_summary"]["snowflake"] == "ANALYTICS.CURATED.GL_SUMMARY"
    assert m["outputs"]["alias:prod_fin/dbo.gl_summary"]["mode"] == "merge"
    assert m["outputs"]["alias:prod_fin/dbo.gl_summary"]["keys"] == ["ACCT", "PERIOD"]
    assert "self_reference" not in m["outputs"]["alias:prod_fin/dbo.gl_summary"]


# --- typing a self-referencing table triggers a warning; declining re-asks; "yes" confirms -------

def test_typing_the_source_table_for_an_output_warns_then_reasks_on_decline(repo):
    tpx.run(repo, "wf_0001")
    # n(open) / ""(Q1 accept) / "SALES.RAW.ORDERS"(Q2 table -> warning) / "no"(decline) /
    # "?"(Q2 re-asked, defer) / "?"(Q3 defer)
    ask, said = scripted(["n", "", "SALES.RAW.ORDERS", "no", "?", "?"])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert "out/sales_summary.yxdb" not in (m.get("outputs") or {})
    assert any("yes" in s.lower() for s in said)  # the confirmation prompt was actually asked


def test_typing_the_source_table_for_an_output_applies_with_yes_and_records_self_reference(repo):
    tpx.run(repo, "wf_0001")
    out_lines: list[str] = []
    # n(open) / ""(Q1 accept SALES.RAW.ORDERS) / "SALES.RAW.ORDERS"(Q2 table -> warning) /
    # "yes"(confirm) / ""(Q2 write mode default) / "?"(Q3 defer)
    ask, said = scripted(["n", "", "SALES.RAW.ORDERS", "yes", "", "?"])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=out_lines.append, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"  # Q3 still deferred
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    entry = m["outputs"]["out/sales_summary.yxdb"]
    assert entry["snowflake"] == "SALES.RAW.ORDERS" and entry["self_reference"] is True
    assert any("WARNING" in line and "tool 1" in line for line in out_lines)
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert "self-reference" in md and "run-ordering" in md


def test_declining_does_not_consume_all_three_attempts(repo):
    """After one decline, the owner still gets to answer normally within the same 3-attempt cap."""
    tpx.run(repo, "wf_0001")
    # n / "" (Q1) / "SALES.RAW.ORDERS"(Q2 -> warn) / "no"(decline, 1 of 3 attempts used) /
    # "ANALYTICS.CURATED.SALES_SUMMARY"(Q2 answered for real) / ""(write mode) / "?"(Q3)
    ask, _ = scripted(["n", "", "SALES.RAW.ORDERS", "no", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert m["outputs"]["out/sales_summary.yxdb"]["snowflake"] == "ANALYTICS.CURATED.SALES_SUMMARY"
    assert "self_reference" not in m["outputs"]["out/sales_summary.yxdb"]


# --- non-interactive: refuses without the token, applies with it ---------------------------------

def test_non_interactive_refuses_a_self_reference_without_the_token(repo):
    tpx.run(repo, "wf_0001")
    ask, _ = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    oq = repo.wf("wf_0001", "intake", "open_questions.md")
    lines = oq.read_text(encoding="utf-8").splitlines()
    lines = [l.replace("- [ ] Q3", "- [x] Q3").rstrip() +
            (" SALES.RAW.ORDERS" if l.startswith("- [ ] Q3") else "") for l in lines]
    oq.write_text("\n".join(lines) + "\n", encoding="utf-8")
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert "out/excluded_orders.csv" not in (m.get("outputs") or {})


def test_non_interactive_applies_a_self_reference_with_the_token(repo):
    tpx.run(repo, "wf_0001")
    ask, _ = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    oq = repo.wf("wf_0001", "intake", "open_questions.md")
    lines = oq.read_text(encoding="utf-8").splitlines()
    lines = [l.replace("- [ ] Q3", "- [x] Q3").rstrip() +
            (" SALES.RAW.ORDERS confirm-self-reference" if l.startswith("- [ ] Q3") else "")
            for l in lines]
    oq.write_text("\n".join(lines) + "\n", encoding="utf-8")
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "READY"
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    entry = m["outputs"]["out/excluded_orders.csv"]
    assert entry["snowflake"] == "SALES.RAW.ORDERS" and entry["self_reference"] is True


# --- the raw-schema exclusion applies even to a table that is not literally a source -------------

def test_raw_schema_candidate_is_excluded_even_when_not_a_known_source():
    tp = {"kind": "output", "source": r"C:\data\out\region_lookup.yxdb", "table": None,
         "fields": ["REGION", "CODE", "NAME"]}
    catalog = [
        {"database": "REF", "schema": "RAW", "table": "REGION_CODES", "column": c, "row_count": 250}
        for c in ("REGION", "CODE", "NAME")
    ]
    program = {"target_database": "ANALYTICS", "raw_schema": "RAW", "target_schema": "CURATED"}
    candidates = tpx.propose_candidates(tp, catalog, program)
    assert candidates == [{"snowflake": "ANALYTICS.CURATED.REGION_LOOKUP", "basis": "naming"}]


# --- the brief's own verbatim scenarios are unaffected --------------------------------------------

def test_brief_verbatim_scenarios_still_behave_identically(repo):
    """Q2 was answered with an explicit FQN and Q3 with `?` in the brief's own test -- neither
    touches a candidate list or the Enter default, so this ruling changes nothing about them."""
    tps = tpx.run(repo, "wf_0001")
    ask, said = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    out = []
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=out.append, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    assert "not visible in the DAG" in said[0] and "7/7 columns" in "\n".join(out)
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert m["sources"]["sales/orders.yxdb"] == {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS",
                                                 "tool_ids": ["1"], "confirmed_by": "wf_owner"}
    assert m["outputs"]["out/sales_summary.yxdb"]["snowflake"] == "ANALYTICS.CURATED.SALES_SUMMARY"
    assert m["outputs"]["out/sales_summary.yxdb"]["mode"] == "overwrite"
