-- tool 2 (anchor T): Filter [REGION] != "WEST" -- the True branch keeps only rows where the
-- expression is really true; a NULL REGION makes it NULL, which is not true, so SQL's own
-- three-valued WHERE already drops those rows here.
WITH t2_filter_t AS (
    SELECT
        ID,
        REGION,
        AMOUNT
    FROM MIG_COOKBOOK.IN_1
    WHERE REGION <> 'WEST'
)
SELECT
    ID,
    REGION,
    AMOUNT
FROM t2_filter_t