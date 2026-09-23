# BROKEN ON PURPOSE -- do not fix: this is a fixture for
# tests/test_cookbook_snowpark.py::test_the_carry_over_example_catches_a_dropped_reset (fix round
# 1, finding C2). The mistake: the cancellation reset is gone -- the Alteryx script zeroes the
# carried deferred balance before a cancelled period's own cap is applied; this variant just
# carries the balance on, which is what anyone reading the loop for its arithmetic rather than
# its bookkeeping would leave out. It is example.py's own transform() with exactly those two
# lines deleted (the NULL CANCELLED guard above them is deliberately left in place, so the diff
# against example.py is the reset and nothing else). Mirrors
# samples/wf_0006/broken_sql/seg_02/01_cancellation_reset_ignored.py's own mutation of the same
# pattern, one segment over.
import pandas as pd
from snowflake.snowpark.types import DoubleType, StringType, StructField, StructType


def transform(session):
    pdf = session.table("MIG_COOKBOOK.IN_1").to_pandas()
    pdf = pdf.sort_values(["CUSTOMER", "PERIOD"]).reset_index(drop=True)
    out = []
    for customer, group in pdf.groupby("CUSTOMER", sort=False):
        deferred = 0.0
        for _, row in group.iterrows():
            if pd.isna(row["CANCELLED"]):
                raise ValueError(f"CANCELLED is NULL for {customer} {row['PERIOD']}")
            billed = 0.0 if pd.isna(row["BILLED"]) else float(row["BILLED"])
            recognized = min(billed + deferred, float(row["CAP"]))
            deferred = billed + deferred - recognized
            out.append([customer, row["PERIOD"], round(recognized, 2), round(deferred, 2)])
    schema = StructType([StructField("CUSTOMER", StringType(254)), StructField("PERIOD", StringType(254)),
                         StructField("RECOGNIZED", DoubleType()), StructField("DEFERRED", DoubleType())])
    return session.create_dataframe(out, schema=schema)
