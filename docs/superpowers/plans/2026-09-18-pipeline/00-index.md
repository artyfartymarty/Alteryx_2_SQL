# Alteryx → Snowflake Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build milestones M0–M2 of the migration program as tested code that runs end to end offline, asks the user to map yxdb files to Snowflake tables, and drives one workflow through the real Copilot SDK on a local BYOK model.

**Architecture:** Deterministic Python scripts communicate through files under `workflows/<wf_id>/`; a TypeScript orchestrator runs the state machine and calls agents through an `AgentRunner` seam. Snowflake, the Alteryx engine and Copilot agents each have a local double (DuckDB + sqlglot, a DAG simulator, a mock runner) behind the same interface as the real thing.

**Tech Stack:** Python 3.14 (`duckdb` 1.5, `sqlglot` 30, `pyyaml`, `pytest`), Node 22 via `fnm` (`@github/copilot-sdk` 1.0.14, TypeScript, `node:test`), GitHub Copilot CLI 1.0.86, PrismML llama.cpp fork serving `Ternary-Bonsai-2-27B-PQ2_0.gguf`.

**Spec:** [design](../../specs/2026-09-18-alteryx-snowflake-pipeline-design.md) · [program spec](../../../spec/00-README.md) · [agents and orchestrator](../../../spec/01-copilot-setup.md) · [artifact schemas](../../../spec/02-schemas-reference.md) · [dag contract](../../../reference/dag-contract.md)

**How this plan is written.** Every task fixes file paths, exact interfaces and the tests that define done. Full listings are given where the code is non-obvious (binary layouts, SQL rewriting, classification rules, the state machine). Elsewhere the interface block plus the tests are the contract and the implementer writes the body test-first. That is a deliberate departure from "every line in the plan": the build is about 10k lines and duplicating it here would make the plan unreviewable.

## Global Constraints

- Run Python as `.venv/Scripts/python.exe` from the repo root; tests as `.venv/Scripts/python.exe -m pytest`. Never install into the system Python.
- Run Node tools as `fnm exec --using=22 <tool>.cmd …` where fnm is `C:\Users\<user>\AppData\Local\Microsoft\WinGet\Links\fnm.exe`. Plain `node` on this PC is 20.18 and is too old for the SDK (`^20.19.0 || >=22.12.0`).
- Every script takes `--root PATH` (default `.`) and touches nothing outside it. Tests use `tmp_path`; no test writes into the repo's `workflows/`.
- Exit codes: `0` success, `1` domain failure (invariant violation, FAIL verdict, compile error), `2` usage or unexpected error.
- Files are UTF-8 with LF endings; JSON is written with `indent=2` and a trailing newline.
- Status vocabulary: `PENDING DONE PARSED RECOVERED QUARANTINED READY WAITING_FOR_ANSWERS BLOCKED NEEDS_HUMAN MANUAL VALIDATED OPEN`. Verdicts: `PASS PASS_WITH_ACCEPTED_DIFF FAIL BLOCK`. Diff classes: `ROUNDING ORDERING NULL_SEMANTICS TRUNCATION TYPE LOGIC GOLDEN_DATA UNKNOWN`. No other values.
- Numbers about data come only from scripts. Agents and mocks never write a count, diff or tolerance.
- Scripts never guess a table name or a tool's semantics. Unresolved stays unresolved; unknown stays `unknown`.
- SQL is Snowflake dialect: one CTE per tool named `t<toolid>_<type>`, preceded by a `-- tool <id>: …` comment.
- Nothing here has run on Snowflake or Alteryx. Any doc that could imply otherwise says so.
- Commit after each task with a conventional message ending in `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- TDD: write the failing test, watch it fail, implement, watch it pass, commit.

## File structure

```
.github/agents/*.agent.md            nine agent definitions (single source for CLI and SDK)
.github/copilot-instructions.md      repo-wide rules
config.json                          Copilot CLI config to copy into COPILOT_HOME
orchestrator.config.json             profiles: local (BYOK) and hosted; python path; budgets
orchestrate.ts                       entry point → orchestrator/cli.ts
orchestrator/{manifest,policy,agents,runner,stages,cli,types}.ts
orchestrator/test/*.test.ts
scripts/lib/{paths,vocab,io,typed_csv,yxdb,backend,proc_runner,types_map}.py
scripts/parsers/{plugin_map,registry,tool_config}.py   scripts/parsers/ext/*.py
scripts/{parse,invariants,segment,inject_outputs}.py
scripts/{load_golden,gen_source_views,compile_check,compare,validate_segment}.py
scripts/{intake_touchpoints,intake_prompt}.py
scripts/dev/{formula,alteryx_sim,build_samples}.py     scripts/dev/serve_model.ps1
samples/wf_000N/{source,golden_inputs,expected_sql,broken_sql,canned}/   samples/wf_000N/sample.json
cookbook/index.md  cookbook/<tool>.md                  tests/cookbook_examples/<tool>/
tests/parser_corpus/<name>/                            tests/*.py
mappings/global.yaml   catalog/columns.csv             snowflake/*.sql
workflows/<wf_id>/…                                    committed result of the offline run
```

## Shared contracts

**C1 · Typed CSV.** Golden data is `<name>.csv` plus `<name>.schema.json`. CSV: header row, `,` delimiter, RFC 4180 quoting, NULL is the two characters `\N`, empty string is empty. Bool is `true`/`false`; Date `YYYY-MM-DD`; DateTime `YYYY-MM-DD HH:MM:SS`; numbers in plain decimal notation. Schema: `[{"name": "AMOUNT", "type": "Double", "size": 8, "scale": null}]` using Alteryx types. `scripts/lib/typed_csv.py` exposes `read_table(csv_path) -> Table` and `write_table(csv_path, table)`, where `Table = {"fields": [field…], "rows": [[value…]…]}` and values are Python `None | bool | int | float | decimal.Decimal | str` (dates stay ISO strings).

**C2 · Golden layout** under `workflows/<wf>/golden/`: `inputs/<set>/<tool_id>.csv`, `targets_before/<set>/<logical>.csv` (only for `update_insert`/`append` outputs), `intermediates/<seg>/<set>/<stream>.csv`, `outputs/<set>/<tool_id>.csv`. A *stream* is `<toolid>_<anchor>`, e.g. `31_U`. Sets: `normal period_end empty edge`.

**C3 · Sandbox tables.** Golden inputs load to `MIG_GOLDEN.<WF>_<SET>_IN_<toolid>`; a view schema `MIG_GOLDEN_<WF>_<SET>` exposes each by its logical name. Segment outputs are `MIG_WORK.<WF>_<SEG>_OUT` for the primary stream and `MIG_WORK.<WF>_<SEG>_OUT_<stream>` for others. `<WF>` is the id upper-cased without underscore (`wf_0001` → `WF0001`); `<SEG>` is `SEG_01`. Test targets live in `MIG_WORK` under their logical name. The local database file is `workflows/<wf>/.sandbox.duckdb`.

**C4 · Procedure signature.** `MIG_WORK.<WF>_<SEG>(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING) RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER`. Sources: `IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.<LOGICAL>')`. Final targets: `IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.<LOGICAL>')`. Upstream segment tables and this segment's `_OUT` tables are written literally as `MIG_WORK.…`. The body is `BEGIN`, a linear list of plain statements, `RETURN 'OK';`, `END;`. No `LET`, `DECLARE`, loops or `EXECUTE IMMEDIATE`.

**C5 · contract.json** follows the program schema plus: `outputs` (list; every outbound stream and final target: `{"stream": "31_U" (for a target: the stream feeding its Output tool), "table": "MIG_WORK.WF0003_SEG_02_OUT", "kind": "work"|"target", "logical": null|"GL_SUMMARY", "columns": […], "keys": […]}`; `output` stays and equals `outputs[0]`; `kind: "target"` entries also carry `"tool_id"` of the Output tool and their golden file is `golden/outputs/<set>/<tool_id>.csv`), `inputs[].logical` for mapped sources, `inputs[].stream` for inputs that come from an upstream segment (which golden intermediate feeds them), and `ordering.order_dependent_columns` (columns whose values depend on row order, such as a Record ID).

**C6 · mappings.yaml** follows the program schema plus `logical:` on every source and output, e.g. `"sales/orders.yxdb": { snowflake: SALES.RAW.ORDERS, logical: ORDERS, tool_ids: ["1"], confirmed_by: wf_owner }`.

**C7 · Segment dag** `segments/seg_NN/dag.json`: `{"workflow", "segment", "nodes": […], "edges": [internal…], "inbound": [edge + "from_segment": "seg_01"|null], "outbound": [edge + "to_segment": "seg_03"|null]}`.

**C8 · Normalized source key** (for `global.yaml` lookups): DB inputs → `alias:<alias>`; DB OUTPUTS → `alias:<alias>/<table lower-cased>` (one alias serves many tables, and an input and an output on the same alias must not collide); files → lower-case, backslashes to `/`, strip a drive (`c:/`) or a leading `//`, then keep the last two path components: `C:\data\sales\orders.yxdb` → `sales/orders.yxdb`; `\\fileserver\crm\customers.yxdb` → `crm/customers.yxdb`; and the program spec's own example `\\fin\gl_2024.yxdb` → `fin/gl_2024.yxdb`.

## Tasks

| # | Task | Phase file | Depends on |
|---|------|-----------|------------|
| 1 | Foundations: tooling config, `lib/paths`, `vocab`, `io`, `typed_csv`, repo rules | [01](01-foundations-parser.md) | — |
| 2 | `lib/yxdb.py` reader and writer | 01 | 1 |
| 3 | Five sample workflow sources and golden inputs | 01 | 1 |
| 4 | `parse.py`, plugin map, extension registry, `invariants.py`, parser corpus | 01 | 1, 3 |
| 5 | `segment.py` | 01 | 4 |
| 6 | SQL runtime: `backend`, `proc_runner`, `types_map`, `load_golden`, `gen_source_views`, `compile_check` | [02](02-sql-runtime-compare.md) | 1 |
| 7 | `compare.py` | 02 | 6 |
| 8 | `dev/formula.py` and `dev/alteryx_sim.py` | [03](03-simulator-samples.md) | 1 |
| 9 | `dev/build_samples.py`: yxdb generation and golden sets | 03 | 2, 4, 5, 8 |
| 10 | Intake: `intake_touchpoints.py`, `intake_prompt.py` | [04](04-intake-agents-cookbook.md) | 2, 4, 6, 9 |
| 11 | `inject_outputs.py` | 04 | 4, 5 |
| 12 | Agents, `config.json`, cookbook v1 with runnable examples, Snowflake DDL | 04 | 6 |
| 13 | Hand migrations: contracts, `expected_sql`, `broken_sql`, canned agent artifacts | [05](05-migrations-orchestrator-live.md) | 7, 9, 12 |
| 14 | `validate_segment.py` and end-to-end parity tests | 05 | 13 |
| 15 | Orchestrator | 05 | 1 (schemas only); integration needs 14 |
| 16 | Live BYOK smoke test | 05 | 15 |
| 17 | Offline full run committed to `workflows/`, README, final verification | 05 | all |

Tasks 2, 3, 6, 8, 12 and 15's unit-tested core touch disjoint files and can proceed in parallel after Task 1.
