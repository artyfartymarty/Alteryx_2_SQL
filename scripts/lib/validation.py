"""Validation-report shaping shared by every segment validator (program spec §9).

`validate_segment.py` (SQL procedures, run through `DuckDBBackend`) and `validate_snowpark.py`
(Snowpark Python procedures, run through the Snowpark Local Testing Framework) drive a procedure
completely differently and compare its output through different machinery, but everything about
*shaping* the result into `validation.json` is identical between them: how the several
`compare.py` reports for one segment's outputs combine into one report for a golden set, how a
declared output table the procedure never created becomes its own kind of FAIL report, how a
`ProcError`/exception while running the procedure becomes a domain FAIL, how several golden sets'
own reports aggregate into one top-level verdict (findings K1/K2/C3/C4/I5), and how a report on
disk always belongs to the most recent invocation. That shared machinery lives here, moved
verbatim out of `validate_segment.py` and made public -- `validate_segment.py` re-exports every
name below under its old private name (`_combine = combine`, …) so its own tests keep working
unchanged.

The workflow-level chain report (`validate_workflow.py`, and `validate_dbt.py` from its own run;
plan Task W1, ruling R-W1) is shaped here too: `chain_report` turns one golden set's chain run --
every output of every segment judged in chain order -- into one report with `boundaries`,
`finals`, `divergence_kind` and `first_divergence`, and `write_workflow_reports` /
`clear_stale_workflow_reports` are `write_reports` / `clear_stale_reports` for
`workflows/<wf>/validation_workflow*.json`, sharing the same tail.

Every validator's engine choice is shared here too (plan Task P2): `add_backend_args` gives each CLI
the same `--backend duckdb|snowflake`, `--connection`, `--sandbox-database`; `snowflake_target`
turns them into nothing (the DuckDB default) or a checked, not yet connected `SnowflakeSandbox`;
`snapshot` / `diverging_tables` compare two runs that share one sandbox database; `error_text` /
`print_crash` redact what a CLI prints on the Snowflake path.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Sequence

import compare
from .backend import SANDBOX_DB, ident, local_name, qualified
from .io import write_json
from .paths import Repo

#: Where a golden CSV / actual-table snapshot is loaded before compare() runs, one table per
#: output (compare.py's own convention is a single `MIG_COMPARE.EXPECTED`; a segment can have
#: several outputs to compare in the same backend, so each gets its own numbered table).
EXPECTED_SCHEMA = "MIG_COMPARE"

#: `load_golden.WORK_SCHEMA`'s value, duplicated here (rather than imported) the same way
#: `validate_snowpark.py` duplicates it too: `load_golden.py` imports `lib.backend` at its own
#: top level, so importing `load_golden` back from here -- a `lib` module -- would be a genuine
#: import cycle the first time either module loads first.
_WORK_SCHEMA = "MIG_WORK"

VERDICT_SEVERITY = {"PASS": 0, "PASS_WITH_ACCEPTED_DIFF": 1, "FAIL": 2}


# --- which engine a validator runs on (plan Task P2) -----------------------------------------------
#
# `duckdb` (the default) is the local double every test and every agent uses. `snowflake` is a real
# account through a NAMED connection, run by a human: it is selected only by `--backend snowflake`,
# and nothing Snowflake is imported or constructed unless it is (`lib.snowflake_conn` and
# `lib.snowflake_sandbox` are imported lazily, here and in each validator).

BACKENDS = ("duckdb", "snowflake")


def add_backend_args(parser: argparse.ArgumentParser) -> None:
    """`--backend`, `--connection`, `--sandbox-database`: the same three options on every validator.
    There is deliberately no option for a password, token or key -- argparse refuses one."""
    parser.add_argument("--backend", choices=BACKENDS, default="duckdb",
                        help="duckdb: the local double (default). snowflake: a real account through a "
                             "named connection -- run by a human, never by an agent")
    parser.add_argument("--connection", default=None, metavar="NAME",
                        help="--backend snowflake: the connections.toml entry to use "
                             "(default: $MIG_SNOWFLAKE_CONNECTION)")
    parser.add_argument("--sandbox-database", default=None, metavar="DB",
                        help="--backend snowflake: the sandbox database; its MIG_GOLDEN, MIG_WORK and "
                             "MIG_COMPARE schemas are replaced, so it must be listed in "
                             "orchestrator.config.json policy.sandboxDatabases (default: $MIG_SANDBOX_DATABASE)")


def snowflake_target(repo: Repo, backend: str, connection: str | None, sandbox_database: str | None):
    """`None` for the DuckDB double; for `snowflake`, the `SnowflakeSandbox` the run uses -- every
    name checked (the policy's `sandboxDatabases` included) and nothing connected yet. A Snowflake
    option without `--backend snowflake`, or an unknown backend, is a `ValueError` (a usage error)."""
    if backend == "duckdb":
        if connection is not None or sandbox_database is not None:
            raise ValueError("--connection and --sandbox-database only apply with --backend snowflake")
        return None
    if backend != "snowflake":
        raise ValueError(f"unknown backend {backend!r}: expected one of {', '.join(BACKENDS)}")
    from . import snowflake_sandbox  # noqa: PLC0415  (lazy: the DuckDB path never imports it)
    return snowflake_sandbox.sandbox_for(repo.root, connection, sandbox_database)


def error_text(backend: str, text: str) -> str:
    """An error message as a CLI may print it: redacted on the Snowflake path, untouched locally."""
    if backend != "snowflake":
        return text
    from .snowflake_conn import redact  # noqa: PLC0415
    return redact(text)


def print_crash(backend: str) -> None:
    """The exit-2 traceback every validator prints for an unexpected exception -- redacted on the
    Snowflake path, whose exceptions may carry connector text."""
    if backend != "snowflake":
        traceback.print_exc()
        return
    sys.stderr.write(error_text(backend, traceback.format_exc()))


def worst_verdict(verdicts: Sequence[str]) -> str:
    if not verdicts:
        return "PASS"
    return max(verdicts, key=lambda verdict: VERDICT_SEVERITY[verdict])


def expected_fqn(index: int) -> str:
    """The `MIG_COMPARE.EXPECTED_<n>` table one contract output's golden data is loaded into
    before `compare.compare()` runs (a caller loads whatever it ran the same way, conventionally
    as the matching `MIG_COMPARE.ACTUAL_<n>`)."""
    return f"{EXPECTED_SCHEMA}.EXPECTED_{index}"


# --- table/path resolution for one contract output ---------------------------------------------


def actual_table(wf_id: str, seg: str, output: dict, database: str = SANDBOX_DB) -> str:
    """The sandbox table a `contract.outputs[]` entry actually lands in (plan contracts C3/C4).

    A `work` output is whatever literal table name the contract gives (the procedure creates it
    unqualified, e.g. `MIG_WORK.WF0009_SEG_01_OUT`, in the current database). A `target` output has
    no `table` of its own -- a SQL procedure writes it through `IDENTIFIER(:<LOGICAL>_TGT)`, the name
    its `LET <LOGICAL>_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.<LOGICAL>'` builds, and a
    Snowpark one writes it to `f"{tgt_db}.{tgt_schema}.<LOGICAL>"`; both resolve to
    `<database>.MIG_WORK.<logical>`: `MIGDB.MIG_WORK.<logical>` for a local run (matching
    `load_golden`'s own `WORK_SCHEMA`), the sandbox database's `MIG_WORK` for a `--backend snowflake`
    run (plan Task P2).

    The name is spliced into SQL on both backends, so every component must be a plain identifier
    (`lib.backend.ident` / `qualified`): a `ValueError` naming the value otherwise (fix round 1, C2).
    """
    if output.get("kind") == "target":
        logical = output.get("logical")
        if not logical:
            raise ValueError(f"{wf_id}/{seg} contract output for stream {output.get('stream')!r} "
                             f"is a target with no `logical` name (plan contract C5)")
        ident(logical, f"{wf_id}/{seg} contract target logical name")
        return f"{ident(database, 'database')}.{_WORK_SCHEMA}.{logical}"
    table = output.get("table")
    if not table:
        raise ValueError(f"{wf_id}/{seg} contract output for stream {output.get('stream')!r} "
                         f"has no `table` name (plan contract C5)")
    return qualified(table, f"{wf_id}/{seg} contract work table for stream {output.get('stream')!r}")


def check_contract_names(wf_id: str, seg: str, contract: dict, database: str = SANDBOX_DB) -> None:
    """Every name a validator splices into SQL from `contract` -- each output's actual table and each
    upstream input's `table` -- checked before anything runs (fix round 1, C2). An input with a stream
    but no table keeps its own later message (C5)."""
    for output in contract.get("outputs") or []:
        actual_table(wf_id, seg, output, database)
    for entry in contract.get("inputs") or []:
        if entry.get("stream") and entry.get("table"):
            qualified(entry["table"], f"{wf_id}/{seg} contract input table for stream {entry['stream']!r}")


def golden_path(repo: Repo, wf_id: str, seg: str, golden_set: str, output: dict) -> Path:
    """`golden/outputs/<set>/<tool_id>.csv` for a `target`, `golden/intermediates/<seg>/<set>/
    <stream>.csv` for a `work` output (plan contract C2). `tool_id`/`stream` come straight from
    the contract, so they go through `Repo` -- a hostile value there raises `ValueError`."""
    if output.get("kind") == "target":
        tool_id = output.get("tool_id")
        if not tool_id:
            raise ValueError(f"{wf_id}/{seg} contract output for stream {output.get('stream')!r} "
                             f"is a target with no `tool_id` (plan contract C5)")
        return repo.wf(wf_id, "golden", "outputs", golden_set, f"{tool_id}.csv")
    stream = output.get("stream")
    if not stream:
        raise ValueError(f"{wf_id}/{seg} contract output {output!r} has no `stream`")
    return repo.wf(wf_id, "golden", "intermediates", seg, golden_set, f"{stream}.csv")


# --- combining the per-output compare() reports into one report per golden set ------------------


def missing_table_report(actual_fqn: str) -> dict:
    """The procedure never created one of its declared output tables (finding C4(a)). This is an
    *actual*-side failure -- a domain FAIL that feeds the fixer, exactly like a compile error --
    not compare.py's own `ValueError("there is no table … to compare")`, which is reserved for a
    usage error on the *expected* side. Shaped like one of `compare.compare()`'s own per-output
    reports so `combine` can merge it the same way, plus an `"error"` naming the table."""
    return {
        "verdict": "FAIL", "error": f"output table {actual_fqn} does not exist after running "
                                    f"the procedure",
        "checks": {}, "diff_clusters": [], "normalizations_applied": [], "needs_human": False,
        "truncated": False,
    }


def ordered_rows(backend, table: str) -> list[tuple]:
    """Every row of `table`, ordered by every column so two runs' outputs can be compared for
    equality as row multisets: sorting by all columns puts duplicate rows next to each other, and
    since duplicates are indistinguishable, their relative order can never affect the comparison."""
    columns = backend.table_columns(table)
    if not columns:
        return []
    order = ", ".join(str(i) for i in range(1, len(columns) + 1))
    return backend.query(f"SELECT * FROM {table} ORDER BY {order}")[1]


def snapshot(backend, tables: Sequence[str]) -> dict[str, list[tuple] | None]:
    """Every table's `ordered_rows`, `None` for a table that does not exist. The idempotency check
    takes the first run's snapshot BEFORE the second run starts: on a real account both runs share
    one sandbox database, and the second run's fresh sandbox replaces the first run's schemas."""
    return {table: (ordered_rows(backend, table) if backend.table_exists(table) else None)
            for table in dict.fromkeys(tables)}


def diverging_tables(first: dict[str, list[tuple] | None], second: dict[str, list[tuple] | None],
                     tables: Sequence[str]) -> list[str]:
    """The tables (in `tables`' order) whose two snapshots differ; a table missing from either run
    counts as diverging -- idempotency is never verified without both runs compared."""
    return [table for table in tables
            if first.get(table) is None or second.get(table) is None or first[table] != second[table]]


#: Where a view is materialised on its way to being reversed.
_MATERIALISED = f"{EXPECTED_SCHEMA}.REVERSE_MATERIALISED"


def _is_view(backend, table: str) -> bool:
    schema, name = local_name(table)
    quote = lambda text: text.replace("'", "''")  # noqa: E731
    rows = backend.query("SELECT table_type FROM information_schema.tables "
                         f"WHERE upper(table_schema) = upper('{quote(schema)}') "
                         f"AND upper(table_name) = upper('{quote(name)}')")[1]
    return bool(rows) and str(rows[0][0]).upper() == "VIEW"


def reverse_physical_order(backend, table: str) -> None:
    """Rewrites `table` with its rows in reversed physical order, keeping its column types -- how
    an idempotency re-run presents a different input order (Task W1, fix rounds 1 and 2): a
    procedure or model that depends on the order its input arrives in (none is promised by
    Snowflake) then computes something else, and the two runs differ. A VIEW (a segment may write
    its stream as one, with its own ORDER BY) has no physical rows to reverse: it is first
    materialised into a table under the same name, in the order it presents, and that table is
    reversed (fix round 3, R2). `rowid` is the DuckDB double's own physical order; it says nothing
    about the order a real account would deliver."""
    if _is_view(backend, table):
        backend.execute(f"CREATE OR REPLACE TABLE {_MATERIALISED} AS SELECT * FROM {table}")
        backend.execute(f"DROP VIEW {table}")
        backend.execute(f"CREATE TABLE {table} AS SELECT * FROM {_MATERIALISED} ORDER BY rowid DESC")
        backend.execute(f"DROP TABLE {_MATERIALISED}")
        return
    backend.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM {table} ORDER BY rowid DESC")


def combine(contract: dict, golden_set: str, output_reports: list[tuple[dict, dict]]) -> dict:
    """Merges one `compare.compare()` report (or `missing_table_report`) per contract output into
    one report for this segment and golden set. Nothing about a check or a cluster is re-derived:
    every count, verdict and classification is exactly what `compare.py` produced. Only `"stream"`
    is added, to each cluster, and a `"checks"` entry per output (keyed by stream and kind, since a
    work stream and the target it feeds share the same `stream` id, plan contract C5). When two or
    more outputs share the same stream:kind key -- e.g. one stream feeding two `target` outputs,
    ruling R-B1 -- `:<tool_id>` is appended to EVERY occurrence of that colliding key so both
    checks survive; a key that never collides is left exactly as before, so no already-committed
    report changes shape. Any per-output `"error"` (a missing actual table) is carried up too,
    joined if there is more than one."""
    verdict = worst_verdict([report["verdict"] for _, report in output_reports])
    clusters: list[dict] = []
    for output, report in output_reports:
        for cluster in report["diff_clusters"]:
            tagged = dict(cluster)
            tagged["stream"] = output.get("stream")
            clusters.append(tagged)
    clusters.sort(key=lambda cluster: (compare.CLASS_ORDER.index(cluster["class"]),
                                       cluster.get("stream") or "", cluster["columns"]))
    keys = [f"{output.get('stream')}:{output.get('kind')}" for output, _ in output_reports]
    repeated = {key for key in keys if keys.count(key) > 1}
    checks = {(f"{key}:{output.get('tool_id')}" if key in repeated else key): report["checks"]
             for key, (output, report) in zip(keys, output_reports)}
    normalizations = sorted({op for _, report in output_reports
                             for op in report["normalizations_applied"]})
    errors = [report["error"] for _, report in output_reports if report.get("error")]
    result = {
        "segment": contract.get("segment"),
        "golden_set": golden_set,
        "verdict": verdict,
        "checks": checks,
        "diff_clusters": clusters,
        "normalizations_applied": normalizations,
        "idempotent": None,             # filled in by the caller, once per segment
        "idempotency_diff": [],         # filled in by the caller, once per segment
        "runtime_ms": 0,                # filled in by the caller: wall time for the whole set
        "credits": None,                # a local run burns no Snowflake credits
        "needs_human": any(report["needs_human"] for _, report in output_reports),
        "truncated": any(report.get("truncated") for _, report in output_reports),
    }
    if errors:
        result["error"] = "; ".join(errors)
    return result


def fail_report(contract: dict, golden_set: str, error: Exception) -> dict:
    """Running the procedure raised (a SQL compile/runtime error, or a Python exception from a
    Snowpark `run()`): a domain FAIL that feeds the fixer (program spec §9), not a human
    escalation -- so `needs_human` stays false. The first run itself never completed, so no second
    run was attempted either: `idempotent` stays `None` (finding K2's documented exception -- it
    must never become `True` without both runs being compared)."""
    return {
        "segment": contract.get("segment"), "golden_set": golden_set, "verdict": "FAIL",
        "error": str(error), "checks": {}, "diff_clusters": [], "normalizations_applied": [],
        "idempotent": None, "idempotency_diff": [], "runtime_ms": 0, "credits": None,
        "needs_human": False, "truncated": False,
    }


def aggregate_sets(sets: Sequence[str], reports: dict[str, dict]) -> dict:
    """Combines every golden set's own report into the segment-level result written to
    `validation.json` (finding K1's ruling): the verdict is the *worst* across every processed
    set; the body (every other key) is copied from the first set, in processing order, whose own
    verdict equals that worst verdict; `needs_human` is true if *any* set's own report says so,
    regardless of which set supplied the body; `sets` lists every processed set's own verdict."""
    worst = worst_verdict([reports[golden_set]["verdict"] for golden_set in sets])
    chosen = next(reports[golden_set] for golden_set in sets
                 if reports[golden_set]["verdict"] == worst)
    result = dict(chosen)
    result["verdict"] = worst
    result["needs_human"] = any(reports[golden_set]["needs_human"] for golden_set in sets)
    result["sets"] = {golden_set: reports[golden_set]["verdict"] for golden_set in sets}
    return result


def clear_stale_reports(repo: Repo, wf_id: str, seg: str) -> None:
    """Deletes this segment's `validation.json` and every `validation.<set>.json` before anything
    else runs (findings C4(b)/I5): a report on disk always belongs to the most recent invocation,
    so any usage error or crash below this point leaves *no* report at all, and a golden set that
    is no longer requested loses its own leftover report too. Deletes only those two exact shapes
    in this one segment directory -- nothing else there is touched."""
    seg_dir = repo.seg(wf_id, seg)
    if not seg_dir.is_dir():
        return
    exact = seg_dir / "validation.json"
    if exact.is_file():
        exact.unlink()
    for path in seg_dir.glob("validation.*.json"):
        if path.is_file():
            path.unlink()


def _write_set_reports(directory: Path, stem: str, sets: Sequence[str], reports: dict[str, dict],
                       idempotent: tuple[bool | None, list[str]], extra: dict | None,
                       aggregate=aggregate_sets) -> dict:
    """The tail every validator shares: idempotency is a property of the whole run, established
    once from the first golden set and carried onto every set's report regardless of which set it
    came from -- `idempotent` is that `(is_idempotent, idempotency_diff)` pair. Stamps it onto
    every report, aggregates them, merges `extra` into the *top-level* result only -- never into a
    per-set report -- writes `<stem>.json` and `<stem>.<set>.json` for every set into `directory`,
    and returns the top-level result."""
    is_idempotent, idempotency_diff = idempotent
    for report in reports.values():
        report["idempotent"] = is_idempotent
        report["idempotency_diff"] = idempotency_diff

    result = aggregate(sets, reports)
    if extra:
        result.update(extra)

    write_json(directory / f"{stem}.json", result)
    for golden_set in sets:
        write_json(directory / f"{stem}.{golden_set}.json", reports[golden_set])
    return result


def write_reports(repo: Repo, wf_id: str, seg: str, sets: Sequence[str], reports: dict[str, dict],
                  idempotent: tuple[bool | None, list[str]], *, extra: dict | None = None) -> dict:
    """The tail shared by `validate_segment()`, `validate_snowpark()` and `validate_dbt()`: stamps
    the segment's idempotency onto every set's report, aggregates them (`aggregate_sets`), merges
    `extra` into the *top-level* result only -- so a caller like `validate_snowpark.py` can add its
    own `"target": "snowpark"` without it leaking into `validation.<set>.json` -- writes
    `segments/<seg>/validation.json` and `validation.<set>.json` for every set, and returns the
    top-level result (`_write_set_reports`)."""
    return _write_set_reports(repo.seg(wf_id, seg), "validation", sets, reports, idempotent, extra)


# --- the workflow-level chain report (plan Task W1, ruling R-W1) -----------------------------------


class ChainError(Exception):
    """A segment raised while the chain ran it on its upstream segments' actual output: a domain
    failure localised to that segment (ruling R-W1: a `boundary` divergence with `stream: null`),
    never a usage error. `validate_workflow.py` raises it; `validate_dbt.py` builds one for a
    `dbt run` that failed, naming the segment that owns the first failed model."""

    def __init__(self, segment: str, cause: Exception):
        super().__init__(f"chain stopped at {segment}: {type(cause).__name__}: {cause}")
        self.segment = segment
        self.cause = cause
        #: the chain entries judged before the segment raised (set by whoever ran the chain)
        self.entries: list[dict] = []


def _divergence(golden_set: str, entry: dict | None = None, **fields) -> dict:
    if entry is not None:
        fields = {"segment": entry["segment"], "stream": entry["stream"], "output": entry["output"]}
    return {**fields, "set": golden_set}


def chain_report(wf_id: str, golden_set: str, entries: list[dict], error: Exception | None = None, *,
                 diverging: Sequence[str] = (), rerun_error: Exception | None = None) -> dict:
    """One golden set's chain run as one report: the shared report shape (`combine`'s, with
    `segment: None` -- a workflow report belongs to no single segment) plus `workflow`,
    `boundaries` (every work stream judged: `{segment, stream, verdict}`), `finals` (every target
    judged: `{segment, stream, output, verdict}`), `divergence_kind` and `first_divergence`.

    `entries` are `{"segment", "stream", "kind": "work"|"target", "output": <logical or table>,
    "relation": <the actual table>, "report": <compare report>}` in chain order (waves, then the
    segments of a wave, then contract output order). Nothing is re-derived: every check, cluster
    and verdict is `compare.py`'s own; each cluster is tagged with its `segment` and `stream`, and
    every entry's checks are keyed `<segment>:<stream>:<kind>:<output>`.

    Ruling R-W1's classification, in chain order: the FIRST failing boundary is a `boundary`
    divergence; failing that, a segment that raised (`error`, a `ChainError` naming it) is a
    `boundary` divergence with `stream: None` (every entry judged before it passed its boundary);
    failing that, the first failing final is `chain_drift` -- every boundary was within tolerance,
    yet a final output was not. `diverging` names the relations whose rows differed between two
    runs of the chain from fresh backends (idempotency): that forces `FAIL`, and the first entry
    (chain order) writing a diverging relation is a `boundary` divergence unless a real boundary
    failure or a raise came first -- a non-deterministic segment is that segment's defect, never
    accumulated tolerance. `rerun_error` is a `ChainError` from that second run: the chain is not
    idempotent, and the divergence is a `boundary` at the raising segment (`stream: None`), placed
    in chain order after that segment's own judged outputs, rather than at whichever relation the
    unfinished second run could not produce."""
    checks: dict[str, dict] = {}
    clusters: list[dict] = []
    boundaries: list[dict] = []
    finals: list[dict] = []
    for entry in entries:
        report = entry["report"]
        checks[f"{entry['segment']}:{entry['stream']}:{entry['kind']}:{entry['output']}"] = report["checks"]
        for cluster in report["diff_clusters"]:
            clusters.append({**cluster, "segment": entry["segment"], "stream": entry["stream"]})
        if entry["kind"] == "target":
            finals.append({"segment": entry["segment"], "stream": entry["stream"], "output": entry["output"],
                           "verdict": report["verdict"]})
        else:
            boundaries.append({"segment": entry["segment"], "stream": entry["stream"], "verdict": report["verdict"]})

    unstable = set(diverging)
    # (chain position, divergence) for every boundary-kind candidate; the earliest wins.
    candidates = [(index, _divergence(golden_set, entry)) for index, entry in enumerate(entries)
                  if (entry["kind"] != "target" and entry["report"]["verdict"] == "FAIL")
                  or (rerun_error is None and entry["relation"] in unstable)]
    if rerun_error is not None:
        raised_at = getattr(rerun_error, "segment", None)
        position = max((index for index, entry in enumerate(entries) if entry["segment"] == raised_at),
                       default=len(entries)) + 0.5
        candidates.append((position, _divergence(golden_set, segment=raised_at, stream=None, output=None)))
    failing_final = next((entry for entry in entries
                          if entry["kind"] == "target" and entry["report"]["verdict"] == "FAIL"), None)
    if candidates:
        kind, first = "boundary", min(candidates, key=lambda candidate: candidate[0])[1]
    elif error is not None:
        kind, first = "boundary", _divergence(golden_set, segment=getattr(error, "segment", None),
                                              stream=None, output=None)
    elif failing_final is not None:
        kind, first = "chain_drift", _divergence(golden_set, failing_final)
    else:
        kind, first = None, None

    verdicts = [entry["report"]["verdict"] for entry in entries]
    if error is not None or unstable or rerun_error is not None:
        verdicts.append("FAIL")
    errors = [entry["report"]["error"] for entry in entries if entry["report"].get("error")]
    if error is not None:
        errors.append(str(error))
    if rerun_error is not None:
        errors.append(f"idempotency re-run: {rerun_error}")
    result = {
        "workflow": wf_id,
        "segment": None,
        "golden_set": golden_set,
        "verdict": worst_verdict(verdicts),
        "checks": checks,
        "diff_clusters": clusters,
        "normalizations_applied": sorted({op for entry in entries
                                          for op in entry["report"]["normalizations_applied"]}),
        "boundaries": boundaries,
        "finals": finals,
        "divergence_kind": kind,
        "first_divergence": first,
        "idempotent": None,             # filled in by write_workflow_reports, once per chain
        "idempotency_diff": [],
        "runtime_ms": 0,                # filled in by the caller: wall time for the whole set
        "credits": None,                # a local run burns no Snowflake credits
        "needs_human": any(entry["report"]["needs_human"] for entry in entries),
        "truncated": any(entry["report"].get("truncated") for entry in entries),
    }
    if errors:
        result["error"] = "; ".join(errors)
    return result


def _aggregate_chain(sets: Sequence[str], reports: dict[str, dict]) -> dict:
    """`aggregate_sets`, except that a `boundary` divergence in ANY golden set supplies the body
    before a `chain_drift` in an earlier one (ruling R-W1 across sets: if any boundary fails, the
    workflow's divergence is a boundary -- a fixable segment beats a tolerance question)."""
    result = aggregate_sets(sets, reports)
    if result.get("divergence_kind") != "boundary":
        boundary = next((golden_set for golden_set in sets
                         if reports[golden_set].get("divergence_kind") == "boundary"), None)
        if boundary is not None:
            result = {**reports[boundary], "verdict": result["verdict"],
                      "needs_human": result["needs_human"], "sets": result["sets"]}
    return result


def write_workflow_reports(repo: Repo, wf_id: str, sets: Sequence[str], reports: dict[str, dict],
                           idempotent: tuple[bool | None, list[str]], *, extra: dict | None = None) -> dict:
    """`write_reports`' tail for a workflow's chain: `workflows/<wf>/validation_workflow.json` (the
    worst verdict across sets, its body from `_aggregate_chain`) and `validation_workflow.<set>.json`
    for every set, the chain's idempotency stamped on each."""
    return _write_set_reports(repo.wf(wf_id), "validation_workflow", sets, reports, idempotent, extra,
                              aggregate=_aggregate_chain)


def clear_stale_workflow_reports(repo: Repo, wf_id: str) -> None:
    """Deletes `validation_workflow.json` and every `validation_workflow.<set>.json` at the
    workflow root before anything else runs, so a usage error or crash leaves no chain report at
    all and a golden set no longer requested loses its own. Touches only those two exact shapes."""
    wf_dir = repo.wf(wf_id)
    if not wf_dir.is_dir():
        return
    exact = wf_dir / "validation_workflow.json"
    if exact.is_file():
        exact.unlink()
    for path in wf_dir.glob("validation_workflow.*.json"):
        if path.is_file():
            path.unlink()
