# wf_0002 — intake plan

Customer orders fact load · owner `wf_owner` · schedule `0 6 * * 1-5` ·
source `customer_orders.yxmd`, `yxmdVer 2023.1`, E1 engine.

This plan restates `parsed/dag.json`, `intake/touchpoints.json` and `mappings/global.yaml`.
Nothing here has run on Alteryx or on Snowflake, and no count, diff or tolerance in this project
comes from an agent — those come only from `scripts/`.

## Tier

**T1 — pure SQL.** Eleven tools, three Tool Containers and a TextBox. Every tool that carries
data is one the cookbook has a SQL pattern for: two Input Data tools, a Data Cleansing macro
(`Cleanse.yxmc`, which the parser classifies from `<EngineSettings Macro="…">` and whose options
map one-to-one onto `COALESCE`/`TRIM`/`UPPER`), a Select, a Join, three Formula tools, a Union, a
Browse and one Output Data tool. Nothing needs Snowpark and nothing needs a human rewrite.

## Touchpoints and how they resolve

| Tool | Touchpoint | Resolution |
|------|-----------|------------|
| 1 | `\fileserver\crm\customers.yxdb` (key `crm/customers.yxdb`) | `CRM.RAW.CUSTOMERS`, logical `CUSTOMERS` — confirmed by the owner |
| 3 | `\fileserver\crm\orders_export.csv` (key `crm/orders_export.csv`) | `CRM.RAW.ORDERS_EXPORT`, logical `ORDERS_EXPORT` — confirmed |
| 10 | ODBC `DW_SALES`, table `dbo.CUSTOMER_ORDER_FACT` (key `alias:dw_sales/dbo.customer_order_fact`) | `ANALYTICS.CURATED.CUSTOMER_ORDER_FACT`, logical `CUSTOMER_ORDER_FACT`, write mode `append` — confirmed |

Tool 10's connection string carries credentials (`UID=etl_user;PWD=…`), which the parser scrubs to
`<scrubbed:dw_sales>` before anything is written under `workflows/`; only the alias survives, and
it is what the touchpoint is keyed on. The Output tool has no PreSQL, no PostSQL and no update
keys. There are no constants, no app parameters and no macros other than the Data Cleansing tool,
whose behaviour the parser reads from its `<Value name="…">` pairs rather than from a macro file.

Program-level answers all come from `mappings/global.yaml` and none had to be asked again.

## Expected segment cuts

Three segments in two waves: `[[seg_01, seg_02], [seg_03]]`.

| Segment | Tools | Why |
|---------|-------|-----|
| `seg_01` | 1, 2 | Tool Container 100 ("Prep customers") is a soft cut and holds exactly these two |
| `seg_02` | 3, 4 | Tool Container 210 ("Type the CSV export"), inside container 200, holds exactly these two |
| `seg_03` | 5, 6, 7, 8, 9, 10, 11 | everything downstream of the Join, which needs both prep streams |

`sample.json` sets `min_tools: 2` for this workflow, where the others use 3. That is deliberate: at
a floor of 3 both prep groups would fall under it and merge into the main group, collapsing the
workflow to one segment. At 2 they stand, and the workflow demonstrates a parallel wave feeding a
join segment. Tool 11 is a Browse, which carries no data out and never counts toward a segment's
size; containers 100, 200 and 210 and TextBox 300 carry no data at all.

`seg_01` and `seg_02` each have one outbound stream, materialised as
`MIG_WORK.WF0002_SEG_01_OUT` and `MIG_WORK.WF0002_SEG_02_OUT`; `seg_03` reads both at those
literal names.

## Unsupported or manual tools

None. `unsupported.json` is `{"tier": "T1", "unsupported": [], "unknown": []}`.

## Parity risks by tool id

| Tool | Risk | Why it matters |
|------|------|----------------|
| 2 | Data Cleansing option order | The macro replaces NULL strings, then trims, then changes case; nesting them the other way round changes what a NULL becomes |
| 2 | Unicode case folding | `UPPER` is Snowflake's, and its rules are not guaranteed to match Alteryx's for every script |
| 3 | CSV typing | Every column arrives as `V_String(254)`, so the source is entirely text and nothing may assume otherwise |
| 4 | Warn-and-null coercion | Text that is not a number, and a date that does not exist, must become NULL rather than raise |
| 5 | NULL join keys | A NULL key matches nothing, so those rows leave on `L` or `R` — an anti-join written with `NOT IN` would drop them all |
| 5 | Duplicate-name collision | The right side's `CUST_ID` arrives as `Right_CUST_ID` and the Join's own Select drops it |
| 5 | Fan-out | Duplicates on both sides multiply, and the append has to preserve every copy |
| 9 | Union by name | The output keeps input #1's field order and a field an input lacks arrives NULL; a union by position would misalign three different field sets |
| 10 | Write mode `append` | The target keeps what it already held, so the expected result includes the prior state, and columns are matched to it by name |

## Fix-loop budget

Three iterations, one more than `wf_0001`: the Join's three anchors and the Union's three field
sets are where a first pass most often loses rows, and the `append` write mode means a mistake
shows up mixed with the target's prior state.

## Dependencies, owner and consumers

No shared macros and no upstream workflow: both extracts are produced outside the Alteryx estate.
`manifest.json` records no consumers for the target. Owner `wf_owner`; the schedule
`0 6 * * 1-5` is the Alteryx Server schedule recorded at parse time.
