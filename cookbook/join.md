# Join  (plugin: AlteryxBasePluginsGui.Join.Join)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

An inner equi-join on the configured key pairs, with three outputs: `J` (matched rows, both sides'
fields), `L` (left rows that matched nothing, left fields only), `R` (right rows that matched
nothing, right fields only) — `docs/reference/dag-contract.md` §4. **A NULL key never matches
anything on either side**, so a row with a NULL join key always leaves on `L` or `R`, never on `J`.
Key comparison is case-sensitive and exact: no trimming, no case folding, no numeric coercion of
text. `J` carries the left fields, then the right fields; a right field whose name collides with a
left field is renamed `Right_<name>` **before** the Join's own Select applies. `J` rows come out in
left order, and within one left row, in right order — a left row that matches several right rows
produces one `J` row per match (a fan-out that duplicates the left row's own values once per right
match, which is why `J` almost never has a usable natural key). `L` and `R` keep their own side's
row order and fields untouched.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 3 (anchor J): Join on CUST_ID -- an inner equi-join. The right side's CUST_ID collides
-- with the left's and is renamed Right_CUST_ID before the Join's own Select applies (here, a
-- pass-through Select that keeps every field).
WITH t3_join_j AS (
    SELECT
        L.CUST_ID,
        L.NAME,
        L.TIER,
        R.ORDER_ID,
        R.CUST_ID AS Right_CUST_ID,
        R.AMOUNT
    FROM MIG_COOKBOOK.IN_1 L
    JOIN MIG_COOKBOOK.IN_2 R
      ON L.CUST_ID = R.CUST_ID
)
SELECT
    CUST_ID,
    NAME,
    TIER,
    ORDER_ID,
    Right_CUST_ID,
    AMOUNT
FROM t3_join_j
```

```sql
-- tool 3 (anchor L): the left rows that matched nothing, carrying the left side's fields only.
-- `=` never matches a NULL key, so a customer with a NULL CUST_ID leaves here too -- which is
-- exactly what Alteryx does, and why NOT EXISTS (never NOT IN) is used.
WITH t3_join_l AS (
    SELECT
        CUST_ID,
        NAME,
        TIER
    FROM MIG_COOKBOOK.IN_1 L
    WHERE NOT EXISTS (
        SELECT 1 FROM MIG_COOKBOOK.IN_2 R WHERE R.CUST_ID = L.CUST_ID
    )
)
SELECT
    CUST_ID,
    NAME,
    TIER
FROM t3_join_l
```

```sql
-- tool 3 (anchor R): the right rows that matched nothing, carrying the right side's own fields
-- and order.
WITH t3_join_r AS (
    SELECT
        ORDER_ID,
        CUST_ID,
        AMOUNT
    FROM MIG_COOKBOOK.IN_2 R
    WHERE NOT EXISTS (
        SELECT 1 FROM MIG_COOKBOOK.IN_1 L WHERE L.CUST_ID = R.CUST_ID
    )
)
SELECT
    ORDER_ID,
    CUST_ID,
    AMOUNT
FROM t3_join_r
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **`NOT EXISTS`, never `NOT IN`, for the anti-joins.** `tests/cookbook_examples/join/` puts a
   NULL key on both sides (a customer with a NULL `CUST_ID`, an order with a NULL `CUST_ID`). SQL's
   `NOT IN` against a subquery that returns even one NULL makes the whole predicate NULL for every
   row, silently dropping every unmatched row, not just the one with the NULL key — `NOT EXISTS`
   does not have this failure mode, which is the entire reason to prefer it.
2. **A left row that matches several right rows produces one `J` row per match, duplicating the
   left row's own values.** The example's `CUST_ID = 1` customer has three matching orders, so it
   appears three times in `J` — this is expected, not a bug, and it is why the `J` stream compares
   keyless here (see index.md's "Local verification" note on keyless streams generally).
3. **`Right_<name>` renaming happens before the Join's own Select, not after.** A Select configured
   inside the Join tool that deselects the *original* right-side name (`CUST_ID`, before the
   rename) will not do what it looks like — it has to target `Right_CUST_ID` instead. This page's
   own example keeps every field (a pass-through Select) specifically so the rename is visible in
   `J`'s own output rather than hidden by a later deselect.

## Config fields that change the pattern

- `keys`: one `ON L.<left> = R.<right>` clause per pair, `AND`-combined for a composite key.
- `select`: the Join's own embedded Select, applied to `J` only (never to `L`/`R`) — same rules as
  [select.md](select.md), including the `Right_<name>` renaming happening first.

## Do not  (known wrong translations)

- Do not use `NOT IN` for `L`/`R`; use `NOT EXISTS` (or a `LEFT JOIN ... WHERE R.key IS NULL`,
  which has the same NULL-safety but is easy to get backwards under a schema change).
- Do not assume `J`'s row count equals `MIN(rows_left, rows_right)` or any other shortcut — it is
  the sum, over every left row, of how many right rows shared its key, which can be more than
  either side's own row count.
- Do not carry the pre-rename right-side column name into a later CTE; once `J` exists, the
  collided field is `Right_<name>` and stays that way for anything downstream that reads it.
