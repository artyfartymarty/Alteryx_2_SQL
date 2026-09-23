from snowflake.snowpark.functions import col


# tool 2: Sort -- AMOUNT descending then ACCT ascending. Alteryx sorts NULL first ascending and
# last descending, which the explicit asc_nulls_first()/desc_nulls_last() calls spell out, the
# same way the SQL pattern's explicit NULLS clauses do. A DataFrame's own row order does not
# survive being read back as a table (there is no ORDER BY on a table read), so this order only
# ever matters to whatever reads this DataFrame next, in the same run.
def transform(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    return df.sort(col("AMOUNT").desc_nulls_last(), col("ACCT").asc_nulls_first())
