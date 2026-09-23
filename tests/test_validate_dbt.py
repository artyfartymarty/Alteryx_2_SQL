"""`validate_dbt.py`: a fresh DuckDB sandbox per golden set loaded exactly as `load_golden.load_set`
does, one `dbt run` of the whole project, and every contract output of every segment judged by the
unchanged `compare.py` (spec §5.3, DV1/DV3/DV7).

Each test builds `tests/dbt_fixtures.build_dbt_workflow`'s `wf_0009` project, optionally with
`replace=` overriding one or more of its files, and calls `validate_dbt.validate_dbt(repo, WF,
…)`. `golden_sets` is restricted to `["normal"]` wherever the scenario under test does not need the
second set, since each `dbt run` measures ~3 s on this machine (implementer-rules.md: "keep the
test count of dbt runs reasonable").
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import validate_dbt as vd
from lib.io import read_json, write_json
from tests import dbt_fixtures
from tests.dbt_fixtures import WF, build_dbt_workflow


def run_cli(root, *extra):
    """The CLI as a subprocess (matching test_validate_segment.py's own convention): a
    `parser.error(...)` call raises `SystemExit`, which is only observable as a process exit code,
    not as `main()`'s own return value, when `main` is called in-process."""
    return subprocess.run(
        [sys.executable, str(Path(vd.__file__)), *extra, "--root", str(root)],
        capture_output=True, text=True)


ITEMS_OUT_SQL = dbt_fixtures.MODEL_FILES["models/items_out.sql"]
WORK_SQL = dbt_fixtures.MODEL_FILES["models/wf0009_seg_01_out.sql"]

SCHEMA_YML_NO_ITEMS_OUT = """\
version: 2
models:
  - name: wf0009_seg_01_out
    columns:
      - name: ID
      - name: NOTE
      - name: AMOUNT
  - name: items_hist
    columns:
      - name: ID
        tests:
          - not_null
          - unique
      - name: NOTE
      - name: AMOUNT
"""


# --- the fixture project: PASS everywhere, both files written, sandboxes kept -------------------


def test_a_correct_project_passes_every_set_and_every_segment(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    reports = vd.validate_dbt(repo, WF)

    assert set(reports) == {"seg_01", "seg_02"}
    for seg, report in reports.items():
        assert report["sets"] == {"normal": "PASS", "second": "PASS"}
        assert report["idempotent"] is True
        assert report["idempotency_diff"] == []
        assert report["target"] == "dbt"
        assert report["diff_clusters"] == []
        assert read_json(repo.seg(WF, seg, "validation.json")) == report
        normal = read_json(repo.seg(WF, seg, "validation.normal.json"))
        assert "target" not in normal

    project = repo.wf(WF, "dbt")
    for name in ("validate_normal.log", "validate_second.log", "validate_normal_rerun.log"):
        assert (project / "logs" / name).is_file()
    assert repo.wf(WF, "dbt_sandbox_normal.duckdb").is_file()
    assert repo.wf(WF, "dbt_sandbox_second.duckdb").is_file()
    assert not list(repo.wf(WF).glob("*_rerun.duckdb*"))


# --- wrong logic: FAIL with clusters carrying the right stream, chained to the next segment ------


def test_wrong_logic_fails_with_clusters_on_the_right_stream(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/wf0009_seg_01_out.sql": WORK_SQL.replace("where NOTE = 'keep'", "where NOTE = 'drop'")})
    reports = vd.validate_dbt(repo, WF, ["normal"])

    assert reports["seg_01"]["verdict"] == "FAIL"
    assert reports["seg_01"]["diff_clusters"]
    assert all(cluster["stream"] == "2_T" for cluster in reports["seg_01"]["diff_clusters"])
    assert reports["seg_02"]["verdict"] == "FAIL"


# --- a model that fails to run: a domain FAIL naming the model, never a stack trace --------------


def test_a_model_that_fails_to_run_is_a_domain_fail_naming_it(tmp_path, capsys):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_out.sql": "{{ config(materialized='table', alias='ITEMS_OUT') }}\n"
                                "select NO_SUCH_COLUMN from {{ ref('wf0009_seg_01_out') }}\n"})
    reports = vd.validate_dbt(repo, WF, ["normal"])

    for report in reports.values():
        assert report["verdict"] == "FAIL"
        assert report["diff_clusters"] == []
        assert "failed models: items_out" in report["error"]
        assert "NO_SUCH_COLUMN" in report["error"]
        assert str(tmp_path) not in report["error"]

    rc = vd.main([WF, "--set", "normal", "--root", str(tmp_path)])
    assert rc == 1
    assert "Traceback" not in capsys.readouterr().err


# --- non-determinism: only the segment with the non-deterministic model loses idempotency --------


def test_a_non_deterministic_model_is_not_idempotent(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_out.sql": ITEMS_OUT_SQL.replace(
            "select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}",
            "select ID, NOTE, cast(random() * 100 as decimal(19,2)) as AMOUNT "
            "from {{ ref('wf0009_seg_01_out') }}")})
    reports = vd.validate_dbt(repo, WF, ["normal"])

    assert reports["seg_02"]["idempotent"] is False
    assert "MIGDB.MIG_WORK.ITEMS_OUT" in reports["seg_02"]["idempotency_diff"]
    assert reports["seg_02"]["verdict"] == "FAIL"
    assert reports["seg_01"]["idempotent"] is True


# --- a missing output table: a FAIL naming it, not a KeyError or compare.py usage error -----------


def test_a_missing_output_table_is_a_fail_naming_it(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_out.sql": None, "models/schema.yml": SCHEMA_YML_NO_ITEMS_OUT})
    reports = vd.validate_dbt(repo, WF, ["normal"])

    assert "MIGDB.MIG_WORK.ITEMS_OUT does not exist" in reports["seg_02"]["error"]


# --- usage errors: a stale report is cleared and never replaced, CLI exits 2 every time -----------


def test_usage_errors_leave_no_report(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    stale = repo.seg(WF, "seg_01", "validation.json")

    write_json(stale, {"stale": True})
    with pytest.raises(FileNotFoundError):
        vd.validate_dbt(repo, "wf_9999")

    write_json(stale, {"stale": True})
    with pytest.raises(ValueError):
        vd.validate_dbt(repo, WF, [])
    assert not stale.is_file()
    assert not repo.seg(WF, "seg_02", "validation.json").is_file()

    write_json(stale, {"stale": True})
    repo.wf(WF, "dbt", "dbt_project.yml").unlink()
    with pytest.raises(FileNotFoundError):
        vd.validate_dbt(repo, WF)
    assert not stale.is_file()
    assert not repo.seg(WF, "seg_02", "validation.json").is_file()

    # The CLI reproduces all three usage errors: unknown workflow, an empty golden_sets (via the
    # manifest, since --set has no way to spell "explicitly no sets"), and no dbt project. A
    # `parser.error(...)` call raises SystemExit, observable only as a subprocess exit code -- not
    # as `main()`'s own return value when called in-process (see `run_cli` above).
    done = run_cli(tmp_path, "wf_9999")
    assert done.returncode == 2 and "Traceback" not in done.stderr

    other_root = tmp_path / "other_repo"
    other_repo = build_dbt_workflow(other_root)
    manifest_path = other_repo.wf(WF, "manifest.json")
    manifest = read_json(manifest_path)
    manifest["golden_sets"] = []
    write_json(manifest_path, manifest)
    done = run_cli(other_root, WF)
    assert done.returncode == 2 and "Traceback" not in done.stderr

    other_repo.wf(WF, "dbt", "dbt_project.yml").unlink()
    done = run_cli(other_root, WF)
    assert done.returncode == 2 and "Traceback" not in done.stderr


# --- a dropped golden set loses its stale per-set report, for every segment ----------------------


def test_a_dropped_golden_set_loses_its_stale_report(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    vd.validate_dbt(repo, WF)
    assert repo.seg(WF, "seg_01", "validation.second.json").is_file()
    assert repo.seg(WF, "seg_02", "validation.second.json").is_file()

    vd.validate_dbt(repo, WF, ["normal"])
    assert not repo.seg(WF, "seg_01", "validation.second.json").is_file()
    assert not repo.seg(WF, "seg_02", "validation.second.json").is_file()


# --- --project validates a different directory, the workflow's own dbt/ is left untouched --------


def test_the_project_option_validates_another_directory(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    other = tmp_path / "other"
    shutil.copytree(repo.wf(WF, "dbt"), other)
    broken = WORK_SQL.replace("where NOTE = 'keep'", "where NOTE = 'drop'")
    (other / "models" / "wf0009_seg_01_out.sql").write_text(broken, encoding="utf-8", newline="\n")
    original = repo.wf(WF, "dbt", "models", "wf0009_seg_01_out.sql").read_text(encoding="utf-8")

    reports = vd.validate_dbt(repo, WF, ["normal"], project_dir=other)

    assert reports["seg_01"]["verdict"] == "FAIL"
    assert repo.wf(WF, "dbt", "models", "wf0009_seg_01_out.sql").read_text(encoding="utf-8") == original
    assert (other / "logs" / "validate_normal.log").is_file()


# --- no dbt console script: a usage error, before anything runs ----------------------------------


def test_dbt_unavailable_is_a_usage_error(tmp_path, monkeypatch):
    repo = build_dbt_workflow(tmp_path)

    def _boom():
        raise vd.DbtUnavailable("no dbt console script beside this interpreter")

    monkeypatch.setattr(vd.dbt_project, "dbt_executable", _boom)

    # `main`'s DbtUnavailable branch is `parser.error(...)`, which raises SystemExit(2) -- only
    # observable as a subprocess exit code, except monkeypatching `dbt_executable` only reaches
    # this same process, so this asserts on the SystemExit directly instead of using `run_cli`.
    with pytest.raises(SystemExit) as exc_info:
        vd.main([WF, "--root", str(tmp_path)])
    assert exc_info.value.code == 2
    assert not repo.seg(WF, "seg_01", "validation.json").is_file()


# --- Task W1 fix round 2: DV7's re-run sandbox is loaded in REVERSED row order ---------------------
# A Snowflake table has no row order, so a model that depends on the order its input arrives in (a
# LIMIT without ORDER BY, a window without a total order) is non-deterministic in production. The
# first sandbox keeps file order; the second, fresh one loads every raw golden input and every
# targets_before table reversed, so such a model's two runs differ and it FAILs as non-idempotent.

LIMIT_ONE_SQL = ITEMS_OUT_SQL.replace(
    "select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }}",
    "select ID, NOTE, AMOUNT from {{ ref('wf0009_seg_01_out') }} limit 1")


def test_a_model_that_depends_on_row_order_is_not_idempotent(tmp_path):
    from lib import typed_csv

    assert LIMIT_ONE_SQL != ITEMS_OUT_SQL
    repo = build_dbt_workflow(tmp_path, sets=("normal",), replace={"models/items_out.sql": LIMIT_ONE_SQL})
    # Alteryx's "first record" is the first in file order: ID 1.
    typed_csv.write_table(repo.wf(WF, "golden", "outputs", "normal", "4.csv"),
                          {"fields": dbt_fixtures.F, "rows": [[1, "keep", "10.00"]]})

    reports = vd.validate_dbt(repo, WF, ["normal"])

    assert reports["seg_01"]["verdict"] == "PASS" and reports["seg_01"]["idempotent"] is True
    seg_02 = reports["seg_02"]
    assert seg_02["idempotent"] is False and seg_02["verdict"] == "FAIL"
    assert "MIGDB.MIG_WORK.ITEMS_OUT" in seg_02["idempotency_diff"]
    assert seg_02["diff_clusters"] == [], "the judged (file-order) run matched the golden output"
    chain = read_json(repo.wf(WF, "validation_workflow.json"))
    assert chain["verdict"] == "FAIL" and chain["idempotent"] is False
    assert chain["divergence_kind"] == "boundary"
    assert chain["first_divergence"] == {"segment": "seg_02", "stream": "2_T", "output": "ITEMS_OUT", "set": "normal"}


def test_the_rerun_sandbox_loads_raw_inputs_and_targets_before_in_reversed_order(tmp_path):
    from lib.backend import DuckDBBackend

    repo = build_dbt_workflow(tmp_path, sets=("normal",))
    first, second = tmp_path / "dbt_sandbox_first.duckdb", tmp_path / "dbt_sandbox_second.duckdb"
    vd._load_sandbox(repo, WF, "normal", first)
    vd._load_sandbox(repo, WF, "normal", second, reverse=True)
    orders = []
    for path in (first, second):
        backend = DuckDBBackend(str(path))
        try:
            orders.append({
                "input": [row[0] for row in backend.query("SELECT ID FROM MIG_GOLDEN.WF0009_NORMAL_IN_1")[1]],
                "view": [row[0] for row in backend.query("SELECT ID FROM MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS")[1]],
                "before": [row[0] for row in backend.query("SELECT ID FROM MIGDB.MIG_WORK.ITEMS_HIST")[1]],
                "types": backend.table_columns("MIGDB.MIG_WORK.ITEMS_HIST"),
            })
        finally:
            backend.close()
    assert orders[0]["input"] == [1, 2, 3, 4] and orders[0]["before"] == [1, 7]
    assert orders[1]["input"] == [4, 3, 2, 1] and orders[1]["view"] == [4, 3, 2, 1]
    assert orders[1]["before"] == [7, 1]
    assert orders[1]["types"] == orders[0]["types"]


# --- final fix wave M1: an order.json that flattens to no segment is a usage error ----------------


@pytest.mark.parametrize("order", [[], [[]], [[], []]], ids=["empty", "one_empty_wave", "two_empty_waves"])
def test_an_order_with_no_segment_is_a_usage_error_and_writes_nothing(tmp_path, order):
    """Before the fix, `_segments` raised only for a MISSING order.json: one that flattened to `[]`
    ran the whole project, judged no segment, and `main`'s `all(...)` over an empty dict exited 0
    with a PASS chain report. It is a prerequisite problem like a missing file: exit 2, no report."""
    repo = build_dbt_workflow(tmp_path, sets=("normal",))
    write_json(repo.wf(WF, "segments", "order.json"), order)
    with pytest.raises(ValueError, match="order.json"):
        vd.validate_dbt(repo, WF)
    done = run_cli(tmp_path, WF)
    assert done.returncode == 2 and "Traceback" not in done.stderr and "order.json" in done.stderr
    assert not list(repo.wf(WF).rglob("validation*.json"))
    assert not list(repo.wf(WF).glob("dbt_sandbox_*"))
    assert not repo.wf(WF, "dbt", "logs").exists()
