-- wf_0004 / seg_01 -- hand migration of samples/wf_0004/source/inventory.yxmd (tool 1).
-- The macro at tool 2 gets a segment of its own, so this segment is the Input tool alone: it
-- reads the mapped stock table and hands it to seg_02 as a work table.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0004_SEG_01(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- contract C4: each mapped table's name is built once, then referenced as IDENTIFIER(:<name>)
  LET STOCK_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.STOCK';

  -- ===========================================================================================
  -- Outbound stream 1_Output: the stock rows the macro in seg_02 cleans.
  -- ===========================================================================================
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0004_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data -- a yxdb file in the Alteryx estate, mapped to a Snowflake table and read
  -- by its logical name. The tool has no record limit and no query of its own, so the whole table
  -- is read and the projection is just its four declared fields, in their declared order.
  t1_input AS (
      SELECT
          SKU,
          WAREHOUSE,
          QTY,
          NOTE
      FROM IDENTIFIER(:STOCK_SRC)
  )
  SELECT
      SKU,
      WAREHOUSE,
      QTY,
      NOTE
  FROM t1_input;

  RETURN 'OK';
END;
$$;
