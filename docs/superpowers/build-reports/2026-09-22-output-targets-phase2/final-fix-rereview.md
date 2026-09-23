# Phase-2 final fix wave — adversarial re-review of the dbt closed-surface gate

Scope: the C1 fix wave (`final-fix-wave.md`) that closes the review's Critical — an agent-written dbt
project could run arbitrary code or read/write arbitrary files during local validation while
`compile_check` said OK and `validate_dbt` said PASS. Head `fd40971`, base `a7aa602`, worktree
`.worktrees/p2-ffix`. Adversarial mandate: get code executed, a file outside the project read/written, or a
PASS for an unsafe project, through the new gate.

Method: read the gate whole (`scripts/lib/dbt_surface.py`, 987 lines; `dbt_project.run_dbt`/`check_surface`;
`compile_check.compile_check_dbt`; `validate_dbt`; `deploy._execute_dbt`; `orchestrator/policy.ts`
`dbtModelLanes`; `orchestrator/stages.ts` `translateDbt`). Then drove ~124 hand-crafted payloads plus a
117-function DuckDB brute-force sweep (241 total) through `check_surface` / `hook_problems` on scratch
projects under the session scratchpad, every payload writing only a marker inside its own scratch dir (no
marker was ever written — nothing executed). Ran the three focused pytest files and the node policy/stages
suites. All read-only on tracked files; no commits, stashes, resets or pushes.

## Result

**No bypass found.** Every attempt to reach code execution, an out-of-project read/write, or a PASS for an
unsafe project was refused (or was benign SQL that reads nothing outside `source()`/`ref()`/`{{ this }}`).
The render step never executed (`RENDER_MARKER` never created). Both SQL parsers (sqlglot DuckDB dialect +
DuckDB's own `extract_statements`/`json_serialize_sql`/`tokenize`) are consulted and I found no disagreement
that lets danger through — every disagreement I could induce fails **closed** (unparseable → refused).

## Choke-point coverage (every path that can start dbt)

- `run_dbt` calls `check_surface` FIRST, before `dbt_executable()` and before building any argument; a
  non-empty result raises `DbtUnsafe` and no `subprocess.run` is reached. Confirmed the only `subprocess.run`
  that spawns dbt in `scripts/` lives in `run_dbt` (grep); no `dbtRunner`/Python-API/`os.system` path.
- `compile_check_dbt` computes `surface = check_surface(project)` and skips `dbt parse` entirely when it is
  non-empty (report ERROR, exit 1).
- `validate_dbt.validate_dbt` calls `check_surface` after the usage prerequisites and before `_run_project`;
  `_refuse` writes a FAIL report per segment/set naming `dbt:surface`, a FAIL chain, exit 1, and no sandbox.
  `_run_project` → `run_dbt` re-checks, so the check→run window is re-gated inside one process (closes the
  in-script TOCTOU the implementer disclosed).
- `validate_workflow._validate_dbt_workflow` delegates to `validate_dbt` (gated).
- `deploy._execute_dbt` → `run_dbt` (gated); `test_cookbook_dbt` → `run_dbt` (gated).
- No path builds a project copy AFTER the check and runs a different tree: every caller runs the exact dir it
  checked, and `run_dbt` re-checks that dir.

## Attacks tried (241) — payload / expected / observed

### Model SQL — file/table functions and replacement scans (dbt:model_sql)
| payload | expected | observed |
|---|---|---|
| `from 'C:/evil.csv'` (replacement scan) | DENY | DENY (table other than source/ref/this) |
| `from 'evil.parquet'` | DENY | DENY |
| `read_csv(...)` / `read_json_auto(...)` / `read_text(...)` / `read_blob(...)` as table or scalar | DENY | DENY (table-valued fn / sqlglot-unknown) |
| `glob(...)`, `generate_series`, `range`, `duckdb_settings()`, `sql_auto_complete`, `TABLE('x')` | DENY | DENY (table-valued fn) |
| `getvariable`, `current_setting`, `set_config`, `nextval`, `pragma_database_list`, `now` | DENY | DENY (sqlglot-unknown / denied) |
| `from 'x.csv' select *` (from-first), `pivot`, `unpivot`, `pg_settings` | DENY | DENY |
| union/subquery hiding `read_parquet`/`glob` | DENY | DENY (walks subqueries + set ops) |
| 117-fn DuckDB brute-force (`read|scan|glob|env|setting|http|secret|attach|copy|pragma|execute|query|...`) | all DENY | only 9 harmless allowed (`current_date/_schema/_user`, `json_array/_keys/_type`, `get_current_time`) |
| benign: `unnest([..])`, `values`, `list_transform` lambda, window fn, `version()`, `current_schema()`, `columns('a')` | ALLOW | ALLOW (read nothing external) |

### Model SQL — CTE-scope and statement-shape games
| payload | expected | observed |
|---|---|---|
| recursive/self `with x as (select * from x) select * from x` | DENY | DENY (forward/recursive ref falls through to real table) |
| forward-ref `with a as (select from b), b as (...)` | DENY | DENY |
| `select 1; drop table x` / trailing `;` (multi-statement) | DENY | DENY (2 statements) |
| `summarize`/`describe`/`show`/`execute`/`call`/`copy` inside/as model | DENY | DENY (not one SELECT / COMMAND / fails parse) |
| both `is_incremental()` branches judged — read hidden in `{% if %}` **or** `{% else %}` | DENY | DENY (each variant caught in its own branch) |
| unforgeable nonce placeholders (`mig_source_<12hex>`) — literal `mig_source_foo` in SQL | DENY | DENY (exact nonce match required) |

### Model Jinja (dbt:model_jinja)
| payload | expected | observed |
|---|---|---|
| `{% set %}` `{% do %}` `{% call %}` `{% for %}` `{% macro %}` `{% raw %}` | DENY | DENY |
| `{{ env_var(..) }}` `{{ run_query(..) }}` `{{ 7*7 }}` `{{ this.database }}` `{{ this.__class__ }}` | DENY | DENY |
| whitespace-control `{%- if is_incremental() -%}`/`-%}` | ALLOW | ALLOW |
| SSTI via allowed-looking span (`config(materialized=cycler)`, global access) | DENY | DENY; render never executed (no marker) |
| BOM prefix, trailing `\x00`, CRLF, fullwidth-`a` config key (NFKC) | fail-closed/benign | BOM→DENY(parse), null/CRLF→benign ALLOW, homoglyph→benign (ast+Jinja both normalise, value validated) |

### config() kwargs (dbt:model_jinja) and hooks (dbt:hook_sql)
| payload | expected | observed |
|---|---|---|
| f-string / bytes / `r'...'` raw-string alias | DENY | DENY (not a literal / ast≠Jinja divergence caught) |
| implicit-concat `'ITE' 'MS'`, `\x5f` escape, string-concat hook | ALLOW (value validated as identifier / valid hook) | ALLOW |
| `unique_key='ID; drop table x'`, `alias='ITEMS;DROP'`, `materialized='external'` | DENY | DENY (identifier / closed-set) |
| `post_hook=['a','b']` (list), `config(**{..})` (splat), paren-breaking args | DENY | DENY |
| hook `COPY {{this}} TO ...`, multi-statement, comment, `CALL`/`SET`, other-table subquery/CTE, `read_*`/`getvariable` | DENY | DENY |
| hook `DELETE/UPDATE/TRUNCATE/INSERT` against `{{ this }}` only, `INSERT..SELECT FROM {{this}}` | ALLOW | ALLOW |

### YAML (dbt:yaml), dbt_project.yml (dbt:project_yml), profiles (dbt:profiles)
| payload | expected | observed |
|---|---|---|
| `!!python/...` tag, anchor/alias, `<<` merge key, duplicate key, multi-document | DENY | DENY |
| Jinja in description / in a key / in a comment; `schema` with inner-paren spaces | DENY | DENY (raw-text `{{`-count + `_strings` + strict loader) |
| schema.yml `config:` block, custom test, `accepted_values` with `(`, `relationships.to="{{ run_query }}"` | DENY | DENY |
| project_yml `on-run-start`, `models:`+`+post-hook`, extra `*-paths`, `model-paths:['.']`, any Jinja | DENY | DENY |
| profiles.yml any deviation (added comment, `threads: 8`) | DENY | DENY (byte-for-byte template) |

### File set (dbt:surface)
| payload | expected | observed |
|---|---|---|
| `.py` anywhere incl. `logs/`,`target/`; `macros/`,`packages.yml`,`snapshots/`,`seeds/`,`analyses/`,`tests/`,`dbt_packages/` | DENY | DENY |
| junction/symlink anywhere (incl. project dir itself) | DENY | DENY (never followed) |
| case variants `Models/`, `Schema.YML`, `Items.SQL`, dash/space/hidden model names | DENY | DENY (fail-closed; dbt-would-read cases still refuse → dbt never runs) |
| NTFS Alternate Data Stream `x.sql:evil.sql` | benign | ALLOW but inert — dbt reads only the main stream; ADS never executed (see Observations) |

## Regression checks
- `check_surface` = `[]` on all three committed projects: `samples/wf_0007/canned/dbt`,
  `workflows/wf_0007/dbt`, `tests/cookbook_examples/dbt/merge/project`.
- Both broken `wf_0007` variants (`samples/wf_0007/broken_sql/dbt/models/*.sql`) PASS the surface gate, so
  they still FAIL later at the runtime compare with their recorded class (no false surface refusal).
- Policy `dbtModelLanes`: `COMPONENT = [a-z0-9][a-z0-9_-]*` has no dot, so `.py`/`.yml`/`x.sql.py` cannot
  slip the `.sql` lane; the project-file lane (dbt_project/profiles/readme/translation_notes/fix_log) and the
  two-YAML lane are unchanged, so translator/fixer can still write every legitimate file.
- N-dbt: `translateDbt` parks `dbt: chain needs_human` when the chain PASSes but says needs_human — no
  `procs/README.md`, no documenter (`stages.ts` 1058-1064; test `stages.test.ts:1473`).
- Tests re-run by me: `tests/test_dbt_surface.py tests/test_compile_check_dbt.py tests/test_validate_dbt.py`
  → 260 passed, exit 0. Node `policy.test.ts` + `stages.test.ts` → 219 passed, 0 fail (includes the C1.8
  17-denial/4-allow lane test and the N-dbt parking test). Consistent with the implementer's counts.

## Ruling corrections — judged sound
1. Render both `is_incremental()` variants (not concatenate branches): strictly more faithful, and I confirmed
   a read hidden in either branch is caught in that branch. Sound.
2. "one query" = sqlglot `exp.Query` (SELECT / top-level set op / parenthesised query), DuckDB-typed SELECT;
   every inner table still judged. Sound (set ops verified safe).
3. Tightenings (`model-paths` exactly `[models]`; profiles part of the surface; source `schema` must be the
   `var('src_schema')` form; `alias`/`unique_key` identifiers; `materialized`/`incremental_strategy` closed
   sets; a test-argument string holds no `(`; both parsers consulted; a hook holds no comment). Each closes a
   real dbt-render/SQL-splice vector; none weakens the ruling. Sound.

## Out-of-scope observations (non-blocking; not bypasses)
- **NTFS ADS / hardlinks.** `is_link` catches symlinks/junctions but not an alternate data stream or a
  hardlink. Neither is a bypass: dbt reads only a file's main stream (the ADS is dead data), and a hardlinked
  model file is still read as SQL and judged; the only hardlink-write surface (`compile_check.json`) writes
  the gate's own ERROR report, and creating either requires filesystem syscalls the policy'd Write tool does
  not expose. Worth a defence-in-depth note only.
- **Over-strict refusals** (`var( 'src_schema' )` with inner spaces; a dash in a model *filename*; a model
  using an `iff()`-style function sqlglot's DuckDB dialect does not know) refuse some otherwise-valid dbt.
  Fail-closed and the committed trees avoid them, but a real translator may hit them — a robustness/backlog
  item, not a security hole.
- Disclosed limitations (concurrent-writer TOCTOU outside the orchestrated flow; `json_serialize_sql` needs
  the json extension or every model is refused) are both fail-closed and correctly documented.

## Verdict

**Fix round: All findings addressed, no new Critical/Important breakage.** C1 (arbitrary code / arbitrary
file read-write on the dbt validation path) is closed by a closed-surface gate enforced before any dbt
process starts, and I could not defeat it with 241 adversarial payloads across Jinja, YAML, SQL (both
parsers), config kwargs, hooks, the file set and every choke point. N-dbt, M1, M2, M3 verified present and
green. Ready for the production setting on this finding.
