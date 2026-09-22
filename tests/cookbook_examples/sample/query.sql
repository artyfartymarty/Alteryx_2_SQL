-- tool 2: Sample -- first 2 rows per GRP, kept in incoming order (SEQ, an upstream ordinal); a
-- group with fewer than 2 rows just keeps what it has.
WITH t2_sample AS (
    SELECT
        GRP,
        VAL,
        SEQ
    FROM MIG_COOKBOOK.IN_1
    QUALIFY ROW_NUMBER() OVER (PARTITION BY GRP ORDER BY SEQ) <= 2
)
SELECT
    GRP,
    VAL,
    SEQ
FROM t2_sample