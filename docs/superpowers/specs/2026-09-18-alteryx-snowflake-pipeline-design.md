# Alteryx → Snowflake migration pipeline — implementation design

Date: 2026-09-18 · Status: approved by owner in session · Source spec: [docs/spec/](../../spec/00-README.md)

This document says how the three-file program spec is built **on this machine**, where there is no
Alteryx engine, no Snowflake account and no hosted Copilot model. It does not restate the program
spec; where the two disagree, the "Deviations" section below wins and says why.

## 1. Goal and success criteria

Build milestones M0–M2 of the program spec as working, tested code, plus the M4 Snowflake objects as
DDL files, such that:

1. `pytest` and `npm test` pass from a clean checkout with no network, no Alteryx, no Snowflake.
2. Five synthetic Alteryx workflows run end to end through the orchestrator with a mock agent runner
   and finish `VALIDATED`, `MANUAL` or `QUARANTINED` as designed.
3. `compare.py` reports `PASS` on every hand-written procedure across all four golden sets and `FAIL`
   with the expected diff class on every deliberately broken one (the M1 exit criterion).
4. The pipeline **asks the user** to name the Snowflake table for every `.yxdb` (and other) input and
   output, proposing a default, and records the answers.
5. One workflow runs through the real Copilot SDK against the local BYOK model. Success here means the
   mechanics work (sessions, hooks, permission policy, `ask_user`, artifacts written). It does not
   mean a ternary 27B model writes parity-passing SQL; the fix loop and `NEEDS_HUMAN` exist for that.

## 2. Verified environment (2026-09-18)

| Item | Finding |
|------|---------|
| Node | system `v20.18.0` stays the default; `v22.23.2` installed side by side with `fnm` 1.39.0 and pinned by `.node-version`. The SDK requires `^20.19.0 \|\| >=22.12.0`. |
| Running Node tools | `fnm exec --using=22 <tool>.cmd …` — on Windows `npm`/`copilot` must be invoked as `npm.cmd`/`copilot.cmd` because `fnm exec` spawns without shell resolution. |
| Copilot CLI | `@github/copilot` 1.0.86, global under Node 22. Needs PowerShell 6+: PowerShell 7.6.6 installed (`WindowsApps\pwsh.exe`). |
| Copilot SDK | `@github/copilot-sdk` (bundles its own CLI). |
| Python | 3.14.2, project `.venv` with `duckdb`, `sqlglot`, `pyyaml`, `pytest` (all have cp314 wheels). |
| GPU | RTX 5070 Ti 16 GB, driver 616.92. |
| Model | `Ternary-Bonsai-2-27B-PQ2_0.gguf`, 7,206,168,928 bytes (matches Hugging Face `x-linked-size`), in `C:\Users\<user>\models\Ternary-Bonsai-2-27B\`. |
| Model runtime | Stock llama.cpp cannot load `PQ2_0`. PrismML fork release `prism-b10685-7dffb15`, **CUDA 13.3** Windows build, in `C:\Users\<user>\tools\llama-prism\`. The newer `b10687` release was published with only `cudart` bundles, so it is not usable yet. |
| Model smoke test | Loads fully on GPU; OpenAI-style tool calling returns structured `tool_calls`; 223 tok/s prompt, 73 tok/s decode; 12,926 MiB VRAM in use at 32K context against a 3,230 MiB desktop baseline, so about 9.7 GiB for the model. |
| Not installed | `gh` (issue/PR steps degrade gracefully), Alteryx, Snowflake. |
| Git | The project folder sat inside an accidental home-directory repo; it now has its own repo on `main`. |

## 3. Approach: offline doubles behind the production seams

Every external system is reached through an interface with a real implementation and a local one.

| External system | Interface | Real implementation | Local implementation |
|-----------------|-----------|---------------------|----------------------|
| Snowflake | `scripts/lib/backend.py: SqlBackend` | `SnowflakeBackend` (lazy `snowflake-connector` import; untested here, marked so) | `DuckDBBackend`: Snowflake SQL → `sqlglot` → DuckDB |
| Alteryx engine | golden-set producer | `inject_outputs.py` + `AlteryxEngineCmd.exe` | `scripts/dev/alteryx_sim.py` |
| Copilot agents | `orchestrator/runner.ts: AgentRunner` | `CopilotRunner` (SDK, BYOK or hosted) | `MockRunner` replaying canned artifacts |

Rejected alternatives: `fakesnow` (no stored-procedure support, heavy dependency) and parse-only SQL
checking (leaves `compare.py` unproven against real translated SQL).

**Honesty rule.** Sample procedures are genuine Snowflake dialect. They are verified by the sqlglot
Snowflake parser and by execution on DuckDB after transpilation. Nothing in this repo has run on a
real Snowflake account, and the docs say so wherever it matters.

### 3.1 The procedure subset the local runner executes

Snowflake Scripting cannot be transpiled, so translated procedures follow one shape, which the
reviewer enforces and `scripts/lib/proc_runner.py` executes:

```sql
CREATE OR REPLACE PROCEDURE MIG_WORK.WF0001_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  ALTER SESSION SET TIMEZONE = 'America/New_York', WEEK_START = 1;
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0001_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data (orders.yxdb)
  t1_input AS (SELECT … FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')),
  …
  SELECT … FROM t9_select;
  RETURN 'OK';
END;
$$;
```

The body is a linear list of plain SQL statements. The runner extracts the `$$` body, splits
statements, substitutes binds, resolves `IDENTIFIER(...)`, records and skips `ALTER SESSION`,
rewrites `DB.SCHEMA.T` to a DuckDB schema `DB__SCHEMA`, transpiles, and executes.

`EXECUTE AS CALLER` is used because owner's-rights procedures restrict session changes. This is
flagged in the cookbook as **verify on your account**.

## 4. Deviations from the program spec

1. **Logical source names.** `SRC_DB`/`SRC_SCHEMA` cannot express a workflow that reads two production
   schemas, and golden tables are named differently from production tables. Procedures therefore
   reference only a *logical* name inside `SRC_DB.SRC_SCHEMA`. `scripts/gen_source_views.py` generates
   the view-schema mapping logical names to golden tables (test) or real tables (production), from
   `mappings.yaml`. Each source gains a `logical:` key. The rule "never name production schemas in
   SQL" stays true.
   The same indirection applies to final **targets**: procedures take `TGT_DB` and `TGT_SCHEMA` and write
   to a logical name, so a test run lands in `MIG_WORK`, a shadow run in `__SHADOW` tables, and production
   in the real table, all with one body. The signature is therefore
   `(SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID)`.
   A segment can also feed more than one downstream consumer (a Join's L, J and R outputs, or two Output
   tools), so `contract.json` gains an `outputs[]` list; the program schema's single `output` is kept and
   equals `outputs[0]`.
2. **Agents are loaded by the orchestrator.** The SDK does not document auto-loading
   `.github/agents/*.agent.md`. `orchestrator/agents.ts` parses frontmatter and body and passes them as
   `customAgents`, selecting one with `agent: <role>`. The `.agent.md` files stay the single source for
   both interactive CLI use and the SDK.
3. **`compare.py` pre-labels diff classes** deterministically (the M1 exit criterion needs "the right
   cluster class" without an agent). The validator agent may refine the label and must explain it.
4. **Golden files are CSV plus a schema JSON**, not Parquet: diffable in review and no `pyarrow`.
5. **`gh` is optional.** When absent, issue/PR creation is logged and skipped; status is unchanged.
6. **Idempotency means determinism.** The program spec says "run twice on the same golden set". An Append
   output legitimately doubles when run twice without a reset, in Alteryx too, so the check here is: two
   runs from the *same starting state* must produce identical outputs. That still catches
   non-deterministic functions and unordered order-dependent tools.
7. **Model routing collapses under BYOK.** `orchestrator.config.json` holds a role → model map. The
   local profile maps every role to the one local model; the hosted profile keeps the spec's
   `gpt-6-astra` / `gpt-5.6-luna` split.
8. **`EXECUTE AS CALLER`, not the spec's `execute_as: OWNER`.** Contract C4: every generated
   procedure runs as its caller. The spec's production runtime defaults to owner's rights; this
   build uses caller's rights everywhere because an owner's-rights procedure cannot run
   `ALTER SESSION` (needed here to set `TIMEZONE`/`WEEK_START`), and because it is the stricter,
   safer default when nothing here has been verified against a real account — flagged in the
   cookbook as "verify on your account".
9. **Row-scope and schema-scope differences are never approvable.** `compare.py`'s `_approved` only
   ever signs off a cluster whose `scope` is `"columns"` and whose `columns` list is non-empty: a
   row-presence cluster, a duplicated key, a schema failure or a synthetic cluster (an unexplained
   check, or a truncated diff) always ends in `FAIL`, whatever `accepted_diff_classes` says. On top
   of that (coordinator ruling, final review), `GOLDEN_DATA` and `UNKNOWN` are never approvable
   either, at ANY scope: `GOLDEN_DATA` is a fault in the reference data itself (e.g. a NOT NULL
   violation `_nullability_clusters` finds in the golden data, which does carry `scope: "columns"`),
   which no human signature over a translation difference can repair, and `UNKNOWN` by definition
   names nothing a human could be signing off on.
10. **Keyless streams are classified by nearest-match pairing.** Every `edge` golden set carries
    byte-identical duplicate rows on purpose, so a stream with no declared key cannot join golden
    and actual rows on anything. `compare.py` instead pulls each side's surplus rows (the ones
    `set_diff` already counts) and pairs each expected row with the not-yet-used actual row it
    disagrees with in the fewest columns — accepted only when at least half the columns agree, and
    reported with `"paired_by": "nearest_match"`. A heuristic about *which* two rows to compare, not
    about how many differences there are: `set_diff` itself stays the exact SQL multiset difference.
11. **A parked stage needs an explicit `--from-stage` to resume, and idempotent re-runs are more than
    "skip what's `DONE`".** The program spec's own phrasing — "the orchestrator skips any stage
    already `DONE`/`PASSED`" — undersells what an ordinary re-run actually does. The orchestrator
    skips any stage already at a success status (`DONE`/`PARSED`/`RECOVERED`/`READY`/`VALIDATED`/
    `MANUAL`/`OPEN`). A stage at `NEEDS_HUMAN`, `QUARANTINED` or `BLOCKED` is different: it is
    **parked**, not done, so a plain re-run does not retry it and does not run any later stage of
    that workflow either — the run logs one line naming the stage, the status and the resume
    command, and only an explicit `--from-stage <stage>` reopens it and grants it a fresh tool-call
    budget. `WAITING_FOR_ANSWERS` is not an escalation and keeps re-running intake's scripts every
    pass, so a newly merged answer can move it forward without anyone typing `--from-stage`.

## 5. Interactive intake (the yxdb → table requirement)

Principle 2 of the program spec applies: enumeration and prompting are deterministic code; the agent
adds judgment (tier, plan, risks, ambiguous cases).

- `scripts/intake_touchpoints.py <wf>` reads `dag.json` and writes `intake/touchpoints.json`: every
  input (yxdb, csv, xlsx, DB alias with scrubbed connection, embedded SQL), output (target, write mode,
  keys, pre/post SQL), macro, constant, app parameter and T3 tool. For a reachable `.yxdb` it reads the
  field list and record count from the file header via `scripts/lib/yxdb.py`. It resolves each
  touchpoint against `mappings/global.yaml` by normalized key, then proposes candidates ranked by
  column overlap and name-token similarity against a catalog (`INFORMATION_SCHEMA.COLUMNS` through the
  backend, or `catalog/columns.csv`), falling back to a naming convention
  (`gl_2024.yxdb` → `<target_db>.RAW.GL_2024`).
- `scripts/intake_prompt.py <wf>` asks one question per unresolved touchpoint:

  ```
  Tool 12 · Input Data reads fin\gl_2024.yxdb (14 fields, 1,200,000 rows)
    1) FINANCE.RAW.GL_LEDGER   13/14 columns (missing POSTING_FLAG)
    2) FINANCE.RAW.GL_2024     naming convention
  Snowflake table [1]:  <Enter = accept · number · DB.SCHEMA.TABLE · ? = defer>
  ```

  Outputs are asked for target, write mode and MERGE keys. It validates the FQN shape, never invents a
  table, writes `intake/mappings.yaml` with `confirmed_by`, promotes reusable answers to
  `mappings/global.yaml`, and regenerates `open_questions.md` with answered boxes ticked.
- Deferred or unanswered blocking items leave unchecked boxes → `WAITING_FOR_ANSWERS`, as in the spec.
- The orchestrator runs the prompt when stdin is a TTY (`--interactive`, `--no-interactive`). In live
  sessions the same prompt backs the SDK `onUserInputRequest` handler so the intake agent can ask
  through `ask_user`; unattended, the handler reports the user is unavailable.

## 6. Components

Python, `scripts/` (each accepts `--root` so tests run in temp directories):

| Unit | Purpose | Depends on |
|------|---------|------------|
| `lib/yxdb.py` | Read yxdb header, `RecordInfo`, record count, records (LZF); write yxdb for fixtures | stdlib |
| `parse.py`, `parsers/registry.py`, `parsers/ext/` | XML → `dag.json` + `parse_report.json`; containers, macros, anchors, scrubbing, constants, CDATA, `.yxzp` | `invariants.py` |
| `invariants.py` | The spec's six structural checks plus Join-has-two-inputs | — |
| `segment.py` | Cut heuristics → `segments/seg_NN/dag.json`, `order.json` | `dag.json` |
| `lib/backend.py`, `lib/proc_runner.py` | SQL backends; local procedure execution | duckdb, sqlglot |
| `gen_source_views.py`, `load_golden.py` | Logical-source views; golden CSV → tables | backend |
| `compile_check.py` | Parse as Snowflake, then dry-run on an empty-schema sandbox | proc_runner |
| `compare.py` | Schema → counts → aggregates → set diff → row hash → tolerances → `validation.json` | backend |
| `inject_outputs.py` | Instrument a workflow XML with capture Output tools | parse |
| `intake_touchpoints.py`, `intake_prompt.py` | §5 | yxdb, backend |
| `dev/alteryx_sim.py`, `dev/formula.py` | Test-only Alteryx interpreter and formula evaluator | `dag.json` |
| `dev/build_samples.py`, `dev/serve_model.ps1` | Generate yxdb and golden sets; start llama-server | sim |

TypeScript, `orchestrator/`: `manifest.ts`, `policy.ts` (pure permission decisions), `agents.ts`,
`runner.ts`, `stages.ts` (state machine, loops, budgets, error routing), `cli.ts`; `orchestrate.ts` is
the thin entry point. Flags: `--only`, `--from-stage`, `--tier`, `--dry-run`, `--runner mock|copilot`,
`--profile local|hosted`, `--interactive`.

Also: nine `.github/agents/*.agent.md`, `.github/copilot-instructions.md`, `config.json`,
`mappings/global.yaml`, cookbook v1 (about 15 tool pages, each with a runnable example under
`tests/cookbook_examples/`), and `snowflake/` DDL for `RUN_LOG`, `RECON_RESULTS`, shadow tables, the
reconciliation task and alerts.

## 7. Sample workflows

Sources live in `samples/<wf>/` (`source/`, `golden_inputs/<set>/`, `expected_sql/`, `broken_sql/`,
`canned/`). `workflows/` holds the committed result of the offline end-to-end run as worked examples.

| Id | Exercises |
|----|-----------|
| `wf_0001_sales_summary` | yxdb input, Select `String(n)` truncation, Filter NULL→False, Formula (`IIF`, `ToNumber`, `Round`), Summarize, Sort, Output overwrite |
| `wf_0002_customer_orders` | yxdb + csv inputs, Data Cleansing, Join with L/J/R all consumed, Union, nested Tool Containers, two append Outputs |
| `wf_0003_gl_period_close` | DB input with credentials to scrub, CDATA SQL, workflow constants, DateTime, Sort→Unique→Multi-Row Formula→Record ID (must not be split), Output "Update; Insert if new" → `MERGE` with pre/post SQL |
| `wf_0004_inventory_macro` | `.yxmc` macro with Interface parameters, Cross Tab (frozen headers), Transpose, RegEx |
| `wf_0005_vendor_tool` | unknown vendor plugin (parser-recovery path) and Run Command (T3 → `MANUAL`) |

Parser corpus fixtures: UTF-8 BOM, cp1252 bytes, `.yxzp` package, `.yxwz` app, locked workflow
(`QUARANTINED`). Golden sets per workflow: `normal`, `period_end`, `empty`, `edge`.

## 8. Error handling

Follows the program spec's routing table. Added locally: a missing `gh`, a missing Snowflake
connector or an unreachable model endpoint produce a clear one-line message and a non-zero exit from
the affected command; they never produce a fabricated artifact. `compare.py` exits 0/1/2 for
PASS/FAIL/error. Scripts never guess a table or a tool's semantics: unknown stays `unknown`.

## 9. Testing

- `pytest`: yxdb round-trip; parser (each sample, each corpus fixture, extension registry); each
  invariant; segmentation rules; formula evaluator and simulator semantics against hand-computed
  values (the simulator is the oracle, so it is tested independently of the SQL); proc runner;
  `compare.py` for every diff class, tolerance edges, early stop on schema failure and exit codes;
  intake with scripted stdin; cookbook examples; end-to-end parity for 5 workflows × 4 golden sets
  and the broken variants.
- `node --test`: happy path, `WAITING_FOR_ANSWERS` then resume, three failed iterations →
  `NEEDS_HUMAN`, two failed recoveries → `QUARANTINED`, T3 → `MANUAL`, idempotent re-run, and every
  `policy.ts` denial.
- `tsc --noEmit` against the installed SDK types, which settles the spec's "verify against your
  build" list for option and hook names.
- Live smoke test: one command, documented, run last.

## 10. Out of scope

Real Snowflake or Alteryx connectivity, CI deployment, actual shadow runs and cutover, Snowpark (T2)
translation, the cookbook-curator batch loop beyond its agent definition, and OpenTelemetry export.
