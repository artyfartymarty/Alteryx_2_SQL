# Select  (plugin: AlteryxBasePluginsGui.AlteryxSelect.AlteryxSelect)

## What Alteryx does (precisely, incl. nulls, order, case, truncation)

Reorders, drops, renames and retypes fields (`docs/reference/dag-contract.md` §4). Listed fields
that are selected are emitted in the order they are listed, each optionally renamed and retyped;
listed fields that are **not** selected are dropped entirely. When `*Unknown` is itself selected,
every field the list does not mention is appended afterward, in the incoming stream's own order —
this is how a Select survives an upstream schema change without being reconfigured. A retype that
does not give an explicit size falls back to the new type's natural width and drops the source's
scale. `String(n)`/`WString(n)` truncate silently to `n` characters on a retype or on a plain
passthrough of an already-fixed-width field; `V_String`/`V_WString` never truncate. Every value —
new type or old — goes through the same coercion `docs/reference/simulator-semantics.md` §5
describes: bad input becomes NULL rather than raising.

## Snowflake pattern  (SQL, with placeholders)

```sql
-- tool 2: Select -- CUSTOMER to String(10) (Alteryx truncates silently), STATUS renamed
-- ORDER_STATUS, NOTE deselected and dropped, then *Unknown appends ORDER_ID and REGION in
-- incoming order.
WITH t2_select AS (
    SELECT
        LEFT(CUSTOMER, 10) AS CUSTOMER,
        STATUS             AS ORDER_STATUS,
        ORDER_ID,
        REGION
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    CUSTOMER,
    ORDER_STATUS,
    ORDER_ID,
    REGION
FROM t2_select
```

## Parity risks  (numbered; each references an example under tests/cookbook_examples)

1. **`LEFT(x, n)`, never a `VARCHAR(n)` column declaration, is what reproduces the truncation.**
   Declaring the CTE's output column `VARCHAR(10)` would be silently unenforced on the local
   DuckDB double (`scripts/lib/backend.py`'s module docstring) and would **raise** on real
   Snowflake instead of truncating — the opposite of what Alteryx does.
   `tests/cookbook_examples/select/` truncates a 16-character name, keeps a name of exactly 10
   characters unchanged (the boundary), and truncates a name containing non-ASCII characters —
   `LEFT` counts characters, not bytes, matching `docs/reference/simulator-semantics.md` §5's rule.
2. **A field the Select lists but does not select is dropped, not passed through blank.** A
   reviewer skimming the config for "does this Select touch REGION" can miss that a *listed and
   deselected* field (`NOTE` in the example) never reaches the output at all — the same as an
   unlisted, unselected field.
3. **A rename with no retype keeps the source's exact type, including its "never truncates" rule.**
   `STATUS` → `ORDER_STATUS` above changes only the name; because `STATUS` is `V_String`, the value
   is never truncated even though `V_String(10)` and `String(10)` sound alike (index.md's type map
   distinguishes them: only the fixed-width `String`/`WString` truncate).

## Config fields that change the pattern

- `fields[].type` / `fields[].size`: absent means "no retype" (name/type/size pass through from the
  source); present means `CAST` plus, for `String`/`WString`, `LEFT(x, size)` for the truncation.
- `fields[].rename`: only changes the output column's alias; never affects the value.
- `fields[].selected`: `false` drops a *listed* field — different from simply not listing it, but
  with the same effect on the output.
- `unknown_selected`: `false` means the Select is a strict allow-list — any field not explicitly
  listed is dropped, including one a later upstream change adds.

## Do not  (known wrong translations)

- Do not declare the target column `VARCHAR(n)` and rely on Snowflake to truncate on insert —
  Snowflake raises on overflow instead (program spec §8.3's `String(n)` note); truncate explicitly
  with `LEFT(x, n)`.
- Do not reorder the `SELECT` list to match the *source* table's column order instead of the
  Select's configured order; Alteryx's output order is the tool's own listed order, then the
  unknown fields, and downstream tools (especially Union by position) depend on it.
- Do not skip a retype that only changes size on a `String`/`WString` field just because "the type
  name didn't change" — `String(20)` to `String(10)` still truncates.
