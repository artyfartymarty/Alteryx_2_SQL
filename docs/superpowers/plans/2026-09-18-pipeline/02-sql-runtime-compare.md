# Phase 02 — SQL runtime and compare.py

Read [00-index.md](00-index.md) first. Contracts C1–C5 matter most here.

---

### Task 6: SQL runtime

**Files:** Create `scripts/lib/types_map.py`, `scripts/lib/backend.py`, `scripts/lib/proc_runner.py`, `scripts/load_golden.py`, `scripts/gen_source_views.py`, `scripts/compile_check.py`; Tests `tests/test_backend.py`, `tests/test_proc_runner.py`, `tests/test_load_golden.py`, `tests/test_compile_check.py`.

**Interfaces — Consumes:** `lib.paths`, `lib.io`, `lib.typed_csv`. **Produces:**
```python
# scripts/lib/types_map.py
def alteryx_to_snowflake(field: dict) -> str   # Int*/Byte→NUMBER(38,0); FixedDecimal→NUMBER(size,scale); Float/Double→FLOAT;
                                               # String/WString→VARCHAR(size); V_String/V_WString→VARCHAR; Bool→BOOLEAN;
                                               # Date→DATE; Time→TIME; DateTime→TIMESTAMP_NTZ; Blob→BINARY
def type_family(sql_type: str) -> str          # "number" | "float" | "string" | "date" | "timestamp" | "time" | "bool" | "binary" | "other"

# scripts/lib/backend.py
SANDBOX_DB = "MIGDB"
def local_name(fqn: str) -> tuple[str, str]    # "A.B.C"→("A__B","C"); "B.C"→("B","C"); "C"→("main","C"); strips double quotes
class BackendError(Exception)
class DuckDBBackend:
    def __init__(self, db_path: str = ":memory:")
    def translate(self, sql: str) -> str                                   # one Snowflake statement → one DuckDB statement
    def execute(self, sql: str) -> None                                    # Snowflake dialect in; creates missing schemas for CREATE targets
    def query(self, sql: str) -> tuple[list[str], list[tuple]]
    def load_table(self, fqn: str, table: dict) -> None                    # typed_csv Table → CREATE OR REPLACE TABLE + rows
    def create_view(self, view_fqn: str, target_fqn: str) -> None
    def table_exists(self, fqn: str) -> bool
    def table_columns(self, fqn: str) -> list[dict]                        # [{"name", "type", "nullable"}], type upper-cased
    def close(self) -> None
class SnowflakeBackend: ...   # same methods; imports snowflake.connector lazily; raises BackendError("snowflake-connector-python is not installed") if absent
def get_backend(kind: str = "duckdb", **kwargs)

# scripts/lib/proc_runner.py
@dataclass
class ProcInfo: name: str; params: list[str]; execute_as: str; statements: list[str]; session: dict[str, str]
class ProcError(Exception)
def parse_proc(sql_text: str) -> ProcInfo
def bind(statement: str, args: dict[str, str]) -> str      # substitutes :PARAM outside quotes/comments; folds IDENTIFIER('a' || '.' || 'b') → a.b
def run_proc(backend, sql_text: str, args: dict[str, str]) -> ProcInfo

# scripts/load_golden.py
def load_set(backend, repo: Repo, wf_id: str, golden_set: str) -> dict
    # returns {"args": {"SRC_DB": SANDBOX_DB, "SRC_SCHEMA": "MIG_GOLDEN_WF0001_NORMAL", "TGT_DB": SANDBOX_DB, "TGT_SCHEMA": "MIG_WORK"}, "loaded": [fqn…]}
def load_intermediate(backend, repo, wf_id, seg, golden_set, stream, table_fqn) -> None
# scripts/gen_source_views.py
def production_views_sql(wf_id: str, mappings: dict, program: dict) -> str     # CREATE SCHEMA/VIEW text for <target_database>.MIG_SRC_<WF>
# scripts/compile_check.py
def compile_check(repo: Repo, wf_id: str, seg: str) -> dict      # {"status": "OK"|"ERROR", "errors": [str], "statements": int}
```

**`DuckDBBackend.translate`.** Parse with `sqlglot.parse_one(sql, read="snowflake")`. Walk `exp.Table` nodes: when `catalog` is set, replace `db` with `<catalog>__<db>` and clear `catalog`; never touch a table whose name matches a CTE alias in scope. Remove the `TRANSIENT` property from `exp.Create`. Generate with `dialect="duckdb"`. Raise `BackendError` carrying the original statement on any sqlglot error. `execute` runs `CREATE SCHEMA IF NOT EXISTS` for the schema of any `CREATE … TABLE/VIEW` or `MERGE` target first.

**`parse_proc`.** Regex the header for the name, the parameter list and `EXECUTE AS (CALLER|OWNER)`; take the text between the first and last `$$`. Split statements on `;` outside single quotes, double quotes, `--` and `/* */` comments. Drop a leading `BEGIN` and trailing `END`. `RETURN …` is skipped. `ALTER SESSION SET k = v, …` is parsed into `session` and skipped. A statement starting with `LET`, `DECLARE`, `IF`, `FOR`, `WHILE`, `EXECUTE IMMEDIATE` or `CALL` raises `ProcError("outside the supported procedure subset (plan contract C4): …")`. Comments stay attached to the statement they precede.

**`bind`.** Parameter names are case-insensitive. `:SRC_DB` becomes `'MIGDB'` (a SQL string literal, quotes doubled). Then every `IDENTIFIER(<expr>)` whose `<expr>` is string literals joined by `||` is replaced by the concatenated text, unquoted. Any other `IDENTIFIER(` form raises `ProcError`.

**`load_set`.** Reads `workflows/<wf>/intake/mappings.yaml` for `logical` names by `tool_ids`. Loads each `golden/inputs/<set>/<tool_id>.csv` to `MIG_GOLDEN.<WF>_<SET>_IN_<toolid>`, creates view `MIGDB.MIG_GOLDEN_<WF>_<SET>.<LOGICAL>` over it, and loads each `golden/targets_before/<set>/<LOGICAL>.csv` to `MIGDB.MIG_WORK.<LOGICAL>`. A source without a `logical` name raises `ValueError` naming the tool id.

**`compile_check`.** (1) `parse_proc`; (2) every statement must parse as Snowflake with no `exp.Command` node; (3) in a fresh in-memory backend create empty tables for each `contract.inputs[]` (mapped sources as `MIGDB.MIG_COMPILE.<logical>`, upstream segments at their literal `table`) and each `outputs[]` of kind `target` (as `MIGDB.MIG_WORK.<logical>`), using `columns[].type`; (4) `run_proc` with `SRC_SCHEMA="MIG_COMPILE"`; (5) every `outputs[]` of kind `work` must exist with column names equal to the contract's, case-insensitively and in order. Writes `segments/<seg>/compile_check.json`. CLI `python scripts/compile_check.py <wf> <seg> [--root .]`, exit 0 on `OK`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backend.py
import pytest
from lib.backend import DuckDBBackend, BackendError, local_name, get_backend
from lib.types_map import alteryx_to_snowflake, type_family

T = {"fields": [{"name": "ACCT", "type": "V_String", "size": 20, "scale": None},
                {"name": "AMT", "type": "FixedDecimal", "size": 19, "scale": 2},
                {"name": "D", "type": "Date", "size": 10, "scale": None}],
     "rows": [["A1", __import__("decimal").Decimal("10.50"), "2024-02-29"], ["A2", None, None]]}

def test_names(): assert local_name("FIN.RAW.GL") == ("FIN__RAW", "GL") and local_name('MIG_WORK."T"') == ("MIG_WORK", "T")
def test_types():
    assert alteryx_to_snowflake(T["fields"][1]) == "NUMBER(19,2)" and alteryx_to_snowflake({"type": "String", "size": 10}) == "VARCHAR(10)"
    assert type_family("NUMBER(19,2)") == "number" and type_family("TIMESTAMP_NTZ") == "timestamp" and type_family("VARCHAR(10)") == "string"

def test_three_part_names_transient_and_snowflake_functions():
    b = DuckDBBackend()
    b.load_table("MIGDB.MIG_GOLDEN.X", T)
    b.execute("""CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.OUT AS
                 WITH t1_input AS (SELECT ACCT, AMT, D FROM MIGDB.MIG_GOLDEN.X)
                 SELECT ACCT, IFF(AMT IS NULL, 0, AMT) AS AMT, TRY_TO_NUMBER('abc') AS BAD, DATEADD(day, 1, D) AS NEXT_D
                 FROM t1_input QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCT ORDER BY AMT) = 1""")
    cols, rows = b.query("SELECT ACCT, AMT, BAD, NEXT_D FROM MIG_WORK.OUT ORDER BY ACCT")
    assert [c.upper() for c in cols] == ["ACCT", "AMT", "BAD", "NEXT_D"]
    assert rows[0][0] == "A1" and float(rows[0][1]) == 10.5 and rows[0][2] is None and str(rows[0][3])[:10] == "2024-03-01"
    assert float(rows[1][1]) == 0 and [c["name"].upper() for c in b.table_columns("MIG_WORK.OUT")] == cols and b.table_exists("MIG_WORK.OUT")

def test_merge_runs():
    b = DuckDBBackend(); b.load_table("MIGDB.MIG_WORK.TGT", T)
    b.load_table("MIG_WORK.SRC", {"fields": T["fields"], "rows": [["A2", 5, "2026-01-01"], ["A3", 7, None]]})
    b.execute("""MERGE INTO MIGDB.MIG_WORK.TGT t USING MIG_WORK.SRC s ON t.ACCT = s.ACCT
                 WHEN MATCHED THEN UPDATE SET AMT = s.AMT, D = s.D WHEN NOT MATCHED THEN INSERT (ACCT, AMT, D) VALUES (s.ACCT, s.AMT, s.D)""")
    assert b.query("SELECT COUNT(*), SUM(AMT) FROM MIGDB.MIG_WORK.TGT")[1][0] == (3, __import__("decimal").Decimal("22.50"))

def test_bad_sql_names_the_statement():
    with pytest.raises(BackendError, match="SELEC"): DuckDBBackend().execute("SELEC 1")
def test_snowflake_backend_fails_clearly_without_connector():
    with pytest.raises(BackendError, match="snowflake-connector-python"): get_backend("snowflake")
```
```python
# tests/test_proc_runner.py
import pytest
from lib.backend import DuckDBBackend
from lib.proc_runner import parse_proc, bind, run_proc, ProcError

PROC = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0009_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0009_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data; note the semicolon in this comment; and 'a quote
  t1_input AS (SELECT ID, NOTE FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ITEMS')),
  -- tool 2: Filter, True branch
  t2_filter AS (SELECT * FROM t1_input WHERE NOTE <> 'x;y' AND NOTE <> ':SRC_DB')
  SELECT ID, NOTE FROM t2_filter;
  INSERT INTO IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.ITEMS_OUT') SELECT ID, NOTE FROM MIG_WORK.WF0009_SEG_01_OUT;
  RETURN 'OK';
END;
$$;"""
ARGS = {"SRC_DB": "MIGDB", "SRC_SCHEMA": "MIG_GOLDEN_WF0009_NORMAL", "TGT_DB": "MIGDB", "TGT_SCHEMA": "MIG_WORK", "RUN_ID": "r1"}
F = [{"name": "ID", "type": "Int32", "size": 4, "scale": None}, {"name": "NOTE", "type": "V_String", "size": 20, "scale": None}]

def test_parse_proc():
    p = parse_proc(PROC)
    assert p.name == "MIG_WORK.WF0009_SEG_01" and p.params == ["SRC_DB", "SRC_SCHEMA", "TGT_DB", "TGT_SCHEMA", "RUN_ID"]
    assert p.execute_as == "CALLER" and p.session == {"TIMEZONE": "America/New_York", "WEEK_START": "1"} and len(p.statements) == 2

def test_bind_leaves_string_literals_alone():
    out = bind("SELECT ':SRC_DB' AS s FROM IDENTIFIER(:src_db || '.' || :SRC_SCHEMA || '.T')", ARGS)
    assert "':SRC_DB'" in out and "MIGDB.MIG_GOLDEN_WF0009_NORMAL.T" in out and "IDENTIFIER" not in out

def test_run_proc_end_to_end():
    b = DuckDBBackend()
    b.load_table("MIG_GOLDEN.WF0009_NORMAL_IN_1", {"fields": F, "rows": [[1, "keep"], [2, "x;y"], [3, None]]})
    b.create_view("MIGDB.MIG_GOLDEN_WF0009_NORMAL.ITEMS", "MIG_GOLDEN.WF0009_NORMAL_IN_1")
    b.load_table("MIGDB.MIG_WORK.ITEMS_OUT", {"fields": F, "rows": []})
    run_proc(b, PROC, ARGS)
    assert b.query("SELECT ID FROM MIGDB.MIG_WORK.ITEMS_OUT")[1] == [(1,)]

@pytest.mark.parametrize("stmt", ["LET x := 1", "EXECUTE IMMEDIATE 'select 1'", "FOR r IN c DO NULL; END FOR"])
def test_scripting_outside_the_subset_is_rejected(stmt):
    with pytest.raises(ProcError, match="supported procedure subset"): parse_proc(PROC.replace("RETURN 'OK';", stmt + ";\n  RETURN 'OK';"))
```
`tests/test_load_golden.py` builds a temp workflow (one input CSV, a `mappings.yaml` with `logical: ITEMS`, one `targets_before` table), calls `load_set`, and asserts the three tables/views exist and the returned dict; a second test removes `logical` and expects `ValueError` naming the tool id. `tests/test_compile_check.py` writes `PROC` with a matching contract and expects `OK`; then (a) renames an output column in the contract, (b) misspells `SELECT`, (c) references an unmapped table — each must give `ERROR` with a message naming the cause, and the CLI must exit 1.

- [ ] **Step 2:** Run the four files. Expected: FAIL on import.
- [ ] **Step 3:** Implement. If a Snowflake construct will not transpile, fix it in `translate` with an AST transform and add a test; do not rewrite the sample SQL into DuckDB dialect.
- [ ] **Step 4:** Run. Expected: all pass.
- [ ] **Step 5:** Commit `feat: DuckDB-backed Snowflake runtime, procedure runner, golden loader, compile check`.

---

### Task 7: `scripts/compare.py`

**Files:** Create `scripts/compare.py`; Test `tests/test_compare.py`.

**Interfaces — Consumes:** `DuckDBBackend`, `type_family`, `typed_csv.read_table`. **Produces:**
```python
def compare(backend, expected: str, actual: str, contract: dict, tolerances: dict, *, output: dict | None = None,
            accepted_classes: Sequence[str] = (), approvals: Sequence[dict] = (), segment_dag: dict | None = None,
            golden_set: str | None = None, sample_rows: int = 5, max_diff_rows: int = 10000) -> dict
```
`output` selects one entry of `contract["outputs"]` (default `contract["output"]`); its `columns` and `keys` drive the comparison. CLI per program spec §9.2: `--expected <csv|table> --actual <table> --contract contract.json --out validation.json [--stream 31_U] [--tolerances mappings/global.yaml] [--manifest manifest.json] [--dag segments/seg_NN/dag.json] [--db workflows/<wf>/.sandbox.duckdb] [--golden-set normal] [--sample-rows 5]`. A `.csv` expected file is loaded to `MIG_COMPARE.EXPECTED`. Exit 0 for `PASS`/`PASS_WITH_ACCEPTED_DIFF`, 1 for `FAIL`, 2 for errors.

**Checks, in order** (only a schema failure stops early):
1. **Schema.** Column names compared upper-cased (the `sanitize` policy) and in order; type *family* per column; extra or missing columns listed. Failure → cluster `TYPE`, verdict `FAIL`, remaining checks `"SKIPPED"`.
2. **Counts.** Rows; per column NULL count and distinct count, computed in SQL.
3. **Aggregates.** Numeric columns: sum, min, max, avg with tolerance. String columns: min and max length.
4. **Keyed diff** when `keys` is non-empty. First check key uniqueness on both sides: duplicates in expected → cluster `GOLDEN_DATA` and `needs_human: true`; duplicates only in actual → cluster `LOGIC` with note `duplicate keys in actual`. Then `only_expected` and `only_actual` via anti-joins on the keys, and mismatching rows via a join where any non-key column `IS DISTINCT FROM`, pulled up to `max_diff_rows` (`truncated: true` beyond that).
5. **Row multiset** when there are no keys: group both sides by all columns with counts, report `only_expected`/`only_actual` totals. *Amended by task 7b:* the surplus rows behind those totals are also pulled (bounded by `max_diff_rows` per side, `truncated: true` beyond that) and paired by nearest match — each surplus expected row takes the unused surplus actual row it disagrees with in the fewest columns, accepted only when at least half of the columns are equal (`differing <= columns // 2`; a one-column stream never pairs). A pair that agrees everywhere is two copies of one row within tolerance: it leaves both surplus sides, reduces the `set_diff` totals and is reported in `normalizations_applied`. A pair that disagrees is classified exactly as a keyed mismatch is, and its cluster carries `"paired_by": "nearest_match"`; a row no pair claimed becomes a row-presence cluster.
6. **Tolerances**, applied in Python to the pulled rows: floats pass when `abs(e-a) <= max(float_abs, float_rel * max(abs(e), abs(a)))`; `contract.tolerances[COL].float_abs` overrides and also applies to `number` columns; timestamps truncate to `timestamp_precision`. String normalizations run only if listed in `contract["normalizations"]` (`"trim:COL"`, `"upper:COL"`), and every one applied is reported in `normalizations_applied`.

**Cluster classification** (first matching rule per mismatching column, evaluated over all of that column's mismatches; row-presence clusters use the last two rules):

| Rule | Class |
|------|-------|
| mismatching columns ⊆ `contract.ordering.order_dependent_columns` and the multiset of those columns' values is equal on both sides | `ORDERING` |
| one side NULL and the other not, in every mismatch | `NULL_SEMANTICS` |
| numeric, outside float tolerance, and `abs(e-a) <= tolerances.rounding.abs` (default 0.01) in every mismatch | `ROUNDING` |
| string, and one value is a proper prefix of the other in every mismatch | `TRUNCATION` |
| string vs. number of equal value, or date vs. timestamp at midnight | `TYPE` |
| anything else | `LOGIC` (add `hint: "whitespace_only"` or `"case_only"` when that is the entire difference) |
| rows only on one side, and some non-key column is NULL in all of them while under 100% NULL overall in expected | `NULL_SEMANTICS` on those columns |
| rows only on one side otherwise | `LOGIC` (with no keys too, since task 7b; `UNKNOWN` is left for a difference nothing could classify, such as a pull cut short at `max_diff_rows`) |

Each cluster: `{"class", "columns", "count", "example_rows": [{"key", "expected", "actual"}…] (at most sample_rows), "suspect_cte", "hint"?}`. `suspect_cte` is filled only when `segment_dag` is given: for a column cluster, the last node in topological order whose config writes that column (formula field, summarize `rename`, select `rename`, multi-row `field`, record id `field`, regex `output_fields`/`field`, datetime `out_field`, cross-tab header, transpose `Name`/`Value`); for a row-presence cluster, the last `filter`, `join`, `unique` or `sample` node. Format `t<id>_<type>`; otherwise `null`.

**Verdict.** No clusters → `PASS`. All clusters in `accepted_classes` and each matched by an approval with the same `segment`, `class` and a superset of `columns` → `PASS_WITH_ACCEPTED_DIFF`. Otherwise `FAIL`. *Amended by task 7b fix round 1:* only a cluster whose `scope` is `"columns"` and which names at least one column can be matched by an approval at all. An approval record pins columns and never pins *which* rows, so rows present on one side only, a duplicated key, a schema failure and a synthetic cluster can never reach `PASS_WITH_ACCEPTED_DIFF` on either path, whatever `accepted_diff_classes` holds. Report fields follow the program schema's `validation.json`, plus `checks.column_mismatches` and `truncated`. `runtime_ms` is measured; `credits` is `null` locally; `idempotent` is `null` here (Task 14 fills it).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compare.py
import copy, json, subprocess, sys
from decimal import Decimal
from pathlib import Path
from lib.backend import DuckDBBackend
from lib import typed_csv
import compare as cmp

FIELDS = [{"name": "ACCT", "type": "V_String", "size": 20, "scale": None}, {"name": "PERIOD", "type": "V_String", "size": 7, "scale": None},
          {"name": "REGION", "type": "V_String", "size": 10, "scale": None}, {"name": "AMOUNT", "type": "Double", "size": 8, "scale": None},
          {"name": "NAME", "type": "V_String", "size": 50, "scale": None}, {"name": "RID", "type": "Int32", "size": 4, "scale": None}]
ROWS = [["A1", "2026-08", "EMEA", 100.0, "Alexander", 1], ["A2", "2026-08", None, 250.005, "Bo", 2], ["A3", "2026-08", "EMEA", -0.0, "Chandrasekhar", 3]]
CONTRACT = {"segment": "seg_01", "output": {"table": "MIG_WORK.ACT", "stream": "9_Output", "kind": "work", "keys": ["ACCT", "PERIOD"],
            "columns": [{"name": f["name"], "type": "FLOAT" if f["type"] == "Double" else "NUMBER(38,0)" if f["type"] == "Int32" else "VARCHAR", "nullable": True} for f in FIELDS]},
            "ordering": {"keys": ["ACCT"], "alteryx_deterministic": True, "order_dependent_columns": ["RID"]}, "tolerances": {}}
TOL = {"float_abs": 1e-6, "float_rel": 1e-9, "timestamp_precision": "milliseconds", "rounding": {"abs": 0.01}}

def run(actual_rows, contract=CONTRACT, fields=FIELDS, **kw):
    b = DuckDBBackend()
    b.load_table("MIG_COMPARE.EXP", {"fields": FIELDS, "rows": ROWS}); b.load_table("MIG_WORK.ACT", {"fields": fields, "rows": actual_rows})
    return cmp.compare(b, "MIG_COMPARE.EXP", "MIG_WORK.ACT", contract, TOL, **kw)
def edit(i, col, value):
    rows = copy.deepcopy(ROWS); rows[i][[f["name"] for f in FIELDS].index(col)] = value; return rows

def test_identical_passes():
    r = run(ROWS)
    assert r["verdict"] == "PASS" and r["diff_clusters"] == [] and r["checks"]["counts"] == {"expected": 3, "actual": 3, "verdict": "PASS"}
def test_float_noise_inside_tolerance_passes(): assert run(edit(0, "AMOUNT", 100.0000000001))["verdict"] == "PASS"
def test_rounding():
    r = run(edit(1, "AMOUNT", 250.01)); c = r["diff_clusters"][0]
    assert r["verdict"] == "FAIL" and (c["class"], c["columns"], c["count"]) == ("ROUNDING", ["AMOUNT"], 1)
    assert c["example_rows"][0]["key"] == {"ACCT": "A2", "PERIOD": "2026-08"}
def test_rounding_can_be_accepted_with_an_approval():
    appr = [{"segment": "seg_01", "class": "ROUNDING", "columns": ["AMOUNT"], "approver": "wf_owner", "date": "2026-09-18"}]
    assert run(edit(1, "AMOUNT", 250.01), accepted_classes=["ROUNDING"], approvals=appr)["verdict"] == "PASS_WITH_ACCEPTED_DIFF"
    assert run(edit(1, "AMOUNT", 250.01), accepted_classes=["ROUNDING"])["verdict"] == "FAIL"          # class accepted, nobody signed
def test_logic(): assert run(edit(0, "AMOUNT", 175.0))["diff_clusters"][0]["class"] == "LOGIC"
def test_truncation(): assert run(edit(2, "NAME", "Chandrasek"))["diff_clusters"][0]["class"] == "TRUNCATION"
def test_null_semantics_value(): assert run(edit(0, "REGION", None))["diff_clusters"][0]["class"] == "NULL_SEMANTICS"
def test_null_semantics_missing_rows():
    r = run([x for x in ROWS if x[2] is not None]); c = r["diff_clusters"][0]
    assert r["checks"]["set_diff"] == {"only_expected": 1, "only_actual": 0} and (c["class"], c["columns"]) == ("NULL_SEMANTICS", ["REGION"])
def test_ordering():
    rows = copy.deepcopy(ROWS); rows[0][5], rows[1][5] = 2, 1
    assert run(rows)["diff_clusters"][0]["class"] == "ORDERING"
def test_whitespace_hint():
    c = run(edit(1, "NAME", "Bo "))["diff_clusters"][0]; assert c["class"] == "LOGIC" and c["hint"] == "whitespace_only"
def test_schema_failure_stops_early():
    fields = [dict(f, type="V_String") if f["name"] == "AMOUNT" else f for f in FIELDS]
    r = run([[*x[:3], str(x[3]), *x[4:]] for x in ROWS], fields=fields)
    assert r["checks"]["schema"] == "FAIL" and r["checks"]["counts"] == "SKIPPED" and r["diff_clusters"][0]["class"] == "TYPE"
def test_duplicate_keys_in_expected_need_a_human():
    b = DuckDBBackend(); b.load_table("MIG_COMPARE.EXP", {"fields": FIELDS, "rows": ROWS + [ROWS[0]]}); b.load_table("MIG_WORK.ACT", {"fields": FIELDS, "rows": ROWS})
    r = cmp.compare(b, "MIG_COMPARE.EXP", "MIG_WORK.ACT", CONTRACT, TOL)
    assert r["needs_human"] is True and r["diff_clusters"][0]["class"] == "GOLDEN_DATA"
def test_no_keys_uses_row_multiset():
    c = copy.deepcopy(CONTRACT); c["output"]["keys"] = []
    assert run(ROWS[::-1], contract=c)["verdict"] == "PASS"
    assert run(edit(0, "AMOUNT", 1.0), contract=c)["diff_clusters"][0]["class"] == "UNKNOWN"
def test_normalization_is_opt_in_and_reported():
    c = copy.deepcopy(CONTRACT); c["normalizations"] = ["trim:NAME"]
    r = run(edit(1, "NAME", "Bo "), contract=c); assert r["verdict"] == "PASS" and r["normalizations_applied"] == ["trim:NAME"]
def test_suspect_cte_from_segment_dag():
    dag = {"nodes": [{"tool_id": "4", "type": "formula", "config": {"formulas": [{"field": "AMOUNT", "expression": "1"}]}},
                     {"tool_id": "3", "type": "filter", "config": {"expression": "x"}}], "edges": [{"src": "3", "src_anchor": "T", "dst": "4", "dst_anchor": "Input"}]}
    assert run(edit(0, "AMOUNT", 175.0), segment_dag=dag)["diff_clusters"][0]["suspect_cte"] == "t4_formula"
    assert run(ROWS[:2], segment_dag=dag)["diff_clusters"][0]["suspect_cte"] == "t3_filter"
def test_cli_exit_codes(tmp_path):
    typed_csv.write_table(tmp_path / "exp.csv", {"fields": FIELDS, "rows": ROWS}); (tmp_path / "c.json").write_text(json.dumps(CONTRACT))
    db = tmp_path / "s.duckdb"; b = DuckDBBackend(str(db)); b.load_table("MIG_WORK.ACT", {"fields": FIELDS, "rows": edit(0, "AMOUNT", 1.0)}); b.close()
    script = Path(cmp.__file__)
    args = [sys.executable, str(script), "--expected", str(tmp_path / "exp.csv"), "--actual", "MIG_WORK.ACT", "--contract", str(tmp_path / "c.json"),
            "--out", str(tmp_path / "v.json"), "--db", str(db)]
    assert subprocess.run(args).returncode == 1 and json.loads((tmp_path / "v.json").read_text())["verdict"] == "FAIL"
    assert subprocess.run(args[:-1] + [str(tmp_path / "missing.duckdb.nope" / "x")]).returncode == 2
```

- [ ] **Step 2:** Run `pytest tests/test_compare.py`. Expected: FAIL on import.
- [ ] **Step 3:** Implement. Counts, null/distinct counts, aggregates, anti-joins and the mismatch join are SQL through `backend.query` so the same code serves Snowflake; only the bounded mismatch rows come into Python.
- [ ] **Step 4:** Run. Expected: 16 passed.
- [ ] **Step 5:** Commit `feat: deterministic parity comparison with diff-class clustering`.
