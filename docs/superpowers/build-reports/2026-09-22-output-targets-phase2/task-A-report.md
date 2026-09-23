# Task A report — the dbt artefact contract and `compile_check.py --target dbt`

Worktree: `.worktrees/p2-A`, branch `wt/p2-A`. Commits (top to bottom, newest first):

- `321dc21` style: type-hint the dbt compile_check helpers' `project` parameter as `Path`
- `ce11339` feat: dbt project contract — one dbt invocation helper, the profile template, compile_check --target dbt
- `c8466ae` wip: dbt project contract — layout, naming, profile template, one dbt invocation

Base: `73cf21c` (the phase-2 plan doc).

## What was built

- **`scripts/lib/dbt_project.py`** (new), implemented verbatim from the brief's Step 3: `PROFILE`,
  `DUCKDB_PATH_ENV`, `WORK_SCHEMA`, `COMPILE_SRC_SCHEMA`, `DBT_TIMEOUT_S`, `PROJECT_FILES`,
  `FAILED_STATUSES`, `PROFILES_TEMPLATE`, `DbtUnavailable`, `DbtResult` (`ok`, `failed_models`,
  `skipped_models`), `project_dir`, `model_name`, `model_relation`, `local_vars`, `sandbox_path`,
  `dbt_executable`, `expected_model_config`, `tail`, `bounded`, `run_dbt` — the ONE dbt invocation
  helper: a subprocess of the console script found via `sysconfig.get_path("scripts")`, telemetry
  and colours off, `--target-path`/`--log-path` in a temp dir outside the project, ANSI/CRLF/path
  redaction on the captured output.
- **`tests/dbt_fixtures.py`** (new): `WF = "wf_0009"`, `SEGMENTS`, `MODEL_FILES`,
  `build_dbt_workflow(tmp_path, *, replace=None, sets=("normal","second"))` — a hand-built
  two-segment dbt workflow (`seg_01` filters `ITEMS` into work stream `2_T`; `seg_02` feeds that
  stream to two targets, `ITEMS_OUT` overwrite and `ITEMS_HIST` merge on `ID`) with golden data for
  two sets, built the way `tests/test_validate_segment.py`'s `build()` builds its own `wf_0009`.
- **`tests/test_dbt_project.py`**: the brief's 9 tests verbatim (layout/naming/vars/config
  functions, plus two real `dbt run` invocations against a loaded DuckDB sandbox and one mocked
  `subprocess.run` to check the telemetry/colour/path flags, plus the missing-console-script case).
- **`scripts/compile_check.py`**: `compile_check(repo, wf_id, seg, target="auto")` now branches to
  `compile_check_dbt` first and raises `ValueError` when `seg is None` for any other target; new
  `compile_check_dbt(repo, wf_id)` plus six helpers (`_dbt_layout_errors`, `_dbt_profile_errors`,
  `_dbt_source_errors`, `_dbt_model_errors`/`_dbt_target_config_errors`,
  `_dbt_tool_comment_errors`, `_dbt_hook_errors`) producing the nine named `dbt:<check>` errors and
  writing `workflows/<wf>/dbt/compile_check.json`. CLI: `seg` is now `nargs="?"`, `--target` gained
  `dbt`, the two invalid combinations (a segment with `--target dbt`; no segment with any other
  target) print a usage message and return 2 directly — not `parser.error()`/`SystemExit`, because
  the brief's own CLI test calls `main()` in-process and expects an `int` back (see "Brief
  corrections" below). `dbt_project.DbtUnavailable` joins the `(FileNotFoundError, KeyError)` tuple
  that exits 2. Module docstring gained "The `dbt` target" paragraph naming all nine checks.
- **`tests/test_compile_check_dbt.py`** (new, written by me from the brief's prose — see below):
  15 tests, one per named scenario in the brief plus `test_cli_exit_codes`.
- **`.gitignore`**: added `workflows/*/dbt/{logs,target,dbt_packages}/` and
  `tests/cookbook_examples/dbt/*/project/logs/`, with a comment explaining who writes what.
- **Spec amendment** (`docs/superpowers/specs/2026-09-22-output-targets-design.md`): §4.3's
  `profiles.yml` and `models/<logical>.sql` rows rewritten to DV2/DV4, plus the required spike-date
  sentence; §5.1's `dbt` bullet rewritten to name `compile_check.py --target dbt` (DV6), the one
  invocation helper, and all nine named checks; §5.3's first two sentences rewritten to DV1
  (sandbox naming) and DV3 (flattened local `--vars`) — the idempotency sentence (DV7, outside the
  "first two sentences" the brief names) is left for Task B, which implements it; §6's policy
  bullet rewritten to DV5's narrowed translator lane. Nothing outside those spans touched;
  confirmed `tests/test_agents_config.py::_spec_rules_bullet` (a substring search anchored on other
  text) is unaffected.

## TDD evidence

**RED** (before `scripts/lib/dbt_project.py` existed):
```
$ .venv/Scripts/python.exe -m pytest tests/test_dbt_project.py
ImportError: cannot import name 'dbt_project' from 'lib' (…/scripts/lib/__init__.py)
1 error in 0.09s
```
(`tests/dbt_fixtures.py` itself imports `lib.dbt_project`, so this also stood in for the fixture's
own RED per the brief.)

**GREEN**:
```
$ .venv/Scripts/python.exe -m pytest tests/test_dbt_project.py -v
9 passed in 7.88s
```

**RED** (before `compile_check_dbt` existed, after adding `--target dbt` tests):
```
$ .venv/Scripts/python.exe -m pytest tests/test_compile_check_dbt.py -v
... AttributeError (no compile_check_dbt) / argparse "invalid choice: 'dbt'" ...
15 failed in 0.67s
```

**GREEN**:
```
$ .venv/Scripts/python.exe -m pytest tests/test_compile_check_dbt.py -v
15 passed in 49.17s
```
(one intermediate RED→GREEN cycle inside this: `test_cli_exit_codes` first failed with
`SystemExit: 2` because `parser.error()` can't be caught by a direct `main()` call — fixed by
returning 2 directly instead of calling `parser.error()`; see Brief corrections.)

**Step 6 target set**:
```
$ .venv/Scripts/python.exe -m pytest tests/test_dbt_project.py tests/test_compile_check_dbt.py \
    tests/test_compile_check.py tests/test_compile_check_snowpark.py tests/test_agents_config.py \
    tests/test_committed_workflows.py
132 passed in 60.11s
```

**Full suite, twice** (once before the type-hint cleanup commit, once after — both clean):
```
$ .venv/Scripts/python.exe -m pytest
1330 passed in 133.19s / 136.98s
```
Baseline was 1306 passed / 0 skipped; 24 new tests (9 + 15), 0 skipped throughout, output pristine
(no warnings).

## Brief corrections

1. **`test_sources_yml_must_declare_every_mapped_source` vs. the given `compile_check_dbt` code
   structure.** The brief's literal Step 4 code calls `_dbt_source_errors(result.manifest, …)`
   only inside the `else` branch — i.e. only when `dbt parse` succeeds. But the brief's own test
   description for this scenario ("`ITEMS` renamed `ITEMZ` in `sources.yml`: `dbt:sources` naming
   `ITEMS` (plus the resulting `dbt:parse`, since the model's `source()` breaks — assert only that a
   `dbt:sources` error exists)") requires a `dbt:sources` error to appear *together with* a
   `dbt:parse` failure. I proved these two are incompatible as given: renaming the declared source
   makes the model's `{{ source('src','ITEMS') }}` unresolvable, and `dbt parse` then fails outright
   with **no manifest written at all** — I confirmed this empirically with the real `dbt.exe` in a
   throwaway project (`dbt parse` → `Compilation Error: … depends on a source named 'src.ITEMS'
   which was not found`, exit 2, no `target/` directory created). Since `_dbt_source_errors` as
   specified needs `result.manifest`, it can never run in that branch.

   Smallest fix: `_dbt_source_errors` now takes `project` (not `manifest`) and reads
   `models/sources.yml` directly via `read_yaml`, so it runs unconditionally alongside
   `_dbt_layout_errors`/`_dbt_profile_errors`, before `dbt parse` is even invoked — exactly like the
   other two structural checks. This is strictly more robust (it still catches a source mismatch
   when the project doesn't parse at all) and it's what makes the described test's own expectation
   ("both a `dbt:sources` and the resulting `dbt:parse` error") true. All other prose behaviour of
   the check (name set comparison, per-input column comparison) is unchanged; only where the data
   comes from changed (project YAML instead of the compiled manifest — since we write the columns
   into `sources.yml` ourselves, the manifest would report exactly the same names either way when
   parse *does* succeed).

2. **CLI exit-code test calls `main()` directly, not through `subprocess`.** Every other
   `parser.error()`-driven usage error in this codebase (`parse.py`, `load_golden.py`,
   `compile_check.py`'s own pre-existing `seg`-less-contract case) is exercised only via a
   `subprocess.run([...])` helper in its tests, because `parser.error()` calls `sys.exit()`
   internally and raises `SystemExit`, which a direct `main([...])` call can't turn into a return
   value. The brief's `test_cli_exit_codes`, however, explicitly calls `cc.main([...]) == 2` for
   both new usage errors (segment given with `--target dbt`; no segment given with any other
   target) in-process. I kept the two new validations inside `main()` but replaced
   `parser.error(...)` with a plain `print(..., file=sys.stderr); return 2` for just these two
   checks, so they're testable the way the brief's own test calls them, while every other exit
   path (contract/procedure/project not found, an unexpected exception) is untouched.

Both corrections are additive/narrowing (nobody could have relied on the literal-but-unreachable
behaviour), covered by tests that were RED for the stated reason before the fix and GREEN after,
and don't change any test name or assertion the brief specified.

## Design decisions not spelled out in the brief

- The fixture's `AMOUNT` (a `FixedDecimal 19,2` field) golden rows are written as plain strings
  (`"10.00"`, `None`, …) rather than `decimal.Decimal`; `lib.typed_csv.format_value`'s fallback is
  `str(value)` for any type it doesn't special-case, so this round-trips to the same CSV text as a
  `Decimal` would, and it matches the brief's own quoted-string notation for the table literally.
- `_dbt_model_errors`'s per-key `model_config` comparison and the appended `dbt:columns` check run
  for *every* output (work and target alike) rather than skipping columns for work outputs — the
  brief's prose states the columns rule as a general, unqualified bullet after describing the
  target-specific config rule, so I read it as applying uniformly; nothing in the fixture or the
  fifteen tests exercises a work-output column mismatch, so this is untested but low-risk.
- `_dbt_hook_errors`'s "no mapping for this tool" branch (a PreSQL/PostSQL node whose tool id isn't
  in any `intake/mappings.yaml` output) is handled defensively with its own `dbt:hooks` message;
  the brief's prose doesn't name this sub-case and none of the fifteen tests exercise it either.

## Concerns

- None that block Task B/C/D. The two corrections above are narrow and evidenced; everything else
  matches the brief's verbatim code/tests exactly, including the exact `PROFILES_TEMPLATE` text,
  the `dbt_sandbox_<set>.duckdb` naming, and the flattened `--vars`.
- I did not implement or touch `validate_dbt.py`, `lib.validation` changes, or DV7's idempotency
  documentation in §5.3 — all explicitly Task B's.

## Interfaces for B/C/D

**`scripts/lib/dbt_project.py`** (import as `from lib import dbt_project` or
`from lib.dbt_project import …`):

```python
PROFILE = "alteryx_migration"
DUCKDB_PATH_ENV = "MIG_DBT_DUCKDB_PATH"
WORK_SCHEMA = "MIG_WORK"
COMPILE_SRC_SCHEMA = "MIG_COMPILE"
DBT_TIMEOUT_S = 600
PROJECT_FILES: tuple[str, ...]          # ("dbt_project.yml", "profiles.yml", "models/sources.yml", "models/schema.yml", "README.md")
FAILED_STATUSES: frozenset[str]         # {"error", "fail", "runtime error"}
PROFILES_TEMPLATE: str                  # the fixed profiles.yml text, byte for byte

class DbtUnavailable(RuntimeError): ...

@dataclass(frozen=True)
class DbtResult:
    code: int
    output: str
    results: list[dict]                 # [{"name","unique_id","status","message"}, …]
    manifest: dict | None
    @property ok: bool
    @property failed_models: list[str]
    @property skipped_models: list[str]

def project_dir(repo: Repo, wf_id: str) -> Path                            # repo.wf(wf_id, "dbt")
def model_name(output: dict) -> str                                       # target: logical.lower(); work: table-name-tail.lower()
def model_relation(wf_id: str, seg: str, output: dict, database: str = SANDBOX_DB) -> str
def local_vars(src_schema: str, tgt_schema: str = WORK_SCHEMA) -> dict[str, str]   # flattened MIGDB__… names
def sandbox_path(repo: Repo, wf_id: str, golden_set: str, suffix: str = "") -> Path  # dbt_sandbox_<set><suffix>.duckdb
def dbt_executable() -> Path                                              # raises DbtUnavailable
def expected_model_config(mode: str, keys: list[str], logical: str) -> dict   # raises ValueError for update_only
def tail(text: str, lines: int = 5) -> str
def bounded(text: str, limit: int = 500) -> str
def run_dbt(command: str, project: Path, *, vars: dict[str, str], duckdb_path: Path | None,
           target: str = "local", log_file: Path | None = None,
           extra_env: dict[str, str] | None = None, timeout: int = DBT_TIMEOUT_S) -> DbtResult
```

`run_dbt` is the ONLY place a `dbt` subprocess is ever invoked in this repo — Task B's
`validate_dbt.py` (and anything else that needs to run dbt) must call it, not `subprocess` directly.

**`scripts/compile_check.py`**:

```python
def compile_check(repo: Repo, wf_id: str, seg: str | None, target: str = "auto") -> dict
    # target="dbt" ignores seg and calls compile_check_dbt; any other target raises ValueError if seg is None

def compile_check_dbt(repo: Repo, wf_id: str) -> dict
    # {"status": "OK"|"ERROR", "target": "dbt", "errors": [str, …], "statements": 0, "models": int}
    # writes workflows/<wf>/dbt/compile_check.json; raises FileNotFoundError if dbt_project.yml,
    # segments/order.json, or any segment's contract.json is missing
```
CLI: `compile_check.py <wf> --target dbt` (no segment; passing one is a usage error, exit 2).

**`tests/dbt_fixtures.py`** (for Task B/C's own dbt tests to reuse or crib from):

```python
WF = "wf_0009"
SEGMENTS = ["seg_01", "seg_02"]
MODEL_FILES: dict[str, str]             # relative path under dbt/ -> text (includes all PROJECT_FILES + 3 model .sql files)
def build_dbt_workflow(tmp_path, *, replace: dict[str, str | None] | None = None,
                       sets=("normal", "second")) -> Repo
```
`replace` only overrides files under `workflows/wf_0009/dbt/` (`MODEL_FILES`' keys); a `None` value
deletes that file. Golden data, `manifest.json` (`output_kind: "dbt"`), `intake/mappings.yaml`,
`segments/order.json` and both segments' `dag.json`/`contract.json` are always written fresh from
the module's own constants — the fixture has no way to override those from the outside; a test that
needs to (e.g. the PreSQL/hook test) mutates them directly via `repo.seg(...)`/`lib.io.write_json`
after calling `build_dbt_workflow`.

## Fix round 1

Review of `321dc21` confirmed the brief was implemented faithfully and both Task A brief
corrections reproduced against real dbt. Five findings, rulings in `task-A-fix1.md`, applied
RED-first. Commit: `12f3819`.

### I1 — a blank hook is not a hook (`scripts/compile_check.py::_dbt_hook_errors`)

dbt's manifest turns even `pre_hook=""` into a non-empty list `[{"sql": "", ...}]`, so the old
`if not hooks:` check treated an explicitly-blank hook as present. Fixed to `if not hooks or not
any(str(h.get("sql", "")).strip() for h in hooks):`.

**RED** (`test_a_blank_hook_is_not_a_hook`, before the fix): `pre_hook=''` on a mapped tool with
PreSQL set → `report["status"] == "OK"` (expected `"ERROR"`) — `AssertionError: ('pre_sql',
'pre_hook', '') / assert 'OK' == 'ERROR'`.

**GREEN**: all four cases (`pre_hook`/`post_hook` × `""`/`"   "`) now produce a `dbt:hooks` error
naming tool `4`.

### I2 — every model must be a contract output (new check `dbt:model_orphan`)

New helper `_dbt_orphan_errors(nodes, contracts)`: every manifest model node's name must equal
`dbt_project.model_name(output)` for some output of some segment's contract; anything else is
`dbt:model_orphan: models/<file>.sql is not a contract output`. Wired into `compile_check_dbt`'s
post-parse branch, next to `_dbt_model_errors`. Named in spec §5.1's check list.

**RED** (`test_an_orphan_model_is_refused`, before the fix): fixture project plus
`models/extra_model.sql` (`{{ config(materialized='table') }}\nselect 1 as X\n`) →
`report["status"] == "OK"` (expected `"ERROR"`).

**GREEN**: `report["errors"] == ["dbt:model_orphan: models/extra_model.sql is not a contract
output"]` exactly — no other check fires on the addition.

### M3 — model SQL is a closed Jinja surface (new check `dbt:model_jinja`)

New section in `scripts/compile_check.py` (`_dbt_jinja_spans`, `_dbt_jinja_construct_ok`,
`_dbt_jinja_config_ok`, `_dbt_jinja_errors`): a small tokenizer over `{{`/`}}`, `{%`/`%}`, `{#`/`#}`
delimiters, explicitly not a full Jinja parser. `_dbt_jinja_spans` walks the token stream with a
depth counter so a `{{ config(...) }}` span can legitimately contain one nested `{{ this }}` pair
(dbt's own `pre_hook="delete from {{ this }} where 1 = 0"` idiom) without prematurely closing at
the inner pair's `}}`. Each span's stripped inner text is checked against the allow-list: `this`
alone; `config(...)` whose arguments, after scrubbing any nested `{{ this }}`, contain no other
`identifier(` call pattern and no stray Jinja delimiter; `source('src', '<LOGICAL>')` and
`ref('<model>')` matched by a *strict* `fullmatch` regex (so `source('src', env_var('X'))` — a
disallowed call nested inside an otherwise-allowed one's own arguments — fails the strict pattern
and falls through to refused, with no special-case code needed for "nested calls"); `{% if
is_incremental() %}`/`{% else %}`/`{% endif %}` exactly; any `{# ... #}` comment. Everything else
(`env_var`, `run_query`, `statement`, `adapter.*`, `var`, `{% for %}`, …) is refused. Wired into
`compile_check_dbt`'s unconditional group (alongside layout/profile/sources), so it fires even when
`dbt parse` itself fails. Added as a new bullet under spec §4.3.

**RED** (`test_model_jinja_is_a_closed_allow_list`, before the fix): the `env_var` case (`select
{{ env_var('X') }} as ID, ...`) — `dbt parse` itself failed on the undefined env var
(`Parsing Error | Env var required but not provided: 'X'`) and no `dbt:model_jinja` error was
present at all (the check didn't exist yet): `AssertionError: ('env_var', ["dbt:parse: dbt parse
exited 2: ... Env var required but not provided: 'X'"])`. This also confirms the check has to run
statically/unconditionally rather than only after a successful parse, or it would never fire for
constructs that also break dbt parse.

**GREEN**: all seven negative cases (`env_var`, `run_query`, `statement` block, `adapter.execute`,
top-level `var(...)`, `source('src', env_var('X'))` nested, `{% for %}`) produce a `dbt:model_jinja`
error; the positive control (one model exercising `config` with a `pre_hook` containing `{{ this
}}`, a comment, `is_incremental()` if/else/endif, `source` and `ref`) produces none. Verified this
doesn't regress the base fixture or any earlier fixture variant (all three existing model files use
only `config`/`source`/`ref`, so the full `test_compile_check_dbt.py` + `test_dbt_project.py` run
clean — see below).

### M4 — a work-output column mismatch (self-disclosed gap, no code change)

`test_a_work_model_column_mismatch_is_refused`: swaps `wf0009_seg_01_out`'s `schema.yml` columns to
`NOTE, ID, AMOUNT`. The `dbt:columns` check in `_dbt_model_errors` already runs for every output
kind (work and target alike — this was a deliberate reading of the original brief's prose, called
out as untested in the original Task A report). Passed on the first run, no implementation change.

### M5 — a hook for an unmapped tool (self-disclosed gap, no code change)

`test_a_hook_for_an_unmapped_tool_is_named`: sets a DAG node's `tool_id` to `"99"` (in no
`intake/mappings.yaml` output's `tool_ids`) with PreSQL set. Read `_dbt_hook_errors`: when no
mapping is found, it already appends `dbt:hooks: no intake/mappings.yaml output maps tool 99 for
its PreSQL` and `continue`s — a clear message naming the tool id, never a crash or a wrong model
name. Passed on the first run, no implementation change.

### Verification

```
$ .venv/Scripts/python.exe -m pytest tests/test_dbt_project.py tests/test_compile_check_dbt.py -v
29 passed in 102.52s   (9 + 20; 5 new this round)

$ .venv/Scripts/python.exe -m pytest tests/test_dbt_project.py tests/test_compile_check_dbt.py \
    tests/test_agents_config.py tests/test_committed_workflows.py
113 passed in 100.52s

$ .venv/Scripts/python.exe -m pytest
1335 passed in 178.31s   (was 1330 before this round; +5, 0 skipped)
```

### Files changed this round

- `scripts/compile_check.py`: `_dbt_hook_errors` (I1 fix), new `_dbt_orphan_errors` (I2), new
  `_dbt_jinja_spans`/`_dbt_jinja_construct_ok`/`_dbt_jinja_config_ok`/`_dbt_jinja_errors` (M3), both
  wired into `compile_check_dbt`, module and function docstrings updated to eleven named checks.
- `tests/test_compile_check_dbt.py`: five new tests (I1, I2, M3, M4, M5) appended under a
  `# --- fix round 1 ---` divider.
- `docs/superpowers/specs/2026-09-22-output-targets-design.md`: §4.3 gains the Jinja allow-list
  bullet; §5.1's `dbt` bullet renamed to eleven checks and now names `dbt:model_orphan` and
  `dbt:model_jinja`.

### Concerns

None. All five findings resolved or confirmed pre-existing-correct with new coverage; full suite
clean at 1335/0 skipped.

### Interfaces for B/C/D — additions this round

`scripts/compile_check.py` gains two more named `dbt:<check>` error prefixes in
`compile_check_dbt`'s report: `dbt:model_orphan` and `dbt:model_jinja`. No public function
signatures changed. `scripts/lib/dbt_project.py` is unchanged this round.

## Fix round 2

Re-review of `12f3819` confirmed all five fix-round-1 rulings addressed and tested. One residual:
the M3 tokenizer's `_dbt_jinja_construct_ok` stripped ordinary whitespace but not Jinja's
whitespace-control markers, so `{{- this -}}`, `{{- config(...) -}}`, `{{- ref(...) -}}`,
`{{- source(...) -}}`, `{%- if is_incremental() -%}`, `{%- else -%}` and `{%- endif -%}` were all
wrongly refused as `dbt:model_jinja`, although real `dbt parse` accepts them. Commit: `ab0d065`.

### Fix (`scripts/compile_check.py::_dbt_jinja_construct_ok`)

Per the ruling: strip one leading `-`/`+` and one trailing `-`/`+` from the inner text, each
independently, right after slicing off the delimiter and before `.strip()`:

```python
inner = raw[len(kind): -len(_JINJA_CLOSE[kind])]
if inner[:1] in ("-", "+"):
    inner = inner[1:]
if inner[-1:] in ("-", "+"):
    inner = inner[:-1]
inner = inner.strip()
```

Applied once, before the `kind`-specific branches, so it covers `{{ }}`, `{% %}` and `{# #}` alike
(a comment's own content never matters, but the strip is harmless there too). Nothing else in the
function changed. The two checks are independent (`inner[:1]`/`inner[-1:]` on the string as it
stands at that point), so a single-character inner text like `"-"` only ever loses that one
character once, not twice.

### RED

`test_model_jinja_allows_whitespace_control_markers`, before the fix — a model using every
allow-listed construct in both-sided `-`, one-sided `-` and `+` trimmed forms:
```
AssertionError: ['dbt:model_jinja: models/items_out.sql uses {% else +%}, which a migration model
may not', 'dbt:model_jinja: models/items_out.sql uses {%- if is_incremental() -%}, which a
migration model may not', ...]
```
— every trimmed construct in the positive fixture was refused, exactly the reported residual.

### GREEN

```
$ .venv/Scripts/python.exe -m pytest tests/test_compile_check_dbt.py -k whitespace_control -v
1 passed in 8.27s

$ .venv/Scripts/python.exe -m pytest tests/test_dbt_project.py tests/test_compile_check_dbt.py -v
30 passed in 107.11s   (9 + 21; 1 new)

$ .venv/Scripts/python.exe -m pytest
1336 passed in 179.65s   (was 1335 before this round; +1, 0 skipped)
```

The same test also confirms `{{- env_var('X') -}}` and `{%- for x in y -%}` (a disallowed
construct, wrapped the same way) are still refused.

### Files changed this round

- `scripts/compile_check.py`: `_dbt_jinja_construct_ok` strips whitespace-control markers before
  matching (5 lines added, 1 changed).
- `tests/test_compile_check_dbt.py`: one new test,
  `test_model_jinja_allows_whitespace_control_markers`, under a `# --- fix round 2 ---` divider.

### Concerns

None.
