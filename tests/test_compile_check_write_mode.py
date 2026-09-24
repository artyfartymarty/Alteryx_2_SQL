"""Task L4 (R1): `compile_check.py`'s `c4:write_mode` -- every final target is written with exactly
the statement form its write mode requires.

The live probe of wf_0001 (a real model through the real SDK) wrote an `overwrite` target as
`TRUNCATE TABLE IDENTIFIER(:SALES_SUMMARY_TGT);` + `INSERT INTO …`. It compiled, and then
`validate_segment.py` failed every golden set with a catalog error that named no rule. The forms
(`cookbook/output.md`, "Config fields that change the pattern"):

- overwrite -> `CREATE OR REPLACE TABLE IDENTIFIER(:X_TGT) AS …`, nothing else on it;
- append -> `INSERT INTO IDENTIFIER(:X_TGT) …`, nothing else on it;
- truncate_append -> a `TRUNCATE`/`DELETE FROM` of it (no WHERE) before one `INSERT INTO` it;
- update_insert (intake's `merge`) -> one `MERGE INTO IDENTIFIER(:X_TGT)` on the contract's keys,
  with `WHEN MATCHED THEN UPDATE` and `WHEN NOT MATCHED THEN INSERT`;
- the Output tool's PreSQL/PostSQL (`dag.json` node config) are separate statements before/after
  the write, and only a tool that has one may have them.

Nothing here has run on Snowflake: the forms are checked in the text, and the run is the local
DuckDB double.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import compile_check as cc
from lib.io import read_json, write_json
from lib.paths import Repo

ROOT = Path(__file__).resolve().parents[1]
WF, SEG = "wf_0009", "seg_01"

HEAD = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';
  LET ITEMS_OUT_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.ITEMS_OUT';
"""
TAIL = """  RETURN 'OK';
END;
$$;"""
QUERY = "SELECT ID, NOTE FROM IDENTIFIER(:ITEMS_SRC) WHERE NOTE IS NOT NULL"

#: One statement per form, each on the one target this segment has.
FORMS = {
    "ctas": f"CREATE OR REPLACE TABLE IDENTIFIER(:ITEMS_OUT_TGT) AS\n  -- tool 2: Filter\n  {QUERY};",
    "transient": f"CREATE OR REPLACE TRANSIENT TABLE IDENTIFIER(:ITEMS_OUT_TGT) AS\n  {QUERY};",
    "insert": f"INSERT INTO IDENTIFIER(:ITEMS_OUT_TGT) (ID, NOTE)\n  {QUERY};",
    "with_insert": f"WITH t2_filter AS ({QUERY})\n  INSERT INTO IDENTIFIER(:ITEMS_OUT_TGT) (ID, NOTE) SELECT ID, NOTE FROM t2_filter;",
    "truncate": "TRUNCATE TABLE IDENTIFIER(:ITEMS_OUT_TGT);",
    "delete": "DELETE FROM IDENTIFIER(:ITEMS_OUT_TGT);",
    "delete_where": "DELETE FROM IDENTIFIER(:ITEMS_OUT_TGT) WHERE ID < 0;",
    "update": "UPDATE IDENTIFIER(:ITEMS_OUT_TGT) SET NOTE = 'x' WHERE NOTE IS NULL;",
    "merge": ("MERGE INTO IDENTIFIER(:ITEMS_OUT_TGT) AS T\n"
              f"  USING ({QUERY}) AS S\n"
              "  ON T.ID = S.ID\n"
              "  WHEN MATCHED THEN UPDATE SET NOTE = S.NOTE\n"
              "  WHEN NOT MATCHED THEN INSERT (ID, NOTE) VALUES (S.ID, S.NOTE);"),
}

COLUMNS = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": True},
           {"name": "NOTE", "type": "VARCHAR", "nullable": True}]


def proc(*forms: str) -> str:
    return HEAD + "".join(f"  {FORMS.get(form, form)}\n" for form in forms) + TAIL


def contract(write_mode: str | None = "overwrite", keys: list[str] | None = None) -> dict:
    target = {"stream": "2_True", "table": None, "kind": "target", "logical": "ITEMS_OUT", "tool_id": "7",
              "columns": COLUMNS, "keys": ["ID"] if keys is None else keys}
    if write_mode is not None:
        target["write_mode"] = write_mode
    doc = {"segment": SEG, "workflow": WF, "target": "sql",
           "inputs": [{"logical": "ITEMS", "tool_id": "1", "columns": COLUMNS, "keys": ["ID"]}],
           "outputs": [target], "row_relation": "filter",
           "ordering": {"keys": ["ID"], "alteryx_deterministic": True}, "tolerances": {}}
    doc["output"] = doc["outputs"][0]
    return doc


def build(tmp_path: Path, sql: str, doc: dict, *, pre_sql: str | None = None, post_sql: str | None = None,
          mappings: dict | None = None) -> Repo:
    repo = Repo(tmp_path)
    seg_dir = repo.seg(WF, SEG)
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / "proc.sql").write_text(sql, encoding="utf-8", newline="\n")
    write_json(seg_dir / "contract.json", doc)
    write_json(seg_dir / "dag.json", {"nodes": [
        {"tool_id": "1", "type": "input", "config": {}},
        {"tool_id": "2", "type": "filter", "config": {}},
        {"tool_id": "7", "type": "output", "config": {"write_mode": doc["outputs"][0].get("write_mode"),
                                                       "pre_sql": pre_sql, "post_sql": post_sql}}]})
    if mappings is not None:
        path = repo.wf(WF, "intake", "mappings.yaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        path.write_text(yaml.safe_dump(mappings), encoding="utf-8")
    return repo


def write_mode_errors(tmp_path: Path, sql: str, doc: dict, **kwargs) -> list[str]:
    report = cc.compile_check(build(tmp_path, sql, doc, **kwargs), WF, SEG)
    return [error for error in report["errors"] if error.startswith("c4:write_mode")]


def compiles(tmp_path: Path, sql: str, doc: dict, **kwargs) -> dict:
    return cc.compile_check(build(tmp_path, sql, doc, **kwargs), WF, SEG)


# --- overwrite ----------------------------------------------------------------------------------

def test_overwrite_written_as_create_or_replace_table_as_compiles(tmp_path):
    report = compiles(tmp_path, proc("ctas"), contract("overwrite"))
    assert report["status"] == "OK", report["errors"]


def test_the_live_shape_truncate_plus_insert_for_an_overwrite_target_is_refused_with_the_rule(tmp_path):
    errors = write_mode_errors(tmp_path, proc("truncate", "with_insert"), contract("overwrite"))
    assert errors == ["c4:write_mode: ITEMS_OUT is overwrite: write it with CREATE OR REPLACE TABLE "
                      "IDENTIFIER(:ITEMS_OUT_TGT) AS …, not TRUNCATE + INSERT"]


@pytest.mark.parametrize("forms, found", [
    (("insert",), "INSERT"),
    (("delete", "insert"), "DELETE + INSERT"),
    (("merge",), "MERGE"),
    (("transient",), "CREATE OR REPLACE TRANSIENT TABLE"),
])
def test_overwrite_in_any_other_form_is_refused(tmp_path, forms, found):
    errors = write_mode_errors(tmp_path, proc(*forms), contract("overwrite"))
    assert errors == ["c4:write_mode: ITEMS_OUT is overwrite: write it with CREATE OR REPLACE TABLE "
                      f"IDENTIFIER(:ITEMS_OUT_TGT) AS …, not {found}"]


@pytest.mark.parametrize("statement", [
    f"CREATE OR REPLACE TABLE IDENTIFIER(:ITEMS_OUT_TGT) COPY GRANTS AS {QUERY};",
    "CREATE OR REPLACE TABLE IDENTIFIER(:ITEMS_OUT_TGT) (ID NUMBER(38,0), NOTE VARCHAR);",
])
def test_a_create_that_is_not_create_or_replace_table_as_says_so(tmp_path, statement):
    errors = write_mode_errors(tmp_path, proc(statement), contract("overwrite"))
    assert errors == ["c4:write_mode: ITEMS_OUT is overwrite: write it with CREATE OR REPLACE TABLE "
                      "IDENTIFIER(:ITEMS_OUT_TGT) AS …, not CREATE OR REPLACE TABLE with no AS right after the name"]


def test_overwrite_with_a_second_write_after_it_is_refused(tmp_path):
    errors = write_mode_errors(tmp_path, proc("ctas", "insert"), contract("overwrite"))
    assert errors == ["c4:write_mode: ITEMS_OUT is overwrite: write it with CREATE OR REPLACE TABLE "
                      "IDENTIFIER(:ITEMS_OUT_TGT) AS … alone, not CREATE OR REPLACE TABLE … AS + INSERT "
                      "(a statement after the write is allowed only as tool 7's PostSQL, and it has none)"]


def test_overwrite_written_twice_is_refused(tmp_path):
    errors = write_mode_errors(tmp_path, proc("ctas", "ctas"), contract("overwrite"))
    assert errors == ["c4:write_mode: ITEMS_OUT is overwrite: write it with CREATE OR REPLACE TABLE "
                      "IDENTIFIER(:ITEMS_OUT_TGT) AS … once, not CREATE OR REPLACE TABLE … AS + "
                      "CREATE OR REPLACE TABLE … AS"]


def test_a_target_the_procedure_never_writes_is_refused(tmp_path):
    sql = proc(f"CREATE OR REPLACE TABLE MIG_WORK.WF0009_SEG_01_OUT AS {QUERY};")
    errors = write_mode_errors(tmp_path, sql, contract("overwrite"))
    assert errors == ["c4:write_mode: ITEMS_OUT is overwrite: write it with CREATE OR REPLACE TABLE "
                      "IDENTIFIER(:ITEMS_OUT_TGT) AS …; the procedure never writes IDENTIFIER(:ITEMS_OUT_TGT)"]


# --- append -------------------------------------------------------------------------------------

@pytest.mark.parametrize("form", ["insert", "with_insert"])
def test_append_written_as_insert_into_compiles(tmp_path, form):
    report = compiles(tmp_path, proc(form), contract("append"))
    assert report["status"] == "OK", report["errors"]


@pytest.mark.parametrize("forms, found", [
    (("ctas",), "CREATE OR REPLACE TABLE … AS"),
    (("truncate", "insert"), "TRUNCATE + INSERT"),
    (("delete", "insert"), "DELETE + INSERT"),
    (("merge",), "MERGE"),
])
def test_append_in_any_other_form_is_refused(tmp_path, forms, found):
    errors = write_mode_errors(tmp_path, proc(*forms), contract("append"))
    assert len(errors) == 1, errors
    assert errors[0].startswith("c4:write_mode: ITEMS_OUT is append: write it with INSERT INTO "
                                "IDENTIFIER(:ITEMS_OUT_TGT) (<columns>) SELECT …"), errors
    assert errors[0].endswith(f"not {found}") or f"not {found} (" in errors[0], errors


# --- truncate_append ----------------------------------------------------------------------------

@pytest.mark.parametrize("forms", [("truncate", "insert"), ("delete", "with_insert")])
def test_truncate_append_written_as_a_clear_then_insert_compiles(tmp_path, forms):
    report = compiles(tmp_path, proc(*forms), contract("truncate_append"))
    assert report["status"] == "OK", report["errors"]


@pytest.mark.parametrize("forms, found", [
    (("insert",), "INSERT"),
    (("ctas",), "CREATE OR REPLACE TABLE … AS"),
    (("insert", "truncate"), "INSERT + TRUNCATE"),
    (("delete_where", "insert"), "DELETE … WHERE + INSERT"),
    (("merge",), "MERGE"),
])
def test_truncate_append_in_any_other_form_is_refused(tmp_path, forms, found):
    errors = write_mode_errors(tmp_path, proc(*forms), contract("truncate_append"))
    assert errors == ["c4:write_mode: ITEMS_OUT is truncate_append: write it with TRUNCATE TABLE "
                      "IDENTIFIER(:ITEMS_OUT_TGT) (or DELETE FROM it, with no WHERE) followed by INSERT INTO "
                      f"IDENTIFIER(:ITEMS_OUT_TGT) (<columns>) SELECT …, not {found}"]


# --- update_insert / merge ----------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["update_insert", "merge"])
def test_update_insert_written_as_a_merge_on_the_keys_compiles(tmp_path, mode):
    report = compiles(tmp_path, proc("merge"), contract(mode))
    assert report["status"] == "OK", report["errors"]


def test_a_merge_on_two_keys_in_either_order_and_quoted_compiles(tmp_path):
    sql = proc(FORMS["merge"].replace("ON T.ID = S.ID", 'ON (S."NOTE" = T."NOTE") AND T.ID = S.ID'))
    report = compiles(tmp_path, sql, contract("update_insert", keys=["NOTE", "ID"]))
    assert report["status"] == "OK", report["errors"]


@pytest.mark.parametrize("forms, found", [
    (("insert",), "INSERT"),
    (("update", "insert"), "UPDATE + INSERT"),
    (("ctas",), "CREATE OR REPLACE TABLE … AS"),
])
def test_update_insert_in_any_other_form_is_refused(tmp_path, forms, found):
    errors = write_mode_errors(tmp_path, proc(*forms), contract("update_insert"))
    assert errors == ["c4:write_mode: ITEMS_OUT is update_insert: write it with MERGE INTO "
                      "IDENTIFIER(:ITEMS_OUT_TGT) … ON the contract's keys ID … WHEN MATCHED THEN UPDATE … "
                      f"WHEN NOT MATCHED THEN INSERT …, not {found}"]


@pytest.mark.parametrize("on_clause, found", [
    ("ON T.ID = S.ID AND T.NOTE = S.NOTE", "T.ID = S.ID AND T.NOTE = S.NOTE"),   # a non-key narrows the match
    ("ON T.NOTE = S.NOTE", "T.NOTE = S.NOTE"),                                   # the wrong key
    ("ON EQUAL_NULL(T.ID, S.ID)", "EQUAL_NULL(T.ID, S.ID)"),                     # NULL keys would match
    ("ON T.ID = T.ID", "T.ID = T.ID"),                                           # a tautology
    ("ON T.ID = S.ID OR T.ID IS NULL", "T.ID = S.ID OR T.ID IS NULL"),
])
def test_a_merge_that_is_not_on_exactly_the_contracts_keys_is_refused(tmp_path, on_clause, found):
    sql = proc(FORMS["merge"].replace("ON T.ID = S.ID", on_clause))
    errors = write_mode_errors(tmp_path, sql, contract("update_insert"))
    assert errors == ["c4:write_mode: ITEMS_OUT is update_insert: the MERGE INTO IDENTIFIER(:ITEMS_OUT_TGT) "
                      "must match on exactly the contract's keys ID -- ON <target>.<KEY> = <source>.<KEY> for "
                      f"each key, joined by AND, nothing else -- not ON {found}"]


def test_a_merge_without_both_clauses_is_refused(tmp_path):
    sql = proc(FORMS["merge"].replace("  WHEN MATCHED THEN UPDATE SET NOTE = S.NOTE\n", ""))
    errors = write_mode_errors(tmp_path, sql, contract("update_insert"))
    assert errors == ["c4:write_mode: ITEMS_OUT is update_insert: the MERGE INTO IDENTIFIER(:ITEMS_OUT_TGT) "
                      "needs both WHEN MATCHED THEN UPDATE and WHEN NOT MATCHED THEN INSERT (Update; Insert if new)"]


# --- PreSQL / PostSQL ---------------------------------------------------------------------------

def test_presql_and_postsql_statements_are_separate_and_allowed_when_the_tool_has_them(tmp_path):
    sql = proc("delete_where", "merge", "update")
    report = compiles(tmp_path, sql, contract("update_insert"),
                      pre_sql="DELETE FROM dbo.ITEMS_OUT WHERE ID < 0",
                      post_sql="UPDATE dbo.ITEMS_OUT SET NOTE = 'x' WHERE NOTE IS NULL")
    assert report["status"] == "OK", report["errors"]


# Task L8 fix round 1 (M3): the tool's PreSQL/PostSQL is now REQUIRED as well as allowed, so each of the
# next two keeps the other clause's statement in place to test one rule at a time.

def test_a_statement_before_the_write_is_refused_when_the_tool_has_no_presql(tmp_path):
    errors = write_mode_errors(tmp_path, proc("delete_where", "merge", "update"), contract("update_insert"),
                               post_sql="UPDATE dbo.ITEMS_OUT SET NOTE = 'x'")
    assert errors == ["c4:write_mode: ITEMS_OUT is update_insert: write it with MERGE INTO "
                      "IDENTIFIER(:ITEMS_OUT_TGT) … ON the contract's keys ID … WHEN MATCHED THEN UPDATE … "
                      "WHEN NOT MATCHED THEN INSERT … alone, not DELETE … WHERE + MERGE + UPDATE (a statement "
                      "before the write is allowed only as tool 7's PreSQL, and it has none)"]


def test_a_statement_after_the_write_is_refused_when_the_tool_has_no_postsql(tmp_path):
    errors = write_mode_errors(tmp_path, proc("delete_where", "merge", "update"), contract("update_insert"),
                               pre_sql="DELETE FROM dbo.ITEMS_OUT WHERE ID < 0")
    assert errors == ["c4:write_mode: ITEMS_OUT is update_insert: write it with MERGE INTO "
                      "IDENTIFIER(:ITEMS_OUT_TGT) … ON the contract's keys ID … WHEN MATCHED THEN UPDATE … "
                      "WHEN NOT MATCHED THEN INSERT … alone, not DELETE … WHERE + MERGE + UPDATE (a statement "
                      "after the write is allowed only as tool 7's PostSQL, and it has none)"]


@pytest.mark.parametrize("forms, missing", [
    (("merge", "update"), "tool 7 has a PreSQL: it is a statement on IDENTIFIER(:ITEMS_OUT_TGT) before the write, "
                          "and there is none"),
    (("delete_where", "merge"), "tool 7 has a PostSQL: it is a statement on IDENTIFIER(:ITEMS_OUT_TGT) after the "
                                "write, and there is none"),
])
def test_a_presql_or_postsql_the_tool_has_is_required_not_only_allowed(tmp_path, forms, missing):
    """Task L8 fix round 1 (M3): a translation skeleton's PreSQL/PostSQL slot deleted outright used to
    compile and drop the statement silently; `dbt:hooks` already required a hook."""
    errors = write_mode_errors(tmp_path, proc(*forms), contract("update_insert"),
                               pre_sql="DELETE FROM dbo.ITEMS_OUT WHERE ID < 0",
                               post_sql="UPDATE dbo.ITEMS_OUT SET NOTE = 'x' WHERE NOTE IS NULL")
    assert len(errors) == 1 and errors[0].endswith(f"({missing})"), errors


def test_an_overwrite_target_with_a_postsql_update_compiles(tmp_path):
    report = compiles(tmp_path, proc("ctas", "update"), contract("overwrite"),
                      post_sql="UPDATE dbo.ITEMS_OUT SET NOTE = 'x' WHERE NOTE IS NULL")
    assert report["status"] == "OK", report["errors"]


# --- where the write mode comes from ------------------------------------------------------------

def test_a_contract_without_write_mode_falls_back_to_intake_mappings_mode(tmp_path):
    mappings = {"sources": {}, "outputs": {"out/items.yxdb": {
        "snowflake": "A.B.ITEMS_OUT", "logical": "ITEMS_OUT", "tool_ids": ["7"], "mode": "merge", "keys": ["ID"]}}}
    assert compiles(tmp_path / "a", proc("merge"), contract(None), mappings=mappings)["status"] == "OK"
    errors = write_mode_errors(tmp_path / "b", proc("ctas"), contract(None), mappings=mappings)
    assert len(errors) == 1 and errors[0].startswith("c4:write_mode: ITEMS_OUT is merge: write it with MERGE"), errors


def test_a_target_with_no_write_mode_anywhere_is_a_usage_error(tmp_path, capsys):
    """The contract is the analyzer's and the mappings intake's: nothing a translator could change
    in the procedure fixes a missing write mode, so it is exit 2 (a missing prerequisite), with no
    report written."""
    repo = build(tmp_path, proc("ctas"), contract(None))
    assert cc.main([WF, SEG, "--root", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "ITEMS_OUT" in err and "write_mode" in err, err
    assert not repo.seg(WF, SEG, "compile_check.json").exists()


@pytest.mark.parametrize("bad", ["update_only", ""])
def test_an_unknown_write_mode_is_a_usage_error(tmp_path, capsys, bad):
    build(tmp_path, proc("ctas"), contract(bad))
    assert cc.main([WF, SEG, "--root", str(tmp_path)]) == 2
    assert "write_mode" in capsys.readouterr().err


def test_an_update_insert_target_without_keys_is_a_usage_error(tmp_path, capsys):
    build(tmp_path, proc("merge"), contract("update_insert", keys=[]))
    assert cc.main([WF, SEG, "--root", str(tmp_path)]) == 2
    assert "keys" in capsys.readouterr().err


# --- the live model's procedure, verbatim -------------------------------------------------------

#: `segments/seg_01/proc.sql` as the live translator wrote it for wf_0001 (probe 3: a real model
#: through the real SDK, the sample's reference contract). It compiled (`OK: 4 statements`) and then
#: failed validation on every golden set with a catalog error.
LIVE_WF0001_PROC = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0001_SEG_01(
    SRC_DB STRING,
    SRC_SCHEMA STRING,
    TGT_DB STRING,
    TGT_SCHEMA STRING,
    RUN_ID STRING
)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN
    ALTER SESSION SET TIMEZONE = 'America/New_York';
    ALTER SESSION SET WEEK_START = 1;
    -- session parameters above per mappings/global.yaml (session).
    -- tool 1: read the orders source
    LET ORDERS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ORDERS';
    -- tool 7: sales_summary overwrite target
    LET SALES_SUMMARY_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.SALES_SUMMARY';
    -- tool 8: excluded_orders overwrite target
    LET EXCLUDED_ORDERS_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.EXCLUDED_ORDERS';

    -- tool 7: totals by region and size band (overwrite)
    TRUNCATE TABLE IDENTIFIER(:SALES_SUMMARY_TGT);
    WITH
        -- tool 1: read orders source
        t1_input AS (
            SELECT ORDER_ID, CUSTOMER, REGION, AMOUNT_TXT, QTY, ORDER_DATE, STATUS
            FROM IDENTIFIER(:ORDERS_SRC)
        ),
        -- tool 2: truncate CUSTOMER to String(10), rename STATUS to ORDER_STATUS
        t2_select AS (
            SELECT LEFT(CUSTOMER, 10) AS CUSTOMER,
                   STATUS AS ORDER_STATUS,
                   ORDER_ID,
                   REGION,
                   AMOUNT_TXT,
                   QTY,
                   ORDER_DATE
            FROM t1_input
        ),
        -- tool 3: keep the non-WEST rows (TRUE branch)
        t3_filter AS (
            SELECT CUSTOMER, ORDER_STATUS, ORDER_ID, REGION, AMOUNT_TXT, QTY, ORDER_DATE
            FROM t2_select
            WHERE REGION <> 'WEST'
        ),
        -- tool 4: AMOUNT / NET / SIZE_BAND (formulas run in order)
        t4_formula AS (
            SELECT
                CUSTOMER,
                ORDER_STATUS,
                ORDER_ID,
                REGION,
                AMOUNT_TXT,
                QTY,
                ORDER_DATE,
                TRY_TO_NUMBER(AMOUNT_TXT) AS AMOUNT,
                ROUND(
                    (TRY_TO_NUMBER(AMOUNT_TXT)::NUMBER) * (CASE WHEN QTY >= 10 THEN 0.9::NUMBER ELSE 1::NUMBER END),
                    2
                ) AS NET,
                CASE
                    WHEN TRY_TO_NUMBER(AMOUNT_TXT) >= 1000 THEN 'LARGE'
                    WHEN TRY_TO_NUMBER(AMOUNT_TXT) >= 100 THEN 'MEDIUM'
                    ELSE 'SMALL'
                END AS SIZE_BAND
            FROM t3_filter
        ),
        -- tool 5: totals by region and size band
        t5_summarize AS (
            SELECT
                REGION,
                SIZE_BAND,
                SUM(NET) AS TOTAL_NET,
                COUNT(ORDER_ID) AS ORDERS,
                COUNT(AMOUNT) AS PRICED_ORDERS,
                MAX(ORDER_DATE) AS LAST_ORDER
            FROM t4_formula
            GROUP BY REGION, SIZE_BAND
        ),
        -- tool 6: biggest totals first
        t6_sort AS (
            SELECT REGION, SIZE_BAND, TOTAL_NET, ORDERS, PRICED_ORDERS, LAST_ORDER
            FROM t5_summarize
            ORDER BY TOTAL_NET DESC, REGION ASC
        )
    INSERT INTO IDENTIFIER(:SALES_SUMMARY_TGT)
        (REGION, SIZE_BAND, TOTAL_NET, ORDERS, PRICED_ORDERS, LAST_ORDER)
    SELECT
        REGION,
        SIZE_BAND,
        TOTAL_NET::FLOAT AS TOTAL_NET,
        ORDERS,
        PRICED_ORDERS,
        LAST_ORDER
    FROM t6_sort;

    -- tool 8: WEST (and NULL-region) rows (overwrite)
    TRUNCATE TABLE IDENTIFIER(:EXCLUDED_ORDERS_TGT);
    WITH
        -- tool 1: read orders source
        t1_input AS (
            SELECT ORDER_ID, CUSTOMER, REGION, AMOUNT_TXT, QTY, ORDER_DATE, STATUS
            FROM IDENTIFIER(:ORDERS_SRC)
        ),
        -- tool 2: truncate CUSTOMER to String(10), rename STATUS to ORDER_STATUS
        t2_select AS (
            SELECT LEFT(CUSTOMER, 10) AS CUSTOMER,
                   STATUS AS ORDER_STATUS,
                   ORDER_ID,
                   REGION,
                   AMOUNT_TXT,
                   QTY,
                   ORDER_DATE
            FROM t1_input
        ),
        -- tool 3: WEST and NULL-region rows go to the FALSE branch
        t3_filter_false AS (
            SELECT CUSTOMER, ORDER_STATUS, ORDER_ID, REGION, AMOUNT_TXT, QTY, ORDER_DATE
            FROM t2_select
            WHERE REGION = 'WEST' OR REGION IS NULL
        )
    INSERT INTO IDENTIFIER(:EXCLUDED_ORDERS_TGT)
        (CUSTOMER, ORDER_STATUS, ORDER_ID, REGION, AMOUNT_TXT, QTY, ORDER_DATE)
    SELECT
        CUSTOMER,
        ORDER_STATUS,
        ORDER_ID,
        REGION,
        AMOUNT_TXT,
        QTY,
        ORDER_DATE
    FROM t3_filter_false;

    RETURN 'OK';
END
$$;
"""


def _committed_segment(tmp_path: Path, wf_id: str, seg: str) -> Repo:
    """The committed `workflows/<wf>` segment (contract, dag, procedure) and its intake mappings,
    copied into a scratch repo -- compile_check writes its report beside the procedure, and the
    committed trees must stay byte-for-byte what the mock run produces."""
    repo = Repo(tmp_path)
    source = ROOT / "workflows" / wf_id
    target = repo.seg(wf_id, seg)
    target.mkdir(parents=True)
    for name in ("contract.json", "dag.json", "proc.sql"):
        shutil.copy2(source / "segments" / seg / name, target / name)
    mappings = repo.wf(wf_id, "intake", "mappings.yaml")
    mappings.parent.mkdir(parents=True)
    shutil.copy2(source / "intake" / "mappings.yaml", mappings)
    return repo


def test_the_live_models_procedure_is_refused_for_both_overwrite_targets(tmp_path):
    repo = _committed_segment(tmp_path, "wf_0001", "seg_01")
    repo.seg("wf_0001", "seg_01", "proc.sql").write_text(LIVE_WF0001_PROC, encoding="utf-8", newline="\n")
    report = cc.compile_check(repo, "wf_0001", "seg_01")
    assert report["status"] == "ERROR"
    assert [e for e in report["errors"] if e.startswith("c4:")] == [
        "c4:write_mode: SALES_SUMMARY is overwrite: write it with CREATE OR REPLACE TABLE "
        "IDENTIFIER(:SALES_SUMMARY_TGT) AS …, not TRUNCATE + INSERT",
        "c4:write_mode: EXCLUDED_ORDERS is overwrite: write it with CREATE OR REPLACE TABLE "
        "IDENTIFIER(:EXCLUDED_ORDERS_TGT) AS …, not TRUNCATE + INSERT",
    ]


def test_the_live_models_procedure_is_refused_by_the_cli_with_exit_1(tmp_path):
    repo = _committed_segment(tmp_path, "wf_0001", "seg_01")
    repo.seg("wf_0001", "seg_01", "proc.sql").write_text(LIVE_WF0001_PROC, encoding="utf-8", newline="\n")
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "compile_check.py"), "wf_0001", "seg_01",
                             "--root", str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1, result.stderr
    assert "c4:write_mode: SALES_SUMMARY is overwrite" in result.stderr
    assert read_json(repo.seg("wf_0001", "seg_01", "compile_check.json"))["status"] == "ERROR"


# --- every committed procedure still compiles, every broken variant still fails only at validation --

def _committed_sql_segments() -> list[tuple[str, str]]:
    cases = []
    for contract_path in sorted((ROOT / "workflows").glob("wf_*/segments/seg_*/contract.json")):
        doc = read_json(contract_path)
        if (doc.get("target") or "sql") == "sql" and (contract_path.parent / "proc.sql").is_file():
            cases.append((contract_path.parents[2].name, contract_path.parent.name))
    return cases


@pytest.mark.parametrize("wf_id, seg", _committed_sql_segments())
def test_every_committed_sql_procedure_writes_its_targets_in_their_write_mode(tmp_path, wf_id, seg):
    report = cc.compile_check(_committed_segment(tmp_path, wf_id, seg), wf_id, seg)
    assert report["status"] == "OK", report["errors"]


def _broken_sql_variants() -> list[tuple[str, str, Path]]:
    return [(path.parents[2].name, path.parent.name, path)
            for path in sorted((ROOT / "samples").glob("wf_*/broken_sql/seg_*/*.sql"))]


#: Task L8 fix round 1 (M3): a missing PreSQL/PostSQL is now refused at compile time. These two variants
#: are exactly that mistake, so `c4:write_mode` names it before validation could; their `broken.json`
#: rows still describe what the validator finds when it is given them directly (tests/test_e2e_parity.py
#: does exactly that, and no mock scenario serves them: a scenario serves the first variant in name order).
CAUGHT_AT_COMPILE_TIME = {"02_postsql_not_translated.sql": "tool 10 has a PostSQL",
                          "03_presql_not_translated.sql": "tool 10 has a PreSQL"}


@pytest.mark.parametrize("wf_id, seg, variant", _broken_sql_variants(),
                         ids=lambda value: value.name if isinstance(value, Path) else value)
def test_every_broken_sql_variant_still_compiles_so_it_fails_at_validation(tmp_path, wf_id, seg, variant):
    """`broken.json` records each variant's failure as a validation diff class; the mock run serves a
    variant as the translator's first attempt. A variant this rule refused would fail at compile time
    instead, and its row would be wrong -- except the two that drop the Output tool's PreSQL/PostSQL,
    which compile_check now names itself (`CAUGHT_AT_COMPILE_TIME`)."""
    repo = _committed_segment(tmp_path, wf_id, seg)
    shutil.copy2(variant, repo.seg(wf_id, seg, "proc.sql"))
    report = cc.compile_check(repo, wf_id, seg)
    if variant.name in CAUGHT_AT_COMPILE_TIME:
        assert report["status"] == "ERROR"
        assert [e for e in report["errors"] if CAUGHT_AT_COMPILE_TIME[variant.name] in e], report["errors"]
        return
    assert report["status"] == "OK", report["errors"]


# --- fix round 1 (I2): MERGE keys compare case-insensitively on both sides, quoted or not ----------
# Contract keys are Alteryx field names verbatim (`UpdateKeys`), so a key may carry a space or mixed
# case; every other stage treats keys case-insensitively (`compare.py`). The message shows each key as
# it has to be written: quoted when it is not a plain identifier.

SPACED = [{"name": "Customer ID", "type": "NUMBER(38,0)", "nullable": True},
          {"name": "NOTE", "type": "VARCHAR", "nullable": True}]


def _spaced_contract(keys: list[str]) -> dict:
    doc = contract("update_insert", keys=keys)
    doc["inputs"][0]["columns"] = SPACED
    doc["outputs"][0]["columns"] = SPACED
    doc["output"] = doc["outputs"][0]
    return doc


def _spaced_merge(on_clause: str) -> str:
    return proc(
        "MERGE INTO IDENTIFIER(:ITEMS_OUT_TGT) AS T\n"
        '  USING (SELECT "Customer ID", NOTE FROM IDENTIFIER(:ITEMS_SRC)) AS S\n'
        f"  {on_clause}\n"
        "  WHEN MATCHED THEN UPDATE SET NOTE = S.NOTE\n"
        '  WHEN NOT MATCHED THEN INSERT ("Customer ID", NOTE) VALUES (S."Customer ID", S.NOTE);')


@pytest.mark.parametrize("on_clause", [
    'ON T."Customer ID" = S."Customer ID"',
    'ON T."customer id" = S."CUSTOMER ID"',
    'ON (S."Customer ID" = T."Customer ID")',
])
def test_a_merge_on_a_quoted_key_with_a_space_is_accepted_in_any_case(tmp_path, on_clause):
    assert write_mode_errors(tmp_path, _spaced_merge(on_clause), _spaced_contract(["Customer ID"])) == []


@pytest.mark.parametrize("on_clause", ['ON T."AcctId" = S."AcctId"', "ON T.AcctId = S.ACCTID", 'ON t.acctid = s."ACCTID"'])
def test_a_merge_on_a_mixed_case_key_is_accepted_quoted_or_not(tmp_path, on_clause):
    sql = _spaced_merge(on_clause).replace('"Customer ID"', "AcctId")
    doc = _spaced_contract(["AcctId"])
    for columns in (doc["inputs"][0]["columns"], doc["outputs"][0]["columns"]):
        columns[0]["name"] = "AcctId"
    assert write_mode_errors(tmp_path, sql, doc) == []


def test_the_message_shows_a_key_that_needs_quoting_quoted(tmp_path):
    errors = write_mode_errors(tmp_path, _spaced_merge("ON T.NOTE = S.NOTE"), _spaced_contract(["Customer ID"]))
    assert errors == ['c4:write_mode: ITEMS_OUT is update_insert: the MERGE INTO IDENTIFIER(:ITEMS_OUT_TGT) must '
                      'match on exactly the contract\'s keys "Customer ID" -- ON <target>.<KEY> = <source>.<KEY> '
                      'for each key, joined by AND, nothing else -- not ON T.NOTE = S.NOTE']


# --- fix round 1 (M1): a missing write mode exits 2 with a message, never a traceback --------------

@pytest.mark.parametrize("write_mode, keys, words", [(None, None, "no write_mode"), ("update_only", None, "update_only"),
                                                      ("update_insert", [], "keys")])
def test_a_contract_error_exits_2_with_a_message_not_a_traceback(tmp_path, capsys, write_mode, keys, words):
    build(tmp_path, proc("ctas"), contract(write_mode, keys=keys))
    assert cc.main([WF, SEG, "--root", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    assert err.startswith(f"cannot compile-check {WF}/{SEG}: c4:write_mode: target ITEMS_OUT"), err
    assert words in err


# --- fix round 1 (N2): two labels that named the wrong thing -----------------------------------------

def test_a_delete_with_only_an_alias_is_a_whole_table_clear(tmp_path):
    sql = proc("DELETE FROM IDENTIFIER(:ITEMS_OUT_TGT) AS T;", "insert")
    assert write_mode_errors(tmp_path, sql, contract("truncate_append")) == []


def test_a_create_like_is_labelled_as_such(tmp_path):
    sql = proc("CREATE TABLE IDENTIFIER(:ITEMS_OUT_TGT) LIKE MIG_WORK.WF0009_SEG_01_OUT;", "insert")
    errors = write_mode_errors(tmp_path, sql, contract("overwrite"))
    assert len(errors) == 1 and errors[0].endswith("not CREATE TABLE … LIKE + INSERT"), errors
