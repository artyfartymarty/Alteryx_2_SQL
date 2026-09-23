{{ config(materialized='incremental', incremental_strategy='merge', unique_key=['REGION', 'PERIOD'], alias='ATTAINMENT_HISTORY') }}
-- models/attainment_history.sql -- wf_0007 / seg_02 (tools 4, 5 and 7): the target
-- ATTAINMENT_HISTORY. The same tool 4 and tool 5 CTEs as models/region_attainment.sql (one model
-- per contract output). No is_incremental() filter: Update; Insert if new writes every row the
-- run produces, and the merge on the full key (REGION, PERIOD) decides update or insert.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py --target dbt and scripts/validate_dbt.py against simulator-generated
-- golden data, on dbt-duckdb.
WITH
-- tool 4: Filter Left([PERIOD], 4) = [User.CurrentYear], True anchor. User.CurrentYear is the
-- text constant '2026', inlined. A NULL condition is not true, so such a row would be dropped.
t4_filter_t AS (
    SELECT
        REGION,
        PERIOD,
        TARGET,
        ACTUAL
    FROM {{ ref('wf0007_seg_01_out') }}
    WHERE LEFT(PERIOD, 4) = '2026'
),
-- tool 5: Summarize -- group by REGION, PERIOD; Sum TARGET -> TARGET_TOTAL, Sum ACTUAL ->
-- ACTUAL_TOTAL (NULLs skipped, all-NULL is NULL), Count -> LINES (rows, so COUNT(*)).
t5_summarize AS (
    SELECT
        REGION,
        PERIOD,
        CAST(SUM(TARGET) AS DECIMAL(19,2)) AS TARGET_TOTAL,
        CAST(SUM(ACTUAL) AS DECIMAL(19,2)) AS ACTUAL_TOTAL,
        COUNT(*) AS LINES
    FROM t4_filter_t
    GROUP BY REGION, PERIOD
)
-- tool 7: Output Data (Update; Insert if new on REGION, PERIOD -> merge, logical ATTAINMENT_HISTORY)
SELECT
    REGION,
    PERIOD,
    TARGET_TOTAL,
    ACTUAL_TOTAL,
    LINES
FROM t5_summarize
