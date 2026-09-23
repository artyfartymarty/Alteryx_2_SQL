-- wf_0003 / seg_01 -- hand migration of samples/wf_0003/source/gl_period_close.yxmd (tools 1-3,
-- the "Extract" Tool Container). The segment ends on an outbound stream, so its only output is
-- the work table seg_02 reads; no Output Data tool terminates here.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py and scripts/validate_segment.py against simulator-generated golden data.
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0003_SEG_01(
    SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN

  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;

  -- contract C4: each mapped table's name is built once, then referenced as IDENTIFIER(:<name>)
  LET GL_LEDGER_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.GL_LEDGER';

  -- ===========================================================================================
  -- Outbound stream 3_Output: the extract seg_02 sorts, de-duplicates and summarises.
  -- ===========================================================================================
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0003_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data -- an ODBC source whose own query is part of the tool's configuration.
  -- The procedure reads the whole mapped table, so the query's six columns AND its
  -- `WHERE AMOUNT <> 0` are re-applied here; dropping the predicate would let rows through that
  -- Alteryx never saw.
  t1_input AS (
      SELECT
          ACCT,
          PERIOD,
          POSTED,
          AMOUNT,
          REGION,
          ENTRY_ID
      FROM IDENTIFIER(:GL_LEDGER_SRC)
      WHERE AMOUNT <> 0
  ),
  -- tool 2 (anchor T): Filter [REGION] = [User.Region], with the workflow constant User.Region
  -- resolved to the literal 'EMEA'. Only the True anchor is wired. A NULL REGION makes the
  -- expression NULL, which is not true, so SQL's own three-valued WHERE drops exactly the rows
  -- Alteryx sends to the unwired False anchor.
  t2_filter_t AS (
      SELECT
          ACCT,
          PERIOD,
          POSTED,
          AMOUNT,
          REGION,
          ENTRY_ID
      FROM t1_input
      WHERE REGION = 'EMEA'
  ),
  -- tool 3: DateTime -- POSTED is dd/mm/yyyy text read into a DateTime field, so the format is
  -- spelled out and text the format cannot read (31/02/2026) becomes NULL rather than raising.
  t3_datetime AS (
      SELECT
          ACCT,
          PERIOD,
          POSTED,
          AMOUNT,
          REGION,
          ENTRY_ID,
          TRY_TO_TIMESTAMP_NTZ(POSTED, 'DD/MM/YYYY') AS POSTED_DT
      FROM t2_filter_t
  )
  SELECT
      ACCT,
      PERIOD,
      POSTED,
      AMOUNT,
      REGION,
      ENTRY_ID,
      POSTED_DT
  FROM t3_datetime;

  RETURN 'OK';
END;
$$;
