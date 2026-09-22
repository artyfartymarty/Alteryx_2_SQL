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