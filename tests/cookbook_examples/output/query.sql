-- tool 2: Output Data (write mode Overwrite, logical CUSTOMERS_OUT) -- an Output tool is a write,
-- not a computation: it has no CTE of its own (translation_notes.md's "no CTE" convention), it is
-- this CREATE OR REPLACE TABLE statement whose SELECT ends at the upstream CTE.
CREATE OR REPLACE TABLE MIG_COOKBOOK.OUT_2 AS
SELECT
    CUST_ID,
    NAME,
    TIER,
    CREDIT_LIMIT
FROM MIG_COOKBOOK.IN_1