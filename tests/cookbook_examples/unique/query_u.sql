-- tool 2 (anchor U): Unique on ACCT, DAY -- the first row per key combination in incoming order
-- (SEQ, an upstream ordinal) is kept; comparison is case-sensitive and a NULL key is its own
-- distinct value, grouped with any other NULL-key row the same way any other value would be.
WITH t2_unique_u AS (
    SELECT
        ACCT,
        DAY,
        VALUE,
        SEQ
    FROM MIG_COOKBOOK.IN_1
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCT, DAY ORDER BY SEQ) = 1
)
SELECT
    ACCT,
    DAY,
    VALUE,
    SEQ
FROM t2_unique_u