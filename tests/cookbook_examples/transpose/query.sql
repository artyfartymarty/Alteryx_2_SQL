-- tool 2: Transpose -- keys SKU, one Name/Value row per data field (EAST, WEST) per input row, in
-- configured order. INCLUDE NULLS is required: a plain UNPIVOT drops a row whose value is NULL
-- entirely, but Alteryx keeps it.
WITH t2_transpose AS (
    SELECT
        SKU,
        NAME,
        VALUE
    FROM MIG_COOKBOOK.IN_1
    UNPIVOT INCLUDE NULLS (VALUE FOR NAME IN (EAST, WEST))
)
SELECT
    SKU,
    NAME,
    VALUE
FROM t2_transpose