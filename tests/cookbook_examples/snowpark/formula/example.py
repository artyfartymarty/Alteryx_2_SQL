from snowflake.snowpark.functions import col, iff, lit, try_cast
from snowflake.snowpark.types import DecimalType, DoubleType


def transform(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    # tool 2: Formula, AMOUNT -- ToNumber([AMOUNT_TXT]): a NULL or unparseable AMOUNT_TXT becomes
    # a NULL AMOUNT, never an error, matching Alteryx's warn-and-null ToNumber (try_cast, not cast).
    df = df.with_column("AMOUNT", try_cast(col("AMOUNT_TXT"), DoubleType()))
    # tool 2: Formula, NET -- Round([AMOUNT] * IIF([QTY] >= 10, 0.9, 1), 0.01): a later expression
    # sees the value the earlier one just wrote; with_column gives that for free. try_cast to a
    # two-decimal DecimalType is the rounding step (there is no `round()` DataFrame function that
    # reaches a value already written this way -- see the page's "What the local double does not
    # prove").
    discounted = col("AMOUNT") * iff(col("QTY") >= 10, lit(0.9), lit(1.0))
    df = df.with_column("NET", try_cast(try_cast(discounted, DecimalType(38, 2)), DoubleType()))
    # tool 2: Formula, SIZE_BAND -- IF [AMOUNT]>=1000 THEN "LARGE" ELSEIF [AMOUNT]>=100 THEN
    # "MEDIUM" ELSE "SMALL" ENDIF: nested iff(), the same shape as the expression itself; a NULL
    # AMOUNT makes every condition NULL (not true), so the row lands in the innermost ELSE,
    # exactly like Alteryx's IF/ELSEIF/ELSE.
    df = df.with_column(
        "SIZE_BAND",
        iff(col("AMOUNT") >= 1000, lit("LARGE"), iff(col("AMOUNT") >= 100, lit("MEDIUM"), lit("SMALL"))),
    )
    return df
