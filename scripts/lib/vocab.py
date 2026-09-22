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

# Tool types whose output depends on incoming row order (plan §7.2, §8.5).
ORDER_DEPENDENT_TYPES = frozenset({"sample", "record_id", "unique", "multi_row_formula"})

# Tool types that are always tier T3 (manual; plan §8.2).
T3_TYPES = frozenset({"run_command"})
