-- tool 2: Formula -- three expressions in order; NET and SIZE_BAND both read the AMOUNT the first
-- expression wrote, so AMOUNT is computed in a nested SELECT and the other two read it from there.
-- AMOUNT is a Double field, so ToNumber's result is stored as a FLOAT; NET's arithmetic then goes
-- through NUMBER(38,10) (never FLOAT) so Round's half-away-from-zero lands on the same cent the
-- oracle computes in exact decimals (index.md's "Local verification" note).
WITH t2_formula AS (
    SELECT
        AMOUNT_TXT,
        QTY,
        AMOUNT,
        CAST(ROUND(CAST(AMOUNT AS NUMBER(38,10))
                   * IFF(QTY >= 10, CAST(0.9 AS NUMBER(2,1)), CAST(1 AS NUMBER(2,1))),
                   2) AS FLOAT)                                         AS NET,
        CASE WHEN AMOUNT >= 1000 THEN 'LARGE'
             WHEN AMOUNT >= 100  THEN 'MEDIUM'
             ELSE 'SMALL'
        END                                                             AS SIZE_BAND
    FROM (
        SELECT
            AMOUNT_TXT,
            QTY,
            TRY_TO_DOUBLE(AMOUNT_TXT) AS AMOUNT
        FROM MIG_COOKBOOK.IN_1
    ) f2
)
SELECT
    AMOUNT_TXT,
    QTY,
    AMOUNT,
    NET,
    SIZE_BAND
FROM t2_formula