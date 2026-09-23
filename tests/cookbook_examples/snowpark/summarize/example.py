from snowflake.snowpark.functions import cast, coalesce, col, count, listagg, lit
from snowflake.snowpark.functions import sum as sum_
from snowflake.snowpark.types import DecimalType, DoubleType


# tool 2: Summarize -- group by REGION. Alteryx's Count counts rows and CountNonNull counts
# values, so ORDER_COUNT is count(lit(1)) and PRICED_COUNT is count(col("AMOUNT")) (count() skips
# NULL by itself); Concat skips NULLs and joins in incoming order, which SEQ (an upstream Record
# ID/Sort's ordinal) makes explicit -- the DataFrame API has no notion of "the order rows arrived
# in" on its own, so listagg needs an explicit within_group. A group with nothing to join is '' in
# Alteryx; listagg gives NULL there instead, so it is wrapped in coalesce(..., lit("")). SUM goes
# through NUMBER(38,10), never FLOAT, for the same reason formula.md's pattern does -- cast to
# that scale BEFORE the group_by (the local double refuses a cast nested inside an aggregate
# call), then cast the aggregated total back to Double, keep_column_order so it stays where the
# oracle puts it (right after the group-by column).
def transform(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    df = df.with_column("AMOUNT_EXACT", cast(col("AMOUNT"), DecimalType(38, 10)))
    out = df.group_by("REGION").agg(
        sum_(col("AMOUNT_EXACT")).alias("TOTAL_AMOUNT"),
        count(lit(1)).alias("ORDER_COUNT"),
        count(col("AMOUNT")).alias("PRICED_COUNT"),
        coalesce(listagg(col("NOTE"), ",").within_group(col("SEQ").asc()), lit("")).alias("NOTES"),
    )
    return out.with_column("TOTAL_AMOUNT", cast(col("TOTAL_AMOUNT"), DoubleType()), keep_column_order=True)
