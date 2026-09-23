-- models/history.sql
-- tool 2: Output Data (Update; Insert if new on ID -> merge, logical HISTORY). The alias is not
-- optional: dbt refuses to adopt a pre-existing upper-case HISTORY table for a lower-case model
-- name ("approximate match"), so every target model names itself explicitly.
{{ config(materialized='incremental', incremental_strategy='merge', unique_key=['ID'], alias='HISTORY') }}
select ID, REGION, AMOUNT from {{ source('src', 'IN_1') }}
