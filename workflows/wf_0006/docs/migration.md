# wf_0006 — migration record

Restates `parsed/dag.json`, `analysis.md`, `segments/targets.json`,
`segments/*/translation_notes.md`, `segments/*/validation.json`, `intake/mappings.yaml` and
`manifest.json`. It adds no claim about behaviour that those artifacts do not support, and it
quotes no number about data: every count, diff and verdict lives in
`segments/<seg>/validation*.json`, written by `scripts/validate_segment.py` and
`scripts/validate_snowpark.py`.

**Nothing in this migration has been run on a real Snowflake account or a real Alteryx engine.**
"Validated" here means the SQL procedures were executed on the local DuckDB double and the
Snowpark procedure in the Snowpark **Local Testing Framework** — an in-memory implementation, not
an account — both against golden data produced by `scripts/dev/alteryx_sim.py`, the project's
stand-in for the Alteryx engine.

## Overview

| | |
|---|---|
| Workflow | `wf_0006` — Subscription revenue recognition |
| Source | `subscription_revenue.yxmd`, `yxmdVer 2023.1`, E1 engine |
| Owner | `wf_owner` |
| Schedule | `0 6 * * 1-5` (the Alteryx Server schedule recorded at parse time) |
| Consumers | none recorded in `manifest.json` |
| Tier | T2 — one tool, the Python tool at 3, has no SQL pattern and takes the Snowpark target |
| Output kind | `procedures` (`mappings/global.yaml`'s `program.output_target`) |
| Segments | `seg_01` (tools 1–2, `sql`), `seg_02` (tool 3, `snowpark`), `seg_03` (tools 4–5, `sql`), three waves |
| Procedures | `MIG_WORK.WF0006_SEG_01`, `…_SEG_02`, `…_SEG_03`, all `EXECUTE AS CALLER` |

## Source mappings

| Alteryx touchpoint | Logical name | Snowflake | Write mode | Confirmed by |
|---|---|---|---|---|
| `C:\data\billing\subscriptions.yxdb` (tool 1) | `SUBSCRIPTIONS` | `BILLING.RAW.SUBSCRIPTIONS` | read | owner |
| `C:\data\out\revenue_by_period.yxdb` (tool 5) | `REVENUE_BY_PERIOD` | `ANALYTICS.CURATED.REVENUE_BY_PERIOD` | overwrite | owner |

No procedure names either of these tables. The source is read as `IDENTIFIER(:SUBSCRIPTIONS_SRC)`
after `LET SUBSCRIPTIONS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.SUBSCRIPTIONS';`, and the
target written as `IDENTIFIER(:REVENUE_BY_PERIOD_TGT)` after
`LET REVENUE_BY_PERIOD_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.REVENUE_BY_PERIOD';` (Snowflake's documented `IDENTIFIER` form, which takes one value, never an expression; chosen because the expression form is not in Snowflake's grammar, it has not run on a real account yet, and the first real-account run confirms it),
so the same bodies run against the golden view schema in validation and against the real schema in
production. `seg_02`
touches neither: it reads and writes only `MIG_WORK` work tables, by literal name.

## Tool → CTE map

| Tool | Type | Segment | Target | CTE |
|------|------|---------|--------|-----|
| 1 | `input` | `seg_01` | `sql` | `t1_input` |
| 2 | `filter` | `seg_01` | `sql` | `t2_filter_t` (the False anchor is wired to nothing, so it has no CTE) |
| 3 | `python` | `seg_02` | `snowpark` | **no CTE** — the segment is a Python module, and tool 3 is its `run()` body, marked by the `# tool 3:` comment the Snowpark rules require |
| 4 | `summarize` | `seg_03` | `sql` | `t4_summarize` |
| 5 | `output` | `seg_03` | `sql` | no CTE — the `CREATE OR REPLACE TABLE … AS` statement that wraps `t4_summarize` |

The "every data tool has a CTE" rule is a rule about SQL procedures. A Snowpark segment satisfies
the same intent through a different mechanism: `lib/snowpark_rules.py` requires a real
`# tool <id>:` comment (found by the tokenizer, not by a text search) for every data node of the
segment, and `scripts/compile_check.py --target snowpark` enforces it.

## Deployment

`seg_01` and `seg_03` deploy the way every other procedure in this repository does: their
`proc.sql` *is* the translation, and running it creates a `LANGUAGE SQL` procedure.

`seg_02` is different, and this is the one thing about this workflow a deployer has to know:

- **`proc.py` is the source of truth.** It is the module that runs, and it is what
  `scripts/validate_snowpark.py` executes.
- **`proc.sql` is its DDL, and nothing else.** `scripts/render_snowpark.py wf_0006 seg_02`
  produces it by wrapping `proc.py` verbatim in a `CREATE OR REPLACE PROCEDURE … LANGUAGE PYTHON`
  statement with contract C4's five parameters, `HANDLER = 'run'` and `EXECUTE AS CALLER`. That
  file is what you deploy; it is not edited by hand, and `compile_check.py --target snowpark`
  fails the segment if it has drifted from a fresh render.
- **The runtime is `RUNTIME_VERSION = '3.11'`**, taken from `program.snowpark_runtime` in
  `mappings/global.yaml`, with `PACKAGES = ('snowflake-snowpark-python', 'pandas')`. Whether a
  given Snowflake account offers that runtime and those package versions has not been checked from
  here — see Open items.

## Assumptions

Every assumption is listed one per line in each segment's `translation_notes.md`. The ones a
reader of this document should know about:

- A Filter's True anchor takes only the rows its expression makes really true. `NULL > 0` is NULL,
  so an unbilled period leaves on the False anchor, which is wired to nothing; and the comparison
  is strict, so a period billed exactly 0 is dropped as well.
- Tool 3 is translated as itself rather than rewritten. Its deferred balance carries from one
  period to the next inside a customer and resets on cancellation, and `min(carried + billed, cap)`
  does not distribute over a running sum, so no window function reproduces it.
- The script sorts by `CUSTOMER, PERIOD` before grouping, and that pair is unique on its input in
  every golden set. Without that uniqueness the sort would not be total and the carry-over would
  not be reproducible — the contract records the pair as both the stream's keys and the segment's
  `ordering.keys`.
- The cancellation reset happens **before** the cancelled period's own cap is applied, so it moves
  `RECOGNIZED` as well as `DEFERRED`.
- pandas `groupby` drops rows whose key is NULL, so a NULL `CUSTOMER` would vanish rather than
  form its own schedule. Alteryx's Python tool does the same thing, so this is a faithful
  translation of a silent row loss, not a new one.
- A NULL `CANCELLED` is the one place the two engines do **not** agree by default: the simulator's
  nullable `boolean` column makes `bool(pd.NA)` raise, so the Alteryx script fails on that row,
  while the Local Testing Framework's `to_pandas()` yields `None` and `bool(None)` is quietly
  `False`. `proc.py` therefore tests `pd.isna(row["CANCELLED"])` and raises, so the procedure fails
  on exactly the row the script fails on rather than writing a number nobody asked for — a
  migration must not turn a loud failure into silent output. See Open items for what remains
  unverified about it.
- `to_pandas()` materialises the whole input stream in the procedure's memory. That is sound here
  because the segment's input is bounded (`contract.inputs[0].expected_rows`, `large: false`), and
  it is the translation's single scaling assumption.
- The Snowpark procedure declares its output schema explicitly as a `StructType` rather than
  letting Snowpark infer one, so that an empty run still produces a four-column table and
  `RECOGNIZED`/`DEFERRED` are `FLOAT` whatever the first row happens to hold.
- Alteryx's `Count` counts rows rather than values, so `CUSTOMERS` is `COUNT(*)`.
- Summarize's `Sum` adds the exact decimal value of each `Double` and lands back in a `Double`, so
  the addition goes through `NUMBER(38,10)` rather than accumulating in `FLOAT`.
- `CREATE OR REPLACE [TRANSIENT] TABLE … AS` takes its column types from the expressions that
  produced them and does not enforce the `VARCHAR(n)`/`NUMBER(p,s)` widths the contracts declare.
  The `edge` set's 20-character `CUSTOMER` sits exactly on its limit and proves nothing about what
  Snowflake would do one character further.

## Accepted differences

None. `mappings/global.yaml` accepts the classes `ROUNDING` and `ORDERING` in principle, but
`manifest.accepted_diffs` is empty for this workflow and no cluster has been approved, so a
difference of any class would be a `FAIL`.

## Unsupported / manual items

None. `unsupported.json` is `{"tier": "T2", "unsupported": [], "unknown": []}`. The Python tool is
not unsupported — it is supported on a different target. Had the same script done something the
Snowpark rules refuse (raw SQL through `session.sql`, file or network I/O, a second `run`), the
segment would have been `manual` instead and the workflow would have parked at `NEEDS_HUMAN`.

## Validation summary

`scripts/validate_segment.py wf_0006 seg_01` and `… seg_03` compare every entry of
`contract.outputs[]` against its golden file for each golden set on the DuckDB double;
`scripts/validate_snowpark.py wf_0006 seg_02` does the same for the Snowpark segment by running
`proc.py`'s `run()` in a fresh Snowpark local-testing session, reading the real table's schema and
rows back, and handing both to the **unchanged** `scripts/compare.py`. Each writes
`segments/<seg>/validation.<set>.json` per set and `segments/<seg>/validation.json` for the
segment. Read the verdicts, counts and any diff clusters there — this document does not restate
them, because a number about data may come only from a script.

Which validator runs is not a choice made here: it follows `contract.json`'s `target`, exactly as
`compile_check.py --target auto` picks its static gate from the same field.

The golden sets exercised are the four `manifest.golden_sets` names: `normal`, `period_end`,
`empty` and `edge`. This workflow has no date column — `PERIOD` is text — so `period_end` is a set
of month- and quarter-end billing periods rather than a set of period-end dates, and it is where a
customer's deferred balance lands exactly on its cap. Idempotency is checked by running each
procedure twice from the same starting state in two fresh sandboxes (two `DuckDBBackend`s, or two
Snowpark sessions) and comparing the outputs as row multisets.

Deliberately broken copies of the procedures live in `samples/wf_0006/broken_sql/`, each with one
realistic translator mistake and a `broken.json` row naming the diff class the validator reports
for it. One of them is a `.py` variant, so the Snowpark path is shown failing as well as passing.

## Runbook

1. Deploy `MIG_WORK.WF0006_SEG_01` and `…_SEG_03` from their `proc.sql`. Deploy `…_SEG_02` from
   its `proc.sql` too — for that segment `proc.sql` is the rendered `LANGUAGE PYTHON` DDL; re-run
   `python scripts/render_snowpark.py wf_0006 seg_02` first if `proc.py` has changed.
2. Load or refresh the golden inputs if running against the sandbox:
   `python scripts/load_golden.py wf_0006 <set>`. The target has no prior state, so there is no
   `targets_before` file to load.
3. Call the three procedures **in order**, `seg_01` then `seg_02` then `seg_03` — they are three
   waves, not one — each with the four schema arguments and a run id:
   `CALL MIG_WORK.WF0006_SEG_01('<SRC_DB>', '<SRC_SCHEMA>', '<TGT_DB>', '<TGT_SCHEMA>', '<RUN_ID>')`.
   In validation `SRC_SCHEMA` is the golden view schema `MIG_GOLDEN_WF0006_<SET>`; in production it
   is the schema that exposes `SUBSCRIPTIONS` under that logical name. `seg_02` ignores all five
   arguments — it reads and writes work tables only — but contract C4 fixes the signature.
4. Each SQL procedure sets `TIMEZONE` and `WEEK_START` itself, which is why contract C4 requires
   `EXECUTE AS CALLER`. Neither setting can affect this workflow's result: it has no date or
   timestamp column.
5. Check parity: `python scripts/validate_segment.py wf_0006 seg_01`, then
   `python scripts/validate_snowpark.py wf_0006 seg_02`, then
   `python scripts/validate_segment.py wf_0006 seg_03`. Exit 0 is a pass, 1 a domain failure, 2 a
   usage error.
6. Rollback: the output uses write mode `overwrite`, so `seg_03` replaces the target wholesale and
   holds no prior state. Restore it with Snowflake Time Travel
   (`CREATE OR REPLACE TABLE … CLONE … BEFORE (STATEMENT => …)`) or re-run the Alteryx workflow.
   `seg_01` and `seg_02` write only their own work tables, which the next run replaces.

## Open items

- No step of this migration has run against a real Snowflake account or a real Alteryx engine.
  Every rule the translation relies on is an assumption recorded in
  `docs/reference/simulator-semantics.md`, to be checked one by one when an engine is available.
- The Snowpark **Local Testing Framework** is not Snowflake. It is unaccounted, in-memory, and its
  pandas round-trip has quirks of its own (`scripts/validate_snowpark.py`'s module docstring lists
  the ones this project has met). A green `seg_02` means the handler is right about *this*
  implementation's semantics.
- `RUNTIME_VERSION = '3.11'` and `PACKAGES = ('snowflake-snowpark-python', 'pandas')` are what
  `mappings/global.yaml` asks for. Whether the account offers that runtime, and whether its pandas
  gives bit-identical results for `min` and `round` on the same doubles, is unverified.
- The exact-decimal arithmetic the oracle uses is a deliberate simplification and is the single
  largest known parity risk in the project; see `docs/reference/simulator-semantics.md` §1.1. It
  bears on `seg_03`'s sums, not on `seg_02`, which computes in doubles on both sides.
- **A NULL `CAP` is silently not a cap, and that is a data-quality item for whoever owns
  `subscriptions.yxdb`, not a translation defect.** `float(NULL)` is `nan` on both engines and
  `min(x, nan)` returns `x`, so such a row recognizes everything billed and carries nothing
  forward — in Alteryx exactly as in the procedure. Parity holds, so no validation run will ever
  raise it; if an uncapped subscription is not supposed to exist, the source needs a constraint.
- **How real Alteryx types a NULL `Bool` field inside the Python tool is unverified.** The claim
  above about `CANCELLED` rests on `scripts/dev/alteryx_sim.py` typing the column as pandas'
  nullable `boolean`, which is this project's assumption, not an observation — the same register
  as every other simulator-semantics assumption.
- No golden set carries a NULL `CANCELLED`, and none can be built: the simulator raises inside the
  Python tool before it could write the stream, so there is no expected output to compare against.
  The guard in `proc.py` is reasoned from the two engines' behaviour, not exercised by a golden
  row.
