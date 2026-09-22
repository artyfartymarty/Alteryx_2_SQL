-- tool 2: Sort -- AMOUNT descending then ACCT ascending. Alteryx sorts NULL first ascending and
-- last descending, which the explicit NULLS clauses spell out.
WITH t2_sort AS (
    SELECT
        ACCT,
        AMOUNT
    FROM MIG_COOKBOOK.IN_1
    ORDER BY AMOUNT DESC NULLS LAST, ACCT ASC NULLS FIRST
)
SELECT
    ACCT,
    AMOUNT
FROM t2_sort