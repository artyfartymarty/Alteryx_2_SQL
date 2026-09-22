-- wf_0001 / seg_01 -- hand migration of samples/wf_0001/source/sales_summary.yxmd (tools 1-8).
-- Two Output Data tools terminate two branches of one chain, so the shared prefix (tools 1-3)
-- is spelled out in both statements; no work table is materialised for it (see translation_notes.md).
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0001_SEG_01(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- ===========================================================================================
  -- Output tool 7 (write mode Overwrite, logical SALES_SUMMARY): the Filter's True branch.
  -- ===========================================================================================
  CREATE OR REPLACE TABLE IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.SALES_SUMMARY') AS
  WITH
  -- tool 1: Input Data -- the orders extract, read by its logical name from mappings.yaml.
  t1_input AS (
      SELECT
          ORDER_ID,
          CUSTOMER,
          REGION,
          AMOUNT_TXT,
          QTY,
          ORDER_DATE,
          STATUS
      FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')
  ),
  -- tool 2: Select -- CUSTOMER to String(10) (Alteryx truncates silently), STATUS renamed
  -- ORDER_STATUS, then *Unknown appends the five unlisted fields in incoming order.
  t2_select AS (
      SELECT
          LEFT(CUSTOMER, 10) AS CUSTOMER,
          STATUS             AS ORDER_STATUS,
          ORDER_ID,
          REGION,
          AMOUNT_TXT,
          QTY,
          ORDER_DATE
      FROM t1_input
  ),
  -- tool 3 (anchor T): Filter [REGION] != "WEST" -- the True branch keeps only rows where the
  -- expression is really true; a NULL REGION makes it NULL, which is not true, so SQL's own
  -- three-valued WHERE already drops those rows here.
  t3_filter_t AS (
      SELECT
          CUSTOMER,
          ORDER_STATUS,
          ORDER_ID,
          REGION,
          AMOUNT_TXT,
          QTY,
          ORDER_DATE
      FROM t2_select
      WHERE REGION <> 'WEST'
  ),
  -- tool 4: Formula -- three expressions in order; the second and third read the AMOUNT the
  -- first wrote, so AMOUNT is computed in a nested SELECT and the other two read it from there.
  -- AMOUNT is a Double field, so the ToNumber result is stored as a FLOAT; NET's arithmetic then
  -- goes through NUMBER(38,10) (never FLOAT) so Round's half-away-from-zero lands on the same
  -- cent the oracle computes in exact decimals.
  t4_formula AS (
      SELECT
          CUSTOMER,
          ORDER_STATUS,
          ORDER_ID,
          REGION,
          AMOUNT_TXT,
          QTY,
          ORDER_DATE,
          AMOUNT,
          CAST(ROUND(CAST(AMOUNT AS NUMBER(38,10))
                     * IFF(QTY >= 10, CAST(0.9 AS NUMBER(38,10)), CAST(1 AS NUMBER(38,10))),
                     2) AS FLOAT)                                         AS NET,
          CASE WHEN AMOUNT >= 1000 THEN 'LARGE'
               WHEN AMOUNT >= 100  THEN 'MEDIUM'
               ELSE 'SMALL'
          END                                                             AS SIZE_BAND
      FROM (
          SELECT
              CUSTOMER,
              ORDER_STATUS,
              ORDER_ID,
              REGION,
              AMOUNT_TXT,
              QTY,
              ORDER_DATE,
              TRY_TO_DOUBLE(AMOUNT_TXT) AS AMOUNT
          FROM t3_filter_t
      ) f4
  ),
  -- tool 5: Summarize -- group by REGION, SIZE_BAND. Alteryx's Count counts rows and
  -- CountNonNull counts values, so ORDERS is COUNT(*) and PRICED_ORDERS is COUNT(AMOUNT).
  -- Sum runs over the exact decimal value of each NET, then lands back in a Double field.
  t5_summarize AS (
      SELECT
          REGION,
          SIZE_BAND,
          CAST(SUM(CAST(NET AS NUMBER(38,10))) AS FLOAT) AS TOTAL_NET,
          COUNT(*)                                       AS ORDERS,
          COUNT(AMOUNT)                                  AS PRICED_ORDERS,
          MAX(ORDER_DATE)                                AS LAST_ORDER
      FROM t4_formula
      GROUP BY REGION, SIZE_BAND
  ),
  -- tool 6: Sort -- TOTAL_NET descending then REGION ascending. Alteryx sorts NULL first
  -- ascending and last descending, which the explicit NULLS clauses spell out.
  t6_sort AS (
      SELECT
          REGION,
          SIZE_BAND,
          TOTAL_NET,
          ORDERS,
          PRICED_ORDERS,
          LAST_ORDER
      FROM t5_summarize
      ORDER BY TOTAL_NET DESC NULLS LAST, REGION ASC NULLS FIRST
  )
  SELECT
      REGION,
      SIZE_BAND,
      TOTAL_NET,
      ORDERS,
      PRICED_ORDERS,
      LAST_ORDER
  FROM t6_sort;

  -- ===========================================================================================
  -- Output tool 8 (write mode Overwrite, logical EXCLUDED_ORDERS): the Filter's False branch.
  -- ===========================================================================================
  CREATE OR REPLACE TABLE IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.EXCLUDED_ORDERS') AS
  WITH
  -- tool 1: Input Data -- the same orders extract (see the first statement).
  t1_input AS (
      SELECT
          ORDER_ID,
          CUSTOMER,
          REGION,
          AMOUNT_TXT,
          QTY,
          ORDER_DATE,
          STATUS
      FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')
  ),
  -- tool 2: Select -- the same projection (see the first statement).
  t2_select AS (
      SELECT
          LEFT(CUSTOMER, 10) AS CUSTOMER,
          STATUS             AS ORDER_STATUS,
          ORDER_ID,
          REGION,
          AMOUNT_TXT,
          QTY,
          ORDER_DATE
      FROM t1_input
  ),
  -- tool 3 (anchor F): Filter [REGION] != "WEST" -- the False branch takes the rows the
  -- expression makes false AND the rows it makes NULL, which is why `IS NULL` is spelled out:
  -- a plain `WHERE REGION = 'WEST'` would silently lose every NULL-region row.
  t3_filter_f AS (
      SELECT
          CUSTOMER,
          ORDER_STATUS,
          ORDER_ID,
          REGION,
          AMOUNT_TXT,
          QTY,
          ORDER_DATE
      FROM t2_select
      WHERE NOT (REGION <> 'WEST') OR (REGION) IS NULL
  )
  SELECT
      CUSTOMER,
      ORDER_STATUS,
      ORDER_ID,
      REGION,
      AMOUNT_TXT,
      QTY,
      ORDER_DATE
  FROM t3_filter_f;

  RETURN 'OK';
END;
$$;
