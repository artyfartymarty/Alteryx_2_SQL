-- tool 2: Select -- CUSTOMER to String(10) (Alteryx truncates silently), STATUS renamed
-- ORDER_STATUS, NOTE deselected and dropped, then *Unknown appends ORDER_ID and REGION in
-- incoming order.
WITH t2_select AS (
    SELECT
        LEFT(CUSTOMER, 10) AS CUSTOMER,
        STATUS             AS ORDER_STATUS,
        ORDER_ID,
        REGION
    FROM MIG_COOKBOOK.IN_1
)
SELECT
    CUSTOMER,
    ORDER_STATUS,
    ORDER_ID,
    REGION
FROM t2_select