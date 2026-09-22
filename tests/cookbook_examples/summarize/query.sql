-- tool 2: Summarize -- group by REGION. Alteryx's Count counts rows and CountNonNull counts
-- values, so ORDER_COUNT is COUNT(*) and PRICED_COUNT is COUNT(AMOUNT); Concat skips NULLs and
-- joins in incoming order, which SEQ (an upstream Record ID/Sort's ordinal) makes explicit --
-- SQL has no notion of "the order rows arrived in" on its own. A group with nothing to join is
-- '' in Alteryx; LISTAGG gives NULL there instead, so it is wrapped in COALESCE(..., '').
WITH t2_summarize AS (
    SELECT
        REGION,
        CAST(SUM(CAST(AMOUNT AS NUMBER(38,10))) AS FLOAT)             AS TOTAL_AMOUNT,
        COUNT(*)                                                      AS ORDER_COUNT,
        COUNT(AMOUNT)                                                 AS PRICED_COUNT,
        COALESCE(LISTAGG(NOTE, ',') WITHIN GROUP (ORDER BY SEQ), '')  AS NOTES
    FROM MIG_COOKBOOK.IN_1
    GROUP BY REGION
)
SELECT
    REGION,
    TOTAL_AMOUNT,
    ORDER_COUNT,
    PRICED_COUNT,
    NOTES
FROM t2_summarize