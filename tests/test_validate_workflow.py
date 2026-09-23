"""The chain test: every segment on its upstream segments' ACTUAL output (plan Task W1, ruling R-W1).

`validate_segment` feeds each segment the golden intermediate Alteryx produced at its input
boundary, so a difference that only appears once segments are stitched together is invisible to
it. `validate_workflow` loads only the raw golden inputs (+ `targets_before`), runs every segment in
`segments/order.json` wave order on the actual tables its upstream segments wrote, judges every
boundary (work stream) and every final output (target) with the unchanged `compare.py`, localises
the first divergence, and runs the whole chain twice from fresh backends for idempotency.

The fixtures are in `tests/chain_fixtures.py`; their premise (each segment passes ALONE) is asserted
here, never assumed. Nothing here has run on Snowflake or Alteryx.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import validate_segment as vs
import validate_snowpark as vsp
import validate_workflow as vw
from lib import typed_csv, validation as v
from lib.backend import DuckDBBackend
from lib.io import read_json, read_yaml, write_json, write_yaml
from lib.paths import Repo
from tests import chain_fixtures as cf
from tests import dbt_fixtures as dbtf
from tests.helpers import prepare_workflow

COMPOSITION = cf.WF_COMPOSITION
DRIFT = cf.WF_DRIFT


def _segments(repo, wf):
    return [seg for wave in read_json(repo.wf(wf, "segments", "order.json")) for seg in wave]


def _alone(repo, wf):
    """Every segment's verdict when validated in isolation, fed its golden intermediates."""
    return {seg: vs.validate_segment(repo, wf, seg)["verdict"] for seg in _segments(repo, wf)}


def run_cli(root, wf, *extra):
    return subprocess.run([sys.executable, str(Path(vw.__file__)), wf, *extra, "--root", str(root)],
                          capture_output=True, text=True)


# --- the two fixtures of the risk register ----------------------------------------------------------

def test_each_segment_passes_alone_but_the_chain_fails_at_the_first_boundary(tmp_path):
    repo = cf.build_composition(tmp_path)
    alone = _alone(repo, COMPOSITION)
    assert all(verdict.startswith("PASS") for verdict in alone.values()), alone
    assert alone["seg_01"] == "PASS_WITH_ACCEPTED_DIFF"      # the accepted rounding

    r = vw.validate_workflow(repo, COMPOSITION, ["normal"])

    assert r["verdict"] == "FAIL"
    assert r["workflow"] == COMPOSITION
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": "3_Output",
                                     "output": "MIG_WORK.WF0008_SEG_02_OUT", "set": "normal"}
    assert r["boundaries"][0] == {"segment": "seg_01", "stream": "2_Output", "verdict": "PASS_WITH_ACCEPTED_DIFF"}
    assert [b["segment"] for b in r["boundaries"]] == ["seg_01", "seg_02"]
    assert r["boundaries"][1]["verdict"] == "FAIL"
    assert r["finals"] == [{"segment": "seg_03", "stream": "3_Output", "output": "MILLI_OUT", "verdict": "FAIL"}]
    # every cluster says which segment and stream it belongs to; seg_02's MILLI is 5 off, a LOGIC diff
    assert all({"segment", "stream"} <= set(cluster) for cluster in r["diff_clusters"])
    assert any(c["segment"] == "seg_02" and c["class"] == "LOGIC" and "MILLI" in c["columns"]
               for c in r["diff_clusters"])
    assert set(r["checks"]) == {"seg_01:2_Output:work:MIG_WORK.WF0008_SEG_01_OUT",
                                "seg_02:3_Output:work:MIG_WORK.WF0008_SEG_02_OUT",
                                "seg_03:3_Output:target:MILLI_OUT"}
    # the segments' own reports are untouched by the chain
    assert read_json(repo.seg(COMPOSITION, "seg_02", "validation.json"))["verdict"] == "PASS"


def test_accumulated_rounding_is_chain_drift_not_a_boundary(tmp_path):
    repo = cf.build_drift(tmp_path)
    alone = _alone(repo, DRIFT)
    assert all(verdict.startswith("PASS") for verdict in alone.values()), alone

    r = vw.validate_workflow(repo, DRIFT, ["normal"])

    assert r["verdict"] == "FAIL"
    assert r["divergence_kind"] == "chain_drift"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": "3_Output", "output": "TOTALS", "set": "normal"}
    assert r["boundaries"] and all(b["verdict"].startswith("PASS") for b in r["boundaries"])
    assert r["finals"] == [{"segment": "seg_02", "stream": "3_Output", "output": "TOTALS", "verdict": "FAIL"}]
    assert r["needs_human"] is False


def test_a_chain_that_matches_passes_and_writes_its_reports(tmp_path):
    repo = cf.build_composition(tmp_path, rounding=False)

    r = vw.validate_workflow(repo, COMPOSITION)

    assert r["verdict"] == "PASS"
    assert r["first_divergence"] is None and r["divergence_kind"] is None
    assert r["idempotent"] is True and r["idempotency_diff"] == []
    assert r["sets"] == {"normal": "PASS"}
    assert "error" not in r
    top = read_json(repo.wf(COMPOSITION, "validation_workflow.json"))
    assert top == r
    per_set = read_json(repo.wf(COMPOSITION, "validation_workflow.normal.json"))
    assert per_set["verdict"] == "PASS" and "sets" not in per_set and per_set["golden_set"] == "normal"
    assert [b["verdict"] for b in r["boundaries"]] == ["PASS", "PASS"]
    assert [f["verdict"] for f in r["finals"]] == ["PASS"]


def test_a_non_deterministic_segment_makes_the_chain_non_idempotent(tmp_path):
    # UNIFORM(1, 1000000, RANDOM()) is the random form lib/backend.py translates (validate_segment's
    # own non-determinism test uses it); a million-way draw per row makes two equal runs negligible.
    repo = cf.build_composition(tmp_path, rounding=False,
                                seg_02_expression="PRICE * 1000 + UNIFORM(1, 1000000, RANDOM()) * 0.000001")

    r = vw.validate_workflow(repo, COMPOSITION)

    assert r["idempotent"] is False
    assert r["verdict"] == "FAIL"
    assert "MIG_WORK.WF0008_SEG_02_OUT" in r["idempotency_diff"]
    assert r["divergence_kind"] == "boundary" and r["first_divergence"]["segment"] == "seg_02"


def test_a_segment_that_raises_in_the_chain_is_a_boundary_divergence(tmp_path):
    repo = cf.build_composition(tmp_path, rounding=False)
    cf.make_seg_03_read_what_seg_02_never_writes(repo)
    alone = _alone(repo, COMPOSITION)
    assert all(verdict.startswith("PASS") for verdict in alone.values()), alone   # seg_03 too

    r = vw.validate_workflow(repo, COMPOSITION)

    assert r["verdict"] == "FAIL"
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"]["segment"] == "seg_03"
    assert r["first_divergence"]["stream"] is None
    assert r["first_divergence"]["set"] == "normal"
    assert "seg_03" in r["error"]
    assert r["idempotent"] is None                    # the first run never completed
    # what ran before the raise is still judged and reported
    assert [b["verdict"] for b in r["boundaries"]] == ["PASS", "PASS"]
    assert r["finals"] == []


def test_usage_errors_leave_no_report(tmp_path):
    def stale(repo):
        write_json(repo.wf(COMPOSITION, "validation_workflow.json"), {"verdict": "PASS"})
        write_json(repo.wf(COMPOSITION, "validation_workflow.normal.json"), {"verdict": "PASS"})

    def gone(repo):
        return not (repo.wf(COMPOSITION, "validation_workflow.json").exists()
                    or repo.wf(COMPOSITION, "validation_workflow.normal.json").exists())

    # 1. no segments/order.json
    repo = cf.build_composition(tmp_path / "a", rounding=False)
    repo.wf(COMPOSITION, "segments", "order.json").unlink()
    stale(repo)
    with pytest.raises(FileNotFoundError, match="order.json"):
        vw.validate_workflow(repo, COMPOSITION)
    assert gone(repo)
    stale(repo)
    done = run_cli(tmp_path / "a", COMPOSITION)
    assert done.returncode == 2 and "Traceback" not in done.stderr and "order.json" in done.stderr
    assert gone(repo)

    # 2. a segment with no proc.sql
    repo = cf.build_composition(tmp_path / "b", rounding=False)
    repo.seg(COMPOSITION, "seg_02", "proc.sql").unlink()
    stale(repo)
    with pytest.raises(FileNotFoundError, match="seg_02"):
        vw.validate_workflow(repo, COMPOSITION)
    assert gone(repo)
    stale(repo)
    done = run_cli(tmp_path / "b", COMPOSITION)
    assert done.returncode == 2 and "Traceback" not in done.stderr and "proc.sql" in done.stderr
    assert gone(repo)

    # 3. no golden sets at all ([] explicitly; the CLI reaches the same state through the manifest)
    repo = cf.build_composition(tmp_path / "c", rounding=False)
    stale(repo)
    with pytest.raises(ValueError, match="golden sets"):
        vw.validate_workflow(repo, COMPOSITION, [])
    assert gone(repo)
    manifest = read_json(repo.wf(COMPOSITION, "manifest.json"))
    manifest["golden_sets"] = []
    write_json(repo.wf(COMPOSITION, "manifest.json"), manifest)
    stale(repo)
    done = run_cli(tmp_path / "c", COMPOSITION)
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert gone(repo)


def test_the_cli_on_a_workflow_that_was_never_prepared_is_a_usage_error(tmp_path):
    done = run_cli(tmp_path, "wf_0404")
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert not (tmp_path / "workflows").exists()


def test_the_cli_exit_code_follows_the_chain_verdict(tmp_path):
    cf.build_composition(tmp_path / "pass", rounding=False)
    done = run_cli(tmp_path / "pass", COMPOSITION)
    assert done.returncode == 0, done.stderr
    assert "PASS" in done.stdout

    cf.build_composition(tmp_path / "fail")
    done = run_cli(tmp_path / "fail", COMPOSITION, "--set", "normal", "--backend", "duckdb")
    assert done.returncode == 1, done.stderr
    assert "seg_02" in done.stdout and "boundary" in done.stdout


def test_main_returns_two_on_an_unexpected_exception(tmp_path, monkeypatch):
    cf.build_composition(tmp_path, rounding=False)

    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")
    monkeypatch.setattr(vw, "validate_workflow", boom)
    assert vw.main([COMPOSITION, "--root", str(tmp_path)]) == 2


def test_a_golden_set_that_is_no_longer_requested_loses_its_stale_report(tmp_path):
    repo = cf.build_composition(tmp_path, rounding=False)
    write_json(repo.wf(COMPOSITION, "validation_workflow.old.json"), {"verdict": "FAIL"})
    vw.validate_workflow(repo, COMPOSITION)
    assert not repo.wf(COMPOSITION, "validation_workflow.old.json").exists()


# --- engines crossing at a Snowpark seam --------------------------------------------------------------

def test_a_snowpark_segment_writing_the_wrong_type_is_a_type_difference_in_the_chain(tmp_path):
    repo = prepare_workflow(tmp_path, "wf_0006")
    proc_py = repo.seg("wf_0006", "seg_02", "proc.py")
    text = proc_py.read_text(encoding="utf-8")
    wrong = text.replace('StructField("RECOGNIZED", DoubleType())', 'StructField("RECOGNIZED", StringType())')
    assert wrong != text
    proc_py.write_text(wrong, encoding="utf-8", newline="\n")

    r = vw.validate_workflow(repo, "wf_0006", ["normal"])

    assert r["verdict"] == "FAIL"
    type_clusters = [c for c in r["diff_clusters"] if c["class"] == "TYPE" and "RECOGNIZED" in c["columns"]]
    assert type_clusters, r["diff_clusters"]
    assert all(c["segment"] == "seg_02" for c in type_clusters)
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"]["segment"] == "seg_02"
    assert r["first_divergence"]["stream"] == "3_1"


# --- a dbt workflow: the chain IS its full dbt run ------------------------------------------------------

def test_a_dbt_workflow_delegates_to_validate_dbt_and_reads_back_its_chain_report(tmp_path):
    repo = dbtf.build_dbt_workflow(tmp_path, sets=("normal",))

    r = vw.validate_workflow(repo, dbtf.WF)

    assert r == read_json(repo.wf(dbtf.WF, "validation_workflow.json"))
    assert r["verdict"] == "PASS" and r["target"] == "dbt"
    assert r["boundaries"] == [{"segment": "seg_01", "stream": "2_T", "verdict": "PASS"}]
    assert [(f["segment"], f["output"], f["verdict"]) for f in r["finals"]] == [
        ("seg_02", "ITEMS_OUT", "PASS"), ("seg_02", "ITEMS_HIST", "PASS")]
    assert r["idempotent"] is True and r["first_divergence"] is None
    # validate_dbt's own per-segment reports were written by the same run
    assert read_json(repo.seg(dbtf.WF, "seg_02", "validation.json"))["verdict"] == "PASS"


def test_a_dbt_model_that_fails_to_run_is_a_raised_boundary_in_the_chain_report(tmp_path):
    repo = dbtf.build_dbt_workflow(tmp_path, sets=("normal",), replace={
        "models/items_out.sql": "{{ config(materialized='table', alias='ITEMS_OUT') }}\n"
                                "select NO_SUCH_COLUMN from {{ ref('wf0009_seg_01_out') }}\n"})

    r = vw.validate_workflow(repo, dbtf.WF)

    assert r["verdict"] == "FAIL"
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": None, "output": None, "set": "normal"}
    assert "items_out" in r["error"] and "seg_02" in r["error"]


# --- the report shape itself ---------------------------------------------------------------------------

def _entry(seg, stream, kind, output, verdict, clusters=()):
    report = {"verdict": verdict, "checks": {"schema": "PASS"}, "diff_clusters": list(clusters),
              "normalizations_applied": [], "needs_human": False, "truncated": False}
    return {"segment": seg, "stream": stream, "kind": kind, "output": output,
            "relation": output if kind == "work" else f"MIGDB.MIG_WORK.{output}", "report": report}


def test_chain_report_puts_the_first_failing_boundary_before_a_later_raise_or_an_earlier_final():
    entries = [_entry("seg_01", "1_T", "target", "EARLY_OUT", "FAIL"),
               _entry("seg_01", "1_T", "work", "MIG_WORK.A", "PASS"),
               _entry("seg_02", "2_T", "work", "MIG_WORK.B", "FAIL")]
    r = v.chain_report("wf_0001", "normal", entries, v.ChainError("seg_03", RuntimeError("boom")))
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": "2_T", "output": "MIG_WORK.B", "set": "normal"}
    assert r["error"].startswith("chain stopped at seg_03")

    raised = v.chain_report("wf_0001", "normal", entries[:2], v.ChainError("seg_03", RuntimeError("boom")))
    assert raised["divergence_kind"] == "boundary"
    assert raised["first_divergence"] == {"segment": "seg_03", "stream": None, "output": None, "set": "normal"}

    drift = v.chain_report("wf_0001", "normal", entries[:2])
    assert drift["divergence_kind"] == "chain_drift"
    assert drift["first_divergence"] == {"segment": "seg_01", "stream": "1_T", "output": "EARLY_OUT", "set": "normal"}

    clean = v.chain_report("wf_0001", "normal", entries[1:2])
    assert (clean["verdict"], clean["divergence_kind"], clean["first_divergence"]) == ("PASS", None, None)


def test_non_determinism_alone_is_localised_to_the_first_output_whose_two_runs_differ():
    """Every output within tolerance on the judged run, but the second run from a fresh backend
    wrote different rows: still a FAIL, and a defect of the segment that wrote the first diverging
    output in chain order -- a boundary divergence (a fixer can make a segment deterministic;
    nothing about it is accumulated tolerance)."""
    entries = [_entry("seg_01", "1_T", "work", "MIG_WORK.A", "PASS"),
               _entry("seg_02", "2_T", "work", "MIG_WORK.B", "PASS"),
               _entry("seg_03", "2_T", "target", "OUT", "PASS")]
    r = v.chain_report("wf_0001", "normal", entries, diverging=["MIGDB.MIG_WORK.OUT", "MIG_WORK.B"])
    assert r["verdict"] == "FAIL"
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": "2_T", "output": "MIG_WORK.B", "set": "normal"}


def test_a_boundary_in_any_golden_set_outranks_a_drift_in_an_earlier_one(tmp_path):
    repo = Repo(tmp_path)
    drift = v.chain_report("wf_0001", "normal", [_entry("seg_01", "1_T", "work", "MIG_WORK.A", "PASS"),
                                                 _entry("seg_02", "2_T", "target", "OUT", "FAIL")])
    boundary = v.chain_report("wf_0001", "edge", [_entry("seg_01", "1_T", "work", "MIG_WORK.A", "FAIL")])
    top = v.write_workflow_reports(repo, "wf_0001", ["normal", "edge"], {"normal": drift, "edge": boundary},
                                   (True, []))
    assert top["verdict"] == "FAIL"
    assert top["divergence_kind"] == "boundary" and top["first_divergence"]["set"] == "edge"
    assert top["sets"] == {"normal": "FAIL", "edge": "FAIL"}
    assert read_json(repo.wf("wf_0001", "validation_workflow.normal.json"))["divergence_kind"] == "chain_drift"


# --- fix round 1 -----------------------------------------------------------------------------------
# I2: a Snowflake table has no row order, so a procedure that depends on its input's physical order
# is non-deterministic in production. The first chain run hands rows on in physical order; the
# idempotency re-run presents every segment's inputs in REVERSED order, so such a segment's two runs
# differ: a non-idempotent chain, a boundary divergence at that segment.

@pytest.mark.parametrize("snowpark", [False, True], ids=["sql", "snowpark"])
def test_a_segment_that_relies_on_its_input_order_is_a_non_idempotent_boundary(tmp_path, snowpark):
    repo = cf.build_order_dependent(tmp_path, snowpark=snowpark)
    alone = {seg: (vsp.validate_snowpark if snowpark and seg == "seg_02" else vs.validate_segment)(
        repo, COMPOSITION, seg)["verdict"] for seg in _segments(repo, COMPOSITION)}
    assert all(verdict == "PASS" for verdict in alone.values()), alone

    r = vw.validate_workflow(repo, COMPOSITION)

    assert r["verdict"] == "FAIL"
    assert r["idempotent"] is False
    assert r["idempotency_diff"] == ["MIG_WORK.WF0008_SEG_02_OUT", "MIGDB.MIG_WORK.MILLI_OUT"]
    assert all(b["verdict"] == "PASS" for b in r["boundaries"]), r["boundaries"]   # the judged run matched
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": "3_Output",
                                     "output": "MIG_WORK.WF0008_SEG_02_OUT", "set": "normal"}


@pytest.mark.parametrize("snowpark", [False, True], ids=["sql", "snowpark"])
def test_an_upstream_order_by_does_not_hide_a_downstream_order_dependence(tmp_path, snowpark):
    """seg_01 ends with ORDER BY PRICE, so a sorted hand-off and a physical one differ, and seg_02
    (the first two records of whatever arrives) keeps 3 and 1 where Alteryx kept 1 and 2."""
    repo = cf.build_order_dependent(tmp_path, snowpark=snowpark, upstream_order_by="PRICE")
    r = vw.validate_workflow(repo, COMPOSITION)
    assert r["verdict"] == "FAIL"
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"]["segment"] == "seg_02" and r["first_divergence"]["stream"] == "3_Output"
    assert r["idempotent"] is False


def test_the_wf_0006_snowpark_segment_without_its_own_sort_fails_the_chain(tmp_path):
    """The canned wf_0006 seg_02 sorts its input itself; with that sort removed and seg_01's output
    written in a different order, the carry-over runs in the wrong order: the chain FAILs at seg_02."""
    repo = prepare_workflow(tmp_path, "wf_0006")
    proc_py = repo.seg("wf_0006", "seg_02", "proc.py")
    text = proc_py.read_text(encoding="utf-8")
    unsorted = text.replace('    pdf = pdf.sort_values(["CUSTOMER", "PERIOD"]).reset_index(drop=True)\n', "")
    assert unsorted != text
    proc_py.write_text(unsorted, encoding="utf-8", newline="\n")
    proc_sql = repo.seg("wf_0006", "seg_01", "proc.sql")
    text = proc_sql.read_text(encoding="utf-8")
    reordered = text.replace("  FROM t2_filter_t;", "  FROM t2_filter_t\n  ORDER BY PERIOD DESC, CUSTOMER DESC;")
    assert reordered != text
    proc_sql.write_text(reordered, encoding="utf-8", newline="\n")

    r = vw.validate_workflow(repo, "wf_0006", ["normal"])

    assert r["verdict"] == "FAIL"
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"]["segment"] == "seg_02" and r["first_divergence"]["stream"] == "3_1"


def test_the_idempotency_rerun_presents_every_input_in_reversed_order(tmp_path, monkeypatch):
    """What the Snowpark seg_02 was handed, run by run: the first run in physical (file) order, the
    re-run reversed -- and not reversed twice when the order-preserving seg_01 already passed its
    reversed raw input on in reverse."""
    repo = cf.build_order_dependent(tmp_path, snowpark=True)
    seen = []
    real = vw.handoff.load_into_snowpark

    def spy(session, fqn, table):
        if fqn == "MIG_WORK.WF0008_SEG_01_OUT":
            seen.append([row[0] for row in table["rows"]])
        return real(session, fqn, table)
    monkeypatch.setattr(vw.handoff, "load_into_snowpark", spy)
    vw.validate_workflow(repo, COMPOSITION)
    assert seen == [[1, 2, 3], [3, 2, 1]]


def test_the_rerun_reverses_what_an_upstream_sort_would_otherwise_hand_on_unchanged(tmp_path, monkeypatch):
    repo = cf.build_order_dependent(tmp_path, snowpark=True, upstream_order_by="PRICE")
    seen = []
    real = vw.handoff.load_into_snowpark

    def spy(session, fqn, table):
        if fqn == "MIG_WORK.WF0008_SEG_01_OUT":
            seen.append([row[0] for row in table["rows"]])
        return real(session, fqn, table)
    monkeypatch.setattr(vw.handoff, "load_into_snowpark", spy)
    vw.validate_workflow(repo, COMPOSITION)
    assert seen == [[3, 1, 2], [2, 1, 3]]          # PRICE order, then exactly reversed


def test_reversing_a_table_keeps_its_types_and_reverses_its_physical_order():
    backend = DuckDBBackend()
    try:
        backend.execute("CREATE TABLE MIGDB.MIG_WORK.T (ID BIGINT, D DECIMAL(19,3), S VARCHAR)")
        backend.execute("INSERT INTO MIGDB.MIG_WORK.T VALUES (3, 1.5, 'c'), (1, NULL, 'a'), (2, 2.5, 'b')")
        types = backend.table_columns("MIGDB.MIG_WORK.T")
        vw._reverse(backend, "MIGDB.MIG_WORK.T")
        assert [row[0] for row in backend.query("SELECT * FROM MIGDB.MIG_WORK.T")[1]] == [2, 1, 3]
        assert backend.table_columns("MIGDB.MIG_WORK.T") == types
    finally:
        backend.close()


# I3: a Snowpark segment that loses its output must be judged on a missing table, never on the copy
# the chain's backend still holds from before it ran.

def test_a_vanished_snowpark_output_is_a_missing_table_not_a_stale_copy(tmp_path):
    repo = cf.build_composition(tmp_path, rounding=False)
    wf = COMPOSITION
    # MILLI_OUT becomes a merge target that ALREADY holds exactly the rows the correct run produces
    typed_csv.write_table(repo.wf(wf, "golden", "targets_before", "normal", "MILLI_OUT.csv"),
                          {"fields": cf.MILLI_FIELDS, "rows": cf.MILLI_ROWS})
    mappings = read_yaml(repo.wf(wf, "intake", "mappings.yaml"))
    mappings["outputs"]["out/milli_out.yxdb"]["mode"] = "merge"
    write_yaml(repo.wf(wf, "intake", "mappings.yaml"), mappings)
    contract = read_json(repo.seg(wf, "seg_03", "contract.json"))
    contract["target"] = "snowpark"
    for output in (contract["outputs"][0], contract["output"]):
        output["write_mode"] = "merge"
    write_json(repo.seg(wf, "seg_03", "contract.json"), contract)
    repo.seg(wf, "seg_03", "proc.sql").unlink()
    repo.seg(wf, "seg_03", "proc.py").write_text(
        "# tool 9: Output Data (merge on ID, logical MILLI_OUT) -- broken: drops its own target\n"
        "def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):\n"
        "    session.table(f\"{tgt_db}.{tgt_schema}.MILLI_OUT\").drop_table()\n"
        "    return \"OK\"\n", encoding="utf-8", newline="\n")

    r = vw.validate_workflow(repo, wf)

    assert r["verdict"] == "FAIL"
    assert r["finals"] == [{"segment": "seg_03", "stream": "3_Output", "output": "MILLI_OUT", "verdict": "FAIL"}]
    assert "MIGDB.MIG_WORK.MILLI_OUT does not exist" in r["error"]


# M2: an idempotency re-run that raises is blamed on the segment that raised.

RAISES_ON_SECOND_RUN = '''# tool 3: Formula -- MILLI = PRICE in thousandths; raises on its second run
import pathlib
from snowflake.snowpark.types import DecimalType, LongType, StructField, StructType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    marker = pathlib.Path(MARKER)
    if marker.exists():
        raise RuntimeError("flaky: second run")
    marker.write_text("x")
    rows = [[int(r["ID"]), r["PRICE"] * 1000] for r in session.table("MIG_WORK.WF0008_SEG_01_OUT").collect()]
    schema = StructType([StructField("ID", LongType()), StructField("MILLI", DecimalType(19, 3))])
    session.create_dataframe(rows, schema=schema).write.mode("overwrite").save_as_table("MIG_WORK.WF0008_SEG_02_OUT")
    return "OK"
'''


def test_a_rerun_that_raises_is_a_boundary_at_the_raising_segment(tmp_path):
    repo = cf.build_composition(tmp_path, rounding=False)
    wf = COMPOSITION
    contract = read_json(repo.seg(wf, "seg_02", "contract.json"))
    contract["target"] = "snowpark"
    write_json(repo.seg(wf, "seg_02", "contract.json"), contract)
    repo.seg(wf, "seg_02", "proc.sql").unlink()
    marker = repr(str(tmp_path / "seg_02_ran_once"))
    repo.seg(wf, "seg_02", "proc.py").write_text(RAISES_ON_SECOND_RUN.replace("MARKER", marker),
                                                  encoding="utf-8", newline="\n")

    r = vw.validate_workflow(repo, wf)

    assert r["verdict"] == "FAIL" and r["idempotent"] is False
    assert all(b["verdict"] == "PASS" for b in r["boundaries"])       # the judged (first) run was right
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": None, "output": None, "set": "normal"}
    assert "seg_02" in r["error"] and "flaky: second run" in r["error"]


def test_chain_report_places_a_rerun_raise_at_its_segment_in_chain_order():
    entries = [_entry("seg_01", "1_T", "work", "MIG_WORK.A", "PASS"),
               _entry("seg_02", "2_T", "work", "MIG_WORK.B", "PASS"),
               _entry("seg_03", "3_T", "work", "MIG_WORK.C", "FAIL")]
    rerun = v.ChainError("seg_02", RuntimeError("flaky"))
    r = v.chain_report("wf_0001", "normal", entries, diverging=["MIG_WORK.A", "MIG_WORK.B", "MIG_WORK.C"],
                       rerun_error=rerun)
    assert r["first_divergence"] == {"segment": "seg_02", "stream": None, "output": None, "set": "normal"}
    assert "idempotency re-run" in r["error"] and "seg_02" in r["error"]
    earlier = v.chain_report("wf_0001", "normal", entries, diverging=["MIG_WORK.C"],
                             rerun_error=v.ChainError("seg_03", RuntimeError("flaky")))
    assert earlier["first_divergence"]["segment"] == "seg_03" and earlier["first_divergence"]["stream"] == "3_T"


# M5: one tolerance for boundaries and finals, on the keyed path too.

def test_a_keyed_final_past_tolerance_is_chain_drift_while_every_boundary_is_inside_it(tmp_path):
    repo = cf.build_keyed_drift(tmp_path, rows=3)
    alone = _alone(repo, DRIFT)
    assert alone == {"seg_01": "PASS", "seg_02": "PASS"}, alone
    r = vw.validate_workflow(repo, DRIFT)
    assert r["verdict"] == "FAIL"
    assert [b["verdict"] for b in r["boundaries"]] == ["PASS"]
    assert r["divergence_kind"] == "chain_drift"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": "3_Output", "output": "TOTALS", "set": "normal"}


def test_a_keyed_final_inside_tolerance_passes(tmp_path):
    repo = cf.build_keyed_drift(tmp_path, rows=1)
    r = vw.validate_workflow(repo, DRIFT)
    assert r["verdict"] == "PASS" and r["first_divergence"] is None


# M7

def test_a_chain_error_carries_typed_entries():
    error = v.ChainError("seg_03", RuntimeError("boom"))
    assert error.entries == [] and error.segment == "seg_03"


# --- fix round 3 -----------------------------------------------------------------------------------
# R1: an upstream that reorders its input only PARTLY (a sort on a non-unique key, a Filter's
# branches unioned back) does not hand on the exact reverse of the first run's order, so the re-run
# reverses it too: the downstream first-N consumer sees a different order and is caught (the
# re-reviewer's A2 and A3).

@pytest.mark.parametrize("snowpark", [False, True], ids=["sql", "snowpark"])
@pytest.mark.parametrize("select", [cf.TIE_SORT_SELECT, cf.FILTER_UNION_SELECT], ids=["tie_sort", "filter_union"])
def test_a_partial_reorder_upstream_does_not_hide_a_first_n_consumer(tmp_path, select, snowpark):
    repo = cf.build_partial_reorder(tmp_path, seg_01_select=select, snowpark=snowpark)
    alone = {seg: (vsp.validate_snowpark if snowpark and seg == "seg_02" else vs.validate_segment)(
        repo, COMPOSITION, seg)["verdict"] for seg in _segments(repo, COMPOSITION)}
    assert all(verdict == "PASS" for verdict in alone.values()), alone

    r = vw.validate_workflow(repo, COMPOSITION)

    assert all(b["verdict"] == "PASS" for b in r["boundaries"]), r["boundaries"]   # the judged run matched
    assert r["verdict"] == "FAIL" and r["idempotent"] is False
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": "3_Output",
                                     "output": "MIG_WORK.WF0008_SEG_02_OUT", "set": "normal"}


def test_the_rerun_presents_a_partly_reordered_input_reversed(tmp_path, monkeypatch):
    repo = cf.build_partial_reorder(tmp_path, seg_01_select=cf.TIE_SORT_SELECT, snowpark=True)
    seen = []
    real = vw.handoff.load_into_snowpark

    def spy(session, fqn, table):
        if fqn == "MIG_WORK.WF0008_SEG_01_OUT":
            seen.append([int(row[0]) for row in table["rows"]])
        return real(session, fqn, table)
    monkeypatch.setattr(vw.handoff, "load_into_snowpark", spy)
    vw.validate_workflow(repo, COMPOSITION)
    # run 1: 1, 3, 2, 4; the re-run's seg_01 (on reversed raw input) wrote 3, 1, 4, 2 -- not the exact
    # reverse of run 1 -- so it is reversed once more before seg_02 sees it
    assert seen == [[1, 3, 2, 4], [2, 4, 1, 3]]


# R2: a segment whose stream is a VIEW (with its own ORDER BY) must not crash the re-run.

def test_a_correct_segment_that_writes_its_stream_as_a_sorted_view_still_passes(tmp_path):
    repo = cf.build_composition(tmp_path, rounding=False)
    cf.make_seg_01_write_a_sorted_view(repo)
    assert vs.validate_segment(repo, COMPOSITION, "seg_01")["verdict"] == "PASS"

    r = vw.validate_workflow(repo, COMPOSITION)

    assert r["verdict"] == "PASS" and r["idempotent"] is True, r.get("error")
    done = run_cli(tmp_path, COMPOSITION)
    assert done.returncode == 0 and "Traceback" not in done.stderr, done.stderr


def test_reversing_a_view_materialises_it_into_a_reversed_table():
    backend = DuckDBBackend()
    try:
        backend.execute("CREATE TABLE MIG_WORK.BASE (ID BIGINT, S VARCHAR)")
        backend.execute("INSERT INTO MIG_WORK.BASE VALUES (2, 'b'), (1, 'a'), (3, 'c')")
        backend.execute("CREATE VIEW MIG_WORK.SORTED AS SELECT ID, S FROM MIG_WORK.BASE ORDER BY ID")
        v.reverse_physical_order(backend, "MIG_WORK.SORTED")
        assert [row[0] for row in backend.query("SELECT * FROM MIG_WORK.SORTED")[1]] == [3, 2, 1]
        assert [c["type"] for c in backend.table_columns("MIG_WORK.SORTED")] == ["BIGINT", "VARCHAR"]
        # a view no longer: a second reversal works on the table it became
        v.reverse_physical_order(backend, "MIG_WORK.SORTED")
        assert [row[0] for row in backend.query("SELECT * FROM MIG_WORK.SORTED")[1]] == [1, 2, 3]
    finally:
        backend.close()


def test_a_reversal_that_fails_is_reported_at_its_segment_never_a_crash(tmp_path, monkeypatch):
    repo = cf.build_composition(tmp_path, rounding=False)
    real = vw._reverse

    def broken(backend, fqn):
        if fqn == "MIG_WORK.WF0008_SEG_01_OUT":
            raise RuntimeError("cannot reverse this relation")
        return real(backend, fqn)
    monkeypatch.setattr(vw, "_reverse", broken)
    # a seg_01 whose output is NOT the exact reverse on the re-run, so seg_02's input must be reversed
    path = repo.seg(COMPOSITION, "seg_01", "proc.sql")
    path.write_text(path.read_text(encoding="utf-8").replace(
        "  SELECT ID, PRICE FROM t2_formula;", "  SELECT ID, PRICE FROM t2_formula ORDER BY PRICE;"),
        encoding="utf-8", newline="\n")

    r = vw.validate_workflow(repo, COMPOSITION)

    assert r["verdict"] == "FAIL" and r["idempotent"] is False
    assert r["divergence_kind"] == "boundary"
    assert r["first_divergence"] == {"segment": "seg_02", "stream": None, "output": None, "set": "normal"}
    assert "cannot reverse this relation" in r["error"] and "seg_02" in r["error"]


# --- final fix wave M1: an order.json that flattens to no segment is a usage error ----------------


@pytest.mark.parametrize("order", [[], [[]]], ids=["empty", "one_empty_wave"])
def test_an_order_with_no_segment_is_a_usage_error_for_either_output_kind(tmp_path, order):
    procedures = cf.build_composition(tmp_path / "procedures", rounding=False)
    write_json(procedures.wf(COMPOSITION, "segments", "order.json"), order)
    done = run_cli(tmp_path / "procedures", COMPOSITION)
    assert done.returncode == 2 and "Traceback" not in done.stderr and "order.json" in done.stderr
    assert not list(procedures.wf(COMPOSITION).rglob("validation*.json"))

    dbt = dbtf.build_dbt_workflow(tmp_path / "dbt", sets=("normal",))
    write_json(dbt.wf(dbtf.WF, "segments", "order.json"), order)
    done = run_cli(tmp_path / "dbt", dbtf.WF)
    assert done.returncode == 2 and "Traceback" not in done.stderr and "order.json" in done.stderr
    assert not list(dbt.wf(dbtf.WF).rglob("validation*.json"))
