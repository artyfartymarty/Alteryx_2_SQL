"""Closed vocabularies shared across the pipeline (plan Global Constraints). No other values are valid."""

STATUSES: frozenset[str] = frozenset({
    "PENDING",
    "DONE",
    "PARSED",
    "RECOVERED",
    "QUARANTINED",
    "READY",
    "WAITING_FOR_ANSWERS",
    "BLOCKED",
    "NEEDS_HUMAN",
    "MANUAL",
    "VALIDATED",
    "OPEN",
})

VERDICTS: frozenset[str] = frozenset({
    "PASS",
    "PASS_WITH_ACCEPTED_DIFF",
    "FAIL",
    "BLOCK",
})

DIFF_CLASSES: tuple[str, ...] = (
    "ROUNDING",
    "ORDERING",
    "NULL_SEMANTICS",
    "TRUNCATION",
    "TYPE",
    "LOGIC",
    "GOLDEN_DATA",
    "UNKNOWN",
)

# Golden data sets captured per workflow (plan §7.5).
GOLDEN_SETS = ("normal", "period_end", "empty", "edge")

# Node types that carry no data-transformation semantics of their own.
NON_DATA_TYPES = frozenset({"container", "comment", "interface", "action"})

# The same, plus `browse`: a Browse tool holds a copy of its input for a human to look at and
# transforms nothing, so a stage that asks "which nodes must this procedure account for?" skips it
# too. `scripts/target_check.py` and `scripts/compile_check.py` both need this superset; derived
# from NON_DATA_TYPES rather than retyped, so a type added above reaches both of them.
DATA_LESS_TYPES = NON_DATA_TYPES | {"browse"}

# A final target's write mode, in every spelling the pipeline writes (Task L4), to the one form it
# names: a contract's `outputs[].write_mode` carries the parser's vocabulary
# (`docs/reference/dag-contract.md` §4: overwrite, append, update_insert, truncate_append) and
# `intake/mappings.yaml`'s `mode` intake's (`intake_prompt._VALID_MODES`: overwrite, append, merge,
# where `merge` is `update_insert`). `compile_check.py`'s `c4:write_mode` and `lib.snowpark_rules`'
# `rule:write_mode` hold a procedure to the form; anything outside this table is not a write mode.
TARGET_WRITE_MODES: dict[str, str] = {
    "overwrite": "overwrite",
    "append": "append",
    "truncate_append": "truncate_append",
    "update_insert": "update_insert",
    "merge": "update_insert",
}

# Tool types whose output depends on incoming row order (plan §7.2, §8.5).
ORDER_DEPENDENT_TYPES = frozenset({"sample", "record_id", "unique", "multi_row_formula"})

# Tool types that are always tier T3 (manual; plan §8.2).
T3_TYPES = frozenset({"run_command"})
