# Snowpark

A Snowpark segment's `proc.py` is one module with exactly one top-level `def run(session, src_db,
src_schema, tgt_db, tgt_schema, run_id)` (contract C4, design spec §4.2). `scripts/lib/
snowpark_rules.py` is the enforcement of that contract, checked by `compile_check.py --target
snowpark`: `session` may only ever be the receiver of `session.table(...)` or
`session.create_dataframe(...)`, written directly in `run`; the segment has exactly one sink,
`.write.mode("overwrite" | "append").save_as_table(<one literal>)`; every table name is a literal
or a `{src_db}.{src_schema}.<LOGICAL>` / `{tgt_db}.{tgt_schema}.<LOGICAL>` f-string naming a
logical this segment's contract actually declares; the DataFrame API only -- no `session.sql`, no
`sql_expr`/`call_function`/the rest of the raw-SQL escape hatches, no dunder access anywhere in the
module; the output `StructType` lists columns in the contract's declared order.

Since live hardening L4 fix round 5 the gate is an **allow-list**: it accepts only the Snowpark/pandas
surface these patterns use and refuses everything else. Imports come from the fixed list
(`snowflake.snowpark[.functions|.types]`, `pandas`, `numpy`, `re`, `math`, `datetime`, `decimal`); a
`pd.`/`np.` attribute must be on its short allow-list (`pd.isna`, `pd.notna`, `pd.NA`, `pd.DataFrame`,
`pd.Series`, `pd.Timestamp`; `np.random`, `np.nan`), so `pd.eval`/`pd.read_csv`/`np.load` never
resolve; a method call is refused unless its name is a documented Snowpark DataFrame/Column method or
a pandas carry-over method (`to_pandas`, `sort_values`, `reset_index`, `groupby`, `iterrows`,
`itertuples`, `to_dict`, `sum`), so `.eval`, `.query`, `.pipe`, `.style`, `.plot`, `.apply`, every
`to_*` writer and every `read_*` reader are off it; and `engine="python"` is refused outright (it
would run a string the gate never sees). Every fragment below stays inside that surface.

Those rules are the contract this page's snippets are written to fit inside. **Every snippet below
is a fragment, not a whole file**: it is what a translator pastes into `run`, reading from
`session.table("...")` and returning (or, for the carry-over pattern, building) a DataFrame -- the
one real `run()` around it, with its `.write.mode(...).save_as_table(...)` sink, is what
`render_snowpark.py`/`compile_check.py --target snowpark` actually check end to end (see
`samples/wf_0006/canned/segments/seg_02/proc.py` for a real one built on the carry-over pattern
below). Each fragment is checked here exactly the way the SQL cookbook's patterns are
(`tests/test_cookbook_snowpark.py`): the oracle is `scripts/dev/alteryx_sim.py`, the actual is the
fragment run as a plain `(session) -> DataFrame` function inside a real local Snowpark session
(`validate_snowpark._session`, the Local Testing Framework), and the judge is `compare.py` on
DuckDB, over data that carries a NULL and a duplicate row.

## Filter

**What the SQL page does** ([filter.md](filter.md)): `WHERE <expr>` for the True branch;
`WHERE NOT (<expr>) OR (<expr's operand>) IS NULL` for the False branch, because a plain `NOT`
reproduces False but not NULL (`NOT NULL` is still NULL).

```python
from snowflake.snowpark.functions import col


# tool 2: Filter [REGION] != "WEST" -- True branch (anchor T): keeps only rows where the
# expression is really true; a NULL REGION makes the comparison NULL, which is not true. The
# nullity is tested on REGION itself (col("REGION").is_not_null()), not on the comparison's own
# is_null() -- see the page's "What the local double does not prove": the Local Testing
# Framework's own `!=`/`==` do not implement three-valued NULL logic (a NULL operand compares
# as an ordinary True/False rather than NULL), so a derived condition's is_null() never fires.
def true_branch(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    return df.filter((col("REGION") != "WEST") & col("REGION").is_not_null())


# tool 2: Filter [REGION] != "WEST" -- False branch (anchor F): takes the rows the expression
# makes false AND the rows it makes NULL -- the IS NULL half is the whole point, so it is not
# enough to negate the True branch's own condition (on real Snowflake, ~cond alone is still NULL
# on a NULL row, not True).
def false_branch(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    cond = col("REGION") != "WEST"
    return df.filter((~cond & col("REGION").is_not_null()) | col("REGION").is_null())
```

**Parity notes.** The SQL page's own three-valued `WHERE` gets the True branch's NULL handling for
free; a naive Snowpark translation of the False branch as `df.filter(~(col("REGION") != "WEST"))`
would reproduce that same gap the SQL page warns about (`NOT NULL` is still NULL, so the NULL row
would silently vanish from both branches). The pattern above tests `REGION`'s own nullity directly
on both branches rather than trusting the comparison's nullity to propagate -- **which it does not,
in this local double**: see below.

## Formula

**What the SQL page does** ([formula.md](formula.md)): chain each formula through a nested
`SELECT`/CTE so a later expression reads the value an earlier one just wrote; cast money arithmetic
through `NUMBER`, never raw `FLOAT`, before rounding.

```python
from snowflake.snowpark.functions import col, iff, lit, try_cast
from snowflake.snowpark.types import DecimalType, DoubleType


def transform(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    # tool 2: Formula, AMOUNT -- ToNumber([AMOUNT_TXT]): a NULL or unparseable AMOUNT_TXT becomes
    # a NULL AMOUNT, never an error, matching Alteryx's warn-and-null ToNumber (try_cast, not cast).
    df = df.with_column("AMOUNT", try_cast(col("AMOUNT_TXT"), DoubleType()))
    # tool 2: Formula, NET -- Round([AMOUNT] * IIF([QTY] >= 10, 0.9, 1), 0.01): a later expression
    # sees the value the earlier one just wrote; with_column gives that for free. try_cast to a
    # two-decimal DecimalType is the rounding step (there is no `round()` DataFrame function that
    # reaches a value already written this way -- see the page's "What the local double does not
    # prove").
    discounted = col("AMOUNT") * iff(col("QTY") >= 10, lit(0.9), lit(1.0))
    df = df.with_column("NET", try_cast(try_cast(discounted, DecimalType(38, 2)), DoubleType()))
    # tool 2: Formula, SIZE_BAND -- IF [AMOUNT]>=1000 THEN "LARGE" ELSEIF [AMOUNT]>=100 THEN
    # "MEDIUM" ELSE "SMALL" ENDIF: nested iff(), the same shape as the expression itself; a NULL
    # AMOUNT makes every condition NULL (not true), so the row lands in the innermost ELSE,
    # exactly like Alteryx's IF/ELSEIF/ELSE.
    df = df.with_column(
        "SIZE_BAND",
        iff(col("AMOUNT") >= 1000, lit("LARGE"), iff(col("AMOUNT") >= 100, lit("MEDIUM"), lit("SMALL"))),
    )
    return df
```

**Parity notes.** `with_column` gives the SQL pattern's "a later expression reads what an earlier
one wrote" for free -- there is no sibling-alias restriction to work around the way a single SQL
`SELECT` list has one. `functions.round` is not a working substitute for `ROUND` here: see below.
This example's edge-case row is `1.006`, not the SQL page's `1.005` -- see below for why.

## Summarize

**What the SQL page does** ([summarize.md](summarize.md)): `GROUP BY` with `COUNT(*)` for `Count`
vs `COUNT(col)` for `CountNonNull`, `LISTAGG(...) WITHIN GROUP (ORDER BY <seq>)` for `Concat`
wrapped in `COALESCE(..., '')`, and the same `NUMBER` idiom as Formula for `Sum`.

```python
from snowflake.snowpark.functions import cast, coalesce, col, count, listagg, lit
from snowflake.snowpark.functions import sum as sum_
from snowflake.snowpark.types import DecimalType, DoubleType


# tool 2: Summarize -- group by REGION. Alteryx's Count counts rows and CountNonNull counts
# values, so ORDER_COUNT is count(lit(1)) and PRICED_COUNT is count(col("AMOUNT")) (count() skips
# NULL by itself); Concat skips NULLs and joins in incoming order, which SEQ (an upstream Record
# ID/Sort's ordinal) makes explicit -- the DataFrame API has no notion of "the order rows arrived
# in" on its own, so listagg needs an explicit within_group. A group with nothing to join is '' in
# Alteryx; listagg gives NULL there instead, so it is wrapped in coalesce(..., lit("")). SUM goes
# through NUMBER(38,10), never FLOAT, for the same reason formula.md's pattern does -- cast to
# that scale BEFORE the group_by (the local double refuses a cast nested inside an aggregate
# call), then cast the aggregated total back to Double, keep_column_order so it stays where the
# oracle puts it (right after the group-by column).
def transform(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    df = df.with_column("AMOUNT_EXACT", cast(col("AMOUNT"), DecimalType(38, 10)))
    out = df.group_by("REGION").agg(
        sum_(col("AMOUNT_EXACT")).alias("TOTAL_AMOUNT"),
        count(lit(1)).alias("ORDER_COUNT"),
        count(col("AMOUNT")).alias("PRICED_COUNT"),
        coalesce(listagg(col("NOTE"), ",").within_group(col("SEQ").asc()), lit("")).alias("NOTES"),
    )
    return out.with_column("TOTAL_AMOUNT", cast(col("TOTAL_AMOUNT"), DoubleType()), keep_column_order=True)
```

**Parity notes.** `group_by(...).agg(...)` puts the group-by column(s) first and the aggregate
columns in the order they are passed -- the same column order the oracle's own `GroupBy`-first
convention produces, so no explicit `select` is needed to reorder afterward. `within_group` is
Snowpark's own name for `LISTAGG`'s `WITHIN GROUP (ORDER BY ...)` clause, not a different idiom.

## Sort

**What the SQL page does** ([sort.md](sort.md)): `ORDER BY <col> DESC NULLS LAST, <col> ASC NULLS
FIRST`, with the NULLS clause spelled out explicitly rather than left to the dialect default.

```python
from snowflake.snowpark.functions import col


# tool 2: Sort -- AMOUNT descending then ACCT ascending. Alteryx sorts NULL first ascending and
# last descending, which the explicit asc_nulls_first()/desc_nulls_last() calls spell out, the
# same way the SQL pattern's explicit NULLS clauses do. A DataFrame's own row order does not
# survive being read back as a table (there is no ORDER BY on a table read), so this order only
# ever matters to whatever reads this DataFrame next, in the same run.
def transform(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    return df.sort(col("AMOUNT").desc_nulls_last(), col("ACCT").asc_nulls_first())
```

**Parity notes.** A Sort's order survives only through an explicit window function or `ORDER BY`
downstream, in Snowpark exactly as in SQL ([sort.md](sort.md) Parity risk 1) -- writing this
DataFrame's rows to a table with `.save_as_table(...)` and reading them back gives no ordering
guarantee at all. This harness compares the example's output as a row multiset (`"keys": []`), the
same as the SQL page's own executable example, for the same reason: a multiset comparison proves
the *values* survive Sort correctly, never that the `ORDER BY`/`.sort(...)` clause itself is
right or present. `record_id.md`/`unique.md` (and, for the pandas-processed carry-over pattern
below, an explicit `sort_values` before any sequential logic) are what actually exercise sequence
end to end.

## Python tool with carry-over (pandas)

**What the SQL page does**: nothing -- a Python tool has no SQL cookbook page. Its logic is
row-sequential (a running balance carried from one row to the next inside a customer), which the
DataFrame API has no expression for; contract C4 permits exactly this one escape hatch,
`to_pandas()` / `session.create_dataframe(pdf)`, for exactly this reason (design spec §4.2).

```python
import pandas as pd
from snowflake.snowpark.types import DoubleType, StringType, StructField, StructType


# tool 2: Python tool -- revenue schedule with carry-over (samples/wf_0006's subscription_revenue
# workflow). Sequential per customer (the deferred balance feeds the next period's cap check and
# resets on cancellation), so the rows are processed in pandas exactly as the Alteryx script does
# -- this is the same body as samples/wf_0006/canned/segments/seg_02/proc.py, returning the
# DataFrame instead of writing it, since a real proc.py's `run()` does the write itself.
def transform(session):
    pdf = session.table("MIG_COOKBOOK.IN_1").to_pandas()
    pdf = pdf.sort_values(["CUSTOMER", "PERIOD"]).reset_index(drop=True)
    out = []
    for customer, group in pdf.groupby("CUSTOMER", sort=False):
        deferred = 0.0
        for _, row in group.iterrows():
            # The Alteryx script fails outright on a NULL CANCELLED (bool(pd.NA) raises); the
            # framework's to_pandas() would hand us None instead and bool(None) is quietly False,
            # so the check is written out to keep the failure loud on both engines.
            if pd.isna(row["CANCELLED"]):
                raise ValueError(f"CANCELLED is NULL for {customer} {row['PERIOD']}")
            if bool(row["CANCELLED"]):
                deferred = 0.0
            billed = 0.0 if pd.isna(row["BILLED"]) else float(row["BILLED"])
            recognized = min(billed + deferred, float(row["CAP"]))
            deferred = billed + deferred - recognized
            out.append([customer, row["PERIOD"], round(recognized, 2), round(deferred, 2)])
    schema = StructType([StructField("CUSTOMER", StringType(254)), StructField("PERIOD", StringType(254)),
                         StructField("RECOGNIZED", DoubleType()), StructField("DEFERRED", DoubleType())])
    return session.create_dataframe(out, schema=schema)
```

**Parity notes.** The `StructType` lists `CUSTOMER, PERIOD, RECOGNIZED, DEFERRED` -- the contract's
declared output column order, checked as a `TYPE` difference (never an "ordering" one) if it
disagrees (design spec §4.2). `sort_values(["CUSTOMER", "PERIOD"])` fixes a total order because
`(CUSTOMER, PERIOD)` is the stream's own key on real segment data; this page's own example carries
a byte-identical duplicate `(CUSTOMER, PERIOD)` pair on purpose (two identical `ACME, 2026-01`
rows), which the harness compares keyless (`"keys": []`) for exactly the reason
`test_cookbook_examples.py`'s module docstring gives -- a duplicate business key is not, by itself,
a translation bug. A NULL `CANCELLED` is not exercised here (nor by `wf_0006`'s own golden data):
the Alteryx script raises on it outright (`bool` of a NULL bool), so the simulator cannot build a
golden set that carries one at all -- see `samples/wf_0006/canned/segments/seg_02/contract.json`'s
own `NULL_SEMANTICS` parity risk for the full argument. This example's NULL is on `CAP` instead
(`BETA, 2026-01`), which both engines already agree is "no cap this period" rather than an error.

## What the local double does not prove

The Snowpark Local Testing Framework (`snowflake-snowpark-python`'s `local_testing=True` session,
what every example above actually runs in) is a subset of Snowflake's own SQL functions and types,
not Snowflake itself (design spec §9): there is no `session.sql` locally at all (by design, the
same rule contract C4 enforces); `RUNTIME_VERSION`/`PACKAGES` resolution is never exercised, since
nothing here ever builds the real `CREATE ... PROCEDURE` wrapper `render_snowpark.py` writes;
performance is not represented. Two concrete gaps this page's own examples ran into, version-pinned
(`snowflake-snowpark-python` 1.55.0) so a future upgrade can be checked against them:

- **Comparison operators do not implement three-valued NULL logic.** `col("REGION") != lit("WEST")`
  on a NULL `REGION` evaluates to `True` locally, and `col("REGION") == lit("WEST")` evaluates to
  `False` -- neither is `NULL`, so a comparison Column's own `.is_null()` never fires, unlike real
  Snowflake's (and the SQL pattern's) three-valued `WHERE`. [Filter](#filter)'s pattern above works
  around this by testing the *source column's* nullity directly rather than the derived
  condition's.
- **There is no working `round()`, and every decimal-narrowing path reaches Python's own
  binary-float `round()` regardless of the source type.** `functions.round(...)` raises
  `NotImplementedError` unconditionally; `cast`/`try_cast` to a smaller-scale `DecimalType`,
  `to_decimal(...)`, and `to_char(...)`/`to_varchar(...)` (the only other numeric-narrowing
  functions this version implements) all convert through `float(x)` and Python's built-in `round()`
  before re-quantizing -- even when `x` is already a `Decimal`. For a value whose nearest IEEE-754
  double sits a hair below an exact decimal midpoint, this reproduces
  [formula.md](formula.md) Parity risk 1's `ROUND(1.005::FLOAT, 2)` problem with no local
  workaround: `float('1.005')` is really `1.00499999999999989…`, so `round(float('1.005'), 2)`
  is `1.0`, not the oracle's exact-decimal `1.01` -- and there is no DataFrame-API rounding
  primitive available locally that reaches a different answer. [Formula](#formula)'s example above
  therefore uses `1.006` (which rounds to `1.01` unambiguously under any rounding rule) rather than
  the SQL page's own `1.005` row, so the pattern's *correctness* is still checked end to end without
  exercising a local-double bug that has no DataFrame-API fix; the SQL `cookbook/formula.md`
  example itself is untouched and still carries `1.005`, because DuckDB's `CAST(... AS NUMBER)` has
  no such gap. On real Snowflake, `CAST(x AS NUMBER(p,2))` is a documented, correct rounding
  operation -- this is a local-double limitation, not a claim about what the pattern computes on a
  real account, which remains unverified either way (design spec §9's honesty rule).
