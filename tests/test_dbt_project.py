"""scripts/lib/dbt_project.py: layout, naming and the one way dbt is run."""
from __future__ import annotations

import subprocess
import sysconfig

import pytest

from lib import dbt_project as dp
from lib.backend import DuckDBBackend
from load_golden import load_set
from tests.dbt_fixtures import WF, build_dbt_workflow

WORK = {"stream": "2_T", "kind": "work", "table": "MIG_WORK.WF0009_SEG_01_OUT", "logical": None}
TARGET = {"stream": "2_T", "kind": "target", "tool_id": "5", "logical": "ITEMS_HIST", "table": None}


def test_model_name_is_the_logical_or_the_work_table_lower_cased():
    assert dp.model_name(TARGET) == "items_hist"
    assert dp.model_name(WORK) == "wf0009_seg_01_out"


def test_model_relation_puts_every_model_in_mig_work():
    assert dp.model_relation(WF, "seg_02", TARGET) == "MIGDB.MIG_WORK.ITEMS_HIST"
    assert dp.model_relation(WF, "seg_01", WORK) == "MIGDB.MIG_WORK.WF0009_SEG_01_OUT"
    assert dp.model_relation(WF, "seg_01", WORK, database="SANDBOX") == "SANDBOX.MIG_WORK.WF0009_SEG_01_OUT"


def test_local_vars_are_the_flattened_duckdb_schema_names():
    assert dp.local_vars("MIG_GOLDEN_WF0009_NORMAL") == {
        "src_schema": "MIGDB__MIG_GOLDEN_WF0009_NORMAL", "tgt_schema": "MIGDB__MIG_WORK"}


def test_sandbox_path_is_a_catalog_friendly_file_name(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    path = dp.sandbox_path(repo, WF, "period_end", "_rerun")
    assert path == repo.wf(WF, "dbt_sandbox_period_end_rerun.duckdb")
    assert not path.name.startswith(".") and path.name.count(".") == 1


def test_expected_model_config_per_write_mode():
    assert dp.expected_model_config("overwrite", [], "A") == {"materialized": "table", "alias": "A"}
    assert dp.expected_model_config("append", [], "A") == {
        "materialized": "incremental", "incremental_strategy": "append", "alias": "A"}
    assert dp.expected_model_config("merge", ["id", "K"], "A") == {
        "materialized": "incremental", "incremental_strategy": "merge", "unique_key": ["ID", "K"], "alias": "A"}
    with pytest.raises(ValueError, match="update_only"):
        dp.expected_model_config("update_only", [], "A")


def _sandbox(repo, name="normal"):
    path = dp.sandbox_path(repo, WF, name)
    backend = DuckDBBackend(str(path))
    try:
        load_set(backend, repo, WF, name)
    finally:
        backend.close()
    return path


def test_run_dbt_runs_the_project_and_writes_nothing_into_it(tmp_path):
    repo = build_dbt_workflow(tmp_path)
    project = dp.project_dir(repo, WF)
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    log = project / "logs" / "validate_normal.log"
    result = dp.run_dbt("run", project, vars=dp.local_vars("MIG_GOLDEN_WF0009_NORMAL"),
                        duckdb_path=_sandbox(repo), log_file=log)
    assert result.ok and result.code == 0 and result.failed_models == []
    assert sorted(r["name"] for r in result.results) == ["items_hist", "items_out", "wf0009_seg_01_out"]
    after = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    assert after == sorted(before + ["logs", "logs/validate_normal.log"])
    text = log.read_text(encoding="utf-8")
    assert "\x1b[" not in text and "\r\n" not in text and str(tmp_path) not in result.output


def test_run_dbt_names_the_model_that_failed(tmp_path):
    repo = build_dbt_workflow(tmp_path, replace={
        "models/items_out.sql": "{{ config(materialized='table', alias='ITEMS_OUT') }}\n"
                                "select NO_SUCH_COLUMN from {{ ref('wf0009_seg_01_out') }}\n"})
    result = dp.run_dbt("run", dp.project_dir(repo, WF), vars=dp.local_vars("MIG_GOLDEN_WF0009_NORMAL"),
                        duckdb_path=_sandbox(repo))
    assert result.code == 1 and result.failed_models == ["items_out"]
    assert "NO_SUCH_COLUMN" in next(r["message"] for r in result.results if r["name"] == "items_out")


def test_run_dbt_turns_telemetry_and_colours_off(tmp_path, monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["env"] = args, kwargs["env"]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(dp.subprocess, "run", fake_run)
    dp.run_dbt("parse", tmp_path, vars={"src_schema": "S", "tgt_schema": "T"}, duckdb_path=tmp_path / "x.duckdb")
    assert seen["env"]["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"
    assert seen["env"][dp.DUCKDB_PATH_ENV] == str((tmp_path / "x.duckdb").resolve())
    for flag in ("--no-use-colors", "--target-path", "--log-path", "--profiles-dir", "--vars"):
        assert flag in seen["args"]


def test_a_missing_console_script_is_dbt_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(tmp_path))
    with pytest.raises(dp.DbtUnavailable, match="dbt"):
        dp.dbt_executable()
