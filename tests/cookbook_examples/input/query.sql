-- tool 1: Input Data -- the orders extract, read by its logical name (mappings.yaml, contract C6).
WITH t1_input AS (
    SELECT
        ORDER_ID,
        CUSTOMER,
        REGION,
        AMOUNT
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    ORDER_ID,
    CUSTOMER,
    REGION,
    AMOUNT
FROM t1_input