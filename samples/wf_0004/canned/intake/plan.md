# wf_0004 — intake plan

Inventory by warehouse · owner `wf_owner` · schedule `0 6 * * 1-5` ·
source `inventory.yxmd`, `yxmdVer 2023.1`, E1 engine, plus
`source/Supporting_Macros/clean_codes.yxmc`.

This plan restates `parsed/dag.json`, `intake/touchpoints.json` and `mappings/global.yaml`.
Nothing here has run on Alteryx or on Snowflake, and no count, diff or tolerance in this project
comes from an agent — those come only from `scripts/`.

## Tier

**T1 — pure SQL.** Every tool is one the cookbook has a SQL pattern for: Input Data, RegEx
(Replace and Parse), Formula, Filter, Cross Tab, Transpose and two Output Data tools. The macro at
tool 2 is not itself a tool with a pattern; it is a workflow, and it resolves — `macro_path`
`Supporting_Macros/clean_codes.yxmc` is on disk, `sub_dag` parsed, `unresolved` false — so every
tool that actually needs translating is inside it and all four are `sql`. Nothing needs Snowpark
and nothing needs a human rewrite.

An **unresolved** macro would have been a different answer: tier T3 and `NEEDS_HUMAN`, because the
pipeline never guesses what a macro does.

## Touchpoints and how they resolve

| Tool | Touchpoint | Resolution |
|------|-----------|------------|
| 1 | `C:\data\wh\stock.yxdb` (key `wh/stock.yxdb`) | `WH.RAW.STOCK`, logical `STOCK` — confirmed by the owner |
| 6 | `C:\data\out\inventory_by_wh.yxdb` (key `out/inventory_by_wh.yxdb`) | `ANALYTICS.CURATED.INVENTORY_BY_WH`, logical `INVENTORY_BY_WH`, write mode `overwrite` — confirmed |
| 7 | `C:\data\out\inventory_long.yxdb` (key `out/inventory_long.yxdb`) | `ANALYTICS.CURATED.INVENTORY_LONG`, logical `INVENTORY_LONG`, write mode `overwrite` — confirmed |
| 2 | macro `Supporting_Macros/clean_codes.yxmc` | resolved on disk and inlined; recorded under `macros:` in `intake/mappings.yaml` as `inline: true` |

There are no constants and no app parameters at workflow level. The macro has one **question**,
`MinQty`, a `NumericUpDown` whose own default is `0`; the workflow passes `1` through
`<Value name="MinQty">1</Value>` on tool 2. That value, not the default, is what the translation
must use — it is the one number in this workflow that lives in the caller rather than in the
callee.

Both Output tools are file targets, so neither has PreSQL, PostSQL or update keys, and neither has
a prior state to merge into.

Program-level answers all come from `mappings/global.yaml` and none had to be asked again: target
`ANALYTICS.CURATED`, work schema `MIG_WORK`, warehouse `MIG_WH`, owning role `MIGRATION_ROLE`,
`EXECUTE AS CALLER`, column-name policy `sanitize`, session `TIMEZONE America/New_York` and
`WEEK_START 1`, accepted diff classes `ROUNDING` and `ORDERING`.

## Expected segment cuts

Three segments in three waves: `seg_01` holding tool **1**, `seg_02` holding tool **2** (the
macro), and `seg_03` holding tools **3, 4, 5, 6, 7**.

`min_tools` is 3 and `max_tools` 40, and there are no Tool Containers. The rule that decides the
shape here is "give every macro its own segment": tool 2 cannot share a statement with the tools
around it, because what it contributes is a whole sub-workflow. That leaves tool 1 alone in
`seg_01`, below the floor — deliberately, since merging it forwards would put a workflow inside
another segment's statement and there is nothing behind it to merge with. Each of the first two
segments hands the next one work table.

## Unsupported or manual tools

None. `unsupported.json` is `{"tier": "T1", "unsupported": [], "unknown": []}`.

## Parity risks by tool id

| Tool | Risk | Why it matters |
|------|------|----------------|
| 2 | The question value overrides the macro's default | `MinQty` is `1` in the caller and `0` in `clean_codes.yxmc`; translating the callee alone keeps every zero-quantity row |
| 2 | RegEx `Replace` with `CopyUnmatched` | A SKU the pattern does not match is **kept**, not nulled |
| 2 | Case-**insensitive** here, case-**sensitive** at tool 3 | Two RegEx tools with opposite flags, one inside the macro and one outside it |
| 2 | A NULL quantity leaves on the Filter's False anchor | `NULL >= 1` is NULL, which is not true; that is what keeps the NULL warehouse out of the Cross Tab |
| 3 | RegEx `Parse` never drops a row | A non-matching SKU keeps its row with both parsed fields NULL |
| 4 | Cross Tab's header list is **frozen** in the tool's MetaInfo | Columns are `EAST, NORTH, WEST` whatever the data holds; a fourth warehouse would be dropped |
| 4 | A combination with no rows is NULL, not zero | The single most likely mistake in a hand-written pivot |
| 5 | Transpose keeps NULL values | A bare `UNPIVOT` drops them; `UNPIVOT INCLUDE NULLS` keeps them too, but this segment uses `UNION ALL` |
| 6, 7 | Write mode `overwrite`, two targets from one chain | Both targets are replaced wholesale; the shared upstream tools are computed twice |

## Fix-loop budget

Three iterations. The macro is the risk: it is the only workflow in the samples where a tool's
behaviour is defined in another file, and the two things most likely to be got wrong — the
question value and `CopyUnmatched` — are both invisible in `inventory.yxmd`. The Cross Tab's
NULL-versus-zero rule is the second.

## Dependencies, owner and consumers

`clean_codes.yxmc` is a **shared macro**: it lives under `Supporting_Macros/` beside the workflow,
and any other workflow in the estate that uses it would be translated against the same sub-DAG.
Changing it changes this migration even though `inventory.yxmd` did not change, which is worth
recording before anyone edits it. No upstream workflow produces `stock.yxdb`. `manifest.json`
records no consumers for either output. Owner `wf_owner`; the schedule `0 6 * * 1-5` is the Alteryx
Server schedule recorded at parse time.
