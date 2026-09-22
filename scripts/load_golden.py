"""Golden CSV → sandbox tables, and the call arguments a segment procedure needs (contracts C2/C3).

    python scripts/load_golden.py <wf_id> <golden_set> [--root .] [--db PATH]

A golden set is loaded the way a real run would see it:

* each mapped source's `golden/inputs/<set>/<tool_id>.csv` becomes `MIG_GOLDEN.<WF>_<SET>_IN_<id>`;
* a view schema `MIGDB.MIG_GOLDEN_<WF>_<SET>` exposes each source under its *logical* name, which
  is the only name the procedure knows — production swaps this schema for the one
  `gen_source_views.py` writes, and the procedure body does not change;
* `golden/targets_before/<set>/<LOGICAL>.csv` becomes `MIGDB.MIG_WORK.<LOGICAL>`, the starting
  state an update/insert or append output merges into. Outputs that overwrite have no such file.

A source with no `logical` name is an error: the pipeline never guesses a table name.
"""
from __future__ import annotations

import argparse
import json
import sys

from lib.backend import SANDBOX_DB, get_backend
from lib.io import read_yaml
from lib.paths import Repo, add_root_arg, wf_token
from lib.typed_csv import read_table

GOLDEN_SCHEMA = "MIG_GOLDEN"
WORK_SCHEMA = "MIG_WORK"


def golden_view_schema(wf_id: str, golden_set: str) -> str:
    """The view schema a procedure's SRC_SCHEMA points at for one golden set."""
    return f"{GOLDEN_SCHEMA}_{wf_token(wf_id)}_{golden_set.upper()}"


def load_set(backend, repo: Repo, wf_id: str, golden_set: str) -> dict:
    """Loads one golden set and returns {"args": …, "loaded": [fqn…]} for `run_proc`."""
    mappings = read_yaml(repo.wf(wf_id, "intake", "mappings.yaml")) or {}
    view_schema = golden_view_schema(wf_id, golden_set)
    prefix = f"{wf_token(wf_id)}_{golden_set.upper()}"
    loaded: list[str] = []

    for key, source in (mappings.get("sources") or {}).items():
        tool_ids = [str(tool_id) for tool_id in source.get("tool_ids") or []]
        logical = _logical(source, key, tool_ids, "source")
        input_tables = []
        for tool_id in tool_ids:
            path = repo.wf(wf_id, "golden", "inputs", golden_set, f"{tool_id}.csv")
            if not path.exists():
                raise FileNotFoundError(
                    f"golden input for tool {tool_id} ({key}) of {wf_id}/{golden_set} is missing: {path}")
            fqn = f"{GOLDEN_SCHEMA}.{prefix}_IN_{tool_id}"
            backend.load_table(fqn, read_table(path))
            loaded.append(fqn)
            input_tables.append(fqn)
        if input_tables:
            # Several tools reading the same file share one logical name and one golden file's
            # worth of data, so the view points at the first of them.
            view_fqn = f"{SANDBOX_DB}.{view_schema}.{logical}"
            backend.create_view(view_fqn, input_tables[0])
            loaded.append(view_fqn)

    for key, output in (mappings.get("outputs") or {}).items():
        tool_ids = [str(tool_id) for tool_id in output.get("tool_ids") or []]
        logical = _logical(output, key, tool_ids, "output")
        path = repo.wf(wf_id, "golden", "targets_before", golden_set, f"{logical}.csv")
        if not path.exists():
            continue        # an overwrite output starts from nothing (contract C2)
        fqn = f"{SANDBOX_DB}.{WORK_SCHEMA}.{logical}"
        backend.load_table(fqn, read_table(path))
        loaded.append(fqn)

    return {"args": {"SRC_DB": SANDBOX_DB, "SRC_SCHEMA": view_schema,
                     "TGT_DB": SANDBOX_DB, "TGT_SCHEMA": WORK_SCHEMA},
            "loaded": loaded}


def load_intermediate(backend, repo: Repo, wf_id: str, seg: str, golden_set: str, stream: str,
                      table_fqn: str) -> None:
    """Loads `golden/intermediates/<seg>/<set>/<stream>.csv` into `table_fqn`.

    This is how a segment that consumes an upstream segment's output is tested in isolation: the
    upstream table is filled from golden data rather than by running the upstream procedure.
    """
    path = repo.wf(wf_id, "golden", "intermediates", seg, golden_set, f"{stream}.csv")
    if not path.exists():
        raise FileNotFoundError(
            f"golden intermediate {stream} of {wf_id}/{seg}/{golden_set} is missing: {path}")
    backend.load_table(table_fqn, read_table(path))


def _logical(entry: dict, key: str, tool_ids: list[str], kind: str) -> str:
    logical = entry.get("logical")
    if not logical:
        where = f"tool {', '.join(tool_ids)}" if tool_ids else "no tool id recorded"
        raise ValueError(f"{kind} {key!r} ({where}) has no `logical:` name in intake/mappings.yaml "
                         f"(plan contract C6); nothing can be guessed for it")
    return str(logical)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("golden_set", help="golden set name, e.g. normal")
    parser.add_argument("--db", help="DuckDB file (default: workflows/<wf_id>/.sandbox.duckdb)")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    repo = Repo(args.root)
    # Usage problems are settled before anything is created, so a typo leaves no sandbox behind.
    mappings = repo.wf(args.wf_id, "intake", "mappings.yaml")
    if not mappings.exists():
        parser.error(f"{args.wf_id} has no intake/mappings.yaml to load against: {mappings}")
    golden = repo.wf(args.wf_id, "golden", "inputs", args.golden_set)
    if not golden.is_dir():
        parser.error(f"{args.wf_id} has no golden set {args.golden_set!r}: {golden}")

    backend = None
    try:
        backend = get_backend("duckdb", db_path=args.db or str(repo.wf(args.wf_id, ".sandbox.duckdb")))
        loaded = load_set(backend, repo, args.wf_id, args.golden_set)
    except ValueError as exc:       # domain failure: the mappings do not satisfy contract C6
        print(f"{args.wf_id}/{args.golden_set}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:        # a golden file that is not there, an unwritable db, anything else
        print(f"{args.wf_id}/{args.golden_set}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if backend is not None:
            backend.close()
    print(json.dumps(loaded, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
