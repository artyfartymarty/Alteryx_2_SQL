# BROKEN ON PURPOSE -- do not fix: this is a fixture for tests/test_e2e_parity.py.
# The mistake: the cancellation reset is gone. The Alteryx script zeroes the carried deferred
# balance before a cancelled period's own cap is applied; this variant just carries the balance
# on, which is what anyone reading the loop for its arithmetic rather than its bookkeeping would
# leave out. It is the canned proc.py with exactly those two lines deleted -- the NULL CANCELLED
# guard above them is deliberately left in place, so the diff against the canned module is the
# reset and nothing else. Everything else -- the sort, the grouping, the cap, the rounding, the
# schema -- is unchanged, so the procedure still runs and still writes the same columns.
# tool 3: Python tool -- revenue schedule with carry-over. Sequential per customer (the deferred
# balance feeds the next period's cap check and resets on cancellation), so the rows are
# processed in pandas exactly as the Alteryx script does; the frame is small by construction.
import pandas as pd
from snowflake.snowpark.types import DoubleType, StringType, StructField, StructType


def run(session, src_db, src_schema, tgt_db, tgt_schema, run_id):
    pdf = session.table("MIG_WORK.WF0006_SEG_01_OUT").to_pandas()
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
            billed = 0.0 if pd.isna(row["BILLED"]) else float(row["BILLED"])
            recognized = min(billed + deferred, float(row["CAP"]))
            deferred = billed + deferred - recognized
            out.append([customer, row["PERIOD"], round(recognized, 2), round(deferred, 2)])
    schema = StructType([StructField("CUSTOMER", StringType(254)), StructField("PERIOD", StringType(254)),
                         StructField("RECOGNIZED", DoubleType()), StructField("DEFERRED", DoubleType())])
    session.create_dataframe(out, schema=schema).write.mode("overwrite").save_as_table("MIG_WORK.WF0006_SEG_02_OUT")
    return "OK"
