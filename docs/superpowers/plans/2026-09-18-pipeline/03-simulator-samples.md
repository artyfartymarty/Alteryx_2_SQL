# Phase 03 — Alteryx simulator and sample build

Read [00-index.md](00-index.md) and [dag-contract.md](../../../reference/dag-contract.md) first.

The simulator stands in for `AlteryxEngineCmd.exe` so golden data exists without Alteryx. It is
test tooling under `scripts/dev/`; nothing in the production path imports it. Because it is the parity
oracle, its semantics are tested against **hand-computed** values, never against the SQL.

---

### Task 8: `dev/formula.py` and `dev/alteryx_sim.py`

**Files:** Create `scripts/dev/__init__.py`, `scripts/dev/formula.py`, `scripts/dev/alteryx_sim.py`, `docs/reference/simulator-semantics.md`; Tests `tests/test_formula.py`, `tests/test_alteryx_sim.py`.

**Interfaces — Consumes:** `lib.typed_csv` Table shape, `lib.io`, `lib.paths`, `segments/*/dag.json` (C7). **Produces:**
```python
# scripts/dev/formula.py
class FormulaError(Exception)
def compile_expr(text: str) -> "Expr"
def evaluate(expr: "Expr | str", row: dict[str, object], *, constants: dict[str, str] | None = None,
             rows: "RowWindow | None" = None) -> object
class RowWindow:                       # for [Row-1:F] / [Row+1:F]
    def __init__(self, group_rows: list[dict], index: int, unknown: str, types: dict[str, str]): ...
def coerce(value: object, alteryx_type: str, size: int | None, scale: int | None = None) -> object

# scripts/dev/alteryx_sim.py
class UnsupportedTool(Exception)       # unknown, run_command, unresolved macro
@dataclass
class SimResult: streams: dict[str, dict]; outputs: dict[str, dict]; warnings: list[str]   # streams keyed "<tool_id>_<anchor>"; values are Tables
def simulate(dag: dict, inputs: dict[str, dict], *, targets_before: dict[str, dict] | None = None,
             logical_by_tool: dict[str, str] | None = None) -> SimResult
def run(repo: Repo, wf_id: str, golden_sets: Sequence[str], logical_by_tool: dict[str, str]) -> list[str]
```
CLI: `python scripts/dev/alteryx_sim.py <wf_id> [--set normal|…|all] [--root .]`. It reads `parsed/dag.json`, `segments/`, `golden/inputs/<set>/` and `golden/targets_before/<set>/`, and writes `golden/intermediates/<seg>/<set>/<stream>.csv` for every outbound stream of every segment, `golden/outputs/<set>/<tool_id>.csv` for every output tool, and `manifest.golden_sets`. `logical_by_tool` comes from `intake/mappings.yaml` when present, else from `samples/<wf>/sample.json`. On `UnsupportedTool` it writes nothing for that workflow, sets `golden_sets: []`, prints the reason and exits 1.

**Formula language.** Tokens: numbers; `"…"` and `'…'` strings; `[Field]`, `[Row-1:Field]`, `[Row+1:Field]`, `[User.Name]`/`[Engine.Name]` constants; identifiers; `+ - * / % ( ) ,`; `= == != <> < <= > >=`; `AND OR NOT && || !`; `IN (…)`; `IF … THEN … ELSEIF … ELSE … ENDIF`. Identifiers and keywords are case-insensitive. Precedence low → high: `OR`, `AND`, `NOT`, comparison, `+ -`, `* / %`, unary `-`. A `[Name]` that is not a row field is looked up in `constants`; constants are strings, converted to a number when the other operand of an arithmetic or comparison operator is numeric.

**Semantics to implement and to record in `docs/reference/simulator-semantics.md`** (one line each, marked *assumption — verify per Alteryx version* because no Alteryx was available):
- NULL propagates through arithmetic, string `+`, and functions unless stated. Comparisons with NULL give NULL. `AND`/`OR`/`NOT` are three-valued. `IF`/`IIF` treat a NULL condition as false.
- `=` on strings is case-sensitive. `Contains`, `StartsWith`, `EndsWith` take an optional third argument, default case-insensitive.
- `+` concatenates when either side is a string. `/` is float division. Division by zero gives NULL.
- `ToNumber(s)`: trimmed text that is a plain decimal (optional sign, digits, optional fraction, optional exponent) converts; anything else, including `1,200.50`, gives NULL (program spec §8.5: warn-and-null).
- `Round(x, m)`: nearest multiple of `m`, halves away from zero, computed on `Decimal(repr(float))` so `Round(2.675, 0.01)` is `2.68`.
- `Substring(s, start, len)` is 0-based. `Left`, `Right`, `PadLeft(s, n, c)`, `PadRight`, `Trim` (whitespace only), `TrimLeft`, `TrimRight`, `Length`, `Uppercase`, `Lowercase`, `TitleCase`, `Replace(s, a, b)`, `REGEX_Match(s, p, icase=1)`, `REGEX_Replace(s, p, r, icase=1)` with `$1` groups.
- `IsNull(x)`, `IsEmpty(x)` (NULL or `""`; whitespace is not empty), `Null()`, `ToString(x)` (integral floats print without `.0`), `Abs`, `Ceil`, `Floor`, `Mod`, `Pow`, `Min`, `Max` (scalar, ignore NULL).
- `DateTimeFormat(dt, fmt)`, `DateTimeParse(s, fmt)` (invalid → NULL), `DateTimeAdd(dt, n, unit)`, `DateTimeDiff(a, b, unit)` (a − b, whole units truncated), `ToDate(x)`; format tokens `%Y %m %d %H %M %S %y %b %B %j`. Date values are ISO strings. `DateTimeNow`/`DateTimeToday` raise `FormulaError("nondeterministic")`.
- `coerce`: `String`/`WString` truncate to `size` characters; `V_*` do not; integers round half away from zero; `FixedDecimal` quantizes to `scale` half away from zero; `Bool` from number is `!= 0`.
- `[Row-n:F]` outside the group: `unknown_rows` `"null"` → NULL; `"zero"` → `0` for numeric types and `""` for strings; `"nearest"` → the group's first or last row's value.

**Tool semantics** (each is one function `sim_<type>(node, inputs_by_anchor, ctx) -> dict[anchor, Table]`; details are in the dag contract §4):
- `input`: emits `inputs[tool_id]` with the fields from its `meta.Output` when present. `browse`, `output`: no out streams. `block_until_done`: pass-through.
- `select`: reorder, drop, rename, retype with `coerce`; `unknown_selected` appends unlisted fields in incoming order.
- `filter`: true → `T`; false **and NULL** → `F`. `formula`: sequential; a new field is appended, an existing one keeps its position; results go through `coerce`.
- `join`: inner equi-join on the key pairs for `J`; NULL keys never match; output order is left order, then right order within a left row; `L`/`R` are the unmatched rows in their own order; key comparison is case-sensitive; `J` columns are left fields then right fields with colliding names prefixed `Right_`, then `select` applies.
- `union`: per contract. `summarize`: groups in ascending group-key order with NULL first; `Sum`/`Avg` ignore NULL and give NULL for an all-NULL group; `Count` counts rows, `CountNonNull` non-NULL values; `First`/`Last` use incoming order; `Concat` skips NULL. Output types: `Count*` → Int64, `Sum`/`Avg` → Double (FixedDecimal stays FixedDecimal for `Sum`), others keep the source type.
- `sort`: stable, multi-key, NULL first ascending and last descending, strings by code point. `unique`, `sample`, `record_id`: per contract, in incoming order.
- `multi_row_formula`: processes rows in incoming order within each group, so `[Row-1:RUN_BAL]` sees the value just computed.
- `cross_tab`: rows in ascending group-key order; header columns from `meta.Output` when present (frozen list), else sorted distinct sanitized header values; missing combinations are NULL; methods `Sum Count Avg Min Max First Last Concat`.
- `transpose`, `regex`, `datetime`, `data_cleansing`: per contract. `modify_case: title` capitalizes each word.
- `macro`: substitute `[%Question.<name>%]` in every expression of `sub_dag` with the configured value (or the interface default), feed `Input<id>` streams to the matching `macro_input`, return `Output<id>` streams. Unresolved → `UnsupportedTool`.
- `output`: file targets and `overwrite` → the incoming table. DB targets with `targets_before[logical]`: load it into an in-memory DuckDB under the node's `table` name, run `pre_sql` (sqlglot `read="tsql"`), apply the mode by column **name** (`append` inserts; `update_insert` updates matching keys' listed columns and inserts the rest with other columns NULL; `truncate_append` clears first), run `post_sql`, and read the table back ordered by its keys or all columns. That final state is `outputs[tool_id]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_formula.py
import pytest
from dev.formula import evaluate, coerce, RowWindow, FormulaError

@pytest.mark.parametrize("expr,row,expected", [
    ('[A] + [B] * 2', {"A": 1, "B": 3}, 7),
    ('[A] + 1', {"A": None}, None),
    ('[A] > 5', {"A": None}, None),
    ('IIF([A] > 5, "hi", "lo")', {"A": None}, "lo"),
    ('IF [A] >= 1000 THEN "LARGE" ELSEIF [A] >= 100 THEN "MEDIUM" ELSE "SMALL" ENDIF', {"A": 100}, "MEDIUM"),
    ('[S] = "abc"', {"S": "ABC"}, False),
    ('Contains([S], "bc")', {"S": "ABC"}, True),
    ('Contains([S], "bc", 0)', {"S": "ABC"}, False),
    ('ToNumber([S])', {"S": " 12.50 "}, 12.5),
    ('ToNumber([S])', {"S": "1,200.50"}, None),
    ('ToNumber([S])', {"S": "abc"}, None),
    ('Round(2.675, 0.01)', {}, 2.68),
    ('Round(-2.5, 1)', {}, -3),
    ('Round([A] * IIF([Q] >= 10, 0.9, 1), 0.01)', {"A": 33.35, "Q": 10}, 30.02),
    ('Substring("abcdef", 1, 3)', {}, "bcd"),
    ('PadLeft(ToString(7), 3, "0")', {}, "007"),
    ('ToString(12.0)', {}, "12"),
    ('IsEmpty([S])', {"S": "  "}, False),
    ('IsEmpty([S]) OR IsNull([S])', {"S": None}, True),
    ('[A] / 0', {"A": 1}, None),
    ('"x" + [S]', {"S": None}, None),
    ('[S] IN ("a", "b")', {"S": "b"}, True),
    ('NOT ([A] = 1) AND [B] = 2', {"A": 2, "B": 2}, True),
    ('REGEX_Replace([S], "^\\s*([A-Za-z]+)[- ]?(\\d+)\\s*$", "$1-$2")', {"S": " ab 12 "}, "ab-12"),
    ('DateTimeFormat([D], "%Y-%m-%d")', {"D": "2026-08-31 00:00:00"}, "2026-08-31"),
    ('DateTimeParse([S], "%d/%m/%Y")', {"S": "31/02/2026"}, None),
    ('DateTimeParse([S], "%d/%m/%Y")', {"S": "29/02/2024"}, "2024-02-29 00:00:00"),
    ('DateTimeDiff("2026-03-01", "2026-02-27", "days")', {}, 2),
])
def test_evaluate(expr, row, expected):
    got = evaluate(expr, row)
    assert got == pytest.approx(expected) if isinstance(expected, float) else got == expected

def test_constants_convert_by_context():
    assert evaluate('[REGION] = [User.Region]', {"REGION": "EMEA"}, constants={"User.Region": "EMEA"}) is True
    assert evaluate('[QTY] >= [User.Min]', {"QTY": 5}, constants={"User.Min": "5"}) is True

def test_row_window_unknown_rows():
    rows = [{"AMT": 10.0, "RUN": 10.0}, {"AMT": 5.0, "RUN": None}]
    types = {"AMT": "Double", "RUN": "Double"}
    assert evaluate('[Row-1:RUN] + [AMT]', rows[1], rows=RowWindow(rows, 1, "zero", types)) == 15.0
    assert evaluate('[Row-1:RUN] + [AMT]', rows[0], rows=RowWindow(rows, 0, "zero", types)) == 10.0
    assert evaluate('[Row-1:RUN] + [AMT]', rows[0], rows=RowWindow(rows, 0, "null", types)) is None

def test_coerce():
    assert coerce("Chandrasekhar", "String", 10) == "Chandrasek" and coerce("Chandrasekhar", "V_String", 10) == "Chandrasekhar"
    assert coerce(2.5, "Int32", 4) == 3 and coerce(-2.5, "Int32", 4) == -3 and str(coerce(1.005, "FixedDecimal", 19, 2)) == "1.01"

def test_nondeterministic_functions_are_refused():
    with pytest.raises(FormulaError, match="nondeterministic"): evaluate("DateTimeNow()", {})
def test_syntax_error_is_reported():
    with pytest.raises(FormulaError): evaluate("IF [A] THEN 1", {"A": True})
```
```python
# tests/test_alteryx_sim.py
from decimal import Decimal
import pytest
from dev.alteryx_sim import simulate, UnsupportedTool

def F(name, type="V_String", size=50, scale=None): return {"name": name, "type": type, "size": size, "scale": scale}
def node(tid, type, config=None, **kw): return {"tool_id": tid, "type": type, "config": config or {}, "container_id": None, "meta": {}, **kw}
def edge(s, sa, d, da="Input", order=1): return {"src": s, "src_anchor": sa, "dst": d, "dst_anchor": da, "dst_order": order}
def dag(nodes, edges, constants=None): return {"workflow": "t", "nodes": nodes, "edges": edges, "constants": constants or {}}
def table(fields, rows): return {"fields": fields, "rows": rows}
def names(t): return [f["name"] for f in t["fields"]]

def test_filter_sends_null_to_false():
    d = dag([node("1", "input"), node("2", "filter", {"expression": '[R] != "WEST"'})], [edge("1", "Output", "2")])
    r = simulate(d, {"1": table([F("R")], [["EAST"], ["WEST"], [None]])})
    assert r.streams["2_T"]["rows"] == [["EAST"]] and r.streams["2_F"]["rows"] == [["WEST"], [None]]

def test_select_truncates_renames_and_keeps_unknown():
    cfg = {"fields": [{"name": "C", "selected": True, "rename": None, "type": "String", "size": 3},
                      {"name": "S", "selected": True, "rename": "STATUS", "type": None, "size": None},
                      {"name": "X", "selected": False, "rename": None, "type": None, "size": None}], "unknown_selected": True}
    d = dag([node("1", "input"), node("2", "select", cfg)], [edge("1", "Output", "2")])
    out = simulate(d, {"1": table([F("C"), F("S"), F("X"), F("Z")], [["abcdef", "ok", "drop", "z"]])}).streams["2_Output"]
    assert names(out) == ["C", "STATUS", "Z"] and out["rows"] == [["abc", "ok", "z"]] and out["fields"][0]["type"] == "String"

def test_formula_is_sequential():
    cfg = {"formulas": [{"field": "A", "expression": "[A] + 1", "type": "Int32", "size": 4},
                        {"field": "B", "expression": "[A] * 10", "type": "Int32", "size": 4}]}
    d = dag([node("1", "input"), node("2", "formula", cfg)], [edge("1", "Output", "2")])
    assert simulate(d, {"1": table([F("A", "Int32", 4)], [[1]])}).streams["2_Output"]["rows"] == [[2, 20]]

def test_join_outputs_and_right_prefix():
    cfg = {"keys": [{"left": "ID", "right": "ID"}], "select": {"fields": [], "unknown_selected": True}}
    d = dag([node("1", "input"), node("2", "input"), node("3", "join", cfg)], [edge("1", "Output", "3", "Left"), edge("2", "Output", "3", "Right")])
    r = simulate(d, {"1": table([F("ID", "Int32", 4), F("N")], [[1, "a"], [2, "b"], [None, "n"]]),
                     "2": table([F("ID", "Int32", 4), F("V")], [[1, "x"], [1, "y"], [3, "z"], [None, "q"]])})
    assert names(r.streams["3_J"]) == ["ID", "N", "Right_ID", "V"]
    assert r.streams["3_J"]["rows"] == [[1, "a", 1, "x"], [1, "a", 1, "y"]]
    assert r.streams["3_L"]["rows"] == [[2, "b"], [None, "n"]] and r.streams["3_R"]["rows"] == [[3, "z"], [None, "q"]]

def test_union_by_name_fills_missing_with_null():
    d = dag([node("1", "input"), node("2", "input"), node("3", "union", {"mode": "name"})],
            [edge("1", "Output", "3", order=1), edge("2", "Output", "3", order=2)])
    out = simulate(d, {"1": table([F("A"), F("B")], [["a", "b"]]), "2": table([F("B"), F("C")], [["b2", "c2"]])}).streams["3_Output"]
    assert names(out) == ["A", "B", "C"] and out["rows"] == [["a", "b", None], [None, "b2", "c2"]]

def test_summarize_counts_nulls_and_orders_groups():
    cfg = {"fields": [{"field": "G", "action": "GroupBy", "rename": "G", "separator": None}, {"field": "V", "action": "Sum", "rename": "S", "separator": None},
                      {"field": "V", "action": "Count", "rename": "N", "separator": None}, {"field": "V", "action": "CountNonNull", "rename": "NN", "separator": None},
                      {"field": "T", "action": "Last", "rename": "LAST_T", "separator": None}, {"field": "T", "action": "Concat", "rename": "CAT", "separator": "|"}]}
    d = dag([node("1", "input"), node("2", "summarize", cfg)], [edge("1", "Output", "2")])
    rows = [["b", 1.0, "x"], ["a", None, "y"], ["b", 2.5, None], [None, 4.0, "z"]]
    out = simulate(d, {"1": table([F("G"), F("V", "Double", 8), F("T")], rows)}).streams["2_Output"]
    assert out["rows"] == [[None, 4.0, 1, 1, "z", "z"], ["a", None, 1, 0, "y", "y"], ["b", 3.5, 2, 2, None, "x"]]

def test_sort_unique_multirow_recordid_chain():
    nodes = [node("1", "input"), node("2", "sort", {"fields": [{"field": "ACCT", "order": "asc"}, {"field": "DAY", "order": "asc"}, {"field": "ENTRY", "order": "desc"}]}),
             node("3", "unique", {"fields": ["ACCT", "DAY"]}),
             node("4", "multi_row_formula", {"field": "RUN", "update_existing": False, "type": "Double", "size": 8, "num_rows": 1,
                                             "expression": "[Row-1:RUN] + [AMT]", "group_by": ["ACCT"], "unknown_rows": "zero"}),
             node("5", "record_id", {"field": "RecordID", "start": 1, "type": "Int32", "position": "first"})]
    edges = [edge("1", "Output", "2"), edge("2", "Output", "3"), edge("3", "U", "4"), edge("4", "Output", "5")]
    rows = [["B", "d1", 1, 5.0], ["A", "d1", 1, 10.0], ["A", "d1", 2, 11.0], ["A", "d2", 3, 1.5]]
    r = simulate(dag(nodes, edges), {"1": table([F("ACCT"), F("DAY"), F("ENTRY", "Int32", 4), F("AMT", "Double", 8)], rows)})
    assert r.streams["5_Output"]["rows"] == [[1, "A", "d1", 2, 11.0, 11.0], [2, "A", "d2", 3, 1.5, 12.5], [3, "B", "d1", 1, 5.0, 5.0]]
    assert r.streams["3_D"]["rows"] == [["A", "d1", 1, 10.0]]

def test_cross_tab_frozen_headers_and_transpose():
    ct = node("2", "cross_tab", {"group_by": ["SKU"], "header_field": "WH", "data_field": "QTY", "methods": ["Sum"]},
              meta={"Output": [F("SKU"), F("EAST", "Double", 8), F("NORTH", "Double", 8), F("WEST", "Double", 8)]})
    tp = node("3", "transpose", {"key_fields": ["SKU"], "data_fields": ["EAST", "NORTH", "WEST"]})
    d = dag([node("1", "input"), ct, tp], [edge("1", "Output", "2"), edge("2", "Output", "3")])
    r = simulate(d, {"1": table([F("SKU"), F("WH"), F("QTY", "Int32", 4)], [["B-2", "WEST", 1], ["A-1", "EAST", 2], ["A-1", "EAST", 3], ["A-1", "WEST", 4]])})
    assert r.streams["2_Output"]["rows"] == [["A-1", 5.0, None, 4.0], ["B-2", None, None, 1.0]]
    assert r.streams["3_Output"]["rows"][:3] == [["A-1", "EAST", 5.0], ["A-1", "NORTH", None], ["A-1", "WEST", 4.0]]

def test_regex_parse_and_datetime_and_cleansing():
    rx = node("2", "regex", {"field": "SKU", "expression": "^([A-Z]+)-(\\d+)$", "case_insensitive": False, "method": "parse", "replace": None,
                             "copy_unmatched": False, "output_fields": [F("FAMILY", size=20), F("ITEM_NO", size=10)], "match_field": None})
    dt = node("3", "datetime", {"direction": "to_datetime", "field": "P", "format": "%d/%m/%Y", "out_field": "P_DT"})
    dc = node("4", "data_cleansing", {"fields": ["N"], "replace_null_strings_blank": True, "replace_null_numeric_zero": False, "trim_whitespace": True,
                                      "remove_tabs_linebreaks_dupspaces": False, "remove_all_whitespace": False, "remove_letters": False,
                                      "remove_numbers": False, "remove_punctuation": False, "modify_case": "upper"})
    d = dag([node("1", "input"), rx, dt, dc], [edge("1", "Output", "2"), edge("2", "Output", "3"), edge("3", "Output", "4")])
    out = simulate(d, {"1": table([F("SKU"), F("P"), F("N")], [["AB-12", "29/02/2024", "  zoë "], ["bad", "31/02/2026", None]])}).streams["4_Output"]
    assert names(out) == ["SKU", "P", "N", "FAMILY", "ITEM_NO", "P_DT"]
    assert out["rows"] == [["AB-12", "29/02/2024", "ZOË", "AB", "12", "2024-02-29 00:00:00"], ["bad", "31/02/2026", "", None, None, None]]

def test_macro_substitutes_question_values():
    sub = dag([node("1", "macro_input"), node("4", "filter", {"expression": "[QTY] >= [%Question.MinQty%]"}), node("5", "macro_output")],
              [edge("1", "Output", "4"), edge("4", "T", "5")])
    m = node("2", "macro", {"values": {"MinQty": "2"}}, sub_dag=sub, interface=[{"name": "MinQty", "type": "NumericUpDown", "default": "0"}], macro_path="m.yxmc")
    d = dag([node("1", "input"), m], [edge("1", "Output", "2", "Input1")])
    assert simulate(d, {"1": table([F("QTY", "Int32", 4)], [[1], [2], [None]])}).streams["2_Output5"]["rows"] == [[2]]

def test_update_insert_with_pre_and_post_sql():
    out = node("2", "output", {"source": "<scrubbed:fin>", "format": "db", "alias": "fin", "table": "dbo.GL_SUMMARY", "write_mode": "update_insert",
                               "keys": ["ACCT"], "pre_sql": "DELETE FROM dbo.GL_SUMMARY WHERE ACCT = 'OLD'",
                               "post_sql": "UPDATE dbo.GL_SUMMARY SET LOADED_FLAG = 'Y' WHERE LOADED_FLAG IS NULL"})
    d = dag([node("1", "input"), out], [edge("1", "Output", "2")])
    before = table([F("ACCT"), F("TOTAL", "FixedDecimal", 19, 2), F("LOADED_FLAG", size=1)],
                   [["A", Decimal("1.00"), "Y"], ["OLD", Decimal("9.00"), "Y"], ["K", Decimal("5.00"), "N"]])
    r = simulate(d, {"1": table([F("ACCT"), F("TOTAL", "FixedDecimal", 19, 2)], [["A", Decimal("2.50")], ["NEW", Decimal("7.00")]])},
                 targets_before={"GL_SUMMARY": before}, logical_by_tool={"2": "GL_SUMMARY"})
    assert r.outputs["2"]["rows"] == [["A", Decimal("2.50"), "Y"], ["K", Decimal("5.00"), "N"], ["NEW", Decimal("7.00"), "Y"]]

def test_unknown_tool_is_refused():
    d = dag([node("1", "input"), node("2", "unknown")], [edge("1", "Output", "2")])
    with pytest.raises(UnsupportedTool, match="2"): simulate(d, {"1": table([F("A")], [])})
```

- [ ] **Step 2:** Run both files. Expected: FAIL on import.
- [ ] **Step 3:** Implement `formula.py` (tokenizer, recursive-descent parser to a small AST, evaluator), then `alteryx_sim.py` (topological execution; one `sim_<type>` per tool). Write `docs/reference/simulator-semantics.md` from the list above.
- [ ] **Step 4:** Run. Expected: all pass. Recompute by hand any expected value you are tempted to change; change the test only if the hand computation shows the test was wrong, and say so in the commit body.
- [ ] **Step 5:** Commit `feat: test-only Alteryx formula evaluator and DAG simulator`.

---

### Task 9: `dev/build_samples.py`

**Files:** Create `scripts/dev/build_samples.py`; Test `tests/test_build_samples.py`.

**Interfaces — Consumes:** `lib.yxdb.write_yxdb`, `typed_csv.read_table`, `parse.run`, `segment.run`, `alteryx_sim.run`. **Produces:**
```python
def seed(repo: Repo, samples_dir: Path, wf_id: str) -> dict       # returns the manifest it wrote
def build(repo: Repo, samples_dir: Path, wf_id: str) -> dict      # seed → parse → segment → simulate; returns {"parse", "segments", "golden_sets"}
```
CLI: `python scripts/dev/build_samples.py [seed|build] [--only wf_0001] [--samples samples] [--root .]`.

`seed` copies `samples/<wf>/source/**` to `workflows/<wf>/source/`; copies `golden_inputs/<set>/` to `golden/inputs/<set>/` and `golden_inputs/targets_before/` to `golden/targets_before/`; for every `input` node whose format is `yxdb`, writes `workflows/<wf>/source/data/<basename>.yxdb` from that tool's `normal` rows (parsing the sample source in memory to find those nodes); and writes `manifest.json` with `id`, `source` (`file`, `alteryx_version`, `engine`, `server_schedule`, `owner`, `consumers: []`), `segmentation`, empty `status` and `metrics`. `seed` never overwrites an existing `manifest.json`'s `status`.

- [ ] **Step 1:** Write `tests/test_build_samples.py`: (a) `seed` for `wf_0001` into `tmp_path` creates `source/data/orders.yxdb` whose header has 7 fields and a record count equal to the normal CSV's rows, and a manifest with `engine == "AMP"`; (b) `build` for `wf_0003` yields segments `seg_01`, `seg_02` and files `golden/intermediates/seg_01/normal/3_Output.csv` and `golden/outputs/edge/10.csv`; (c) the `empty` set produces zero-row outputs with full schemas; (d) `build` for `wf_0005` reports `golden_sets == []` without raising; (e) running `build` twice leaves byte-identical golden files.
- [ ] **Step 2:** Run. Expected: FAIL on import.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: 5 passed.
- [ ] **Step 5:** Commit `feat: sample seeding, yxdb generation and simulated golden sets`.
