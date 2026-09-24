"""Task L4 (R1), the Snowpark twin of `c4:write_mode`: `lib.snowpark_rules`' `rule:write_mode`.

A Snowpark procedure writes a final target with ONE call, and the contract's write mode picks it:
overwrite -> `.write.mode("overwrite").save_as_table(f"{tgt_db}.{tgt_schema}.<LOGICAL>")`; append ->
`.mode("append")`; truncate_append -> `.mode("truncate")` (snowflake-snowpark-python 1.55's own
"truncate the table, then append" mode); update_insert (intake's merge) ->
`session.table(<target>).merge(<source>, <target>["K"] == <source>["K"] & …, [when_matched().update(…),
when_not_matched().insert(…)])` on exactly the contract's keys. A `.update(…)`/`.delete(…)` of the
target is the Output tool's PreSQL (before the write) or PostSQL (after it) and needs the tool to
have one. Checked on the AST; nothing here runs a Snowpark session, and nothing has run on Snowflake.
"""
from __future__ import annotations

import json

import pytest

import compile_check as cc
import render_snowpark as rs
from lib import snowpark_rules as rules
from lib.io import write_yaml
from lib.paths import Repo

WF, SEG = "wf_0006", "seg_02"
TARGET = 'f"{tgt_db}.{tgt_schema}.REVENUE"'

HEAD = '''# tool 3: Python tool -- revenue schedule
from snowflake.snowpark.functions import col, lit, when_matched, when_not_matched
from snowflake.snowpark.types import StructType, StructField, StringType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()
    out = session.create_dataframe(pdf, schema=StructType([StructField("CUSTOMER", StringType(20))]))
    # tool 5: Output Data -- the target write below
'''
TAIL = '    return "OK"\n'

WRITES = {
    "overwrite": f'    out.write.mode("overwrite").save_as_table({TARGET})\n',
    "append": f'    out.write.mode("append").save_as_table({TARGET})\n',
    "truncate": f'    out.write.mode("truncate").save_as_table({TARGET})\n',
    "no_mode": f'    out.write.save_as_table({TARGET})\n',
    "camel_overwrite": f'    out.write.mode("overwrite").saveAsTable({TARGET})\n',
    "merge": (f'    target = session.table({TARGET})\n'
              '    target.merge(out, (target["CUSTOMER"] == out["CUSTOMER"]),\n'
              '                 [when_matched().update({"CUSTOMER": out["CUSTOMER"]}),\n'
              '                  when_not_matched().insert({"CUSTOMER": out["CUSTOMER"]})])\n'),
    "merge_chained": (f'    session.table({TARGET}).merge(out, session.table({TARGET})["CUSTOMER"] == out["CUSTOMER"],\n'
                      '        [when_matched().update({"CUSTOMER": out["CUSTOMER"]}),\n'
                      '         when_not_matched().insert({"CUSTOMER": out["CUSTOMER"]})])\n'),
    "update": (f'    fix = session.table({TARGET})\n'
               '    fix.update({"CUSTOMER": lit("X")}, col("CUSTOMER").is_null())\n'),
    "delete": (f'    old = session.table({TARGET})\n'
               '    old.delete(col("CUSTOMER").is_null())\n'),
}

REQUIRED = {
    "overwrite": '.write.mode("overwrite").save_as_table(f"{tgt_db}.{tgt_schema}.REVENUE")',
    "append": '.write.mode("append").save_as_table(f"{tgt_db}.{tgt_schema}.REVENUE")',
    "truncate_append": '.write.mode("truncate").save_as_table(f"{tgt_db}.{tgt_schema}.REVENUE")',
    "update_insert": ('session.table(f"{tgt_db}.{tgt_schema}.REVENUE").merge(<source>, <target>["CUSTOMER"] == '
                      '<source>["CUSTOMER"], [when_matched().update({…}), when_not_matched().insert({…})])'),
}


def module(*writes: str) -> str:
    return HEAD + "".join(WRITES.get(write, write) for write in writes) + TAIL


def contract(write_mode: str | None = "overwrite", keys: list[str] | None = None,
             pre_sql: str | None = None, post_sql: str | None = None) -> dict:
    target = {"stream": "3_Output", "kind": "target", "tool_id": "5", "table": None, "logical": "REVENUE",
              "columns": [{"name": "CUSTOMER", "type": "VARCHAR(20)", "nullable": True}],
              "keys": ["CUSTOMER"] if keys is None else keys}
    if write_mode is not None:
        target["write_mode"] = write_mode
    return {"segment": SEG, "target": "snowpark",
            "inputs": [{"from": "seg_01", "stream": "2_T", "table": "MIG_WORK.WF0006_SEG_01_OUT", "columns": []}],
            "outputs": [target],
            "nodes": [{"tool_id": "3", "type": "python", "config": {}},
                      {"tool_id": "5", "type": "output", "config": {"pre_sql": pre_sql, "post_sql": post_sql}}]}


def write_mode_errors(source: str, doc: dict) -> list[str]:
    return [error for error in rules.check_proc_py(source, WF, SEG, doc) if error.startswith("rule:write_mode")]


@pytest.mark.parametrize("mode, write", [
    ("overwrite", "overwrite"), ("overwrite", "camel_overwrite"), ("append", "append"),
    ("truncate_append", "truncate"), ("update_insert", "merge"), ("merge", "merge"),
    ("update_insert", "merge_chained"),
])
def test_each_write_mode_in_its_own_form_passes(mode, write):
    assert rules.check_proc_py(module(write), WF, SEG, contract(mode)) == []


@pytest.mark.parametrize("mode, write, found", [
    ("overwrite", "append", '.mode("append").save_as_table'),
    ("overwrite", "no_mode", ".save_as_table with no .mode(…)"),
    ("overwrite", "merge", ".merge"),
    ("append", "overwrite", '.mode("overwrite").save_as_table'),
    ("append", "truncate", '.mode("truncate").save_as_table'),
    ("truncate_append", "append", '.mode("append").save_as_table'),
    ("update_insert", "overwrite", '.mode("overwrite").save_as_table'),
    ("update_insert", "append", '.mode("append").save_as_table'),
])
def test_a_write_in_another_modes_form_is_refused(mode, write, found):
    assert write_mode_errors(module(write), contract(mode)) == [
        f"rule:write_mode: REVENUE is {mode}: write it with {REQUIRED[mode]}, not {found}"]


def test_a_target_the_module_never_writes_is_refused():
    assert write_mode_errors(module(), contract("overwrite")) == [
        f"rule:write_mode: REVENUE is overwrite: write it with {REQUIRED['overwrite']}; the module never writes "
        f'f"{{tgt_db}}.{{tgt_schema}}.REVENUE"']


def test_a_target_written_twice_is_refused():
    assert write_mode_errors(module("overwrite", "overwrite"), contract("overwrite")) == [
        f"rule:write_mode: REVENUE is overwrite: write it with {REQUIRED['overwrite']} once, not "
        '.mode("overwrite").save_as_table + .mode("overwrite").save_as_table']


@pytest.mark.parametrize("join, keys", [
    ('(target["CUSTOMER"] == out["CUSTOMER"]) & (target["PERIOD"] == out["PERIOD"])', ["CUSTOMER"]),
    ('target["CUSTOMER"] == out["CUSTOMER"]', ["CUSTOMER", "PERIOD"]),
    ('target["CUSTOMER"] == target["CUSTOMER"]', ["CUSTOMER"]),
    ('(target["CUSTOMER"] == out["CUSTOMER"]) | target["CUSTOMER"].is_null()', ["CUSTOMER"]),
    ('target["CUSTOMER"].equal_null(out["CUSTOMER"])', ["CUSTOMER"]),
])
def test_a_merge_not_on_exactly_the_contracts_keys_is_refused(join, keys):
    source = module(WRITES["merge"].replace('(target["CUSTOMER"] == out["CUSTOMER"])', join))
    errors = write_mode_errors(source, contract("update_insert", keys=keys))
    assert len(errors) == 1 and errors[0].startswith(
        "rule:write_mode: REVENUE is update_insert: the merge into f\"{tgt_db}.{tgt_schema}.REVENUE\" must join on "
        f"exactly the contract's keys {', '.join(keys)}"), errors


def test_a_merge_on_col_references_and_two_keys_in_any_order_passes():
    source = module(WRITES["merge"].replace(
        '(target["CUSTOMER"] == out["CUSTOMER"])',
        '(out.col("PERIOD") == target.col("PERIOD")) & (target["CUSTOMER"] == out["CUSTOMER"])'))
    assert rules.check_proc_py(source, WF, SEG, contract("update_insert", keys=["CUSTOMER", "PERIOD"])) == []


def test_a_merge_without_both_clauses_is_refused():
    source = module(WRITES["merge"].replace('[when_matched().update({"CUSTOMER": out["CUSTOMER"]}),\n', "[\n"))
    assert write_mode_errors(source, contract("update_insert")) == [
        'rule:write_mode: REVENUE is update_insert: the merge into f"{tgt_db}.{tgt_schema}.REVENUE" needs both '
        "when_matched().update(…) and when_not_matched().insert(…) (Update; Insert if new)"]


def test_presql_and_postsql_are_allowed_only_when_the_output_tool_has_them():
    source = module("delete", "merge", "update")
    assert write_mode_errors(source, contract("update_insert", pre_sql="DELETE …", post_sql="UPDATE …")) == []
    errors = write_mode_errors(source, contract("update_insert"))
    assert errors == [
        f"rule:write_mode: REVENUE is update_insert: write it with {REQUIRED['update_insert']} alone, not "
        ".delete + .merge + .update (a call before the write is allowed only as tool 5's PreSQL, and it has none; "
        "a call after the write is allowed only as tool 5's PostSQL, and it has none)"]


@pytest.mark.parametrize("write_mode", [None, "update_only"])
def test_a_target_without_a_known_write_mode_is_refused(write_mode):
    errors = write_mode_errors(module("overwrite"), contract(write_mode))
    assert len(errors) == 1 and "REVENUE" in errors[0] and "write mode" in errors[0], errors


# --- through compile_check.py --target snowpark ------------------------------------------------

def _build(tmp_path, source: str, doc: dict, mappings: dict | None = None) -> Repo:
    repo = Repo(tmp_path)
    write_yaml(tmp_path / "mappings" / "global.yaml", {"program": {"snowpark_runtime": "3.11"}})
    seg_dir = repo.seg(WF, SEG)
    seg_dir.mkdir(parents=True, exist_ok=True)
    nodes = doc.pop("nodes")
    (seg_dir / "contract.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    (seg_dir / "dag.json").write_text(json.dumps({"nodes": nodes}), encoding="utf-8", newline="\n")
    (seg_dir / "proc.py").write_text(source, encoding="utf-8", newline="\n")
    if mappings is not None:
        write_yaml(repo.wf(WF, "intake", "mappings.yaml"), mappings)
    assert rs.main([WF, SEG, "--root", str(tmp_path)]) == 0
    return repo


def test_compile_check_reports_the_snowpark_write_mode_rule(tmp_path):
    report = cc.compile_check(_build(tmp_path, module("append"), contract("overwrite")), WF, SEG)
    assert report["status"] == "ERROR"
    assert report["errors"] == [
        f'rule:write_mode: REVENUE is overwrite: write it with {REQUIRED["overwrite"]}, not .mode("append").save_as_table']


def test_compile_check_takes_the_mode_from_the_mappings_and_the_prepost_sql_from_dag_json(tmp_path):
    mappings = {"outputs": {"out/revenue.yxdb": {"logical": "REVENUE", "mode": "merge", "keys": ["CUSTOMER"]}}}
    doc = contract(None, post_sql="UPDATE dbo.REVENUE SET CUSTOMER = 'X' WHERE CUSTOMER IS NULL")
    report = cc.compile_check(_build(tmp_path, module("merge", "update"), doc, mappings), WF, SEG)
    assert report == {"status": "OK", "target": "snowpark", "errors": [], "statements": 0}


def test_compile_check_exits_2_on_a_snowpark_target_with_no_write_mode(tmp_path, capsys):
    _build(tmp_path, module("overwrite"), contract(None))
    assert cc.main([WF, SEG, "--root", str(tmp_path)]) == 2
    assert "REVENUE" in capsys.readouterr().err


# --- fix round 1 (I2): the merge's keys compare case-insensitively on both sides ------------------

@pytest.mark.parametrize("join, key", [
    ('target["\\"Customer ID\\""] == out["\\"customer id\\""]', "Customer ID"),
    ('target["acctid"] == out["ACCTID"]', "AcctId"),
    ('target["\\"AcctId\\""] == out.col("ACCTID")', "AcctId"),
])
def test_a_merge_key_matches_the_contract_in_any_case(join, key):
    source = module(WRITES["merge"].replace('(target["CUSTOMER"] == out["CUSTOMER"])', join))
    assert write_mode_errors(source, contract("update_insert", keys=[key])) == []
