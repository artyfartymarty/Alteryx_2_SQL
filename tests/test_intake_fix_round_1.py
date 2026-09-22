"""Fix round 1: three issues the reviewer found probing the multi-session and self-reference
scenarios live, after task 10's first review otherwise passed.

CRITICAL -- `intake/mappings.yaml` regressed (lost previously-confirmed sources/outputs) once
`intake_touchpoints.py` was re-run mid-session and a touchpoint became `resolved` from the freshly
promoted `mappings/global.yaml`: `apply_answers` rebuilt the file from scratch from only the
current call's answers, and prompting skips anything already `resolved`, so nothing ever
repopulated it. `load_golden.py::load_set` reads ONLY the workflow's own `intake/mappings.yaml`,
so a `READY` workflow with a regressed file failed at validation -- this is the real consumer test
(a) exercises directly.

IMPORTANT A -- self-reference protection only ever scanned DAG `input` touchpoints, so a source
declared through the opening "other yxdb" loop (a synthetic `U<n>` id, or one already sitting in
`intake/mappings.yaml` from an earlier session) was never in `known_input_sources` and so was never
protected.

IMPORTANT B -- the self-reference note rendered a second `- [x] Q<n> (self-reference) - …` line
whose own prose contains `": "`; since the parser matched on the bare `Q<n>` prefix and the last
matching line won, a subsequent non-interactive resume read that note's prose as the answer and
(harmlessly, since it fails `FQN_RE`) dropped the confirmed mapping.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import intake_prompt as ip
import intake_touchpoints as tpx
import load_golden
from dev import build_samples
from lib import io
from lib.backend import DuckDBBackend
from lib.paths import Repo
from tests.helpers import copy_pristine_mappings_and_catalog

ROOT = Path(__file__).parents[1]


def _repo(tmp_path, wf_id="wf_0001"):
    copy_pristine_mappings_and_catalog(tmp_path)
    repo = Repo(tmp_path)
    build_samples.build(repo, ROOT / "samples", wf_id)
    return repo


def scripted(answers):
    it = iter(answers)
    said = []
    return (lambda prompt: (said.append(prompt), next(it))[1]), said


def _check_and_answer(oq_path: Path, qid: str, answer_text: str) -> None:
    """Emulates a human checking `qid`'s box and typing the answer directly after it on the
    checkbox's own (first) line -- the same editing style the brief's own verbatim test uses, kept
    working by `_item_answer`'s fallback precedence."""
    lines = oq_path.read_text(encoding="utf-8").splitlines()
    out_lines = []
    for line in lines:
        prefix = f"- [ ] {qid}"
        if line.startswith(prefix):
            line = ("- [x] " + line[len("- [ ] "):]).rstrip() + f" {answer_text}"
        out_lines.append(line)
    oq_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


# --- test (a): the reviewer's CRITICAL sequence end to end, with the real consumer ---------------

def test_a_mappings_survive_a_touchpoints_rerun_and_load_golden_succeeds(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    tpx.run(repo, "wf_0001")

    # Interactive: Q1 (input) and Q2 (output) mapped, Q3 (output) deferred.
    ask, _ = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"

    # Re-running intake_touchpoints.py: Q1/Q2 are now resolved from the freshly promoted
    # mappings/global.yaml, so a fresh prompt session would never ask about them again.
    touchpoints2 = tpx.run(repo, "wf_0001")
    q1 = next(t for t in touchpoints2 if t["key"] == "sales/orders.yxdb")
    q2 = next(t for t in touchpoints2 if t["key"] == "out/sales_summary.yxdb")
    q3 = next(t for t in touchpoints2 if t["key"] == "out/excluded_orders.csv")
    assert q1["resolved"] is not None and q2["resolved"] is not None and q3["resolved"] is None

    # Non-interactive: answer Q3 by hand-editing open_questions.md (regenerated against the new
    # touchpoints.json, so it now has only Q3 open).
    oq = repo.wf("wf_0001", "intake", "open_questions.md")
    assert "- [ ] Q1" not in oq.read_text(encoding="utf-8")  # Q1 is resolved: not even rendered
    _check_and_answer(oq, "Q3", "ANALYTICS.CURATED.EXCLUDED_ORDERS")
    status2 = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status2 == "READY"

    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert set(m["sources"]) == {"sales/orders.yxdb"}
    assert set(m["outputs"]) == {"out/sales_summary.yxdb", "out/excluded_orders.csv"}
    for section in ("sources", "outputs"):
        for key, entry in m[section].items():
            assert entry.get("logical"), (section, key)
            assert entry.get("tool_ids"), (section, key)

    # The real consumer: load_golden.py reads only this file.
    backend = DuckDBBackend()
    info = load_golden.load_set(backend, repo, "wf_0001", "normal")
    assert info["loaded"]
    assert backend.table_exists(info["loaded"][0])


# --- test (b): a workflow fully resolved from mappings/global.yaml before the first prompt -------

def test_b_fully_resolved_workflow_asks_nothing_and_backfills_from_global(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    g = io.read_yaml(repo.global_mappings)
    g["sources"]["sales/orders.yxdb"] = {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS",
                                         "confirmed_by": "jdoe"}
    g["outputs"]["out/sales_summary.yxdb"] = {"snowflake": "ANALYTICS.CURATED.SALES_SUMMARY",
                                              "logical": "SALES_SUMMARY", "confirmed_by": "jdoe",
                                              "mode": "overwrite", "keys": []}
    g["outputs"]["out/excluded_orders.csv"] = {"snowflake": "ANALYTICS.CURATED.EXCLUDED_ORDERS",
                                               "logical": "EXCLUDED_ORDERS", "confirmed_by": "jdoe",
                                               "mode": "overwrite", "keys": []}
    io.write_yaml(repo.global_mappings, g)

    tps = tpx.run(repo, "wf_0001")
    assert all(t["resolved"] is not None for t in tps if t["blocking"])

    ask, said = scripted(["n"])  # the opening yxdb question is asked regardless; nothing else is
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "READY"
    assert len(said) == 1

    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    src = m["sources"]["sales/orders.yxdb"]
    assert src["confirmed_by"] == "jdoe" and src["from"] == "global" and src["logical"] == "ORDERS"
    for key, logical in (("out/sales_summary.yxdb", "SALES_SUMMARY"),
                         ("out/excluded_orders.csv", "EXCLUDED_ORDERS")):
        out_entry = m["outputs"][key]
        assert out_entry["confirmed_by"] == "jdoe" and out_entry["from"] == "global"
        assert out_entry["logical"] == logical and out_entry["mode"] == "overwrite"


# --- test (c): the READY guard --------------------------------------------------------------------

def test_c_guard_downgrades_a_falsely_ready_status_to_needs_human(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "wf_0001")
    tpx.run(repo, "wf_0001")

    def fake_apply_answers(repo_, wf_id, touchpoints, answers, user, **_kwargs):
        return {
            "status": "READY",
            "mappings": {"sources": {}, "outputs": {}, "constants": {}, "parameters": {},
                        "macros": {}},
            "conflicts": [], "confirmed_constants": set(), "problems": [], "answers_by_qid": {},
        }

    monkeypatch.setattr(ip, "apply_answers", fake_apply_answers)
    ask, _ = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    out_lines: list[str] = []
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=out_lines.append, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    guard_lines = [l for l in out_lines if "GUARD" in l or "no entry" in l]
    assert any("GUARD" in l for l in guard_lines)
    # Fix round 2's fuller guard reports one line per problem (here: all 3 touchpoints have no
    # entry at all, since the faked apply_answers wrote nothing), not one combined line.
    assert all(any(qid in l for l in guard_lines) for qid in ("Q1", "Q2", "Q3"))
    assert io.load_manifest(repo, "wf_0001")["status"]["intake"] == "NEEDS_HUMAN"


# --- test (d): self-reference protection covers user-declared and earlier-session sources --------

def test_d_typed_user_declared_source_triggers_the_warning(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    tpx.run(repo, "wf_0001")
    # Declare fx/rates.yxdb -> ANALYTICS.CURATED.FX_RATES (NOT in the raw schema, so only the
    # source-collision path -- not the raw-schema path -- can be catching this).
    ask, said = scripted([
        "y", r"\\share\fx\rates.yxdb", "ANALYTICS.CURATED.FX_RATES", "",  # opening loop
        "", "ANALYTICS.CURATED.FX_RATES", "no", "?",                      # Q1 accept, Q2 warn+decline+defer
        "?",                                                              # Q3 defer
    ])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    assert any("Type 'yes'" in s for s in said)
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert "out/sales_summary.yxdb" not in (m.get("outputs") or {})


def test_d_differently_cased_user_declared_source_also_triggers_the_warning(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    tpx.run(repo, "wf_0001")
    ask, said = scripted([
        "y", r"\\share\fx\rates.yxdb", "ANALYTICS.CURATED.FX_RATES", "",
        "", "analytics.curated.fx_rates", "no", "?",
        "?",
    ])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    assert any("Type 'yes'" in s for s in said)


def test_d_non_interactive_refuses_a_user_declared_source_without_the_token(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    tpx.run(repo, "wf_0001")
    ask, _ = scripted([
        "y", r"\\share\fx\rates.yxdb", "ANALYTICS.CURATED.FX_RATES", "",
        "", "?", "?",
    ])
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    oq = repo.wf("wf_0001", "intake", "open_questions.md")
    _check_and_answer(oq, "Q2", "ANALYTICS.CURATED.FX_RATES")
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert "out/sales_summary.yxdb" not in (m.get("outputs") or {})


def test_d_source_declared_in_an_earlier_session_is_protected_the_same_way(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    tpx.run(repo, "wf_0001")
    # Session 1: declare the user source, defer everything else.
    ask1, _ = scripted(["y", r"\\share\fx\rates.yxdb", "ANALYTICS.CURATED.FX_RATES", "", "?", "?", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask1, out=lambda s: None, user="wf_owner")
    assert io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))["sources"]["fx/rates.yxdb"][
        "snowflake"] == "ANALYTICS.CURATED.FX_RATES"

    # Session 2: nothing new declared this time; typing that same table for an output must still warn.
    ask2, said2 = scripted(["n", "", "ANALYTICS.CURATED.FX_RATES", "no", "?", "?"])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask2, out=lambda s: None, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    assert any("Type 'yes'" in s for s in said2)
    # The declared source itself must still be there (cumulative, ruling 1).
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert m["sources"]["fx/rates.yxdb"]["snowflake"] == "ANALYTICS.CURATED.FX_RATES"


# --- test (e): a confirmed self-reference survives repeated non-interactive resumes --------------

def test_e_confirmed_self_reference_survives_two_more_non_interactive_runs(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    tpx.run(repo, "wf_0001")
    ask, _ = scripted(["n", "", "SALES.RAW.ORDERS", "yes", "", "?"])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    entry = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))["outputs"]["out/sales_summary.yxdb"]
    assert entry["snowflake"] == "SALES.RAW.ORDERS" and entry["self_reference"] is True

    for _ in range(2):
        status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
        assert status == "WAITING_FOR_ANSWERS"  # Q3 was never answered
        entry = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))["outputs"]["out/sales_summary.yxdb"]
        assert entry["snowflake"] == "SALES.RAW.ORDERS" and entry["self_reference"] is True


# --- test (f): parser precedence and the dangerous-default interaction with an empty answer ------

def test_f_prose_containing_colon_space_in_an_item_body_is_ignored():
    md = (
        "# Open questions - wf_0009 (owner: @wf_owner)\n\n"
        "## Blocking (translation waits)\n"
        "- [x] Q1 - Tool 1 Input Data reads C:\\data\\x.yxdb (3 fields, 5 rows).\n"
        "      Proposed: SOME.OTHER.TABLE (2/3 columns match; missing X).\n"
        "      Confirm or supply another: REAL.ANSWER.TABLE\n"
        "\n## Non-blocking (proceeds with the assumption)\n(none)\n"
    )
    assert ip.parse_answered_questions(md) == {"Q1": "REAL.ANSWER.TABLE"}


def test_f_a_note_line_never_parses_as_its_own_item():
    md = (
        "# Open questions - wf_0009 (owner: @wf_owner)\n\n"
        "## Blocking (translation waits)\n(none)\n\n"
        "## Non-blocking (proceeds with the assumption)\n"
        "- [x] Q2 - Tool 7 Output Data writes out.yxdb (2 fields), mode overwrite.\n"
        "      Proposed: SOME.OTHER.TABLE (naming convention; not verified to exist).\n"
        "      Confirm or supply another: SALES.RAW.ORDERS\n"
        "* (self-reference) Q2 - Tool 7 Output Data was confirmed to target SALES.RAW.ORDERS "
        "(mode overwrite), which is also one of this workflow's own inputs: a run-ordering risk "
        "(program spec §8.5) -- make sure this output only runs after that input has loaded.\n"
    )
    answers = ip.parse_answered_questions(md)
    # The note's own prose (after "target ") must NOT have overwritten Q2's real answer.
    assert answers == {"Q2": "SALES.RAW.ORDERS"}


def test_f_empty_answer_on_a_checked_output_item_with_no_enter_acceptable_proposal_stays_unresolved(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    tpx.run(repo, "wf_0001")
    ask, _ = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    oq = repo.wf("wf_0001", "intake", "open_questions.md")
    lines = oq.read_text(encoding="utf-8").splitlines()
    lines = ["- [x] " + l[len("- [ ] "):] if l.startswith("- [ ] Q3") else l for l in lines]
    oq.write_text("\n".join(lines) + "\n", encoding="utf-8")  # checked, but no answer text anywhere
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert "out/excluded_orders.csv" not in (m.get("outputs") or {})


def test_f_empty_checked_answer_non_interactively_accepts_a_full_match_output(tmp_path):
    repo3 = _repo(tmp_path, "wf_0003")
    tpx.run(repo3, "wf_0003")
    # First pass: defer everything, just to get an open_questions.md with both items unchecked.
    ask, _ = scripted(["n", "?", "?"])
    status = ip.run(repo3, "wf_0003", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"

    # Check Q1 (input) and Q2 (output, full 6/6 match, non-raw schema) with NO answer text
    # anywhere: "accept the proposal" -- Q1's rule (input) always allows this; Q2's (output) only
    # because its top candidate is a full match, per the dangerous-default ruling.
    oq = repo3.wf("wf_0003", "intake", "open_questions.md")
    lines = oq.read_text(encoding="utf-8").splitlines()
    lines = ["- [x] " + l[len("- [ ] "):] if (l.startswith("- [ ] Q1") or l.startswith("- [ ] Q2"))
            else l for l in lines]
    oq.write_text("\n".join(lines) + "\n", encoding="utf-8")

    status2 = ip.run(repo3, "wf_0003", interactive=False, user="wf_owner")
    assert status2 == "READY"
    m = io.read_yaml(repo3.wf("wf_0003", "intake", "mappings.yaml"))
    assert m["sources"]["alias:prod_fin"]["snowflake"] == "FINANCE.RAW.GL_LEDGER"
    out_entry = m["outputs"]["alias:prod_fin/dbo.gl_summary"]
    assert out_entry["snowflake"] == "ANALYTICS.CURATED.GL_SUMMARY"
    assert out_entry["mode"] == "merge" and out_entry["keys"] == ["ACCT", "PERIOD"]


# --- test (g): the brief's own eight verbatim tests, unaffected -----------------------------------
# (already exercised by tests/test_intake_prompt.py; re-run together with this file below.)
