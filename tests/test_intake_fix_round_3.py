"""Fix round 3 (a CRITICAL regression dating from round 1, found by the controller reproducing
round 2 through the real CLI): logical names flip-flop on every no-op `--no-interactive` resume.

Round 1 seeded `logical_name`'s collision set (`taken_logicals`) from the logical names already in
`intake/mappings.yaml`, so a genuinely NEW entry wouldn't collide with an established one. But the
non-interactive resume path re-applies the SAME checked answers from `open_questions.md` on EVERY
run (nothing marks an already-answered item as "settled" -- the file is re-parsed fresh each time).
So re-answering a touchpoint with its OWN unchanged FQN collided with its own prior logical name
(already in the seeded "taken" set), fell back to `<SCHEMA>_<TABLE>`, and the FOLLOWING run saw
THAT fallback name on file instead -- freeing the plain name again. Reproduction: `ORDERS` ->
`RAW_ORDERS` -> `ORDERS` -> `RAW_ORDERS` on alternating no-op resumes. Load-bearing: translated
procedures read `IDENTIFIER(:ORDERS_SRC)` after `LET ORDERS_SRC VARCHAR := … || '.ORDERS'`,
`load_golden.load_set` builds the sandbox view schema from these exact names, and the orchestrator
runs intake more than once per workflow -- so a harmless re-run would silently break a validated
workflow.
"""
from __future__ import annotations

from pathlib import Path

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


def _map_all_three(repo):
    """The coordinator's exact reproduction: Q1 accepted via Enter, Q2/Q3 typed explicitly with
    Enter for the (default) write mode."""
    tpx.run(repo, "wf_0001")
    ask, _ = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "",
                       "ANALYTICS.CURATED.EXCLUDED_ORDERS", ""])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "READY"


def _logicals(repo) -> dict[str, str]:
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    return {
        "sales/orders.yxdb": m["sources"]["sales/orders.yxdb"]["logical"],
        "out/sales_summary.yxdb": m["outputs"]["out/sales_summary.yxdb"]["logical"],
        "out/excluded_orders.csv": m["outputs"]["out/excluded_orders.csv"]["logical"],
    }


# --- the reproduction: three no-op resumes leave everything byte-identical -----------------------

def test_three_no_op_resumes_leave_every_file_byte_identical(tmp_path):
    repo = _repo(tmp_path)
    _map_all_three(repo)

    mappings_path = repo.wf("wf_0001", "intake", "mappings.yaml")
    oq_path = repo.wf("wf_0001", "intake", "open_questions.md")
    global_path = repo.global_mappings

    expected_logicals = {"sales/orders.yxdb": "ORDERS", "out/sales_summary.yxdb": "SALES_SUMMARY",
                         "out/excluded_orders.csv": "EXCLUDED_ORDERS"}
    assert _logicals(repo) == expected_logicals

    mappings_bytes = mappings_path.read_bytes()
    oq_bytes = oq_path.read_bytes()
    global_bytes = global_path.read_bytes()

    for i in range(1, 4):
        status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
        assert status == "READY", f"resume {i}"
        assert _logicals(repo) == expected_logicals, f"resume {i}: logical names changed"
        assert mappings_path.read_bytes() == mappings_bytes, f"resume {i}: mappings.yaml changed"
        assert oq_path.read_bytes() == oq_bytes, f"resume {i}: open_questions.md changed"
        assert global_path.read_bytes() == global_bytes, f"resume {i}: mappings/global.yaml changed"


# --- the orchestrator's own handover: interactive, then non-interactive, then load_golden --------

def test_interactive_then_non_interactive_handover_exposes_original_logical_names(tmp_path):
    repo = _repo(tmp_path)
    _map_all_three(repo)
    original = _logicals(repo)

    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "READY"
    assert _logicals(repo) == original

    backend = DuckDBBackend()
    info = load_golden.load_set(backend, repo, "wf_0001", "normal")
    assert "MIGDB.MIG_GOLDEN_WF0001_NORMAL.ORDERS" in info["loaded"]
    assert backend.table_exists("MIGDB.MIG_GOLDEN_WF0001_NORMAL.ORDERS")


# --- hand-edited fields survive a resume (ruling 2) -----------------------------------------------

def test_hand_edited_logical_mode_and_keys_survive_a_resume(tmp_path):
    repo = _repo(tmp_path)
    _map_all_three(repo)
    mappings_path = repo.wf("wf_0001", "intake", "mappings.yaml")
    m = io.read_yaml(mappings_path)
    m["sources"]["sales/orders.yxdb"]["logical"] = "ORDERS_V2"
    m["outputs"]["out/sales_summary.yxdb"]["mode"] = "append"
    m["outputs"]["out/excluded_orders.csv"]["keys"] = ["ORDER_ID"]
    io.write_yaml(mappings_path, m)

    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "READY"
    m2 = io.read_yaml(mappings_path)
    assert m2["sources"]["sales/orders.yxdb"]["logical"] == "ORDERS_V2"
    assert m2["outputs"]["out/sales_summary.yxdb"]["mode"] == "append"
    assert m2["outputs"]["out/excluded_orders.csv"]["keys"] == ["ORDER_ID"]
    # And it stays put across a further resume too.
    ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    m3 = io.read_yaml(mappings_path)
    assert m3 == m2


# --- the FQN round trip (ruling 3): no self-collision -----------------------------------------------
#
# Exercises `apply_answers` directly (not the full `ip.run()` -> promote-to-global pipeline):
# changing an already-promoted entry's FQN legitimately conflicts with what's already in
# `mappings/global.yaml` under the OLD value (a separate, correct feature -- NEEDS_HUMAN, not a
# bug), which would make a same-workflow, same-key round trip through the CLI re-trigger that
# conflict on every step and obscure what this ruling actually tests. `apply_answers` still writes
# the workflow's own `intake/mappings.yaml` unconditionally regardless of a global conflict, so its
# `logical` value is the right thing to assert on here.

def test_fqn_round_trip_returns_to_the_original_logical_name_not_the_schema_prefixed_one(tmp_path):
    repo = _repo(tmp_path)
    touchpoints = tpx.run(repo, "wf_0001")
    q1 = next(t for t in touchpoints if t["key"] == "sales/orders.yxdb")
    mappings_path = repo.wf("wf_0001", "intake", "mappings.yaml")

    def answer(fqn):
        return [{"id": q1["id"], "action": "map", "snowflake": fqn}]

    ip.apply_answers(repo, "wf_0001", touchpoints, answer("SALES.RAW.ORDERS"), "wf_owner")
    assert io.read_yaml(mappings_path)["sources"]["sales/orders.yxdb"]["logical"] == "ORDERS"

    ip.apply_answers(repo, "wf_0001", touchpoints, answer("SALES.RAW.ORDERS_ARCHIVE"), "wf_owner")
    assert io.read_yaml(mappings_path)["sources"]["sales/orders.yxdb"]["logical"] == "ORDERS_ARCHIVE"

    ip.apply_answers(repo, "wf_0001", touchpoints, answer("SALES.RAW.ORDERS"), "wf_owner")
    assert io.read_yaml(mappings_path)["sources"]["sales/orders.yxdb"]["logical"] == "ORDERS"


def test_changing_only_the_database_keeps_the_same_table_name_from_colliding_with_itself(tmp_path):
    """The coordinator's own worked example (`ORDERS` -> `ORDERS_ARCHIVE` -> `ORDERS`) turns out to
    already come back to `ORDERS` even under the pre-round-3 code (confirmed by hand: neither
    `ORDERS` nor `ORDERS_ARCHIVE` is ever, at the moment it's computed, present in the seeded "taken"
    set under THAT specific sequence, since the two table names never coincide) -- see the "Fix
    round 3" section of the report for why that test alone isn't RED. This is the sequence that
    actually reproduces ruling 3's self-collision distinctly from ruling 2's "unchanged FQN" case:
    changing only the DATABASE while the TABLE NAME stays `ORDERS` is a genuinely different FQN (so
    ruling 2's same-FQN skip does not apply), but its target table name is identical to the key's
    OWN current logical name -- exactly the case a naive `taken_logicals` seed self-collides on.
    """
    repo = _repo(tmp_path)
    touchpoints = tpx.run(repo, "wf_0001")
    q1 = next(t for t in touchpoints if t["key"] == "sales/orders.yxdb")
    mappings_path = repo.wf("wf_0001", "intake", "mappings.yaml")

    def answer(fqn):
        return [{"id": q1["id"], "action": "map", "snowflake": fqn}]

    ip.apply_answers(repo, "wf_0001", touchpoints, answer("SALES.RAW.ORDERS"), "wf_owner")
    assert io.read_yaml(mappings_path)["sources"]["sales/orders.yxdb"]["logical"] == "ORDERS"

    # Same schema and table name ("ORDERS"), different database -- a real FQN change, but one that
    # should never need the <SCHEMA>_<TABLE> fallback since nothing else is using "ORDERS" either.
    ip.apply_answers(repo, "wf_0001", touchpoints, answer("ANALYTICS.RAW.ORDERS"), "wf_owner")
    assert io.read_yaml(mappings_path)["sources"]["sales/orders.yxdb"]["logical"] == "ORDERS"


# --- backfilled (from: global) entries are stable too ---------------------------------------------

def test_backfilled_from_global_entries_are_stable_across_resumes(tmp_path):
    repo = _repo(tmp_path)
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
    tpx.run(repo, "wf_0001")
    ask, _ = scripted(["n"])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "READY"

    mappings_path = repo.wf("wf_0001", "intake", "mappings.yaml")
    mappings_bytes = mappings_path.read_bytes()
    global_bytes = repo.global_mappings.read_bytes()

    for i in range(1, 4):
        status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
        assert status == "READY", f"resume {i}"
        assert mappings_path.read_bytes() == mappings_bytes, f"resume {i}: mappings.yaml changed"
        assert repo.global_mappings.read_bytes() == global_bytes, f"resume {i}: global.yaml changed"


# --- no duplicate tool_ids --------------------------------------------------------------------------

def test_tool_ids_never_grow_across_resumes(tmp_path):
    repo = _repo(tmp_path)
    _map_all_three(repo)
    for _ in range(3):
        ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert m["sources"]["sales/orders.yxdb"]["tool_ids"] == ["1"]
    assert m["outputs"]["out/sales_summary.yxdb"]["tool_ids"] == ["7"]
    assert m["outputs"]["out/excluded_orders.csv"]["tool_ids"] == ["8"]


# --- user-declared sources are not duplicated across (interactive) resumes -----------------------

def test_user_declared_source_is_not_duplicated_across_interactive_resumes(tmp_path):
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")
    ask1, _ = scripted(["y", r"\\share\fx\rates.yxdb", "FINANCE.RAW.FX_RATES", "", "?", "?", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask1, out=lambda s: None, user="wf_owner")
    m1 = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert len(m1["sources"]) == 1
    assert m1["sources"]["fx/rates.yxdb"]["snowflake"] == "FINANCE.RAW.FX_RATES"

    # A second interactive session that re-declares the exact same source again.
    ask2, _ = scripted(["y", r"\\share\fx\rates.yxdb", "FINANCE.RAW.FX_RATES", "", "?", "?", "?"])
    ip.run(repo, "wf_0001", interactive=True, ask=ask2, out=lambda s: None, user="wf_owner")
    m2 = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert len(m2["sources"]) == 1  # not duplicated under a second key
    assert m2["sources"]["fx/rates.yxdb"] == m1["sources"]["fx/rates.yxdb"]  # byte-for-byte kept


# --- point 5: manifest.answers does not grow or change on a no-op resume -------------------------

def test_manifest_answers_stable_across_resumes(tmp_path):
    repo = _repo(tmp_path)
    _map_all_three(repo)
    answers1 = dict(io.load_manifest(repo, "wf_0001")["answers"])
    for i in range(1, 4):
        ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
        answers_i = dict(io.load_manifest(repo, "wf_0001")["answers"])
        assert answers_i == answers1, f"resume {i}"
