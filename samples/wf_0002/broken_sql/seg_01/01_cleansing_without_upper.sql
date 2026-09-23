-- BROKEN ON PURPOSE -- do not fix: this is a fixture for tests/test_e2e_parity.py.
-- The mistake: tool 2's `modify case = upper` option is not translated, so NAME and CITY keep
-- the casing they arrived with. Everything else about the rows is right, which is why the
-- difference is nothing but case.
-- wf_0002 / seg_01 -- hand migration of samples/wf_0002/source/customer_orders.yxmd tools 1-2
-- (Tool Container 100, "Prep customers"). Its one outbound stream, 2_Output, feeds the Join's
-- Left anchor in seg_03, so it is materialised as the segment's primary work table.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0002_SEG_01(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- contract C4: each mapped table's name is built once, then referenced as IDENTIFIER(:<name>)
  LET CUSTOMERS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.CUSTOMERS';

  -- Outbound stream 2_Output -> seg_03 (Join, Left anchor).
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0002_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data -- the CRM customer extract, read by its logical name from mappings.yaml.
  t1_input AS (
      SELECT
          CUST_ID,
          NAME,
          CITY,
          TIER
      FROM IDENTIFIER(:CUSTOMERS_SRC)
  ),
  -- tool 2: Data Cleansing (Cleanse.yxmc) on NAME and CITY only, with the options applied in the
  -- macro's own order: replace NULL strings with blank, then trim whitespace, then modify case.
  -- COALESCE comes first for that reason -- trimming a NULL would leave it NULL, and the tool
  -- promises a blank. TIER and CUST_ID are not in the field list, so they pass through untouched.
  t2_data_cleansing AS (
      SELECT
          CUST_ID,
          TRIM(COALESCE(NAME, '')) AS NAME,
          TRIM(COALESCE(CITY, '')) AS CITY,
          TIER
      FROM t1_input
  )
  SELECT
      CUST_ID,
      NAME,
      CITY,
      TIER
  FROM t2_data_cleansing;

  RETURN 'OK';
END;
$$;
