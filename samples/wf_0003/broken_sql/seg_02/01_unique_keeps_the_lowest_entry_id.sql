-- BROKEN ON PURPOSE -- do not fix: this is a fixture for tests/test_e2e_parity.py.
-- The mistake: tool 5's row number is ordered `ENTRY_ID ASC`, the default a translator falls
-- into when writing ROW_NUMBER() without re-reading tool 4's Sort. Unique keeps the FIRST row
-- in incoming order, and incoming order is ENTRY_ID DESCENDING, so this keeps the wrong one of
-- each duplicate pair and the group's TOTAL is computed from the superseded entry.
-- wf_0003 / seg_02 -- hand migration of samples/wf_0003/source/gl_period_close.yxmd (tools 4-10).
-- One statement per Output-tool clause: the PreSQL, the `Update; Insert if new` write as a MERGE,
-- then the PostSQL. The whole tool chain lives in the MERGE's USING subquery, because contract C4
-- allows no variable to hold an intermediate result.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0003_SEG_02(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- ===========================================================================================
  -- Output tool 10, PreSQL. Alteryx runs it against the target connection before the write, so
  -- it is its own statement here, ahead of the MERGE, and it names the target the same way the
  -- MERGE does.
  -- ===========================================================================================
  DELETE FROM IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.GL_SUMMARY')
  WHERE PERIOD < '2020-01';

  -- ===========================================================================================
  -- Output tool 10 (write mode Update; Insert if new on ACCT, PERIOD; logical GL_SUMMARY).
  -- Alteryx maps the incoming columns to the target BY NAME and leaves every other column of the
  -- target alone -- untouched on a matched row, NULL on an inserted one -- which is why the MERGE
  -- names the six incoming columns and never mentions LOADED_FLAG.
  -- ===========================================================================================
  MERGE INTO IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.GL_SUMMARY') AS T
  USING (
    WITH
    -- tool 4: Sort -- ACCT and POSTED_DT ascending, ENTRY_ID descending. Alteryx sorts NULL first
    -- ascending and last descending, which the explicit NULLS clauses spell out. This order is
    -- what every order-dependent tool below reads, so the same three keys are repeated in each
    -- window's ORDER BY rather than relied on from this CTE (a CTE's row order is not a promise).
    t4_sort AS (
        SELECT
            ACCT,
            PERIOD,
            POSTED,
            AMOUNT,
            REGION,
            ENTRY_ID,
            POSTED_DT
        FROM MIG_WORK.WF0003_SEG_01_OUT
        ORDER BY ACCT ASC NULLS FIRST, POSTED_DT ASC NULLS FIRST, ENTRY_ID DESC NULLS LAST
    ),
    -- tool 5 (anchor U): Unique on ACCT, POSTED_DT -- the FIRST row of each key combination in
    -- incoming order survives, and incoming order is tool 4's sort, so the row number is taken
    -- over exactly those keys. The Duplicates anchor is not wired, so the D branch is not built.
    t5_unique_u AS (
        SELECT
            ACCT,
            PERIOD,
            POSTED,
            AMOUNT,
            REGION,
            ENTRY_ID,
            POSTED_DT
        FROM t4_sort
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY ACCT, POSTED_DT
            ORDER BY ACCT ASC NULLS FIRST, POSTED_DT ASC NULLS FIRST, ENTRY_ID ASC NULLS FIRST
        ) = 1
    ),
    -- tool 6: Multi-Row Formula -- [Row-1:RUN_BAL] + [AMOUNT] grouped by ACCT with "rows that
    -- don't exist" worth 0, which is a running total of AMOUNT within ACCT in incoming order.
    -- The sum is taken over the exact NUMBER amounts and cast to FLOAT once, because RUN_BAL is a
    -- Double field and the model converts an exact decimal to a binary double at that boundary.
    t6_multi_row_formula AS (
        SELECT
            ACCT,
            PERIOD,
            POSTED,
            AMOUNT,
            REGION,
            ENTRY_ID,
            POSTED_DT,
            CAST(SUM(AMOUNT) OVER (
                PARTITION BY ACCT
                ORDER BY ACCT ASC NULLS FIRST, POSTED_DT ASC NULLS FIRST, ENTRY_ID DESC NULLS LAST
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS FLOAT)                                                     AS RUN_BAL
        FROM t5_unique_u
    ),
    -- tool 7: Record ID -- numbered from 1 in incoming order, first position. The column exists
    -- only so tool 9's `Last` has something to take the last row by; it is not written to the
    -- target. `sanitize` upper-snakes Alteryx's `RecordID` to RECORD_ID.
    t7_record_id AS (
        SELECT
            ROW_NUMBER() OVER (
                ORDER BY ACCT ASC NULLS FIRST, POSTED_DT ASC NULLS FIRST, ENTRY_ID DESC NULLS LAST
            )                                                               AS RECORD_ID,
            ACCT,
            PERIOD,
            POSTED,
            AMOUNT,
            REGION,
            ENTRY_ID,
            POSTED_DT,
            RUN_BAL
        FROM t6_multi_row_formula
    ),
    -- tool 8: Formula -- IIF(DateTimeFormat([POSTED_DT], "%Y-%m-%d") = [User.PeriodEnd], "Y", "N")
    -- with the workflow constant User.PeriodEnd resolved to '2026-08-31'. A NULL POSTED_DT makes
    -- the formatted text NULL and the comparison NULL, and both Alteryx's IIF and SQL's IFF take
    -- the else branch on a NULL condition, so such a row is flagged 'N'.
    t8_formula AS (
        SELECT
            RECORD_ID,
            ACCT,
            PERIOD,
            POSTED,
            AMOUNT,
            REGION,
            ENTRY_ID,
            POSTED_DT,
            RUN_BAL,
            IFF(TO_CHAR(POSTED_DT, 'YYYY-MM-DD') = '2026-08-31', 'Y', 'N')  AS PERIOD_END_FLAG
        FROM t7_record_id
    ),
    -- tool 9: Summarize -- group by ACCT, PERIOD. Alteryx's Count counts ROWS, so ENTRIES is
    -- COUNT(*); Sum of a FixedDecimal stays a FixedDecimal, so TOTAL keeps NUMBER(19,2); Last
    -- takes the group's last row in incoming order, which is its largest RECORD_ID, so
    -- CLOSING_BAL is MAX_BY(RUN_BAL, RECORD_ID); Max over a string ignores NULL, as SQL's does.
    t9_summarize AS (
        SELECT
            ACCT,
            PERIOD,
            CAST(SUM(AMOUNT) AS NUMBER(19,2))    AS TOTAL,
            MAX_BY(RUN_BAL, RECORD_ID)           AS CLOSING_BAL,
            COUNT(*)                             AS ENTRIES,
            MAX(PERIOD_END_FLAG)                 AS HAS_PERIOD_END
        FROM t8_formula
        GROUP BY ACCT, PERIOD
    )
    SELECT
        ACCT,
        PERIOD,
        TOTAL,
        CLOSING_BAL,
        ENTRIES,
        HAS_PERIOD_END
    FROM t9_summarize
  ) AS S
  ON T.ACCT = S.ACCT AND T.PERIOD = S.PERIOD
  WHEN MATCHED THEN UPDATE SET
      TOTAL = S.TOTAL,
      CLOSING_BAL = S.CLOSING_BAL,
      ENTRIES = S.ENTRIES,
      HAS_PERIOD_END = S.HAS_PERIOD_END
  WHEN NOT MATCHED THEN INSERT (ACCT, PERIOD, TOTAL, CLOSING_BAL, ENTRIES, HAS_PERIOD_END)
      VALUES (S.ACCT, S.PERIOD, S.TOTAL, S.CLOSING_BAL, S.ENTRIES, S.HAS_PERIOD_END);

  -- ===========================================================================================
  -- Output tool 10, PostSQL. It runs after the write and only fills NULLs, so a row the target
  -- already held with LOADED_FLAG 'N' keeps its 'N' and only this run's inserts become 'Y'.
  -- ===========================================================================================
  UPDATE IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.GL_SUMMARY')
  SET LOADED_FLAG = 'Y'
  WHERE LOADED_FLAG IS NULL;

  RETURN 'OK';
END;
$$;
