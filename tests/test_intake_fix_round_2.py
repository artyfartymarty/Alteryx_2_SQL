"""Fix round 2 (re-review of fix round 1): the READY guard validated entry PRESENCE only, not
COMPLETENESS. `_missing_blocking_entries` (as it stood after round 1) only checked
`t["key"] not in mappings[section]`; it never checked that the entry actually had `logical` (or
`tool_ids`/`confirmed_by`/a valid `mode`). Live reproduction: seed `intake/mappings.yaml` with
entries for all three of wf_0001's blocking touchpoints but omit `logical` from the source entry --
`ip.run(..., interactive=False, ...)` returned `READY`, and `load_golden.load_set` then raised
`ValueError: source 'sales/orders.yxdb' (tool 1) has no 'logical:' name in intake/mappings.yaml` --
the exact failure the guard was written to make impossible.
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


def _valid_mappings() -> dict:
    """A hand-crafted, fully valid intake/mappings.yaml for wf_0001's 3 blocking touchpoints --
    the same shape apply_answers itself would produce. Each test mutates exactly one field of one
    copy of this to reproduce one rule violation."""
    return {
        "sources": {
            "sales/orders.yxdb": {"snowflake": "SALES.RAW.ORDERS", "logical": "ORDERS",
                                  "tool_ids": ["1"], "confirmed_by": "wf_owner"},
        },
        "outputs": {
            "out/sales_summary.yxdb": {"snowflake": "ANALYTICS.CURATED.SALES_SUMMARY",
                                       "logical": "SALES_SUMMARY", "tool_ids": ["7"],
                                       "confirmed_by": "wf_owner", "mode": "overwrite", "keys": []},
            "out/excluded_orders.csv": {"snowflake": "ANALYTICS.CURATED.EXCLUDED_ORDERS",
                                        "logical": "EXCLUDED_ORDERS", "tool_ids": ["8"],
                                        "confirmed_by": "wf_owner", "mode": "overwrite", "keys": []},
        },
        "constants": {}, "parameters": {}, "macros": {},
    }


def _seed(repo, wf_id, mappings: dict) -> None:
    tpx.run(repo, wf_id)
    io.write_yaml(repo.wf(wf_id, "intake", "mappings.yaml"), mappings)


# --- the reviewer's exact reproduction ------------------------------------------------------------

def test_missing_logical_forces_needs_human_not_ready_and_blocks_load_golden(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    del mappings["sources"]["sales/orders.yxdb"]["logical"]
    _seed(repo, "wf_0001", mappings)

    out_lines: list[str] = []
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    assert io.load_manifest(repo, "wf_0001")["status"]["intake"] == "NEEDS_HUMAN"

    # The message names the entry key and the field, wherever it surfaces:
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert 'sources["sales/orders.yxdb"]: logical is missing' in md
    assert "GUARD" in md

    # The written file was NOT repaired -- the guard only ever reports.
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert "logical" not in m["sources"]["sales/orders.yxdb"]

    # load_golden.py must never be reached with a READY status built on this file; if it were
    # attempted anyway, it fails exactly the way the live reproduction showed.
    assert status != "READY"
    with pytest.raises(ValueError, match="logical"):
        load_golden.load_set(DuckDBBackend(), repo, "wf_0001", "normal")


def test_missing_logical_message_also_printed(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    del mappings["sources"]["sales/orders.yxdb"]["logical"]
    _seed(repo, "wf_0001", mappings)

    out_lines: list[str] = []
    # `out` is accepted regardless of interactive/non-interactive (run()'s signature); the
    # coordinator ruling wants the message "in the returned/printed status line", i.e. wherever
    # `out` sends it, not only in open_questions.md.
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner", out=out_lines.append)
    assert status == "NEEDS_HUMAN"
    assert any("GUARD" in l for l in out_lines)
    assert any('sources["sales/orders.yxdb"]: logical is missing' in l for l in out_lines)


# --- one test per other rule ----------------------------------------------------------------------

def test_bad_snowflake_shape_forces_needs_human(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["sources"]["sales/orders.yxdb"]["snowflake"] = "SALES.ORDERS"  # only 2 parts
    _seed(repo, "wf_0001", mappings)
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert 'sources["sales/orders.yxdb"]: snowflake' in md


def test_lower_case_logical_forces_needs_human(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["sources"]["sales/orders.yxdb"]["logical"] = "orders"
    _seed(repo, "wf_0001", mappings)
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert 'sources["sales/orders.yxdb"]: logical' in md and "not a valid identifier" in md


def test_spaced_logical_forces_needs_human(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["outputs"]["out/sales_summary.yxdb"]["logical"] = "SALES SUMMARY"
    _seed(repo, "wf_0001", mappings)
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"


def test_empty_tool_ids_forces_needs_human(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["sources"]["sales/orders.yxdb"]["tool_ids"] = []
    _seed(repo, "wf_0001", mappings)
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert 'sources["sales/orders.yxdb"]: tool_ids is empty' in md


def test_tool_ids_not_containing_the_touchpoints_id_forces_needs_human(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["sources"]["sales/orders.yxdb"]["tool_ids"] = ["99"]
    _seed(repo, "wf_0001", mappings)
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert 'sources["sales/orders.yxdb"]: tool_ids does not contain tool 1' in md


def test_output_with_unknown_mode_forces_needs_human(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["outputs"]["out/sales_summary.yxdb"]["mode"] = "upsert"  # not a real mode
    _seed(repo, "wf_0001", mappings)
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert 'outputs["out/sales_summary.yxdb"]: mode' in md and "'upsert'" in md


def test_merge_mode_with_empty_keys_forces_needs_human(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["outputs"]["out/sales_summary.yxdb"]["mode"] = "merge"
    mappings["outputs"]["out/sales_summary.yxdb"]["keys"] = []
    _seed(repo, "wf_0001", mappings)
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert 'outputs["out/sales_summary.yxdb"]: keys must be non-empty when mode is merge' in md


def test_duplicate_logical_across_a_source_and_an_output_forces_needs_human(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["outputs"]["out/sales_summary.yxdb"]["logical"] = "ORDERS"  # collides with the source
    _seed(repo, "wf_0001", mappings)
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert "'ORDERS' is used by more than one entry" in md or '"ORDERS" is used by more than one entry' in md
    assert 'sources["sales/orders.yxdb"]' in md and 'outputs["out/sales_summary.yxdb"]' in md


# --- user-declared sources are exempt from the tool-id rule, not the others ----------------------

def test_user_declared_source_with_empty_tool_ids_does_not_trip_the_tool_id_rule(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["sources"]["fx/rates.yxdb"] = {"snowflake": "FINANCE.RAW.FX_RATES", "logical": "FX_RATES",
                                            "tool_ids": [], "confirmed_by": "wf_owner",
                                            "note": "user-declared"}
    _seed(repo, "wf_0001", mappings)
    out_lines: list[str] = []
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner", out=out_lines.append)
    assert status == "READY"  # the user-declared entry's empty tool_ids alone must not block READY
    assert not any("GUARD" in l or "tool_ids" in l for l in out_lines)


def test_user_declared_source_with_bad_logical_still_trips_the_logical_rule(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    mappings["sources"]["fx/rates.yxdb"] = {"snowflake": "FINANCE.RAW.FX_RATES", "logical": "fx rates",
                                            "tool_ids": [], "confirmed_by": "wf_owner",
                                            "note": "user-declared"}
    _seed(repo, "wf_0001", mappings)
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "NEEDS_HUMAN"
    md = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert 'sources["fx/rates.yxdb"]: logical' in md


# --- positive control: a fully valid file is READY, and load_golden succeeds ---------------------

def test_a_fully_valid_file_is_ready_and_load_golden_succeeds(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    _seed(repo, "wf_0001", _valid_mappings())
    status = ip.run(repo, "wf_0001", interactive=False, user="wf_owner")
    assert status == "READY"
    backend = DuckDBBackend()
    info = load_golden.load_set(backend, repo, "wf_0001", "normal")
    assert info["loaded"]
    assert backend.table_exists(info["loaded"][0])


# --- CLI exit code ----------------------------------------------------------------------------------

def test_cli_exits_1_for_needs_human(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    mappings = _valid_mappings()
    del mappings["sources"]["sales/orders.yxdb"]["logical"]
    _seed(repo, "wf_0001", mappings)
    assert ip.main(["wf_0001", "--no-interactive", "--user", "wf_owner",
                   "--root", str(repo.root)]) == 1


def test_cli_exits_0_for_a_fully_valid_ready_file(tmp_path):
    repo = _repo(tmp_path, "wf_0001")
    _seed(repo, "wf_0001", _valid_mappings())
    assert ip.main(["wf_0001", "--no-interactive", "--user", "wf_owner",
                   "--root", str(repo.root)]) == 0
