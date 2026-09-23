-- wf_0006 / seg_01 -- hand migration of samples/wf_0006/source/subscription_revenue.yxmd tools 1-2.
-- Its one outbound stream, 2_T, feeds the Python tool that is seg_02, so it is materialised as the
-- segment's work table. The next segment is a Snowpark Python procedure rather than a SQL one,
-- which changes nothing here: it reads this table by the same literal MIG_WORK name a SQL
-- successor would.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0006_SEG_01(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- contract C4: each mapped table's name is built once, then referenced as IDENTIFIER(:<name>)
  LET SUBSCRIPTIONS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.SUBSCRIPTIONS';

  -- Outbound stream 2_T -> seg_02 (the Python tool's only input anchor, #1).
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0006_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data -- the subscription billing extract, read by its logical name from
  -- mappings.yaml. No record limit and no query of its own, so the projection is exactly the
  -- five fields the tool's MetaInfo declares, in their declared order.
  t1_input AS (
      SELECT
          CUSTOMER,
          PERIOD,
          BILLED,
          CAP,
          CANCELLED
      FROM IDENTIFIER(:SUBSCRIPTIONS_SRC)
  ),
  -- tool 2 (anchor T): Filter [BILLED] > 0 -- the True branch keeps only the rows the expression
  -- makes really true. `NULL > 0` is NULL, which is not true, so SQL's own three-valued WHERE
  -- already drops an unbilled period, exactly as the tool sends it out on False; the comparison
  -- is strict, so a BILLED of exactly 0 is dropped too. The False anchor is wired to nothing.
  t2_filter_t AS (
      SELECT
          CUSTOMER,
          PERIOD,
          BILLED,
          CAP,
          CANCELLED
      FROM t1_input
      WHERE BILLED > 0
  )
  SELECT
      CUSTOMER,
      PERIOD,
      BILLED,
      CAP,
      CANCELLED
  FROM t2_filter_t;

  RETURN 'OK';
END;
$$;
