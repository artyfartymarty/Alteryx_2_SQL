"""Interactive intake: the prompt protocol that asks the workflow owner which Snowflake table each
touchpoint corresponds to (plan task 10). This is the brief's Step 1 test file, verbatim.
"""
from pathlib import Path
import pytest
from lib import io
from lib.paths import Repo
import intake_touchpoints as tpx, intake_prompt as ip
from dev import build_samples
from tests.helpers import copy_pristine_mappings_and_catalog

ROOT = Path(__file__).parents[1]
@pytest.fixture
def repo(tmp_path):
    copy_pristine_mappings_and_catalog(tmp_path)
    r = Repo(tmp_path); build_samples.build(r, ROOT / "samples", "wf_0001"); return r
def scripted(answers):
    it = iter(answers); said = []
    return (lambda prompt: (said.append(prompt), next(it))[1]), said

def test_touchpoints_read_the_yxdb_header_and_rank_candidates(repo):
    tps = tpx.run(repo, "wf_0001")
    t = tps[0]
    assert (t["id"], t["kind"], t["format"], t["key"]) == ("Q1", "input", "yxdb", "sales/orders.yxdb")
    assert t["field_source"] == "yxdb_header" and len(t["fields"]) == 7 and t["record_count"] > 0
    assert t["candidates"][0]["snowflake"] == "SALES.RAW.ORDERS" and t["candidates"][0]["matched"] == 7
    assert t["candidates"][1]["missing"] == ["STATUS"] and t["candidates"][-1]["basis"] == "naming"
    assert [x["kind"] for x in tps].count("output") == 2

def test_enter_accepts_a_column_backed_candidate_and_outputs_are_asked_for_mode(repo):
    tps = tpx.run(repo, "wf_0001")
    ask, said = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    out = []
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=out.append, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    assert "not visible in the DAG" in said[0] and "7/7 columns" in "\n".join(out)
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert m["sources"]["sales/orders.yxdb"] == {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS", "tool_ids": ["1"], "confirmed_by": "wf_owner"}
    assert m["outputs"]["out/sales_summary.yxdb"]["snowflake"] == "ANALYTICS.CURATED.SALES_SUMMARY" and m["outputs"]["out/sales_summary.yxdb"]["mode"] == "overwrite"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert "- [x] Q1" in md and "- [ ] Q3" in md and "## Blocking" in md
    assert io.read_yaml(repo.global_mappings)["sources"]["sales/orders.yxdb"]["snowflake"] == "SALES.RAW.ORDERS"

def test_answers_written_into_open_questions_resume_the_workflow(repo):
    tpx.run(repo, "wf_0001"); ask, _ = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    q = repo.wf("wf_0001", "intake", "open_questions.md")
    lines = [l.replace("- [ ] Q3", "- [x] Q3").rstrip() + (" ANALYTICS.CURATED.EXCLUDED_ORDERS" if l.startswith("- [ ] Q3") else "") for l in q.read_text(encoding="utf-8").splitlines()]
    q.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ip.run(repo, "wf_0001", interactive=False, user="wf_owner") == "READY"
    assert io.load_manifest(repo, "wf_0001")["status"]["intake"] == "READY"

def test_globally_resolved_sources_are_never_asked_again(repo):
    g = io.read_yaml(repo.global_mappings); g["sources"]["sales/orders.yxdb"] = {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS", "confirmed_by": "jdoe"}
    io.write_yaml(repo.global_mappings, g)
    tps = tpx.run(repo, "wf_0001")
    assert tps[0]["resolved"] == {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS", "from": "global"}
    ask, said = scripted(["n", "?", "?"]); ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert not any("Q1" in s for s in said)

def test_bad_fqn_is_reasked_then_deferred_and_never_invented(repo):
    tpx.run(repo, "wf_0001"); ask, said = scripted(["n", "orders", "SALES.ORDERS", "nope nope", "?", "?"])
    assert ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner") == "WAITING_FOR_ANSWERS"
    assert "sales/orders.yxdb" not in (io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml")).get("sources") or {})

def test_impossible_touchpoint_blocks(repo):
    tpx.run(repo, "wf_0001"); ask, _ = scripted(["n", "!", "?", "?"])
    assert ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner") == "BLOCKED"

def test_user_declared_yxdb(repo):
    tpx.run(repo, "wf_0001"); ask, _ = scripted(["y", r"\\share\fx\rates.yxdb", "FINANCE.RAW.FX_RATES", "", "?", "?", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    src = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))["sources"]["fx/rates.yxdb"]
    assert src["snowflake"] == "FINANCE.RAW.FX_RATES" and src["tool_ids"] == [] and src["note"] == "user-declared"

def test_conflict_with_global_needs_a_human(repo):
    g = io.read_yaml(repo.global_mappings); g["outputs"]["out/sales_summary.yxdb"] = {"snowflake": "ANALYTICS.CURATED.OTHER", "logical": "OTHER", "confirmed_by": "jdoe"}
    io.write_yaml(repo.global_mappings, g); tps = tpx.run(repo, "wf_0001")
    answers = [{"id": t["id"], "action": "map", "snowflake": "ANALYTICS.CURATED.SALES_SUMMARY", "write_mode": "overwrite", "keys": []} for t in tps if t["key"] == "out/sales_summary.yxdb"]
    for t in tps: t["resolved"] = None
    assert ip.apply_answers(repo, "wf_0001", tps, answers, "wf_owner")["status"] == "NEEDS_HUMAN"
    assert io.read_yaml(repo.global_mappings)["outputs"]["out/sales_summary.yxdb"]["snowflake"] == "ANALYTICS.CURATED.OTHER"
