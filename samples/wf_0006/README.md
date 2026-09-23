# wf_0006 — subscription revenue recognition

`source/subscription_revenue.yxmd`, E1 engine, 5 nodes in one straight chain: Input Data (1) →
Filter (2) → **Python tool (3)** → Summarize (4) → Output Data (5).

**This file was written by hand to `docs/reference/dag-contract.md`. It has never been produced
or opened by Alteryx.** In particular, **the Python tool's plugin id is this repository's own
convention — verify it against a real workflow before trusting it.** Real Alteryx stores a Python
tool's code as notebook JSON; this sample (and the parser) uses a plain `<Script>` element, which
`docs/reference/dag-contract.md` §4 says how to extend when migrating a real one.

## The Python tool's script is not quite `test_alteryx_sim_python.py::SCRIPT`

Tool 3's `<Script>` is the `SCRIPT` constant from `tests/test_alteryx_sim_python.py` with its
**last three lines changed**. That constant ends with

```python
Alteryx.write(pd.DataFrame(out), 1)
```

and this sample ends with

```python
schedule = pd.DataFrame(out, columns=["CUSTOMER", "PERIOD", "RECOGNIZED", "DEFERRED"])
schedule["RECOGNIZED"] = schedule["RECOGNIZED"].astype("float64")
schedule["DEFERRED"] = schedule["DEFERRED"].astype("float64")
Alteryx.write(schedule, 1)
```

Everything above those lines is identical, character for character.

The reason is the **`empty` golden set**. `pd.DataFrame([])` has no rows *and no columns*, so the
written frame would have no schema at all, `golden/intermediates/seg_02/empty/3_1.csv` would come
out with zero columns, and `compare.py` would report a schema failure for a set whose right answer
is "no rows". Naming the columns is not enough on its own either: an empty object-dtype column
maps to `V_WString`, so `RECOGNIZED` and `DEFERRED` have to be cast to `float64` to keep their
`FLOAT` family. On a non-empty set the two spellings produce byte-identical output.

**Task 1's `SCRIPT` constant was deliberately left unchanged.** It belongs to that task's test, its
assertions are about a non-empty frame and still hold, and editing another task's fixture to suit
a sample is the wrong direction. The divergence is confined to those three lines, and
`canned/segments/seg_02/translation_notes.md` records the same fact from the procedure's side.

## Why this sample exists

It is the only sample whose migration is not SQL alone. Tool 3 is a `python` node, which
`plugin_map.TARGET_CLASS` classifies as `snowpark`, so `seg_02` is migrated as a Snowpark Python
procedure (`proc.py`, with `proc.sql` rendered from it) while `seg_01` and `seg_03` stay SQL
procedures. That makes it the fixture for the whole per-target path: `target_check.py`'s proposal,
`compile_check.py --target auto`, `validate_snowpark.py` beside `validate_segment.py`, and the
orchestrator's per-segment dispatch.

**Tier T2.** Not T1 (some tool has no SQL pattern) and not T3 (nothing needs a human rewrite):
one segment simply leaves the SQL target.

## Segmentation this sample is shaped for

`sample.json` sets `min_tools: 1`, where four of the other samples use 3. A `python` node is a
**hard cut** — it contributes a whole program, not a clause — so tool 3 is isolated at any floor;
the floor of 1 only stops the two-tool groups on either side of it from being reported as
undersized. The cuts are `seg_01` {1, 2}, `seg_02` {3}, `seg_03` {4, 5}, in three waves.

The Filter's True anchor is `T` and the Python tool's first output anchor is `1`, so the work
streams are `2_T` (→ `MIG_WORK.WF0006_SEG_01_OUT`) and `3_1` (→ `MIG_WORK.WF0006_SEG_02_OUT`).

## What each tool is here to exercise

| Tool | Behaviour under test |
|------|----------------------|
| 1 input | a plain yxdb read with a `Bool` column — the only one in the samples |
| 2 filter | `[BILLED] > 0`: NULL is not true, and the comparison is strict, so both a NULL and a zero are dropped. The False anchor is wired to nothing |
| 3 python | the sandboxed script and the `Alteryx.read("#1")` / `Alteryx.write(df, 1)` shim; a per-customer recurrence (a deferred balance that carries and resets) that no window function reproduces |
| 4 summarize | `GroupBy` on a text period, two `Sum`s over `Double`, and `Count` — which counts **rows**, not values |
| 5 output | write mode `overwrite`, a file target, so there is no `targets_before` here |

## `normal` (tool 1)

| Rows | Why they are there |
|---|---|
| `ACME` ×3 | a three-period schedule where the deferred balance is non-zero when the cancellation arrives, so the reset moves `RECOGNIZED` and not just `DEFERRED`. This is the row the `seg_02` broken variant gets wrong |
| `BOLT` `2026-01` | `BILLED` is NULL → `NULL > 0` is not true → the Filter drops it |
| `BOLT` `2026-02` | `BILLED` is exactly 0 → the comparison is strict → the Filter drops it too |
| `BOLT` `2026-03` | what is left of `BOLT`: a single billed period whose cap is below the amount, so it defers most of it |
| `CINDER` ×3 | a customer that never cancels, billed under its cap, then far over it, then under it again, so the carried balance builds up and is drawn down |

Three periods survive into the final target, and `CUSTOMERS` differs between them, so the `Count`
aggregate is not constant.

## `period_end`

Month- and quarter-end billing periods (`2026-03`, `2026-06`, `2026-09`, `2026-12`). `PERIOD` is
text, not a date, so "period end" here is about the billing calendar rather than about date
parsing. `ACME`'s first row bills exactly twice its cap, which leaves a deferred balance **equal
to the cap** — the boundary `min(billed + deferred, cap)` is decided on. `DELTA` is a single
cancelled row, so the reset fires on a balance that is already zero.

## `empty`

A header row and no data rows. It is the set that made the Python tool's script declare the
columns it writes: an empty result frame built with no column list has no columns at all, and a
work table with no columns is a schema failure rather than an empty answer.

## `edge`

| Rows | Why they are there |
|---|---|
| an all-NULL row | dropped by the Filter for its NULL `BILLED`, so it never reaches the Python tool |
| `Ångström Ñuñez` | non-ASCII customer name through the Python tool and back |
| `Q`×20 | `CUSTOMER` at exactly the column's 20-character limit, with amounts small enough to round at the second decimal |
| `DUP CO` twice | a **byte-identical duplicate row**. It is billed 0, so the Filter removes both copies — which is exactly why both work streams can declare `(CUSTOMER, PERIOD)` as their keys and get a keyed comparison rather than a row multiset |
| `NEG` | a negative `BILLED`, also dropped by the Filter |
| `SOLO` | a customer with a single, cancelled row: the reset fires on an opening balance of zero |

### Two NULLs that are deliberately absent from every set

**A NULL `CANCELLED` is not here, and cannot be.** The simulator types a `Bool` column as pandas'
nullable `boolean`, so `bool(pd.NA)` raises inside the Python tool and the tool never writes its
stream — there would be no expected output to compare a procedure against. `proc.py` still guards
against it (`pd.isna(row["CANCELLED"])` raises), because the Snowpark Local Testing Framework's
`to_pandas()` hands back `None` instead and `bool(None)` is quietly `False`; without the guard the
procedure would silently treat the row as not cancelled where Alteryx fails loudly. The guard is
reasoned from both engines' behaviour rather than exercised by a golden row, and how *real*
Alteryx types a NULL `Bool` there is **unverified**.

**A NULL `CAP` that survives the Filter is absent for a different reason: it would prove nothing.**
`float(NULL)` is `nan` on both engines and `min(x, nan)` returns `x`, so the cap is silently not
applied — identically on both sides. Parity holds, so no golden row could ever make a validator
say anything about it. It is a data-quality risk in the source, recorded in
`canned/docs/migration.md`, not a translation risk.

## Hand migration

`canned/` holds the artifacts the orchestrator's mock runner replays instead of calling an agent:
`intake/plan.md`, `analysis.md`, `unsupported.json`, `docs/migration.md`, and per segment
`segments/<seg>/{contract.json,proc.sql,translation_notes.md,review.json}` — plus `proc.py` for
`seg_02`, whose `contract.json` carries `"target": "snowpark"` and whose `proc.sql` is the
`LANGUAGE PYTHON` wrapper `scripts/render_snowpark.py` renders from that module. `broken_sql/`
holds copies of a procedure with one deliberate translator mistake each (one `.sql`, one `.py`),
and `broken_sql/broken.json` records which diff class the validator reports for each of them.
`tests/test_e2e_parity.py` runs both paths, dispatching on each contract's `target`;
`tests/test_canned_artifacts.py` checks their shape, including the Snowpark AST rules and that
`proc.sql` still matches a fresh render. **Nothing here has run on Snowflake or on Alteryx** — the
Snowpark side runs in the Snowpark Local Testing Framework, which is an in-memory implementation,
not an account.
