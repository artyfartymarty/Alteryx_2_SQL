-- tool 2: Multi-Row Formula -- RUN = [Row-1:RUN] + [AMT], grouped by ACCT, ordered by SEQ (an
-- upstream ordinal). This is genuinely recursive: each row reads the *previous row's own computed
-- RUN*, not a source column, so a windowed SUM() is not equivalent -- see Parity risk 1. A NULL
-- AMT makes RUN NULL, and NULL then poisons every later row's RUN in the same group, matching
-- Alteryx's own [Row-1:RUN] + [AMT] arithmetic (NULL propagates, it is never skipped).
WITH RECURSIVE
ranked AS (
    SELECT
        ACCT,
        AMT,
        SEQ,
        ROW_NUMBER() OVER (PARTITION BY ACCT ORDER BY SEQ) AS RN
    FROM MIG_COOKBOOK.IN_1
),
t2_multi_row_formula (ACCT, AMT, SEQ, RN, RUN) AS (
    SELECT
        ACCT,
        AMT,
        SEQ,
        RN,
        CAST(0 AS FLOAT) + AMT
    FROM ranked
    WHERE RN = 1
    UNION ALL
    SELECT
        r.ACCT,
        r.AMT,
        r.SEQ,
        r.RN,
        p.RUN + r.AMT
    FROM ranked r
    JOIN t2_multi_row_formula p
      ON r.ACCT = p.ACCT AND r.RN = p.RN + 1
)
SELECT
    ACCT,
    AMT,
    SEQ,
    RUN
FROM t2_multi_row_formula