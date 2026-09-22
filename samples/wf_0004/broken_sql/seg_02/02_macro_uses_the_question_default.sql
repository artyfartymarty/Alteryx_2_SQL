-- BROKEN ON PURPOSE -- do not fix: this is a fixture for tests/test_e2e_parity.py.
-- The mistake: the macro's own NumericUpDown default (MinQty = 0) is translated instead of
-- the value the parent workflow passes (<Value name="MinQty">1</Value>). A reader who opened
-- clean_codes.yxmc and translated what it says, without going back to the tool that calls it,
-- gets exactly this: every zero-quantity row survives.
-- wf_0004 / seg_02 -- hand migration of samples/wf_0004/source/inventory.yxmd (tool 2), the macro
-- Supporting_Macros/clean_codes.yxmc. A macro is not a tool with a pattern of its own: it is a
-- workflow, so its sub-DAG is inlined here one CTE per INNER tool. Each CTE is named
-- `t2_macro_m<inner tool id>_<inner type>` -- the parent tool id first, so the segment DAG's node
-- 2 still has its CTEs, then the macro's own tool ids so a reader can line them up with
-- clean_codes.yxmc. The question value MinQty = 1 comes from the parent's <Value name="MinQty">,
-- overriding the macro's own default of 0.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0004_SEG_02(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- ===========================================================================================
  -- Outbound stream 2_Output5: the macro's Output5 anchor, named after its macro_output tool 5.
  -- ===========================================================================================
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0004_SEG_02_OUT AS
  WITH
  -- tool 2 (macro clean_codes.yxmc, inner tool 1: Macro Input) -- the macro's Input1 anchor, fed
  -- by seg_01's work table. A Macro Input is a parameter, not a read: it carries the incoming
  -- stream through unchanged.
  t2_macro_m1_macro_input AS (
      SELECT
          SKU,
          WAREHOUSE,
          QTY,
          NOTE
      FROM MIG_WORK.WF0004_SEG_01_OUT
  ),
  -- tool 2 (macro clean_codes.yxmc, inner tool 2: RegEx, method Replace, case-insensitive,
  -- CopyUnmatched) -- normalises a SKU to FAMILY-NUMBER in place. Alteryx's `$1`/`$2` become
  -- `\1`/`\2`; the `i` parameter is the CaseInsensitve flag; occurrence 0 replaces every match,
  -- which is what the tool does. CopyUnmatched needs no spelling of its own: REGEXP_REPLACE
  -- already returns the subject unchanged when nothing matches -- but that is exactly the
  -- behaviour the flag asks for, so it is written down rather than assumed.
  t2_macro_m2_regex AS (
      SELECT
          REGEXP_REPLACE(SKU, '^\\s*([A-Za-z]+)[- ]?(\\d+)\\s*$', '\\1-\\2', 1, 0, 'i') AS SKU,
          WAREHOUSE,
          QTY,
          NOTE
      FROM t2_macro_m1_macro_input
  ),
  -- tool 2 (macro clean_codes.yxmc, inner tool 3: Formula Uppercase([SKU])) -- updates SKU in
  -- place, so the field keeps its position rather than being appended.
  t2_macro_m3_formula AS (
      SELECT
          UPPER(SKU) AS SKU,
          WAREHOUSE,
          QTY,
          NOTE
      FROM t2_macro_m2_regex
  ),
  -- tool 2 (macro clean_codes.yxmc, inner tool 4, anchor T: Filter [QTY] >= [%Question.MinQty%])
  -- -- the question value is 1, so the literal is 1 and not the macro's default 0. Only the True
  -- anchor is wired to the Macro Output; a NULL QTY makes the comparison NULL, which is not true,
  -- so SQL's three-valued WHERE drops it exactly as Alteryx sends it to the unwired F.
  t2_macro_m4_filter_t AS (
      SELECT
          SKU,
          WAREHOUSE,
          QTY,
          NOTE
      FROM t2_macro_m3_formula
      WHERE QTY >= 0
  )
  -- tool 2 (macro clean_codes.yxmc, inner tool 5: Macro Output) -- the anchor the parent reads;
  -- like the Macro Input it carries the stream through, so it is this statement's final SELECT.
  SELECT
      SKU,
      WAREHOUSE,
      QTY,
      NOTE
  FROM t2_macro_m4_filter_t;

  RETURN 'OK';
END;
$$;
