-- BROKEN ON PURPOSE -- do not fix: this is a fixture for tests/test_e2e_parity.py.
-- The mistake: the Summarize's GroupBy field is gone, so the whole stream is aggregated into one
-- row instead of one row per period. PERIOD still has to come from somewhere for the projection
-- to be legal, and MIN(PERIOD) is the shortest thing that compiles -- which is exactly why this
-- mistake survives a compile check and has to be caught by a parity run.
-- wf_0006 / seg_03 -- hand migration of samples/wf_0006/source/subscription_revenue.yxmd tools 4-5.
-- Reads seg_02's work table by its literal MIG_WORK name and replaces the mapped target through
-- IDENTIFIER. seg_02 is a Snowpark Python procedure, which this segment neither knows nor needs
-- to: a work table is a work table whatever wrote it.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0006_SEG_03(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- contract C4: each mapped table's name is built once, then referenced as IDENTIFIER(:<name>)
  LET REVENUE_BY_PERIOD_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.REVENUE_BY_PERIOD';

  -- Output tool 5 (write mode Overwrite, logical REVENUE_BY_PERIOD): the target is replaced
  -- wholesale, so it holds no prior state and nothing is merged into it.
  CREATE OR REPLACE TABLE IDENTIFIER(:REVENUE_BY_PERIOD_TGT) AS
  WITH
  -- tool 4: Summarize -- Alteryx's Count counts ROWS and not values, so CUSTOMERS is COUNT(*),
  -- not COUNT(CUSTOMER). Each Sum adds the exact decimal value of every RECOGNIZED / DEFERRED
  -- and lands back in a Double field, which is why the addition goes through NUMBER(38,10)
  -- instead of accumulating in FLOAT.
  t4_summarize AS (
      SELECT
          MIN(PERIOD)                                           AS PERIOD,
          CAST(SUM(CAST(RECOGNIZED AS NUMBER(38,10))) AS FLOAT) AS TOTAL_RECOGNIZED,
          CAST(SUM(CAST(DEFERRED AS NUMBER(38,10))) AS FLOAT)   AS TOTAL_DEFERRED,
          COUNT(*)                                              AS CUSTOMERS
      FROM MIG_WORK.WF0006_SEG_02_OUT
  )
  SELECT
      PERIOD,
      TOTAL_RECOGNIZED,
      TOTAL_DEFERRED,
      CUSTOMERS
  FROM t4_summarize;

  RETURN 'OK';
END;
$$;
