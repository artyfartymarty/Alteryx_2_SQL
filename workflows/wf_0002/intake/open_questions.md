# Open questions - wf_0002 (owner: @wf_owner)

## Blocking (translation waits)
- [x] Q1 - Tool 1 Input Data reads \\fileserver\crm\customers.yxdb (4 fields, 6 rows).
      Proposed: CRM.RAW.CUSTOMERS (4/4 columns match).
      Confirm or supply another: CRM.RAW.CUSTOMERS
- [x] Q2 - Tool 3 Input Data reads \\fileserver\crm\orders_export.csv (4 fields).
      Proposed: CRM.RAW.ORDERS_EXPORT (4/4 columns match).
      Confirm or supply another: CRM.RAW.ORDERS_EXPORT
- [x] Q3 - Tool 10 Output Data writes dbo.CUSTOMER_ORDER_FACT via alias:dw_sales/dbo.customer_order_fact (8 fields), mode append.
      Proposed: ANALYTICS.CURATED.CUSTOMER_ORDER_FACT (8/8 columns match).
      Confirm or supply another: ANALYTICS.CURATED.CUSTOMER_ORDER_FACT

## Non-blocking (proceeds with the assumption)
(none)
