# Final whole-branch review — output targets, phase 1

Branch `feat/output-targets` @ `204c98d` (36 commits over `main@e3052fe`). Spec:
`docs/superpowers/specs/2026-09-22-output-targets-design.md` (phase 1 = everything except §4.3 /
§5.3 / §7.3 dbt, the cookbook pages, `prompt_context.py`, live tests). Reviewed read-only; nothing
in the tree was modified, staged or committed. All probe work happened in a scratch root.

**Verdict up front: needs one fix wave.** No Critical. Six Important, ten Minor. The parity gate
itself held under every adversarial edit that changes what the procedure *promises* (schema, rows,
values, NULLs); the gaps are in the AST rule set's write surface, in `target_check.py`'s blindness
to macro sub-DAGs, and in two shipped `scratchpad/` references.

---

## 1. Spec coverage

| Spec requirement | Where it lives on the branch | Status |
|---|---|---|
| §3.1 `target_check.py <wf> [--prefer …] [--root .]` → `segments/targets.json` | `scripts/target_check.py` | ✅ |
| §3.1 node-class table, machine-readable | `scripts/parsers/plugin_map.py:124` `TARGET_CLASS`, `:131` `node_class` | ✅ (but see I3) |
| §3.1 segment is `snowpark` if any data node is | `scripts/target_check.py:108` | ✅ (but see I3) |
| §3.1 the eight dbt blockers | `scripts/target_check.py:37-64` (`snowpark_segment`, `manual_node`, `unknown_node`, `merge_without_keys`, `write_mode_unsupported`, `presql_not_plain`, `postsql_not_plain`, `no_outputs`) | ✅ (but see I3) |
| §3.1 exit 0 / 1 (unknown, still written) / 2 (usage) | `scripts/target_check.py:127-142`; consumed at `orchestrator/stages.ts:361-363` | ✅ |
| §3.2 analyzer runs it, copies target into `contract.json`, may only lower | `.github/agents/analyzer.agent.md` step 3; task text at `orchestrator/stages.ts:365-370` | ✅ |
| §3.2 orchestrator verify: every contract has `target`; none higher than the proposal | `orchestrator/stages.ts` `checkTargets` (`TARGET_RANK`, `:287-330`) | ✅ |
| §3.2 `output_kind = dbt` only if proposed dbt **and** every contract still `sql` | `orchestrator/stages.ts:325-330` | ✅ |
| §3.2 contradiction parks `NEEDS_HUMAN` `target-mismatch: <detail>` / `target-missing: <seg>` | `orchestrator/stages.ts:305-321`, `escalate` at `:70` | ✅ |
| §3.3 `global.yaml program.output_target` + `snowpark_runtime` | `mappings/global.yaml` (`output_target: procedures`, `snowpark_runtime: "3.11"`, both commented) | ✅ |
| §3.3 `sample.json.output_target` → `manifest.output_target` via the seed | `scripts/dev/build_samples.py:374` | ✅ |
| §3.3 **interactive intake asks it when `global.yaml` has no value** | — nowhere | ❌ **GAP (M1)** |
| §3.3 orchestrator passes the preference | `orchestrator/stages.ts:361` (`--prefer auto`; ruled: the script owns resolution) | ✅ (ruling) |
| §4.1 SQL procedure + `master.sql` unchanged | untouched; `workflows/wf_0006/procs/master.sql` calls all three segments identically | ✅ |
| §4.2 `proc.py` source of truth, one `run(session, …) -> "OK"` | `scripts/lib/snowpark_rules.py:48,159-165` | ✅ |
| §4.2 import allow-list / forbidden names | `scripts/lib/snowpark_rules.py:37-41,167-184` | ✅ |
| §4.2 read forms (`session.table(f"{src_db}…")` / literal `MIG_WORK.…`) | `:152-157,191-201` | ⚠️ shape-only for the `{src_db}`/`{tgt_db}` forms — **I4** |
| §4.2 write forms (`.write.mode(…).save_as_table(…)` / `.merge(…)`) | `:191-201` | ⚠️ only two method names are checked — **I1** |
| §4.2 one `# tool <id>:` comment per data node | `:49,77-89,202-207`; data nodes from the segment DAG at `scripts/compile_check.py:144-153` | ✅ |
| §4.2 no other function may take `session` | `scripts/lib/snowpark_rules.py:126-143` | ⚠️ only the literal name `session` — **I2** |
| §4.2 rendered wrapper, byte-deterministic, committed, pinned by a test | `scripts/render_snowpark.py:15-33`; `tests/test_canned_artifacts.py:334-358`; `tests/test_committed_workflows.py:202` | ✅ |
| §4.2 `master.sql` calls it like a SQL segment; `parse_proc` learns `LANGUAGE PYTHON` | `scripts/lib/proc_runner.py:51,58,149-162` (`ProcInfo.language`, body kept whole) | ✅ |
| §5.1 `compile_check.py --target sql\|snowpark` (default from the contract) | `scripts/compile_check.py:76-98,129-141,300-301` | ✅ (`dbt` correctly absent) |
| §5.1 snowpark gate = `py_compile`+AST rules, `proc.sql` byte-match, C4 signature, `EXECUTE AS CALLER` | `scripts/compile_check.py:129-203` | ✅ (`ast.parse` stands in for `py_compile`) |
| §5.1 exit 0/1/2 | `scripts/compile_check.py:296-316` | ✅ |
| §5.2 `validate_snowpark.py <wf> <seg> [--set …] [--proc FILE]` | `scripts/validate_snowpark.py:435-457` | ✅ |
| §5.2 fresh `local_testing` session per run; goldens via the same CSV readers | `:81-123` (`_session`, `load_set_snowpark`, `read_table`) | ✅ |
| §5.2 Snowpark `StructType` mapping beside the SQL one | `scripts/lib/types_map.py:69-101` `alteryx_to_snowpark` (+ `:110-148` inverse) | ✅ |
| §5.2 `targets_before` under `MIG_WORK` for append/merge | `scripts/validate_snowpark.py:119-122` | ✅ |
| §5.2 read every `contract.outputs[]` back, type it, compare with `compare.compare()` | `:214-251,301-330` — schema read from Snowpark, **not** the contract | ✅ (stronger than the spec asked) |
| §5.2 idempotency / worst-of-sets / stale-report deletion / missing-table FAIL / empty-outputs usage error, **extracted, not copied** | `scripts/lib/validation.py` (used by both validators; `validate_segment.py` re-exports the old private names) | ✅ (idempotency semantics: **M3**) |
| §5.2 a Snowpark error inside `run` is a domain FAIL with `error` | `:296-299` → `validation.fail_report` | ✅ |
| §5.4 same `compare.py`, same tolerances/accepted diffs/verdicts | `compare.py` untouched; `:325-328` | ✅ |
| §6 `stageAnalyze` runs `target_check.py`, verify sets `output_kind` | `orchestrator/stages.ts:355-386` | ✅ |
| §6 `stageTranslate`/`migrateSegment` dispatches per segment target | `orchestrator/stages.ts:455-575` (render → compile `--target snowpark` → `validate_snowpark`) | ✅ |
| §6 `MockRunner` replays `proc.py`, broken variants by extension | `orchestrator/runner.ts:112-118,174-200,218-231` | ✅ |
| §6 policy: new scripts on the per-role allow-lists; translator lane admits `proc.py` | `orchestrator/policy.ts:701-711`; lane regex `policy.ts:197` (pre-existing) | ✅ |
| §6 `docs/spec/**` stays verbatim | `git diff e3052fe 204c98d -- docs/spec/` is empty — verified | ✅ |
| §6 `docs/reference/output-targets.md` | present, 229 lines, accurate | ✅ |
| §7.1 `sim_python`, restricted builtins, allow-list minus Snowpark, dunder AST refusal | `scripts/dev/alteryx_sim.py:778-915` | ✅ |
| §7.1 accident-guard wording in the docs "in those words" | spec §7.1, `docs/reference/simulator-semantics.md`, `docs/reference/dag-contract.md:206-215`, `alteryx_sim.py:794-804` | ✅ (scope nit: **M6**) |
| §7.1 `python` in `plugin_map`, marked "verify against your Alteryx version" | `plugin_map.py:32-33`; `dag-contract.md:48` | ✅ |
| §7.1 `Alteryx.read/write` shim, dtype map, NaN→NULL, column order | `alteryx_sim.py:822-877,886-905` (pandas type predicates, `pd.isna` everywhere) | ✅ |
| §7.2 `wf_0006` sample + committed offline run | `samples/wf_0006/**`, `workflows/wf_0006/**` (T2, VALIDATED, seg_02 snowpark) | ✅ |
| §8 agent files (analyzer, translator, reviewer, validator, fixer) | `.github/agents/*.agent.md`, pinned by `tests/test_agents_config.py:274-341` | ✅ |
| §8 `copilot-instructions.md` one paragraph | `.github/copilot-instructions.md:8-13` | ✅ |
| §8 `docs/reference/output-targets.md`, README section + sample table + deployment | `README.md:153-186, 446-460, 566-586` | ✅ |
| §8 `docs/handoff-copilot-models.md` unchanged except a pointer | 8 added lines, pointer only | ✅ |
| §9 what the doubles do not prove, in every relevant doc | `output-targets.md §6`, README, `validator.agent.md` step 4, `translator.agent.md`, `validate_snowpark.py` docstring | ✅ |
| §10 `tests/test_target_check.py`, `test_render_snowpark.py`, `test_compile_check_snowpark.py`, `test_validate_snowpark.py`, `test_alteryx_sim_python.py`, `test_snowpark_rules.py`, node dispatch/policy tests, `test_e2e_parity`/`test_canned_artifacts` extended, **zero skips** | all present; 1215 passed / 0 skipped | ✅ (§10 names the file `test_compile_check_targets.py`; it shipped as `test_compile_check_snowpark.py` — correct, since dbt is phase 2) |

Phase-2 items correctly absent: §4.3, §5.3, §7.3 (`wf_0007`), `cookbook/snowpark.md` + `cookbook/dbt.md`, `scripts/prompt_context.py`, live tests. `docs/reference/output-targets.md §3.3` states in terms that `output_kind: "dbt"` is recorded and nothing more.

**One spec requirement has no home: §3.3's interactive intake question** (M1).

---

## 2. Strengths

1. **The parity gate reads the *actual* schema, not the contract.** `validate_snowpark._read_back`
   (`scripts/validate_snowpark.py:214-251`) takes `session.table(fqn).schema` — a real `StructType`,
   in the table's own column order — and inverts it through `types_map.snowpark_to_alteryx`. This is
   the single most important design decision on the branch, and it is what made every schema probe
   below FAIL instead of confirming what the contract already assumed. The Task-4 review that forced
   it earned its keep.
2. **Every failure path is biased toward FAIL, never toward PASS.** `_read_back` returns `None` on
   *any* read error (→ `missing_table_report` → FAIL); a `ReadBackError` is a domain FAIL, not a
   usage error; a second run that cannot complete makes every output "diverging"; `idempotent` is
   never `True` without both runs actually compared; `clear_stale_reports` runs before the
   prerequisite checks so a usage error leaves no report at all. I could not find a path where an
   error is swallowed into a PASS.
3. **The AST rule set is genuinely conservative where it looks.** `session` scope, single top-level
   `run`, dunder refusal, keyword-argument refusal on `table`/`save_as_table`, `tokenize`-based tool
   comments, raw-SQL names refused even when merely imported. The nine probes from the task reviews
   plus my `E4_table_name_by_concat` were all refused. The gaps below are about *surface*, not rigour.
4. **`lib/validation.py` is a real extraction, not a copy.** `validate_segment.py` re-exports every
   name under its old private alias, so the SQL twin's 31 pre-existing tests kept working unchanged
   and the two validators cannot drift in report shape.
5. **Honesty discipline holds branch-wide.** `git grep` for any claim of a real Snowflake/Alteryx run
   returns only the five "No step of this migration has run against…" disclaimers. The Snowpark
   subset caveat is repeated in the spec, the reference page, the README, `validator.agent.md`,
   `translator.agent.md` and the module docstring.
6. **Test discipline.** 280 test functions in the new/changed Python test files; zero of them assert
   nothing (AST-scanned). Zero skips executed. `test_committed_workflows.py` pins the new invariants
   directly against `targets.json` rather than against a hard-coded expectation.
7. **`docs/reference/output-targets.md` is accurate.** Every command it shows exists and takes the
   flags it shows; the orchestrator behaviour table matches `stages.ts` line for line, including the
   exit-1-reaches-the-analyzer ruling and the two-reason-formats rule.
8. **`docs/spec/**` is byte-identical to `main`** (verified), and the one spec edit (§7.1's
   accident-guard sentence) is the controller's own recorded amendment.

---

## 3. Issues

### Critical
None.

### Important

**I1 — `rule:table_names` checks two method names out of a large write surface.**
`scripts/lib/snowpark_rules.py:191` triggers only on `ast.Call` whose `func` is an `ast.Attribute`
named `table` or `save_as_table`. Verified live against `snowflake-snowpark-python 1.55.0`:
`DataFrameWriter` also exposes `saveAsTable` (a real camelCase alias), `insert_into`/`insertInto`,
`copy_into_location`, `csv`, `json`, `parquet` and `save`. None is checked. Nor is a *rebound* bound
method: `sink = writer.save_as_table; sink("ANY.TABLE")` is an `ast.Attribute` that is not the
`func` of a call, so the rule never fires.
*Failure scenario:* a translator (or a fixer patching a write) emits
`df.write.mode("overwrite").saveAsTable("ANALYTICS.CURATED.ANYTHING")`. `compile_check --target
snowpark` reports `OK`, the reviewer's blocking list (which paraphrases the same rules) sees a
plausible write, `validate_snowpark` compares only the declared outputs and PASSes, and the
procedure ships writing to a table nobody declared. Both `saveAsTable` and the rebound method were
confirmed to actually create the table in the local session.
*Suggested fix:* in `snowpark_rules.py`, (a) add `saveAsTable`, `insert_into`, `insertInto` to the
checked call names; (b) add a `rule:no_raw_sql`-style flat refusal of the non-table sinks
(`copy_into_location`, `csv`, `json`, `parquet`, `save`) wherever they appear; (c) refuse any bare
`ast.Attribute` whose `attr` is one of the checked write names and which is *not* the `func` of a
call (i.e. the method is being referenced rather than invoked) — the same "conservative and
structural" move ruling 51 already made for `session`. One test per name.

**I2 — the `session` rule only knows the literal name `session`; the live session is reachable
without it.** `scripts/lib/snowpark_rules.py:37` allow-lists the module `snowflake.snowpark`, and
`:126-143` refuses `session` only as an `ast.Name` with `id == "session"`. Verified live:

```python
from snowflake.snowpark import Session          # allowed import
live = Session.builder.getOrCreate()            # returns the very session run() was handed
live.sql("DELETE FROM …")                       # never seen by any rule
```

`check_proc_py` returns `[]` for that module (reproduced). Locally the `.sql` call raises
`NotImplementedError: [Local Testing] Session.sql is not supported`, so `validate_snowpark` turns it
into a FAIL — that is the defence in depth working — but on a real account it executes, and
`session.call`, `session.file`, `session.udf`, `session.add_packages` etc. are reachable the same way
and have no local equivalent that would fail.
*Failure scenario:* the procedure passes compile check and local validation on a path that happens
not to touch the blocked method (e.g. `live.add_packages(...)`, or `live.sql` inside a branch the
golden sets never take), and reaches the deployment PR with raw SQL in it — exactly what ruling
"DataFrame API only (no `session.sql`)" exists to prevent.
*Suggested fix:* refuse the name `Session` (and `session_builder`/`builder` as an attribute chain
off it) outright — no C4 procedure has any business constructing or fetching a session; it is handed
one. Concretely: add `Session` to a new `FORBIDDEN_SNOWPARK_NAMES` checked as `ast.Name`,
`ast.Attribute.attr` and `from … import` alias, alongside `RAW_SQL_NAMES`. Also add `sql`, `call`,
`add_packages`, `add_import`, `udf`, `sproc` to the flat attribute refusal so the name the session
is bound to stops mattering.

**I3 — `target_check.py` never descends into a macro's `sub_dag`, so a Python / manual / unknown
tool hidden inside a macro is invisible to the whole decision.**
`scripts/target_check.py:104-107` walks `dag["nodes"]` and each `segments/<seg>/dag.json` `nodes`
only; `plugin_map.node_class:131-136` maps the type `macro` to `sql` (it is in `ANCHORS` and is not
`unknown`), and nothing else in `scripts/` or `orchestrator/` reads `sub_dag` (only `parse.py`
writes it). `workflows/wf_0004/parsed/dag.json` proves macro nodes with a populated `sub_dag` are a
real shape in committed data.
*Reproduced* in a scratch root (`macro_probe.py`): a workflow whose single macro's `sub_dag`
contains a `python` tool, a `run_command` tool and an `unknown` tool, with `program.output_target:
dbt`, yields

```json
{"preference": "dbt", "output_kind": "dbt", "reason": "preference dbt; every segment is sql and
 every output is dbt-expressible", "dbt_blockers": [], "segments": {"seg_01": "sql"}, "nodes": {}}
```

— exit 0. All three of `snowpark_segment`, `manual_node` and `unknown_node` are missed, the segment
is proposed `sql`, and `manifest.output_kind` is then mirrored as `dbt`. An unresolved macro
(`sub_dag: null`) is likewise classified `sql` rather than `unknown`.
*Failure scenario:* phase 2 reads `manifest.output_kind == "dbt"` and builds a dbt project for a
workflow containing a Python tool and a Run Command — precisely the case the blocker list exists to
refuse. In phase 1 the damage is contained only because the analyzer may still *lower* the segment
(`sql` → `snowpark`/`manual`), i.e. the deterministic layer's guarantee silently depends on the LLM
noticing what the script missed.
*Suggested fix:* in `target_check.target_check`, expand each node to itself plus, recursively, its
`sub_dag["nodes"]` when present, and classify an unresolved macro (`node.get("unresolved")`) as
`unknown`. Two tests: a macro-wrapped `python` tool makes the segment `snowpark`; a macro-wrapped
`unknown` node yields the `unknown_node` blocker and exit 1. If descending is deliberately out of
scope, say so in `docs/reference/output-targets.md §6` under "policy, verified only by its tests" —
but it must not be silent.

**I4 — the `{src_db}`/`{tgt_db}` table forms are checked by *shape* only, never against the
contract's declared names.** `scripts/lib/snowpark_rules.py:156-157` accepts any
`{tgt_db}.{tgt_schema}.<IDENT>`, while the literal `MIG_WORK.…` path at `:153-155,200` is checked
against `allowed_tables` (the segment's own out-table, its work streams and its declared inputs).
The two halves of the same rule enforce very different things.
*Reproduced:* probe `B5_extra_side_effect_table` — the canned `proc.py` with one extra
`…save_as_table(f"{tgt_db}.{tgt_schema}.SHADOW_COPY")` appended — is rules-clean **and validates
PASS on all four golden sets**. Nothing in the pipeline notices a second, undeclared table.
*Failure scenario:* a fixer "helpfully" materialises an intermediate to a scratch table under the
target schema; it passes compile check and validation, ships in `proc.sql`, and on the real account
creates or overwrites a table outside the migration's declared footprint (under `EXECUTE AS CALLER`,
with whatever the caller can write).
*Suggested fix:* build the permitted `<LOGICAL>` set from the contract — `inputs[].logical` for
`src_form`, `outputs[].logical` (kind `target`) for `tgt_form` — and require the identifier to be in
it, falling back to shape-only with a named warning check when the contract declares no logicals.
One test per direction.

**I5 — nothing tells the translator that the output columns must be in the contract's declared
order, but getting it wrong is a hard schema FAIL reported under the wrong class.**
The carry-over note (item 5) asserts "the Snowpark docs tell the translator to write columns in
contract order"; that is not true of the branch — `grep -i "column order|contract's order|same
order"` over `docs/reference/output-targets.md`, `translator.agent.md`, `reviewer.agent.md`,
`samples/wf_0006/README.md` and `translation_notes.md` returns nothing.
*Reproduced:* probe `A3_column_order_swapped` (identical columns, identical values, `RECOGNIZED` and
`DEFERRED` swapped in the `StructType`) → `verdict: FAIL`, `schema: FAIL`, every downstream check
`SKIPPED`, one diff cluster of class **`TYPE`** naming all four columns. For a SQL segment the
contract's column list is transcribed into the final `SELECT`, so this rarely bites; for Snowpark
the `StructType` is hand-built and ordering is an easy slip, and the diagnosis the fixer receives
points at types, not order.
*Suggested fix:* one sentence in `docs/reference/output-targets.md §3.2`, in `translator.agent.md`'s
Snowpark bullet list and in `reviewer.agent.md`'s Snowpark blocking checks: "the written table's
columns must be in `contract.outputs[].columns` order — `compare.py` treats a reordering as a schema
failure." Optionally make `compare.py` name column order explicitly when the two column *sets* match
but the orders differ; that is a `compare.py` change and can wait.

**I6 — two shipped source files point at a reviewer's scratchpad.**
`scripts/lib/snowpark_rules.py:4` — "see scratchpad/ot-rev3/probe_rules.py and probe_rules2.py" —
and `tests/test_snowpark_rules.py:70` — "(scratchpad/ot-rev3/)". Both are new on this branch
(`git grep scratchpad e3052fe -- scripts/ tests/` is empty). The path does not exist in the repo and
never will; the hand-off goal in spec §1 is "no machine paths".
*Failure scenario:* a future maintainer reading the rule set's rationale chases a dead pointer; more
importantly the branch's own hand-off bar is broken by its own source. The tests that enforce this
rule (`tests/test_agents_config.py:330`, `tests/test_committed_workflows.py:279`) cover only
`.github/**` and `workflows/**`, so nothing caught it.
*Suggested fix:* reword both to "the task-3 review's evasion probes (reproduced below / in
`tests/test_snowpark_rules.py`)". While there, extend one of the two existing machine-path tests to
cover `scripts/**`, `tests/**` and `orchestrator/**` for the literal `scratchpad` as well.

### Minor

**M1 — spec §3.3's interactive intake question has no home.** `output_target` is readable from
`sample.json` → manifest (`build_samples.py:374`) and from `mappings/global.yaml`, but no intake
touchpoint or prompt ever asks it (`grep output_target scripts/` shows only `build_samples.py` and
`target_check.py`). The plan never scheduled it either. It is unreachable in practice because the
committed `global.yaml` always carries a value, so this is a recorded-deferral problem, not a
behaviour problem. *Fix:* one line in `docs/reference/output-targets.md §3` ("a workflow-level
preference is set in `sample.json` or `mappings/global.yaml`; there is no interactive question for
it in phase 1") and a line in the ledger.

**M2 — three definitions of "node types that carry no data".** `scripts/lib/vocab.py:40`
`NON_DATA_TYPES = {container, comment, interface, action}`; `scripts/target_check.py:26` `DATA_LESS`
= that plus `browse`; `scripts/compile_check.py:68` `_NON_DATA_NODE_TYPES` = the same five, with a
comment explaining the divergence. `target_check.py` carries no such comment. *Failure scenario:* a
type added to `vocab.NON_DATA_TYPES` silently fails to reach the two copies, and a target proposal
starts counting a non-data node. *Fix:* `vocab.py` gains `DATA_LESS_TYPES = NON_DATA_TYPES |
{"browse"}`; both call sites import it.

**M3 — idempotency compares two *independent* runs, so an append-where-overwrite-was-meant is
idempotent by construction.** `scripts/validate_snowpark.py:334-351` creates a second fresh
`local_testing` session with `_prepare_run` and compares row multisets; the SQL twin
(`validate_segment.py:149-163,215`) does the same with a second `DuckDBBackend`. *Reproduced:* probe
`A6_append_mode` — `.write.mode("append")` in place of `"overwrite"` — PASSes all four sets with
`idempotent: true`, although a second real run against a persistent `MIG_WORK` would double the
table. Also, `_outputs_equal` compares rows only, never the two runs' schemas. This is faithful to
the pre-existing SQL semantics, so it is not a branch regression — but §5.2's "idempotency" now
covers a target whose write mode is chosen in Python rather than fixed by the procedure template.
*Fix (cheap):* say so in `docs/reference/output-targets.md §6`. *Fix (real, later):* run the second
iteration in the **same** session/backend, which is what "a second run must leave every output table
identical" means operationally.

**M4 — the two "same idea" dunder pre-checks disagree.** `snowpark_rules._DUNDER_RE` is
`^__.*__$` (`:53`); `alteryx_sim._check_python_tool_script` uses `attr.startswith("__")` (`:807`),
which also refuses `__private` and name-mangled attributes. The docstring at `snowpark_rules.py:22`
says it mirrors the simulator's check. Harmless (both err toward refusal) but the claim is inexact.

**M5 — `escalate`'s reason precedence can report a previous attempt's verify failure.**
`orchestrator/stages.ts:70` prefers `m.reasons[stage]`; `:366` clears `reasons.analyze` only inside
the verify callback, which runs only when the agent itself succeeded. If attempt 1 fails verify with
`target-mismatch: …` (a `missing-output`, hence retried once) and attempt 2 fails *before* verify —
`denied-tool`, `timeout`, `budget` — the workflow parks with the stale `target-mismatch` reason
instead of the real one. *Fix:* clear `m.reasons.analyze` once at the top of `stageAnalyze`, before
`runAgent`, rather than inside the callback.

**M6 — the accident-guard sentence names only `DataFrame.to_csv`.** The Task-1 re-review deferred
this: `PYTHON_TOOL_ALLOWED_MODULES` admits any submodule of an allowed root
(`alteryx_sim.py:785-790` checks `name.split(".")[0]`), so `import numpy.ctypeslib` and
`import pandas.io.common` are permitted and reach considerably further than writing a CSV. The
sentence is still true; it is just a weaker example than the branch's own allow-list supports.
*Fix:* "an allowed library can still reach the filesystem and load native code (for example
`DataFrame.to_csv`, or `numpy.ctypeslib`)" in the four places the sentence appears.

**M7 — `_guarded_import` binds the wrong object for `import pkg.sub`.**
`scripts/dev/alteryx_sim.py:785-791` returns `importlib.import_module(name)`, i.e. the *submodule*;
CPython's `__import__` contract is to return the top-level package for a dotted plain `import`. A
sample script doing `import pandas.io` would end up with `pandas` bound to `pandas.io`. No committed
script does this. *Fix:* `return importlib.import_module(name) if fromlist else
importlib.import_module(name.split(".")[0])`.

**M8 — README §6's "untouched" wording.** `README.md:426` — "`mappings/global.yaml` is untouched by
this whole sequence". Carry-over item 9 observed that running the sequence in a scratch root
re-serialises *that root's* copy (comments stripped; parsed value identical). The claim's substance
(no answer is promoted; the committed file still parses to program/session/tolerances only) is true
and the repo copy is genuinely unaffected. *Fix:* "no answer is promoted into `mappings/global.yaml`"
instead of "untouched".

**M9 — a machine-dependent silent skip.** `tests/test_committed_workflows.py:308` skips
`test_only_the_fixture_owner_name_appears_never_the_os_login_name` when the OS login name is "too
generic to probe for". It did not skip here (0 skipped), but on another machine this hand-off
invariant would quietly stop being checked. *Fix:* assert on a fixed deny-list of the names the
project actually uses in addition to the login probe.

**M10 — `translator.agent.md` now ends with two "Done when …" lines**, the Snowpark one inside the
new section and the original SQL one after it. Both are correct; a reader skimming to the end sees
the SQL one. *Fix:* fold the SQL "Done when" back above the Snowpark section, or prefix it "For a
`sql` segment: done when …".

---

## 4. Adversarial edits tried, and what happened

All run through `validate_snowpark(repo, "wf_0006", "seg_02", proc_path=<variant>)` against a
workflow staged with `tests/helpers.prepare_workflow` in a scratch root (four golden sets: `normal`,
`period_end`, `empty`, `edge`). `rules` is `snowpark_rules.check_proc_py` on the same variant.

| # | Edit to `samples/wf_0006/canned/segments/seg_02/proc.py` | Rules | Verdict | What caught it |
|---|---|---|---|---|
| BASELINE | unchanged | clean | **PASS** | — (control; `idempotent: true`, all four sets PASS) |
| A1 | `StructType` gains an `EXTRA` column, rows unchanged | clean | **FAIL** | `create_dataframe` rejected the row/field mismatch → domain FAIL with `error` |
| A2 | `DEFERRED` declared `StringType(254)` instead of `DoubleType` | clean | **FAIL** | `_read_back` → real type → `compare` schema check, `TYPE` cluster on `DEFERRED` |
| A3 | same four columns, `RECOGNIZED`/`DEFERRED` **order** swapped | clean | **FAIL** | schema check; `TYPE` cluster naming all four columns (see I5 — right answer, wrong label) |
| A4 | `+ 1e-9` on `RECOGNIZED` | clean | **PASS** | by design: `float_abs: 1e-6` |
| A6 | `.mode("append")` instead of `"overwrite"` | clean | **PASS** | **nothing** — see M3 |
| A7 | cancellation reset removed (the sample's own recorded LOGIC break, as a control) | clean | **FAIL** | `normal` set only; `LOGIC` cluster on `RECOGNIZED`, aggregate mismatch sum 490 vs 500 |
| B1 | genuine extra column **with values** (rows carry a 5th field) | clean | **FAIL** | schema check; `TYPE` cluster on `EXTRA` |
| B2 | `DEFERRED` column dropped from both schema and rows | clean | **FAIL** | schema check; `TYPE` cluster on `DEFERRED` |
| B4 | `+ 0.009` on `RECOGNIZED` (under `tolerances.rounding.abs`, and `ROUNDING` is an accepted class) | clean | **FAIL** | classified `ROUNDING` but **not** auto-accepted — an accepted *class* still needs an approval in `manifest.accepted_diffs`. Good. |
| B5 | one extra `save_as_table(f"{tgt_db}.{tgt_schema}.SHADOW_COPY")` appended | clean | **PASS** | **nothing** — see I4 |
| B6 | `RECOGNIZED` written as `None` on every row | clean | **FAIL** | nullability check + `NULL_SEMANTICS` clusters + aggregate mismatch |
| E1 | `from snowflake.snowpark import Session` → `Session.builder.getOrCreate()` → `.table(...)` and `.sql(...)` | **clean (evasion)** | n/a | rules blind (I2); locally the `.sql` call raises `NotImplementedError` → FAIL, so defence in depth held *here* |
| E2 | `.saveAsTable("ANALYTICS.CURATED.ANYTHING_AT_ALL")` (real snowpark 1.55 alias) | **clean (evasion)** | n/a | rules blind (I1); confirmed to actually write the table in a live local session |
| E3 | `sink = writer.save_as_table; sink("ANALYTICS.CURATED.ANYTHING_AT_ALL")` | **clean (evasion)** | n/a | rules blind (I1); confirmed to actually write the table |
| E4 | `session.table("MIG_WORK." + part)` (concatenated name) | REFUSED | n/a | `rule:table_names: needs a literal or a C4-parameter f-string` |
| MACRO | synthetic workflow: a macro whose `sub_dag` holds `python` + `run_command` + `unknown`, preference `dbt` | n/a | n/a | `target_check` returned `output_kind: "dbt"`, `dbt_blockers: []`, exit 0 — see I3 |

(Two further variants, `A5` and `B3`, were discarded: my edits produced syntax errors, which the
pipeline correctly reported as `rule:syntax` + a domain FAIL rather than anything interesting. Row
loss is already covered by A7's keyed value cluster and by the sample's own
`seg_03/01_summarize_drops_period.sql` variant.)

**Conclusion on the parity-gate guarantee.** A Snowpark procedure that gets the *declared* output
wrong — wrong type, wrong width family, wrong column set, wrong column order, wrong values beyond
tolerance, wrong NULLs, missing table, runtime exception, non-deterministic output — cannot reach
PASS. The three ways a wrong procedure can still PASS are: (a) an **undeclared side effect** (I4,
I1); (b) an **append-vs-overwrite** write mode (M3); (c) a value drift genuinely inside the
configured tolerances (A4 — by design, and identical for the SQL target). None of these is a
mis-comparison of the contract's own outputs; the gate itself is sound.

---

## 5. Carry-over items from `final-review-notes.md`

| # | Item | Verdict |
|---|---|---|
| 1 | `stages.ts` `env.log` prints raw script stderr unredacted while the fixer-prompt copy is redacted | **not load-bearing.** Pre-existing pattern, console only, and the audit trail (`hooks.ts`) is the record that leaves the machine. Worth a one-line note in `POLICY.md` some day; not now. |
| 2 | A dunder in a multi-line chain reports the chain's start line | **not load-bearing.** CPython `ast` semantics; message accuracy only, and the rule name and text are exact. |
| 3 | `snowpark_to_alteryx` reads the private `StringType._is_max_size` with a public fallback | **not load-bearing.** `types_map.py:144-147` — `getattr(…, False)` degrades to the `length is None or length >= 16777216` fallback, which the Task-4 re-review showed classifies both cases correctly. A Snowpark upgrade that drops the attribute changes nothing. |
| 4 | wf_0006's NULL-`CANCELLED` guard is reasoned, not exercised; real Alteryx's typing unverified | **not load-bearing.** Stated as unverified in five documents including the contract's own `parity_risks`; no golden set can carry the row because the simulator raises first. The guard errs toward a loud failure, which is the right side. |
| 5 | `compare.py` flags a column-ORDER difference as a schema failure; "the Snowpark docs tell the translator to write columns in contract order" | **LOAD-BEARING — the second half is false.** Reproduced the FAIL (A3); no doc, agent file or note says anything about column order. Raised as **I5**. |
| 6 | The dunder check refuses any `__x__` bare Name, not only attribute access | **not load-bearing.** Deliberately conservative; ruling 62 priced it ("a legitimate procedure that needs `__name__`-style introspection is refused, which no C4 procedure needs"). |
| 7 | `migrateSegment`'s `failedBeforeReview` is in-memory per call | **not load-bearing.** Same lifetime as the pre-existing `lastReason`; a crash between iterations costs one sentence of fixer context, not correctness. |
| 8 | The nine canned contracts and 17 `broken.json` rows gained `"target": "sql"`; `workflows/` refreshed for all six | **not load-bearing, and verified.** All six `segments/targets.json` present and consistent; all six manifests carry `output_kind`; `test_committed_workflows.py:144-201` pins both against `targets.json` rather than a constant. |
| 9 | Running README §6 in a scratch root re-serialises that root's `mappings/global.yaml` although §6 says "untouched" | **not load-bearing** → **M8** (wording). The repo copy is unaffected and `test_foundations.py` still pins it. |
| 10 | Reproducibility holds byte-for-byte except `updated_at` and `runtime_ms` | **not load-bearing.** Same two volatile fields Task 17 documented; the 6B re-review re-did the export and classified all 58 differing files. |
| 11 | The T3 workflow originally got no `manifest.output_kind` | **closed.** Fixed in 6B fix round 1; `workflows/wf_0005/manifest.json` carries `output_kind`, `targets.json` is present, and the pinned test covers all six `WORKFLOW_IDS`. |

---

## 6. Test counts (this review, on `204c98d`, main tree)

| Suite | Command | Result |
|---|---|---|
| pytest | `.venv/Scripts/python.exe -m pytest` | **1215 passed, 0 skipped, 0 failed** (80.0 s) |
| node | `fnm exec --using=22 npm.cmd test` | **177 pass / 0 fail / 0 skipped / 0 todo** (5.1 s) |
| tsc | `fnm exec --using=22 node.exe node_modules/typescript/bin/tsc --noEmit -p .` | **clean** (exit 0) |

Matches the ledger exactly. `requirements.txt`'s new pins are all satisfied by the venv:
`snowflake-snowpark-python 1.55.0` (≥1.55, with `pandas 2.3.3` for the `[pandas]` extra),
`dbt-core 1.12.5` (≥1.12), `dbt-duckdb 1.11.0` (≥1.11); the pre-existing `duckdb 1.5.5`,
`sqlglot 30.18.0`, `PyYAML 6.0.3`, `pytest 9.1.1` likewise. `docs/spec/**` is untouched. No
`<user>`, `C:\Users` or `/c/Users` occurrence is new on this branch (the pre-existing hits are
`<user>`-redacted build reports and two deliberate `pwd -W` / Git-Bash-path explanations); the only
new hand-off violations are the two `scratchpad/ot-rev3` references in **I6**.

---

## 7. Suggested fix wave (one pass, in this order)

1. **I6** — reword the two `scratchpad/` references; widen one machine-path test to `scripts/`,
   `tests/`, `orchestrator/`. (minutes)
2. **I1 + I4** — close the write surface in `snowpark_rules.py`: extra method names, a refusal of
   non-table sinks, a refusal of a referenced-but-not-called write method, and contract-derived
   logical names for `src_form`/`tgt_form`. Tests: one per new refusal, plus a positive control that
   the canned `wf_0006/seg_02/proc.py` stays clean.
3. **I2** — refuse `Session` and the session-method names regardless of the receiver's name.
4. **I3** — descend into `sub_dag` in `target_check.py` (or document the limitation explicitly).
5. **I5** — the column-order sentence in three files.
6. **M1, M2, M5, M8** — the four cheap ones. M3/M4/M6/M7/M9/M10 can ride along or be logged.

Every one of these is additive and local; none touches `compare.py`, the committed `workflows/`
trees, or the report shapes, so the offline run does not need regenerating. Expect the canned
`wf_0006/seg_02/proc.py` and its broken variant to stay rules-clean under the tightened rules
(checked: they use only `session.table`, `session.create_dataframe` and
`.write.mode(...).save_as_table(...)`).

---

Branch quality: needs one fix wave
