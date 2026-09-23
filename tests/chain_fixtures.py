"""Hand-built chain fixtures for the workflow-level chain test (plan Task W1).

Two tiny SQL workflows in the style of `tests/test_validate_segment.py`'s `build()` (same
manifest, mappings, contract and dag shapes), each with hand-written C4 procedures and golden data,
built so that EVERY segment passes on its own -- fed its golden intermediate by
`validate_segment` -- while the stitched chain does not:

* `build_composition` -- `wf_0008`, three segments in three waves. `seg_01` rounds `PRICE` to cents
  (a human accepted that as a `ROUNDING` difference against the unrounded golden stream), `seg_02`
  scales the price to thousandths (`MILLI`) and `seg_03` copies it to the target `MILLI_OUT`. Alone,
  `seg_02` reads the golden (unrounded) prices and matches; chained, it reads `seg_01`'s rounded
  ones and every `MILLI` is 5 off -- the first divergence is `seg_02`'s own boundary.
* `build_drift` -- `wf_0010`, two segments: the same `seg_01`, then `seg_02` sums the prices into
  the target `TOTALS`. Every boundary is within tolerance (`seg_01`'s accepted rounding), yet the
  total drifts from 3.245 to 3.26: the accumulated rounding only shows at the final output.

The fixtures' premise -- every segment passes alone -- is asserted in `tests/test_validate_workflow.py`,
never assumed. Nothing here has run on Snowflake: the procedures run on the DuckDB double.
"""
from __future__ import annotations

from decimal import Decimal

from lib import typed_csv
from lib.io import read_json, write_json, write_yaml
from lib.paths import Repo

WF_COMPOSITION = "wf_0008"
WF_DRIFT = "wf_0010"

ID = {"name": "ID", "type": "Int64", "size": 8, "scale": None}
PRICE = {"name": "PRICE", "type": "FixedDecimal", "size": 19, "scale": 3}
MILLI = {"name": "MILLI", "type": "FixedDecimal", "size": 19, "scale": 3}
TOTAL = {"name": "TOTAL", "type": "FixedDecimal", "size": 19, "scale": 3}

ITEMS_FIELDS = [ID, PRICE]
MILLI_FIELDS = [ID, MILLI]
TOTAL_FIELDS = [TOTAL]

#: Three prices whose half-cent rounds up: 1.005 -> 1.01, 2.115 -> 2.12, 0.125 -> 0.13 (DuckDB rounds
#: a DECIMAL half away from zero), each 0.005 off -- inside `rounding.abs` 0.01, so compare classifies
#: seg_01's own difference as ROUNDING -- while their sum is 0.015 off, which is not.
ITEMS_ROWS = [[1, Decimal("1.005")], [2, Decimal("2.115")], [3, Decimal("0.125")]]
MILLI_ROWS = [[1, Decimal("1005.000")], [2, Decimal("2115.000")], [3, Decimal("125.000")]]
TOTAL_ROWS = [[Decimal("3.245")]]

ITEMS_COLUMNS = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": False},
                 {"name": "PRICE", "type": "NUMBER(19,3)", "nullable": True}]
MILLI_COLUMNS = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": False},
                 {"name": "MILLI", "type": "NUMBER(19,3)", "nullable": True}]
TOTAL_COLUMNS = [{"name": "TOTAL", "type": "NUMBER(19,3)", "nullable": True}]

SETTINGS = {
    "tolerances": {"float_abs": 1e-6, "float_rel": 1e-9, "timestamp_precision": "milliseconds",
                   "rounding": {"abs": 0.01}},
    "accepted_diff_classes": ["ROUNDING", "ORDERING"],
}

#: The one human approval both fixtures carry: seg_01's rounding of PRICE is accepted.
ACCEPTED_ROUNDING = {"segment": "seg_01", "class": "ROUNDING", "columns": ["PRICE"],
                     "approver": "fixture", "date": "2026-09-22"}

_HEADER = ("CREATE OR REPLACE PROCEDURE MIG_WORK.{wf}_{seg}(SRC_DB STRING, SRC_SCHEMA STRING, "
           "TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)\n"
           "RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS\n$$\nBEGIN\n")
_FOOTER = "  RETURN 'OK';\nEND;\n$$;\n"


def proc(wf: str, seg: str, body: str) -> str:
    """A contract C4 procedure around `body` (one or more `;`-terminated statements)."""
    token = wf.upper().replace("_", "")
    return _HEADER.format(wf=token, seg=seg.upper()) + body + _FOOTER


def seg_01_proc(wf: str, *, rounding: bool = True) -> str:
    formula = "ROUND(PRICE, 2) AS PRICE" if rounding else "PRICE"
    token = wf.upper().replace("_", "")
    return proc(wf, "seg_01", f"""  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.{token}_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data -- logical ITEMS
  t1_input AS (SELECT ID, PRICE FROM IDENTIFIER(:ITEMS_SRC)),
  -- tool 2: Formula -- PRICE to cents
  t2_formula AS (SELECT ID, {formula} FROM t1_input)
  SELECT ID, PRICE FROM t2_formula;
""")


#: seg_02 of the composition fixture: PRICE in thousandths.
MILLI_EXPRESSION = "PRICE * 1000"


def composition_seg_02_proc(expression: str = MILLI_EXPRESSION) -> str:
    return proc(WF_COMPOSITION, "seg_02", f"""  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0008_SEG_02_OUT AS
  -- tool 3: Formula -- MILLI = PRICE in thousandths
  SELECT ID, {expression} AS MILLI FROM MIG_WORK.WF0008_SEG_01_OUT;
""")


COMPOSITION_SEG_03_PROC = proc(WF_COMPOSITION, "seg_03", """  LET MILLI_OUT_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.MILLI_OUT';
  -- tool 9: Output Data (overwrite, logical MILLI_OUT)
  CREATE OR REPLACE TABLE IDENTIFIER(:MILLI_OUT_TGT) AS
  SELECT ID, MILLI FROM MIG_WORK.WF0008_SEG_02_OUT;
""")

DRIFT_SEG_02_PROC = proc(WF_DRIFT, "seg_02", """  LET TOTALS_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.TOTALS';
  -- tool 3: Summarize -- TOTAL = Sum(PRICE); tool 9: Output Data (overwrite, logical TOTALS)
  CREATE OR REPLACE TABLE IDENTIFIER(:TOTALS_TGT) AS
  SELECT SUM(PRICE) AS TOTAL FROM MIG_WORK.WF0010_SEG_01_OUT;
""")


def _contract(seg: str, inputs: list[dict], outputs: list[dict], row_relation: str) -> dict:
    keys = outputs[0].get("keys") or []
    contract = {
        "segment": seg,
        "target": "sql",
        "inputs": inputs,
        "outputs": outputs,
        "row_relation": row_relation,
        "ordering": {"keys": keys, "alteryx_deterministic": True},
        "tolerances": {},
    }
    contract["output"] = outputs[0]
    return contract


def _dag(wf: str, seg: str, nodes: list[tuple[str, str]]) -> dict:
    return {"workflow": wf, "segment": seg,
            "nodes": [{"tool_id": tool_id, "type": kind, "config": {}} for tool_id, kind in nodes],
            "edges": [], "inbound": [], "outbound": []}


def _source_input() -> dict:
    return {"logical": "ITEMS", "tool_id": "1", "columns": ITEMS_COLUMNS, "keys": ["ID"]}


def _seg_01_contract(wf: str) -> dict:
    token = wf.upper().replace("_", "")
    return _contract("seg_01", [_source_input()], [
        {"stream": "2_Output", "kind": "work", "table": f"MIG_WORK.{token}_SEG_01_OUT", "logical": None,
         "columns": ITEMS_COLUMNS, "keys": ["ID"]},
    ], "1:1")


def _common(repo: Repo, wf: str, outputs: dict) -> None:
    write_json(repo.wf(wf, "manifest.json"),
               {"id": wf, "golden_sets": ["normal"], "status": {}, "metrics": {},
                "accepted_diffs": [dict(ACCEPTED_ROUNDING)]})
    write_yaml(repo.global_mappings, SETTINGS)
    write_yaml(repo.wf(wf, "intake", "mappings.yaml"), {
        "sources": {"sales/items.yxdb": {"snowflake": "SALES.RAW.ITEMS", "logical": "ITEMS",
                                         "tool_ids": ["1"], "confirmed_by": "fixture"}},
        "outputs": outputs,
    })
    typed_csv.write_table(repo.wf(wf, "golden", "inputs", "normal", "1.csv"),
                          {"fields": ITEMS_FIELDS, "rows": ITEMS_ROWS})
    # seg_01's golden stream keeps the UNROUNDED prices -- the Alteryx run carried them forward --
    # so the translation's rounding is a ROUNDING difference a human accepted (ACCEPTED_ROUNDING).
    typed_csv.write_table(repo.wf(wf, "golden", "intermediates", "seg_01", "normal", "2_Output.csv"),
                          {"fields": ITEMS_FIELDS, "rows": ITEMS_ROWS})
    write_json(repo.seg(wf, "seg_01", "dag.json"), _dag(wf, "seg_01", [("1", "input"), ("2", "formula")]))
    write_json(repo.seg(wf, "seg_01", "contract.json"), _seg_01_contract(wf))


def write_proc(repo: Repo, wf: str, seg: str, text: str) -> None:
    path = repo.seg(wf, seg, "proc.sql")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_composition(tmp_path, *, rounding: bool = True, seg_02_expression: str = MILLI_EXPRESSION) -> Repo:
    """`wf_0008`: ITEMS -> seg_01 (round PRICE) -> seg_02 (MILLI) -> seg_03 (target MILLI_OUT)."""
    repo = Repo(tmp_path)
    wf = WF_COMPOSITION
    _common(repo, wf, {"out/milli_out.yxdb": {"snowflake": "ANALYTICS.CURATED.MILLI_OUT",
                                              "logical": "MILLI_OUT", "mode": "overwrite",
                                              "keys": ["ID"], "tool_ids": ["9"]}})
    # seg_02's golden stream and the golden target are computed from the unrounded prices.
    typed_csv.write_table(repo.wf(wf, "golden", "intermediates", "seg_02", "normal", "3_Output.csv"),
                          {"fields": MILLI_FIELDS, "rows": MILLI_ROWS})
    typed_csv.write_table(repo.wf(wf, "golden", "outputs", "normal", "9.csv"),
                          {"fields": MILLI_FIELDS, "rows": MILLI_ROWS})
    write_json(repo.wf(wf, "segments", "order.json"), [["seg_01"], ["seg_02"], ["seg_03"]])

    write_json(repo.seg(wf, "seg_02", "dag.json"), _dag(wf, "seg_02", [("3", "formula")]))
    write_json(repo.seg(wf, "seg_02", "contract.json"), _contract("seg_02", [
        {"from": "seg_01", "stream": "2_Output", "table": "MIG_WORK.WF0008_SEG_01_OUT",
         "columns": ITEMS_COLUMNS, "keys": ["ID"]},
    ], [
        {"stream": "3_Output", "kind": "work", "table": "MIG_WORK.WF0008_SEG_02_OUT", "logical": None,
         "columns": MILLI_COLUMNS, "keys": ["ID"]},
    ], "1:1"))
    write_json(repo.seg(wf, "seg_03", "dag.json"), _dag(wf, "seg_03", [("9", "output")]))
    write_json(repo.seg(wf, "seg_03", "contract.json"), _contract("seg_03", [
        {"from": "seg_02", "stream": "3_Output", "table": "MIG_WORK.WF0008_SEG_02_OUT",
         "columns": MILLI_COLUMNS, "keys": ["ID"]},
    ], [
        {"stream": "3_Output", "kind": "target", "tool_id": "9", "logical": "MILLI_OUT", "table": None,
         "write_mode": "overwrite", "columns": MILLI_COLUMNS, "keys": ["ID"]},
    ], "1:1"))

    write_proc(repo, wf, "seg_01", seg_01_proc(wf, rounding=rounding))
    write_proc(repo, wf, "seg_02", composition_seg_02_proc(seg_02_expression))
    write_proc(repo, wf, "seg_03", COMPOSITION_SEG_03_PROC)
    return repo


#: seg_03 of the composition fixture, rewritten to join a table seg_02 NEVER writes: seg_03's
#: contract declares it as a second input from seg_02 (stream `3_Keep`), so validating seg_03 alone
#: loads it from its golden intermediate and passes -- while in the chain nothing ever creates it.
KEEP_FIELDS = [ID]
KEEP_COLUMNS = [{"name": "ID", "type": "NUMBER(38,0)", "nullable": False}]
RAISING_SEG_03_PROC = proc(WF_COMPOSITION, "seg_03", """  LET MILLI_OUT_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.MILLI_OUT';
  -- tool 9: Output Data (overwrite, logical MILLI_OUT) -- only the ids seg_02's 3_Keep stream lists
  CREATE OR REPLACE TABLE IDENTIFIER(:MILLI_OUT_TGT) AS
  SELECT o.ID, o.MILLI FROM MIG_WORK.WF0008_SEG_02_OUT o JOIN MIG_WORK.WF0008_SEG_02_KEEP k ON o.ID = k.ID;
""")


def make_seg_03_read_what_seg_02_never_writes(repo: Repo) -> None:
    wf = WF_COMPOSITION
    contract_path = repo.seg(wf, "seg_03", "contract.json")
    contract = read_json(contract_path)
    contract["inputs"].append({"from": "seg_02", "stream": "3_Keep", "table": "MIG_WORK.WF0008_SEG_02_KEEP",
                               "columns": KEEP_COLUMNS, "keys": ["ID"]})
    write_json(contract_path, contract)
    typed_csv.write_table(repo.wf(wf, "golden", "intermediates", "seg_02", "normal", "3_Keep.csv"),
                          {"fields": KEEP_FIELDS, "rows": [[1], [2], [3]]})
    write_proc(repo, wf, "seg_03", RAISING_SEG_03_PROC)


def build_drift(tmp_path) -> Repo:
    """`wf_0010`: ITEMS -> seg_01 (round PRICE) -> seg_02 (TOTAL = Sum(PRICE), target TOTALS)."""
    repo = Repo(tmp_path)
    wf = WF_DRIFT
    _common(repo, wf, {"out/totals.yxdb": {"snowflake": "ANALYTICS.CURATED.TOTALS", "logical": "TOTALS",
                                           "mode": "overwrite", "keys": [], "tool_ids": ["9"]}})
    typed_csv.write_table(repo.wf(wf, "golden", "outputs", "normal", "9.csv"),
                          {"fields": TOTAL_FIELDS, "rows": TOTAL_ROWS})
    write_json(repo.wf(wf, "segments", "order.json"), [["seg_01"], ["seg_02"]])
    write_json(repo.seg(wf, "seg_02", "dag.json"), _dag(wf, "seg_02", [("3", "summarize"), ("9", "output")]))
    write_json(repo.seg(wf, "seg_02", "contract.json"), _contract("seg_02", [
        {"from": "seg_01", "stream": "2_Output", "table": "MIG_WORK.WF0010_SEG_01_OUT",
         "columns": ITEMS_COLUMNS, "keys": ["ID"]},
    ], [
        {"stream": "3_Output", "kind": "target", "tool_id": "9", "logical": "TOTALS", "table": None,
         "write_mode": "overwrite", "columns": TOTAL_COLUMNS, "keys": []},
    ], "aggregate"))
    write_proc(repo, wf, "seg_01", seg_01_proc(wf))
    write_proc(repo, wf, "seg_02", DRIFT_SEG_02_PROC)
    return repo


# --- fix round 1: order dependence (I2) and a keyed drift (M5) --------------------------------------

#: The golden output of a Sample tool that keeps "the first 2 records" -- in Alteryx's own record
#: order, which is ITEMS' file order.
FIRST_TWO_MILLI_ROWS = MILLI_ROWS[:2]

SAMPLE_SEG_02_PROC = proc(WF_COMPOSITION, "seg_02", """  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0008_SEG_02_OUT AS
  -- tool 3: Sample -- the first 2 records, in whatever order the input arrives (order-dependent)
  SELECT ID, PRICE * 1000 AS MILLI FROM MIG_WORK.WF0008_SEG_01_OUT LIMIT 2;
""")

SAMPLE_SEG_02_PY = '''# tool 3: Sample -- the first 2 records, in whatever order the input arrives (order-dependent)
from snowflake.snowpark.types import DecimalType, LongType, StructField, StructType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    rows = session.table("MIG_WORK.WF0008_SEG_01_OUT").limit(2).collect()
    out = [[int(r["ID"]), r["PRICE"] * 1000] for r in rows]
    schema = StructType([StructField("ID", LongType()), StructField("MILLI", DecimalType(19, 3))])
    session.create_dataframe(out, schema=schema).write.mode("overwrite").save_as_table("MIG_WORK.WF0008_SEG_02_OUT")
    return "OK"
'''


def build_order_dependent(tmp_path, *, snowpark: bool = False, upstream_order_by: str | None = None) -> Repo:
    """`wf_0008` with `seg_02` as a Sample tool: the first two records of whatever order its input
    arrives in. Snowflake promises no row order for a table, so the migrated procedure is
    non-deterministic in production even when every local run happens to agree. Alone it PASSes
    (its golden intermediate arrives in file order); `upstream_order_by` makes `seg_01` end with
    that `ORDER BY` (the physical order the chain then hands on), `snowpark` writes `seg_02` as a
    Snowpark procedure instead of SQL."""
    repo = build_composition(tmp_path, rounding=False)
    wf = WF_COMPOSITION
    if upstream_order_by:
        path = repo.seg(wf, "seg_01", "proc.sql")
        text = path.read_text(encoding="utf-8").replace(
            "  SELECT ID, PRICE FROM t2_formula;", f"  SELECT ID, PRICE FROM t2_formula ORDER BY {upstream_order_by};")
        path.write_text(text, encoding="utf-8", newline="\n")
    for path in (repo.wf(wf, "golden", "intermediates", "seg_02", "normal", "3_Output.csv"),
                 repo.wf(wf, "golden", "outputs", "normal", "9.csv")):
        typed_csv.write_table(path, {"fields": MILLI_FIELDS, "rows": FIRST_TWO_MILLI_ROWS})
    contract_path = repo.seg(wf, "seg_02", "contract.json")
    contract = read_json(contract_path)
    contract["row_relation"] = "filter"
    if snowpark:
        contract["target"] = "snowpark"
        repo.seg(wf, "seg_02", "proc.sql").unlink()
        repo.seg(wf, "seg_02", "proc.py").write_text(SAMPLE_SEG_02_PY, encoding="utf-8", newline="\n")
    else:
        write_proc(repo, wf, "seg_02", SAMPLE_SEG_02_PROC)
    write_json(contract_path, contract)
    return repo


DOUBLE = {"type": "Double", "size": 8, "scale": None}


def build_keyed_drift(tmp_path, *, rows: int = 3, jitter: str = "0.0000009") -> Repo:
    """`wf_0010` in doubles, with a KEYED final: `seg_01` adds `jitter` to every PRICE (inside
    `float_abs` 1e-6 per row, so its boundary PASSes with no approval at all) and `seg_02` writes
    `TOTALS (K, TOTAL = Sum(PRICE))` keyed on `K`. With 3 rows the total is 2.7e-6 off -- past
    `float_abs` -- so the keyed final FAILs while every boundary passed: `chain_drift`. With 1 row it
    stays inside tolerance and the chain PASSes. The pair pins that boundaries and finals are judged
    with one tolerance, on the keyed path as well as the keyless one."""
    repo = build_drift(tmp_path)
    wf = WF_DRIFT
    manifest = read_json(repo.wf(wf, "manifest.json"))
    manifest["accepted_diffs"] = []
    write_json(repo.wf(wf, "manifest.json"), manifest)
    items = [ID, {"name": "PRICE", **DOUBLE}]
    data = [[i + 1, 1.25 * (i + 1)] for i in range(rows)]
    typed_csv.write_table(repo.wf(wf, "golden", "inputs", "normal", "1.csv"), {"fields": items, "rows": data})
    typed_csv.write_table(repo.wf(wf, "golden", "intermediates", "seg_01", "normal", "2_Output.csv"),
                          {"fields": items, "rows": data})
    typed_csv.write_table(repo.wf(wf, "golden", "outputs", "normal", "9.csv"),
                          {"fields": [{"name": "K", "type": "Int64", "size": 8, "scale": None}, {"name": "TOTAL", **DOUBLE}],
                           "rows": [[1, sum(row[1] for row in data)]]})
    float_items = [ITEMS_COLUMNS[0], {"name": "PRICE", "type": "FLOAT", "nullable": True}]
    keyed_total = [{"name": "K", "type": "NUMBER(38,0)", "nullable": False},
                   {"name": "TOTAL", "type": "FLOAT", "nullable": True}]
    seg_01 = read_json(repo.seg(wf, "seg_01", "contract.json"))
    seg_01["inputs"][0]["columns"] = float_items
    seg_01["outputs"][0]["columns"] = float_items
    seg_01["output"] = seg_01["outputs"][0]
    write_json(repo.seg(wf, "seg_01", "contract.json"), seg_01)
    seg_02 = read_json(repo.seg(wf, "seg_02", "contract.json"))
    seg_02["inputs"][0]["columns"] = float_items
    seg_02["outputs"][0]["columns"] = keyed_total
    seg_02["outputs"][0]["keys"] = ["K"]
    seg_02["output"] = seg_02["outputs"][0]
    seg_02["ordering"]["keys"] = ["K"]
    write_json(repo.seg(wf, "seg_02", "contract.json"), seg_02)
    write_proc(repo, wf, "seg_01", seg_01_proc(wf, rounding=False).replace(
        "t2_formula AS (SELECT ID, PRICE FROM t1_input)", f"t2_formula AS (SELECT ID, PRICE + {jitter} AS PRICE FROM t1_input)"))
    write_proc(repo, wf, "seg_02", DRIFT_SEG_02_PROC.replace("SELECT SUM(PRICE) AS TOTAL", "SELECT 1 AS K, SUM(PRICE) AS TOTAL"))
    return repo


# --- fix round 3: partial reorders (R1) and a view stream (R2) ---------------------------------------

#: Four items whose PRICE ties in pairs: {1, 3} at 1.000 and {2, 4} at 2.000.
ITEMS_WITH_TIES = [[1, Decimal("1.000")], [2, Decimal("2.000")], [3, Decimal("1.000")], [4, Decimal("2.000")]]
#: A Sort on the non-unique PRICE: ties keep whatever order they arrived in.
TIE_SORT_SELECT = "SELECT ID, PRICE FROM t2_formula ORDER BY PRICE"
#: A Filter's True and False branches unioned back: each block keeps its arrival order.
FILTER_UNION_SELECT = ("SELECT ID, PRICE FROM t2_formula WHERE PRICE < 1.5 "
                       "UNION ALL SELECT ID, PRICE FROM t2_formula WHERE PRICE >= 1.5")


def build_partial_reorder(tmp_path, *, seg_01_select: str, snowpark: bool = False) -> Repo:
    """`wf_0008` on `ITEMS_WITH_TIES`: `seg_01` reorders its input only PARTLY (a sort on a
    non-unique key, or a Filter's branches unioned back) -- 1, 3, 2, 4 in file order, which is also
    Alteryx's order, so the golden stream is written in that order -- and `seg_02` keeps the first
    two records of whatever arrives (`LIMIT 2`, or `.limit(2)` in Snowpark): IDs 1 and 3. Every
    segment PASSes alone and the judged chain run matches; only a re-run that presents `seg_02` a
    different order shows the dependence. Handed the input reversed, `seg_01`'s output is NOT the
    exact reverse of the first run's (3, 1, 4, 2), which is what a rule of "reverse only if the order
    is unchanged" missed."""
    repo = build_order_dependent(tmp_path, snowpark=snowpark)
    wf = WF_COMPOSITION
    typed_csv.write_table(repo.wf(wf, "golden", "inputs", "normal", "1.csv"),
                          {"fields": ITEMS_FIELDS, "rows": ITEMS_WITH_TIES})
    sorted_rows = [row for row in ITEMS_WITH_TIES if row[1] < 1.5] + [row for row in ITEMS_WITH_TIES if row[1] >= 1.5]
    typed_csv.write_table(repo.wf(wf, "golden", "intermediates", "seg_01", "normal", "2_Output.csv"),
                          {"fields": ITEMS_FIELDS, "rows": sorted_rows})
    kept = [[row[0], row[1] * 1000] for row in sorted_rows[:2]]
    for path in (repo.wf(wf, "golden", "intermediates", "seg_02", "normal", "3_Output.csv"),
                 repo.wf(wf, "golden", "outputs", "normal", "9.csv")):
        typed_csv.write_table(path, {"fields": MILLI_FIELDS, "rows": kept})
    path = repo.seg(wf, "seg_01", "proc.sql")
    text = path.read_text(encoding="utf-8")
    assert "  SELECT ID, PRICE FROM t2_formula;" in text
    path.write_text(text.replace("  SELECT ID, PRICE FROM t2_formula;", f"  {seg_01_select};"),
                    encoding="utf-8", newline="\n")
    return repo


def make_seg_01_write_a_sorted_view(repo: Repo) -> None:
    """A correct `seg_01` that writes its stream as a VIEW with its own ORDER BY (off the C3 CTAS
    convention, but legal SQL): the chain's re-run must reverse what a view presents without
    crashing on it."""
    write_proc(repo, WF_COMPOSITION, "seg_01", proc(WF_COMPOSITION, "seg_01", """  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';
  -- tool 1: Input Data -- logical ITEMS; tool 2: Formula -- PRICE as is, as a sorted view
  CREATE OR REPLACE VIEW MIG_WORK.WF0008_SEG_01_OUT AS
  SELECT ID, PRICE FROM IDENTIFIER(:ITEMS_SRC) ORDER BY PRICE;
"""))
