from snowflake.snowpark.functions import col


# tool 2: Filter [REGION] != "WEST" -- True branch (anchor T): keeps only rows where the
# expression is really true; a NULL REGION makes the comparison NULL, which is not true. The
# nullity is tested on REGION itself (col("REGION").is_not_null()), not on the comparison's own
# is_null() -- see the page's "What the local double does not prove": the Local Testing
# Framework's own `!=`/`==` do not implement three-valued NULL logic (a NULL operand compares
# as an ordinary True/False rather than NULL), so a derived condition's is_null() never fires.
def true_branch(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    return df.filter((col("REGION") != "WEST") & col("REGION").is_not_null())


# tool 2: Filter [REGION] != "WEST" -- False branch (anchor F): takes the rows the expression
# makes false AND the rows it makes NULL -- the IS NULL half is the whole point, so it is not
# enough to negate the True branch's own condition (on real Snowflake, ~cond alone is still NULL
# on a NULL row, not True).
def false_branch(session):
    df = session.table("MIG_COOKBOOK.IN_1")
    cond = col("REGION") != "WEST"
    return df.filter((~cond & col("REGION").is_not_null()) | col("REGION").is_null())
