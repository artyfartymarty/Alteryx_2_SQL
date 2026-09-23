"""Snowpark segment validation driver tests (Task 4 of output-targets-phase1).

Mirrors `tests/test_validate_segment.py`'s wf_0009 fixture exactly (same manifest/mappings/golden
CSVs/dag), but `contract["target"] = "snowpark"` and `segments/seg_01/proc.py` (a Snowpark Python
module) in place of `proc.sql` -- plus the rendered `proc.sql` `render_snowpark.render()` would
produce, for realism (never read by `validate_snowpark.py` itself, which drives `proc.py`
directly through the Snowpark Local Testing Framework).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import render_snowpark
import validate_snowpark as vsp
from lib import typed_csv
from lib.io import read_json, write_json, write_yaml
from lib.paths import Repo

WF = "wf_0009"
SEG = "seg_01"

F = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
     {"name": "NOTE", "type": "V_String", "size": 20, "scale": None}]

COLUMNS = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": True},
           {"name": "NOTE", "type": "VARCHAR", "nullable": True}]

CONTRACT = {
    "segment": SEG,
    "target": "snowpark",
    "inputs": [{"logical": "ITEMS", "columns": COLUMNS, "keys": ["ID"]}],
    "outputs": [
        {"stream": "2_T", "table": "MIG_WORK.WF0009_SEG_01_OUT", "kind": "work", "logical": None,
         "columns": COLUMNS, "keys": ["ID"]},
        {"stream": "2_T", "table": None, "kind": "target", "logical": "ITEMS_OUT", "tool_id": "3",
         "columns": COLUMNS, "keys": ["ID"]},
    ],
    "row_relation": "filter",
    "ordering": {"keys": ["ID"], "alteryx_deterministic": True},
    "tolerances": {},
}
CONTRACT["output"] = CONTRACT["outputs"][0]

DAG = {
    "workflow": WF, "segment": SEG,
    "nodes": [
        {"tool_id": "1", "type": "input", "config": {}},
        {"tool_id": "2", "type": "filter", "config": {}},
        {"tool_id": "3", "type": "output", "config": {}},
    ],
    "edges": [
        {"src": "1", "src_anchor": "Output", "dst": "2", "dst_anchor": "Input"},
        {"src": "2", "src_anchor": "True", "dst": "3", "dst_anchor": "Input"},
    ],
    "inbound": [], "outbound": [],
}

PROC_PY = '''# tool 2: Filter -- keep NOTE = 'keep'
from snowflake.snowpark.functions import col


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")
    kept.write.mode("overwrite").save_as_table("MIG_WORK.WF0009_SEG_01_OUT")
    kept.write.mode("overwrite").save_as_table(f"{tgt_db}.{tgt_schema}.ITEMS_OUT")
    return "OK"
'''
PROC_PY_WRONG = PROC_PY.replace('== "keep"', '== "drop"')
PROC_PY_RAISES = PROC_PY.replace("    kept.write", "    raise RuntimeError('boom')\n    kept.write")
PROC_PY_RANDOM = PROC_PY.replace('kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")',
                                 'import random\n    kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep").with_column("NOTE", col("NOTE"))\n    kept = session.create_dataframe([[r["ID"], r["NOTE"] + str(random.random())] for r in kept.collect()], schema=["ID", "NOTE"])')
PROC_PY_MISSING_TARGET = PROC_PY.replace('    kept.write.mode("overwrite").save_as_table(f"{tgt_db}.{tgt_schema}.ITEMS_OUT")\n', "")

# --- fix round 1 (task-4-fix1.md ruling R1): the ACTUAL table's schema must come from Snowpark
# itself, not from the contract's declared columns -- these four handlers each disagree with the
# contract in a way only a real schema read-back can catch.

# R1 test 1: ID (contract NUMBER(38,0)) actually written as a DoubleType column.
PROC_PY_WRONG_TYPE = PROC_PY.replace(
    'kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")',
    'from snowflake.snowpark.types import DoubleType\n'
    '    kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")'
    '.with_column("ID", col("ID").cast(DoubleType()))')

# R1 test 2: an extra column the contract never declared.
PROC_PY_EXTRA_COLUMN = PROC_PY.replace(
    'kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")',
    'from snowflake.snowpark.functions import lit\n'
    '    kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")'
    '.with_column("SURPRISE", lit(1))')

# R1 test 3: a declared column (NOTE) the handler never wrote.
PROC_PY_MISSING_COLUMN = PROC_PY.replace(
    'kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")',
    'kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep").select("ID")')

# R1 test 4: ID (contract NUMBER(38,0)) actually written as the string "not-a-number" (StringType).
# Finding 3's exact repro -- used to crash with exit 2 ("not-a-number" coerced against the
# CONTRACT's Int64 expectation); after R1 the real column type is read back as a string, so this
# becomes an ordinary schema TYPE mismatch (exit 1, a report written), not a coercion crash.
PROC_PY_BAD_ID_STRING = PROC_PY.replace(
    'kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")',
    'from snowflake.snowpark.functions import lit\n'
    '    kept = session.table(f"{src_db}.{src_schema}.ITEMS").filter(col("NOTE") == "keep")'
    '.with_column("ID", lit("not-a-number"))')

MAPPINGS = {
    "sources": {"sales/items.yxdb": {"snowflake": "SALES.RAW.ITEMS", "logical": "ITEMS",
                                     "tool_ids": ["1"], "confirmed_by": "test"}},
    "outputs": {"out/items_out.yxdb": {"snowflake": "ANALYTICS.CURATED.ITEMS_OUT",
                                       "logical": "ITEMS_OUT", "mode": "overwrite", "keys": ["ID"],
                                       "tool_ids": ["3"]}},
}


def build(tmp_path, *, proc_py=PROC_PY, contract=None, golden_sets=("normal",)):
    repo = Repo(tmp_path)
    write_json(repo.wf(WF, "manifest.json"), {"id": WF, "golden_sets": list(golden_sets)})
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), MAPPINGS)

    typed_csv.write_table(repo.wf(WF, "golden", "inputs", "normal", "1.csv"),
                          {"fields": F, "rows": [[1, "keep"], [2, "drop"]]})
    typed_csv.write_table(repo.wf(WF, "golden", "intermediates", SEG, "normal", "2_T.csv"),
                          {"fields": F, "rows": [[1, "keep"]]})
    typed_csv.write_table(repo.wf(WF, "golden", "outputs", "normal", "3.csv"),
                          {"fields": F, "rows": [[1, "keep"]]})

    write_json(repo.seg(WF, SEG, "dag.json"), DAG)
    write_json(repo.seg(WF, SEG, "contract.json"), contract if contract is not None else CONTRACT)
    proc_path = repo.seg(WF, SEG, "proc.py")
    proc_path.parent.mkdir(parents=True, exist_ok=True)
    proc_path.write_text(proc_py, encoding="utf-8", newline="\n")
    # The rendered proc.sql a real segment would also have on disk (never read by
    # validate_snowpark.py itself), for fixture realism.
    rendered = render_snowpark.render(proc_py, WF, SEG, "3.11")
    repo.seg(WF, SEG, "proc.sql").write_text(rendered, encoding="utf-8", newline="\n")
    return repo


# --- the correct procedure: PASS, idempotent, both files written, target: snowpark --------------

def test_correct_procedure_passes_and_is_idempotent(tmp_path):
    repo = build(tmp_path)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["sets"] == {"normal": "PASS"}
    assert report["idempotent"] is True
    assert report["target"] == "snowpark"
    assert read_json(repo.seg(WF, SEG, "validation.json"))["sets"] == {"normal": "PASS"}
    assert read_json(repo.seg(WF, SEG, "validation.normal.json"))["verdict"] == "PASS"


# --- a wrong filter: FAIL with a cluster carrying stream -----------------------------------------

def test_wrong_filter_fails_with_a_cluster_carrying_stream(tmp_path):
    repo = build(tmp_path, proc_py=PROC_PY_WRONG)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    assert report["diff_clusters"]
    assert all(cluster["stream"] == "2_T" for cluster in report["diff_clusters"])


# --- a raising handler: a domain FAIL, still written, still reported -----------------------------

def test_raising_handler_is_a_domain_fail(tmp_path):
    repo = build(tmp_path, proc_py=PROC_PY_RAISES)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    assert report["error"]
    assert "boom" in report["error"]
    assert report["diff_clusters"] == []
    assert read_json(repo.seg(WF, SEG, "validation.json"))["error"]

    done = run_cli(tmp_path)
    assert done.returncode == 1


# --- non-determinism -------------------------------------------------------------------------

def test_random_note_makes_the_segment_not_idempotent_and_fails(tmp_path):
    repo = build(tmp_path, proc_py=PROC_PY_RANDOM)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["idempotent"] is False
    assert report["verdict"] == "FAIL"


# --- a declared output table the procedure never created: a domain FAIL naming the table --------

def test_missing_target_table_is_a_domain_fail_naming_the_table(tmp_path):
    repo = build(tmp_path, proc_py=PROC_PY_MISSING_TARGET)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    assert "error" in report and "MIGDB.MIG_WORK.ITEMS_OUT" in report["error"]
    on_disk = read_json(repo.seg(WF, SEG, "validation.json"))
    assert on_disk["verdict"] == "FAIL" and "MIGDB.MIG_WORK.ITEMS_OUT" in on_disk["error"]


# ==================================================================================================
# Fix round 1 (task-4-fix1.md, ruling R1): the ACTUAL table's schema must come from Snowpark
# itself (`session.table(fqn).schema`), never from the contract's declared columns -- otherwise a
# handler that silently disagrees with the contract (wrong type, an extra column, a missing
# column, or a value the contract's assumed type cannot even parse) passes compare()'s schema
# check for the wrong reason, or crashes instead of failing cleanly.
# ==================================================================================================

def _type_clusters(report):
    return [c for c in report["diff_clusters"] if c["class"] == "TYPE"]


# R1 test 1: handler casts a declared NUMBER(38,0) column (ID) to DoubleType.
def test_actual_schema_type_mismatch_fails_with_a_type_cluster(tmp_path):
    repo = build(tmp_path, proc_py=PROC_PY_WRONG_TYPE)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    clusters = _type_clusters(report)
    assert clusters, f"expected a TYPE cluster, got {report['diff_clusters']}"
    assert any("ID" in c["columns"] for c in clusters)


# R1 test 2: handler writes an extra column the contract never declared.
def test_actual_schema_extra_column_fails_with_a_type_cluster_naming_it(tmp_path):
    repo = build(tmp_path, proc_py=PROC_PY_EXTRA_COLUMN)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    clusters = _type_clusters(report)
    assert clusters
    assert any("SURPRISE" in c["columns"] for c in clusters)
    assert any("only in actual" in (c.get("note") or "") for c in clusters)


# R1 test 3: handler omits a declared column (NOTE).
def test_actual_schema_missing_column_fails_with_a_type_cluster_naming_it(tmp_path):
    repo = build(tmp_path, proc_py=PROC_PY_MISSING_COLUMN)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    clusters = _type_clusters(report)
    assert clusters
    assert any("NOTE" in c["columns"] for c in clusters)
    assert any("missing from actual" in (c.get("note") or "") for c in clusters)


# R1 test 4: handler writes the string "not-a-number" into ID (StringType). Finding 3's exact
# repro -- used to crash with exit 2 and no report written (the OLD `_read_back` coerced this cell
# against the CONTRACT's Int64 expectation); after R1 the real (StringType) column type is what
# gets read back, so this becomes an ordinary schema TYPE mismatch: exit 1, a report written.
def test_actual_id_written_as_a_string_is_a_schema_fail_not_a_crash(tmp_path):
    repo = build(tmp_path, proc_py=PROC_PY_BAD_ID_STRING)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["verdict"] == "FAIL"
    clusters = _type_clusters(report)
    assert clusters
    assert any("ID" in c["columns"] for c in clusters)
    assert read_json(repo.seg(WF, SEG, "validation.json"))["verdict"] == "FAIL"

    done = run_cli(tmp_path)
    assert done.returncode == 1
    assert "Traceback" not in done.stderr
    assert Repo(tmp_path).seg(WF, SEG, "validation.json").exists()


# R1 test 5 (finding 4 / R2): a FixedDecimal contract output column (AMT NUMBER(19,2)) must PASS
# when the handler writes it as DecimalType(19, 2) -- this used to crash ("NUMBER(None,0)") because
# `_read_back` built the actual field with size/scale forced to None.
F_AMT = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
        {"name": "NOTE", "type": "V_String", "size": 20, "scale": None},
        {"name": "AMT", "type": "FixedDecimal", "size": 19, "scale": 2}]

COLUMNS_AMT = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": True},
              {"name": "NOTE", "type": "VARCHAR", "nullable": True},
              {"name": "AMT", "type": "NUMBER(19,2)", "nullable": True}]

CONTRACT_AMT = {
    "segment": SEG,
    "target": "snowpark",
    "inputs": [{"logical": "ITEMS", "columns": COLUMNS_AMT, "keys": ["ID"]}],
    "outputs": [
        {"stream": "2_T", "table": "MIG_WORK.WF0009_SEG_01_OUT", "kind": "work", "logical": None,
         "columns": COLUMNS_AMT, "keys": ["ID"]},
        {"stream": "2_T", "table": None, "kind": "target", "logical": "ITEMS_OUT", "tool_id": "3",
         "columns": COLUMNS_AMT, "keys": ["ID"]},
    ],
    "row_relation": "filter",
    "ordering": {"keys": ["ID"], "alteryx_deterministic": True},
    "tolerances": {},
}
CONTRACT_AMT["output"] = CONTRACT_AMT["outputs"][0]

PROC_PY_AMT = PROC_PY   # unchanged: filters ITEMS through, whatever columns it has (now incl. AMT)


def test_fixed_decimal_column_round_trips_and_passes(tmp_path):
    from decimal import Decimal
    repo = Repo(tmp_path)
    write_json(repo.wf(WF, "manifest.json"), {"id": WF, "golden_sets": ["normal"]})
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), MAPPINGS)
    typed_csv.write_table(repo.wf(WF, "golden", "inputs", "normal", "1.csv"),
                          {"fields": F_AMT, "rows": [[1, "keep", Decimal("10.50")],
                                                     [2, "drop", Decimal("1.00")]]})
    typed_csv.write_table(repo.wf(WF, "golden", "intermediates", SEG, "normal", "2_T.csv"),
                          {"fields": F_AMT, "rows": [[1, "keep", Decimal("10.50")]]})
    typed_csv.write_table(repo.wf(WF, "golden", "outputs", "normal", "3.csv"),
                          {"fields": F_AMT, "rows": [[1, "keep", Decimal("10.50")]]})
    write_json(repo.seg(WF, SEG, "dag.json"), DAG)
    write_json(repo.seg(WF, SEG, "contract.json"), CONTRACT_AMT)
    proc_path = repo.seg(WF, SEG, "proc.py")
    proc_path.parent.mkdir(parents=True, exist_ok=True)
    proc_path.write_text(PROC_PY_AMT, encoding="utf-8", newline="\n")

    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["sets"] == {"normal": "PASS"}, report


# R1 test 6 (positive control): the existing correct-procedure test
# (test_correct_procedure_passes_and_is_idempotent, above) must still PASS unchanged, including
# the idempotency run -- re-asserted here explicitly against the fix so it is not just implied by
# "no regression."
def test_correct_procedure_still_passes_after_the_schema_fix(tmp_path):
    repo = build(tmp_path)
    report = vsp.validate_snowpark(repo, WF, SEG)

    assert report["sets"] == {"normal": "PASS"}
    assert report["idempotent"] is True
    assert report["diff_clusters"] == []


# --- --proc override --------------------------------------------------------------------------

def test_proc_argument_overrides_the_segment_file(tmp_path):
    repo = build(tmp_path, proc_py=PROC_PY_WRONG)   # the segment's own file would FAIL
    override = tmp_path / "override_proc.py"
    override.write_text(PROC_PY, encoding="utf-8", newline="\n")

    report = vsp.validate_snowpark(repo, WF, SEG, proc_path=override)

    assert report["sets"] == {"normal": "PASS"}


def test_running_a_procedure_leaves_no_bytecode_cache_beside_it(tmp_path):
    """Validating a procedure must not write anything next to the file it validates.

    `_load_module` imports `proc.py` from wherever it sits, and CPython's default is to cache the
    compiled module in a `__pycache__/` directory beside the source. For a `--proc` override that
    directory lands wherever the caller pointed -- for `tests/test_e2e_parity.py` that is
    `samples/<wf>/broken_sql/<seg>/`, i.e. a read-only fixture tree inside the repository. It is
    git-ignored, so nothing can be committed by accident, but a validator has no business writing
    into the tree it is reading from.
    """
    repo = build(tmp_path, proc_py=PROC_PY_WRONG)   # the segment's own file would FAIL
    override_dir = tmp_path / "override"
    override_dir.mkdir()
    override = override_dir / "x.py"
    override.write_text(PROC_PY, encoding="utf-8", newline="\n")

    report = vsp.validate_snowpark(repo, WF, SEG, proc_path=override)

    assert report["sets"] == {"normal": "PASS"}   # it really ran the override, twice
    assert sorted(path.name for path in override_dir.iterdir()) == ["x.py"], (
        "validate_snowpark wrote something beside the procedure it was given")
    assert not (repo.seg(WF, SEG, "proc.py").parent / "__pycache__").exists(), (
        "validate_snowpark wrote a __pycache__ beside the segment's own proc.py")


# --- usage errors: nothing written ----------------------------------------------------------------

def test_unknown_segment_raises_and_writes_nothing(tmp_path):
    repo = build(tmp_path)
    with pytest.raises(FileNotFoundError):
        vsp.validate_snowpark(repo, WF, "seg_99")
    assert not repo.seg(WF, "seg_99", "validation.json").exists()


def test_missing_golden_file_raises_and_writes_nothing(tmp_path):
    repo = build(tmp_path)
    repo.wf(WF, "golden", "outputs", "normal", "3.csv").unlink()
    with pytest.raises(FileNotFoundError):
        vsp.validate_snowpark(repo, WF, SEG)
    assert not repo.seg(WF, SEG, "validation.json").exists()


def test_no_golden_sets_available_raises_value_error(tmp_path):
    repo = build(tmp_path, golden_sets=())
    with pytest.raises(ValueError):
        vsp.validate_snowpark(repo, WF, SEG)
    assert not repo.seg(WF, SEG, "validation.json").exists()


# --- two-segment upstream-intermediate case (contract["inputs"] with a `stream`/`from`) ---------
# Prose-described but untested by the brief's own test list (implementer-rules.md item 9):
# `run_handler`'s loop over `contract.get("inputs", [])` for a `stream`/`from` entry.

SEG2 = "seg_02"
CONTRACT2 = {
    "segment": SEG2,
    "target": "snowpark",
    "inputs": [{"from": "seg_01", "stream": "2_T", "table": "MIG_WORK.WF0009_SEG_01_OUT",
               "columns": COLUMNS, "keys": ["ID"]}],
    "outputs": [{"stream": "4_Output", "table": "MIG_WORK.WF0009_SEG_02_OUT", "kind": "work",
                "logical": None, "columns": COLUMNS, "keys": ["ID"]}],
    "row_relation": "1:1",
    "ordering": {"keys": ["ID"], "alteryx_deterministic": True},
    "tolerances": {},
}
CONTRACT2["output"] = CONTRACT2["outputs"][0]

PROC_PY2 = '''def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    upstream = session.table("MIG_WORK.WF0009_SEG_01_OUT")
    upstream.write.mode("overwrite").save_as_table("MIG_WORK.WF0009_SEG_02_OUT")
    return "OK"
'''


def test_upstream_intermediate_feeds_the_next_segment_without_running_its_procedure(tmp_path):
    repo = build(tmp_path)   # seg_01's own proc.py exists, but seg_02 must never need it
    typed_csv.write_table(repo.wf(WF, "golden", "intermediates", SEG2, "normal", "4_Output.csv"),
                          {"fields": F, "rows": [[1, "keep"]]})
    write_json(repo.seg(WF, SEG2, "contract.json"), CONTRACT2)
    repo.seg(WF, SEG2, "proc.py").write_text(PROC_PY2, encoding="utf-8", newline="\n")

    # Prove seg_01's own procedure is never consulted: replace it with one that would raise if run.
    repo.seg(WF, SEG, "proc.py").write_text(PROC_PY_RAISES, encoding="utf-8", newline="\n")

    report = vsp.validate_snowpark(repo, WF, SEG2)

    assert report["sets"] == {"normal": "PASS"}


def test_missing_proc_py_raises_file_not_found(tmp_path):
    repo = build(tmp_path)
    repo.seg(WF, SEG, "proc.py").unlink()
    with pytest.raises(FileNotFoundError):
        vsp.validate_snowpark(repo, WF, SEG)
    assert not repo.seg(WF, SEG, "validation.json").exists()


# --- a second call leaves no stale validation.*.json for a dropped set --------------------------

def test_a_dropped_golden_set_loses_its_stale_report(tmp_path):
    repo = build(tmp_path, golden_sets=("normal",))
    typed_csv.write_table(repo.wf(WF, "golden", "inputs", "edge", "1.csv"),
                          {"fields": F, "rows": [[1, "keep"], [2, "drop"]]})
    typed_csv.write_table(repo.wf(WF, "golden", "intermediates", SEG, "edge", "2_T.csv"),
                          {"fields": F, "rows": [[1, "keep"]]})
    typed_csv.write_table(repo.wf(WF, "golden", "outputs", "edge", "3.csv"),
                          {"fields": F, "rows": [[1, "keep"]]})

    vsp.validate_snowpark(repo, WF, SEG, ["normal", "edge"])
    assert repo.seg(WF, SEG, "validation.edge.json").exists()
    assert repo.seg(WF, SEG, "validation.normal.json").exists()

    vsp.validate_snowpark(repo, WF, SEG, ["normal"])
    assert not repo.seg(WF, SEG, "validation.edge.json").exists()
    assert repo.seg(WF, SEG, "validation.normal.json").exists()
    assert repo.seg(WF, SEG, "validation.json").exists()


# --- CLI exit codes (implementer-rules.md "CLI EXIT CODES") -------------------------------------

def run_cli(tmp_path, seg=SEG, *extra):
    return subprocess.run(
        [sys.executable, str(Path(vsp.__file__)), WF, seg, *extra, "--root", str(tmp_path)],
        capture_output=True, text=True)


def test_cli_exits_zero_on_pass(tmp_path):
    build(tmp_path)
    done = run_cli(tmp_path)
    assert done.returncode == 0 and "PASS" in done.stdout


def test_cli_exits_one_on_fail(tmp_path):
    build(tmp_path, proc_py=PROC_PY_WRONG)
    done = run_cli(tmp_path)
    assert done.returncode == 1
    assert "Traceback" not in done.stderr


def test_cli_exits_two_on_unknown_segment(tmp_path):
    build(tmp_path)
    done = run_cli(tmp_path, "seg_99")
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert not Repo(tmp_path).seg(WF, "seg_99", "validation.json").exists()


def test_cli_exits_two_and_writes_nothing_when_a_golden_file_is_missing(tmp_path):
    repo = build(tmp_path)
    repo.wf(WF, "golden", "outputs", "normal", "3.csv").unlink()
    done = run_cli(tmp_path)
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert not repo.seg(WF, SEG, "validation.json").exists()


def test_cli_proc_flag_overrides_the_segment_file(tmp_path):
    build(tmp_path, proc_py=PROC_PY_WRONG)
    override = tmp_path / "override_proc.py"
    override.write_text(PROC_PY, encoding="utf-8", newline="\n")
    done = run_cli(tmp_path, SEG, "--proc", str(override))
    assert done.returncode == 0 and "PASS" in done.stdout


def test_cli_set_flag_overrides_the_manifest_default(tmp_path):
    build(tmp_path, golden_sets=("nonexistent",))  # "normal"'s golden files exist on disk anyway
    done = run_cli(tmp_path, SEG, "--set", "normal")
    assert done.returncode == 0
    assert "{'normal': 'PASS'}" in done.stdout


def test_cli_without_set_flag_uses_the_manifest_default(tmp_path):
    build(tmp_path, golden_sets=("nonexistent",))
    done = run_cli(tmp_path)
    assert done.returncode == 2 and "Traceback" not in done.stderr


def test_main_returns_two_on_an_unexpected_exception(tmp_path, monkeypatch):
    build(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")
    monkeypatch.setattr(vsp, "validate_snowpark", boom)

    rc = vsp.main([WF, SEG, "--root", str(tmp_path)])
    assert rc == 2
