"""Regenerate every `samples/wf_*/golden_inputs/**` CSV and `sample.json` (plan task 3).

Run from the repo root:  .venv/Scripts/python.exe samples/_tools/make_golden_inputs.py

The rows here are the readable source of the golden sets: each one is chosen to exercise a
behaviour the plan names, and `samples/wf_000N/README.md` says which row does what. Writing
them through `lib.typed_csv.write_table` is what guarantees contract C1 (`\\N` for NULL, plain
decimal floats, LF endings, a `.schema.json` sidecar per file), so the files are never
hand-edited. Field lists must stay identical to the input tool's `MetaInfo` in the `.yxmd`;
`tests/test_samples_wellformed.py` fails if they drift apart.

Only inputs live here. Expected outputs are produced later by `scripts/dev/alteryx_sim.py`.
Nothing in this file has been run against Alteryx.
"""
from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import io, typed_csv  # noqa: E402

SAMPLES = ROOT / "samples"


def F(name: str, type_: str, size: int, scale: int | None = None) -> dict:
    return {"name": name, "type": type_, "size": size, "scale": scale}


def write(wf: str, golden_set: str, tool_id: str, fields: list[dict], rows: list) -> None:
    typed_csv.write_table(SAMPLES / wf / "golden_inputs" / golden_set / f"{tool_id}.csv",
                          {"fields": fields, "rows": [list(r) for r in rows]})


def write_target(wf: str, golden_set: str, logical: str, fields: list[dict], rows: list) -> None:
    typed_csv.write_table(
        SAMPLES / wf / "golden_inputs" / "targets_before" / golden_set / f"{logical}.csv",
        {"fields": fields, "rows": [list(r) for r in rows]})


# ---------------------------------------------------------------- wf_0001

ORDERS = [F("ORDER_ID", "Int32", 4), F("CUSTOMER", "V_String", 50), F("REGION", "V_String", 20),
          F("AMOUNT_TXT", "V_String", 20), F("QTY", "Int32", 4), F("ORDER_DATE", "Date", 10),
          F("STATUS", "V_String", 10)]

ORDERS_NORMAL = [
    (1, "Acme Corporation", "EAST", "1500.00", 2, "2026-08-03", "SHIPPED"),
    (2, "Bolt Ltd", "EAST", "250.005", 1, "2026-08-04", "SHIPPED"),
    (3, "Cinder Works", "NORTH", "33.35", 10, "2026-08-05", "SHIPPED"),
    (4, "Delta", "WEST", "900.00", 3, "2026-08-06", "SHIPPED"),
    (5, "Echo Supplies", None, "120.00", 1, "2026-08-07", "HOLD"),
    (6, "Foxtrot", "SOUTH", "abc", 4, "2026-08-10", "SHIPPED"),
    (7, "Gamma Industries", "SOUTH", "1,200.50", 2, "2026-08-11", "SHIPPED"),
    (8, "Helio", "NORTH", "99.99", 5, "2026-08-12", "SHIPPED"),
    (9, "Iris Trading", "NORTH", "100.00", 1, "2026-08-13", "SHIPPED"),
    (10, "Juno", "EAST", "1000.00", 1, "2026-08-14", "SHIPPED"),
    (11, "Kilo Mercantile", "EAST", "45.50", 12, "2026-08-17", "SHIPPED"),
    (12, "Lima", "WEST", "75.00", 1, "2026-08-18", "CANCELLED"),
    (13, "Mango", "SOUTH", "0.00", None, "2026-08-19", "SHIPPED"),
    (14, "Nova", "NORTH", "-50.25", 1, "2026-08-20", "RETURNED"),
    (15, "Omega Partners", "EAST", "2500.75", 20, "2026-08-21", "SHIPPED"),
    (16, "Papa", "SOUTH", "150.00", 10, "2026-08-24", "SHIPPED"),
    (17, "Quebec Holdings", "NORTH", "310.10", 9, "2026-08-25", "SHIPPED"),
    (18, "Romeo", "EAST", "", 1, "2026-08-26", "SHIPPED"),
    (19, "Sierra Logistics", "SOUTH", "888.88", 11, "2026-08-27", "SHIPPED"),
    (20, "Tango", "WEST", "60.00", 1, "2026-08-28", "SHIPPED"),
]

ORDERS_PERIOD_END = [
    (101, "Acme Corporation", "EAST", "500.00", 1, "2026-06-30", "SHIPPED"),
    (102, "Bolt Ltd", "EAST", "125.25", 10, "2026-07-31", "SHIPPED"),
    (103, "Cinder Works", "NORTH", "75.00", 2, "2026-08-31", "SHIPPED"),
    (104, "Delta", "WEST", "410.00", 1, "2026-08-31", "SHIPPED"),
    (105, "Echo Supplies", None, "60.00", 1, "2026-09-30", "HOLD"),
    (106, "Foxtrot", "SOUTH", "1100.00", 10, "2026-03-31", "SHIPPED"),
    (107, "Gamma Industries", "SOUTH", "2.675", 1, "2026-12-31", "SHIPPED"),
    (108, "Helio", "NORTH", "999.99", 1, "2026-08-31", "SHIPPED"),
    (109, "Iris Trading", "EAST", "abc", 3, "2026-09-30", "SHIPPED"),
    (110, "Juno", "NORTH", "250.00", 10, "2026-06-30", "SHIPPED"),
]

ORDERS_EDGE = [
    (None, None, None, None, None, None, None),
    (101, "Ångström Fjörd Ñuñez — 日本語テスト", "EAST", "0.005", 1, "2024-02-29", "SHIPPED"),
    (102, "X" * 50, "N" * 20, "-0.0", 10, "2026-08-31", "S" * 10),
    (103, "Dup Co", "WEST", "10.00", 1, "2026-01-31", "SHIPPED"),
    (103, "Dup Co", "WEST", "10.00", 1, "2026-01-31", "SHIPPED"),
    (104, "Half Cent", "EAST", "2.675", 1, "2026-03-31", "SHIPPED"),
    (105, "Bound", "NORTH", "1000.00", 10, "2026-06-30", "SHIPPED"),
    (106, "Tiny", "SOUTH", "0.004", 1, "2026-04-30", "SHIPPED"),
]

# ---------------------------------------------------------------- wf_0002

CUSTOMERS = [F("CUST_ID", "Int32", 4), F("NAME", "V_WString", 60),
             F("CITY", "V_String", 40), F("TIER", "V_String", 10)]
# The CSV Input tool reads every column as text, so this tool's golden data is all strings.
ORDERS_EXPORT = [F("ORDER_ID", "V_String", 254), F("CUST_ID", "V_String", 254),
                 F("AMOUNT", "V_String", 254), F("ORDER_DATE", "V_String", 254)]
FACT = [F("CUST_ID", "Int32", 4), F("NAME", "V_WString", 60), F("CITY", "V_String", 40),
        F("TIER", "V_String", 10), F("ORDER_ID", "Int32", 4), F("AMOUNT", "Double", 8),
        F("ORDER_DATE", "Date", 10), F("MATCH_FLAG", "V_String", 12)]

CUSTOMERS_NORMAL = [
    (1, "  aCme corporation  ", "Brisbane", "GOLD"),
    (2, "Bolt Ltd", "Sydney", "SILVER"),
    (3, "Cinder Works", "Perth", "BRONZE"),
    (4, "Delta Pty", "Hobart", "GOLD"),
    (5, None, "Darwin", "SILVER"),
    (6, "Echo Supplies", "  adelaide  ", "BRONZE"),
]

ORDERS_EXPORT_NORMAL = [
    ("1001", "1", "150.00", "2026-08-03"),
    ("1002", "1", "75.50", "2026-08-04"),
    ("1003", "2", "220.25", "2026-08-05"),
    ("1004", "3", "abc", "2026-08-06"),
    ("1005", "3", "310.00", "2026-02-30"),
    ("1006", "99", "45.00", "2026-08-07"),
    ("1007", "6", "99.99", "2026-08-10"),
    ("1008", "2", "", "2026-08-11"),
    ("1009", "5", "10.00", "2026-08-12"),
]

CUSTOMERS_PERIOD_END = CUSTOMERS_NORMAL
ORDERS_EXPORT_PERIOD_END = [
    ("2001", "1", "500.00", "2026-06-30"),
    ("2002", "2", "125.25", "2026-07-31"),
    ("2003", "3", "75.00", "2026-08-31"),
    ("2004", "6", "410.00", "2026-09-30"),
    ("2005", "99", "60.00", "2026-08-31"),
    ("2006", "5", "1100.00", "2026-03-31"),
]

CUSTOMERS_EDGE = [
    (None, None, None, None),
    (10, "Ǆurđa Ćosić — 東京商事", "Ürümqi", "PLATINUM"),
    (11, "W" * 60, "C" * 40, "T" * 10),
    (12, "Dup Customer", "Dupville", "GOLD"),
    (12, "Dup Customer", "Dupville", "GOLD"),
]

ORDERS_EXPORT_EDGE = [
    (None, None, None, None),
    ("3001", "10", "-0.0", "2024-02-29"),
    ("3002", "11", "0.005", "2026-08-31"),
    ("3003", "12", "10.00", "2026-01-31"),
    ("3003", "12", "10.00", "2026-01-31"),
    ("3004", "12", "77.00", "D" * 254),
    ("3005", "13", "not a number", "31/12/2026"),
]

FACT_BEFORE = [
    (7, "PRIOR RUN CO", "GEELONG", "GOLD", 900, 12.0, "2026-07-31", "MATCHED"),
    (8, "OLD ORPHAN", None, None, 901, 5.0, "2026-07-31", "ORPHAN"),
]

# ---------------------------------------------------------------- wf_0003

LEDGER = [F("ACCT", "V_String", 20), F("PERIOD", "V_String", 7), F("POSTED", "V_String", 10),
          F("AMOUNT", "FixedDecimal", 19, 2), F("REGION", "V_String", 10),
          F("ENTRY_ID", "Int64", 8)]
GL_SUMMARY = [F("ACCT", "V_String", 20), F("PERIOD", "V_String", 7),
              F("TOTAL", "FixedDecimal", 19, 2), F("CLOSING_BAL", "Double", 8),
              F("ENTRIES", "Int64", 8), F("HAS_PERIOD_END", "V_String", 1),
              F("LOADED_FLAG", "V_String", 1)]

# No row may have AMOUNT 0 or NULL: the input tool's own SQL ends in `WHERE AMOUNT <> 0`, and
# the translated procedure re-applies that predicate, so such a row would break parity.
LEDGER_NORMAL = [
    ("4000", "2026-08", "03/08/2026", D("100.00"), "EMEA", 1),
    ("4000", "2026-08", "03/08/2026", D("150.00"), "EMEA", 2),
    ("4000", "2026-08", "31/08/2026", D("200.00"), "EMEA", 3),
    ("4000", "2026-08", "15/08/2026", D("-50.00"), "EMEA", 4),
    ("5000", "2026-08", "31/08/2026", D("75.25"), "EMEA", 5),
    ("5000", "2026-08", "31/02/2026", D("10.00"), "EMEA", 6),
    ("5000", "2026-07", "30/07/2026", D("20.00"), "EMEA", 7),
    ("6000", "2026-08", "29/02/2024", D("33.33"), "EMEA", 8),
    ("6000", "2026-08", "01/08/2026", D("44.44"), "AMER", 9),
    ("6000", "2026-08", "02/08/2026", D("55.55"), None, 10),
    ("7000", "2026-08", "10/08/2026", D("12.50"), "EMEA", 11),
    ("7000", "2026-08", "10/08/2026", D("12.50"), "EMEA", 12),
    ("7000", "2026-09", "01/09/2026", D("99.99"), "EMEA", 13),
    # 14: a fifth row for (4000, 2026-08), posted after the other four (01/09/2026 sorts last in
    # tool 4's ACCT, POSTED_DT order) and large enough and negative enough to pull the running
    # balance back down below its peak from entry 3. This is what separates Summarize's `Last`
    # (MAX_BY(RUN_BAL, RECORD_ID), the group's actual last row) from a plain MAX(RUN_BAL): before
    # this row, every group's running balance happened to peak on its last row, so the two gave
    # the same CLOSING_BAL and a translator's `MAX` mistake passed undetected (fix round 1).
    ("4000", "2026-08", "01/09/2026", D("-400.00"), "EMEA", 14),
]

LEDGER_PERIOD_END = [
    ("4000", "2026-08", "31/08/2026", D("300.00"), "EMEA", 21),
    ("4000", "2026-07", "31/07/2026", D("120.00"), "EMEA", 22),
    ("5000", "2026-08", "31/08/2026", D("80.00"), "EMEA", 23),
    ("5000", "2026-06", "30/06/2026", D("40.00"), "EMEA", 24),
    ("6000", "2026-08", "31/08/2026", D("15.75"), "EMEA", 25),
    ("6000", "2026-03", "31/03/2026", D("10.25"), "AMER", 26),
    ("7000", "2026-08", "31/08/2026", D("-22.50"), "EMEA", 27),
    ("7000", "2026-08", "31/08/2026", D("-22.50"), "EMEA", 28),
]

LEDGER_EDGE = [
    (None, None, None, D("1.00"), "EMEA", None),
    ("Ångström-Ürümqi-Köln", "2026-08", "29/02/2024", D("2.50"), "EMEA", 31),
    ("A" * 20, "2026-08", "31/08/2026", D("0.01"), "EMEA", 32),
    ("DUP", "2026-08", "31/08/2026", D("3.00"), "EMEA", 33),
    ("DUP", "2026-08", "31/08/2026", D("3.00"), "EMEA", 33),
    ("NEG", "2026-08", "31/08/2026", D("-0.01"), "EMEA", 34),
    ("FILT", "2026-08", "31/08/2026", D("9.99"), "AMER_EMEA", 35),
    ("FILT", "2026-08", "30/08/2026", D("8.88"), None, 36),
]

GL_SUMMARY_BEFORE = [
    ("4000", "2026-08", D("1.00"), 1.0, 1, "N", "Y"),
    ("9999", "2026-08", D("5.00"), 5.0, 1, "N", "N"),
    ("4000", "2019-12", D("7.00"), 7.0, 1, "N", "Y"),
]

# ---------------------------------------------------------------- wf_0004

STOCK = [F("SKU", "V_String", 30), F("WAREHOUSE", "V_String", 10), F("QTY", "Int32", 4),
         F("NOTE", "V_String", 100)]

# WAREHOUSE stays inside {EAST, NORTH, WEST}: the cross-tab's header list is frozen in tool 4's
# MetaInfo, so a fourth value would have no column to land in.
STOCK_NORMAL = [
    ("ab 12", "EAST", 5, "space instead of a dash"),
    (" AB-12 ", "EAST", 7, "padded with spaces"),
    ("AB-12", "WEST", 2, "already clean"),
    ("zz9", "NORTH", 3, "no separator at all"),
    ("ZZ-9", "NORTH", 4, "already clean"),
    ("zz9", "EAST", 1, "same sku, other warehouse"),
    ("bad sku!", "WEST", 6, "regex never matches this"),
    ("cd-100", "WEST", 0, "zero qty"),
    ("cd-100", "EAST", None, "unknown qty"),
    ("cd-100", "NORTH", 9, "the only cd-100 that survives"),
    ("AB-12", "NORTH", 8, "third warehouse"),
    ("ef-7", "EAST", 12, "first of two"),
    ("ef-7", "EAST", 3, "second of two, same warehouse"),
    ("GH 42", "WEST", 10, "upper case with a space"),
]

STOCK_PERIOD_END = [
    ("ab 12", "EAST", 20, "month end count"),
    ("AB-12", "NORTH", 5, "month end count"),
    ("zz9", "WEST", 11, "month end count"),
    ("ZZ-9", "EAST", 2, "month end count"),
    ("cd-100", "NORTH", 7, "month end count"),
    ("cd 100", "WEST", 0, "nothing left at month end"),
    ("ef-7", "EAST", 1, "month end count"),
    ("bad sku!", "WEST", 4, "still needs a data fix"),
]

STOCK_EDGE = [
    (None, None, None, None),
    ("ÄB-12", "EAST", 3, "nön-ASCII SKU stays unmatched"),
    ("Q" * 30, "NORTH", 1, "M" * 100),
    ("DUP-1", "WEST", 2, "duplicate"),
    ("DUP-1", "WEST", 2, "duplicate"),
    ("zero-0", "EAST", 0, "zero qty is dropped by MinQty"),
    ("bd-5", "WEST", 1, "exactly MinQty"),
]

# ---------------------------------------------------------------- wf_0005

ACCOUNTS = [F("ACCT", "V_String", 20), F("UPDATED", "Date", 10), F("BALANCE", "Double", 8)]

ACCOUNTS_NORMAL = [
    ("A-100", "2026-08-01", 10.5),
    ("A-100", "2026-08-15", 12.75),
    ("B-200", "2026-07-31", -5.0),
    ("C-300", "2026-08-31", 0.0),
    ("C-300", "2026-06-30", 99.99),
    ("D-400", None, 1.25),
]

# ---------------------------------------------------------------- wf_0006

SUBSCRIPTIONS = [F("CUSTOMER", "V_String", 20), F("PERIOD", "V_String", 7),
                 F("BILLED", "Double", 8), F("CAP", "Double", 8), F("CANCELLED", "Bool", 1)]

# Tool 2 keeps only `[BILLED] > 0`, and a NULL comparison is not true, so a NULL or zero BILLED
# never reaches the Python tool. That is what keeps (CUSTOMER, PERIOD) unique on both work
# streams, which is why both contracts can declare it as their key.
SUBSCRIPTIONS_NORMAL = [
    ("ACME", "2026-01", 100.0, 80.0, False),
    ("ACME", "2026-02", 50.0, 60.0, False),
    # The cancellation arrives while ACME still carries a deferred balance, so the reset is
    # visible in RECOGNIZED as well as in DEFERRED: broken_sql/seg_02/01 removes exactly this.
    ("ACME", "2026-03", 10.0, 80.0, True),
    ("BOLT", "2026-01", None, 80.0, False),
    ("BOLT", "2026-02", 0.0, 80.0, False),
    ("BOLT", "2026-03", 120.0, 50.0, False),
    ("CINDER", "2026-01", 90.0, 100.0, False),
    ("CINDER", "2026-02", 200.0, 100.0, False),
    ("CINDER", "2026-03", 25.0, 100.0, False),
]

# ACME's first row bills exactly twice its cap, so the deferred balance it carries into 2026-06
# equals the cap itself -- the boundary `min(billed + deferred, cap)` is decided on.
SUBSCRIPTIONS_PERIOD_END = [
    ("ACME", "2026-03", 180.0, 90.0, False),
    ("ACME", "2026-06", 10.0, 90.0, False),
    ("BOLT", "2026-06", 50.0, 50.0, False),
    ("CINDER", "2026-09", 75.25, 100.0, False),
    ("DELTA", "2026-12", 300.0, 300.0, True),
]

SUBSCRIPTIONS_EDGE = [
    (None, None, None, None, None),
    ("Ångström Ñuñez", "2026-08", 12.50, 10.00, False),
    ("Q" * 20, "2026-08", 0.01, 0.02, False),
    ("DUP CO", "2026-08", 0.0, 50.0, False),
    ("DUP CO", "2026-08", 0.0, 50.0, False),
    ("NEG", "2026-08", -25.0, 50.0, False),
    ("SOLO", "2026-09", 40.0, 30.0, True),
]

# ---------------------------------------------------------------- wf_0007

TARGETS = [F("REGION", "V_String", 10), F("PERIOD", "V_String", 7),
           F("TARGET", "FixedDecimal", 19, 2)]
ACTUALS = [F("REGION", "V_String", 10), F("PERIOD", "V_String", 7),
           F("ACTUAL", "FixedDecimal", 19, 2)]
# Typed exactly like tool 5's (the Summarize's) output fields, which both outputs write.
ATTAINMENT_HISTORY = [F("REGION", "V_String", 10), F("PERIOD", "V_String", 7),
                      F("TARGET_TOTAL", "FixedDecimal", 19, 2),
                      F("ACTUAL_TOTAL", "FixedDecimal", 19, 2), F("LINES", "Int64", 8)]

TARGETS_NORMAL = [
    ("EAST", "2026-01", D("100.00")),
    ("EAST", "2026-02", D("120.00")),
    ("WEST", "2026-01", D("80.00")),
    ("WEST", "2025-12", D("75.00")),     # prior year: joined, then filtered out
    ("NORTH", "2026-01", D("50.00")),    # no actual: the inner join drops it
    (None, "2026-01", D("10.00")),       # NULL key matches nothing
]

ACTUALS_NORMAL = [
    ("EAST", "2026-01", D("90.00")),
    ("EAST", "2026-01", D("15.00")),     # two lines on one key: the join fans out
    ("EAST", "2026-02", D("130.00")),
    ("WEST", "2026-01", None),           # NULL actual
    ("WEST", "2025-12", D("70.00")),
    ("SOUTH", "2026-01", D("40.00")),    # no target
]

ATTAINMENT_HISTORY_BEFORE_NORMAL = [
    ("EAST", "2026-01", D("1.00"), D("1.00"), 1),     # matched: updated
    ("WEST", "2025-12", D("75.00"), D("70.00"), 1),   # prior year: kept
    ("NORTH", "2025-11", D("60.00"), D("55.00"), 2),  # kept
]

TARGETS_PERIOD_END = [
    ("EAST", "2025-12", D("100.00")),
    ("EAST", "2026-01", D("110.00")),
    ("WEST", "2025-12", D("90.00")),
    ("WEST", "2026-01", D("95.00")),
]

ACTUALS_PERIOD_END = [
    ("EAST", "2025-12", D("98.00")),
    ("EAST", "2026-01", D("112.00")),
    ("WEST", "2025-12", D("91.00")),
    ("WEST", "2026-01", D("93.00")),
]

ATTAINMENT_HISTORY_BEFORE_PERIOD_END = [
    ("WEST", "2026-01", D("1.00"), D("1.00"), 1),     # matched: updated
    ("EAST", "2025-12", D("100.00"), D("97.00"), 1),  # December is filtered out: kept as it was
]

TARGETS_EDGE = [
    ("DUP", "2026-03", D("10.00")),
    ("DUP", "2026-03", D("10.00")),      # byte-identical duplicate: the join fans it out
    ("NORDÖST", "2026-02", D("5.00")),   # non-ASCII letters in the key
    ("NEG", "2026-04", D("20.00")),
    ("BAD", "2026-1", D("7.00")),        # malformed PERIOD whose first four characters are 2026
]

ACTUALS_EDGE = [
    ("DUP", "2026-03", D("4.00")),
    ("NORDÖST", "2026-02", D("6.50")),
    ("NEG", "2026-04", D("-3.25")),      # negative actual
    ("BAD", "2026-1", D("1.00")),
]

ATTAINMENT_HISTORY_BEFORE_EDGE = [
    ("DUP", "2026-03", D("1.00"), D("1.00"), 1),      # matched: updated
    ("OLD", "2025-06", D("9.00"), D("8.00"), 1),      # kept
]

# ---------------------------------------------------------------- sample.json

SAMPLE_JSON = [
    {"id": "wf_0001",
     "title": "Sales summary by region and size band",
     "owner": "wf_owner",
     "schedule": "0 6 * * 1-5",
     "segmentation": {"min_tools": 3, "max_tools": 40},
     "expected_terminal": "VALIDATED",
     "answers": {"sales/orders.yxdb": "SALES.RAW.ORDERS",
                 "out/sales_summary.yxdb": "ANALYTICS.CURATED.SALES_SUMMARY",
                 "out/excluded_orders.csv": "ANALYTICS.CURATED.EXCLUDED_ORDERS"},
     "logical": {"1": "ORDERS", "7": "SALES_SUMMARY", "8": "EXCLUDED_ORDERS"}},
    # min_tools 2, not the 3 the other samples use: this workflow exists to demonstrate container
    # cuts and a parallel segment wave, and its two prep containers hold two data tools each, so at
    # 3 both would merge away into the main group and there would be nothing left to show.
    # The output's key isn't the bare `normalize_key` result: contract C8 gives a DB alias only
    # `alias:<alias>`, but one alias can serve many tables, so intake's actual touchpoint key for
    # a DB *output* appends `/<table lower-cased>` (`alias:dw_sales/dbo.customer_order_fact`) --
    # a shape `sample.json`'s simple answers map was never meant to carry. Answering by tool id
    # instead sidesteps that entirely; the shape sample.json allows for exactly this case.
    {"id": "wf_0002",
     "title": "Customer orders fact load",
     "owner": "wf_owner",
     "schedule": "0 6 * * 1-5",
     "segmentation": {"min_tools": 2, "max_tools": 40},
     "expected_terminal": "VALIDATED",
     "answers": {"crm/customers.yxdb": "CRM.RAW.CUSTOMERS",
                 "crm/orders_export.csv": "CRM.RAW.ORDERS_EXPORT",
                 "10": "ANALYTICS.CURATED.CUSTOMER_ORDER_FACT"},
     "logical": {"1": "CUSTOMERS", "3": "ORDERS_EXPORT", "10": "CUSTOMER_ORDER_FACT"}},
    # Both touchpoints normalize to the same bare `alias:prod_fin` (contract C8): the input and
    # output share one alias with nothing left to disambiguate them, so this sample answers by
    # tool id instead - the shape sample.json allows for exactly this case.
    {"id": "wf_0003",
     "title": "GL period close",
     "owner": "wf_owner",
     "schedule": "0 6 * * 1-5",
     "segmentation": {"min_tools": 3, "max_tools": 40},
     "expected_terminal": "VALIDATED",
     "answers": {"1": "FINANCE.RAW.GL_LEDGER", "10": "ANALYTICS.CURATED.GL_SUMMARY"},
     "logical": {"1": "GL_LEDGER", "10": "GL_SUMMARY"}},
    {"id": "wf_0004",
     "title": "Inventory by warehouse",
     "owner": "wf_owner",
     "schedule": "0 6 * * 1-5",
     "segmentation": {"min_tools": 3, "max_tools": 40},
     "expected_terminal": "VALIDATED",
     "answers": {"wh/stock.yxdb": "WH.RAW.STOCK",
                 "out/inventory_by_wh.yxdb": "ANALYTICS.CURATED.INVENTORY_BY_WH",
                 "out/inventory_long.yxdb": "ANALYTICS.CURATED.INVENTORY_LONG"},
     "logical": {"1": "STOCK", "6": "INVENTORY_BY_WH", "7": "INVENTORY_LONG"}},
    # A Run Command tool is tier T3, so this workflow is expected to end MANUAL and never
    # produces golden outputs; only `normal` and `empty` inputs are needed.
    {"id": "wf_0005",
     "title": "Vendor account dedupe",
     "owner": "wf_owner",
     "schedule": "0 6 * * 1-5",
     "segmentation": {"min_tools": 3, "max_tools": 40},
     "expected_terminal": "MANUAL",
     "answers": {"vendor/accounts.yxdb": "VENDOR.RAW.ACCOUNTS",
                 "out/accounts_clean.yxdb": "ANALYTICS.CURATED.ACCOUNTS_CLEAN"},
     "logical": {"1": "ACCOUNTS", "4": "ACCOUNTS_CLEAN"}},
    # min_tools 1: the Python tool is a hard cut (it is a whole program, not a clause), so it gets
    # a segment of its own whatever the floor says. At 3 the two tools on either side of it would
    # be pushed together with it, and the point of this sample -- one Snowpark segment between two
    # SQL ones -- would be lost.
    {"id": "wf_0006",
     "title": "Subscription revenue recognition",
     "owner": "wf_owner",
     "schedule": "0 6 * * 1-5",
     "segmentation": {"min_tools": 1, "max_tools": 40},
     "expected_terminal": "VALIDATED",
     "answers": {"billing/subscriptions.yxdb": "BILLING.RAW.SUBSCRIPTIONS",
                 "out/revenue_by_period.yxdb": "ANALYTICS.CURATED.REVENUE_BY_PERIOD"},
     "logical": {"1": "SUBSCRIPTIONS", "5": "REVENUE_BY_PERIOD"}},
    # The dbt sample: `output_target` asks for the whole workflow to be migrated as one dbt
    # project (build_samples.seed copies it into the manifest, where target_check.py --prefer auto
    # reads it). min_tools 2 keeps the two containers apart (tools 1-3 and 4-7), so the year
    # filter lands in the target models. Tool 7's DB output answers by tool id, for the same
    # reason as wf_0002's.
    {"id": "wf_0007",
     "title": "Regional targets and attainment",
     "owner": "wf_owner",
     "schedule": "0 6 * * 1-5",
     "segmentation": {"min_tools": 2, "max_tools": 40},
     "expected_terminal": "VALIDATED",
     "output_target": "dbt",
     "answers": {"plan/targets.yxdb": "PLANNING.RAW.TARGETS",
                 "sales/actuals.yxdb": "SALES.RAW.ACTUALS",
                 "out/region_attainment.yxdb": "ANALYTICS.CURATED.REGION_ATTAINMENT",
                 "7": "ANALYTICS.CURATED.ATTAINMENT_HISTORY"},
     "logical": {"1": "TARGETS", "2": "ACTUALS", "6": "REGION_ATTAINMENT",
                 "7": "ATTAINMENT_HISTORY"}},
]


def main() -> None:
    write("wf_0001", "normal", "1", ORDERS, ORDERS_NORMAL)
    write("wf_0001", "period_end", "1", ORDERS, ORDERS_PERIOD_END)
    write("wf_0001", "empty", "1", ORDERS, [])
    write("wf_0001", "edge", "1", ORDERS, ORDERS_EDGE)

    write("wf_0002", "normal", "1", CUSTOMERS, CUSTOMERS_NORMAL)
    write("wf_0002", "normal", "3", ORDERS_EXPORT, ORDERS_EXPORT_NORMAL)
    write("wf_0002", "period_end", "1", CUSTOMERS, CUSTOMERS_PERIOD_END)
    write("wf_0002", "period_end", "3", ORDERS_EXPORT, ORDERS_EXPORT_PERIOD_END)
    write("wf_0002", "empty", "1", CUSTOMERS, [])
    write("wf_0002", "empty", "3", ORDERS_EXPORT, [])
    write("wf_0002", "edge", "1", CUSTOMERS, CUSTOMERS_EDGE)
    write("wf_0002", "edge", "3", ORDERS_EXPORT, ORDERS_EXPORT_EDGE)
    for golden_set in ("normal", "period_end", "edge"):
        write_target("wf_0002", golden_set, "CUSTOMER_ORDER_FACT", FACT, FACT_BEFORE)
    # An empty run must leave empty outputs, so the target starts empty too.
    write_target("wf_0002", "empty", "CUSTOMER_ORDER_FACT", FACT, [])

    write("wf_0003", "normal", "1", LEDGER, LEDGER_NORMAL)
    write("wf_0003", "period_end", "1", LEDGER, LEDGER_PERIOD_END)
    write("wf_0003", "empty", "1", LEDGER, [])
    write("wf_0003", "edge", "1", LEDGER, LEDGER_EDGE)
    for golden_set in ("normal", "period_end", "edge"):
        write_target("wf_0003", golden_set, "GL_SUMMARY", GL_SUMMARY, GL_SUMMARY_BEFORE)
    write_target("wf_0003", "empty", "GL_SUMMARY", GL_SUMMARY, [])

    write("wf_0004", "normal", "1", STOCK, STOCK_NORMAL)
    write("wf_0004", "period_end", "1", STOCK, STOCK_PERIOD_END)
    write("wf_0004", "empty", "1", STOCK, [])
    write("wf_0004", "edge", "1", STOCK, STOCK_EDGE)

    write("wf_0005", "normal", "1", ACCOUNTS, ACCOUNTS_NORMAL)
    write("wf_0005", "empty", "1", ACCOUNTS, [])

    write("wf_0006", "normal", "1", SUBSCRIPTIONS, SUBSCRIPTIONS_NORMAL)
    write("wf_0006", "period_end", "1", SUBSCRIPTIONS, SUBSCRIPTIONS_PERIOD_END)
    write("wf_0006", "empty", "1", SUBSCRIPTIONS, [])
    write("wf_0006", "edge", "1", SUBSCRIPTIONS, SUBSCRIPTIONS_EDGE)

    write("wf_0007", "normal", "1", TARGETS, TARGETS_NORMAL)
    write("wf_0007", "normal", "2", ACTUALS, ACTUALS_NORMAL)
    write("wf_0007", "period_end", "1", TARGETS, TARGETS_PERIOD_END)
    write("wf_0007", "period_end", "2", ACTUALS, ACTUALS_PERIOD_END)
    write("wf_0007", "empty", "1", TARGETS, [])
    write("wf_0007", "empty", "2", ACTUALS, [])
    write("wf_0007", "edge", "1", TARGETS, TARGETS_EDGE)
    write("wf_0007", "edge", "2", ACTUALS, ACTUALS_EDGE)
    write_target("wf_0007", "normal", "ATTAINMENT_HISTORY", ATTAINMENT_HISTORY,
                 ATTAINMENT_HISTORY_BEFORE_NORMAL)
    write_target("wf_0007", "period_end", "ATTAINMENT_HISTORY", ATTAINMENT_HISTORY,
                 ATTAINMENT_HISTORY_BEFORE_PERIOD_END)
    write_target("wf_0007", "edge", "ATTAINMENT_HISTORY", ATTAINMENT_HISTORY,
                 ATTAINMENT_HISTORY_BEFORE_EDGE)
    # An empty run must leave empty outputs, so the target starts empty too.
    write_target("wf_0007", "empty", "ATTAINMENT_HISTORY", ATTAINMENT_HISTORY, [])

    for meta in SAMPLE_JSON:
        io.write_json(SAMPLES / meta["id"] / "sample.json", meta)

    print(f"wrote golden inputs and sample.json for {len(SAMPLE_JSON)} workflows")


if __name__ == "__main__":
    main()
