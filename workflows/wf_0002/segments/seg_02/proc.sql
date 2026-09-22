-- wf_0002 / seg_02 -- hand migration of samples/wf_0002/source/customer_orders.yxmd tools 3-4
-- (Tool Container 210, "Type the CSV export", inside container 200). Its one outbound stream,
-- 4_Output, feeds the Join's Right anchor in seg_03.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0002_SEG_02(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- Outbound stream 4_Output -> seg_03 (Join, Right anchor).
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0002_SEG_02_OUT AS
  WITH
  -- tool 3: Input Data -- the nightly orders CSV export. Every column arrives as V_String(254),
  -- so the source table is all text and tool 4 does the typing.
  t3_input AS (
      SELECT
          ORDER_ID,
          CUST_ID,
          AMOUNT,
          ORDER_DATE
      FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS_EXPORT')
  ),
  -- tool 4: Select -- retypes all four columns and selects nothing else (*Unknown is unselected).
  -- Alteryx's coercion warns and nulls on text it cannot read, which is what the TRY_ forms do:
  -- 'abc' and the empty string become a NULL AMOUNT, and 2026-02-30 -- a date that does not
  -- exist -- becomes a NULL ORDER_DATE rather than an error.
  t4_select AS (
      SELECT
          TRY_TO_NUMBER(ORDER_ID, 38, 0)        AS ORDER_ID,
          TRY_TO_NUMBER(CUST_ID, 38, 0)         AS CUST_ID,
          TRY_TO_DOUBLE(AMOUNT)                 AS AMOUNT,
          TRY_TO_DATE(ORDER_DATE, 'YYYY-MM-DD') AS ORDER_DATE
      FROM t3_input
  )
  SELECT
      ORDER_ID,
      CUST_ID,
      AMOUNT,
      ORDER_DATE
  FROM t4_select;

  RETURN 'OK';
END;
$$;
