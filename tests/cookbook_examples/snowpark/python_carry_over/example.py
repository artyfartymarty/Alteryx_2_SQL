import pandas as pd
from snowflake.snowpark.types import DoubleType, StringType, StructField, StructType


# tool 2: Python tool -- revenue schedule with carry-over (samples/wf_0006's subscription_revenue
# workflow). Sequential per customer (the deferred balance feeds the next period's cap check and
# resets on cancellation), so the rows are processed in pandas exactly as the Alteryx script does
# -- this is the same body as samples/wf_0006/canned/segments/seg_02/proc.py, returning the
# DataFrame instead of writing it, since a real proc.py's `run()` does the write itself.
def transform(session):
    pdf = session.table("MIG_COOKBOOK.IN_1").to_pandas()
    pdf = pdf.sort_values(["CUSTOMER", "PERIOD"]).reset_index(drop=True)
    out = []
    for customer, group in pdf.groupby("CUSTOMER", sort=False):
        deferred = 0.0
        for _, row in group.iterrows():
            # The Alteryx script fails outright on a NULL CANCELLED (bool(pd.NA) raises); the
            # framework's to_pandas() would hand us None instead and bool(None) is quietly False,
            # so the check is written out to keep the failure loud on both engines.
            if pd.isna(row["CANCELLED"]):
                raise ValueError(f"CANCELLED is NULL for {customer} {row['PERIOD']}")
            if bool(row["CANCELLED"]):
                deferred = 0.0
            billed = 0.0 if pd.isna(row["BILLED"]) else float(row["BILLED"])
            recognized = min(billed + deferred, float(row["CAP"]))
            deferred = billed + deferred - recognized
            out.append([customer, row["PERIOD"], round(recognized, 2), round(deferred, 2)])
    schema = StructType([StructField("CUSTOMER", StringType(254)), StructField("PERIOD", StringType(254)),
                         StructField("RECOGNIZED", DoubleType()), StructField("DEFERRED", DoubleType())])
    return session.create_dataframe(out, schema=schema)
