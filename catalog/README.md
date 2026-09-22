# catalog/columns.csv

A stand-in for `INFORMATION_SCHEMA.COLUMNS` on the target Snowflake account. Nothing in this repo
has ever connected to a real Snowflake account (plan Global Constraints), and the `MIGRATION_AGENT`
role is deliberately **not** granted `INFORMATION_SCHEMA` access on production databases (Task 15's
permission policy) — a separate, human-run job is expected to publish a sanitized column list into
`MIG_WORK.CATALOG_COLUMNS` (or, offline, into this file) for the intake agent to read instead.

`scripts/intake_touchpoints.py`'s `load_catalog(repo, backend=None)` reads this file when no
`backend` is given, or the table `MIG_WORK.CATALOG_COLUMNS` (same columns) through `backend` when
one is given (see `scripts/lib/backend.py`).

## Shape

Header row, then one row per `(database, schema, table, column)`:

```
database,schema,table,column,data_type,row_count
```

`row_count` is the table's row count as of the last catalog publish (repeated on every row of that
table); it is informational only — candidate ranking uses `overlap`/`name` similarity, not
`row_count`, and `row_count` is shown to the user for sanity, never compared automatically. A
`data_type` containing a comma (e.g. `NUMBER(19,2)`) is RFC 4180 quoted.

## The query that would export the real one

```sql
SELECT table_catalog AS database, table_schema AS schema, table_name AS table,
       column_name AS column, data_type,
       (SELECT row_count FROM information_schema.tables t
         WHERE t.table_catalog = c.table_catalog AND t.table_schema = c.table_schema
           AND t.table_name = c.table_name) AS row_count
FROM information_schema.columns c
ORDER BY table_catalog, table_schema, table_name, ordinal_position;
```

Run this (or the account's own equivalent) with a read-only role against the databases the
migration cares about, export the result as CSV with this header, and replace this file — or load
it into `MIG_WORK.CATALOG_COLUMNS` for the `backend`-based path. Nothing here has been run against
a real Snowflake account.

## The rows in this file

`SALES.RAW.ORDERS` / `SALES.RAW.ORDERS_ARCHIVE` (samples/wf_0001), `CRM.RAW.CUSTOMERS` /
`CRM.RAW.ORDERS_EXPORT` (wf_0002), `FINANCE.RAW.GL_LEDGER` / `ANALYTICS.CURATED.GL_SUMMARY`
(wf_0003), `WH.RAW.STOCK` (wf_0004), `VENDOR.RAW.ACCOUNTS` (wf_0005), and
`ANALYTICS.CURATED.CUSTOMER_ORDER_FACT` (wf_0002's output) give every sample workflow's touchpoints
a plausible catalog match. `REF.RAW.REGION_CODES` and `REF.RAW.STATUS_CODES` are decoys: each
shares one or two column names with a real table (`REGION`/`NAME`, `STATUS`) but never enough to
clear the 0.5 overlap threshold against any sample touchpoint, so they never rank as a false match
— they exist only to prove the scorer doesn't grab the first table with a familiar column name.
