# wf_0006 — analysis

Reads `parsed/dag.json`, `intake/mappings.yaml`, `segments/` (the cuts `scripts/segment.py`
proposed) and `segments/targets.json` (the targets `scripts/target_check.py` proposed). Restates
them; adds no claim about behaviour that no artifact supports, and no number about data — every
count, diff and tolerance in this project comes from `scripts/`.

## Tool classification

One node is `snowpark`, so the workflow is **T2**: it can be migrated, but not as SQL alone.

| Tool | Type | Class | Rule applied |
|------|------|-------|--------------|
| 1 | `input` | `sql` | Input Data → table read; the source is mapped in `mappings.yaml`, so the segment reads it by its logical name |
| 2 | `filter` | `sql` | `WHERE` for the True anchor; the False anchor is wired to nothing. `NULL > 0` is not true, so an unbilled period leaves on False |
| 3 | `python` | `snowpark` | `plugin_map.TARGET_CLASS` maps a `python` node to `snowpark` outright. There is no SQL pattern for a Python tool: the script is the specification |
| 4 | `summarize` | `sql` | Group by `PERIOD`; `Sum` → `SUM`, `Count` → `COUNT(*)` (Alteryx counts rows, not values) |
| 5 | `output` | `sql` | Output Data, write mode `overwrite` → `CREATE OR REPLACE TABLE … AS` |

No node is `manual` or `unknown`, so `unsupported.json` lists nothing and the status does not go to
`NEEDS_HUMAN`. T2 is not a worse answer than T1: it means one segment leaves the SQL target and
takes the Snowpark one, which the pipeline supports end to end.

## Output target per segment, and why

`segments/targets.json` (written by `scripts/target_check.py`, not by this document) proposes a
target per segment. The analyzer may only **lower** a proposed target, never raise one; nothing
here was lowered.

| Segment | Target | Why |
|---------|--------|-----|
| `seg_01` | `sql` | Tools 1–2 are both `sql`, so the whole segment is a single `CREATE OR REPLACE TRANSIENT TABLE … AS` |
| `seg_02` | `snowpark` | It holds tool 3, the only `snowpark` node. A segment is `snowpark` as soon as one of its nodes is: a procedure is written in one language |
| `seg_03` | `sql` | Tools 4–5 are both `sql` |

The workflow's `output_kind` is `procedures`, which is `mappings/global.yaml`'s
`program.output_target` and needs no argument here. `targets.json` also records, for phase 2's
benefit, why this workflow could not be a dbt project even if the preference were `dbt`:
`seg_02` is a Snowpark segment, and a dbt model is SQL.

### Why tool 3 cannot be SQL

The script keeps a deferred balance that carries from one period to the next inside a customer and
resets when a period is cancelled. Each row's cap check reads the balance the row before it left
behind, and `min(carried + billed, cap)` does not distribute over a running sum — there is no
`SUM(...) OVER (...)` that produces it. Rewriting it as SQL would mean re-deriving the recurrence
rather than translating the tool, and the pipeline does not re-derive: it translates. So tool 3
goes to the Snowpark target and keeps its own loop.

## Segmentation

`scripts/segment.py` proposed three segments and all three are confirmed unchanged.

- `seg_01` — tools 1, 2. Wave 1.
- `seg_02` — tool 3, the Python tool. Wave 2.
- `seg_03` — tools 4, 5. Wave 3.
- Nothing was split and nothing was merged. The rule that fixes this shape is that a `python` node
  is a **hard cut**: it is a whole program, not a clause, so it cannot share a statement with the
  tools around it, and it gets a segment of its own whatever the size floor says.
- `sample.json` sets `min_tools: 1` here rather than the 3 most samples use. That is bookkeeping,
  not a change of shape: the hard cut would isolate tool 3 at any floor, and at 3 the two tools on
  either side of it would sit under the floor for no purpose. At 1 the three groups stand as the
  cut leaves them.
- `seg_01` and `seg_02` each have one outbound edge, so each materialises a work table
  (`MIG_WORK.WF0006_SEG_01_OUT`, `MIG_WORK.WF0006_SEG_02_OUT`) and the next segment's `inputs[0]`
  carries `from`, `stream` and that table. The Filter's True anchor is `T`, so `seg_01`'s stream is
  `2_T`; the Python tool's first output anchor is `1`, so `seg_02`'s stream is `3_1`.

## Contract notes

- `contract.json` carries `"target"` in every segment: `sql`, `snowpark`, `sql`. That field is what
  the orchestrator dispatches on — `compile_check.py --target auto` and the choice between
  `validate_segment.py` and `validate_snowpark.py` both read it, and nothing else decides.
- `seg_02`'s translation is `proc.py`; its `proc.sql` is the `LANGUAGE PYTHON` wrapper
  `scripts/render_snowpark.py` renders from it, and is not edited by hand.
- Both work streams declare `["CUSTOMER", "PERIOD"]` as their keys, and the final target declares
  `["PERIOD"]`. Every one of those is unique in all four golden sets. The `edge` set does hold a
  byte-identical duplicate pair, and it is billed 0, so the Filter removes it before it can reach
  a stream — which is what leaves both streams keyable. That matters most for `seg_02`: a keyed
  comparison names the customer and period whose schedule moved, where a row multiset could only
  say that some row did.
- `BILLED` is `NOT NULL` on `seg_01`'s stream and nothing else there is: the Filter is the only
  thing in that segment that removes a NULL, and it removes NULLs in `BILLED` alone.
- `CUSTOMER`, `RECOGNIZED` and `DEFERRED` are `NOT NULL` on `seg_02`'s stream. `CUSTOMER` because
  pandas `groupby` drops a NULL key outright, so a NULL customer cannot appear in the output (it
  disappears instead — recorded as a parity risk, since the row loss is silent); the other two
  because they are computed from non-NULL inputs.
- `seg_02`'s `ordering.keys` is `["CUSTOMER", "PERIOD"]` and its `order_dependent_columns` are
  `["RECOGNIZED", "DEFERRED"]`: the carry-over makes both depend on the order rows are processed
  in, and the script's own `sort_values` is what makes that order total and reproducible.
- `row_relation` is `filter` for `seg_01` (the Filter removes rows), `1:1` for `seg_02` (one
  schedule row per billed period) and `aggregate` for `seg_03` (periods collapse into groups).
- `ordering.order_dependent_columns` is empty in `seg_01` and `seg_03`: neither numbers rows, takes
  a first or last row, nor runs a window.

## Parity risks

| Tool | Class | Risk |
|------|-------|------|
| 2 | `NULL_SEMANTICS` | `NULL > 0` is NULL, not true, so an unbilled period leaves on the unwired False anchor and is lost |
| 2 | `LOGIC` | The predicate is strict: a period billed exactly 0 is dropped, not kept as zero revenue |
| 3 | `LOGIC` | The deferred balance carries between rows, so the result depends on processing order; the script's own sort is what makes it deterministic |
| 3 | `LOGIC` | A cancellation resets the carried balance **before** the period's own cap applies, so it moves `RECOGNIZED` as well as `DEFERRED` |
| 3 | `NULL_SEMANTICS` | pandas `groupby` drops a NULL key, so a NULL `CUSTOMER` vanishes rather than forming its own schedule |
| 3 | `NULL_SEMANTICS` | A NULL `CAP` is silently no cap on both engines (`float(NULL)` is `nan`, `min(x, nan)` is `x`), so parity holds and nothing would surface it — a data-quality risk in the source |
| 3 | `NULL_SEMANTICS` | A NULL `CANCELLED` raises in the simulator (`bool(pd.NA)`) but is quietly `False` after the framework's `to_pandas()`; `proc.py` raises on it explicitly so both sides fail loudly on the same row. How real Alteryx types a NULL `Bool` there is **unverified** |
| 3 | `ROUNDING` | Both sides round with Python's own `round` on IEEE doubles — round-half-to-even on the binary value, not Alteryx's half-away-from-zero. They agree only because the same expression runs on both sides |
| 4 | `LOGIC` | Alteryx's `Count` counts rows, not values, so `CUSTOMERS` is `COUNT(*)` |
| 4 | `ROUNDING` | `Sum` adds exact decimal values and lands in a `Double`, so the addition goes through `NUMBER(38,10)` rather than accumulating in `FLOAT` |
| 4 | `ORDERING` | Alteryx sorts groups ascending with NULL first; `GROUP BY` promises no order. The target is keyed, so nothing depends on it |
| 5 | `LOGIC` | Write mode `overwrite`: the target is replaced wholesale and holds no prior state |
