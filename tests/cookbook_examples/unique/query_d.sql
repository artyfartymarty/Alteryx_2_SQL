-- tool 2 (anchor D): Unique on ACCT, DAY -- every row after the first per key combination, in
-- the same incoming order (SEQ).
WITH t2_unique_d AS (
    SELECT
        ACCT,
        DAY,
        VALUE,
        SEQ
    FROM MIG_COOKBOOK.IN_1
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCT, DAY ORDER BY SEQ) > 1
)
SELECT
    ACCT,
    DAY,
    VALUE,
    SEQ
FROM t2_unique_d