# Phase 01 — Foundations, yxdb, samples, parser, segmenter

Read [00-index.md](00-index.md) first: Global Constraints and contracts C1–C8 apply to every task.

---

### Task 1: Foundations

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `.gitattributes`, `tsconfig.json`
- Create: `scripts/lib/__init__.py`, `scripts/lib/paths.py`, `scripts/lib/vocab.py`, `scripts/lib/io.py`, `scripts/lib/typed_csv.py`
- Create: `.github/copilot-instructions.md` (verbatim from program spec 01 §A.3, plus the line `- Procedures follow docs/reference/dag-contract.md and plan contract C4: linear statement list, logical source and target names only.`)
- Create: `mappings/global.yaml` (program spec §5.6 template; add `program.raw_schema: RAW`, `rounding: {abs: 0.01}` under `tolerances`, empty `sources: {}` and `outputs: {}`)
- Test: `tests/test_foundations.py`

**Interfaces — Produces:**
```python
# scripts/lib/paths.py
class Repo:
    def __init__(self, root: str | os.PathLike = "."): ...   # .root is resolved Path
    def wf(self, wf_id: str, *parts: str) -> Path            # root/workflows/<wf_id>/…
    def seg(self, wf_id: str, seg: str, *parts: str) -> Path # root/workflows/<wf_id>/segments/<seg>/…
    global_mappings: Path                                     # root/mappings/global.yaml
def add_root_arg(parser: argparse.ArgumentParser) -> None    # adds --root, default "."
def wf_token(wf_id: str) -> str                               # "wf_0001" -> "WF0001"
def seg_token(seg: str) -> str                                # "seg_01" -> "SEG_01"

# scripts/lib/vocab.py
STATUSES: frozenset[str]; VERDICTS: frozenset[str]; DIFF_CLASSES: tuple[str, ...]
GOLDEN_SETS = ("normal", "period_end", "empty", "edge")
NON_DATA_TYPES = frozenset({"container", "comment", "interface", "action"})
ORDER_DEPENDENT_TYPES = frozenset({"sample", "record_id", "unique", "multi_row_formula"})
T3_TYPES = frozenset({"run_command"})

# scripts/lib/io.py
def read_json(path) -> Any;  def write_json(path, obj) -> None      # mkdir parents, indent=2, "\n" at EOF, newline="\n"
def read_yaml(path) -> Any;  def write_yaml(path, obj) -> None      # sort_keys=False, allow_unicode=True
def load_manifest(repo: Repo, wf_id: str) -> dict                   # default {"id", "status": {}, "metrics": {}} if absent
def save_manifest(repo: Repo, manifest: dict) -> None               # sets updated_at = UTC ISO-8601 "…Z"

# scripts/lib/typed_csv.py  (contract C1)
def read_table(csv_path) -> dict;  def write_table(csv_path, table: dict) -> None
def parse_value(text: str, alteryx_type: str) -> object;  def format_value(value, alteryx_type: str) -> str
```

`pyproject.toml`:
```toml
[project]
name = "alteryx-to-snowflake-migration"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
pythonpath = ["scripts", "."]
testpaths = ["tests"]
addopts = "-q"
markers = ["e2e: runs whole sample workflows through DuckDB"]
```
`requirements.txt`: `duckdb>=1.4`, `sqlglot>=30`, `pyyaml>=6`, `pytest>=8`.
`.gitattributes`: `* text=auto eol=lf`, `*.yxdb binary`, `*.yxzp binary`, `*.duckdb binary`, `tests/parser_corpus/cp1252/* -text`.
`tsconfig.json`: `target`/`module` `ES2022`/`NodeNext`, `moduleResolution: NodeNext`, `strict: true`, `noEmit: true`, `allowImportingTsExtensions: true`, `erasableSyntaxOnly: true`, `skipLibCheck: true`, `include: ["orchestrate.ts", "orchestrator/**/*.ts"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_foundations.py
import json
from decimal import Decimal
from lib import io, typed_csv, vocab
from lib.paths import Repo, wf_token, seg_token

def test_repo_paths(tmp_path):
    repo = Repo(tmp_path)
    assert repo.wf("wf_0001", "parsed", "dag.json") == tmp_path.resolve() / "workflows/wf_0001/parsed/dag.json"
    assert repo.seg("wf_0001", "seg_02", "proc.sql").name == "proc.sql"
    assert wf_token("wf_0001") == "WF0001" and seg_token("seg_02") == "SEG_02"

def test_json_is_lf_with_trailing_newline(tmp_path):
    p = tmp_path / "a" / "b.json"
    io.write_json(p, {"k": [1, 2]})
    raw = p.read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw and json.loads(raw) == {"k": [1, 2]}

def test_manifest_default_and_timestamp(tmp_path):
    repo = Repo(tmp_path)
    m = io.load_manifest(repo, "wf_0009")
    assert m == {"id": "wf_0009", "status": {}, "metrics": {}}
    io.save_manifest(repo, m)
    assert io.load_manifest(repo, "wf_0009")["updated_at"].endswith("Z")

def test_typed_csv_round_trip_distinguishes_null_from_empty(tmp_path):
    table = {"fields": [{"name": "S", "type": "V_String", "size": 20, "scale": None},
                        {"name": "N", "type": "Int32", "size": 4, "scale": None},
                        {"name": "D", "type": "FixedDecimal", "size": 19, "scale": 2},
                        {"name": "B", "type": "Bool", "size": 1, "scale": None}],
             "rows": [["a,b", 1, Decimal("10.50"), True], ["", None, None, False], [None, -3, Decimal("0.00"), None]]}
    path = tmp_path / "t.csv"
    typed_csv.write_table(path, table)
    assert (tmp_path / "t.schema.json").exists()
    assert "\\N" in path.read_text(encoding="utf-8")
    assert typed_csv.read_table(path) == table

def test_vocab_is_closed():
    assert "WAITING_FOR_ANSWERS" in vocab.STATUSES and "PASS_WITH_ACCEPTED_DIFF" in vocab.VERDICTS
    assert vocab.DIFF_CLASSES == ("ROUNDING", "ORDERING", "NULL_SEMANTICS", "TRUNCATION", "TYPE", "LOGIC", "GOLDEN_DATA", "UNKNOWN")
```

- [ ] **Step 2:** Run `.venv/Scripts/python.exe -m pytest tests/test_foundations.py`. Expected: FAIL, `ModuleNotFoundError: lib`.
- [ ] **Step 3:** Implement the five modules. `typed_csv`: Int*/Byte → `int`; Float/Double → `float`; FixedDecimal → `Decimal`; Bool → `true`/`false`; everything else `str`; `\N` ↔ `None`; open files with `newline=""` and write `lineterminator="\n"`.
- [ ] **Step 4:** Run the tests. Expected: 5 passed.
- [ ] **Step 5:** Commit `chore: project foundations, shared lib and repo rules`.

---

### Task 2: `scripts/lib/yxdb.py`

**Files:** Create `scripts/lib/yxdb.py`; Test `tests/test_yxdb.py`.

**Interfaces — Produces:**
```python
@dataclass
class YxdbHeader: file_type: str; num_records: int; fields: list[dict]; meta_xml: str   # fields: {"name","type","size","scale"}
def read_header(path) -> YxdbHeader                 # reads 512 bytes + metadata only, never the records
def read_records(path) -> Iterator[list]            # values as in contract C1
def write_yxdb(path, fields: list[dict], rows: Iterable[list]) -> None
def lzf_decompress(data: bytes, max_out: int = 262144) -> bytes
class YxdbError(Exception)
```

**File layout (little-endian).** Header, 512 bytes: `[0:64]` ASCII `Alteryx Database File` NUL-padded; `[64:68]` file id `0x00440205`; `[68:72]` creation date (uint32, seconds since epoch; write 0 so output is reproducible); `[72:80]` two flag words, 0; `[80:84]` metadata length **in UTF-16 code units including the NUL terminator**; `[84:88]` 0; `[88:96]` spatial index position, 0; `[96:104]` record block index position; `[104:112]` record count (int64); `[112:116]` compression version, 1; rest 0. Then the metadata: UTF-16LE `<RecordInfo><Field name="…" type="…" size="…" scale="…" source="…"/>…</RecordInfo>` plus the 2-byte NUL. `size` is omitted for types with a fixed size; `scale` only on FixedDecimal.

Then record blocks: `uint32 length`; high bit set means the next `length & 0x7FFFFFFF` bytes are stored raw, otherwise they are LZF-compressed and inflate to at most 262,144 bytes. Records may straddle blocks, so the reader treats the inflated blocks as one byte stream. The writer emits raw blocks of at most 262,144 bytes, then a block index (`uint32 count`, then `int64` file offsets), and patches `[96:104]`.

Record = fixed part, then if any variable-length field exists, `uint32 var_len` and `var_len` bytes. Fixed part per field, in order: Bool 1 byte (0, 1, or 2 = NULL). Every other fixed type is its value bytes plus one trailing NULL-flag byte (1 = NULL): Byte 1+1; Int16 2+1; Int32 4+1; Int64 8+1; Float 4+1; Double 8+1; FixedDecimal `size`+1 (ASCII text, NUL-padded); String `size`+1 (latin-1, NUL-padded); WString `2*size`+1 (UTF-16LE, NUL-padded); Date 10+1; Time 8+1; DateTime 19+1. V_String, V_WString, Blob, SpatialObj take a 4-byte slot `v`: `0` = empty, `1` = NULL; if `v & 0x80000000 == 0` and `v & 0x30000000 != 0` it is a *tiny* value whose length is `v >> 28` and whose bytes are the slot's first bytes; otherwise the payload starts at `slot_offset + (v & 0x7FFFFFFF)`: a first byte with its low bit set is a *small* block (`length = byte >> 1`, data follows), else a `uint32` whose value `>> 1` is the length, data after those 4 bytes. V_String payloads are latin-1, V_WString UTF-16LE. The writer uses only empty, NULL, small (< 128 bytes) and normal forms; the reader accepts all.

LZF: control byte `c`; `c < 32` copies `c + 1` literals; otherwise `len = c >> 5` (if 7, add the next byte), then `len += 2`, `offset = ((c & 0x1F) << 8 | next) + 1`, copy `len` bytes from `out[-offset]` one at a time (overlap allowed).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_yxdb.py
import struct
from decimal import Decimal
import pytest
from lib import yxdb

FIELDS = [{"name": "ID", "type": "Int32", "size": 4, "scale": None},
          {"name": "NAME", "type": "V_WString", "size": 60, "scale": None},
          {"name": "CODE", "type": "String", "size": 5, "scale": None},
          {"name": "AMT", "type": "FixedDecimal", "size": 19, "scale": 2},
          {"name": "RATE", "type": "Double", "size": 8, "scale": None},
          {"name": "OK", "type": "Bool", "size": 1, "scale": None},
          {"name": "DAY", "type": "Date", "size": 10, "scale": None},
          {"name": "NOTE", "type": "V_String", "size": 200, "scale": None}]
ROWS = [[1, "Zoë Ünal", "AB", Decimal("10.50"), 0.25, True, "2024-02-29", "x" * 150],
        [2, "", "", Decimal("-0.01"), -0.0, False, "2026-08-31", ""],
        [None, None, None, None, None, None, None, None]]

def test_round_trip(tmp_path):
    p = tmp_path / "t.yxdb"
    yxdb.write_yxdb(p, FIELDS, ROWS)
    assert list(yxdb.read_records(p)) == ROWS

def test_header_without_reading_records(tmp_path):
    p = tmp_path / "t.yxdb"
    yxdb.write_yxdb(p, FIELDS, ROWS)
    h = yxdb.read_header(p)
    assert h.file_type == "Alteryx Database File" and h.num_records == 3
    assert [f["name"] for f in h.fields] == [f["name"] for f in FIELDS]
    assert h.fields[3] == {"name": "AMT", "type": "FixedDecimal", "size": 19, "scale": 2}

def test_header_bytes_follow_the_documented_layout(tmp_path):
    p = tmp_path / "t.yxdb"
    yxdb.write_yxdb(p, FIELDS, ROWS)
    raw = p.read_bytes()
    assert raw[:21] == b"Alteryx Database File" and struct.unpack_from("<q", raw, 104)[0] == 3
    units = struct.unpack_from("<I", raw, 80)[0]
    meta = raw[512:512 + units * 2]
    assert meta.endswith(b"\x00\x00") and meta[:-2].decode("utf-16-le").startswith("<RecordInfo>")

def test_many_rows_span_blocks(tmp_path):
    p = tmp_path / "big.yxdb"
    rows = [[i, f"name{i}", "C", Decimal("1.00"), 1.0, True, "2026-01-01", "n" * 190] for i in range(3000)]
    yxdb.write_yxdb(p, FIELDS, rows)
    assert yxdb.read_header(p).num_records == 3000 and sum(1 for _ in yxdb.read_records(p)) == 3000

def test_lzf_back_reference_with_overlap():
    assert yxdb.lzf_decompress(bytes([0x00, 0x61, 0x40, 0x00])) == b"aaaaa"   # literal 'a', then copy 4 from offset 1 (0x40: len field 2, +2 = 4)

def test_tiny_variable_value_is_readable(tmp_path):
    fields = [{"name": "S", "type": "V_String", "size": 10, "scale": None}]
    p = tmp_path / "tiny.yxdb"
    yxdb.write_yxdb(p, fields, [["zz"]])
    raw = bytearray(p.read_bytes())
    start = 512 + struct.unpack_from("<I", raw, 80)[0] * 2
    body_len = struct.unpack_from("<I", raw, start)[0] & 0x7FFFFFFF
    record = b"ab\x00\x20" + struct.pack("<I", 0)             # tiny: length 2 in the top nibble, no var data
    raw[start:start + 4 + body_len] = struct.pack("<I", len(record) | 0x80000000) + record
    p.write_bytes(bytes(raw[:start + 4 + len(record)]))
    assert list(yxdb.read_records(p)) == [["ab"]]

def test_not_a_yxdb(tmp_path):
    p = tmp_path / "x.yxdb"; p.write_bytes(b"nope" * 200)
    with pytest.raises(yxdb.YxdbError):
        yxdb.read_header(p)
```

- [ ] **Step 2:** Run `pytest tests/test_yxdb.py`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement per the layout above. `read_header` must not touch bytes past the metadata.
- [ ] **Step 4:** Run. Expected: 7 passed. Optional cross-check, not a gate: `pip install yxdb` in a scratch venv and read a written file with that independent reader; record the outcome in the commit body.
- [ ] **Step 5:** Commit `feat: yxdb header/record reader and fixture writer`. State in the module docstring that files are verified against this reader only, not against Alteryx Designer.

---

### Task 3: Sample workflow sources and golden inputs

**Files (per workflow `wf_000N`):** `samples/wf_000N/source/*.yxmd` (+ `Supporting_Macros/clean_codes.yxmc` for wf_0004), `samples/wf_000N/golden_inputs/<set>/<tool_id>.csv` + `.schema.json` for the four sets, `samples/wf_000N/golden_inputs/targets_before/<set>/<LOGICAL>.csv` where noted, `samples/wf_000N/sample.json`. Test: `tests/test_samples_wellformed.py`.

XML follows [dag-contract.md](../../../reference/dag-contract.md) §2–§5 exactly. Every node has `<GuiSettings Plugin="…"><Position x= y=/></GuiSettings>`, `<Properties><Configuration>…</Configuration><Annotation DisplayMode="0"><Name/><DefaultAnnotationText>…</DefaultAnnotationText></Annotation><MetaInfo connection="…"><RecordInfo>…</RecordInfo></MetaInfo></Properties>` and `<EngineSettings EngineDll="AlteryxBasePluginsEngine.dll" EngineDllEntryPoint="…"/>`. Give every data tool a correct `MetaInfo` for each out anchor: intake and contracts rely on it.

`sample.json`: `{"id", "title", "owner": "wf_owner", "schedule": "0 6 * * 1-5", "segmentation": {"min_tools": 3, "max_tools": 40}, "expected_terminal": "VALIDATED"|"MANUAL", "answers": {"<normalized key or tool id>": "<FQN>"}, "logical": {"<tool_id>": "<LOGICAL>"}}`.

**wf_0001 — sales summary** (`sales_summary.yxmd`, `yxmdVer="2023.1"`, AMP). Input fields: `ORDER_ID Int32, CUSTOMER V_String(50), REGION V_String(20), AMOUNT_TXT V_String(20), QTY Int32, ORDER_DATE Date, STATUS V_String(10)`.

| Id | Tool | Config |
|----|------|--------|
| 1 | input | `C:\data\sales\orders.yxdb` |
| 2 | select | `CUSTOMER` → `String(10)`; `STATUS` renamed `ORDER_STATUS`; `*Unknown` selected |
| 3 | filter | `[REGION] != "WEST"` — T → 4, F → 8 |
| 4 | formula | `AMOUNT` Double = `ToNumber([AMOUNT_TXT])`; `NET` Double = `Round([AMOUNT] * IIF([QTY] >= 10, 0.9, 1), 0.01)`; `SIZE_BAND` V_String(10) = `IF [AMOUNT] >= 1000 THEN "LARGE" ELSEIF [AMOUNT] >= 100 THEN "MEDIUM" ELSE "SMALL" ENDIF` |
| 5 | summarize | GroupBy `REGION`, `SIZE_BAND`; Sum `NET` → `TOTAL_NET`; Count → `ORDERS`; CountNonNull `AMOUNT` → `PRICED_ORDERS`; Max `ORDER_DATE` → `LAST_ORDER` |
| 6 | sort | `TOTAL_NET` desc, `REGION` asc |
| 7 | output | `C:\data\out\sales_summary.yxdb`, overwrite |
| 8 | output | `C:\data\out\excluded_orders.csv`, overwrite |

`normal` has about 20 rows and must include: a NULL `REGION` (must reach tool 8), `REGION` `WEST`, an `AMOUNT_TXT` of `abc` and one of `1,200.50` (both become NULL), a `CUSTOMER` longer than 10 characters, a `QTY` of exactly 10, and values whose `NET` lands on a half cent.

**wf_0002 — customer orders** (`customer_orders.yxmd`, E1). Container 100 "Prep customers" holds 1–2; container 200 "Prep orders" holds nested container 210, which holds 3–4; TextBox 300 is a comment.

| Id | Tool | Config |
|----|------|--------|
| 1 | input | `\\fileserver\crm\customers.yxdb`: `CUST_ID Int32, NAME V_WString(60), CITY V_String(40), TIER V_String(10)` |
| 2 | data_cleansing | fields `NAME, CITY`; null strings → blank; trim; modify case upper |
| 3 | input | `\\fileserver\crm\orders_export.csv`, csv with header; all `V_String(254)`: `ORDER_ID, CUST_ID, AMOUNT, ORDER_DATE` |
| 4 | select | `ORDER_ID` Int32, `CUST_ID` Int32, `AMOUNT` Double, `ORDER_DATE` Date |
| 5 | join | `CUST_ID` = `CUST_ID`; in J deselect `Right_CUST_ID`; L → 7, J → 6, R → 8 |
| 6 | formula | `MATCH_FLAG` V_String(12) = `"MATCHED"` |
| 7 | formula | `MATCH_FLAG` = `"NO_ORDERS"` |
| 8 | formula | `MATCH_FLAG` = `"ORPHAN"` |
| 9 | union | by name; inputs in order 6, 7, 8 |
| 10 | output | `odbc:DSN=DW_SALES;UID=etl_user;PWD=__EncPwd1__\|\|\|dbo.CUSTOMER_ORDER_FACT`, `Append Existing` |
| 11 | browse | after 9 |

`targets_before/<set>/CUSTOMER_ORDER_FACT.csv` holds two pre-existing rows. Data must include customers with no orders, orders with unknown customers, duplicate `CUST_ID` on the order side, and a customer `NAME` with surrounding spaces and mixed case.

**wf_0003 — GL period close** (`gl_period_close.yxmd`, AMP). Constants `User.Region` = `EMEA`, `User.PeriodEnd` = `2026-08-31`. Container 100 "Extract" holds 1–3 and container 200 "Publish" holds 9–10, so the file has 12 nodes. With the sample's `min_tools: 3` this yields `seg_01` = 1–3 and `seg_02` = 4–10: ordering protection pulls 9 into the 4–8 chain (its `Last` depends on that order) and the lone tool 10 then merges across the soft cut.

| Id | Tool | Config |
|----|------|--------|
| 1 | input | `odbc:DSN=PROD_FIN;UID=svc_alx;PWD=__EncPwd2__\|\|\|` + CDATA `SELECT ACCT, PERIOD, POSTED, AMOUNT, REGION, ENTRY_ID FROM dbo.GL_LEDGER WHERE AMOUNT <> 0`. Fields: `ACCT V_String(20), PERIOD V_String(7), POSTED V_String(10), AMOUNT FixedDecimal(19,2), REGION V_String(10), ENTRY_ID Int64` |
| 2 | filter | `[REGION] = [User.Region]` — T → 3 |
| 3 | datetime | `POSTED` with `%d/%m/%Y` → `POSTED_DT` |
| 4 | sort | `ACCT` asc, `POSTED_DT` asc, `ENTRY_ID` desc |
| 5 | unique | `ACCT, POSTED_DT` — U → 6 |
| 6 | multi_row_formula | new `RUN_BAL` Double = `[Row-1:RUN_BAL] + [AMOUNT]`, group by `ACCT`, rows that don't exist = 0 |
| 7 | record_id | `RecordID` Int32 from 1, first position |
| 8 | formula | `PERIOD_END_FLAG` V_String(1) = `IIF(DateTimeFormat([POSTED_DT], "%Y-%m-%d") = [User.PeriodEnd], "Y", "N")` |
| 9 | summarize | GroupBy `ACCT`, `PERIOD`; Sum `AMOUNT` → `TOTAL`; Last `RUN_BAL` → `CLOSING_BAL`; Count → `ENTRIES`; Max `PERIOD_END_FLAG` → `HAS_PERIOD_END` |
| 10 | output | `odbc:DSN=PROD_FIN;…\|\|\|dbo.GL_SUMMARY`, `Update; Insert if new`, keys `ACCT, PERIOD`; PreSQL `DELETE FROM dbo.GL_SUMMARY WHERE PERIOD < '2020-01'`; PostSQL `UPDATE dbo.GL_SUMMARY SET LOADED_FLAG = 'Y' WHERE LOADED_FLAG IS NULL` |

`targets_before/<set>/GL_SUMMARY.csv` has columns `ACCT, PERIOD, TOTAL, CLOSING_BAL, ENTRIES, HAS_PERIOD_END, LOADED_FLAG` with one row to be updated, one untouched, and one with `PERIOD` `2019-12` that the PreSQL removes. Data needs duplicate `(ACCT, POSTED)` pairs with different `ENTRY_ID`, an unparseable `POSTED` (`31/02/2026`), a leap day, and a NULL `REGION`.

**wf_0004 — inventory with macro** (`inventory.yxmd`, E1).

| Id | Tool | Config |
|----|------|--------|
| 1 | input | `C:\data\wh\stock.yxdb`: `SKU V_String(30), WAREHOUSE V_String(10), QTY Int32, NOTE V_String(100)` |
| 2 | macro | `Supporting_Macros\clean_codes.yxmc`, `MinQty` = `1` |
| 3 | regex | parse `SKU` with `^([A-Z]+)-(\d+)$` → `FAMILY V_String(20)`, `ITEM_NO V_String(10)` |
| 4 | cross_tab | group `SKU, FAMILY`; header `WAREHOUSE`; data `QTY`; Sum. Frozen headers in MetaInfo: `EAST, NORTH, WEST` (Double) |
| 5 | transpose | keys `SKU`; data `EAST, NORTH, WEST` |
| 6 | output | `C:\data\out\inventory_by_wh.yxdb` (from 4) |
| 7 | output | `C:\data\out\inventory_long.yxdb` (from 5) |

Macro tools: 1 `macro_input` (same four fields); 2 `regex` replace on `SKU`, `^\s*([A-Za-z]+)[- ]?(\d+)\s*$` → `$1-$2`, case-insensitive, copy unmatched; 3 `formula` `SKU` = `Uppercase([SKU])`; 4 `filter` `[QTY] >= [%Question.MinQty%]`, T → 5; 5 `macro_output`; 10 `interface` `AlteryxGuiToolkit.Questions.NumericUpDown.NumericUpDown` named `MinQty`, default `0`. Data: SKUs such as `ab 12`, ` AB-12 `, `zz9`, `bad sku!`; `QTY` 0 and NULL; several rows per SKU and warehouse.

**wf_0005 — vendor tool** (`vendor_dedupe.yxmd`, E1): 1 input `C:\data\vendor\accounts.yxdb` (`ACCT V_String(20), UPDATED Date, BALANCE Double`); 2 plugin `AcmeAnalytics.Dedupe.DedupeTool` with `<KeyField>ACCT</KeyField><Keep>MaxDate</Keep><DateField>UPDATED</DateField>` and `<EngineSettings EngineDll="AcmeDedupe.dll" EngineDllEntryPoint="AcmeDedupe"/>`; 3 run_command `C:\scripts\notify.bat`; 4 output `C:\data\out\accounts_clean.yxdb`. `expected_terminal` is `MANUAL`. Only `normal` and `empty` sets are needed.

Edge sets for wf_0001–0004 include: NULL in every column, exact duplicate rows, non-ASCII text, a string at maximum length, `2024-02-29`, `-0.0`, and `0.005`-style rounding boundaries.

- [ ] **Step 1:** Write `tests/test_samples_wellformed.py`: for every `samples/wf_*`, assert each `source/*.yx*` parses with `xml.etree`, the root is `AlteryxDocument` with `yxmdVer`, ToolIDs are unique per file, every `<Connection>` endpoint exists, `sample.json` has the keys above, and each golden input CSV loads with `typed_csv.read_table` and matches its input tool's `MetaInfo` field names. For `empty` sets assert zero rows.
- [ ] **Step 2:** Run it. Expected: FAIL, no samples.
- [ ] **Step 3:** Author the files.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat: five synthetic Alteryx sample workflows with golden inputs`.

---

### Task 4: Parser, extension registry, invariants, corpus

**Files:** Create `scripts/parse.py`, `scripts/invariants.py`, `scripts/parsers/__init__.py`, `scripts/parsers/plugin_map.py`, `scripts/parsers/registry.py`, `scripts/parsers/tool_config.py`, `scripts/parsers/ext/__init__.py`, `scripts/parsers/ext/README.md`; fixtures `tests/parser_corpus/{bom,cp1252,yxzp,yxwz,locked,nested_containers}/`; Tests `tests/test_parse.py`, `tests/test_invariants.py`, `tests/parser_corpus/test_corpus.py`.

**Interfaces — Consumes:** `lib.paths.Repo`, `lib.io`. **Produces:**
```python
# scripts/parsers/registry.py
def register_plugin(name: str, handler: Callable[[ET.Element, dict], dict]) -> None
    # handler(node_xml, node) returns keys to merge: any of type, config, in_anchors, out_anchors, behavior, confidence
def register_element(tag: str, handler: Callable[[ET.Element, dict], None]) -> None   # document-level; mutates dag
def load_extensions(dirs: Iterable[Path]) -> list[str];  def reset() -> None
# scripts/parsers/plugin_map.py
PLUGIN_TYPES: dict[str, str]; BUILTIN_MACROS = {"cleanse.yxmc": "data_cleansing"}
ANCHORS: dict[str, dict]   # type -> {"in": [...], "out": {xml_name: canonical}}
def classify(plugin: str | None, macro: str | None) -> str
# scripts/parsers/tool_config.py
def parse_config(tool_type: str, configuration: ET.Element) -> dict      # dag-contract §4
def scrub(text: str) -> tuple[str, list[str]]                            # (clean text, aliases), dag-contract §6
# scripts/parse.py
def decode_xml(raw: bytes) -> str                       # BOM, declared encoding, else utf-8, else cp1252
def parse_file(path: Path, ext_dirs: Sequence[Path] = ()) -> tuple[dict, list[str]]   # (dag, scrubbed aliases)
def run(repo: Repo, wf_id: str, check: bool) -> dict    # writes parsed/dag.json, parsed/parse_report.json; returns the report
# scripts/invariants.py
def check(xml_text: str, dag: dict) -> list[str]
```
CLI: `python scripts/parse.py <wf_id> [--check] [--root .]`. Extensions load from `scripts/parsers/ext/` beside the script and from `<root>/scripts/parsers/ext/` when that is a different directory. `.yxzp` in `source/` is unzipped in place first. A document with no `<Nodes>` and a `<Locked>` or `<EncryptedNodes>` element reports `QUARANTINED` with reason `locked`. An exception reports `FAILED` with the message. On success the source file is rewritten with scrubbed text. `parse_report.json`: program schema plus `unknown_share` and `scrubbed_aliases`; status `PARSED`, `INVARIANT_VIOLATION`, `FAILED` or `QUARANTINED`; exit 0 only for `PARSED`. If the existing report says `RECOVERED` and the parse is clean, keep `RECOVERED`, its `attempt` and `extension`.

`invariants.check` is the program spec's function (§6.3) with `nodes` keyed by `str(tool_id)` throughout, plus: (7) every `join` has exactly one inbound edge on `Left` and one on `Right`; (8) `unknown` nodes lacking a `behavior` key are at most 10% of data nodes. Invariant 5 reads `config["source"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_parse.py
import shutil
from pathlib import Path
import pytest
import parse, invariants
from parsers import registry

SAMPLES = Path(__file__).parents[1] / "samples"

def dag_of(wf, name):
    dag, _ = parse.parse_file(SAMPLES / wf / "source" / name)
    return dag, {n["tool_id"]: n for n in dag["nodes"]}

def test_filter_anchors_and_formula_order():
    dag, n = dag_of("wf_0001", "sales_summary.yxmd")
    assert dag["engine"] == "AMP" and dag["yxmd_version"] == "2023.1"
    assert n["3"]["type"] == "filter" and n["3"]["out_anchors"] == ["T", "F"]
    assert {"src": "3", "src_anchor": "F", "dst": "8", "dst_anchor": "Input"}.items() <= next(e for e in dag["edges"] if e["dst"] == "8").items()
    assert [f["field"] for f in n["4"]["config"]["formulas"]] == ["AMOUNT", "NET", "SIZE_BAND"]
    assert n["2"]["config"]["fields"][0] == {"name": "CUSTOMER", "selected": True, "rename": None, "type": "String", "size": 10}
    assert n["1"]["meta"]["Output"][0] == {"name": "ORDER_ID", "type": "Int32", "size": 4, "scale": None}

def test_nested_containers_and_cleanse_macro():
    dag, n = dag_of("wf_0002", "customer_orders.yxmd")
    assert n["3"]["container_id"] == "210" and n["210"]["container_id"] == "200" and n["1"]["container_id"] == "100"
    assert n["2"]["type"] == "data_cleansing" and n["2"]["config"]["modify_case"] == "upper"
    assert n["300"]["type"] == "comment"
    assert sorted(e["src_anchor"] for e in dag["edges"] if e["src"] == "5") == ["J", "L", "R"]
    assert [e["src"] for e in sorted((e for e in dag["edges"] if e["dst"] == "9"), key=lambda e: e["dst_order"])] == ["6", "7", "8"]

def test_credentials_are_scrubbed_everywhere():
    dag, n = dag_of("wf_0003", "gl_period_close.yxmd")
    _, aliases = parse.parse_file(SAMPLES / "wf_0003" / "source" / "gl_period_close.yxmd")
    blob = str(dag)
    assert "svc_alx" not in blob and "__EncPwd" not in blob and aliases == ["prod_fin"]
    assert n["1"]["config"]["source"] == "<scrubbed:prod_fin>" and n["1"]["config"]["alias"] == "prod_fin"
    assert n["1"]["config"]["query"].startswith("SELECT ACCT")
    assert dag["constants"] == {"User.Region": "EMEA", "User.PeriodEnd": "2026-08-31"}
    assert n["10"]["config"]["write_mode"] == "update_insert" and n["10"]["config"]["keys"] == ["ACCT", "PERIOD"]
    assert n["10"]["config"]["pre_sql"].startswith("DELETE") and n["10"]["config"]["table"] == "dbo.GL_SUMMARY"

def test_macro_is_parsed_recursively():
    _, n = dag_of("wf_0004", "inventory.yxmd")
    m = n["2"]
    assert m["type"] == "macro" and m["macro_path"] == "Supporting_Macros/clean_codes.yxmc"
    assert m["interface"] == [{"name": "MinQty", "type": "NumericUpDown", "default": "0"}]
    assert m["config"]["values"] == {"MinQty": "1"} and len(m["sub_dag"]["nodes"]) == 6

def test_missing_macro_is_flagged_not_dropped(tmp_path):
    shutil.copy(SAMPLES / "wf_0004" / "source" / "inventory.yxmd", tmp_path / "inventory.yxmd")
    dag, _ = parse.parse_file(tmp_path / "inventory.yxmd")
    m = next(x for x in dag["nodes"] if x["tool_id"] == "2")
    assert m["unresolved"] is True and m["sub_dag"] is None

def test_unknown_plugin_trips_the_share_invariant_until_an_extension_explains_it(tmp_path):
    src = SAMPLES / "wf_0005" / "source" / "vendor_dedupe.yxmd"
    dag, _ = parse.parse_file(src)
    errs = invariants.check(src.read_text(encoding="utf-8"), dag)
    assert any("unknown" in e for e in errs)
    ext = tmp_path / "ext"; ext.mkdir()
    (ext / "acme.py").write_text(
        "from parsers.registry import register_plugin\n"
        "register_plugin('AcmeAnalytics.Dedupe.DedupeTool', lambda x, n: {'type': 'unknown', 'in_anchors': ['Input'],"
        " 'out_anchors': ['Output'], 'behavior': 'appears to dedupe on ACCT keeping max UPDATED', 'confidence': 0.6})\n")
    try:
        dag2, _ = parse.parse_file(src, ext_dirs=[ext])
        assert invariants.check(src.read_text(encoding="utf-8"), dag2) == []
    finally:
        registry.reset()

def test_run_writes_report_and_scrubs_source(tmp_path):
    from lib.paths import Repo
    repo = Repo(tmp_path)
    dest = repo.wf("wf_0003", "source"); dest.mkdir(parents=True)
    shutil.copy(SAMPLES / "wf_0003" / "source" / "gl_period_close.yxmd", dest)
    report = parse.run(repo, "wf_0003", check=True)
    assert report["status"] == "PARSED" and report["scrubbed_aliases"] == ["prod_fin"] and report["node_count"] == 12
    assert "__EncPwd" not in (dest / "gl_period_close.yxmd").read_text(encoding="utf-8")
```
`tests/test_invariants.py` builds a minimal valid dag by hand and asserts one failing case per invariant 1–8 (missing node, dangling edge, bad anchor, `type: None`, macro without path, empty input source, cycle, join with one input, unknown share). `tests/parser_corpus/test_corpus.py` parametrizes over fixture directories: `bom`, `cp1252` (contains byte `0xE9` in an annotation and no encoding declaration), `yxwz` and `nested_containers` must parse with `invariants.check == []`; `yxzp` must unzip then parse; `locked` must report `QUARANTINED`.

- [ ] **Step 2:** Run all three files. Expected: FAIL on import.
- [ ] **Step 3:** Implement. Recurse `<ChildNodes>` carrying the container's ToolID. Resolve macro paths relative to the workflow file, then each `ext_dirs` parent; normalize `\` to `/`. Apply registered plugin handlers before `classify`. Build `in_anchors`/`out_anchors` for `unknown` nodes from the connections that touch them.
- [ ] **Step 4:** Run. Expected: all pass.
- [ ] **Step 5:** Commit `feat: Alteryx XML parser with extension registry, invariants and corpus`.

---

### Task 5: `scripts/segment.py`

**Files:** Create `scripts/segment.py`; Test `tests/test_segment.py`.

**Interfaces — Consumes:** `parsed/dag.json`; `manifest["segmentation"]` if present. **Produces:**
```python
def segment(dag: dict, *, min_tools: int = 15, max_tools: int = 40, formula_heavy_cap: int = 20) -> dict
    # {"segments": {"seg_01": ["1","2"], …}, "order": [["seg_01","seg_02"],["seg_03"]], "warnings": [str], "params": {...}}
def build_segment_dag(dag: dict, seg: str, members: list[str], owner: dict[str, str]) -> dict     # contract C7
def run(repo: Repo, wf_id: str, **overrides) -> dict
```
CLI: `python scripts/segment.py <wf_id> [--min-tools N] [--max-tools N] [--root .]`. Writes `segments/seg_NN/dag.json`, `segments/order.json`, `segments/segmentation.json`; sets `manifest.segments`.

**Algorithm** (deterministic; iterate tool ids in numeric order everywhere):
1. Data nodes are nodes whose type is not in `NON_DATA_TYPES`. `browse` nodes join their upstream node's group and never count toward size.
2. *Hard* cut edges touch a `macro` node. *Soft* cut edges join nodes whose outermost containers differ (`None` is its own value). Union-find all other edges.
3. Ordering protection: from each node in `ORDER_DEPENDENT_TYPES`, and each `summarize` using `First` or `Last`, walk upstream through single-input nodes until a `sort` or a source; union the whole path, overriding soft cuts. If the path hits a hard cut, add warning `ordering dependency of tool <id> crosses macro <id>`.
4. Join protection: a `join`'s input edges are *protected* — step 6 may never split there. Container cuts still win (they are the author's grouping and the program spec's first cut priority), so this step unions nothing.
5. Merge: while some group has fewer than `min_tools` data nodes and a neighbour across a soft cut exists such that the merged size ≤ `max_tools` and the group graph stays acyclic, merge it with the smallest such neighbour (ties: lowest tool id).
6. Split: a group above `max_tools` (or above `formula_heavy_cap` when more than half its nodes are `formula`/`multi_row_formula`) is split at the bridge edge that best balances the halves without undoing steps 3–4; repeat. If none exists, keep it and warn.
7. Number groups in topological order (ties by lowest tool id) as `seg_01…`; `order` is the list of topological levels.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_segment.py
from pathlib import Path
import parse, segment
SAMPLES = Path(__file__).parents[1] / "samples"
def seg_of(wf, name, **kw):
    dag, _ = parse.parse_file(SAMPLES / wf / "source" / name)
    return dag, segment.segment(dag, **kw)
def owner(result): return {t: s for s, ts in result["segments"].items() for t in ts}

def test_small_workflow_collapses_to_one_segment_by_default():
    _, r = seg_of("wf_0001", "sales_summary.yxmd")
    assert list(r["segments"]) == ["seg_01"] and r["order"] == [["seg_01"]]

def test_containers_cut_and_waves_are_topological():
    _, r = seg_of("wf_0002", "customer_orders.yxmd", min_tools=2)
    o = owner(r)
    assert o["1"] == o["2"] and o["3"] == o["4"] and o["1"] != o["3"]
    assert o["5"] not in (o["1"], o["3"]) and len({o[t] for t in ("5", "6", "7", "8", "9", "10")}) == 1
    assert r["order"] == [sorted([o["1"], o["3"]]), [o["5"]]]                # two prep segments in parallel, then the join
    assert "11" in r["segments"][o["9"]]                                     # browse rides with its upstream

def test_ordering_chain_is_never_split():
    _, r = seg_of("wf_0003", "gl_period_close.yxmd", min_tools=2, max_tools=4)
    o = owner(r)
    assert len({o[t] for t in ("4", "5", "6", "7")}) == 1
    assert len({o[t] for t in ("9",)} | {o["6"]}) == 1                       # summarize Last depends on the same order

def test_macro_is_its_own_segment():
    _, r = seg_of("wf_0004", "inventory.yxmd", min_tools=10)
    assert ["2"] in r["segments"].values()

def test_segment_dag_lists_inbound_and_outbound():
    dag, r = seg_of("wf_0004", "inventory.yxmd", min_tools=10)
    o = owner(r); seg = o["2"]
    sd = segment.build_segment_dag(dag, seg, r["segments"][seg], o)
    assert sd["inbound"][0]["src"] == "1" and sd["inbound"][0]["from_segment"] == o["1"]
    assert sd["outbound"][0]["dst"] == "3" and sd["outbound"][0]["to_segment"] == o["3"] and sd["edges"] == []

def test_oversized_group_is_split_at_a_bridge():
    nodes = [{"tool_id": str(i), "type": "formula", "container_id": None} for i in range(1, 31)]
    edges = [{"src": str(i), "src_anchor": "Output", "dst": str(i + 1), "dst_anchor": "Input"} for i in range(1, 30)]
    r = segment.segment({"workflow": "x", "nodes": nodes, "edges": edges}, min_tools=5, max_tools=40, formula_heavy_cap=20)
    assert len(r["segments"]) == 2 and all(len(v) <= 20 for v in r["segments"].values())
```

- [ ] **Step 2:** Run. Expected: FAIL on import.
- [ ] **Step 3:** Implement the algorithm.
- [ ] **Step 4:** Run. Expected: 6 passed.
- [ ] **Step 5:** Commit `feat: deterministic DAG segmentation with ordering and join protection`.
