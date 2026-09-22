"""Deterministic parity comparison of a golden table against a translated one (program spec §9).

    python scripts/compare.py --expected <csv|table> --actual <table> --contract contract.json
                              --out validation.json [--stream 31_U] [--tolerances mappings/global.yaml]
                              [--manifest manifest.json] [--dag segments/seg_NN/dag.json]
                              [--db workflows/<wf>/.sandbox.duckdb] [--golden-set normal]
                              [--sample-rows 5] [--root .]

Every number about data in this program comes from here: no agent ever computes a diff or a row
count. So the checks run in a fixed order, stopping early only on a schema failure, and each one
that runs writes its numbers into `validation.json`:

1. **Schema** — column names upper-cased (the `sanitize` policy) and in order, type *family* per
   column, extra and missing columns listed. A failure is a `TYPE` cluster and skips the rest.
2. **Counts** — rows, plus each column's NULL count and distinct count.
3. **Nullability** — NULLs in a column the contract declares `NOT NULL`, judged on the data
   because locally every column a CTAS produces is nullable whatever the contract says.
4. **Aggregates** — sum/min/max/avg per numeric column, min/max length per string column, over
   the *comparable set*: the keys that occur exactly once on both sides. A row that moved or was
   duplicated is one difference, reported once by `set_diff` and its own cluster, and it does not
   get to disturb the arithmetic of every column as well.
5. **Keyed diff** — key uniqueness on both sides, then the rows only on one side (anti-joins) and
   the rows whose values disagree (a join on the keys where any non-key column disagrees).
6. **Row multiset** — when the contract declares no keys: both sides grouped by every column with
   counts, which gives the two `set_diff` totals exactly. The surplus rows behind those totals are
   then pulled (bounded like the keyed diff) and paired by *nearest match*: each surplus expected
   row takes the unused surplus actual row it disagrees with in the fewest columns, a pair being
   accepted only when at least half of the columns are equal. A pair that
   agrees everywhere is two copies of the same row within tolerance and leaves the report — saying
   so in `normalizations_applied`, because nothing here is silent; a pair that disagrees goes
   through the same classification as a keyed mismatch and its cluster says `paired_by:
   nearest_match`; a row no pair claimed is a row-presence cluster, exactly as on the keyed path.
7. **Tolerances** — applied in Python to the pulled rows, so a float within tolerance is no diff.

Checks 2-6 are SQL through `backend.query`, in Snowflake dialect, built from the contract's column
list rather than `SELECT *`; only the bounded mismatch sample comes into Python. The same code
therefore runs against a real account through `SnowflakeBackend` — though nothing in this repo has
ever run on Snowflake or Alteryx, so locally "parity" means DuckDB agrees with DuckDB.

Mismatches are grouped into diff clusters and each cluster is classified (`ROUNDING`, `ORDERING`,
`NULL_SEMANTICS`, `TRUNCATION`, `TYPE`, `LOGIC`, `GOLDEN_DATA`, `UNKNOWN`).

**The verdict rule, which the rest of the pipeline trusts:**

* `PASS` requires every check to have passed and nothing to have been truncated;
* `PASS_WITH_ACCEPTED_DIFF` requires nothing to have been truncated, and every failing check to be
  accounted for by a cluster that a human approved — in an accepted class, with a matching
  approval whose columns cover the cluster's. Only a cluster about *columns* can be approved at
  all: an approval names columns and never names which rows, so rows on one side only, a
  duplicated key, a schema failure and a synthetic cluster always end in `FAIL` (`_approved`).
  `GOLDEN_DATA` and `UNKNOWN` are never approvable either, at ANY scope, whatever
  `accepted_diff_classes` and however many matching approvals say otherwise: `GOLDEN_DATA` is a
  fault in the reference data itself, which no signature over a translation difference can fix,
  and `UNKNOWN` by definition explains nothing a human could be signing off on;
* anything else is `FAIL`.

Exit code 0 for either PASS, 1 for FAIL, 2 for an error.

So **a report may never pass on a difference it has itself written down.** A failing check that no
cluster accounts for gets a cluster of its own, so the report still says what it saw, and
`_account_for_every_check` plus the guard in `compare()` then force `FAIL` — as a truncated diff
always does, because a diff cut short at `max_diff_rows` has not looked at every difference.

**Accounting is specific, never global.** Each cluster carries a reported `scope`, and it may
speak only for what it explains: a `"rows"` cluster (rows on one side only, a duplicated key)
accounts for the row-count and `set_diff` checks, and only on the side its rows are on; a
`"columns"` cluster accounts for the columns it names. An aggregate that disagrees is about
values — the comparable set already kept moved rows out of it — so only a cluster naming that
column can answer for it, and a reader can check that from the report alone.

That last sentence has one documented exception, and it is the *keyless* streams' alone: with no
keys there is no comparable set, so the aggregates run over every row, and a row that is missing
or extra necessarily shifts every aggregate of every column it carries a value in. So there, and
only there, an unpaired row-presence cluster also answers for the `aggregates` check
(`_KEYLESS_ROW_ACCOUNTABLE`). That would be a hole if such a cluster could be signed off, because
an approval names columns and never names *rows*; it is not, because **`_approved` refuses every
cluster that is not about columns**. A report whose only finding is that rows are missing, extra
or duplicated therefore ends in `FAIL` on either path, whatever `accepted_diff_classes` says, and
what rule 6 silences is an aggregate sitting behind a cluster that was already forcing a `FAIL`.

Three things follow. `SUM` and `AVG` are allowed the tolerance the rows may legitimately have
accumulated (`_aggregate_equal` derives it), because N values each within `float_abs` may add up
to N times that much and scaling by the sum itself collapses as soon as large values cancel. A
column with an absolute tolerance is pre-filtered in SQL rather than pulled through
`IS DISTINCT FROM`, so rows the tolerance will forgive cannot crowd the rows that matter out of
`max_diff_rows`. And that pre-filter only ever chooses *candidates*, deliberately a strict
superset of what could be a difference (see `_PRE_FILTER_MARGIN`); the exact test in Python is the
only thing that calls a row a difference, which is why `checks.column_mismatches` and every
cluster `count` are counted after it, and are exact unless `truncated` says the pull was cut
short — in which case they are lower bounds.

Determinism is a requirement, not an accident: cluster order, the column order inside a cluster and
`example_rows` are all sorted, the keyless pairing walks rows a SQL `ORDER BY` put in order, and
`runtime_ms` is the only field that may differ between two runs on the same data.

Numbers still come only from the data. The pairing is a heuristic about *which* two rows to put
side by side, never about how many there are: `set_diff` stays the SQL multiset difference (less
only the pairs proved equal within tolerance), a value cluster counts the accepted pairs that
disagree in its columns, and a row-presence cluster counts the surplus rows no pair claimed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NamedTuple, Sequence

from lib import typed_csv
from lib.backend import DuckDBBackend
from lib.io import read_json, read_yaml, write_json
from lib.paths import Repo, add_root_arg
from lib.types_map import type_family

#: Where a `--expected foo.csv` file is loaded before the comparison runs.
EXPECTED_TABLE = "MIG_COMPARE.EXPECTED"

#: Cluster sort order: the classification order of the spec, with the two classes that name no
#: rule at the ends — `GOLDEN_DATA` first, because nothing below it is worth reading until the
#: golden data is fixed, and `UNKNOWN` last, because it explains nothing.
CLASS_ORDER = ("GOLDEN_DATA", "ORDERING", "NULL_SEMANTICS", "ROUNDING", "TRUNCATION", "TYPE",
               "LOGIC", "UNKNOWN")

#: Used when the caller names no `mappings/global.yaml`; kept equal to that file's `tolerances`.
DEFAULT_TOLERANCES = {"float_abs": 1e-6, "float_rel": 1e-9, "timestamp_precision": "milliseconds",
                      "rounding": {"abs": 0.01}}

_NUMERIC_FAMILIES = frozenset({"number", "float"})
#: Normalizations a contract may declare, in the order they are applied.
_NORMALIZATION_OPS = ("trim", "upper")
#: Tool types that add or drop whole rows, i.e. the suspects behind a row-presence diff.
_ROW_NODE_TYPES = frozenset({"filter", "join", "unique", "sample"})
_PRECISION_DIGITS = {"seconds": 0, "milliseconds": 3, "microseconds": 6}
#: How far below the tolerance the SQL pre-filter's threshold sits, relative. SQL subtracts in
#: binary floats and Python judges in exact decimals, so at the boundary the two can disagree:
#: a difference a hair over the tolerance can subtract to exactly the tolerance and be dropped
#: before the exact test ever sees it. Rounding error in that subtraction is at most half an ulp
#: of the result, about 1e-16 relative, so a 1e-9 margin is seven orders of magnitude clear of it
#: while pulling only the rows within 1e-9 of the boundary — which Python then judges as usual.
_PRE_FILTER_MARGIN = 1e-9
#: How many surplus rows per side the keyless nearest-match pairing will consider. Pairing is
#: quadratic — every surplus expected row is measured against every unused surplus actual row —
#: and measured locally on five columns, the worst shape (nothing close enough to pair with
#: anything) costs about 1.3s at 1,000 rows a side, 5s at 2,000 and 19s at 4,000, so a default
#: `max_diff_rows` of 10,000 would sit there for minutes.
#:
#: Rows past the bound are simply left unpaired. That cannot turn a `FAIL` into a pass, because an
#: unpaired row becomes a row-presence cluster and `_approved` refuses every cluster about rows:
#: the most the bound can do is trade a named value difference for an unapprovable "these rows
#: are missing", which is a worse *diagnosis* for the fixer but never a weaker verdict. (Before
#: fix round 1 that was not true: a row cluster naming no column was vacuously approvable, and
#: starving the budget really could turn a failing report into a passing one.)
_MAX_PAIRED_ROWS = 1000

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")
_NUMBER_TEXT = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\Z")
_WHITESPACE = re.compile(r"\s+")
_EXPECTED_SIDE, _ACTUAL_SIDE, _ONE_SIDE, _OTHER_SIDE = "EXPECTED_SIDE", "ACTUAL_SIDE", "T", "OTHER_SIDE"


class _Column(NamedTuple):
    """One column of the output being compared, as the contract declares it."""

    name: str                   # upper-cased; every column name in the report is this one
    sql: str                    # how to write that name in SQL
    family: str                 # type_family of the contract's declared type
    ops: tuple[str, ...]        # declared normalizations, in application order


@dataclass
class _Diff:
    """What the checks found: the clusters plus the numbers the report quotes for them."""

    clusters: list[dict] = field(default_factory=list)
    set_diff: dict = field(default_factory=dict)
    column_mismatches: dict = field(default_factory=dict)
    needs_human: bool = False
    truncated: bool = False
    #: True when the contract declares no keys, so the rows were paired by nearest match rather
    #: than joined. `_unexplained` reads it: with no keys there is no comparable set, so the
    #: aggregates cover every row and a row that moved really does move them.
    keyless: bool = False
    #: {check name: (columns it implicates, the count it would report)}, so a failing check that
    #: no cluster accounts for can still say what it saw.
    evidence: dict[str, tuple[list[str], int]] = field(default_factory=dict)


# --- public API ------------------------------------------------------------------------------


def select_output(contract: dict, stream: str | None = None) -> dict:
    """The `outputs[]` entry for `stream`, or `contract["output"]` when no stream is named."""
    if stream is None:
        output = contract.get("output")
        if output is None:
            raise ValueError("contract has no `output`: name a stream with --stream")
        return output
    for output in contract.get("outputs") or []:
        if output.get("stream") == stream:
            return output
    known = [output.get("stream") for output in contract.get("outputs") or []]
    raise ValueError(f"contract has no output for stream {stream!r} (it has {known})")


def compare(backend, expected: str, actual: str, contract: dict, tolerances: dict, *,
            output: dict | None = None, accepted_classes: Sequence[str] = (),
            approvals: Sequence[dict] = (), segment_dag: dict | None = None,
            golden_set: str | None = None, sample_rows: int = 5,
            max_diff_rows: int = 10000) -> dict:
    """Compares two sandbox tables and returns the `validation.json` report (already JSON-safe).

    `output` selects one entry of `contract["outputs"]`; its `columns` and `keys` drive every
    statement. `accepted_classes` comes from `mappings/global.yaml` and `approvals` from
    `manifest.accepted_diffs`; a cluster needs both before it stops being a failure.
    """
    started = time.perf_counter()
    comparison = _Comparison(backend, expected, actual, contract,
                             tolerances or DEFAULT_TOLERANCES, output or select_output(contract),
                             segment_dag=segment_dag, sample_rows=sample_rows,
                             max_diff_rows=max_diff_rows)
    checks, diff = comparison.run()
    _account_for_every_check(checks, diff, max_diff_rows)
    verdict = _verdict(diff.clusters, contract.get("segment"), accepted_classes, approvals)
    if diff.truncated or _unexplained(checks, diff):
        # The guard, applied last and unconditionally: a report may not pass on a difference it
        # has itself written down. Nothing above this line can talk its way past it.
        verdict = "FAIL"
    for cluster in diff.clusters:
        cluster.pop("_sides", None)
    return {
        "segment": contract.get("segment"),
        "golden_set": golden_set,
        "verdict": verdict,
        "checks": checks,
        "diff_clusters": diff.clusters,
        "normalizations_applied": comparison.normalizations_applied,
        "idempotent": None,             # a second run decides that; validate_segment.py fills it in
        "runtime_ms": int(round((time.perf_counter() - started) * 1000)),
        "credits": None,                # a local DuckDB run burns no Snowflake credits
        "needs_human": diff.needs_human,
        "truncated": diff.truncated,
    }


#: `checks` entries that are supporting numbers rather than a verdict. `column_counts` is
#: deliberately here: two values inside tolerance can still be two *distinct* values, so a
#: distinct-count difference is not by itself a failure, and `aggregate_mismatches` is only the
#: detail behind `aggregates`.
_DETAIL_CHECKS = frozenset({"column_counts", "aggregate_mismatches"})


def _check_failed(name: str, value: Any) -> bool:
    """Did this check report a difference? Raises for an entry that declares itself as neither
    a verdict nor detail, so a new `checks` key cannot quietly slip past the guard."""
    if value == "SKIPPED":
        return False
    if name in ("schema", "nullability", "aggregates"):
        return value != "PASS"
    if name in ("counts", "aggregates_rows"):
        return value.get("verdict") != "PASS"
    if name == "set_diff":
        return bool(value.get("only_expected") or value.get("only_actual"))
    if name == "column_mismatches":
        return bool(value)
    if name in _DETAIL_CHECKS:
        return False
    raise ValueError(f"checks[{name!r}] is neither a verdict nor declared detail: add it to "
                     f"_check_failed or to _DETAIL_CHECKS in scripts/compare.py, so the verdict "
                     f"guard knows whether a PASS may ignore it")


def _sides_needed(name: str, value: Any) -> set[str]:
    """Which side has the rows that would have to be explained, for the two row-level checks."""
    if name == "counts":
        return {"expected"} if value["expected"] > value["actual"] else {"actual"}
    return {side for side, key in (("expected", "only_expected"), ("actual", "only_actual"))
            if value.get(key)}


#: The only check a keyless row-presence cluster is allowed to answer for beyond the row-level
#: ones. On a keyless stream `_comparable_set` is empty, so `_aggregates` runs over the whole
#: table: a row that is missing or extra shifts every aggregate of every column it carries a
#: value in, and no arithmetic can separate that shift from a value difference. The keyed path
#: keeps the strict rule — there the comparable set holds moved rows out of the aggregates, so an
#: aggregate that still disagrees is about values and only a column cluster may speak for it.
_KEYLESS_ROW_ACCOUNTABLE = frozenset({"aggregates"})


def _unexplained(checks: dict, diff: _Diff) -> list[str]:
    """The failing checks that no diff cluster accounts for.

    Accounting is specific, never global. A cluster about rows — rows present on one side only,
    a duplicated key, or (with no keys) a pair of surplus rows — accounts for the two row-level
    checks, and only on the side whose rows it is about. On the keyed path it accounts for
    nothing else: the aggregates are computed over the comparable set precisely so that moved
    rows cannot move them, so an aggregate that still disagrees is about values, and only a
    cluster naming that column can speak for it. `_KEYLESS_ROW_ACCOUNTABLE` is the one documented
    exception, and it is reached only when the stream declares no keys. A failing check with
    nothing behind it is the contradiction this guard exists to stop.
    """
    schema_named = any(cluster["scope"] == "schema" for cluster in diff.clusters)
    # Every cluster says which sides' rows it is about; only a cluster about rows sets it.
    sides = {side for cluster in diff.clusters for side in cluster["_sides"]}
    named = {name for cluster in diff.clusters if cluster["scope"] == "columns"
             for name in cluster["columns"]}
    moved_rows = diff.keyless and any(cluster["scope"] == "rows" for cluster in diff.clusters)
    unexplained = []
    for name, value in checks.items():
        if not _check_failed(name, value):
            continue
        if name == "schema":
            accounted = schema_named
        elif name in ("counts", "set_diff"):
            accounted = _sides_needed(name, value) <= sides
        else:
            columns = diff.evidence.get(name, ([], 0))[0]
            accounted = bool(columns) and set(columns) <= named
            accounted = accounted or (moved_rows and name in _KEYLESS_ROW_ACCOUNTABLE)
        if not accounted:
            unexplained.append(name)
    return unexplained


def _account_for_every_check(checks: dict, diff: _Diff, max_diff_rows: int) -> None:
    """Gives every unaccounted-for failing check a cluster of its own, so the report says what it
    saw even when no row-level difference survived to explain it."""
    for name in _unexplained(checks, diff):
        columns, count = diff.evidence.get(name, ([], 0))
        diff.clusters.append(_cluster(
            "UNKNOWN", sorted(columns), count, [], suspect=None, scope="synthetic",
            hint=f"{name} failed with no row-level difference"))
        diff.needs_human = True
    if diff.truncated and not diff.clusters:
        diff.clusters.append(_cluster(
            "UNKNOWN", [], max_diff_rows, [], suspect=None, scope="synthetic",
            hint="diff truncated at max_diff_rows before every difference was examined; "
                 "raise --max-diff-rows"))
        diff.needs_human = True
    diff.clusters = _sort_clusters(diff.clusters)


def _verdict(clusters: list[dict], segment: str | None, accepted_classes: Sequence[str],
             approvals: Sequence[dict]) -> str:
    if not clusters:
        return "PASS"
    accepted = set(accepted_classes or ())
    for cluster in clusters:
        if cluster["class"] not in accepted or not _approved(cluster, segment, approvals):
            return "FAIL"
    return "PASS_WITH_ACCEPTED_DIFF"


#: Coordinator ruling F4: never approvable, at ANY scope, whatever `accepted_diff_classes` says.
#: `GOLDEN_DATA` is a NOT NULL violation sitting in the golden data itself (`_nullability_clusters`
#: writes it with scope="columns") -- a human signature cannot fix broken reference data, so it
#: must never be allowed to silence the finding. `UNKNOWN` explains nothing by definition (today
#: only ever emitted at scope="synthetic", already unapprovable on that basis alone, but this is
#: the class-level guard so a future column-scope UNKNOWN stays unapprovable too).
_NEVER_APPROVABLE_CLASSES = frozenset({"GOLDEN_DATA", "UNKNOWN"})


def _approved(cluster: dict, segment: str | None, approvals: Sequence[dict]) -> bool:
    """Did a human sign off this segment's class over at least these columns?

    **`GOLDEN_DATA` and `UNKNOWN` are never approvable, at any scope** (`_NEVER_APPROVABLE_CLASSES`,
    coordinator ruling F4) — checked first, before `scope` is even looked at.

    **A cluster about rows is never approvable either.** An approval record is
    `{segment, class, columns, approver, date}`: it can pin which columns a difference is allowed
    in, and nothing in it pins *which* rows, or how many. A signature on "these rows are missing"
    would therefore go on covering any later set of missing rows — including the same rows coming
    back with every value in them rewritten, which is exactly what a report looks like when a
    value regression is too wide for the pairing threshold and is re-told as one side's rows
    vanishing and the other's appearing. The same goes for a duplicated key, a schema failure and
    a synthetic cluster. So only a `"columns"` cluster that actually names columns, in a class that
    isn't one of the never-approvable ones, can reach `PASS_WITH_ACCEPTED_DIFF`, on either path,
    whatever `accepted_diff_classes` says.

    `scope` is written by `_cluster` onto every cluster and is still there when `_verdict` runs —
    only `_sides` is popped, and only afterwards — but an absent `scope` is read as "not a column
    cluster", which is the safe way round.
    """
    if cluster["class"] in _NEVER_APPROVABLE_CLASSES:
        return False
    if cluster.get("scope") != "columns" or not cluster["columns"]:
        return False
    columns = {str(name).upper() for name in cluster["columns"]}
    for approval in approvals or ():
        if (approval.get("segment") == segment and approval.get("class") == cluster["class"]
                and columns <= {str(name).upper() for name in approval.get("columns") or []}):
            return True
    return False


# --- the comparison --------------------------------------------------------------------------


class _Comparison:
    """One expected/actual pair, its contract, and the checks run over them."""

    def __init__(self, backend, expected: str, actual: str, contract: dict, tolerances: dict,
                 output: dict, *, segment_dag: dict | None, sample_rows: int, max_diff_rows: int):
        self.backend = backend
        self.expected = expected
        self.actual = actual
        self.contract = contract
        self.tolerances = tolerances
        self.segment_dag = segment_dag
        self.sample_rows = max(0, int(sample_rows))
        self.max_diff_rows = max(0, int(max_diff_rows))

        declared = output.get("columns") or []
        if not declared:
            raise ValueError(f"contract output {output.get('stream')!r} lists no columns")
        normalizations, self.normalizations_applied = _parse_normalizations(
            contract, {str(column["name"]).upper() for column in declared})
        self.columns = [_Column(name=str(column["name"]).upper(),
                                sql=_identifier(str(column["name"])),
                                family=type_family(column.get("type")),
                                ops=normalizations.get(str(column["name"]).upper(), ()))
                        for column in declared]
        self.nullable = {str(column["name"]).upper(): column.get("nullable", True)
                         for column in declared}
        by_name = {column.name: column for column in self.columns}
        self.keys: list[_Column] = []
        for key in output.get("keys") or []:
            if str(key).upper() not in by_name:
                raise ValueError(f"contract key {key!r} is not one of the output's columns "
                                 f"{sorted(by_name)}")
            self.keys.append(by_name[str(key).upper()])
        key_names = {key.name for key in self.keys}
        self.non_keys = [column for column in self.columns if column.name not in key_names]

        self.column_tolerances = {str(name).upper(): value for name, value
                                  in (contract.get("tolerances") or {}).items()}
        self.order_dependent = {
            str(name).upper() for name
            in (contract.get("ordering") or {}).get("order_dependent_columns") or []}
        precision = str(tolerances.get("timestamp_precision", "milliseconds"))
        if precision not in _PRECISION_DIGITS:
            raise ValueError(f"unknown timestamp_precision {precision!r}: "
                             f"expected one of {sorted(_PRECISION_DIGITS)}")
        self.timestamp_digits = _PRECISION_DIGITS[precision]
        self.rounding_abs = float((tolerances.get("rounding") or {}).get("abs", 0.01))

        self.expected_rows = 0
        self.actual_rows = 0
        self.aggregate_rows = 0          # rows the aggregates cover: the comparable set, if keyed
        self.expected_counts: dict[str, dict[str, int]] = {}
        self.actual_counts: dict[str, dict[str, int]] = {}
        self._topological: list[dict] | None = None

    # --- the run ---

    def run(self) -> tuple[dict, _Diff]:
        for fqn in (self.expected, self.actual):
            if not self.backend.table_exists(fqn):
                raise ValueError(f"there is no table {fqn} to compare")

        checks: dict[str, Any] = {
            "schema": "PASS", "nullability": "SKIPPED", "counts": "SKIPPED",
            "aggregates": "SKIPPED", "aggregates_rows": "SKIPPED", "set_diff": "SKIPPED",
            "column_mismatches": "SKIPPED", "column_counts": "SKIPPED",
            "aggregate_mismatches": "SKIPPED"}

        problems, affected, contract_disagrees = self._schema_problems()
        if problems:
            checks["schema"] = "FAIL"
            columns = sorted(affected)
            cluster = _cluster("TYPE", columns, len(columns), [],
                               suspect=self._suspect_for_columns(columns), scope="schema",
                               note="; ".join(problems))
            return checks, _Diff(clusters=[cluster], needs_human=contract_disagrees,
                                 evidence={"schema": (columns, len(columns))})

        self.expected_rows = self._row_count(self.expected)
        self.actual_rows = self._row_count(self.actual)
        checks["counts"] = {"expected": self.expected_rows, "actual": self.actual_rows,
                            "verdict": "PASS" if self.expected_rows == self.actual_rows else "FAIL"}
        checks["column_counts"] = self._count_differences()
        nullability = self._nullability_clusters()
        checks["nullability"] = "FAIL" if nullability else "PASS"

        self.aggregate_rows = self._comparable_rows()
        # Nothing comparable while both sides hold rows means the report compared nothing: every
        # key either moved or was duplicated, and no approval of those should pass it off as parity.
        nothing_comparable = (not self.aggregate_rows and self.expected_rows and self.actual_rows)
        checks["aggregates_rows"] = {"rows": self.aggregate_rows,
                                     "verdict": "FAIL" if nothing_comparable else "PASS"}
        checks["aggregates"], checks["aggregate_mismatches"] = self._aggregate_differences()

        diff = self._keyed_diff() if self.keys else self._multiset_diff()
        diff.clusters.extend(nullability)
        diff.needs_human = diff.needs_human or any(cluster["class"] == "GOLDEN_DATA"
                                                   for cluster in nullability)
        diff.clusters = _sort_clusters(diff.clusters)
        checks["set_diff"] = diff.set_diff
        checks["column_mismatches"] = diff.column_mismatches

        diff.evidence = {
            "schema": ([], 0),
            "nullability": (sorted({name for c in nullability for name in c["columns"]}),
                            sum(cluster["count"] for cluster in nullability)),
            "counts": ([], abs(self.expected_rows - self.actual_rows)),
            "aggregates": (sorted(checks["aggregate_mismatches"]),
                           len(checks["aggregate_mismatches"])),
            "aggregates_rows": ([], self.aggregate_rows),
            "set_diff": ([], sum(diff.set_diff.values())),
            "column_mismatches": (sorted(diff.column_mismatches),
                                  sum(diff.column_mismatches.values()))}
        return checks, diff

    # --- 1. schema ---

    def _schema_problems(self) -> tuple[list[str], set[str], bool]:
        """(problem descriptions, columns involved, whether the contract itself is out of step).

        Expected and actual are compared to each other in full — that is the parity question —
        and the contract's column list has to name the same columns as both, because every
        statement below is built from it.
        """
        expected = self.backend.table_columns(self.expected)
        actual = self.backend.table_columns(self.actual)
        expected_names = [str(column["name"]).upper() for column in expected]
        actual_names = [str(column["name"]).upper() for column in actual]
        contract_names = [column.name for column in self.columns]

        problems: list[str] = []
        affected: set[str] = set()

        missing = [name for name in expected_names if name not in set(actual_names)]
        extra = [name for name in actual_names if name not in set(expected_names)]
        if missing:
            problems.append(f"columns missing from actual: {missing}")
            affected.update(missing)
        if extra:
            problems.append(f"columns only in actual: {extra}")
            affected.update(extra)
        if not missing and not extra and expected_names != actual_names:
            problems.append(f"column order differs: expected {expected_names}, actual {actual_names}")
            affected.update(expected_names)

        actual_types = dict(zip(actual_names, (column["type"] for column in actual)))
        for name, column in zip(expected_names, expected):
            if name not in actual_types:
                continue
            mine, theirs = column["type"], actual_types[name]
            family, other = type_family(mine), type_family(theirs)
            # "other" means type_family does not know the type, so it cannot say two of them are
            # the same family either: fall back to the declared types.
            unequal = family != other or (family == "other" and _base_type(mine) != _base_type(theirs))
            if unequal:
                problems.append(f"{name}: expected type {mine} ({family}), actual {theirs} ({other})")
                affected.add(name)

        contract_disagrees = False
        for side, names in (("expected", expected_names), ("actual", actual_names)):
            absent = [name for name in contract_names if name not in set(names)]
            unlisted = [name for name in names if name not in set(contract_names)]
            if absent:
                problems.append(f"contract columns not in {side}: {absent}")
                affected.update(absent)
            if unlisted:
                problems.append(f"{side} columns the contract does not list: {unlisted}")
                affected.update(unlisted)
            if (absent or unlisted) and side == "expected":
                contract_disagrees = True   # golden data or contract, not the SQL
        return problems, affected, contract_disagrees

    # --- 2. counts ---

    def _row_count(self, table: str) -> int:
        return int(self.backend.query(f"SELECT COUNT(*) FROM {table}")[1][0][0])

    def _comparable_set(self) -> str | None:
        """A `WITH` body naming the keys that occur EXACTLY ONCE on BOTH sides, or None if the
        contract declares no keys.

        Rows that moved or were duplicated already have their own clusters and their own numbers
        in `set_diff`; letting them into the aggregates as well would make one difference look
        like several, and would let a cluster about rows be mistaken for an explanation of a
        column's arithmetic. So the aggregates compare like for like and nothing else.
        """
        if not self.keys:
            return None
        aliased = ", ".join(f"{self._ref(_ONE_SIDE, key)} AS K{i}" for i, key in enumerate(self.keys))
        grouped = ", ".join(self._ref(_ONE_SIDE, key) for key in self.keys)
        sides = [f"SELECT {aliased} FROM {table} {_ONE_SIDE} GROUP BY {grouped} HAVING COUNT(*) = 1"
                 for table in (self.expected, self.actual)]
        return f"COMPARABLE_KEYS AS ({' INTERSECT '.join(sides)})"

    def _in_comparable_set(self) -> str:
        """The `WHERE` clause restricting a side to the comparable keys."""
        matched = " AND ".join(
            f"NOT (COMPARABLE_KEYS.K{i} IS DISTINCT FROM {self._ref(_ONE_SIDE, key)})"
            for i, key in enumerate(self.keys))
        return f"WHERE EXISTS (SELECT 1 FROM COMPARABLE_KEYS WHERE {matched})"

    def _comparable_rows(self) -> int:
        """How many rows the aggregates and the count detail cover on each side."""
        comparable = self._comparable_set()
        if comparable is None:
            return max(self.expected_rows, self.actual_rows)
        return int(self.backend.query(
            f"WITH {comparable} SELECT COUNT(*) FROM COMPARABLE_KEYS")[1][0][0])

    def _column_counts(self, table: str, comparable: bool = False) -> dict[str, dict[str, int]]:
        """Every column's NULL count and distinct count, in one query per side."""
        parts = []
        for column in self.columns:
            reference = self._ref(_ONE_SIDE, column)
            # DuckDB's COUNT_IF is NULL over an empty set where Snowflake's is 0, and an
            # empty set is ordinary here: the `empty` golden set, or nothing comparable.
            parts.append(f"COALESCE(COUNT_IF({reference} IS NULL), 0)")
            parts.append(f"COUNT(DISTINCT {reference})")
        row = self.backend.query(self._over(table, ", ".join(parts), comparable))[1][0]
        return {column.name: {"nulls": int(row[2 * i]), "distinct": int(row[2 * i + 1])}
                for i, column in enumerate(self.columns)}

    def _over(self, table: str, selected: str, comparable: bool) -> str:
        """`SELECT <aggregates> FROM <table>`, over the comparable set when asked for."""
        body = self._comparable_set() if comparable else None
        if body is None:
            return f"SELECT {selected} FROM {table} {_ONE_SIDE}"
        return f"WITH {body} SELECT {selected} FROM {table} {_ONE_SIDE} {self._in_comparable_set()}"

    def _count_differences(self) -> dict:
        # Whole-table counts, kept on the instance: `nullability` is a contract violation wherever
        # the NULL sits, and the row-presence NULL rule asks about the golden data as a whole.
        self.expected_counts = self._column_counts(self.expected)
        self.actual_counts = self._column_counts(self.actual)
        # What gets reported is the comparable set, for the same reason the aggregates are: a row
        # that moved is one difference and is already reported as one, by `set_diff`.
        expected = self._column_counts(self.expected, comparable=True)
        actual = self._column_counts(self.actual, comparable=True)
        differences = {}
        for column in self.columns:
            mine, theirs = expected[column.name], actual[column.name]
            if mine != theirs:
                differences[column.name] = {
                    "nulls": {"expected": mine["nulls"], "actual": theirs["nulls"]},
                    "distinct": {"expected": mine["distinct"], "actual": theirs["distinct"]}}
        return differences

    def _nullability_clusters(self) -> list[dict]:
        """NULLs in a column the contract declares `NOT NULL` (program spec §9.2).

        Checked on the data, not on the DDL: locally every column a CTAS produces is nullable, so
        the declared nullability only means anything against the rows. A NULL in actual is a
        translation bug; a NULL in the golden data means the golden data or the contract is wrong,
        which no amount of fixing SQL will settle.
        """
        clusters = []
        for column in self.columns:
            if self.nullable.get(column.name, True):
                continue
            for table, side, counts, cluster_class in (
                    (self.expected, "expected", self.expected_counts, "GOLDEN_DATA"),
                    (self.actual, "actual", self.actual_counts, "NULL_SEMANTICS")):
                nulls = counts[column.name]["nulls"]
                if nulls:
                    clusters.append(_cluster(
                        cluster_class, [column.name], nulls, self._null_examples(table, column, side),
                        suspect=self._suspect_for_columns([column.name]), scope="columns",
                        note="NULL in a column the contract calls NOT NULL"
                             + ("" if side == "actual" else ", in the golden data")))
        return clusters

    def _null_examples(self, table: str, column: _Column, side: str) -> list[dict]:
        if not self.keys or not self.sample_rows:
            return []
        selected = ", ".join(f"{_ONE_SIDE}.{key.sql}" for key in self.keys)
        _, rows = self.backend.query(
            f"SELECT {selected} FROM {table} {_ONE_SIDE} WHERE {_ONE_SIDE}.{column.sql} IS NULL "
            f"ORDER BY {_ordinals(len(self.keys))} LIMIT {self.sample_rows}")
        return [{"key": self._key_of(row),
                 "expected": {column.name: None} if side == "expected" else None,
                 "actual": {column.name: None} if side == "actual" else None} for row in rows]

    # --- 3. aggregates ---

    def _aggregates(self, table: str) -> dict[tuple[str, str], Any]:
        """One query per side, over the comparable set. `sum_abs` is not reported: it is the
        `SUM(ABS(col))` the accumulated-tolerance bound needs."""
        parts: list[str] = []
        labels: list[tuple[str, str]] = []
        for column in self.columns:
            reference = self._ref(_ONE_SIDE, column)
            if column.family in _NUMERIC_FAMILIES:
                for function in ("SUM", "MIN", "MAX", "AVG"):
                    parts.append(f"{function}({reference})")
                    labels.append((column.name, function.lower()))
                parts.append(f"SUM(ABS({reference}))")
                labels.append((column.name, "sum_abs"))
            elif column.family == "string":
                parts.append(f"MIN(LENGTH({reference}))")
                labels.append((column.name, "min_length"))
                parts.append(f"MAX(LENGTH({reference}))")
                labels.append((column.name, "max_length"))
        if not parts:
            return {}
        row = self.backend.query(self._over(table, ", ".join(parts), comparable=True))[1][0]
        return dict(zip(labels, row))

    def _aggregate_differences(self) -> tuple[str, dict]:
        expected = self._aggregates(self.expected)
        actual = self._aggregates(self.actual)
        by_name = {column.name: column for column in self.columns}
        mismatches: dict[str, dict] = {}
        for (name, aggregate), mine in expected.items():
            if aggregate == "sum_abs":
                continue                     # an input to the bound, not a claim about the data
            theirs = actual.get((name, aggregate))
            magnitude = max(_as_magnitude(expected.get((name, "sum_abs"))),
                            _as_magnitude(actual.get((name, "sum_abs"))))
            if not self._aggregate_equal(by_name[name], aggregate, mine, theirs, magnitude):
                mismatches.setdefault(name, {})[aggregate] = {"expected": _jsonable(mine),
                                                              "actual": _jsonable(theirs)}
        return ("FAIL" if mismatches else "PASS"), mismatches

    def _aggregate_equal(self, column: _Column, aggregate: str, mine: Any, theirs: Any,
                         magnitude: float) -> bool:
        """`SUM` and `AVG` get the tolerance the rows may legitimately have accumulated; `MIN`,
        `MAX` and the string lengths are single values and keep the per-value tolerance.

        The bound is the triangle inequality applied to the declared per-row tolerance:

            |Σe − Σa| ≤ Σ|eᵢ − aᵢ| ≤ Σ max(float_abs, float_rel·max(|eᵢ|,|aᵢ|))
                                  ≤ n·float_abs + float_rel·Σ max(|eᵢ|,|aᵢ|)

        and `Σ max(|eᵢ|,|aᵢ|)` is taken as `max(SUM(ABS(e)), SUM(ABS(a)))`, which is what one
        query per side can produce. That is a slight *under*-estimate, so the check is if anything
        stricter than the true bound — and only by `float_rel` of it, because a row within
        tolerance has `|eᵢ|` and `|aᵢ|` within tolerance of each other too.

        Scaling by `|Σ|` instead, as an ordinary relative tolerance would, collapses as soon as
        large values cancel: 200 rows of ±1e9 sum to 0, and a sum of 0 grants no tolerance at all
        while each of those rows is entitled to 1.0.
        """
        if aggregate not in ("sum", "avg"):
            return self._values_equal(column, mine, theirs)
        if mine is None or theirs is None:
            return mine is None and theirs is None
        if mine == theirs:
            return True
        absolute, relative = self._tolerance(column)
        if absolute is None:                 # an exact NUMBER column the contract did not loosen
            return False
        rows = self.aggregate_rows
        threshold = rows * absolute + relative * magnitude
        if aggregate == "avg":
            threshold = threshold / rows if rows else threshold
        return _abs_difference(mine, theirs) <= threshold

    # --- 4. keyed diff ---

    def _keyed_diff(self) -> _Diff:
        diff = _Diff()

        expected_duplicates, expected_examples = self._duplicate_keys(self.expected)
        if expected_duplicates:
            diff.needs_human = True     # the golden data cannot be repaired by fixing SQL
            diff.clusters.append(_cluster(
                "GOLDEN_DATA", sorted(key.name for key in self.keys), expected_duplicates,
                [{"key": key, "expected": {"rows": rows}, "actual": None}
                 for key, rows in expected_examples],
                suspect=None, scope="rows", sides=("expected",),
                note="duplicate keys in expected"))
        actual_duplicates, actual_examples = self._duplicate_keys(self.actual)
        if actual_duplicates:
            diff.clusters.append(_cluster(
                "LOGIC", sorted(key.name for key in self.keys), actual_duplicates,
                [{"key": key, "expected": None, "actual": {"rows": rows}}
                 for key, rows in actual_examples],
                suspect=self._suspect_for_rows(), scope="rows", sides=("actual",),
                note="duplicate keys in actual"))

        only_expected = self._only_on_one_side(self.expected, self.actual, "expected")
        only_actual = self._only_on_one_side(self.actual, self.expected, "actual")
        diff.set_diff = {"only_expected": only_expected["count"],
                         "only_actual": only_actual["count"]}
        diff.clusters.extend(cluster for cluster
                             in (self._row_presence_cluster(only_expected),
                                 self._row_presence_cluster(only_actual)) if cluster)

        mismatching, diff.truncated = self._mismatching_rows()
        per_column: dict[str, int] = {}
        for row in mismatching:
            for name in row["values"]:
                per_column[name] = per_column.get(name, 0) + 1
        diff.column_mismatches = dict(sorted(per_column.items()))
        diff.clusters.extend(self._value_clusters(mismatching))
        return diff

    def _duplicate_keys(self, table: str) -> tuple[int, list[tuple[dict, int]]]:
        """(how many keys occur more than once, a bounded sample of them with their row counts)."""
        group = ", ".join(self._ref(_ONE_SIDE, key) for key in self.keys)
        total = int(self.backend.query(
            f"SELECT COUNT(*) FROM (SELECT {group} FROM {table} {_ONE_SIDE} GROUP BY {group} "
            f"HAVING COUNT(*) > 1) DUPLICATE_KEYS")[1][0][0])
        if not total:
            return 0, []
        _, rows = self.backend.query(
            f"SELECT {group}, COUNT(*) FROM {table} {_ONE_SIDE} GROUP BY {group} "
            f"HAVING COUNT(*) > 1 ORDER BY {_ordinals(len(self.keys))} LIMIT {self.sample_rows}")
        return total, [(self._key_of(row), int(row[-1])) for row in rows]

    def _only_on_one_side(self, table: str, other: str, side: str) -> dict:
        """Rows of `table` whose key is absent from `other`.

        The count and the "NULL in every one of them" answer both come from SQL, so neither
        depends on how many example rows were pulled.
        """
        matched = " AND ".join(
            f"NOT ({self._ref(_ONE_SIDE, key)} IS DISTINCT FROM {self._ref(_OTHER_SIDE, key)})"
            for key in self.keys)
        where = f"NOT EXISTS (SELECT 1 FROM {other} {_OTHER_SIDE} WHERE {matched})"
        count = int(self.backend.query(
            f"SELECT COUNT(*) FROM {table} {_ONE_SIDE} WHERE {where}")[1][0][0])
        if not count:
            return {"side": side, "count": 0, "rows": [], "all_null": []}

        selected = ", ".join(f"{_ONE_SIDE}.{column.sql}" for column in self.columns)
        _, rows = self.backend.query(
            f"SELECT {selected} FROM {table} {_ONE_SIDE} WHERE {where} "
            f"ORDER BY {_ordinals(len(self.columns))} LIMIT {self.sample_rows}")
        all_null: list[str] = []
        if self.non_keys:
            filled = ", ".join(f"COALESCE(COUNT_IF({_ONE_SIDE}.{column.sql} IS NOT NULL), 0)"
                               for column in self.non_keys)
            counts = self.backend.query(
                f"SELECT {filled} FROM {table} {_ONE_SIDE} WHERE {where}")[1][0]
            all_null = [column.name for column, count_ in zip(self.non_keys, counts) if not count_]
        return {"side": side, "count": count, "rows": rows, "all_null": all_null}

    def _differs(self, column: _Column) -> str:
        """The SQL predicate for "this column may disagree", a candidate filter, never a judge.

        A bare `IS DISTINCT FROM` pulls every row whose value differs at all, including the ones
        the declared tolerance will forgive — and with enough of them the rows that really differ
        never reach `max_diff_rows`. For a column with an absolute tolerance the predicate drops
        differences that are inside it, with `_PRE_FILTER_MARGIN` taken off the threshold so the
        pull stays a strict superset of what the exact test in Python could flag: the effective
        tolerance there is `max(float_abs, float_rel·…)`, never smaller than `float_abs`, and the
        margin covers the gap between SQL's binary subtraction and Python's exact decimals.
        Whether a pulled row is really a difference is decided only by `_values_equal`.
        """
        mine, theirs = self._ref(_EXPECTED_SIDE, column), self._ref(_ACTUAL_SIDE, column)
        absolute = self._tolerance(column)[0] if column.family in _NUMERIC_FAMILIES else None
        if absolute is None:
            return f"({mine} IS DISTINCT FROM {theirs})"
        threshold = absolute * (1 - _PRE_FILTER_MARGIN)
        # NULL on one side only is caught by the first half; NULL on both makes the second half
        # NULL, which SQL's WHERE treats as "no difference" — which is exactly right. A NaN is
        # pulled, because DuckDB and Snowflake both order NaN above every number.
        return f"(({mine} IS NULL) <> ({theirs} IS NULL) OR ABS({mine} - {theirs}) > {threshold!r})"

    def _mismatching_rows(self) -> tuple[list[dict], bool]:
        """The rows whose values disagree beyond tolerance, in key order.

        Each row is `{"key": {…}, "values": {COLUMN: (expected, actual)}}` and carries only the
        columns that actually disagree — the SQL pulls a row as soon as *any* column does, and the
        tolerances then decide which of them are real.
        """
        if not self.non_keys:
            return [], False
        matched = " AND ".join(
            f"NOT ({self._ref(_EXPECTED_SIDE, key)} IS DISTINCT FROM {self._ref(_ACTUAL_SIDE, key)})"
            for key in self.keys)
        differs = " OR ".join(self._differs(column) for column in self.non_keys)
        selected = [f"{_EXPECTED_SIDE}.{key.sql}" for key in self.keys]
        for column in self.non_keys:
            selected.append(f"{_EXPECTED_SIDE}.{column.sql}")
            selected.append(f"{_ACTUAL_SIDE}.{column.sql}")
        _, rows = self.backend.query(
            f"SELECT {', '.join(selected)} FROM {self.expected} {_EXPECTED_SIDE} "
            f"JOIN {self.actual} {_ACTUAL_SIDE} ON {matched} WHERE {differs} "
            f"ORDER BY {_ordinals(len(selected))} LIMIT {self.max_diff_rows + 1}")

        truncated = len(rows) > self.max_diff_rows
        mismatching: list[dict] = []
        for row in rows[:self.max_diff_rows]:
            values = {}
            for i, column in enumerate(self.non_keys):
                mine, theirs = row[len(self.keys) + 2 * i], row[len(self.keys) + 2 * i + 1]
                if not self._values_equal(column, mine, theirs):
                    values[column.name] = (mine, theirs)
            if values:
                mismatching.append({"key": self._key_of(row), "values": values})
        return mismatching, truncated

    # --- 5. row multiset (no keys) ---

    def _group_cte(self, name: str, table: str) -> str:
        """`<name> AS (…)`: one row per distinct (normalized) row of `table`, with its multiplicity."""
        aliased = ", ".join(f"{self._ref(_ONE_SIDE, column)} AS V{i}"
                            for i, column in enumerate(self.columns))
        grouped = ", ".join(self._ref(_ONE_SIDE, column) for column in self.columns)
        return (f"{name} AS (SELECT {aliased}, COUNT(*) AS GROUP_ROWS "
                f"FROM {table} {_ONE_SIDE} GROUP BY {grouped})")

    def _multiset_counts(self) -> tuple[int, int]:
        """(`only_expected`, `only_actual`): the surplus copies per distinct row, summed.

        Exact and computed entirely in SQL, so it does not depend on how many rows were pulled
        into Python afterwards.
        """
        matched = " AND ".join(f"NOT (EXPECTED_GROUPS.V{i} IS DISTINCT FROM ACTUAL_GROUPS.V{i})"
                               for i in range(len(self.columns)))
        only_expected, only_actual = self.backend.query(
            f"WITH {self._group_cte('EXPECTED_GROUPS', self.expected)}, "
            f"{self._group_cte('ACTUAL_GROUPS', self.actual)} "
            f"SELECT COALESCE(SUM(GREATEST(COALESCE(EXPECTED_GROUPS.GROUP_ROWS, 0) "
            f"- COALESCE(ACTUAL_GROUPS.GROUP_ROWS, 0), 0)), 0), "
            f"COALESCE(SUM(GREATEST(COALESCE(ACTUAL_GROUPS.GROUP_ROWS, 0) "
            f"- COALESCE(EXPECTED_GROUPS.GROUP_ROWS, 0), 0)), 0) "
            f"FROM EXPECTED_GROUPS FULL OUTER JOIN ACTUAL_GROUPS ON {matched}")[1][0]
        return int(only_expected), int(only_actual)

    def _surplus_rows(self, table: str, other: str, limit: int) -> list[tuple]:
        """The rows of `table` that `other` has no copy left for, at most `limit` of them.

        One row of the result per surplus *copy*: a row that `table` holds three times and `other`
        once is two surplus copies, and the two are indistinguishable, so SQL returns the distinct
        row with its surplus count and Python repeats it. Ordered by every column, so which rows a
        `limit` keeps is the same on any backend and in any physical row order.
        """
        if limit <= 0:
            return []
        matched = " AND ".join(f"NOT (SURPLUS_GROUPS.V{i} IS DISTINCT FROM OTHER_GROUPS.V{i})"
                               for i in range(len(self.columns)))
        values = ", ".join(f"SURPLUS_GROUPS.V{i}" for i in range(len(self.columns)))
        _, groups = self.backend.query(
            f"WITH {self._group_cte('SURPLUS_GROUPS', table)}, "
            f"{self._group_cte('OTHER_GROUPS', other)} "
            f"SELECT {values}, SURPLUS_GROUPS.GROUP_ROWS "
            f"- COALESCE(OTHER_GROUPS.GROUP_ROWS, 0) AS SURPLUS_ROWS "
            f"FROM SURPLUS_GROUPS LEFT JOIN OTHER_GROUPS ON {matched} "
            f"WHERE SURPLUS_GROUPS.GROUP_ROWS > COALESCE(OTHER_GROUPS.GROUP_ROWS, 0) "
            f"ORDER BY {_ordinals(len(self.columns))} LIMIT {limit}")
        rows: list[tuple] = []
        for group in groups:
            values_tuple = tuple(group[:len(self.columns)])
            rows.extend([values_tuple] * min(int(group[-1]), limit - len(rows)))
            if len(rows) >= limit:
                break
        return rows

    def _pair_surplus(self, mine: list[tuple],
                      theirs: list[tuple]) -> tuple[list[tuple[tuple, tuple, list[str]]],
                                                    list[tuple], list[tuple]]:
        """Pairs surplus expected rows with surplus actual rows by nearest match.

        A heuristic, and the report says so: each cluster it leads to carries
        `"paired_by": "nearest_match"`. Expected rows are taken in their sorted order and each is
        given the not-yet-used actual row it disagrees with in the fewest columns (ties going to
        the first in sorted order), which makes the result independent of physical row order. A
        pair is accepted when **at least half of the columns are equal** — `differing <= n // 2`,
        which for every `n >= 2` is below `n`, so an accepted pair always has a column in common.
        A one-column table is never paired: with a single column "nearest" would mean nothing.

        Refusing to pair is not the safe direction, which is why the threshold is not tighter than
        this. An unpaired row becomes a row-presence cluster, and a row-presence cluster names no
        column at all: a value regression that is too wide to pair is re-told as one side's rows
        vanishing and the other's appearing, which says far less about what went wrong. (It can
        no longer be *approved* — `_approved` refuses every cluster about rows — but a report that
        cannot name the columns still sends a worse diagnosis to the fixer.)

        Returns (pairs, unpaired mine, unpaired theirs); a pair is
        `(expected row, actual row, the columns they disagree in)` and may disagree in none of
        them, which is two rows equal within tolerance.
        """
        count = len(self.columns)
        if count < 2:
            return [], list(mine), list(theirs)
        allowed = count // 2
        mine_budget, theirs_budget = (min(len(mine), _MAX_PAIRED_ROWS),
                                      min(len(theirs), _MAX_PAIRED_ROWS))
        used = [False] * theirs_budget
        pairs: list[tuple[tuple, tuple, list[str]]] = []
        unpaired_mine: list[tuple] = []
        for row in mine[:mine_budget]:
            best_index, best_differing = None, None
            for index in range(theirs_budget):
                if used[index]:
                    continue
                # Nothing over `allowed` can be accepted, so a candidate may be abandoned as soon
                # as it reaches that many disagreements, or the best count found so far.
                limit = allowed + 1 if best_differing is None else len(best_differing)
                differing = self._differing_columns(row, theirs[index], limit)
                if differing is None:
                    continue
                best_index, best_differing = index, differing
                if not differing:
                    break                      # equal within tolerance: nothing can beat that
            if best_index is None:
                unpaired_mine.append(row)      # nothing left within `allowed` of it
            else:
                used[best_index] = True
                pairs.append((row, theirs[best_index], best_differing))
        unpaired_mine += list(mine[mine_budget:])
        unpaired_theirs = [row for index, row in enumerate(theirs[:theirs_budget])
                           if not used[index]] + list(theirs[theirs_budget:])
        return pairs, unpaired_mine, unpaired_theirs

    def _differing_columns(self, mine: tuple, theirs: tuple, limit: int) -> list[str] | None:
        """The columns two rows disagree in, or None once they already disagree in `limit` of them.

        "Disagree" is `_values_equal`: the contract's normalizations and the declared float and
        timestamp tolerances, exactly as the keyed diff judges a joined row.
        """
        differing: list[str] = []
        for index, column in enumerate(self.columns):
            if not self._values_equal(column, mine[index], theirs[index]):
                differing.append(column.name)
                if len(differing) >= limit:
                    return None
        return differing

    def _unpaired_side(self, side: str, rows: list[tuple]) -> dict:
        """The `_row_presence_cluster` input for rows no pairing claimed.

        `all_null` is computed here rather than in SQL because which rows stayed unpaired is a
        Python decision; the rule it feeds is the keyed path's own.
        """
        all_null = [column.name for index, column in enumerate(self.columns)
                    if all(row[index] is None for row in rows)] if rows else []
        return {"side": side, "count": len(rows), "rows": rows[:self.sample_rows],
                "all_null": all_null}

    def _multiset_diff(self) -> _Diff:
        """The comparison when the contract declares no keys (program spec §9, plan task 7b).

        The counts stay a SQL multiset difference — exact, and unaffected by anything below. What
        is added is that the surplus rows themselves are fetched and paired by nearest match, so
        the same classification the keyed path runs can say what the difference *is*: a pair that
        agrees within tolerance is a match and leaves the report, a pair that disagrees goes
        through `_value_clusters`, and a row no pair claimed goes through `_row_presence_cluster`.
        """
        only_expected, only_actual = self._multiset_counts()
        diff = _Diff(set_diff={"only_expected": only_expected, "only_actual": only_actual},
                     keyless=True)
        if not (only_expected or only_actual):
            return diff
        # A pull cut short has not looked at every difference, exactly as on the keyed path.
        diff.truncated = max(only_expected, only_actual) > self.max_diff_rows
        pairs, unpaired_expected, unpaired_actual = self._pair_surplus(
            self._surplus_rows(self.expected, self.actual, min(only_expected, self.max_diff_rows)),
            self._surplus_rows(self.actual, self.expected, min(only_actual, self.max_diff_rows)))

        # A pairing under which two rows agree within tolerance is a witness that those two copies
        # are the same row: in a multiset that removes one copy from each side's surplus.
        matched = sum(1 for _, _, differing in pairs if not differing)
        if matched:
            diff.set_diff = {"only_expected": only_expected - matched,
                             "only_actual": only_actual - matched}
            self.normalizations_applied.append(
                f"no keys: {matched} row(s) matched within tolerance by nearest match")

        index_of = {column.name: index for index, column in enumerate(self.columns)}
        mismatching = [{"key": {},          # no keys: a paired row has nothing to be named by
                        "values": {name: (mine[index_of[name]], theirs[index_of[name]])
                                   for name in differing}}
                       for mine, theirs, differing in pairs if differing]
        per_column: dict[str, int] = {}
        for row in mismatching:
            for name in row["values"]:
                per_column[name] = per_column.get(name, 0) + 1
        diff.column_mismatches = dict(sorted(per_column.items()))
        diff.clusters.extend(self._value_clusters(mismatching, paired_by="nearest_match"))
        diff.clusters.extend(
            cluster for cluster
            in (self._row_presence_cluster(self._unpaired_side("expected", unpaired_expected)),
                self._row_presence_cluster(self._unpaired_side("actual", unpaired_actual)))
            if cluster)
        return diff

    # --- classification ---

    def _value_clusters(self, mismatching: list[dict], *,
                        paired_by: str | None = None) -> list[dict]:
        """The clusters for rows whose values disagree.

        `paired_by` is set only by the keyless path, where the two rows of a mismatch were put
        together by a heuristic rather than joined on a key: every cluster then says so, and
        declares that its rows come off both sides of the multiset difference.
        """
        if not mismatching:
            return []
        by_name = {column.name: column for column in self.columns}
        names = sorted({name for row in mismatching for name in row["values"]})
        shared: dict[str, Any] = {"paired_by": paired_by} if paired_by else {}
        if paired_by:
            # Each pair is one surplus row from each side, so these clusters answer for both.
            shared["sides"] = ("expected", "actual")
            shared["note"] = "no keys: rows paired by nearest match"

        if set(names) <= self.order_dependent \
                and all(self._column_multiset_equal(by_name[name]) for name in names):
            # Every disagreeing column is one the contract calls order-dependent and each still
            # holds the same bag of values: the rows were numbered in a different order.
            return [_cluster("ORDERING", names, len(mismatching),
                             self._examples(mismatching, names), scope="columns",
                             suspect=self._suspect_for_columns(names), **shared)]

        classified = []
        for name in names:
            column = by_name[name]
            pairs = [(_normalize(row["values"][name][0], column.ops),
                      _normalize(row["values"][name][1], column.ops))
                     for row in mismatching if name in row["values"]]
            cluster_class, hint = self._classify(column, pairs)
            classified.append((cluster_class, hint, self._suspect_for_columns([name]), name))

        clusters = []
        for cluster_class, hint, suspect in sorted({group[:3] for group in classified},
                                                   key=lambda group: (CLASS_ORDER.index(group[0]),
                                                                      group[1] or "", group[2] or "")):
            # Columns that fail the same way in the same place are one diff, not several.
            grouped = sorted(name for *group, name in classified
                             if tuple(group) == (cluster_class, hint, suspect))
            rows = [row for row in mismatching if not row["values"].keys().isdisjoint(grouped)]
            clusters.append(_cluster(cluster_class, grouped, len(rows),
                                     self._examples(rows, grouped), suspect=suspect,
                                     scope="columns", hint=hint, **shared))
        return clusters

    def _classify(self, column: _Column, pairs: list[tuple[Any, Any]]) -> tuple[str, str | None]:
        """The first classification rule that holds for *every* mismatch of this column."""
        if all((mine is None) != (theirs is None) for mine, theirs in pairs):
            return "NULL_SEMANTICS", None
        if column.family in _NUMERIC_FAMILIES and all(
                _is_number(mine) and _is_number(theirs)
                and _abs_difference(mine, theirs) <= self.rounding_abs
                for mine, theirs in pairs):
            return "ROUNDING", None
        if column.family == "string" and all(_is_truncation(mine, theirs) for mine, theirs in pairs):
            return "TRUNCATION", None
        if all(_same_value_other_notation(mine, theirs) for mine, theirs in pairs):
            return "TYPE", None
        return "LOGIC", _hint(pairs)

    def _row_presence_cluster(self, side: dict) -> dict | None:
        """Rows that exist on one side only, and the columns (if any) that explain them."""
        if not side["count"]:
            return None
        # A column that is NULL in every missing row only explains them if it is not simply NULL
        # everywhere in the golden data.
        columns = sorted(name for name in side["all_null"]
                         if self.expected_counts[name]["nulls"] < self.expected_rows)
        examples = [{"key": self._key_of(row),
                     "expected": _row_dict(self.columns, row) if side["side"] == "expected" else None,
                     "actual": _row_dict(self.columns, row) if side["side"] == "actual" else None}
                    for row in side["rows"]]
        return _cluster("NULL_SEMANTICS" if columns else "LOGIC", columns, side["count"], examples,
                        suspect=self._suspect_for_rows(), scope="rows", sides=(side["side"],),
                        note=f"rows only in {side['side']}")

    def _column_multiset_equal(self, column: _Column) -> bool:
        """Does this column hold the same bag of values on both sides, only in another order?"""
        reference = self._ref(_ONE_SIDE, column)
        return int(self.backend.query(
            f"WITH EXPECTED_GROUPS AS (SELECT {reference} AS V, COUNT(*) AS GROUP_ROWS "
            f"FROM {self.expected} {_ONE_SIDE} GROUP BY {reference}), "
            f"ACTUAL_GROUPS AS (SELECT {reference} AS V, COUNT(*) AS GROUP_ROWS "
            f"FROM {self.actual} {_ONE_SIDE} GROUP BY {reference}) "
            f"SELECT COALESCE(SUM(ABS(COALESCE(EXPECTED_GROUPS.GROUP_ROWS, 0) "
            f"- COALESCE(ACTUAL_GROUPS.GROUP_ROWS, 0))), 0) "
            f"FROM EXPECTED_GROUPS FULL OUTER JOIN ACTUAL_GROUPS "
            f"ON NOT (EXPECTED_GROUPS.V IS DISTINCT FROM ACTUAL_GROUPS.V)")[1][0][0]) == 0

    # --- suspect CTE ---

    def _suspect_for_columns(self, names: Sequence[str]) -> str | None:
        """The last tool in topological order whose config writes one of these columns."""
        if not self.segment_dag:
            return None
        wanted = {str(name).upper() for name in names}
        for node in reversed(self._topological_nodes()):
            if _written_columns(node) & wanted:
                return _cte_name(node)
        return None

    def _suspect_for_rows(self) -> str | None:
        """The last tool in topological order that can add or drop rows."""
        if not self.segment_dag:
            return None
        for node in reversed(self._topological_nodes()):
            if node.get("type") in _ROW_NODE_TYPES:
                return _cte_name(node)
        return None

    def _topological_nodes(self) -> list[dict]:
        if self._topological is None:
            self._topological = _topological_order(self.segment_dag or {})
        return self._topological

    # --- shared helpers ---

    def _examples(self, rows: list[dict], names: Sequence[str]) -> list[dict]:
        return [{"key": row["key"],
                 "expected": {name: _jsonable(row["values"][name][0])
                              for name in names if name in row["values"]},
                 "actual": {name: _jsonable(row["values"][name][1])
                            for name in names if name in row["values"]}}
                for row in rows[:self.sample_rows]]

    def _key_of(self, row: Sequence[Any]) -> dict:
        return {key.name: _jsonable(row[i]) for i, key in enumerate(self.keys)}

    def _ref(self, alias: str, column: _Column) -> str:
        """`ALIAS.COL`, wrapped in whatever normalizations the contract declared for it."""
        reference = f"{alias}.{column.sql}"
        for operation in column.ops:
            reference = f"{operation.upper()}({reference})"
        return reference

    def _tolerance(self, column: _Column) -> tuple[float | None, float]:
        """(absolute, relative) tolerance, or (None, _) when the column has to match exactly."""
        override = self.column_tolerances.get(column.name) or {}
        relative = float(override.get("float_rel", self.tolerances.get("float_rel", 0.0)))
        if "float_abs" in override:
            return float(override["float_abs"]), relative
        if column.family == "float":
            return float(self.tolerances.get("float_abs", 0.0)), relative
        return None, relative            # NUMBER is exact unless the contract loosens it

    def _values_equal(self, column: _Column, mine: Any, theirs: Any) -> bool:
        mine, theirs = _normalize(mine, column.ops), _normalize(theirs, column.ops)
        if mine is None or theirs is None:
            return mine is None and theirs is None
        if isinstance(mine, (dt.datetime, dt.time)) or isinstance(theirs, (dt.datetime, dt.time)):
            return (_truncate_time(mine, self.timestamp_digits)
                    == _truncate_time(theirs, self.timestamp_digits))
        if _is_number(mine) and _is_number(theirs):
            if mine == theirs:           # also settles -0.0 == 0.0 and Decimal("1") == 1.0
                return True
            absolute, relative = self._tolerance(column)
            if absolute is None:
                return False
            threshold = max(absolute, relative * max(abs(float(mine)), abs(float(theirs))))
            return _abs_difference(mine, theirs) <= threshold
        return mine == theirs


# --- normalizations and classification predicates ---------------------------------------------


def _parse_normalizations(contract: dict,
                          columns: set[str]) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    """`["trim:NAME"]` → ({"NAME": ("trim",)}, ["trim:NAME"]), keeping only compared columns."""
    operations: dict[str, set[str]] = {}
    applied: list[str] = []
    for entry in contract.get("normalizations") or []:
        operation, _, name = str(entry).partition(":")
        operation, name = operation.strip().lower(), name.strip().upper()
        if operation not in _NORMALIZATION_OPS or not name:
            raise ValueError(f"contract normalization {entry!r} is not 'trim:COL' or 'upper:COL' "
                             f"(known operations: {list(_NORMALIZATION_OPS)})")
        if name not in columns:          # declared for some other output's column
            continue
        operations.setdefault(name, set()).add(operation)
        if str(entry) not in applied:
            applied.append(str(entry))
    ordered = {name: tuple(op for op in _NORMALIZATION_OPS if op in chosen)
               for name, chosen in operations.items()}
    return ordered, applied


def _normalize(value: Any, operations: Sequence[str]) -> Any:
    if not operations or not isinstance(value, str):
        return value
    for operation in operations:
        # SQL TRIM removes spaces only, so Python must not strip tabs and newlines either.
        value = value.strip(" ") if operation == "trim" else value.upper()
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _as_magnitude(value: Any) -> float:
    """A `SUM(ABS(col))` as a float: how much magnitude the rows carried, 0.0 when they held none."""
    if value is None or not _is_number(value):
        return 0.0
    magnitude = abs(float(value))
    return magnitude if math.isfinite(magnitude) else 0.0


def _abs_difference(mine: Any, theirs: Any) -> Decimal | float:
    """|e - a|, exactly when both values are decimal-representable.

    Two exact cents apart is the whole point of `rounding.abs = 0.01`, and computing
    `250.02 - 250.01` in binary floating point gives 0.010000000000019, which is over the
    threshold. Decimal arithmetic keeps the boundary where the tolerance says it is.
    """
    first, second = _as_decimal(mine), _as_decimal(theirs)
    if first is not None and second is not None:
        return abs(first - second)
    return abs(float(mine) - float(theirs))


def _truncate_time(value: Any, digits: int) -> Any:
    if not isinstance(value, (dt.datetime, dt.time)):
        return value
    factor = 10 ** (6 - digits)
    return value.replace(microsecond=(value.microsecond // factor) * factor)


def _as_decimal(value: Any) -> Decimal | None:
    """The value as an exact Decimal, or None when it is not a plain finite number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value)) if math.isfinite(value) else None
    if isinstance(value, str) and _NUMBER_TEXT.fullmatch(value.strip()):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    return None


def _as_date_and_time(value: Any) -> tuple[dt.date, dt.time] | None:
    """(date, time) for a date or timestamp value, however it happens to be spelled."""
    if isinstance(value, dt.datetime):
        return value.date(), value.time()
    if isinstance(value, dt.date):
        return value, dt.time(0, 0)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return parsed.date(), parsed.time()
    return None


def _is_date_only(value: Any) -> bool:
    if isinstance(value, dt.datetime):
        return False
    if isinstance(value, dt.date):
        return True
    return isinstance(value, str) and len(value.strip()) == 10


def _is_truncation(mine: Any, theirs: Any) -> bool:
    """One string cut short: a proper prefix of the other, losing something that is not blank.

    Losing only trailing blanks is a whitespace difference, not a truncation, and is left to
    `LOGIC`'s `whitespace_only` hint.
    """
    if not isinstance(mine, str) or not isinstance(theirs, str):
        return False
    shorter, longer = (mine, theirs) if len(mine) < len(theirs) else (theirs, mine)
    return (len(shorter) < len(longer) and longer.startswith(shorter)
            and longer[len(shorter):].strip() != "")


def _same_value_other_notation(mine: Any, theirs: Any) -> bool:
    """The same value written as another type: a number as text, or a date as midnight."""
    if mine is None or theirs is None:
        return False
    if isinstance(mine, str) or isinstance(theirs, str):
        first, second = _as_decimal(mine), _as_decimal(theirs)
        if first is not None and second is not None and first == second:
            return True
    first_stamp, second_stamp = _as_date_and_time(mine), _as_date_and_time(theirs)
    if first_stamp is None or second_stamp is None:
        return False
    midnight = dt.time(0, 0)
    return (first_stamp[0] == second_stamp[0] and first_stamp[1] == second_stamp[1] == midnight
            and _is_date_only(mine) != _is_date_only(theirs))


def _hint(pairs: list[tuple[Any, Any]]) -> str | None:
    """`whitespace_only` / `case_only` when that is the whole of the difference."""
    if not all(isinstance(mine, str) and isinstance(theirs, str) for mine, theirs in pairs):
        return None
    if all(_WHITESPACE.sub("", mine) == _WHITESPACE.sub("", theirs) for mine, theirs in pairs):
        return "whitespace_only"
    if all(mine.lower() == theirs.lower() for mine, theirs in pairs):
        return "case_only"
    return None


# --- report shapes ----------------------------------------------------------------------------


def _cluster(cluster_class: str, columns: Sequence[str], count: int, example_rows: list[dict], *,
             suspect: str | None, scope: str, sides: Sequence[str] = (), hint: str | None = None,
             note: str | None = None, paired_by: str | None = None) -> dict:
    """One diff cluster.

    `scope` says what the cluster can account for, and is reported so a reader can check the
    verdict for themselves: `"schema"`, `"rows"` (rows on one side only, or a duplicated key),
    `"columns"` (these columns' values disagree) or `"synthetic"` (this cluster *is* a failing
    check that nothing else accounted for). `sides` names which side's rows the cluster is about;
    it stays internal. `paired_by` is reported, and says the two rows behind each mismatch were
    put together by that heuristic rather than joined on a key.
    """
    cluster = {"class": cluster_class, "columns": list(columns), "count": int(count),
               "example_rows": example_rows, "suspect_cte": suspect, "scope": scope,
               "_sides": tuple(sides)}
    if hint:
        cluster["hint"] = hint
    if note:
        cluster["note"] = note
    if paired_by:
        cluster["paired_by"] = paired_by
    return cluster


def _sort_clusters(clusters: list[dict]) -> list[dict]:
    return sorted(clusters, key=lambda cluster: (CLASS_ORDER.index(cluster["class"]),
                                                 cluster["columns"], cluster.get("note") or ""))


def _row_dict(columns: Sequence[_Column], row: Sequence[Any]) -> dict:
    return {column.name: _jsonable(row[i]) for i, column in enumerate(columns)}


def _ordinals(count: int) -> str:
    """"1, 2, 3" — ordering by position keeps every LIMIT deterministic without naming columns."""
    return ", ".join(str(i + 1) for i in range(count))


def _base_type(sql_type: str) -> str:
    """"DECIMAL(19,2)" → "DECIMAL": the type without its width, for types no family covers."""
    return re.sub(r"\(.*", "", str(sql_type)).strip().upper()


def _identifier(name: str) -> str:
    """Bare wherever it can be: DuckDB matches an unquoted name case-insensitively, which is how
    a Snowflake-upper-cased contract name still finds a column DuckDB wrote in mixed case."""
    return name if _SAFE_IDENTIFIER.fullmatch(name) else '"' + name.replace('"', '""') + '"'


def _jsonable(value: Any) -> Any:
    """A SQL value as something `json.dumps` accepts, without losing what it said."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        if value == value.to_integral_value():
            return int(value)
        as_float = float(value)
        return as_float if Decimal(repr(as_float)) == value else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


# --- suspect CTE from the segment DAG ----------------------------------------------------------


def _tool_sort_key(tool_id: str) -> tuple[int, int, str]:
    return (0, int(tool_id), "") if tool_id.isdigit() else (1, 0, tool_id)


def _topological_order(dag: dict) -> list[dict]:
    """The segment's nodes in topological order, ties broken by tool id (plan contract C7)."""
    by_id = {str(node.get("tool_id")): node for node in dag.get("nodes") or []}
    successors: dict[str, set[str]] = {tool_id: set() for tool_id in by_id}
    indegree = {tool_id: 0 for tool_id in by_id}
    for edge in dag.get("edges") or []:
        source, destination = str(edge.get("src")), str(edge.get("dst"))
        if source in by_id and destination in by_id and destination not in successors[source]:
            successors[source].add(destination)
            indegree[destination] += 1

    ready = sorted((tool_id for tool_id, degree in indegree.items() if not degree),
                   key=_tool_sort_key)
    order: list[dict] = []
    while ready:
        tool_id = ready.pop(0)
        order.append(by_id[tool_id])
        for destination in sorted(successors[tool_id], key=_tool_sort_key):
            indegree[destination] -= 1
            if not indegree[destination]:
                ready.append(destination)
        ready.sort(key=_tool_sort_key)
    # A cycle would leave nodes unvisited; keep them, in tool id order, rather than losing them.
    seen = {str(node.get("tool_id")) for node in order}
    order.extend(by_id[tool_id] for tool_id in sorted(set(by_id) - seen, key=_tool_sort_key))
    return order


def _written_columns(node: dict) -> set[str]:
    """The columns a tool's config says it writes (dag contract §3), upper-cased."""
    config = node.get("config") or {}
    node_type = node.get("type")
    names: list[Any] = []
    if node_type == "formula":
        names += [formula.get("field") for formula in config.get("formulas") or []]
    elif node_type in ("summarize", "select"):
        names += [field_.get("rename") for field_ in config.get("fields") or []]
    elif node_type in ("multi_row_formula", "record_id"):
        names.append(config.get("field"))
    elif node_type == "regex":
        names += [field_.get("field") for field_ in config.get("output_fields") or []]
        names += [config.get("match_field"), config.get("field")]
    elif node_type == "datetime":
        names.append(config.get("out_field"))
    elif node_type == "cross_tab":
        # Header columns are data-dependent; the frozen list for translation is meta.Output.
        group_by = {str(name).upper() for name in config.get("group_by") or []}
        headers = [field_.get("name") for field_ in (node.get("meta") or {}).get("Output") or []
                   if str(field_.get("name")).upper() not in group_by]
        names += headers or [config.get("header_field")]
    elif node_type == "transpose":
        names += ["Name", "Value"]      # transpose always writes these two (dag contract §3)
    return {str(name).upper() for name in names if name}


def _cte_name(node: dict) -> str:
    return f"t{node.get('tool_id')}_{node.get('type')}"


# --- CLI ----------------------------------------------------------------------------------------


def _load_expected(backend, expected: str, root: Path) -> str:
    """A `.csv` expected file becomes a sandbox table; a table name is used as given."""
    if not expected.lower().endswith(".csv"):
        return expected
    backend.load_table(EXPECTED_TABLE, typed_csv.read_table(root / expected))
    return EXPECTED_TABLE


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expected", required=True, help="golden typed CSV file, or a sandbox table")
    parser.add_argument("--actual", required=True, help="the table the translated procedure wrote")
    parser.add_argument("--contract", required=True, help="segments/<seg>/contract.json")
    parser.add_argument("--out", required=True, help="where to write validation.json")
    parser.add_argument("--stream", help="which contract outputs[] entry to compare (default: output)")
    parser.add_argument("--tolerances", help="mappings/global.yaml: tolerances + accepted_diff_classes")
    parser.add_argument("--manifest", help="manifest.json, for its accepted_diffs approvals")
    parser.add_argument("--dag", help="segments/<seg>/dag.json, so a diff can name a suspect CTE")
    parser.add_argument("--db", help="DuckDB sandbox file (default: a fresh in-memory one)")
    parser.add_argument("--golden-set", help="golden set name, recorded in the report")
    parser.add_argument("--sample-rows", type=int, default=5, help="example rows per diff cluster")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    root = Repo(args.root).root
    backend = None
    try:
        tolerances, accepted_classes = DEFAULT_TOLERANCES, ()
        if args.tolerances:
            settings = read_yaml(root / args.tolerances) or {}
            tolerances = settings.get("tolerances") or DEFAULT_TOLERANCES
            accepted_classes = settings.get("accepted_diff_classes") or ()
        approvals = ()
        if args.manifest:
            approvals = read_json(root / args.manifest).get("accepted_diffs") or ()
        contract = read_json(root / args.contract)
        segment_dag = read_json(root / args.dag) if args.dag else None

        backend = DuckDBBackend(args.db or ":memory:")
        report = compare(backend, _load_expected(backend, args.expected, root), args.actual,
                         contract, tolerances, output=select_output(contract, args.stream),
                         accepted_classes=accepted_classes, approvals=approvals,
                         segment_dag=segment_dag, golden_set=args.golden_set,
                         sample_rows=args.sample_rows)
        write_json(root / args.out, report)
    except Exception as exc:                # noqa: BLE001 — exit 2 is "anything went wrong"
        print(f"cannot compare {args.expected} with {args.actual}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    finally:
        if backend is not None:
            backend.close()

    summary = ", ".join(f"{cluster['class']}({', '.join(cluster['columns']) or 'rows'})"
                        for cluster in report["diff_clusters"])
    print(f"{report['verdict']}: {len(report['diff_clusters'])} diff clusters"
          f"{': ' + summary if summary else ''}")
    return 0 if report["verdict"].startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
