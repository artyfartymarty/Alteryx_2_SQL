-- tool 3: Union by name, inputs stacked in #1, #2 order. The output keeps the first input's field
-- order and a field an input lacks arrives as a typed NULL, never a bare NULL.
WITH t3_union AS (
    SELECT
        CUST_ID,
        NAME,
        MATCH_FLAG,
        CAST(NULL AS NUMBER(38,0)) AS ORDER_ID
    FROM MIG_COOKBOOK.IN_1
    UNION ALL
    SELECT
        CUST_ID,
        CAST(NULL AS VARCHAR) AS NAME,
        MATCH_FLAG,
        ORDER_ID
    FROM MIG_COOKBOOK.IN_2
)
SELECT
    CUST_ID,
    NAME,
    MATCH_FLAG,
    ORDER_ID
FROM t3_union