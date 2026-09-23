-- BROKEN ON PURPOSE -- do not fix: this is a fixture for tests/test_e2e_parity.py.
-- The mistake: tool 4's Filter is not translated at all -- the Summarize reads the joined
-- stream directly, a translator who took Left([PERIOD], 4) = [User.CurrentYear] for a
-- display-only condition. Every prior-year (REGION, PERIOD) the join produces is summarised
-- into REGION_ATTAINMENT, where the Alteryx run has no such row.
{{ config(materialized='table', alias='REGION_ATTAINMENT') }}
-- models/region_attainment.sql -- wf_0007 / seg_02 (tools 4-6): the target REGION_ATTAINMENT.
-- seg_02's CTE chain (tools 4 and 5) is repeated in models/attainment_history.sql: a model is one
-- CTE per tool and one model per contract output, so the two targets fed by tool 5's stream each
-- carry the chain themselves (translation_notes.md).
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py --target dbt and scripts/validate_dbt.py against simulator-generated
-- golden data, on dbt-duckdb.
WITH
-- tool 5: Summarize -- group by REGION, PERIOD; Sum TARGET -> TARGET_TOTAL, Sum ACTUAL ->
-- ACTUAL_TOTAL (NULLs skipped, all-NULL is NULL), Count -> LINES (rows, so COUNT(*)).
t5_summarize AS (
    SELECT
        REGION,
        PERIOD,
        CAST(SUM(TARGET) AS DECIMAL(19,2)) AS TARGET_TOTAL,
        CAST(SUM(ACTUAL) AS DECIMAL(19,2)) AS ACTUAL_TOTAL,
        COUNT(*) AS LINES
    FROM {{ ref('wf0007_seg_01_out') }}
    GROUP BY REGION, PERIOD
)
-- tool 6: Output Data (overwrite, logical REGION_ATTAINMENT)
SELECT
    REGION,
    PERIOD,
    TARGET_TOTAL,
    ACTUAL_TOTAL,
    LINES
FROM t5_summarize
