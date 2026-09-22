-- BROKEN ON PURPOSE -- do not fix: this is a fixture for tests/test_e2e_parity.py.
-- The mistake: only the Join's J and R anchors are stacked by tool 9. Alteryx wires all three
-- anchors, so the customers that matched no order (the L anchor, flagged NO_ORDERS) are
-- silently dropped and never reach the target.
-- wf_0002 / seg_03 -- hand migration of samples/wf_0002/source/customer_orders.yxmd tools 5-11.
-- Reads the two upstream segments' work tables by their literal MIG_WORK names and appends to the
-- mapped target through IDENTIFIER. Tool 11 is a Browse: it emits nothing, so it has no CTE.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0002_SEG_03(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- Output tool 10 (write mode Append Existing, logical CUSTOMER_ORDER_FACT): the rows are added
  -- to whatever the table already holds, and Alteryx maps incoming columns to the target BY NAME,
  -- which the explicit column list spells out.
  INSERT INTO IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.CUSTOMER_ORDER_FACT')
      (CUST_ID, NAME, CITY, TIER, ORDER_ID, AMOUNT, ORDER_DATE, MATCH_FLAG)
  WITH
  -- tool 5 (anchor J): Join on CUST_ID -- an inner equi-join. The right side's CUST_ID collides
  -- with the left's and would arrive as Right_CUST_ID, which the Join's own Select deselects, so
  -- it is simply not projected here.
  t5_join_j AS (
      SELECT
          L.CUST_ID,
          L.NAME,
          L.CITY,
          L.TIER,
          R.ORDER_ID,
          R.AMOUNT,
          R.ORDER_DATE
      FROM MIG_WORK.WF0002_SEG_01_OUT L
      JOIN MIG_WORK.WF0002_SEG_02_OUT R
        ON L.CUST_ID = R.CUST_ID
  ),
  -- tool 5 (anchor L): the left rows that matched nothing, carrying the left side's fields only.
  -- `=` never matches a NULL key, so a customer with a NULL CUST_ID leaves here -- which is
  -- exactly what Alteryx does, and why NOT EXISTS (not a NOT IN) is used.
  t5_join_l AS (
      SELECT
          L.CUST_ID,
          L.NAME,
          L.CITY,
          L.TIER
      FROM MIG_WORK.WF0002_SEG_01_OUT L
      WHERE NOT EXISTS (
          SELECT 1 FROM MIG_WORK.WF0002_SEG_02_OUT R WHERE R.CUST_ID = L.CUST_ID
      )
  ),
  -- tool 5 (anchor R): the right rows that matched nothing, carrying the right side's fields only
  -- and in the right side's own order.
  t5_join_r AS (
      SELECT
          R.ORDER_ID,
          R.CUST_ID,
          R.AMOUNT,
          R.ORDER_DATE
      FROM MIG_WORK.WF0002_SEG_02_OUT R
      WHERE NOT EXISTS (
          SELECT 1 FROM MIG_WORK.WF0002_SEG_01_OUT L WHERE L.CUST_ID = R.CUST_ID
      )
  ),
  -- tool 6: Formula -- appends MATCH_FLAG = "MATCHED" to the Join anchor.
  t6_formula AS (
      SELECT
          CUST_ID,
          NAME,
          CITY,
          TIER,
          ORDER_ID,
          AMOUNT,
          ORDER_DATE,
          'MATCHED' AS MATCH_FLAG
      FROM t5_join_j
  ),
  -- tool 7: Formula -- appends MATCH_FLAG = "NO_ORDERS" to the Left anchor.
  t7_formula AS (
      SELECT
          CUST_ID,
          NAME,
          CITY,
          TIER,
          'NO_ORDERS' AS MATCH_FLAG
      FROM t5_join_l
  ),
  -- tool 8: Formula -- appends MATCH_FLAG = "ORPHAN" to the Right anchor.
  t8_formula AS (
      SELECT
          ORDER_ID,
          CUST_ID,
          AMOUNT,
          ORDER_DATE,
          'ORPHAN' AS MATCH_FLAG
      FROM t5_join_r
  ),
  -- tool 9: Union by name, inputs stacked in #1, #2, #3 order. The output keeps the first input's
  -- field order and a field an input lacks arrives NULL, so each branch is projected into tool 6's
  -- column list and the missing ones are typed NULLs rather than bare NULLs.
  t9_union AS (
      SELECT
          CUST_ID,
          NAME,
          CITY,
          TIER,
          ORDER_ID,
          AMOUNT,
          ORDER_DATE,
          MATCH_FLAG
      FROM t6_formula
      UNION ALL
      SELECT
          CUST_ID,
          CAST(NULL AS VARCHAR) AS NAME,
          CAST(NULL AS VARCHAR) AS CITY,
          CAST(NULL AS VARCHAR) AS TIER,
          ORDER_ID,
          AMOUNT,
          ORDER_DATE,
          MATCH_FLAG
      FROM t8_formula
  )
  SELECT
      CUST_ID,
      NAME,
      CITY,
      TIER,
      ORDER_ID,
      AMOUNT,
      ORDER_DATE,
      MATCH_FLAG
  FROM t9_union;

  RETURN 'OK';
END;
$$;
