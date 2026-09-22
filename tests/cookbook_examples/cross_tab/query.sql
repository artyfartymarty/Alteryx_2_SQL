-- tool 2: Cross Tab -- pivot QTY by WH into a frozen header list (EAST, NORTH, WEST); a WH value
-- outside that list (SOUTH here) is not one of the pivoted columns and contributes to none of
-- them, same as the oracle's own silent-drop rule for an unrecognised header. A (SKU, header)
-- combination with no rows, or only a NULL QTY, is NULL -- never zero -- which SQL's own SUM
-- already gives for free.
WITH t2_cross_tab AS (
    SELECT
        SKU,
        CAST(SUM(CASE WHEN WH = 'EAST'  THEN QTY END) AS FLOAT) AS EAST,
        CAST(SUM(CASE WHEN WH = 'NORTH' THEN QTY END) AS FLOAT) AS NORTH,
        CAST(SUM(CASE WHEN WH = 'WEST'  THEN QTY END) AS FLOAT) AS WEST
    FROM MIG_COOKBOOK.IN_1
    GROUP BY SKU
)
SELECT
    SKU,
    EAST,
    NORTH,
    WEST
FROM t2_cross_tab