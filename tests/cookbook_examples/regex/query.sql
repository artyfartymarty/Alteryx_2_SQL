-- tool 2: RegEx (Parse) -- ^([A-Z]+)-(\d+)$ on SKU appends FAMILY (group 1) and ITEM_NO (group
-- 2); a non-matching row leaves both NULL. NULLIF(..., '') is a local-runtime workaround: this
-- runtime's REGEXP_SUBSTR returns '' rather than NULL for "no match" once translated to DuckDB
-- (a documented deviation, index.md's "Local verification" note); it is a no-op on real Snowflake,
-- where REGEXP_SUBSTR already returns NULL there.
WITH t2_regex AS (
    SELECT
        SKU,
        NULLIF(REGEXP_SUBSTR(SKU, '^([A-Z]+)-(\d+)$', 1, 1, 'e', 1), '') AS FAMILY,
        NULLIF(REGEXP_SUBSTR(SKU, '^([A-Z]+)-(\d+)$', 1, 1, 'e', 2), '') AS ITEM_NO
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    SKU,
    FAMILY,
    ITEM_NO
FROM t2_regex