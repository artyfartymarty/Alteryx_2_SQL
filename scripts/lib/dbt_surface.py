"""The closed surface of a dbt migration project (final fix wave C1, design §4.3): what a project
directory may hold and what each of its files may say, judged from the files alone BEFORE any dbt
process starts. `lib.dbt_project.check_surface` is the one public entry point: `run_dbt` calls it
first and starts nothing when it finds anything, `compile_check.py --target dbt` reports what it
finds and then skips `dbt parse`, and `validate_dbt.py` FAILs every segment on it.

Why: the final review ran a Python model, an `on-run-start` hook and a `post_hook` COPY through
`compile_check.py --target dbt` (OK) and `validate_dbt.py` (PASS) on a scratch copy of wf_0007, and
all three wrote their files. dbt runs Python models, project hooks, model hooks and SQL headers, and
renders Jinja in every YAML file it reads -- so the project is judged as a closed surface:

* `dbt:surface` -- the closed file set: `dbt_project.yml`, `profiles.yml`, `README.md`,
  `translation_notes.md`, `fix_log.md`, `compile_check.json`, `review.json`, `models/**/*.sql`,
  exactly `models/sources.yml` and `models/schema.yml`, and the output directories `logs/` and
  `target/` (never read as input: `run_dbt` points dbt's own `--target-path`/`--log-path`
  elsewhere). Anything else is refused by its relative path -- any `.py` anywhere, any other YAML,
  `macros/`, `packages.yml`, ... -- and so is any symbolic link or junction, wherever it is. dbt
  itself writes nothing into the project (checked: `dbt parse` and `dbt run` through `run_dbt` on a
  scratch copy of wf_0007 left no file behind), so no dbt-written name needs allowing.
* `dbt:profiles` -- `profiles.yml` is `dbt_project.PROFILES_TEMPLATE`, byte for byte (it is
  rendered on every run, and a dbt-duckdb profile can load plugins and extensions).
* `dbt:project_yml` -- `dbt_project.yml` is the template: keys exactly `name`, `version`,
  `config-version`, `profile`, `model-paths`, `vars`; `vars` exactly `src_schema`, `tgt_schema`;
  `model-paths` exactly `[models]` (anything else would have dbt read models this gate never saw);
  no Jinja delimiter anywhere.
* `dbt:yaml` -- `models/sources.yml` and `models/schema.yml` hold only the keys a migration needs,
  no Jinja at all (dbt renders YAML at parse time) except each source's
  `schema: "{{ var('src_schema') }}"`, no `config:` anywhere, and only dbt's four built-in generic
  tests with literal arguments. YAML anchors, aliases, explicit tags and duplicate keys are refused.
* `dbt:model_jinja` -- a model's Jinja is the closed list (`config(...)`, `source('src', '<T>')`,
  `ref('<m>')`, `this`, the `is_incremental()` if/else/endif, comments), and `config(...)` takes
  only `materialized`, `incremental_strategy`, `unique_key`, `alias`, `pre_hook`, `post_hook`, each
  a string literal or a list of string literals, parsed with Python's `ast` and checked against
  Jinja's own reading of the same text. dbt splices `alias` and `unique_key` into SQL unquoted, so
  both must be plain identifiers; `materialized` and `incremental_strategy` are closed sets
  (dbt-duckdb's `external` materialisation writes a file, so it is not among them).
* `dbt:hook_sql` -- a `pre_hook`/`post_hook` is ONE string holding ONE `DELETE`, `UPDATE`, `INSERT`
  or `TRUNCATE` whose only Jinja is `{{ this }}` and whose every table reference (subqueries
  included) is `{{ this }}` or a CTE of the same statement: no other table, no table-valued
  function, no function that reads files, settings or the network (`DENIED_FUNCTION`), no function
  sqlglot does not know, no comment. A PreSQL/PostSQL that touches another table cannot be
  expressed on the dbt target at all.
* `dbt:model_sql` -- a model, rendered the way dbt renders it (Jinja itself, with `source()`,
  `ref()` and `this` as unforgeable placeholders, once with `is_incremental()` true and once false),
  is exactly ONE query whose every table reference is a placeholder or a CTE visible where it is
  used, with the same function rules as a hook.

Both SQL judges run twice, on two parsers, and either one refusing is enough: sqlglot (DuckDB
dialect) as the rulings require, and DuckDB's own parser, the engine a local run executes on
(`duckdb.extract_statements` for the statement count and type, `json_serialize_sql` for a model's
table and function references, `duckdb.tokenize` for a hook's function names and comments) -- so a
construct the two parsers read differently is refused rather than trusted. Nothing is executed:
parsing only, on an in-memory connection.

A refusal names the file and the offending construct, bounded, and never quotes more of the file.
Nothing here has run against Snowflake; the rules are about what a LOCAL dbt run would execute.
"""
from __future__ import annotations

import ast
import json
import os
import re
import secrets
from pathlib import Path

import duckdb
import sqlglot
import yaml
from jinja2 import StrictUndefined
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .dbt_project import PROFILES_TEMPLATE, bounded

# --- the closed file set (dbt:surface) --------------------------------------------------------------

TOP_FILES = frozenset({"dbt_project.yml", "profiles.yml", "README.md", "translation_notes.md", "fix_log.md",
                       "compile_check.json", "review.json"})
MODEL_YAML = frozenset({"sources.yml", "schema.yml"})
OUTPUT_DIRS = frozenset({"logs", "target"})
_MODEL_FILE_RE = re.compile(r"^[A-Za-z0-9_]+\.sql$")
_MODEL_DIR_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_OUTSIDE = "is not part of a migration project's closed file set"

# --- dbt_project.yml (dbt:project_yml) --------------------------------------------------------------

PROJECT_KEYS = frozenset({"name", "version", "config-version", "profile", "model-paths", "vars"})
PROJECT_VARS = frozenset({"src_schema", "tgt_schema"})
_JINJA_OPENERS = ("{{", "{%", "{#")
_JINJA_DELIMITERS = ("{{", "}}", "{%", "%}", "{#", "#}")

# --- the two YAML files (dbt:yaml) ------------------------------------------------------------------

_SRC_SCHEMA_RE = re.compile(r"^\{\{\s*var\('src_schema'\)\s*\}\}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: A `unique_key` that needs quotes (a reserved word such as ORDER; live hardening L8 fix round 1): the
#: quotes are part of the value dbt splices, and nothing inside can end them or open Jinja.
_QUOTED_KEY_RE = re.compile(r'^"[A-Za-z0-9_$ ]+"$')
_SOURCE_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_RELATIONSHIPS_TO_RE = re.compile(r"^ref\('[A-Za-z0-9_]+'\)$")
_BARE_TESTS = frozenset({"not_null", "unique"})

# --- model Jinja (dbt:model_jinja) ------------------------------------------------------------------
#
# A small tokenizer over `{{`/`}}`, `{%`/`%}` and `{#`/`#}` delimiters -- not a real Jinja parser --
# because the allow-list is short and fixed. `config(...)`'s own arguments may embed a second, nested
# `{{ this }}` pair (dbt's `pre_hook="delete from {{ this }} where 1=0"` idiom), so `{{`/`}}` nesting
# is tracked with a depth counter; `{%`/`{#` spans never nest here. Anything the tokenizer reads
# differently from Jinja is refused, never trusted: the rendering below is Jinja's own.

_JINJA_TOKEN_RE = re.compile(r"\{\{|\}\}|\{%|%\}|\{#|#\}")
_JINJA_CLOSE = {"{{": "}}", "{%": "%}", "{#": "#}"}
_JINJA_SOURCE_RE = re.compile(r"source\(\s*'src'\s*,\s*'[A-Za-z0-9_]+'\s*\)")
_JINJA_REF_RE = re.compile(r"ref\(\s*'[A-Za-z0-9_]+'\s*\)")
_JINJA_THIS_RE = re.compile(r"\{\{\s*this\s*\}\}")
_IF_TAG, _ELSE_TAG, _ENDIF_TAG = "if is_incremental()", "else", "endif"
_JINJA_IF_TAGS = frozenset({_IF_TAG, _ELSE_TAG, _ENDIF_TAG})

CONFIG_KEYS = frozenset({"materialized", "incremental_strategy", "unique_key", "alias", "pre_hook", "post_hook"})
HOOK_KEYS = frozenset({"pre_hook", "post_hook"})
MATERIALIZATIONS = frozenset({"table", "view", "incremental", "ephemeral"})
INCREMENTAL_STRATEGIES = frozenset({"append", "merge", "delete+insert"})
_CONFIG_TOKEN_OPERATORS = frozenset({"(", ")", "=", ",", "[", "]"})

# --- the SQL judges (dbt:hook_sql, dbt:model_sql) ---------------------------------------------------

#: Functions that read files, settings, the environment or the network, or run SQL from a string
#: (DuckDB's names; matched on the lower-cased name, schema stripped).
DENIED_FUNCTION = re.compile(
    r"^(?:read_\w*|\w*_scan|glob|getenv|current_setting|sniff_csv|parquet_\w*|load\w*|install\w*|http\w*"
    r"|query|query_table|duckdb_\w*|pragma_\w*|which_secret|json_execute_serialized_sql|iceberg_\w*|delta_\w*"
    r"|sqlite_\w*|postgres_\w*|mysql_\w*|st_read\w*|\w*_metadata|checkpoint|force_checkpoint|enable_\w*"
    r"|disable_\w*)$")
_HOOK_STATEMENTS = (exp.Delete, exp.Update, exp.Insert, exp.TruncateTable)
_DUCK_HOOK_TYPES = frozenset({duckdb.StatementType.DELETE, duckdb.StatementType.UPDATE,
                              duckdb.StatementType.INSERT})
_DUCK_TABLE_REFS = frozenset({"BASE_TABLE", "SUBQUERY", "JOIN", "EMPTY", "EXPRESSION_LIST"})
#: sqlglot's table-valued nodes a model or hook may still use: a `VALUES` list, and `unnest(...)` in
#: a select list (sqlglot's `Explode`), which reads nothing but its argument.
_ALLOWED_UDTF = (exp.Values, exp.Explode)


def shown(text: str, limit: int = 80) -> str:
    """A name from the project, for a message: printable, bounded, never more than the construct."""
    text = str(text)
    if not text.isprintable():
        text = repr(text)
    return bounded(text, limit)


# --- the entry point -------------------------------------------------------------------------------


def surface_errors(project: Path) -> list[str]:
    """Every `dbt:<check>` the project's own files fail, in a stable order; `[]` when the project
    stays inside the closed surface. A missing file is not a surface error (`dbt:layout` says so)."""
    project = Path(project)
    if is_link(project):
        return ["dbt:surface: the project directory itself is a symbolic link or junction"]
    if not project.is_dir():
        return []
    errors, models, present = _walk(project)
    if "profiles.yml" in present:
        errors += _profile_errors(project / "profiles.yml")
    if "dbt_project.yml" in present:
        errors += _project_yml_errors(project / "dbt_project.yml")
    if "models/sources.yml" in present:
        errors += _sources_yml_errors(project / "models" / "sources.yml")
    if "models/schema.yml" in present:
        errors += _schema_yml_errors(project / "models" / "schema.yml")
    for path in models:
        errors += _model_errors(project, path)
    return errors


def model_jinja_errors(project: Path) -> list[str]:
    """Only the `dbt:model_jinja` errors of every model (compile_check's `_dbt_jinja_errors`)."""
    project = Path(project)
    if not project.is_dir() or is_link(project):
        return []
    _, models, _ = _walk(project)
    return [e for path in models for e in _model_errors(project, path) if e.startswith("dbt:model_jinja: ")]


def load_yaml(path: Path):
    """A project YAML file loaded the strict way (no link, no anchor/alias/tag, no duplicate key),
    or `None` when it cannot be -- the surface check says why. For compile_check's own file-level
    checks, which must never read through a link or expand an alias bomb."""
    if is_link(path) or not path.is_file():
        return None
    try:
        return _strict_yaml(_read_text(path))
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
        return None


# --- dbt:surface ------------------------------------------------------------------------------------


def is_link(path: Path) -> bool:
    """A symbolic link or a junction (or something that cannot even be asked): never followed."""
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def _walk(project: Path) -> tuple[list[str], list[Path], set[str]]:
    """(errors, the model SQL files to judge, the relative paths of the allowed YAML/profile files
    present). Never follows a link: a link is refused where it stands and not entered."""
    errors: list[str] = []
    models: list[Path] = []
    present: set[str] = set()

    def refuse(rel: str, why: str) -> None:
        errors.append(f"dbt:surface: {shown(rel, 120)} {why}")

    def visit(directory: Path, parts: tuple[str, ...]) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            refuse("/".join(parts) or ".", f"cannot be listed ({type(exc).__name__})")
            return
        for entry in entries:
            rel_parts = parts + (entry.name,)
            rel = "/".join(rel_parts)
            try:
                link = entry.is_symlink() or entry.is_junction()
            except OSError:
                link = True
            if link:
                refuse(rel, "is a symbolic link or junction; a migration project holds only its own files")
                continue
            if entry.name.lower().endswith(".py"):
                refuse(rel, "is Python; a migration project holds none (dbt would run a Python model)")
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            if not is_dir and not entry.is_file(follow_symlinks=False):
                refuse(rel, "is not a regular file or directory")
                continue
            if rel_parts[0] in OUTPUT_DIRS and (is_dir or len(rel_parts) > 1):
                if is_dir:                           # dbt's own output; never read as input
                    visit(Path(entry.path), rel_parts)
                continue
            if len(rel_parts) == 1:
                if is_dir and entry.name == "models":
                    visit(Path(entry.path), rel_parts)
                elif not is_dir and entry.name in TOP_FILES:
                    present.add(rel)
                else:
                    refuse(rel, _OUTSIDE)
                continue
            if is_dir:                               # under models/
                if _MODEL_DIR_RE.match(entry.name):
                    visit(Path(entry.path), rel_parts)
                else:
                    refuse(rel, _OUTSIDE)
            elif len(rel_parts) == 2 and entry.name in MODEL_YAML:
                present.add(rel)
            elif _MODEL_FILE_RE.match(entry.name):
                models.append(Path(entry.path))
            else:
                refuse(rel, _OUTSIDE)

    visit(project, ())
    return errors, models, present


def _read_text(path: Path) -> str:
    """UTF-8, newlines normalised to `\\n` -- Jinja normalises them the same way when dbt renders."""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


# --- dbt:profiles -----------------------------------------------------------------------------------


def _profile_errors(path: Path) -> list[str]:
    """Byte for byte the template; the message never quotes the file, so a credential pasted into
    it never reaches a report, a log or an agent's context."""
    try:
        text = _read_text(path)
    except (OSError, UnicodeDecodeError):
        text = None
    if text != PROFILES_TEMPLATE:
        return ["dbt:profiles: profiles.yml is not the template in scripts/lib/dbt_project.py "
                "(PROFILES_TEMPLATE); it may never carry a credential"]
    return []


# --- YAML, loaded strictly ----------------------------------------------------------------------------


class _StrictLoader(yaml.SafeLoader):
    """PyYAML's SafeLoader (the loader dbt itself uses), refusing a duplicate key instead of
    silently keeping the last one."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False):
    keys = []
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in keys
        except TypeError:
            duplicate = False
        if duplicate:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {shown(key, 40)}",
                                                    key_node.start_mark)
        keys.append(key)
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _strict_yaml(text: str):
    """The document, or ValueError/YAMLError: no anchor, alias or explicit tag (a YAML alias can
    expand one small file into an enormous document), no duplicate key."""
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise ValueError("uses a YAML anchor or alias")
        if getattr(event, "tag", None) is not None and not isinstance(event, (yaml.DocumentStartEvent,)):
            raise ValueError("uses an explicit YAML tag")
    return yaml.load(text, Loader=_StrictLoader)


def _yaml_doc(path: Path, rel: str, check: str) -> tuple[object, list[str]]:
    try:
        text = _read_text(path)
    except (OSError, UnicodeDecodeError):
        return None, [f"{check}: {rel} is not a readable UTF-8 file"]
    try:
        return (text, _strict_yaml(text)), []
    except ValueError as exc:
        return None, [f"{check}: {rel} {exc}"]
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        return None, [f"{check}: {rel} is not valid YAML{where}"]


def _strings(value, path: str = ""):
    """Every string key and value in a YAML document, with a readable path to it."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield f"{path}.{key}" if path else key, key
            yield from _strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]")


def _has_jinja(text: str) -> bool:
    return any(delimiter in text for delimiter in _JINJA_OPENERS)


def _keys_error(check: str, rel: str, where: str, mapping: dict, allowed: frozenset) -> str | None:
    extra = sorted(shown(key, 40) for key in mapping if not isinstance(key, str) or key not in allowed)
    if extra:
        return (f"{check}: {rel} {where} may not carry {', '.join(extra)} "
                f"(only {', '.join(sorted(allowed))})")
    return None


# --- dbt:project_yml ------------------------------------------------------------------------------------


def _project_yml_errors(path: Path) -> list[str]:
    rel, check = "dbt_project.yml", "dbt:project_yml"
    loaded, errors = _yaml_doc(path, rel, check)
    if loaded is None:
        return errors
    text, doc = loaded
    if any(opener in text for opener in _JINJA_OPENERS):
        errors.append(f"{check}: {rel} holds a Jinja delimiter; the project file is the template and renders nothing")
    if not isinstance(doc, dict):
        return errors + [f"{check}: {rel} is not a mapping"]
    keys = {key for key in doc if isinstance(key, str)}
    if keys != PROJECT_KEYS or len(keys) != len(doc):
        extra = sorted(shown(key, 40) for key in doc if key not in PROJECT_KEYS)
        missing = sorted(PROJECT_KEYS - keys)
        detail = "; ".join(part for part in (f"refused {', '.join(extra)}" if extra else "",
                                             f"missing {', '.join(missing)}" if missing else "") if part)
        errors.append(f"{check}: {rel} keys must be exactly {', '.join(sorted(PROJECT_KEYS))} ({detail})")
    if doc.get("model-paths") != ["models"]:
        errors.append(f"{check}: {rel} model-paths must be exactly [models]; dbt would read models this gate never saw")
    variables = doc.get("vars")
    if not isinstance(variables, dict) or set(variables) != PROJECT_VARS:
        errors.append(f"{check}: {rel} vars must be exactly src_schema and tgt_schema")
    return errors


# --- dbt:yaml -------------------------------------------------------------------------------------------


def _sources_yml_errors(path: Path) -> list[str]:
    rel, check = "models/sources.yml", "dbt:yaml"
    loaded, errors = _yaml_doc(path, rel, check)
    if loaded is None:
        return errors
    text, doc = loaded
    if not isinstance(doc, dict):
        return errors + [f"{check}: {rel} is not a mapping"]
    add = errors.append
    if (problem := _keys_error(check, rel, "at the top level", doc, frozenset({"version", "sources"}))):
        add(problem)
    if doc.get("version") != 2:
        add(f"{check}: {rel} version must be 2")
    sources = doc.get("sources") or []
    if not isinstance(sources, list):
        return errors + [f"{check}: {rel} sources must be a list"]
    permitted = 0
    for i, source in enumerate(sources):
        where = f"sources[{i}]"
        if not isinstance(source, dict):
            add(f"{check}: {rel} {where} is not a mapping")
            continue
        if (problem := _keys_error(check, rel, where, source, frozenset({"name", "schema", "description", "tables"}))):
            add(problem)
        if not isinstance(source.get("name"), str):
            add(f"{check}: {rel} {where} needs a string name")
        schema = source.get("schema")
        if isinstance(schema, str) and _SRC_SCHEMA_RE.match(schema):
            permitted += 1
        else:
            add(f"{check}: {rel} {where} schema must be exactly \"{{{{ var('src_schema') }}}}\"")
        if "description" in source and not isinstance(source["description"], str):
            add(f"{check}: {rel} {where} description must be a string")
        tables = source.get("tables") or []
        if not isinstance(tables, list):
            add(f"{check}: {rel} {where} tables must be a list")
            continue
        for j, table in enumerate(tables):
            twhere = f"{where}.tables[{j}]"
            if not isinstance(table, dict):
                add(f"{check}: {rel} {twhere} is not a mapping")
                continue
            if (problem := _keys_error(check, rel, twhere, table, frozenset({"name", "description", "columns"}))):
                add(problem)
            if not (isinstance(table.get("name"), str) and _SOURCE_TABLE_RE.match(table["name"])):
                add(f"{check}: {rel} {twhere} name must be a plain identifier (dbt quotes it into SQL)")
            if "description" in table and not isinstance(table["description"], str):
                add(f"{check}: {rel} {twhere} description must be a string")
            errors.extend(_column_errors(check, rel, twhere, table.get("columns"), frozenset({"name", "description"})))
    for where, value in _strings(doc):
        if _has_jinja(value) and not (where.endswith(".schema") and _SRC_SCHEMA_RE.match(value)):
            add(f"{check}: {rel} {shown(where, 60)} holds Jinja; dbt renders it at parse time")
    if text.count("{{") != permitted or "{%" in text or "{#" in text:
        add(f"{check}: {rel} holds Jinja outside the one permitted schema value (a comment counts)")
    return errors


def _column_errors(check: str, rel: str, where: str, columns, allowed: frozenset) -> list[str]:
    errors: list[str] = []
    if columns is None:
        return errors
    if not isinstance(columns, list):
        return [f"{check}: {rel} {where}.columns must be a list"]
    for k, column in enumerate(columns):
        cwhere = f"{where}.columns[{k}]"
        if not isinstance(column, dict):
            errors.append(f"{check}: {rel} {cwhere} is not a mapping")
            continue
        if (problem := _keys_error(check, rel, cwhere, column, allowed)):
            errors.append(problem)
        if not isinstance(column.get("name"), str):
            errors.append(f"{check}: {rel} {cwhere} needs a string name")
        if "description" in column and not isinstance(column["description"], str):
            errors.append(f"{check}: {rel} {cwhere} description must be a string")
        for key in ("data_tests", "tests"):
            if key in column and key in allowed:
                errors.extend(_test_errors(check, rel, f"{cwhere}.{key}", column[key]))
    return errors


def _test_errors(check: str, rel: str, where: str, tests) -> list[str]:
    """dbt's four built-in generic tests only, each with literal arguments: dbt renders every test
    argument as Jinja, and wraps one that looks like `env_var(...)`/`ref(...)`/`var(...)` in `{{ }}`
    before it does -- so an argument string may hold no `(` at all, except a `relationships` `to`
    that is exactly `ref('<model>')`."""
    if not isinstance(tests, list):
        return [f"{check}: {rel} {where} must be a list"]
    errors: list[str] = []
    for index, test in enumerate(tests):
        twhere = f"{where}[{index}]"
        if isinstance(test, str):
            if test not in _BARE_TESTS:
                errors.append(f"{check}: {rel} {twhere} {shown(test, 40)} is not a built-in test "
                              f"(not_null, unique, accepted_values, relationships)")
            continue
        if not (isinstance(test, dict) and len(test) == 1):
            errors.append(f"{check}: {rel} {twhere} is not one built-in test")
            continue
        (name, arguments), = test.items()
        if name == "accepted_values" and isinstance(arguments, dict):
            if (problem := _keys_error(check, rel, twhere, arguments, frozenset({"values", "quote"}))):
                errors.append(problem)
            values = arguments.get("values")
            if not (isinstance(values, list) and values and all(
                    isinstance(v, (int, float, bool)) or (isinstance(v, str) and "(" not in v) for v in values)):
                errors.append(f"{check}: {rel} {twhere} accepted_values values must be a list of plain literals")
            if "quote" in arguments and not isinstance(arguments["quote"], bool):
                errors.append(f"{check}: {rel} {twhere} accepted_values quote must be true or false")
        elif name == "relationships" and isinstance(arguments, dict):
            if set(arguments) != {"to", "field"}:
                errors.append(f"{check}: {rel} {twhere} relationships takes exactly to and field")
            if not (isinstance(arguments.get("to"), str) and _RELATIONSHIPS_TO_RE.match(arguments["to"])):
                errors.append(f"{check}: {rel} {twhere} relationships to must be exactly ref('<model>')")
            if not (isinstance(arguments.get("field"), str) and _IDENTIFIER_RE.match(arguments["field"])):
                errors.append(f"{check}: {rel} {twhere} relationships field must be a plain identifier")
        else:
            errors.append(f"{check}: {rel} {twhere} {shown(name, 40)} is not a built-in test with literal "
                          f"arguments (not_null and unique take none)")
    return errors


def _schema_yml_errors(path: Path) -> list[str]:
    rel, check = "models/schema.yml", "dbt:yaml"
    loaded, errors = _yaml_doc(path, rel, check)
    if loaded is None:
        return errors
    text, doc = loaded
    if not isinstance(doc, dict):
        return errors + [f"{check}: {rel} is not a mapping"]
    add = errors.append
    if (problem := _keys_error(check, rel, "at the top level", doc, frozenset({"version", "models"}))):
        add(problem)
    if doc.get("version") != 2:
        add(f"{check}: {rel} version must be 2")
    models = doc.get("models") or []
    if not isinstance(models, list):
        return errors + [f"{check}: {rel} models must be a list"]
    for i, model in enumerate(models):
        where = f"models[{i}]"
        if not isinstance(model, dict):
            add(f"{check}: {rel} {where} is not a mapping")
            continue
        if (problem := _keys_error(check, rel, where, model, frozenset({"name", "description", "columns"}))):
            add(problem)
        if not isinstance(model.get("name"), str):
            add(f"{check}: {rel} {where} needs a string name")
        if "description" in model and not isinstance(model["description"], str):
            add(f"{check}: {rel} {where} description must be a string")
        errors.extend(_column_errors(check, rel, where, model.get("columns"),
                                     frozenset({"name", "description", "data_tests", "tests"})))
    for where, value in _strings(doc):
        if _has_jinja(value):
            add(f"{check}: {rel} {shown(where, 60)} holds Jinja; dbt renders it at parse time")
    if any(opener in text for opener in _JINJA_OPENERS):
        add(f"{check}: {rel} holds a Jinja delimiter (a comment counts); schema.yml renders nothing")
    return errors


# --- models: dbt:model_jinja, dbt:hook_sql, dbt:model_sql ------------------------------------------------


def jinja_spans(text: str) -> list[tuple[str, str]]:
    """Every `{{ … }}`/`{% … %}`/`{# … #}` span in `text` as `(open_token, raw_span_text)`, outermost
    first: a `{{ }}` span may itself contain nested `{{ }}` pairs (the hook-string idiom), tracked
    with a depth counter rather than a real parser."""
    tokens = list(_JINJA_TOKEN_RE.finditer(text))
    spans: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i].group()
        if token not in _JINJA_CLOSE:
            i += 1
            continue
        close = _JINJA_CLOSE[token]
        depth, start, j = 1, tokens[i].start(), i + 1
        while j < len(tokens) and depth > 0:
            if tokens[j].group() == token:
                depth += 1
            elif tokens[j].group() == close:
                depth -= 1
            j += 1
        spans.append((token, text[start:tokens[j - 1].end()]))
        i = j
    return spans


def span_inner(kind: str, raw: str) -> str:
    """The construct inside a span: delimiters and one whitespace-control mark (`-`/`+`) on either
    side stripped (real dbt accepts `{{- this -}}` on every allowed form), then whitespace."""
    inner = raw[len(kind):]
    if inner.endswith(_JINJA_CLOSE[kind]):
        inner = inner[:-len(_JINJA_CLOSE[kind])]
    if inner[:1] in ("-", "+"):
        inner = inner[1:]
    if inner[-1:] in ("-", "+"):
        inner = inner[:-1]
    return inner.strip()


def _construct_ok(kind: str, raw: str, inner: str) -> bool:
    if not raw.endswith(_JINJA_CLOSE[kind]):
        return False                                 # an unclosed span
    if kind == "{#":
        return True
    if kind == "{%":
        return inner in _JINJA_IF_TAGS
    return inner == "this" or bool(_JINJA_SOURCE_RE.fullmatch(inner) or _JINJA_REF_RE.fullmatch(inner))


def _is_config(kind: str, inner: str) -> bool:
    return kind == "{{" and inner.startswith("config(") and inner.endswith(")")


def _model_errors(project: Path, path: Path) -> list[str]:
    rel = path.relative_to(project).as_posix()
    try:
        text = _read_text(path)
    except (OSError, UnicodeDecodeError):
        return [f"dbt:model_sql: {rel} is not a readable UTF-8 file"]
    spans = jinja_spans(text)
    jinja: list[str] = []
    hooks: list[str] = []
    for kind, raw in spans:
        inner = span_inner(kind, raw)
        if raw.endswith(_JINJA_CLOSE[kind]) and _is_config(kind, inner):
            config_jinja, config_hooks = _config_errors(rel, inner[len("config("):-1])
            jinja += config_jinja
            hooks += config_hooks
        elif not _construct_ok(kind, raw, inner):
            jinja.append(f"dbt:model_jinja: {rel} uses {shown(' '.join(raw.split()), 160)}, "
                         f"which a migration model may not")
    if jinja:
        return jinja + hooks                         # the SQL cannot be rendered faithfully
    return hooks + _model_sql_errors(rel, text, spans)


# --- config(...) (dbt:model_jinja) and its hooks (dbt:hook_sql) ---------------------------------------


def _config_errors(rel: str, args: str) -> tuple[list[str], list[str]]:
    """(model_jinja errors, hook_sql errors) of one `config(<args>)`."""
    refuse = f"dbt:model_jinja: {rel}'s config(...)"
    try:
        call = ast.parse(f"config({args})", mode="eval").body
    except SyntaxError:
        return [f"{refuse} is not a list of key=literal arguments"], []
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "config"):
        return [f"{refuse} is not a list of key=literal arguments"], []
    if call.args or any(keyword.arg is None for keyword in call.keywords):
        return [f"{refuse} takes keyword arguments only"], []
    names = [keyword.arg for keyword in call.keywords]
    if len(names) != len(set(names)):
        return [f"{refuse} sets the same key twice"], []
    parsed: dict[str, object] = {}
    errors: list[str] = []
    for keyword in call.keywords:
        if keyword.arg not in CONFIG_KEYS:
            errors.append(f"{refuse} may not set {shown(keyword.arg, 40)} (only {', '.join(sorted(CONFIG_KEYS))})")
            continue
        value = _literal(keyword.value)
        if value is None:
            errors.append(f"{refuse} sets {keyword.arg} to something other than a string literal or a list of them")
            continue
        parsed[keyword.arg] = value
    if errors:
        return errors, []
    jinja_values = _jinja_config_values(args)
    if jinja_values != parsed:
        return [f"{refuse} reads differently to Jinja than as plain literals; write plain quoted strings"], []
    hooks: list[str] = []
    for key, value in parsed.items():
        if key in HOOK_KEYS:
            if not isinstance(value, str):
                hooks.append(f"dbt:hook_sql: {rel} {key} must be ONE string literal holding ONE statement")
            else:
                hooks += [f"dbt:hook_sql: {rel} {key} {problem}" for problem in hook_problems(value)]
        elif (problem := _config_value_problem(key, value)):
            errors.append(f"{refuse} {problem}")
    return errors, hooks


def _literal(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.List) and node.elts and all(
            isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.elts):
        return [item.value for item in node.elts]
    return None


def _jinja_config_values(args: str) -> dict | None:
    """What Jinja -- the reader dbt uses -- makes of the same argument text, or None when Jinja reads
    anything but names, `=`, `,`, brackets and string literals there (then nothing is evaluated)."""
    env = SandboxedEnvironment(undefined=StrictUndefined)
    source = "{{ config(" + args + ") }}"
    try:
        tokens = [(kind, value) for _, kind, value in env.lex(source) if kind != "whitespace"]
    except TemplateError:
        return None
    body = tokens[3:-2]                              # between `{{ config (` and `) }}`
    if tokens[:3] != [("variable_begin", "{{"), ("name", "config"), ("operator", "(")] or \
            tokens[-2:] != [("operator", ")"), ("variable_end", "}}")]:
        return None
    for index, (kind, value) in enumerate(body):
        if kind == "string":
            continue
        if kind == "operator" and value in _CONFIG_TOKEN_OPERATORS:
            continue
        if kind == "name" and index + 1 < len(body) and body[index + 1] == ("operator", "="):
            continue
        return None
    captured: dict = {}

    def config(*positional, **kwargs):
        captured.update(kwargs)
        return ""

    try:
        env.from_string(source).render(config=config)
    except TemplateError:
        return None
    return captured


def _config_value_problem(key: str, value) -> str | None:
    if key == "materialized":
        if not (isinstance(value, str) and value in MATERIALIZATIONS):
            return f"materialized must be one of {', '.join(sorted(MATERIALIZATIONS))}"
    elif key == "incremental_strategy":
        if not (isinstance(value, str) and value in INCREMENTAL_STRATEGIES):
            return f"incremental_strategy must be one of {', '.join(sorted(INCREMENTAL_STRATEGIES))}"
    elif key == "alias":
        if not (isinstance(value, str) and _IDENTIFIER_RE.match(value)):
            return "alias must be a plain identifier (dbt splices it into SQL)"
    elif key == "unique_key":
        keys = [value] if isinstance(value, str) else value
        if not all(isinstance(k, str) and (_IDENTIFIER_RE.match(k) or _QUOTED_KEY_RE.match(k)) for k in keys):
            return ("unique_key must be plain identifiers, or double-quoted names of letters, digits, _, $ and "
                    "spaces (dbt splices each into SQL as written)")
    return None


def hook_problems(hook: str) -> list[str]:
    """Why one pre_hook/post_hook string is refused, or []. A blank hook runs nothing (and
    `dbt:hooks` still refuses it where a PreSQL/PostSQL needs a real one)."""
    if not hook.strip():
        return []
    if any(delimiter in _JINJA_THIS_RE.sub("", hook) for delimiter in _JINJA_DELIMITERS):
        return ["holds Jinja other than {{ this }}"]
    this = _placeholder("this")
    sql = _JINJA_THIS_RE.sub(f'"{this}"', hook)
    if "--" in sql or "/*" in sql:
        return ["holds a comment; a hook is one plain statement"]
    problems: list[str] = []
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except (SqlglotError, ValueError, RecursionError):
        return ["does not parse as one DuckDB statement"]
    if len(statements) != 1:
        return [f"holds {len(statements)} statements; a hook is ONE statement"]
    statement = statements[0]
    if not isinstance(statement, _HOOK_STATEMENTS):
        return [f"is a {shown(statement.key.upper(), 40)}; a hook may only DELETE, UPDATE, INSERT or TRUNCATE"]
    problems += _reference_problems(statement, {this: "{{ this }}"})
    problems += _duckdb_hook_problems(sql)
    return list(dict.fromkeys(problems))


# --- the SQL judges -------------------------------------------------------------------------------------


def _placeholder(kind: str) -> str:
    return f"mig_{kind}_{secrets.token_hex(6)}"


def _function_names(node: exp.Func) -> list[str]:
    if isinstance(node, (exp.Anonymous, exp.AnonymousAggFunc)):
        return [str(node.name).lower()]
    return [name.lower() for name in type(node).sql_names()]


def _visible_ctes(node: exp.Expression) -> set[str]:
    """The CTE names a table reference at `node` can see: every CTE of an enclosing WITH, except
    that inside a CTE's own body only the CTEs defined BEFORE it (so a forward or recursive
    reference falls through to a real table, and is refused)."""
    names: set[str] = set()
    child, parent = node, node.parent
    while parent is not None:
        if isinstance(parent, exp.With):
            if isinstance(child, exp.CTE):
                index = next(i for i, cte in enumerate(parent.expressions) if cte is child)
                names.update(cte.alias_or_name.lower() for cte in parent.expressions[:index])
        else:
            with_ = parent.args.get("with_") or parent.args.get("with")
            if isinstance(with_, exp.With) and with_ is not child:
                names.update(cte.alias_or_name.lower() for cte in with_.expressions)
        child, parent = parent, parent.parent
    return names


def _reference_problems(statement: exp.Expression, placeholders: dict[str, str]) -> list[str]:
    """sqlglot's reading: every table is a placeholder or a visible CTE, no table-valued function, no
    function it does not know, none on the deny-list."""
    problems: list[str] = []
    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            name = _function_names(table.this)[0] if isinstance(table.this, exp.Func) else table.this.key
            problems.append(f"reads a table-valued function ({shown(name, 40)})")
            continue
        name = table.name.lower()
        qualified = bool(table.args.get("db") or table.args.get("catalog"))
        if not qualified and (name in placeholders or name in _visible_ctes(table)):
            continue
        readable = placeholders.get(name, table.name)
        prefix = ".".join(part.name for part in (table.args.get("catalog"), table.args.get("db")) if part)
        problems.append(f"reads table {shown(f'{prefix}.{readable}' if prefix else readable, 80)} other than "
                        f"through source(), ref() or {{{{ this }}}}")
    for node in statement.find_all(exp.UDTF):
        if not isinstance(node, _ALLOWED_UDTF):
            problems.append(f"uses a table-valued construct ({shown(node.key, 40)})")
    for node in statement.find_all(exp.Func):
        names = _function_names(node)
        if isinstance(node, (exp.Anonymous, exp.AnonymousAggFunc)):
            problems.append(f"calls {shown(names[0], 40)}(), a function sqlglot does not know")
        elif (denied := next((n for n in names if DENIED_FUNCTION.match(n)), None)):
            problems.append(f"calls {shown(denied, 40)}(), which reads files, settings or the network")
    return problems


def _duckdb_statements(sql: str):
    try:
        return duckdb.extract_statements(sql)
    except duckdb.Error:
        return None


def _duckdb_hook_problems(sql: str) -> list[str]:
    """DuckDB's own reading of a hook: one DELETE/UPDATE/INSERT (TRUNCATE is a DELETE there), no
    comment, and no denied name applied to `(`."""
    statements = _duckdb_statements(sql)
    if statements is None:
        return ["does not parse as DuckDB SQL"]
    if len(statements) != 1 or statements[0].type not in _DUCK_HOOK_TYPES:
        return ["is not one DELETE, UPDATE, INSERT or TRUNCATE to DuckDB's own parser"]
    problems: list[str] = []
    data = sql.encode("utf-8")                       # DuckDB's token offsets count bytes
    tokens = duckdb.tokenize(sql)
    for index, (start, kind) in enumerate(tokens):
        end = tokens[index + 1][0] if index + 1 < len(tokens) else len(data)
        if kind == duckdb.token_type.comment:
            problems.append("holds a comment; a hook is one plain statement")
        elif kind in (duckdb.token_type.identifier, duckdb.token_type.keyword) and index + 1 < len(tokens) \
                and data[tokens[index + 1][0]:tokens[index + 1][0] + 1] == b"(":
            name = data[start:end].decode("utf-8", "replace").strip().strip('"').lower()
            if DENIED_FUNCTION.match(name):
                problems.append(f"calls {shown(name, 40)}(), which reads files, settings or the network")
    return problems


def _duckdb_select_problems(sql: str, placeholders: dict[str, str]) -> list[str]:
    """DuckDB's own reading of a model: one SELECT, and in its parse tree every base table a
    placeholder or a visible CTE, no table function, no denied or schema-qualified function."""
    statements = _duckdb_statements(sql)
    if statements is None:
        return ["does not parse as DuckDB SQL"]
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        return ["is not one SELECT statement to DuckDB's own parser"]
    connection = duckdb.connect(":memory:")
    try:
        tree = json.loads(connection.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    except (duckdb.Error, ValueError, TypeError):
        return ["cannot be read back from DuckDB's own parser"]
    finally:
        connection.close()
    if not isinstance(tree, dict) or tree.get("error"):
        return ["cannot be read back from DuckDB's own parser"]
    problems: list[str] = []
    _duck_walk(tree.get("statements"), frozenset(), placeholders, problems)
    return problems


def _duck_walk(node, visible: frozenset, placeholders: dict[str, str], problems: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _duck_walk(item, visible, placeholders, problems)
        return
    if not isinstance(node, dict):
        return
    cte_map = node.get("cte_map")
    ctes = (cte_map.get("map") or []) if isinstance(cte_map, dict) else []
    names = [str(cte.get("key", "")).lower() for cte in ctes if isinstance(cte, dict)]
    for index, cte in enumerate(ctes):
        _duck_walk(cte.get("value") if isinstance(cte, dict) else None, visible | frozenset(names[:index]),
                   placeholders, problems)
    inside = visible | frozenset(names)
    kind = node.get("type")
    if "sample" in node and "class" not in node and isinstance(kind, str) and not kind.endswith("_NODE"):
        if kind not in _DUCK_TABLE_REFS:
            problems.append(f"reads a {shown(kind.lower(), 40)} table reference")
        elif kind == "BASE_TABLE":
            name = str(node.get("table_name", "")).lower()
            qualified = bool(node.get("schema_name") or node.get("catalog_name"))
            if qualified or name not in placeholders and name not in inside:
                readable = placeholders.get(name, str(node.get("table_name", "")))
                prefix = ".".join(str(node[k]) for k in ("catalog_name", "schema_name") if node.get(k))
                problems.append(f"reads table {shown(f'{prefix}.{readable}' if prefix else readable, 80)} other "
                                f"than through source(), ref() or {{{{ this }}}}")
    if "function_name" in node:
        name = str(node["function_name"]).lower()
        if node.get("catalog") or node.get("schema") not in (None, "", "main"):
            problems.append(f"calls a schema-qualified function ({shown(name, 40)})")
        elif DENIED_FUNCTION.match(name):
            problems.append(f"calls {shown(name, 40)}(), which reads files, settings or the network")
    for key, value in node.items():
        if key != "cte_map":
            _duck_walk(value, inside, placeholders, problems)


# --- dbt:model_sql --------------------------------------------------------------------------------------


def _if_structure_error(spans: list[tuple[str, str]]) -> str | None:
    """`{% if is_incremental() %}` blocks are flat and closed: each variant below then renders
    every byte of branch text at least once."""
    depth, seen_else = 0, False
    for kind, raw in spans:
        if kind != "{%":
            continue
        tag = span_inner(kind, raw)
        if tag == _IF_TAG:
            if depth:
                return "nests one {% if is_incremental() %} block inside another"
            depth, seen_else = 1, False
        elif tag == _ELSE_TAG:
            if not depth or seen_else:
                return "has an {% else %} outside an {% if is_incremental() %} block"
            seen_else = True
        elif tag == _ENDIF_TAG:
            if not depth:
                return "has an {% endif %} with no {% if is_incremental() %}"
            depth = 0
    return "leaves an {% if is_incremental() %} block open" if depth else None


def _render(text: str, incremental: bool, names: dict[str, str]) -> str:
    """The model as dbt renders it (Jinja itself, dbt's defaults: no trim_blocks, no lstrip_blocks,
    the file stripped first), with quoted placeholder identifiers where dbt would put a relation."""
    env = SandboxedEnvironment(undefined=StrictUndefined)
    return env.from_string(text.strip()).render(
        config=lambda *args, **kwargs: "",
        source=lambda *args: f'"{names["source"]}"',
        ref=lambda *args: f'"{names["ref"]}"',
        this=f'"{names["this"]}"',
        is_incremental=lambda: incremental,
    )


def _model_sql_errors(rel: str, text: str, spans: list[tuple[str, str]]) -> list[str]:
    refuse = f"dbt:model_sql: {rel}"
    if (problem := _if_structure_error(spans)):
        return [f"{refuse} {problem}"]
    names = {kind: _placeholder(kind) for kind in ("source", "ref", "this")}
    placeholders = {names["source"]: "source()", names["ref"]: "ref()", names["this"]: "{{ this }}"}
    has_if = any(kind == "{%" and span_inner(kind, raw) == _IF_TAG for kind, raw in spans)
    variants = [(" (with is_incremental() true)", True), (" (with is_incremental() false)", False)] if has_if \
        else [("", False)]
    errors: list[str] = []
    for label, incremental in variants:
        try:
            sql = _render(text, incremental, names)
        except (TemplateError, TypeError, ValueError):
            return [f"{refuse} does not render as a Jinja template"]
        for problem in _query_problems(sql, placeholders):
            errors.append(f"{refuse}{label} {problem}")
    return list(dict.fromkeys(errors))


def _query_problems(sql: str, placeholders: dict[str, str]) -> list[str]:
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except (SqlglotError, ValueError, RecursionError):
        return ["does not parse as one DuckDB query"]
    if len(statements) != 1:
        return [f"holds {len(statements)} statements; a model is exactly ONE query"]
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        return [f"is a {shown(statement.key.upper(), 40)}; a model is exactly ONE SELECT"]
    problems = _reference_problems(statement, placeholders)
    problems += _duckdb_select_problems(sql, placeholders)
    return list(dict.fromkeys(problems))
