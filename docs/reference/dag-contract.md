# dag.json contract — Alteryx XML shapes and their parsed form

The parser writes this, and the segmenter, intake scripts, simulator and agents read it. It extends
[the program schema](../spec/02-schemas-reference.md#dagjson) with a fixed `config` shape per tool type.
Sample workflows under `samples/` are hand-written to these XML shapes. They follow Alteryx Designer's
file layout as closely as we know it, but **no file here was produced or opened by Alteryx**.

## 1. Node

```json
{ "tool_id": "12", "type": "input", "plugin": "AlteryxBasePluginsGui.DbFileInput.DbFileInput",
  "container_id": null, "config": {}, "raw_config": "<Configuration>…</Configuration>",
  "annotation": "GL extract", "in_anchors": [], "out_anchors": ["Output"],
  "meta": { "Output": [ { "name": "ACCT", "type": "V_String", "size": 20, "scale": null } ] } }
```

`tool_id` is always a string. `container_id` is the ToolID of the enclosing Tool Container or `null`.
`meta` comes from `<MetaInfo connection="…"><RecordInfo>`; its keys are **canonical** anchor names.
Sink tools (`output`, `browse`) have no out anchor but may carry `<MetaInfo connection="Output">` describing the
records they receive; the parser keeps it as `meta["Output"]` rather than dropping it, because intake needs a
field list for outputs whose target does not exist yet. A field's `source` attribute is ignored.

## 2. Plugin → type, and anchors

| Plugin (GuiSettings `Plugin`) | `type` | in anchors | out anchors (XML → canonical) |
|---|---|---|---|
| `AlteryxBasePluginsGui.DbFileInput.DbFileInput` | `input` | — | Output |
| `AlteryxBasePluginsGui.DbFileOutput.DbFileOutput` | `output` | Input | — |
| `AlteryxBasePluginsGui.AlteryxSelect.AlteryxSelect` | `select` | Input | Output |
| `AlteryxBasePluginsGui.Filter.Filter` | `filter` | Input | True → `T`, False → `F` |
| `AlteryxBasePluginsGui.Formula.Formula` | `formula` | Input | Output |
| `AlteryxBasePluginsGui.Join.Join` | `join` | Left, Right | Left → `L`, Join → `J`, Right → `R` |
| `AlteryxBasePluginsGui.Union.Union` | `union` | Input (many) | Output |
| `AlteryxSpatialPluginsGui.Summarize.Summarize` | `summarize` | Input | Output |
| `AlteryxBasePluginsGui.Sort.Sort` | `sort` | Input | Output |
| `AlteryxBasePluginsGui.Unique.Unique` | `unique` | Input | Unique → `U`, Duplicates → `D` |
| `AlteryxBasePluginsGui.Sample.Sample` | `sample` | Input | Output |
| `AlteryxBasePluginsGui.RecordID.RecordID` | `record_id` | Input | Output |
| `AlteryxBasePluginsGui.MultiRowFormula.MultiRowFormula` | `multi_row_formula` | Input | Output |
| `AlteryxBasePluginsGui.CrossTab.CrossTab` | `cross_tab` | Input | Output |
| `AlteryxBasePluginsGui.Transpose.Transpose` | `transpose` | Input | Output |
| `AlteryxBasePluginsGui.RegEx.RegEx` | `regex` | Input | Output |
| `AlteryxBasePluginsGui.DateTime.DateTime` | `datetime` | Input | Output |
| `AlteryxBasePluginsGui.AppendFields.AppendFields` | `append_fields` | Targets, Source | Output |
| `AlteryxBasePluginsGui.BlockUntilDone.BlockUntilDone` | `block_until_done` | Input | Output1, Output2, Output3 |
| `AlteryxBasePluginsGui.BrowseV2.BrowseV2` | `browse` | Input | — |
| `AlteryxBasePluginsGui.RunCommand.RunCommand` | `run_command` | Input | Output |
| `AlteryxBasePluginsGui.PythonTool.PythonTool` | `python` | Input (connections #1..#n, ordered) | 1..5 (Output1..Output5) — verify the plugin id against your Alteryx version |
| `AlteryxBasePluginsGui.MacroInput.MacroInput` | `macro_input` | — | Output |
| `AlteryxBasePluginsGui.MacroOutput.MacroOutput` | `macro_output` | Input | — |
| `AlteryxBasePluginsGui.Action.Action` | `action` | — | — |
| `AlteryxGuiToolkit.Questions.*` (prefix) | `interface` | — | — |
| `AlteryxGuiToolkit.ToolContainer.ToolContainer` | `container` | — | — |
| `AlteryxGuiToolkit.TextBox.TextBox` | `comment` | — | — |
| no Plugin, `<EngineSettings Macro="Cleanse.yxmc">` | `data_cleansing` | Input | Output |
| no Plugin, `<EngineSettings Macro="…other…">` | `macro` | from macro's `macro_input` tools | from `macro_output` tools |
| anything else | `unknown` | as seen in `<Connections>` | as seen in `<Connections>` |

`container`, `comment`, `interface` and `action` nodes carry no data. They appear in `nodes` (invariant 1
requires every `<Node>`), never in `edges`, and are ignored by segment size counts.

Tier hints: `run_command` and any `unknown` that an analyzer marks manual are T3.

## 3. Edge

`{ "src": "12", "src_anchor": "Output", "dst": "20", "dst_anchor": "Left", "wireless": false }`
Anchors are canonical on both sides. Union inputs arrive as `Input`; Alteryx's `#1`, `#2` suffix is written as the Destination's `name` attribute
(`<Destination ToolID="9" Connection="Input" name="#2"/>`) and becomes the edge's `dst_order` integer (1-based).
Every other edge has `dst_order` 1.

## 4. `config` per type

XML is abbreviated; attribute spellings are the ones the parser must accept.

**input** — `<File FileFormat="19">C:\data\sales\orders.yxdb</File>`; FileFormat `19` yxdb, `0` csv,
`25` xlsx, `23` ODBC/OleDB. DB form: `odbc:DSN=PROD_FIN;UID=svc_alx;PWD=__EncPwd1__|||SELECT … ` or alias
`aka:PROD_ORACLE|||SELECT …`; the query may sit in CDATA.
```json
{ "source": "C:\\data\\sales\\orders.yxdb", "format": "yxdb", "query": null, "alias": null,
  "csv": null, "record_limit": null }
```
For DB inputs `source` is `"<scrubbed:prod_fin>"`, `alias` is `"prod_fin"` (the DSN or aka name,
lower-cased), `format` is `"db"`, and `query` holds the SQL verbatim. csv adds
`"csv": {"delimiter": ",", "header": true, "codepage": "28591"}`.

**output** — same `<File>` forms, plus `<FormatSpecificOptions><OutputOption>`, `<PreSQL>`, `<PostSQL>`,
`<UpdateKeys><Field field="ACCT"/></UpdateKeys>` (our simplification of Alteryx's key picker). In the samples
`PreSQL`, `PostSQL` and `UpdateKeys` are direct children of `<Configuration>`; real Alteryx nests the first two
inside `<FormatSpecificOptions>`. The parser must find all four wherever they sit (search with `.//`).
```json
{ "source": "<scrubbed:prod_fin>", "format": "db", "alias": "prod_fin", "table": "dbo.GL_SUMMARY",
  "write_mode": "update_insert", "keys": ["ACCT", "PERIOD"], "pre_sql": "…", "post_sql": "…" }
```
`write_mode` from OutputOption: `Overwrite`/`Create New Table`/`Overwrite Table (Drop)` → `overwrite`;
`Append Existing` → `append`; `Update; Insert if new` → `update_insert`; `Delete Data & Append` →
`truncate_append`. File outputs default to `overwrite`; `table` is `null` for files.

**select** — `<SelectFields><SelectField field="CUST" selected="True" rename="CUSTOMER" type="String" size="10"/>
<SelectField field="*Unknown" selected="True"/></SelectFields>`
```json
{ "fields": [ { "name": "CUST", "selected": true, "rename": "CUSTOMER", "type": "String", "size": 10 } ],
  "unknown_selected": true }
```
Missing `rename`/`type`/`size` attributes are `null`. Field order in `fields` is the output order.

**filter** — `<Expression>[AMOUNT] &gt; 100</Expression><Mode>Custom</Mode>` → `{ "expression": "[AMOUNT] > 100" }`.
Rows where the expression is NULL go to `F`.

**formula** — `<FormulaFields><FormulaField field="NET" expression="[AMOUNT]*0.9" type="Double" size="8"/>`
→ `{ "formulas": [ { "field": "NET", "expression": "[AMOUNT]*0.9", "type": "Double", "size": 8 } ] }`.
Formulas run in order; a later one sees an earlier one's result. Expressions are kept verbatim.

**join** — `<JoinInfo connection="Left"><Field field="CUST_ID"/></JoinInfo>` and the same for Right, then
`<SelectConfiguration><Configuration outputConnection="Join"><SelectFields>…`.
```json
{ "keys": [ { "left": "CUST_ID", "right": "CUST_ID" } ],
  "select": { "fields": [ { "name": "Right_CUST_ID", "selected": false, "rename": null, "type": null, "size": null } ],
              "unknown_selected": true } }
```
In the `J` output a right-side field whose name collides with a left field is named `Right_<name>`
before `select` applies. `L` and `R` outputs carry their own side's fields untouched.

**union** — `<Mode>ByName</Mode>` or `ByPos` → `{ "mode": "name" }`. Output field order is the first
input's; by name, fields missing from an input are NULL; inputs are concatenated in `dst_order`.

**summarize** — `<SummarizeField field="REGION" action="GroupBy" rename="REGION"/>`
```json
{ "fields": [ { "field": "REGION", "action": "GroupBy", "rename": "REGION", "separator": null } ] }
```
Actions: `GroupBy Sum Count CountNonNull CountDistinct Min Max Avg First Last Concat`. `Concat` reads the
`sep` attribute into `separator`. Output rows are ordered by the group-by fields ascending.

**sort** — `<SortInfo><Field field="ACCT" order="Ascending"/>` → `{ "fields": [ { "field": "ACCT", "order": "asc" } ] }`.
NULL sorts first ascending. The sort is stable.

**unique** — `<UniqueFields><Field field="ACCT"/>` → `{ "fields": ["ACCT"] }`. First row in incoming order
goes to `U`, the rest to `D`. Comparison is case-sensitive.

**sample** — `<Mode>First</Mode><N>1</N><GroupFields><Field name="ACCT"/>` →
`{ "mode": "first", "n": 1, "group_by": ["ACCT"] }`. Modes: `first last skip one_in_n`.

**record_id** — `<FieldName>RecordID</FieldName><StartValue>1</StartValue><FieldType>Int32</FieldType><Position>0</Position>`
→ `{ "field": "RecordID", "start": 1, "type": "Int32", "position": "first" }` (`Position` 0 first, 1 last).

**multi_row_formula** — `<UpdateField value="False"/><CreateField_Name>RUN_BAL</CreateField_Name>
<CreateField_Type>Double</CreateField_Type><CreateField_Size>8</CreateField_Size><OtherRows>0</OtherRows>
<NumRows value="1"/><Expression>[Row-1:RUN_BAL] + [AMOUNT]</Expression><GroupByFields><Field field="ACCT"/>`
```json
{ "field": "RUN_BAL", "update_existing": false, "type": "Double", "size": 8, "num_rows": 1,
  "expression": "[Row-1:RUN_BAL] + [AMOUNT]", "group_by": ["ACCT"], "unknown_rows": "zero" }
```
`OtherRows`: `NULL` → `"null"`, `0` → `"zero"`, `Nearest` → `"nearest"`. With `update_existing` the name
comes from `<UpdateField_Name>`.

**cross_tab** — `<GroupFields><Field field="SKU"/></GroupFields><HeaderField field="WAREHOUSE"/>
<DataField field="QTY"/><Methods><Method method="Sum"/></Methods>`
→ `{ "group_by": ["SKU"], "header_field": "WAREHOUSE", "data_field": "QTY", "methods": ["Sum"] }`.
Output header columns are the distinct header values sorted ascending, with every character outside
`[A-Za-z0-9_]` replaced by `_`. The frozen header list for translation is `meta.Output`.

**transpose** — `<KeyFields><Field field="SKU"/></KeyFields><DataFields><Field field="EAST" selected="True"/>`
→ `{ "key_fields": ["SKU"], "data_fields": ["EAST", "WEST"] }`. Output columns: keys, `Name`, `Value`.
Rows are emitted per input row, data fields in configured order. NULL values are kept.

**regex** — `<Field>SKU</Field><RegExExpression value="^([A-Z]+)-(\d+)$"/><CaseInsensitve value="True"/>
<Method>Replace</Method><Replace expression="$1"><CopyUnmatched value="True"/></Replace>` (Alteryx really
spells it `CaseInsensitve`).
```json
{ "field": "SKU", "expression": "^([A-Z]+)-(\\d+)$", "case_insensitive": true, "method": "replace",
  "replace": "$1", "copy_unmatched": true, "output_fields": [], "match_field": null }
```
`method` `parse` reads `<ParseFields><Field field="FAMILY" type="V_String" size="20"/>` into `output_fields`;
`match` reads `<Match><Field>SKU_Matched</Field>` into `match_field`. `replace` overwrites `field` in place.

**datetime** — `<IsFrom value="False"/><InputFieldName>POSTED</InputFieldName><Format>%d/%m/%Y</Format>
<OutputFieldName>POSTED_DT</OutputFieldName>` → `{ "direction": "to_datetime", "field": "POSTED",
"format": "%d/%m/%Y", "out_field": "POSTED_DT" }`. `IsFrom` True means `to_string`. Unparseable → NULL.

**data_cleansing** — macro `Cleanse.yxmc`; values live in `<Configuration><Value name="Check Box (135)">True</Value>`.
The parser maps the known names:
```json
{ "fields": ["NAME", "CITY"], "replace_null_strings_blank": true, "replace_null_numeric_zero": false,
  "trim_whitespace": true, "remove_tabs_linebreaks_dupspaces": false, "remove_all_whitespace": false,
  "remove_letters": false, "remove_numbers": false, "remove_punctuation": false, "modify_case": "upper" }
```
Value names: `List Box (11)` fields (comma list of `"NAME"` tokens), `Check Box (84)` null strings → blank,
`Check Box (117)` null numerics → 0, `Check Box (15)` trim, `Check Box (109)` tabs/linebreaks,
`Check Box (122)` all whitespace, `Check Box (53)` letters, `Check Box (58)` numbers, `Check Box (70)`
punctuation, `Check Box (77)` modify case with `Drop Down (81)` = `upper|lower|title`.

**macro** — `<EngineSettings Macro="Supporting_Macros\clean_codes.yxmc"/>`, values as
`<Configuration><Value name="Threshold">0.5</Value>`. Node gains `macro_path` (forward slashes),
`sub_dag` (the macro parsed recursively, same contract), `interface`
(`[{ "name": "Threshold", "type": "NumericUpDown", "default": "0.5" }]`), and
`config` = `{ "values": { "Threshold": "0.5" } }`. Unresolvable file → `"unresolved": true`, `sub_dag: null`.
Macro anchors are named after the macro's own tools: in anchor `Input<ToolID of the macro_input>`, out anchor
`Output<ToolID of the macro_output>` (e.g. `Input1`, `Output5`). The workflow's `<Connection>` elements use
the same names, and they are already canonical.
Inside a macro, an `interface` tool's value is referenced in formulas as `[#1]` via its `action` tool;
our sample macros instead reference the question by name as `[%Question.Threshold%]`, which the
simulator substitutes textually before evaluating.

**run_command** — `<Command>C:\scripts\post.bat</Command><CommandArguments>--x</CommandArguments>` →
`{ "command": "C:\\scripts\\post.bat", "args": "--x" }`.

**python** — `<Script>import pandas as pd\ndf = Alteryx.read("#1")\nAlteryx.write(df, 1)</Script>` →
`{ "script": "<the tool's code>" }` — this repo's samples keep the code in
`<Configuration><Script>`; a real Alteryx workflow stores a Jupyter notebook JSON, to be extracted
into the same `"script"` key. `Input` connections are ordered by `dst_order` from the connection's
`#1`, `#2`, … name, the same rule `union` uses (§3); a Python tool is always its own segment (§2, and
`scripts/segment.py`'s `is_hard`), so it becomes one Snowpark Python procedure rather than being
merged with neighbouring tools. The simulator runs this script in a sandbox
([simulator-semantics.md §7.1](simulator-semantics.md#71-the-python-tool)). **This is an accident
guard, not a security boundary: an allowed library can still reach the filesystem and load native
code (for example `DataFrame.to_csv`, or a submodule the allow-list admits by its top-level package
alone, such as `numpy.ctypeslib` or `pandas.io.common`); the simulator runs only this repository's
own committed sample scripts and must never be pointed at an untrusted workflow's Python tool.**

**unknown** — `config` is `{}`; `raw_config` is kept; an extension or the parser-recovery agent may add
`behavior` and `confidence`.

## 5. Document level

`workflow`, `yxmd_version` (`yxmdVer`), `engine` (`AMP` when a `<RunE2 value="True"/>` element exists anywhere under the document's `<Properties>`,
or the root has attribute `RunE2="T"`; else `E1`), `constants` (from `<Properties><Constants><Constant><Name>` /
`<Value>`), `file_kind` (`yxmd|yxwz|yxmc`), `source_file`.

## 6. Scrubbing

Any `PWD=…;`, `Password=…;`, `UID=…;`, `User ID=…;` value and anything inside `<Passwords>` is removed
from `config`, `raw_config` and any file written under `workflows/<id>/source/`. The connection string
is replaced by `<scrubbed:alias>`; aliases are listed in `parse_report.json.scrubbed_aliases`.

## 7. Alteryx field types

`Bool Byte Int16 Int32 Int64 FixedDecimal Float Double String WString V_String V_WString Date Time DateTime Blob SpatialObj`.
Snowflake mapping (program spec §8.3): Bool→BOOLEAN; Byte/Int*→NUMBER(38,0); FixedDecimal(p,s)→NUMBER(p,s);
Float/Double→FLOAT; String/WString/V_String/V_WString→VARCHAR; Date→DATE; Time→TIME; DateTime→TIMESTAMP_NTZ.
