"""The dbt project an `output_kind: dbt` workflow is migrated into (design §4.3), and the ONE way
this repository runs dbt (design §5.1/§5.3).

dbt runs as a subprocess of the console script installed beside the running interpreter
(`sysconfig.get_path("scripts")`), with anonymous usage statistics and colours off and its
`target/` and `logs/` directories in a temporary directory, so a run never writes into the project
and one invocation never inherits another's process state.

Before any of that, `run_dbt` calls `check_surface` (final fix wave C1, `lib.dbt_surface`): a project
outside the closed dbt surface -- a Python model, a project or model hook that is not one plain
statement against `{{ this }}`, a macro, a `packages.yml`, Jinja in YAML, a model reading anything but
`source()`/`ref()`/`{{ this }}`, ... -- raises `DbtUnsafe` and no dbt process starts, whoever calls.

Facts this module encodes (spike 2026-09-22; dbt-core 1.12.5, dbt-duckdb 1.11.0, duckdb 1.5.5):
* dbt-duckdb names the attached catalog after the whole file stem while DuckDB drops a leading
  dot, so `.sandbox.<set>.duckdb` fails: sandboxes are `dbt_sandbox_<set>.duckdb`.
* `lib.backend.DuckDBBackend` stores `MIGDB.<SCHEMA>.<T>` as schema `MIGDB__<SCHEMA>`; a local
  run's `--vars` carry those flattened names (`local_vars`).
* dbt will not adopt a pre-existing upper-case target table for a lower-case model name
  ("approximate match"), so every target model carries `alias='<LOGICAL>'`.
Nothing here has run against Snowflake; the profile's `snowflake` output is unverified
(dbt-snowflake is not installed in this environment).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .backend import SANDBOX_DB, local_name
from .paths import Repo
from .validation import actual_table

PROFILE = "alteryx_migration"
DUCKDB_PATH_ENV = "MIG_DBT_DUCKDB_PATH"
WORK_SCHEMA = "MIG_WORK"
COMPILE_SRC_SCHEMA = "MIG_COMPILE"
DBT_TIMEOUT_S = 600
PROJECT_FILES = ("dbt_project.yml", "profiles.yml", "models/sources.yml", "models/schema.yml", "README.md")
FAILED_STATUSES = frozenset({"error", "fail", "runtime error"})
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

PROFILES_TEMPLATE = """\
# The dbt profile of every migrated workflow: scripts/lib/dbt_project.py PROFILES_TEMPLATE, and
# compile_check.py --target dbt refuses any other content. No credential is ever written here:
# every Snowflake value is read from the environment when dbt runs. `local` is the DuckDB double
# scripts/validate_dbt.py uses; `snowflake` has never been run (dbt-snowflake is not installed).
alteryx_migration:
  target: local
  outputs:
    local:
      type: duckdb
      path: "{{ env_var('MIG_DBT_DUCKDB_PATH', 'dbt_sandbox.duckdb') }}"
      schema: "{{ var('tgt_schema') }}"
      threads: 1
    snowflake:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
      user: "{{ env_var('SNOWFLAKE_USER') }}"
      authenticator: "{{ env_var('SNOWFLAKE_AUTHENTICATOR', 'externalbrowser') }}"
      private_key_path: "{{ env_var('SNOWFLAKE_PRIVATE_KEY_PATH', '') }}"
      role: "{{ env_var('SNOWFLAKE_ROLE') }}"
      warehouse: "{{ env_var('SNOWFLAKE_WAREHOUSE') }}"
      database: "{{ env_var('SNOWFLAKE_DATABASE') }}"
      schema: "{{ var('tgt_schema') }}"
      threads: 4
"""


class DbtUnavailable(RuntimeError):
    """No dbt console script beside this interpreter: a usage problem, never a domain verdict."""


class DbtUnsafe(ValueError):
    """The project is outside the closed dbt surface (`check_surface`), so no dbt process was started.
    `errors` are the `dbt:<check>` strings; the message lists them, bounded."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        listed = "; ".join(self.errors[:12])
        more = f"; and {len(self.errors) - 12} more" if len(self.errors) > 12 else ""
        super().__init__(bounded(
            f"dbt:surface: the project is outside the closed dbt surface, so no dbt process was started "
            f"({len(self.errors)} error{'' if len(self.errors) == 1 else 's'}): {listed}{more}", 2000))


@dataclass(frozen=True)
class DbtResult:
    code: int
    output: str                 # stdout+stderr: ANSI stripped, LF endings, temp/project paths redacted
    results: list[dict]         # run_results.json: {"name", "unique_id", "status", "message"} per node
    manifest: dict | None       # target/manifest.json when dbt wrote one (parse, run)

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def failed_models(self) -> list[str]:
        return sorted(r["name"] for r in self.results if r["status"] in FAILED_STATUSES)

    @property
    def skipped_models(self) -> list[str]:
        return sorted(r["name"] for r in self.results if r["status"] == "skipped")


def project_dir(repo: Repo, wf_id: str) -> Path:
    return repo.wf(wf_id, "dbt")


def model_name(output: dict) -> str:
    """A target's model is its logical name lower-cased; a work stream's is its MIG_WORK table name
    lower-cased (design §4.3)."""
    if output.get("kind") == "target":
        return str(output["logical"]).lower()
    return str(output["table"]).split(".")[-1].lower()


def model_relation(wf_id: str, seg: str, output: dict, database: str = SANDBOX_DB) -> str:
    """Where a model's table is read back: every model lands in `tgt_schema` (DV2), which is
    `<database>.MIG_WORK` for a validation run."""
    if output.get("kind") == "target":
        return actual_table(wf_id, seg, output).replace(f"{SANDBOX_DB}.", f"{database}.", 1)
    return f"{database}.{WORK_SCHEMA}.{str(output['table']).split('.')[-1].upper()}"


def local_vars(src_schema: str, tgt_schema: str = WORK_SCHEMA) -> dict[str, str]:
    return {"src_schema": local_name(f"{SANDBOX_DB}.{src_schema}.X")[0],
            "tgt_schema": local_name(f"{SANDBOX_DB}.{tgt_schema}.X")[0]}


def sandbox_path(repo: Repo, wf_id: str, golden_set: str, suffix: str = "") -> Path:
    return repo.wf(wf_id, f"dbt_sandbox_{golden_set}{suffix}.duckdb")


def dbt_executable() -> Path:
    scripts = Path(sysconfig.get_path("scripts"))
    for name in ("dbt.exe", "dbt"):
        if (scripts / name).is_file():
            return scripts / name
    raise DbtUnavailable(f"no dbt console script in {scripts.name}/ beside this interpreter; "
                         f"install requirements.txt into it")


def expected_model_config(mode: str, keys: list[str], logical: str) -> dict:
    if mode == "overwrite":
        return {"materialized": "table", "alias": logical}
    if mode == "append":
        return {"materialized": "incremental", "incremental_strategy": "append", "alias": logical}
    if mode == "merge":
        return {"materialized": "incremental", "incremental_strategy": "merge",
                "unique_key": sorted(str(k).upper() for k in keys), "alias": logical}
    raise ValueError(f"write mode {mode!r} has no dbt model config (target_check.py blocks it)")


def tail(text: str, lines: int = 5) -> str:
    return " | ".join(line.strip() for line in text.strip().splitlines()[-lines:])


def bounded(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clean(text: str, replacements: dict[str, str]) -> str:
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    for raw, placeholder in replacements.items():
        for form in {raw, raw.replace("\\", "/"), raw.replace("/", "\\")}:
            text = text.replace(form, placeholder)
    return text


def check_surface(project: Path) -> list[str]:
    """Every `dbt:<check>` error the project's own files fail against the closed dbt surface
    (`lib.dbt_surface`: `dbt:surface`, `dbt:profiles`, `dbt:project_yml`, `dbt:yaml`,
    `dbt:model_jinja`, `dbt:hook_sql`, `dbt:model_sql`), `[]` when it stays inside it. Judged from
    the files alone; nothing runs."""
    from .dbt_surface import surface_errors  # noqa: PLC0415 (dbt_surface reads PROFILES_TEMPLATE from here)
    return surface_errors(Path(project))


def run_dbt(command: str, project: Path, *, vars: dict[str, str], duckdb_path: Path | None,
            target: str = "local", log_file: Path | None = None,
            extra_env: dict[str, str] | None = None, timeout: int = DBT_TIMEOUT_S) -> DbtResult:
    """The ONE way this repository runs dbt. `check_surface` first: a project outside the closed
    surface raises `DbtUnsafe` before any argument is built or any process starts."""
    refused = check_surface(Path(project))
    if refused:
        raise DbtUnsafe(refused)
    project = Path(project).resolve()
    executable = dbt_executable()
    with tempfile.TemporaryDirectory(prefix="dbt-") as tmp:
        target_path, log_path = Path(tmp, "target"), Path(tmp, "logs")
        args = [str(executable), command, "--project-dir", str(project), "--profiles-dir", str(project),
                "--target", target, "--target-path", str(target_path), "--log-path", str(log_path),
                "--vars", json.dumps(vars, sort_keys=True), "--no-use-colors"]
        env = {**os.environ, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DO_NOT_TRACK": "1",
               **(extra_env or {})}
        if duckdb_path is not None:
            env[DUCKDB_PATH_ENV] = str(Path(duckdb_path).resolve())
        completed = subprocess.run(args, cwd=project, env=env, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
        redact = {tmp: "<dbt-temp>", str(project): "<project>"}
        output = _clean((completed.stdout or "") + (completed.stderr or ""), redact)
        results = []
        run_results = target_path / "run_results.json"
        if run_results.is_file():
            for r in json.loads(run_results.read_text(encoding="utf-8")).get("results") or []:
                results.append({"name": r["unique_id"].split(".")[-1], "unique_id": r["unique_id"],
                                "status": str(r.get("status")), "message": _clean(r.get("message") or "", redact)})
        manifest_path = target_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(output, encoding="utf-8", newline="\n")
    return DbtResult(completed.returncode, output, results, manifest)
