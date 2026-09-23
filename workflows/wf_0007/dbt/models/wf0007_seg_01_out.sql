{{ config(materialized='table') }}
-- models/wf0007_seg_01_out.sql -- wf_0007 / seg_01 (tools 1-3): the work stream 3_J, which lands
-- in MIG_WORK.WF0007_SEG_01_OUT. Hand migration of samples/wf_0007/source/regional_targets.yxmd.
-- Nothing in this file has ever run on Snowflake or on Alteryx: it is checked locally by
-- scripts/compile_check.py --target dbt and scripts/validate_dbt.py against simulator-generated
-- golden data, on dbt-duckdb.
WITH
-- tool 1: Input Data -- plan targets, logical TARGETS.
t1_input AS (
    SELECT
        REGION,
        PERIOD,
        TARGET
    FROM {{ source('src', 'TARGETS') }}
),
-- tool 2: Input Data -- booked actuals, one row per sales line, logical ACTUALS.
t2_input AS (
    SELECT
        REGION,
        PERIOD,
        ACTUAL
    FROM {{ source('src', 'ACTUALS') }}
),
-- tool 3: Join on REGION, PERIOD -- the J anchor only (L and R are wired to nothing), so an inner
-- join. A NULL key matches nothing, which `=` keeps; a key with several actual lines pairs the
-- target with each of them. Right_REGION and Right_PERIOD are deselected.
t3_join_j AS (
    SELECT
        l.REGION,
        l.PERIOD,
        l.TARGET,
        r.ACTUAL
    FROM t1_input AS l
    INNER JOIN t2_input AS r
        ON l.REGION = r.REGION
       AND l.PERIOD = r.PERIOD
)
SELECT
    REGION,
    PERIOD,
    TARGET,
    ACTUAL
FROM t3_join_j
