# Task 4 — fix round 1 (rulings from the task review of f342cac)

Read `task-4-review-findings.md` (the reviewer's verdict, condensed) beside this file first.
The findings are correct; the controller has ruled as follows. Apply every ruling.

## R0 — bring Task 3's fix into this worktree FIRST
Task 3's fix round changed the string policy of `types_map.alteryx_to_snowpark` and other T3 files
(commit 0d674fb on branch `wt/ot-task-3`). Before touching anything, run, in this worktree:
`git merge --no-edit wt/ot-task-3` (it must be a clean merge: T3's fix touched snowpark_rules.py,
types_map.py, render_snowpark.py and their tests, plus tests/test_backend.py; T4 touched only
validation.py, validate_segment.py, validate_snowpark.py, test_validate_snowpark.py). Report the
merge commit hash: it is the base of the re-review diff. Then run the whole suite once so you know
the merged baseline (expect ~1054 + your 20 = green, 0 skipped).

## R1 — the actual table's schema comes from Snowpark, never from the contract (C1, C2, I3)
`_read_back` must build the actual table's fields from the REAL schema:
`session.table(fqn).schema` (a `StructType`), one field per `StructField`, in the table's own column
order, including columns the contract never declared and omitting columns the handler failed to
write. That is what lets `compare.py`'s own schema check (families, missing, extra) run for real.
Add to `scripts/lib/types_map.py`, beside `alteryx_to_snowpark`, its exact inverse:

    def snowpark_to_alteryx(data_type) -> dict   # {"type", "size", "scale"} (no "name")

with lazy imports like its sibling, and this policy (mirror the merged `alteryx_to_snowpark`):
- LongType / IntegerType / ShortType / ByteType -> {"type": "Int64", "size": None, "scale": None}
- DoubleType / FloatType -> "Double"
- DecimalType(p, s) -> {"type": "FixedDecimal", "size": p, "scale": s} (also when s == 0)
- BooleanType -> "Bool"; DateType -> "Date"; TimestampType (any tz variant) -> "DateTime"; TimeType -> "Time"
- StringType: a concrete length that is not the VARCHAR maximum -> {"type": "String", "size": n};
  no length, `is_max_size`, or length >= 16777216 -> {"type": "V_String", "size": None}
  (the Local Testing Framework and real Snowflake both report an unsized column as the max length)
- anything else (BinaryType, VariantType, ArrayType, MapType, StructType, GeographyType, ...) ->
  raise ValueError naming the type.
Round-trip test (tests/test_types_map.py or your test file): for every Alteryx type
`alteryx_to_snowpark` accepts, `snowpark_to_alteryx(alteryx_to_snowpark(field))` returns the same
type/size/scale, EXCEPT the documented lossy cases (Int16/Int32/Byte -> Int64; V_WString -> V_String;
WString(n) -> String(n)) which the test states explicitly.

`_coerce` then coerces each cell per the REAL column's Alteryx type (from the schema), not the
contract's. A cell that still does not convert (ValueError, TypeError, decimal.InvalidOperation,
OverflowError) is a DOMAIN failure, never a usage error: raise a small `ReadBackError(table, column,
row_index, reason)` and have `_run_one_set` turn it into that set's FAIL report through
`validation.fail_report` (reason text names the table, column and row), report written, exit 1.
The same path handles an unsupported Snowpark column type from `snowpark_to_alteryx`.
Keep the NULL-upcast handling (`int(12.0)` for a LongType column whose pandas dtype became float64).

RED-first tests to add to tests/test_validate_snowpark.py (each must FAIL before the code change):
1. handler casts a declared NUMBER(38,0) column to DoubleType -> verdict FAIL; the table's schema check
   reports the TYPE mismatch (assert on the compare report's problem cluster, not just the verdict).
2. handler writes an extra undeclared column -> FAIL, schema problem `extra` names it.
3. handler omits a declared column -> FAIL, schema problem `missing` names it.
4. handler writes the string "not-a-number" into the ID column (StringType) -> FAIL (schema), and through
   the CLI: exit 1 with validation.json written (this was exit 2 with no report).
5. contract output column `AMT NUMBER(19,2)` written as DecimalType(19,2) -> PASS (this is the
   FixedDecimal ruling, finding 4, which crashed with "NUMBER(None,0)").
6. positive control: the existing correct-procedure test still PASSes, including the idempotency run.

## R2 — no `"size": None` for a FixedDecimal anywhere (finding 4)
After R1, `_alteryx_for` is either unused (delete it and `_NUMBER_SCALE_RE`) or, if anything still needs
the contract side as Alteryx fields, it must return the full field: `NUMBER(p,s)` with s > 0 ->
FixedDecimal size p scale s; bare `NUMBER` / scale 0 -> Int64; `VARCHAR(n)` -> String(n); `VARCHAR` ->
V_String. Grep the file for `"size": None` before you finish.

## R3 — accepted as-is (finding 5)
`run_handler(repo, wf_id, seg, golden_set, proc_path, run_id, contract)` (7 args) stands; the plan's
Interfaces line is annotated by the controller in the final docs pass. Nothing to do.

## R4 — remove the dead `GOLDEN_SCHEMA` import (finding 6)

## Report
Append a "## Fix round 1" section to task-4-report.md: merge commit, fix commit(s), the test counts
(whole suite; 0 skipped), and any concern. Commit as `wip: fix round 1 (C1, C2, I3, I4, M6) — <what>`.
Do not touch compare.py, validate_segment.py, or scripts/lib/validation.py unless a ruling above needs it
(it should not). No subagents. Never git stash / checkout -- / reset --hard.
