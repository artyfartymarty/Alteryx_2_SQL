-- tool 2: Data Cleansing (Cleanse.yxmc) on NAME only -- the macro's own option order: replace
-- NULL strings with blank, then remove tabs/linebreaks/duplicate spaces, then trim, then modify
-- case. COALESCE is innermost: trimming a NULL would leave it NULL, and the tool promises blank.
-- CODE is not in the field list, so it passes through untouched.
WITH t2_data_cleansing AS (
    SELECT
        UPPER(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(COALESCE(NAME, ''), '[\t\r\n]', ' '), ' {2,}', ' '))) AS NAME,
        CODE
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    NAME,
    CODE
FROM t2_data_cleansing