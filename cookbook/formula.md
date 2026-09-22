# Formula  (plugin: AlteryxBasePluginsGui.Formula.Formula)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Runs one or more expressions in configured order, each writing one field; a later expression sees
what an earlier one just wrote, not what the field held on the way in
(`docs/reference/dag-contract.md` §4). Writing to an existing field's name updates it in place and
keeps its position; writing to a new name appends it at the end. Every result goes through the
target field's own coercion (`docs/reference/simulator-semantics.md` §5): `ToNumber` on text that
is not a plain decimal is NULL, not an error; `Round(x, m)` rounds to the nearest multiple of `m`
with halves away from zero; `IIF` and `IF/ELSEIF/ELSE` both treat a NULL condition as false, so
those rows take the else branch, never a NULL result from the branch alone.

**Arithmetic itself is a documented parity risk, not just a translation detail** — see below.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Formula -- three expressions in order; NET and SIZE_BAND both read the AMOUNT the first
-- expression wrote, so AMOUNT is computed in a nested SELECT and the other two read it from there.
-- AMOUNT is a Double field, so ToNumber's result is stored as a FLOAT; NET's arithmetic then goes
-- through NUMBER(38,10) (never FLOAT) so Round's half-away-from-zero lands on the same cent the
-- oracle computes in exact decimals (index.md's "Local verification" note).
WITH t2_formula AS (
    SELECT
        AMOUNT_TXT,
        QTY,
        AMOUNT,
        CAST(ROUND(CAST(AMOUNT AS NUMBER(38,10))
                   * IFF(QTY >= 10, CAST(0.9 AS NUMBER(2,1)), CAST(1 AS NUMBER(2,1))),
                   2) AS FLOAT)                                         AS NET,
        CASE WHEN AMOUNT >= 1000 THEN 'LARGE'
             WHEN AMOUNT >= 100  THEN 'MEDIUM'
             ELSE 'SMALL'
        END                                                             AS SIZE_BAND
    FROM (
        SELECT
            AMOUNT_TXT,
            QTY,
            TRY_TO_DOUBLE(AMOUNT_TXT) AS AMOUNT
        FROM MIG_COOKBOOK.IN_1
    ) f2
)
SELECT
    AMOUNT_TXT,
    QTY,
    AMOUNT,
    NET,
    SIZE_BAND
FROM t2_formula
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **Money arithmetic must be done in `NUMBER`, never `FLOAT`.** `ROUND(1.005::FLOAT, 2)` is `1.00`
   on this runtime where the exact decimal form is `1.01` — the oracle computes formula arithmetic
   in exact decimal, a deliberate, documented simplification from Alteryx's own IEEE-754 binary64
   (`docs/reference/simulator-semantics.md` §1.1). Casting to `NUMBER(38,10)` before multiplying,
   rounding the `NUMBER`, and casting back to `FLOAT` only at the end is what makes the pattern
   above agree with the oracle; `tests/cookbook_examples/formula/` includes a row whose `QTY` sits
   exactly on the `>= 10` boundary (`10`) so the discount actually applies in one of the checked
   rows, and a half-cent row (`1.005`) that a `FLOAT` `ROUND` sends the other way.
2. **This project's own runtime and Snowflake disagree on multiplication scale.** The local runtime
   types `NUMBER(38,10) * NUMBER(38,10)` at scale 20; Snowflake's documented rule gives scale
   `min(S1 + S2, max(S1, S2, 12))`. The pattern above casts `AMOUNT` to scale 10 and the factor to the
   scale it really has (`NUMBER(2,1)`), so `S1 + S2` is 11 and both rules give the same exact
   product; two operands both left at `NUMBER(38,10)` would be exact here and cut to scale 12 on
   Snowflake, so a formula with more decimal places needs the same check redone against the
   real rule — see index.md's "Local verification" note.
3. **A later expression sees the earlier one's *written* value, not a re-evaluation of its
   expression.** `NET` and `SIZE_BAND` both read `AMOUNT` from the nested `SELECT`, not by repeating
   `TRY_TO_DOUBLE(AMOUNT_TXT)` a second time — SQL cannot reference a sibling alias in the same
   `SELECT` list, so a translation that inlines the earlier expression instead of nesting risks
   subtly re-deriving a different value if the expression is not perfectly idempotent (and always
   costs an extra evaluation even when it is).
4. **A NULL or unparseable `AMOUNT_TXT` propagates through every later expression, and still lands
   in `SIZE_BAND`'s `ELSE`.** `tests/cookbook_examples/formula/` includes a NULL `AMOUNT_TXT` and a
   non-numeric one (`'abc'`); both make `AMOUNT` NULL, which makes `NET` NULL and makes both `CASE`
   comparisons NULL — landing on `'SMALL'`, not an error and not NULL, because a NULL condition is
   not true in Alteryx's `IF` any more than in SQL's `CASE`.

## Config fields that change the pattern

- `formulas[].field`: an existing field name updates it in place (keeping position); a new name
  appends a column at the end of the `SELECT` list.
- `formulas[].type` / `.size`: the field's Alteryx type after this expression, which decides the
  `CAST`/coercion Snowflake side needs (index.md's type map) — critically, whether the arithmetic
  above needs the `NUMBER` idiom at all (only when the target is `Double`/`Float`/`FixedDecimal`).
- The **order** of `formulas[]` is the order CTEs (or nested `SELECT`s) must be chained in; two
  formulas that do not depend on each other may still share one `SELECT`, but a dependent one may
  not move earlier.

## Do not  (known wrong translations)

- Do not `ROUND` a raw `FLOAT` expression. Cast to `NUMBER` first, round the `NUMBER`, cast the
  rounded result back to `FLOAT` only if the target field is a `Double`.
- Do not reference an alias defined earlier in the *same* `SELECT` list (`NET` computed from a
  `AMOUNT` alias in that same list) — most SQL dialects, Snowflake included, do not resolve
  sibling aliases that way; nest or chain a CTE per dependency instead.
- Do not translate `ToNumber`/`ToString` as a plain `CAST`. `CAST` raises on bad input; Alteryx's
  conversion functions warn and return NULL — use `TRY_TO_NUMBER`/`TRY_TO_DOUBLE`/`TO_VARCHAR`.
