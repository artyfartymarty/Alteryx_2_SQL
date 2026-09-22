# wf_0004 — inventory with a macro

`source/inventory.yxmd`, E1 engine, 7 nodes, plus `source/Supporting_Macros/clean_codes.yxmc`
(6 nodes). Input (1) → macro (2) → regex parse (3) → cross tab (4) → yxdb output (6), with a
transpose (5) → yxdb output (7) hanging off the cross tab.

**These files were written by hand to `docs/reference/dag-contract.md`. Neither has been produced
or opened by Alteryx.**

## The macro

`clean_codes.yxmc` has no `Plugin` attribute on tool 2 in the parent — it is identified by
`<EngineSettings Macro="Supporting_Macros\clean_codes.yxmc">`, and its anchors are named after its
own tools: `Input1` in, `Output5` out. Inside it:

1 `macro_input` → 2 `regex` replace `^\s*([A-Za-z]+)[- ]?(\d+)\s*$` → `$1-$2`, case-insensitive,
copy unmatched → 3 `formula` `Uppercase([SKU])` → 4 `filter` `[QTY] >= [%Question.MinQty%]` →
5 `macro_output`. Tool 10 is the `NumericUpDown` question `MinQty`, default `0`; the workflow
passes `1`. Following the dag contract, the filter references the question by name rather than
through an Action tool, and the simulator substitutes it textually.

## What each tool is here to exercise

| Tool | Behaviour under test |
|------|----------------------|
| 2 macro | a question value overriding an interface default (`1` over `0`), and a sub-DAG that rewrites a field in place |
| 3 regex | `Parse` method, case **sensitive**, two output fields; a non-matching row leaves both NULL |
| 4 cross tab | two group fields, a frozen header list (`EAST, NORTH, WEST`) in its `MetaInfo`, `Sum`, and missing combinations that must come out NULL |
| 5 transpose | keys are `SKU` only, so `FAMILY` is dropped; NULL values are kept |

`WAREHOUSE` stays inside `{EAST, NORTH, WEST}` in every set, because the cross tab's header list is
frozen in the `MetaInfo` and a fourth value would have no column to land in. A NULL `WAREHOUSE`
appears only on rows the macro's filter removes first.

This workflow has no date column, so `period_end` cannot carry period-end dates; it is a month-end
stock count instead, which keeps the four-set layout uniform.

## `normal` (14 rows)

| `SKU` as written | Why it is there |
|---|---|
| `ab 12` | a space instead of a dash, and lower case → `AB-12` |
| `" AB-12 "` | leading and trailing spaces → `AB-12`, the same key as the row above |
| `AB-12` | already clean, so all three forms collapse to one group |
| `zz9` | no separator at all → `ZZ-9` |
| `ZZ-9` | already clean |
| `bad sku!` | the macro's regex never matches, `CopyUnmatched` keeps it, and tool 3's parse then leaves `FAMILY` and `ITEM_NO` NULL |
| `cd-100` (`QTY` 0) | dropped by `MinQty = 1` |
| `cd-100` (`QTY` NULL) | `NULL >= 1` is NULL, so it goes to the filter's `False` and is dropped too |
| `cd-100` (`QTY` 9) | the only `cd-100` that survives, so its group has one warehouse and two NULL columns |
| `ef-7` twice | several rows for the same SKU **and** the same warehouse, so `Sum` has something to add |
| `GH 42` | upper case with a space |

The surviving rows give six cross-tab groups; `AB-12` has all three warehouses filled, `ZZ-9` two,
and the rest one each.

## `period_end` (8 rows)

A month-end stock count over the same SKU shapes, including one `cd 100` at zero that the macro
drops and one unparseable `bad sku!`.

## `empty`

Header row, zero data rows.

## `edge` (7 rows)

| Row | Why it is there |
|---|---|
| all `\N` | NULL in every column; its NULL `QTY` means the macro's filter removes it, which is what keeps the NULL `WAREHOUSE` away from the frozen header list |
| `ÄB-12` | non-ASCII: `Ä` is outside `[A-Za-z]`, so neither regex matches and `FAMILY` stays NULL |
| 30 × `Q` | `SKU` at its 30-character maximum with a `NOTE` at its 100-character maximum |
| `DUP-1` twice | exact duplicate rows that must sum to 4 |
| `zero-0` | `QTY` 0 → dropped by `MinQty` |
| `bd-5` | `QTY` exactly 1, the `MinQty` boundary → kept |
