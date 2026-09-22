-- tool 2: Record ID -- numbers rows from 100 in incoming order (SEQ, an upstream ordinal) and
-- puts the field first. Snowflake tables carry no implicit row order, so a real segment needs
-- this same explicit ordering column from whatever established determinism upstream (a Sort, or
-- the source query's own ORDER BY) -- see Parity risk 1.
WITH t2_record_id AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY SEQ) + 99 AS RID,
        CODE,
        VAL,
        SEQ
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    RID,
    CODE,
    VAL,
    SEQ
FROM t2_record_id