"""Golden data into the sandbox (contracts C2/C3) and the production source-view generator."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import gen_source_views
import load_golden
from lib.backend import DuckDBBackend
from lib.io import write_yaml
from lib.paths import Repo
from lib import typed_csv
from load_golden import load_set, load_intermediate
from gen_source_views import production_views_sql

WF = "wf_0009"
F = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
     {"name": "NOTE", "type": "V_String", "size": 20, "scale": None}]
MAPPINGS = {
    "sources": {"sales/items.yxdb": {"snowflake": "SALES.RAW.ITEMS", "logical": "ITEMS",
                                     "tool_ids": ["1"], "confirmed_by": "wf_owner"}},
    "outputs": {"out/items_out.yxdb": {"snowflake": "ANALYTICS.CURATED.ITEMS_OUT", "logical": "ITEMS_OUT",
                                       "mode": "update_insert", "keys": ["ID"], "tool_ids": ["7"]}},
}


def build(tmp_path, mappings=None):
    repo = Repo(tmp_path)
    typed_csv.write_table(repo.wf(WF, "golden", "inputs", "normal", "1.csv"),
                          {"fields": F, "rows": [[1, "keep"], [2, "drop"]]})
    typed_csv.write_table(repo.wf(WF, "golden", "targets_before", "normal", "ITEMS_OUT.csv"),
                          {"fields": F, "rows": [[9, "already there"]]})
    write_yaml(repo.wf(WF, "intake", "mappings.yaml"), MAPPINGS if mappings is None else mappings)
    return repo


def test_load_set_loads_inputs_views_and_targets(tmp_path):
    repo = build(tmp_path)
    b = DuckDBBackend()
    info = load_set(b, repo, WF, "normal")
    assert info["args"] == {"SRC_DB": "MIGDB", "SRC_SCHEMA": "MIG_GOLDEN_WF0009_NORMAL",
                            "TGT_DB": "MIGDB", "TGT_SCHEMA": "MIG_WORK"}
    assert info["loaded"] == ["MIG_GOLDEN.WF0009_NORMAL_IN_1",
                              "MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS",
                              "MIGDB.MIG_WORK.ITEMS_OUT"]
    assert all(b.table_exists(fqn) for fqn in info["loaded"])
    assert b.query("SELECT ID, NOTE FROM MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS ORDER BY ID")[1] == \
        [(1, "keep"), (2, "drop")]
    assert b.query("SELECT ID, NOTE FROM MIGDB.MIG_WORK.ITEMS_OUT")[1] == [(9, "already there")]


def test_a_source_without_a_logical_name_is_not_guessed(tmp_path):
    mappings = {"sources": {"sales/items.yxdb": {"snowflake": "SALES.RAW.ITEMS", "tool_ids": ["1"]}},
                "outputs": {}}
    repo = build(tmp_path, mappings)
    with pytest.raises(ValueError, match="tool 1"):
        load_set(DuckDBBackend(), repo, WF, "normal")


def test_a_target_without_golden_starting_rows_is_skipped(tmp_path):
    repo = build(tmp_path)
    repo.wf(WF, "golden", "targets_before", "normal", "ITEMS_OUT.csv").unlink()
    b = DuckDBBackend()
    info = load_set(b, repo, WF, "normal")
    assert info["loaded"] == ["MIG_GOLDEN.WF0009_NORMAL_IN_1", "MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS"]
    assert not b.table_exists("MIGDB.MIG_WORK.ITEMS_OUT")


def test_a_missing_golden_input_names_the_tool(tmp_path):
    repo = build(tmp_path)
    repo.wf(WF, "golden", "inputs", "normal", "1.csv").unlink()
    with pytest.raises(FileNotFoundError, match="tool 1"):
        load_set(DuckDBBackend(), repo, WF, "normal")


def test_load_intermediate_feeds_an_upstream_segment_table(tmp_path):
    repo = build(tmp_path)
    typed_csv.write_table(repo.wf(WF, "golden", "intermediates", "seg_01", "normal", "31_U.csv"),
                          {"fields": F, "rows": [[7, "from upstream"]]})
    b = DuckDBBackend()
    load_intermediate(b, repo, WF, "seg_01", "normal", "31_U", "MIG_WORK.WF0009_SEG_01_OUT")
    assert b.query("SELECT ID, NOTE FROM MIG_WORK.WF0009_SEG_01_OUT")[1] == [(7, "from upstream")]


def test_production_views_sql_maps_logical_names_to_real_tables():
    sql = production_views_sql(WF, MAPPINGS, {"target_database": "ANALYTICS"})
    assert "CREATE SCHEMA IF NOT EXISTS ANALYTICS.MIG_SRC_WF0009;" in sql
    assert "CREATE OR REPLACE VIEW ANALYTICS.MIG_SRC_WF0009.ITEMS AS SELECT * FROM SALES.RAW.ITEMS;" in sql
    assert "ITEMS_OUT" not in sql          # targets are written through TGT_DB/TGT_SCHEMA, not a view
    assert sql.endswith("\n")


def test_production_views_sql_orders_views_by_logical_name():
    mappings = {"sources": {
        "b.yxdb": {"snowflake": "S.R.ZEBRA", "logical": "ZEBRA", "tool_ids": ["2"]},
        "a.yxdb": {"snowflake": "S.R.APPLE", "logical": "APPLE", "tool_ids": ["1"]}}}
    sql = production_views_sql(WF, mappings, {"target_database": "ANALYTICS"})
    assert sql.index("MIG_SRC_WF0009.APPLE") < sql.index("MIG_SRC_WF0009.ZEBRA")


def test_production_views_sql_refuses_a_source_without_a_logical_name():
    mappings = {"sources": {"a.yxdb": {"snowflake": "S.R.APPLE", "tool_ids": ["4"]}}}
    with pytest.raises(ValueError, match="tool 4"):
        production_views_sql(WF, mappings, {"target_database": "ANALYTICS"})


# --- CLI exit codes (Global Constraints: 0 success, 1 domain failure, 2 usage or unexpected) ---

def cli(module, tmp_path, *extra):
    return subprocess.run([sys.executable, str(Path(module.__file__)), *extra, "--root", str(tmp_path)],
                          capture_output=True, text=True)


def test_load_golden_cli_loads_a_set(tmp_path):
    build(tmp_path)
    done = cli(load_golden, tmp_path, WF, "normal")
    assert done.returncode == 0
    assert json.loads(done.stdout)["args"]["SRC_SCHEMA"] == "MIG_GOLDEN_WF0009_NORMAL"
    assert (Repo(tmp_path).wf(WF, ".sandbox.duckdb")).exists()


def test_load_golden_cli_exits_one_when_a_source_has_no_logical_name(tmp_path):
    build(tmp_path, {"sources": {"sales/items.yxdb": {"snowflake": "SALES.RAW.ITEMS", "tool_ids": ["1"]}},
                     "outputs": {}})
    done = cli(load_golden, tmp_path, WF, "normal")
    assert done.returncode == 1 and "tool 1" in done.stderr and "Traceback" not in done.stderr


@pytest.mark.parametrize("wf_id,golden_set", [("wf_9999", "normal"), (WF, "no_such_set")])
def test_load_golden_cli_exits_two_when_there_is_nothing_to_load(tmp_path, wf_id, golden_set):
    build(tmp_path)
    done = cli(load_golden, tmp_path, wf_id, golden_set)
    assert done.returncode == 2 and "Traceback" not in done.stderr
    assert not Repo(tmp_path).wf(wf_id, ".sandbox.duckdb").exists()


def test_load_golden_cli_exits_two_when_a_golden_input_is_missing(tmp_path):
    repo = build(tmp_path)
    repo.wf(WF, "golden", "inputs", "normal", "1.csv").unlink()
    done = cli(load_golden, tmp_path, WF, "normal")
    assert done.returncode == 2 and "tool 1" in done.stderr and "Traceback" not in done.stderr


def test_gen_source_views_cli_writes_ddl(tmp_path):
    repo = build(tmp_path)
    write_yaml(repo.global_mappings, {"program": {"target_database": "ANALYTICS"}})
    done = cli(gen_source_views, tmp_path, WF)
    assert done.returncode == 0 and "MIG_SRC_WF0009.ITEMS" in done.stdout


def test_gen_source_views_cli_exits_one_when_a_source_has_no_logical_name(tmp_path):
    repo = build(tmp_path, {"sources": {"sales/items.yxdb": {"snowflake": "SALES.RAW.ITEMS", "tool_ids": ["4"]}}})
    write_yaml(repo.global_mappings, {"program": {"target_database": "ANALYTICS"}})
    done = cli(gen_source_views, tmp_path, WF)
    assert done.returncode == 1 and "tool 4" in done.stderr and "Traceback" not in done.stderr


@pytest.mark.parametrize("with_global", [True, False])
def test_gen_source_views_cli_exits_two_when_an_input_file_is_missing(tmp_path, with_global):
    repo = Repo(tmp_path)
    if with_global:                       # global.yaml present, but the workflow has no mappings
        write_yaml(repo.global_mappings, {"program": {"target_database": "ANALYTICS"}})
    else:                                 # workflow mappings present, but no program answers
        build(tmp_path)
    done = cli(gen_source_views, tmp_path, WF)
    assert done.returncode == 2 and "Traceback" not in done.stderr
