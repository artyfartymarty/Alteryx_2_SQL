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