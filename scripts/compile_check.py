"""Does a segment's procedure compile and run against tables shaped like its contract?

    python scripts/compile_check.py <wf_id> <seg> [--root .]

This is the cheap gate before parity: it catches a procedure that is not valid Snowflake SQL, that
reads a table nobody mapped, or that produces different columns from the ones `contract.json`
promises — without any golden data. The run happens on an empty in-memory sandbox, so it says
nothing about values; `compare.py` does that.

Six checks, in order:

0. the signature is contract C4's: `MIG_WORK.<WF>_<SEG>` for this workflow and segment, exactly
   `(SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID)` in that order, and `EXECUTE AS CALLER` —
   a procedure that runs with owner's rights cannot set the session parameters these procedures
   rely on. Nothing else is checked when the signature is wrong: the call would not bind.
1. the text is the supported procedure shape (plan contract C4), including the one table-reference
   form contract C4 allows (Task C4V), as two named checks: `c4:let_form` -- every `LET` is
   `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>'` or its `TGT` twin
   (`<LOGICAL>_TGT`, `TGT_DB`, `TGT_SCHEMA`), the arguments named WITHOUT a colon (a colon there is
   refused by name: Snowflake's Scripting documentation uses the colon to bind a variable inside a
   SQL statement, not in an expression) -- and `c4:identifier_expression` -- the argument of every
   `IDENTIFIER(…)` is one `:<VAR>` a `LET` of this procedure declares (with the colon: it is inside
   a SQL statement), one string literal or one session variable, never an expression. Snowflake
   documents `IDENTIFIER( { string_literal | session_variable | bind_variable |
   snowflake_scripting_variable } )`, a single value; the concatenation inside `IDENTIFIER(…)` these
   procedures once used is not in that grammar. The documented form has not run on Snowflake
   either: the first real-account run confirms it. A comment inside a `LET` is a `c4:let_form`
   too (one in front of it is fine), and `c4:return_form` (fix round 2) holds the body to one
   `RETURN '<string literal>'`, as its last statement -- the local double never runs a RETURN, and
   would run statements Snowflake never reaches after one. `c4:identifier_role` (fix round 3): a
   `<LOGICAL>_SRC` name is only ever read (FROM/JOIN/USING) and a `<LOGICAL>_TGT` name only ever
   written (CREATE … TABLE, INSERT/MERGE INTO, UPDATE, DELETE FROM, TRUNCATE). Check 0 also requires every parameter to
   be declared exactly `<NAME> STRING` (no other type, no DEFAULT), the one header the orchestrator's
   SQL policy accepts. `c4:write_mode` (live hardening L4, `write_mode_errors`): every final target is
   written in exactly the form its write mode needs -- the contract's `write_mode`, else
   `intake/mappings.yaml`'s `mode` (`lib.vocab.TARGET_WRITE_MODES`): overwrite one `CREATE OR REPLACE
   TABLE IDENTIFIER(:<LOGICAL>_TGT) AS …`, append one `INSERT INTO`, truncate_append a `TRUNCATE` or
   `DELETE FROM` (no WHERE) then one `INSERT INTO`, update_insert/merge one `MERGE INTO` on exactly the
   contract's keys with `WHEN MATCHED THEN UPDATE` and `WHEN NOT MATCHED THEN INSERT`; any other
   statement on the target is the Output tool's PreSQL (before) or PostSQL (after), allowed only when
   `dag.json` gives the tool one -- and required when it does (live hardening L8 fix round 1, M3: a
   dropped PreSQL/PostSQL is named here rather than left to validation). It is reported beside the parse and run results, not instead of
   them. A target with no known write mode, or a merge target with no keys, is exit 2 (the contract
   has to say it, not the procedure). `c4:external_access` (live hardening L4 fix round 1,
   `external_access_errors`): no statement reaches a file, a stage, the network, an extension or the
   engine's settings -- `COPY`, `PUT`/`GET`, `ATTACH`, `INSTALL`/`LOAD`, `EXPORT`/`IMPORT`, `PRAGMA`,
   `SET`, `CREATE STAGE`/`SECRET`/`… INTEGRATION`, a function that reads files, settings or the
   network (`lib.dbt_surface.DENIED_FUNCTION`, plus Snowflake's stage and file functions), or a table
   reference that is a file or a stage. Such a procedure never runs, and `run_proc` also switches the
   double's external access off (and locks it) before a procedure's first statement;
2. every statement parses with sqlglot's Snowflake parser and is not a `Command` fallback, which
   is what sqlglot produces when it does not actually understand a statement;
3. empty tables are created for each `contract.inputs[]` — mapped sources under
   `MIGDB.MIG_COMPILE`, upstream segment tables at the literal name the contract gives — and for
   each `outputs[]` of kind `target` under `MIGDB.MIG_WORK`;
4. the procedure runs against them;
5. each `outputs[]` of kind `work` exists afterwards with the contract's column names, compared
   case-insensitively and in order.

That six-check sequence is the `sql` target: contract C4's Snowflake Scripting subset. A `snowpark`
target (`contract.json`'s `"target"`, or `--target`) is a different shape and gets a different,
cheaper gate instead — nothing here runs a Snowpark procedure locally (there is no local Snowpark
runtime to run it on): `proc.py` is checked against `lib.snowpark_rules.check_proc_py` (spec §4.2's
AST-walk rules: allowed imports, no session.sql/call, no exec/eval/open, the C4 signature, the
table-name allow-list, per-tool comments, and `rule:write_mode` -- the target write call its write
mode needs, resolved as for `c4:write_mode`), and `proc.sql` is checked for staying in sync with
`render_snowpark.render(proc.py, …)` plus its own C4 signature.

The `dbt` target (`compile_check.py <wf> --target dbt`, no segment: a dbt project is one unit) is a
third, different shape again: `dbt parse` (the one dbt invocation, `lib.dbt_project.run_dbt`) builds
a manifest and nothing here runs a model -- and only once the project is inside the closed dbt surface
(`lib.dbt_project.check_surface`, final fix wave C1: `dbt:surface` the closed file set, `dbt:project_yml` the
template project file, `dbt:yaml` the two closed YAML files, `dbt:hook_sql` one plain statement against `{{ this }}`
per hook, `dbt:model_sql` one query per model reading only through `source()`/`ref()`/`{{ this }}`, plus
`dbt:profiles` and `dbt:model_jinja` below); any surface error and `dbt parse` is not run at all. Sixteen named
checks, each `dbt:<name>` in `compile_check.json`'s `errors`: those five, and `dbt:layout` (the project files `lib.dbt_project.PROJECT_FILES`
names, plus `dbt_project.yml`'s own name/profile/model-paths/vars), `dbt:profiles` (`profiles.yml`
byte-identical to `PROFILES_TEMPLATE` — no credential is ever accepted), `dbt:sources`
(`models/sources.yml` declares exactly the sources `intake/mappings.yaml` maps, with the contract's
own input columns), `dbt:model_jinja` (a model's Jinja is outside the closed allow-list of design
§4.3 — `config`/`source`/`ref`/`this`/`is_incremental()` if/else/endif/comments, nothing else, not
even nested inside one of those), `dbt:parse` (dbt parse itself failed), `dbt:model_missing` (a
contract output with no model), `dbt:model_orphan` (a model that is not any contract output's),
`dbt:model_config` (a model's `materialized`/`incremental_strategy`/`unique_key`/`alias` does not
match its write mode), `dbt:columns` (`models/schema.yml`'s columns do not match the contract's, in
order), `dbt:tool_comments` (a data node with no `-- tool <id>` comment in any model) and
`dbt:hooks` (a node with non-blank PreSQL/PostSQL whose model has no matching, non-blank
`pre-hook`/`post-hook`). Writes `dbt/compile_check.json`.

Writes `segments/<seg>/compile_check.json` (`workflows/<wf>/dbt/compile_check.json` for the `dbt`
target). Exit 0 on OK, 1 on a compile error, 2 when there is nothing to check. Nothing here has run
on a Snowflake account: passing means DuckDB accepted the translated SQL (`sql` target), the AST
rules and the renderer agreed (`snowpark` target), or dbt-duckdb accepted the project (`dbt`
target).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

import render_snowpark
from lib import dbt_project, dbt_surface
from lib.backend import SANDBOX_DB, BackendError, DuckDBBackend
from lib.io import read_json, read_yaml, write_json
from lib.paths import Repo, add_root_arg, seg_token, wf_token
from lib.proc_runner import (LetError, ProcError, ProcInfo, bind, identifier_arguments, identifier_calls,
                              let_values, masked_code, parse_proc, run_proc)

# Fix round 2 (M1): the one RETURN a body may have -- a single string literal (quotes doubled or
# backslash-escaped inside), nothing else.
# Fix round 3 (c4:identifier_role): the words in front of an IDENTIFIER(…) that make it a write target
# or a source read. Modifiers between CREATE/TRUNCATE and the name are skipped (`CREATE OR REPLACE
# TRANSIENT TABLE`, `TRUNCATE TABLE IF EXISTS`).
_ROLE_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*|[(),;]")
_TABLE_MODIFIERS = frozenset({"OR", "REPLACE", "TRANSIENT", "TEMPORARY", "TEMP", "VOLATILE", "LOCAL", "GLOBAL",
                              "TABLE", "IF", "NOT", "EXISTS"})
_RETURN_LITERAL_RE = re.compile(r"^RETURN\s*'(?:[^'\\]|''|\\.)*'$", re.IGNORECASE | re.DOTALL)
from lib import snowpark_rules
from lib.scaffold import todo_errors
from lib.vocab import DATA_LESS_TYPES
from lib.write_modes import ContractError, TargetWrite, dag_nodes, mappings_of, target_writes

COMPILE_SCHEMA = "MIG_COMPILE"
WORK_SCHEMA = "MIG_WORK"
ARGS = {"SRC_DB": SANDBOX_DB, "SRC_SCHEMA": COMPILE_SCHEMA,
        "TGT_DB": SANDBOX_DB, "TGT_SCHEMA": WORK_SCHEMA, "RUN_ID": "compile_check"}

# Contract C4's procedure signature.
C4_PARAMS = ("SRC_DB", "SRC_SCHEMA", "TGT_DB", "TGT_SCHEMA", "RUN_ID")
C4_EXECUTE_AS = "CALLER"

# Contract C4's table-reference rule (Task C4V): one LET per mapped table, then IDENTIFIER(:<VAR>).
# `<LOGICAL>` has the shape intake gives a logical name (`intake_prompt._LOGICAL_RE`).
_C4_LET_RE = re.compile(
    r"^(?P<side>SRC|TGT)_DB\s*\|\|\s*'\.'\s*\|\|\s*(?P=side)_SCHEMA\s*\|\|\s*"
    r"'\.(?P<logical>[A-Z_][A-Z0-9_$]*)'$")
_C4_LET_RULE = ("LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>' for a source, "
                "LET <LOGICAL>_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.<LOGICAL>' for a target, "
                "the arguments named without a colon")
_IDENTIFIER_VARIABLE_RE = re.compile(r"^:(?P<name>[A-Za-z_][A-Za-z_0-9$]*)$")
_SESSION_VARIABLE_RE = re.compile(r"^\$[A-Za-z_][A-Za-z_0-9$]*$")
_STRING_LITERAL_RE = re.compile(r"^'(?:[^'\\]|''|\\.)*'$", re.DOTALL)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Presence only — proc_runner reads the value. This tells "EXECUTE AS OWNER was written" from
# "the clause is missing", which both reach us as ProcInfo.execute_as == "OWNER".
_EXECUTE_AS_CLAUSE_RE = re.compile(r"\bEXECUTE\s+AS\b", re.IGNORECASE)


def compile_check(repo: Repo, wf_id: str, seg: str | None, target: str = "auto") -> dict:
    """Returns {"status": "OK"|"ERROR", "errors": [str], "statements": int} (plus `"target":
    "snowpark"` for that target) and writes it to compile_check.json.

    `target`: "auto" (default) resolves from contract.json's `"target"` field, defaulting to
    "sql" when that field is absent -- unchanged from before `--target` existed. "sql" or
    "snowpark" forces the corresponding check regardless of what the contract says. "dbt" checks
    the whole workflow's dbt project (DV6): `seg` is ignored -- a dbt project is one unit.
    """
    if target == "dbt":
        return compile_check_dbt(repo, wf_id)
    if seg is None:
        raise ValueError(f"{wf_id}: --target {target} needs a segment (only --target dbt does not)")

    contract_path = repo.seg(wf_id, seg, "contract.json")
    if not contract_path.exists():
        raise FileNotFoundError(f"{wf_id}/{seg} has no contract to check: {contract_path}")
    contract = read_json(contract_path)
    resolved = target if target != "auto" else (contract.get("target") or "sql")

    if resolved == "snowpark":
        report = _check_snowpark(repo, wf_id, seg, contract)
    else:
        proc_path = repo.seg(wf_id, seg, "proc.sql")
        if not proc_path.exists():
            raise FileNotFoundError(f"{wf_id}/{seg} has no procedure to check: {proc_path}")
        targets = target_writes(contract, _mappings(repo, wf_id), _dag_nodes(repo, wf_id, seg))
        report = _check(proc_path.read_text(encoding="utf-8"), contract, wf_id, seg, targets)
    write_json(repo.seg(wf_id, seg, "compile_check.json"), report)
    return report


# --- c4:write_mode (Task L4): each final target written in exactly its write mode's form -----------


# ContractError, TargetWrite and target_writes live in `lib.write_modes` (fix round 1): the Snowpark
# gate `validate_snowpark.py` applies before it imports a module resolves write modes the same way.


def _mappings(repo: Repo, wf_id: str) -> dict:
    return mappings_of(repo, wf_id)


def _dag_nodes(repo: Repo, wf_id: str, seg: str) -> list[dict]:
    return dag_nodes(repo, wf_id, seg)


# --- the dbt target -----------------------------------------------------------------------------


def compile_check_dbt(repo: Repo, wf_id: str) -> dict:
    """The dbt target's gate (design §5.1): the project stays inside the closed dbt surface
    (`dbt_project.check_surface`, final fix wave C1), is the template layout, `dbt parse` accepts it,
    and its manifest agrees with every contract -- a model per output and no model that isn't one,
    the config its write mode needs, the contract's columns in order, every mapped source, a
    `-- tool <id>:` comment per data node, a hook per PreSQL/PostSQL. Any surface error and
    `dbt parse` is not run at all. Writes dbt/compile_check.json.

    Prerequisites (each a `FileNotFoundError` naming its path, a usage error): the project's
    `dbt/dbt_project.yml`, `segments/order.json`, every segment's `contract.json`, and
    `intake/mappings.yaml`. A segment's `dag.json` is read when present."""
    project = dbt_project.project_dir(repo, wf_id)
    if not (project / "dbt_project.yml").is_file():
        raise FileNotFoundError(f"{wf_id} has no dbt project to check: {project / 'dbt_project.yml'}")
    order_path = repo.wf(wf_id, "segments", "order.json")
    if not order_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no segments/order.json to check the project against: {order_path}")
    order = read_json(order_path)
    segments = [seg for wave in order for seg in wave]
    contracts = {}
    for seg in segments:
        path = repo.seg(wf_id, seg, "contract.json")
        if not path.is_file():
            raise FileNotFoundError(f"{wf_id}/{seg} has no contract to check the project against: {path}")
        contracts[seg] = read_json(path)
    mappings_path = repo.wf(wf_id, "intake", "mappings.yaml")
    if not mappings_path.is_file():
        raise FileNotFoundError(f"{wf_id} has no intake/mappings.yaml to check the project against: "
                                f"{mappings_path}; run intake first")
    mappings = read_yaml(mappings_path) or {}
    dags = {seg: (read_json(repo.seg(wf_id, seg, "dag.json")) if repo.seg(wf_id, seg, "dag.json").is_file()
                  else {"nodes": []}) for seg in segments}

    # The closed surface (dbt:surface, dbt:profiles, dbt:project_yml, dbt:yaml, dbt:model_jinja,
    # dbt:hook_sql, dbt:model_sql), dbt:layout and dbt:sources are all checked from the project's own
    # files, so they fire even when the project cannot parse at all (e.g. a renamed source breaks
    # every model that reads it, so dbt parse fails too -- both are real problems and both are
    # reported). A surface error means dbt never runs: not even `dbt parse`.
    # Task L8: an unfilled skeleton (scripts/translation_scaffold.py) is refused by name, and nothing
    # else is checked -- dbt never runs over a project that still holds a TODO body.
    unfilled = _dbt_todo_errors(project)
    surface = [] if unfilled else dbt_project.check_surface(project)
    errors = unfilled or (surface + _dbt_layout_errors(project, wf_id) + _dbt_source_errors(project, contracts, mappings))
    models = 0
    if not surface and not unfilled:
        with tempfile.TemporaryDirectory(prefix="dbt-compile-") as tmp:
            result = dbt_project.run_dbt("parse", project,
                                         vars=dbt_project.local_vars(dbt_project.COMPILE_SRC_SCHEMA),
                                         duckdb_path=Path(tmp) / "dbt_sandbox_compile.duckdb")
        if result.code != 0 or result.manifest is None:
            errors.append(f"dbt:parse: dbt parse exited {result.code}: "
                          f"{dbt_project.bounded(dbt_project.tail(result.output))}")
        else:
            nodes = {n["name"]: n for n in result.manifest.get("nodes", {}).values()
                    if n.get("resource_type") == "model"}
            models = len(nodes)
            errors += _dbt_model_errors(nodes, contracts, mappings)
            errors += _dbt_orphan_errors(nodes, contracts)
            errors += _dbt_tool_comment_errors(project, dags)
            errors += _dbt_hook_errors(nodes, dags, mappings)
    report = {"status": "ERROR" if errors else "OK", "target": "dbt", "errors": sorted(set(errors)),
              "statements": 0, "models": models}
    report_path = project / "compile_check.json"
    if dbt_surface.is_link(report_path):               # dbt:surface refused it; never write through it
        try:
            report_path.unlink()
        except OSError:
            os.rmdir(report_path)                    # a junction or a directory symlink
    write_json(report_path, report)
    return report


def _dbt_todo_errors(project: Path) -> list[str]:
    """`scaffold:todo` for every line of the project's models and YAML files that still holds the
    skeleton's TODO marker (a model body, a hook in a config line). Only regular files inside the
    project are read: a link, or anything it leads to, is `dbt:surface`'s to refuse."""
    errors = []
    inside = project.resolve()
    paths = [project / name for name in ("dbt_project.yml", "profiles.yml")]
    paths += sorted(project.glob("models/**/*.yml")) + sorted(project.glob("models/**/*.sql"))
    for path in paths:
        if path.is_file() and not dbt_surface.is_link(path) and path.resolve().is_relative_to(inside):
            rel = path.relative_to(project).as_posix()
            errors += todo_errors(path.read_text(encoding="utf-8", errors="replace"), rel)
    return errors


def _dbt_layout_errors(project: Path, wf_id: str) -> list[str]:
    """Every `dbt_project.PROJECT_FILES` entry exists, and `dbt_project.yml` itself declares the
    name, profile, model-paths and vars a project of this shape must have."""
    errors = [f"dbt:layout: missing {rel}" for rel in dbt_project.PROJECT_FILES
             if not (project / rel).is_file()]
    project_yml = project / "dbt_project.yml"
    if not project_yml.is_file():
        return errors
    doc = dbt_surface.load_yaml(project_yml)
    if not isinstance(doc, dict):
        return errors                                # dbt:project_yml says why
    if doc.get("name") != wf_id:
        errors.append(f"dbt:layout: dbt_project.yml name={doc.get('name')!r}; expected {wf_id!r}")
    if doc.get("profile") != dbt_project.PROFILE:
        errors.append(f"dbt:layout: dbt_project.yml profile={doc.get('profile')!r}; "
                      f"expected {dbt_project.PROFILE!r}")
    if doc.get("model-paths") != ["models"]:
        errors.append(f"dbt:layout: dbt_project.yml model-paths={doc.get('model-paths')!r}; "
                      f"expected ['models']")
    var_keys = set(doc["vars"]) if isinstance(doc.get("vars"), dict) else set()
    if not {"src_schema", "tgt_schema"} <= var_keys:
        errors.append(f"dbt:layout: dbt_project.yml vars={doc.get('vars')!r}; "
                      f"expected a mapping with src_schema and tgt_schema")
    return errors


def _dbt_source_errors(project: Path, contracts: dict, mappings: dict) -> list[str]:
    """`models/sources.yml`'s `src` source must declare exactly the logical names
    `intake/mappings.yaml` maps, each with the columns of any contract input carrying that logical
    name (upper-cased, in order). Read from the project's own YAML rather than dbt's manifest, so
    this fires even when a renamed source also breaks `dbt parse` for every model that reads it."""
    sources_path = project / "models" / "sources.yml"
    if not sources_path.is_file():
        return []  # already reported by _dbt_layout_errors
    doc = dbt_surface.load_yaml(sources_path)
    declared: dict[str, list[str]] = {}
    sources = doc.get("sources") if isinstance(doc, dict) else None
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict) or source.get("name") != "src":
            continue
        tables = source.get("tables")
        for table in tables if isinstance(tables, list) else []:
            if not isinstance(table, dict):
                continue
            columns = table.get("columns")
            declared[str(table.get("name"))] = [str(c.get("name")).upper() for c in
                                                 (columns if isinstance(columns, list) else []) if isinstance(c, dict)]

    expected_names = {str(s["logical"]) for s in (mappings.get("sources") or {}).values() if s.get("logical")}
    actual_names = set(declared)
    errors = []
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        detail = "; ".join(part for part in (
            f"missing {missing}" if missing else "", f"undeclared extra {extra}" if extra else "") if part)
        errors.append(f"dbt:sources: models/sources.yml declares {sorted(actual_names)}; "
                      f"intake/mappings.yaml maps {sorted(expected_names)} ({detail})")

    for contract in contracts.values():
        for source in contract.get("inputs") or []:
            logical = source.get("logical")
            if not logical or logical not in declared:
                continue
            expected_cols = [c["name"].upper() for c in source.get("columns") or []]
            if declared[logical] != expected_cols:
                errors.append(f"dbt:sources: models/sources.yml lists {declared[logical]} for "
                              f"{logical}; the contract declares {expected_cols}")
    return errors


# --- M3 (fix round 1): model SQL is a closed Jinja surface ---------------------------------------
#
# Now part of the closed dbt surface (final fix wave C1, `lib.dbt_surface`), which `run_dbt` itself
# enforces. `_dbt_jinja_errors` keeps its name for the agent-file pins in tests/test_agents_config.py.


def _dbt_jinja_errors(project: Path) -> list[str]:
    """Every model file's Jinja is the closed allow-list of design §4.3, no more (M3, fix round
    1; `config(...)`'s keyword arguments closed by final fix wave C1.5): only the `dbt:model_jinja`
    errors of `dbt_surface`."""
    return dbt_surface.model_jinja_errors(project)


def _dbt_model_errors(nodes: dict, contracts: dict, mappings: dict) -> list[str]:
    """Every contract output has a model; a work output's model is a `table`; a target output's
    model config matches `dbt_project.expected_model_config` for its mapped write mode; every
    model's `schema.yml` columns equal the contract's, upper-cased, in order."""
    errors: list[str] = []
    outputs_map = mappings.get("outputs") or {}
    for seg, contract in contracts.items():
        for output in contract.get("outputs") or []:
            name = dbt_project.model_name(output)
            node = nodes.get(name)
            if node is None:
                errors.append(f"dbt:model_missing: no model models/{name}.sql for {seg} output "
                              f"{output.get('stream')!r} ({output.get('kind')})")
                continue
            config = node.get("config") or {}
            if output.get("kind") == "work":
                if config.get("materialized") != "table":
                    errors.append(f"dbt:model_config: models/{name}.sql has "
                                  f"materialized={config.get('materialized')!r}; a work model needs "
                                  f"materialized='table'")
            else:
                errors.extend(_dbt_target_config_errors(name, config, output, outputs_map))
            expected_cols = [c["name"].upper() for c in output.get("columns") or []]
            actual_cols = [str(c).upper() for c in node.get("columns") or []]
            if actual_cols != expected_cols:
                errors.append(f"dbt:columns: models/schema.yml lists {actual_cols} for {name}; "
                              f"the contract declares {expected_cols}")
    return errors


def _dbt_orphan_errors(nodes: dict, contracts: dict) -> list[str]:
    """I2 (fix round 1): every model node the manifest parsed must be some contract output's model
    -- work stream or target alike. A leftover or invented `models/*.sql` that happens to build
    cleanly would otherwise deploy an untracked table with nothing to catch it."""
    known = {dbt_project.model_name(output) for contract in contracts.values()
            for output in contract.get("outputs") or []}
    return [f"dbt:model_orphan: models/{name}.sql is not a contract output"
           for name in nodes if name not in known]


def _dbt_target_config_errors(name: str, config: dict, output: dict, outputs_map: dict) -> list[str]:
    logical = output.get("logical")
    mapping = next((m for m in outputs_map.values() if m.get("logical") == logical), None)
    if mapping is None:
        return [f"dbt:model_config: no intake/mappings.yaml output has logical {logical}"]
    mode = mapping.get("mode")
    expected = dbt_project.expected_model_config(mode, mapping.get("keys") or [], logical)
    errors = []
    for key, value in expected.items():
        actual = config.get(key)
        if key == "unique_key" and actual is not None:
            # A key that needs quotes (a reserved word) is written double-quoted (Task L8 fix round 1, I2):
            # the same column as the mapping's bare name.
            actual = sorted(_unquote_key(str(k)).upper() for k in actual) if isinstance(actual, list) \
                else [_unquote_key(str(actual)).upper()]
        if actual != value:
            errors.append(f"dbt:model_config: models/{name}.sql has {key}={actual!r}; the {mode} "
                          f"write mode needs {key}={value!r}")
    return errors


def _unquote_key(key: str) -> str:
    return key[1:-1] if len(key) > 1 and key.startswith('"') and key.endswith('"') else key


def _dbt_tool_comment_errors(project: Path, dags: dict) -> list[str]:
    """Every data node (not in `DATA_LESS_TYPES`) of every segment needs a `-- tool <id>` comment
    somewhere in the project's models -- the same rule `render_snowpark`'s procedures follow,
    applied to SQL files instead of a Python AST."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(project.glob("models/**/*.sql")))
    errors = []
    for dag in dags.values():
        for node in dag.get("nodes") or []:
            if node.get("type") in DATA_LESS_TYPES:
                continue
            tool_id = str(node.get("tool_id"))
            if not re.search(rf"--\s*tool\s+{re.escape(tool_id)}\b", text):
                errors.append(f'dbt:tool_comments: no "-- tool {tool_id}:" comment in any model')
    return errors


_HOOK_LABELS = {"pre_sql": ("pre-hook", "pre_hook", "PreSQL"), "post_sql": ("post-hook", "post_hook", "PostSQL")}


def _dbt_hook_errors(nodes: dict, dags: dict, mappings: dict) -> list[str]:
    """A node with non-empty PreSQL/PostSQL needs its mapped model to carry a matching, non-empty
    `pre-hook`/`post-hook` (dbt's manifest key spelling)."""
    errors = []
    outputs_map = mappings.get("outputs") or {}
    for dag in dags.values():
        for node in dag.get("nodes") or []:
            config = node.get("config") or {}
            tool_id = str(node.get("tool_id"))
            for phase, (hook_key, hook_label, sql_label) in _HOOK_LABELS.items():
                if not config.get(phase):
                    continue
                mapping = next((m for m in outputs_map.values()
                               if tool_id in [str(t) for t in (m.get("tool_ids") or [])]), None)
                logical = mapping.get("logical") if mapping else None
                if not logical:
                    errors.append(f"dbt:hooks: no intake/mappings.yaml output maps tool {tool_id} "
                                  f"for its {sql_label}")
                    continue
                model_node = nodes.get(str(logical).lower())
                hooks = (model_node.get("config") or {}).get(hook_key) if model_node else None
                # dbt's manifest turns even pre_hook="" into [{"sql": "", ...}] -- a non-empty
                # list `if not hooks` alone would accept as "has a hook". It only really has one
                # if some entry's sql is more than blank (I1, fix round 1).
                if not hooks or not any(str(h.get("sql", "")).strip() for h in hooks):
                    errors.append(f"dbt:hooks: models/{str(logical).lower()}.sql has no {hook_label} "
                                  f"for tool {tool_id}'s {sql_label}")
    return errors


def _check(sql_text: str, contract: dict, wf_id: str, seg: str, targets: list[TargetWrite] | None = None) -> dict:
    """The `sql` target's checks. `targets` are the final targets with their write modes
    (`target_writes`); None reads them from `contract` alone (no mappings, no PreSQL/PostSQL)."""
    if targets is None:
        targets = target_writes(contract)
    # Task L8: an unfilled skeleton (scripts/translation_scaffold.py) is refused by name before
    # anything else is checked -- a TODO body is not SQL, so every later check would only echo it.
    unfilled = todo_errors(sql_text, "proc.sql")
    if unfilled:
        return {"status": "ERROR", "errors": unfilled, "statements": 0}
    errors: list[str] = []
    try:
        proc = parse_proc(sql_text)
    except LetError as exc:
        return {"status": "ERROR", "errors": [_clean(f"c4:let_form: {exc}")], "statements": 0}
    except ProcError as exc:
        return {"status": "ERROR", "errors": [_clean(str(exc))], "statements": 0}

    signature = _signature_errors(proc, sql_text, wf_id, seg)
    if signature:
        # A wrong signature is not something the later checks can work around: the call would not
        # bind, so anything they reported would be a consequence, not a second problem.
        return {"status": "ERROR", "errors": signature, "statements": len(proc.statements)}

    table_references = table_reference_errors(proc) + identifier_role_errors(proc) + return_form_errors(proc)
    if table_references:
        # Nor is a table reference outside contract C4's form: it is refused whatever it would do
        # on the local double (which still folds the old expression form -- see proc_runner).
        return {"status": "ERROR", "errors": table_references, "statements": len(proc.statements)}

    # Fix round 1 (P): a statement that reaches a file, a stage, the network or the engine's settings
    # never runs -- not even on the double, whose own lock (`run_proc`) is the second guard.
    external = external_access_errors(proc)
    if external:
        return {"status": "ERROR", "errors": external, "statements": len(proc.statements)}

    # Task L4: a write in the wrong form still runs on the local double (every target table is
    # created empty below), so it is reported beside whatever the parse and the run say, not
    # instead of them -- one compile report then names everything a fixer has to change.
    write_errors = write_mode_errors(proc, targets)

    values = {**ARGS, **let_values(proc, ARGS)}
    bound = []
    for statement in proc.statements:
        try:
            bound.append(bind(statement, values))
        except ProcError as exc:
            errors.append(_clean(str(exc)))
    if len(bound) == len(proc.statements):
        errors.extend(_parse_errors(bound))

    if not errors:
        errors.extend(_run_errors(sql_text, contract))
    errors = write_errors + errors
    return {"status": "ERROR" if errors else "OK", "errors": errors,
            "statements": len(proc.statements)}


def _check_snowpark(repo: Repo, wf_id: str, seg: str, contract: dict) -> dict:
    """The snowpark target's gate: proc.py against the AST rules, proc.sql against the renderer
    and its own C4 signature. No procedure runs locally -- there is no local Snowpark runtime."""
    proc_py_path = repo.seg(wf_id, seg, "proc.py")
    if not proc_py_path.exists():
        raise FileNotFoundError(f"{wf_id}/{seg} has no procedure module to check: {proc_py_path}")
    source = proc_py_path.read_text(encoding="utf-8")
    # Task L8: an unfilled skeleton is refused by name first (see `_check`).
    unfilled = todo_errors(source, "proc.py")
    if unfilled:
        return {"status": "ERROR", "target": "snowpark", "errors": unfilled, "statements": 0}

    # The whole Snowpark gate (fix round 1): the same one `validate_snowpark.py` applies before it
    # imports a module -- the data nodes, and each target's write mode resolved as for `c4:write_mode`
    # (the contract's, else the mappings'; ContractError: exit 2).
    errors = list(snowpark_rules.segment_rule_errors(repo, wf_id, seg, contract, source))
    errors.extend(_render_errors(repo, wf_id, seg, source))
    return {"status": "ERROR" if errors else "OK", "target": "snowpark",
            "errors": sorted(set(errors)), "statements": 0}


def _render_errors(repo: Repo, wf_id: str, seg: str, source: str) -> list[str]:
    """rule:render_mismatch when proc.sql is missing or does not match render(proc.py, …), plus
    proc.sql's own C4 signature check through parse_proc when it exists and parses."""
    proc_sql_path = repo.seg(wf_id, seg, "proc.sql")
    program = {}
    if repo.global_mappings.is_file():
        program = (read_yaml(repo.global_mappings) or {}).get("program") or {}
    runtime = str(program.get("snowpark_runtime") or "3.11")
    try:
        expected = render_snowpark.render(source, wf_id, seg, runtime)
    except ValueError as exc:
        return [f"rule:render_mismatch: proc.py cannot be rendered: {exc}"]

    if not proc_sql_path.exists():
        return [f"rule:render_mismatch: {proc_sql_path} does not exist; run render_snowpark.py "
               f"{wf_id} {seg}"]

    errors = []
    actual = proc_sql_path.read_text(encoding="utf-8")
    if actual != expected:
        errors.append(f"rule:render_mismatch: {proc_sql_path} does not match render(proc.py); "
                      f"run render_snowpark.py {wf_id} {seg} again")
    try:
        proc = parse_proc(actual)
    except ProcError as exc:
        errors.append(_clean(str(exc)))
    else:
        errors.extend(_signature_errors(proc, actual, wf_id, seg))
    return errors


def _signature_errors(proc: ProcInfo, sql_text: str, wf_id: str, seg: str) -> list[str]:
    """Check 0: the procedure is the one contract C4 says this segment must expose."""
    errors = []
    expected_name = f"{WORK_SCHEMA}.{wf_token(wf_id)}_{seg_token(seg)}"
    if proc.name.replace('"', "").upper() != expected_name:
        errors.append(f"procedure {proc.name} found; contract C4 requires {expected_name} "
                      f"for {wf_id}/{seg}")
    if tuple(param.upper() for param in proc.params) != C4_PARAMS:
        errors.append(f"parameters ({', '.join(proc.params)}) found; contract C4 requires "
                      f"({', '.join(C4_PARAMS)}) in that order")
    # Fix round 2 (c): every parameter is declared exactly `<NAME> STRING` -- no other type, no
    # DEFAULT -- which is also the only header the orchestrator's SQL policy accepts.
    for declaration in proc.param_declarations:
        name = declaration.split()[0]
        if declaration.upper() != f"{name.upper()} STRING":
            errors.append(f"parameter {declaration} found; contract C4 declares every parameter exactly "
                          f"as <NAME> STRING (no other type, no DEFAULT)")
    if proc.execute_as != C4_EXECUTE_AS:
        header = sql_text.split("$$", 1)[0]
        found = (f"EXECUTE AS {proc.execute_as} found" if _EXECUTE_AS_CLAUSE_RE.search(header)
                 else "no EXECUTE AS clause found (Snowflake defaults to owner's rights)")
        errors.append(f"{found}; contract C4 requires EXECUTE AS {C4_EXECUTE_AS}, because a "
                      f"procedure running with owner's rights cannot ALTER SESSION")
    return errors


def table_reference_errors(proc: ProcInfo) -> list[str]:
    """`c4:let_form` and `c4:identifier_expression` (Task C4V): every LET builds one mapped
    table's name by contract C4's rule, and every IDENTIFIER(…) takes one documented value."""
    errors = []
    for let in proc.lets:
        rule = _C4_LET_RE.match(let.expression)
        expected = f"{rule.group('logical')}_{rule.group('side')}" if rule else None
        if rule is None or let.name != expected:
            named = f"; this one must be named {expected}" if expected else ""
            errors.append(_clean(f"c4:let_form: {let.text} does not build a table name by contract "
                                 f"C4's rule ({_C4_LET_RULE}){named}"))
    declared = {let.name.upper() for let in proc.lets}
    for statement in proc.statements:
        for argument in identifier_arguments(statement):
            variable = _IDENTIFIER_VARIABLE_RE.match(argument)
            if variable is not None and variable.group("name").upper() in declared:
                continue
            if _STRING_LITERAL_RE.match(argument) or _SESSION_VARIABLE_RE.match(argument):
                continue
            what = ("names no variable a LET of this procedure declares" if variable is not None
                    else "takes an expression; Snowflake documents IDENTIFIER( with a single string "
                         "literal, session variable, bind variable or Snowflake Scripting variable")
            errors.append(_clean(f"c4:identifier_expression: IDENTIFIER({argument}) {what}. Build the "
                                 f"name first ({_C4_LET_RULE}) and write IDENTIFIER(:<LOGICAL>_SRC) or "
                                 f"IDENTIFIER(:<LOGICAL>_TGT)"))
    return errors


def identifier_role(code_before: str) -> str | None:
    """"write" when the IDENTIFIER(…) after `code_before` is a write target (CREATE … TABLE, INSERT /
    MERGE / COPY INTO, UPDATE, DELETE FROM, TRUNCATE), "read" when it is read as a source (FROM,
    JOIN, USING), None otherwise."""
    words = [word.upper() for word in _ROLE_WORD_RE.findall(code_before)]
    if not words:
        return None
    last, previous = words[-1], (words[-2] if len(words) > 1 else "")
    if last in ("INTO", "UPDATE"):
        return "write"
    if last == "FROM":
        return "write" if previous == "DELETE" else "read"
    if last in ("JOIN", "USING"):
        return "read"
    position = len(words) - 1
    while position >= 0 and words[position] in _TABLE_MODIFIERS:
        position -= 1
    if position >= 0 and words[position] in ("CREATE", "TRUNCATE"):
        return "write"
    return None


def identifier_role_errors(proc: ProcInfo) -> list[str]:
    """`c4:identifier_role` (fix round 3): a `<LOGICAL>_SRC` name is only ever read and a
    `<LOGICAL>_TGT` name only ever written -- a procedure that writes its source, or reads its
    target where contract C4 reads a source, is refused."""
    declared = {let.name.upper() for let in proc.lets}
    errors = []
    for statement in proc.statements:
        for code_before, argument in identifier_calls(statement):
            variable = _IDENTIFIER_VARIABLE_RE.match(argument)
            if variable is None or variable.group("name").upper() not in declared:
                continue
            name = variable.group("name").upper()
            role = identifier_role(code_before)
            if name.endswith("_SRC") and role == "write":
                errors.append(f"c4:identifier_role: IDENTIFIER({argument}) is written to; a <LOGICAL>_SRC name "
                              f"is only ever read -- write a target through its <LOGICAL>_TGT name")
            if name.endswith("_TGT") and role == "read":
                errors.append(f"c4:identifier_role: IDENTIFIER({argument}) is read as a source; a "
                              f"<LOGICAL>_TGT name is only ever written -- read a source through its "
                              f"<LOGICAL>_SRC name")
    return errors


def return_form_errors(proc: ProcInfo) -> list[str]:
    """`c4:return_form` (fix round 2): the body has one `RETURN '<string literal>'`, as its last
    statement. The local double never executes a RETURN, and it would run statements Snowflake never
    reaches after one, so anything else is refused rather than left unchecked."""
    errors = []
    if len(proc.returns) > 1:
        errors.append(_clean(f"c4:return_form: the body has {len(proc.returns)} RETURNs; contract C4 allows "
                             f"one RETURN, a string literal, as the last statement: "
                             f"{'; '.join(ret.text for ret in proc.returns)}"))
    for ret in proc.returns:
        if not _RETURN_LITERAL_RE.match(ret.text):
            errors.append(_clean(f"c4:return_form: {ret.text} is not RETURN '<string literal>' -- nothing "
                                 f"else is accepted there (the local double never runs a RETURN)"))
        if ret.followed_by:
            errors.append(_clean(f"c4:return_form: {ret.text} is not the last statement; Snowflake stops "
                                 f"there, while the local double would run the {ret.followed_by} "
                                 f"statement(s) after it"))
    return errors


#: `IDENTIFIER(:<VAR>)` -- the one table reference contract C4 allows a procedure to write through.
_TGT_CALL_RE = re.compile(r"\bIDENTIFIER\s*\(\s*:(?P<name>[A-Za-z_][A-Za-z_0-9$]*)\s*\)", re.IGNORECASE)
_AS_RE = re.compile(r"\s*AS\b", re.IGNORECASE)
_ALIAS_ONLY_RE = re.compile(r"\s*(?:AS\s+)?[A-Za-z_][A-Za-z0-9_$]*\s*", re.IGNORECASE)
_CREATE_OTHER_RE = re.compile(r"\s*(LIKE|CLONE)\b", re.IGNORECASE)
#: The token stream of a MERGE's tail: a quoted identifier, a word, or a parenthesis.
_MERGE_TOKEN_RE = re.compile(r'"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*|[()]')
_NAME = r'(?:[A-Za-z_][A-Za-z0-9_$]*|"(?:[^"]|"")+")'
#: One conjunct of a MERGE's ON clause as `c4:write_mode` accepts it: `<alias>.<col> = <alias>.<col>`.
_KEY_EQUALITY_RE = re.compile(rf"(?P<lq>{_NAME})\s*\.\s*(?P<lc>{_NAME})\s*=\s*(?P<rq>{_NAME})\s*\.\s*(?P<rc>{_NAME})")
#: Each `WHEN [NOT] MATCHED … THEN <action>` clause of a MERGE, in order.
_MERGE_CLAUSE_RE = re.compile(r"\bWHEN\s+(?P<matched>NOT\s+MATCHED|MATCHED)\b.*?\bTHEN\s+(?P<action>UPDATE|DELETE|INSERT)\b",
                              re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class _Write:
    """One statement's write to a target: its `form` (what `c4:write_mode` compares), `label` (how a
    message shows it), and what follows the `IDENTIFIER(…)` in the statement (a MERGE's ON clause):
    `code` with comments and string literals blanked, `text` with only the comments blanked -- the
    same offsets, so a clause found in the one is shown from the other."""
    form: str
    label: str
    code: str
    text: str


def _write_form(before: str, after: str, statement_head: str) -> tuple[str, str] | None:
    """(form, label) of the write an `IDENTIFIER(:<X>_TGT)` makes after the code `before` it and
    before the code `after` it; None when it is not a write (a read is `c4:identifier_role`'s)."""
    words = [word.upper() for word in _ROLE_WORD_RE.findall(before)]
    if not words:
        return None
    last, previous = words[-1], (words[-2] if len(words) > 1 else "")
    if last == "INTO":
        known = {"INSERT": ("insert", "INSERT"), "OVERWRITE": ("insert_overwrite", "INSERT OVERWRITE"),
                 "MERGE": ("merge", "MERGE"), "COPY": ("copy", "COPY INTO")}
        return known.get(previous, ("other", f"{statement_head} … INTO"))
    if last == "FROM":
        if previous != "DELETE":
            return None
        # Only an alias after the name (fix round 1, N2) still empties the whole table.
        whole = not after.strip() or _ALIAS_ONLY_RE.fullmatch(after) is not None
        return ("delete", "DELETE") if whole else ("delete_where", "DELETE … WHERE")
    if last == "UPDATE":
        return "update", "UPDATE"
    position = len(words) - 1
    while position >= 0 and words[position] in _TABLE_MODIFIERS:
        position -= 1
    if position < 0:
        return None
    head = words[position]
    if head == "CREATE":
        as_follows = _AS_RE.match(after) is not None
        if words[position + 1:] == ["OR", "REPLACE", "TABLE"] and as_follows:
            return "ctas", "CREATE OR REPLACE TABLE … AS"
        other = _CREATE_OTHER_RE.match(after)                  # N2: CREATE TABLE … LIKE / CLONE
        if other is not None:
            return "create", f"{' '.join(words[position:])} … {other.group(1).upper()}"
        return "create", " ".join(words[position:]) + ("" if as_follows else " with no AS right after the name")
    if head in ("TRUNCATE", "DROP", "ALTER"):
        return head.lower(), head
    return None


def _target_writes_in(proc: ProcInfo, variable: str) -> list[_Write]:
    """Every write to `IDENTIFIER(:<variable>)` in the body, in statement order."""
    writes = []
    for statement in proc.statements:
        code = masked_code(statement)
        text = masked_code(statement, strings=False)   # the same offsets, string literals kept
        head_words = _ROLE_WORD_RE.findall(code)
        head = head_words[0].upper() if head_words else ""
        for match in _TGT_CALL_RE.finditer(code):
            if match.group("name").upper() != variable:
                continue
            form = _write_form(code[:match.start()], code[match.end():], head)
            if form is not None:
                writes.append(_Write(form=form[0], label=form[1], code=code[match.end():], text=text[match.end():]))
    return writes


def _required_form(target: TargetWrite) -> str:
    variable = f"IDENTIFIER(:{target.logical}_TGT)"
    return {
        "overwrite": f"CREATE OR REPLACE TABLE {variable} AS …",
        "append": f"INSERT INTO {variable} (<columns>) SELECT …",
        "truncate_append": (f"TRUNCATE TABLE {variable} (or DELETE FROM it, with no WHERE) followed by "
                            f"INSERT INTO {variable} (<columns>) SELECT …"),
        "update_insert": (f"MERGE INTO {variable} … ON the contract's keys {_sql_keys(target.keys)} … "
                          f"WHEN MATCHED THEN UPDATE … WHEN NOT MATCHED THEN INSERT …"),
    }[target.mode]


_WRITE_FORM = {"overwrite": "ctas", "append": "insert", "truncate_append": "insert", "update_insert": "merge"}
_CLEARS = ("truncate", "delete")


def write_mode_errors(proc: ProcInfo, targets: list[TargetWrite]) -> list[str]:
    """`c4:write_mode` (Task L4): each final target is written through `IDENTIFIER(:<LOGICAL>_TGT)`
    exactly as its write mode requires (`cookbook/output.md`, "Config fields that change the
    pattern"): overwrite is one `CREATE OR REPLACE TABLE … AS`; append one `INSERT INTO`;
    truncate_append a `TRUNCATE`/`DELETE FROM` (no WHERE) of it, then one `INSERT INTO`;
    update_insert (intake's merge) one `MERGE INTO` on exactly the contract's keys with both
    `WHEN MATCHED THEN UPDATE` and `WHEN NOT MATCHED THEN INSERT`. Any other statement on the target
    is the Output tool's PreSQL (before the write) or PostSQL (after it) -- allowed only when the
    tool has one (`dag.json`), and required when it does (Task L8 fix round 1, M3). A form is judged from
    the text; nothing here has run on Snowflake."""
    errors = []
    for target in targets:
        variable = f"{target.logical}_TGT".upper()
        writes = _target_writes_in(proc, variable)
        head = f"c4:write_mode: {target.logical} is {target.spelling}: write it with {_required_form(target)}"
        if not writes:
            errors.append(f"{head}; the procedure never writes IDENTIFIER(:{target.logical}_TGT)")
            continue
        found = " + ".join(write.label for write in writes)
        core = [i for i, write in enumerate(writes) if write.form == _WRITE_FORM[target.mode]]
        if not core:
            errors.append(f"{head}, not {found}")
            continue
        if len(core) > 1:
            errors.append(f"{head} once, not {found}")
            continue
        start = end = core[0]
        if target.mode == "truncate_append":
            clears = [i for i in range(end) if writes[i].form in _CLEARS]
            if not clears:
                errors.append(f"{head}, not {found}")
                continue
            start = clears[-1]
        problems = []
        if start > 0 and not target.pre_sql:
            problems.append(f"a statement before the write is allowed only as tool {target.tool_id}'s PreSQL, "
                            f"and it has none")
        if writes[start + 1:end]:
            problems.append("nothing may come between the TRUNCATE/DELETE and the INSERT")
        if writes[end + 1:] and not target.post_sql:
            problems.append(f"a statement after the write is allowed only as tool {target.tool_id}'s PostSQL, "
                            f"and it has none")
        # Task L8 fix round 1 (M3): the Output tool's PreSQL/PostSQL is not optional -- a skeleton's slot
        # deleted outright would otherwise compile and drop it silently (dbt:hooks already requires a hook).
        if target.pre_sql and start == 0:
            problems.append(f"tool {target.tool_id} has a PreSQL: it is a statement on "
                            f"IDENTIFIER(:{target.logical}_TGT) before the write, and there is none")
        if target.post_sql and not writes[end + 1:]:
            problems.append(f"tool {target.tool_id} has a PostSQL: it is a statement on "
                            f"IDENTIFIER(:{target.logical}_TGT) after the write, and there is none")
        if problems:
            errors.append(f"{head} alone, not {found} ({'; '.join(problems)})")
            continue
        if target.mode == "update_insert":
            errors.extend(_merge_errors(target, writes[end]))
    return errors


def _merge_errors(target: TargetWrite, merge: _Write) -> list[str]:
    """The MERGE matches on exactly the contract's keys -- `<a>.<K> = <b>.<K>` per key, two different
    aliases, joined by AND and nothing else (a NULL key then never matches, which is Alteryx's rule
    too) -- and has both an update of matched rows and an insert of the rest."""
    variable = f"IDENTIFIER(:{target.logical}_TGT)"
    errors = []
    span = _merge_on_clause(merge.code)
    keys = _merge_keys(merge.code[span[0]:span[1]]) if span is not None else None
    if keys is None or set(keys) != {key.upper() for key in target.keys}:
        shown = " ".join(merge.text[span[0]:span[1]].split()) if span is not None else "(no ON clause found)"
        errors.append(f"c4:write_mode: {target.logical} is {target.spelling}: the MERGE INTO {variable} must match "
                      f"on exactly the contract's keys {_sql_keys(target.keys)} -- ON <target>.<KEY> = "
                      f"<source>.<KEY> for each key, joined by AND, nothing else -- not ON {shown}")
    clauses = {(" ".join(m.group("matched").upper().split()), m.group("action").upper())
               for m in _MERGE_CLAUSE_RE.finditer(merge.code)}
    if not {("MATCHED", "UPDATE"), ("NOT MATCHED", "INSERT")} <= clauses:
        errors.append(f"c4:write_mode: {target.logical} is {target.spelling}: the MERGE INTO {variable} needs both "
                      f"WHEN MATCHED THEN UPDATE and WHEN NOT MATCHED THEN INSERT (Update; Insert if new)")
    return errors


def _merge_on_clause(after: str) -> tuple[int, int] | None:
    """The (start, end) offsets of a MERGE's ON clause in `after`: from the first top-level ON after
    its USING to the first top-level WHEN. None when there is no such ON."""
    depth, using, start = 0, False, None
    for match in _MERGE_TOKEN_RE.finditer(after):
        token = match.group(0)
        if token in "()":
            depth += 1 if token == "(" else -1
            continue
        if depth:
            continue
        word = token.upper()
        if start is None:
            if word == "USING":
                using = True
            elif word == "ON" and using:
                start = match.end()
        elif word == "WHEN":
            return start, match.start()
    return (start, len(after)) if start is not None else None


def _merge_keys(on_clause: str) -> list[str] | None:
    """The key columns an ON clause equates, one `<a>.<K> = <b>.<K>` per top-level AND; None as soon
    as a conjunct is anything else (an OR, a function, a literal, one alias on both sides)."""
    conjuncts, depth, start = [], 0, 0
    for match in _MERGE_TOKEN_RE.finditer(on_clause):
        token = match.group(0)
        if token in "()":
            depth += 1 if token == "(" else -1
        elif depth == 0 and token.upper() == "AND":
            conjuncts.append(on_clause[start:match.start()])
            start = match.end()
    conjuncts.append(on_clause[start:])
    keys = []
    for conjunct in conjuncts:
        text = conjunct.strip()
        while text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
            text = text[1:-1].strip()
        match = _KEY_EQUALITY_RE.fullmatch(text)
        if match is None:
            return None
        left, right = _unquote_name(match.group("lc")), _unquote_name(match.group("rc"))
        if _unquote_name(match.group("lq")) == _unquote_name(match.group("rq")) or left != right:
            return None
        keys.append(left)
    return keys


def _balanced(text: str) -> bool:
    depth = 0
    for char in text:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth < 0:
            return False
    return depth == 0


def _unquote_name(name: str) -> str:
    """A name as the key check compares it: unquoted, upper-cased. Quoted or not, and in any case,
    because contract keys are Alteryx field names verbatim and every other stage compares keys
    case-insensitively (`compare.py`) -- fix round 1, I2."""
    return (name[1:-1].replace('""', '"') if name.startswith('"') else name).upper()


_PLAIN_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def _sql_keys(keys) -> str:
    """The keys as a MERGE has to spell them: a plain identifier as is, anything else (a space, a
    symbol) double-quoted, so the message names a spelling that compiles."""
    return ", ".join(key if _PLAIN_NAME_RE.fullmatch(key) else '"' + key.replace('"', '""') + '"' for key in keys)


# --- c4:external_access (live hardening L4 fix round 1, P) ---------------------------------------------
#
# A segment procedure reads its sources through IDENTIFIER(:<LOGICAL>_SRC) and writes tables; nothing in
# it has any business with a file, a stage, the network, an extension or the engine's settings. On the
# local double (DuckDB) such SQL would reach the host's files, so it is refused here by name, before
# anything runs; `lib.proc_runner.run_proc` also takes the double's external access away before a
# procedure's first statement, for whatever gets past this list.

#: Statement heads that move data to or from files, stages or the network, load extensions, or change
#: the engine's settings -- DuckDB's and Snowflake's.
_EXTERNAL_HEADS = frozenset({"COPY", "PUT", "GET", "LIST", "LS", "REMOVE", "RM", "ATTACH", "DETACH", "INSTALL",
                             "FORCE", "LOAD", "EXPORT", "IMPORT", "PRAGMA", "SET", "RESET", "UNSET"})
#: `CREATE` of an object that reaches outside the database: a stage, a secret, an integration, an
#: external table, a file format.
_EXTERNAL_CREATE_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:TEMP|TEMPORARY|PERSISTENT)\s+)?"
    r"(?P<what>STAGE|SECRET|(?:\w+\s+)?INTEGRATION|EXTERNAL\s+TABLE|FILE\s+FORMAT)\b", re.IGNORECASE)
#: Snowflake's own stage and file functions (DuckDB's are `dbt_surface.DENIED_FUNCTION`'s, reused).
_SNOWFLAKE_FILE_FUNCTION = re.compile(
    r"^(?:get_presigned_url|build_scoped_file_url|build_stage_file_url|get_stage_location|get_relative_path"
    r"|get_absolute_path|infer_schema|to_file|fl_\w+|system\$\w+)$")
_EXTERNAL_TAIL = ("a segment procedure reads its sources through IDENTIFIER(:<LOGICAL>_SRC) and writes tables, "
                  "nothing else")
_PATH_CHARACTERS = re.compile(r"[./\\:]")


def _function_name(node: exp.Func) -> str:
    return str(node.name if isinstance(node, (exp.Anonymous, exp.AnonymousAggFunc)) else node.sql_name()).lower()


def external_access_errors(proc: ProcInfo, values: dict | None = None) -> list[str]:
    """`c4:external_access`: no statement of the body reaches a file, a stage, the network, an
    extension or the engine's settings -- by its head (`COPY`, `PUT`/`GET`, `ATTACH`, `INSTALL`/`LOAD`,
    `EXPORT`/`IMPORT DATABASE`, `PRAGMA`, `SET`, ...), by `CREATE STAGE`/`SECRET`/`... INTEGRATION`, by a
    function that reads files, settings or the network (`dbt_surface.DENIED_FUNCTION`: `read_*`,
    `*_scan`, `glob`, `getenv`, `sniff_csv`, ...; Snowflake's stage and file functions), or by a table
    reference that is a file (`FROM '<path>'`, a quoted name that looks like a path) or a stage (`@...`).
    Functions and table references are read from sqlglot's tree of the bound statement -- the tree the
    double renders and runs."""
    if values is None:
        values = {**ARGS, **let_values(proc, ARGS)}
    errors: list[str] = []
    for statement in proc.statements:
        code = masked_code(statement)
        words = _ROLE_WORD_RE.findall(code)
        head = words[0].upper() if words else ""
        if head in _EXTERNAL_HEADS:
            errors.append(f"c4:external_access: {head} moves data to or from files, stages or the network, or "
                          f"changes the engine's settings; {_EXTERNAL_TAIL}")
            continue
        created = _EXTERNAL_CREATE_RE.match(code)
        if created:
            errors.append(f"c4:external_access: CREATE {' '.join(created.group('what').upper().split())} reaches "
                          f"outside the database; {_EXTERNAL_TAIL}")
            continue
        try:
            tree = sqlglot.parse_one(bind(statement, values), read="snowflake")
        except (ProcError, SqlglotError):
            continue                                  # never runs: `_parse_errors` refuses it
        if tree is None:
            continue
        for node in tree.find_all(exp.Func):
            name = _function_name(node)
            if dbt_surface.DENIED_FUNCTION.match(name) or _SNOWFLAKE_FILE_FUNCTION.match(name):
                errors.append(f"c4:external_access: {name}() reads files, settings or the network; {_EXTERNAL_TAIL}")
        for table in tree.find_all(exp.Table):
            this = table.this
            if isinstance(this, exp.Literal) and this.is_string:
                errors.append(f"c4:external_access: '{this.name}' as a table reads a file; {_EXTERNAL_TAIL}")
            elif isinstance(this, exp.Var) and str(this.name).startswith("@"):
                errors.append(f"c4:external_access: {this.name} is a stage; {_EXTERNAL_TAIL}")
            elif isinstance(this, exp.Identifier) and this.quoted and _PATH_CHARACTERS.search(this.name):
                errors.append(f'c4:external_access: "{this.name}" as a table names a file; {_EXTERNAL_TAIL}')
        for var in tree.find_all(exp.Var):
            if str(var.name).startswith("@") and not isinstance(var.parent, exp.Table):
                errors.append(f"c4:external_access: {var.name} is a stage; {_EXTERNAL_TAIL}")
    return list(dict.fromkeys(errors))


def _parse_errors(statements: list[str]) -> list[str]:
    errors = []
    for statement in statements:
        try:
            tree = sqlglot.parse_one(statement, read="snowflake")
        except SqlglotError as exc:
            errors.append(_clean(f"not valid Snowflake SQL: {exc}\n{statement.strip()}"))
            continue
        if tree is None:
            errors.append(_clean(f"empty statement: {statement.strip()}"))
            continue
        command = next(tree.find_all(exp.Command), None)
        if command is not None:
            errors.append(_clean("the Snowflake parser does not understand this statement and fell "
                                 f"back to a raw command: {command.sql(dialect='snowflake')}"))
    return errors


def _run_errors(sql_text: str, contract: dict) -> list[str]:
    errors: list[str] = []
    outputs = contract.get("outputs") or ([contract["output"]] if contract.get("output") else [])
    backend = DuckDBBackend()
    try:
        for fqn, columns in _fixtures(contract, outputs, errors):
            try:
                _create_empty(backend, fqn, columns)
            except (BackendError, ValueError) as exc:
                errors.append(_clean(f"cannot build a contract-shaped table {fqn}: {exc}"))
        if errors:
            return errors
        try:
            run_proc(backend, sql_text, ARGS)
        except (BackendError, ProcError) as exc:
            return [_clean(str(exc))]
        errors.extend(_output_errors(backend, outputs))
    finally:
        backend.close()
    return errors


def _fixtures(contract: dict, outputs: list[dict], errors: list[str]):
    """(fqn, columns) for every table the procedure expects to find already there."""
    for source in contract.get("inputs") or []:
        logical = source.get("logical")
        fqn = f"{SANDBOX_DB}.{COMPILE_SCHEMA}.{logical}" if logical else source.get("table")
        if not fqn:
            errors.append(f"contract input {source!r} has neither a `logical` name nor a `table`")
            continue
        yield fqn, source.get("columns") or []
    for output in outputs:
        if output.get("kind") != "target":
            continue
        logical = output.get("logical")
        if not logical:
            errors.append(f"contract output {output.get('stream')!r} is a target with no `logical` name")
            continue
        yield f"{SANDBOX_DB}.{WORK_SCHEMA}.{logical}", output.get("columns") or []


def _create_empty(backend: DuckDBBackend, fqn: str, columns: list[dict]) -> None:
    if not columns:
        raise ValueError("the contract lists no columns for it")
    declarations = ", ".join(f'"{column["name"]}" {column["type"]}' for column in columns)
    backend.execute(f"CREATE OR REPLACE TABLE {fqn} ({declarations})")


def _output_errors(backend: DuckDBBackend, outputs: list[dict]) -> list[str]:
    errors = []
    for output in outputs:
        if output.get("kind") != "work":
            continue
        table = output.get("table")
        if not table:
            errors.append(f"contract output {output.get('stream')!r} is a work table with no `table` name")
            continue
        if not backend.table_exists(table):
            errors.append(f"{table}: the procedure did not create this output table")
            continue
        actual = [column["name"].upper() for column in backend.table_columns(table)]
        expected = [column["name"].upper() for column in output.get("columns") or []]
        if actual != expected:
            errors.append(f"{table}: produces columns {actual} but the contract promises {expected}")
    return errors


def _clean(text: str) -> str:
    """sqlglot underlines the offending token with ANSI codes; they do not belong in JSON."""
    return _ANSI_RE.sub("", text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wf_id", help="workflow id, e.g. wf_0003")
    parser.add_argument("seg", nargs="?", help="segment id, e.g. seg_01 (omit with --target dbt)")
    parser.add_argument("--target", choices=["auto", "sql", "snowpark", "dbt"], default="auto",
                        help="procedure shape to check (default: auto, from contract.json's \"target\")")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    # Not parser.error(): main() is called directly (in-process, no subprocess) by callers that
    # expect an int back, so a usage problem here still has to return 2, not raise SystemExit.
    if args.target == "dbt" and args.seg is not None:
        print(f"--target dbt checks the whole workflow, not one segment (DV6); got {args.seg!r}",
             file=sys.stderr)
        return 2
    if args.target != "dbt" and args.seg is None:
        print("seg is required unless --target dbt (a dbt project is checked as a whole)", file=sys.stderr)
        return 2

    try:
        report = compile_check(Repo(args.root), args.wf_id, args.seg, target=args.target)
    except (FileNotFoundError, KeyError, dbt_project.DbtUnavailable, ContractError) as exc:
        where = f"{args.wf_id}/{args.seg}" if args.seg else args.wf_id
        print(f"cannot compile-check {where}: {exc}", file=sys.stderr)
        return 2
    except Exception:  # exit 2: a crash outside the checks above, which leaves no report either
        traceback.print_exc()
        return 2
    print(f"{report['status']}: {report['statements']} statements, {len(report['errors'])} errors")
    for error in report["errors"]:
        print(f"  {error}", file=sys.stderr)
    return 0 if report["status"] == "OK" else 1


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
