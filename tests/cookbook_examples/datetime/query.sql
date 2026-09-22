-- tool 2: DateTime -- text to DateTime, format DD/MM/YYYY (Snowflake token style; the Alteryx
-- tool's own config uses %d/%m/%Y, Python strftime style -- see Parity risk 1). Unparseable text,
-- and a date that does not exist (31 February), both become NULL.
WITH t2_datetime AS (
    SELECT
        POSTED,
        TRY_TO_TIMESTAMP(POSTED, 'DD/MM/YYYY') AS POSTED_DT
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    POSTED,
    POSTED_DT
FROM t2_datetime