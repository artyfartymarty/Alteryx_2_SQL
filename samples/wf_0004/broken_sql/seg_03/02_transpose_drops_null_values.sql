-- BROKEN ON PURPOSE -- do not fix: this is a fixture for tests/test_e2e_parity.py.
-- The mistake: the Transpose is filtered to the cells that have a value, which is what
-- a bare UNPIVOT does (UNPIVOT INCLUDE NULLS would not) and what reaching for it gets. The
-- Transpose tool keeps NULL values, so this silently loses most of the long table's rows.
-- wf_0004 / seg_03 -- hand migration of samples/wf_0004/source/inventory.yxmd (tools 3-7).
-- Two Output Data tools terminate two branches of one chain (tool 6 off the Cross Tab, tool 7 off
-- the Transpose below it), so the shared prefix -- tools 3 and 4 -- is spelled out in both
-- statements; contract C4 allows no variable to hold a shared result and no work table is
-- materialised for it (see translation_notes.md).
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0004_SEG_03(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- contract C4: each mapped table's name is built once, then referenced as IDENTIFIER(:<name>)
  LET INVENTORY_BY_WH_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.INVENTORY_BY_WH';
  LET INVENTORY_LONG_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.INVENTORY_LONG';

  -- ===========================================================================================
  -- Output tool 6 (write mode Overwrite, logical INVENTORY_BY_WH): the Cross Tab's wide table.
  -- ===========================================================================================
  CREATE OR REPLACE TABLE IDENTIFIER(:INVENTORY_BY_WH_TGT) AS
  WITH
  -- tool 3: RegEx, method Parse, case-SENSITIVE -- appends FAMILY and ITEM_NO from the two
  -- capture groups and never drops a row: a SKU that does not match leaves both fields NULL.
  -- REGEXP_SUBSTR returns NULL on Snowflake when nothing matched, but the local DuckDB double
  -- returns an empty string instead, so the match is tested explicitly with REGEXP_LIKE and the
  -- NULL is written out. The pattern is anchored, so REGEXP_LIKE's whole-subject rule and the
  -- tool's own search agree.
  t3_regex AS (
      SELECT
          SKU,
          WAREHOUSE,
          QTY,
          NOTE,
          IFF(REGEXP_LIKE(SKU, '^([A-Z]+)-(\\d+)$'),
              REGEXP_SUBSTR(SKU, '^([A-Z]+)-(\\d+)$', 1, 1, 'c', 1), NULL)  AS FAMILY,
          IFF(REGEXP_LIKE(SKU, '^([A-Z]+)-(\\d+)$'),
              REGEXP_SUBSTR(SKU, '^([A-Z]+)-(\\d+)$', 1, 1, 'c', 2), NULL)  AS ITEM_NO
      FROM MIG_WORK.WF0004_SEG_02_OUT
  ),
  -- tool 4: Cross Tab -- group by SKU and FAMILY, header WAREHOUSE, data QTY, method Sum. The
  -- header columns are the frozen list the tool's MetaInfo records (EAST, NORTH, WEST), so the
  -- pivot is written as one conditional aggregate per column rather than discovered from the
  -- data; a warehouse outside that list has no column and is dropped, which is the tool's own
  -- rule. A group with no row for a warehouse is NULL and not zero, which SUM over an empty set
  -- already gives -- so nothing here may wrap it in COALESCE.
  t4_cross_tab AS (
      SELECT
          SKU,
          FAMILY,
          CAST(SUM(IFF(WAREHOUSE = 'EAST', QTY, NULL)) AS FLOAT)   AS EAST,
          CAST(SUM(IFF(WAREHOUSE = 'NORTH', QTY, NULL)) AS FLOAT)  AS NORTH,
          CAST(SUM(IFF(WAREHOUSE = 'WEST', QTY, NULL)) AS FLOAT)   AS WEST
      FROM t3_regex
      GROUP BY SKU, FAMILY
      ORDER BY SKU ASC NULLS FIRST, FAMILY ASC NULLS FIRST
  )
  SELECT
      SKU,
      FAMILY,
      EAST,
      NORTH,
      WEST
  FROM t4_cross_tab;

  -- ===========================================================================================
  -- Output tool 7 (write mode Overwrite, logical INVENTORY_LONG): the Transpose's long table.
  -- ===========================================================================================
  CREATE OR REPLACE TABLE IDENTIFIER(:INVENTORY_LONG_TGT) AS
  WITH
  -- tool 3: RegEx -- the same parse (see the first statement).
  t3_regex AS (
      SELECT
          SKU,
          WAREHOUSE,
          QTY,
          NOTE,
          IFF(REGEXP_LIKE(SKU, '^([A-Z]+)-(\\d+)$'),
              REGEXP_SUBSTR(SKU, '^([A-Z]+)-(\\d+)$', 1, 1, 'c', 1), NULL)  AS FAMILY,
          IFF(REGEXP_LIKE(SKU, '^([A-Z]+)-(\\d+)$'),
              REGEXP_SUBSTR(SKU, '^([A-Z]+)-(\\d+)$', 1, 1, 'c', 2), NULL)  AS ITEM_NO
      FROM MIG_WORK.WF0004_SEG_02_OUT
  ),
  -- tool 4: Cross Tab -- the same pivot (see the first statement).
  t4_cross_tab AS (
      SELECT
          SKU,
          FAMILY,
          CAST(SUM(IFF(WAREHOUSE = 'EAST', QTY, NULL)) AS FLOAT)   AS EAST,
          CAST(SUM(IFF(WAREHOUSE = 'NORTH', QTY, NULL)) AS FLOAT)  AS NORTH,
          CAST(SUM(IFF(WAREHOUSE = 'WEST', QTY, NULL)) AS FLOAT)   AS WEST
      FROM t3_regex
      GROUP BY SKU, FAMILY
  ),
  -- tool 5: Transpose -- key field SKU only, so FAMILY is dropped; one row per incoming row per
  -- data field, in the configured order EAST, NORTH, WEST. Written as three projections stacked
  -- with UNION ALL rather than a bare UNPIVOT, which drops NULL values (UNPIVOT INCLUDE NULLS
  -- would keep them too) -- the NULL warehouse cells are half the rows this target carries.
  t5_transpose AS (
      SELECT SKU, 'EAST'  AS NAME, EAST  AS VALUE FROM t4_cross_tab
      UNION ALL
      SELECT SKU, 'NORTH' AS NAME, NORTH AS VALUE FROM t4_cross_tab
      UNION ALL
      SELECT SKU, 'WEST'  AS NAME, WEST  AS VALUE FROM t4_cross_tab
  )
  SELECT
      SKU,
      NAME,
      VALUE
  FROM t5_transpose
  WHERE VALUE IS NOT NULL
  ORDER BY SKU ASC NULLS FIRST,
           CASE NAME WHEN 'EAST' THEN 1 WHEN 'NORTH' THEN 2 ELSE 3 END;

  RETURN 'OK';
END;
$$;
