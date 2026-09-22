-- tool 2 (anchor F): Filter [REGION] != "WEST" -- the False branch takes the rows the expression
-- makes false AND the rows it makes NULL; the IS NULL half is the whole point, and a plain
-- WHERE REGION = 'WEST' would silently lose the NULL-region rows.
WITH t2_filter_f AS (
    SELECT
        ID,
        REGION,
        AMOUNT
    FROM MIG_COOKBOOK.IN_1
    WHERE NOT (REGION <> 'WEST') OR (REGION) IS NULL
)
SELECT
    ID,
    REGION,
    AMOUNT
FROM t2_filter_f