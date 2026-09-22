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