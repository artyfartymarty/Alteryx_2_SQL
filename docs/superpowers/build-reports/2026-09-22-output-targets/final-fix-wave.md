# Final fix wave — rulings on the whole-branch review of 204c98d

Read `final-review-report.md` first (sections 3, 4 and 7): every finding there is reproduced with
file:line, a scenario and a suggested fix. This file is the controller's ruling on each. Apply ALL
of them in ONE pass, RED-first where a test is named, keeping the committed `workflows/` trees
byte-identical unless F4 forces a refresh (see there). Do not touch `compare.py`, `docs/spec/**`,
or the report shapes.

## F1 (I6) — no scratchpad references in shipped files
Reword `scripts/lib/snowpark_rules.py:4` and `tests/test_snowpark_rules.py:70` (say "the review
probes" without a path). Widen the machine-path scan in `tests/test_committed_workflows.py` (or
`tests/test_agents_config.py`, whichever is the better home) to cover `scripts/**`, `tests/**`,
`orchestrator/**`, `docs/**`, `samples/**` for `<user>`, `C:\Users\<name>`-style paths (except the
documented `<user>` redaction and the two Git-Bash path explanations), and `scratchpad`.

## F2 (I1 + I4) — close the write surface (`scripts/lib/snowpark_rules.py`)
RULING: a C4 procedure has exactly ONE sink: `.write.mode(<literal>).save_as_table(<one positional
literal>)`.
- `rule:table_names` checks `save_as_table` AND `saveAsTable` (same rule as `.table`).
- Any other `DataFrameWriter` sink — `insert_into`, `insertInto`, `copy_into_location`,
  `copyIntoLocation`, `csv`, `json`, `parquet`, `orc`, `save`, `saveAsTable`'s siblings — is refused
  as `rule:no_io` wherever the name appears (Attribute.attr or Name), regardless of receiver.
- A write-method name (`save_as_table`, `saveAsTable`, `table`, `create_dataframe`) that appears as
  an Attribute which is NOT the `func` of a Call is refused (`rule:table_names: … must be called
  directly, not referenced`) — the same structural move as `session`.
- The `{src_db}.{src_schema}.NAME` / `{tgt_db}.{tgt_schema}.NAME` f-string forms require NAME to be a
  declared logical: for reads, an `inputs[].logical` of the contract; for writes, an
  `outputs[].logical`. No fallback: a write to an undeclared logical is exactly what must be refused.
  Confirm first what the canned contracts (`samples/wf_000*/canned/segments/*/contract.json`) actually
  carry for logical names (grep `logical`) and use that field; if the field is named differently,
  use the real one and say so in the report.
Tests: `saveAsTable` refused-or-checked, `insert_into` refused, `sink = w.save_as_table; sink(...)`
refused, `f"{tgt_db}.{tgt_schema}.SHADOW_COPY"` refused for wf_0006 seg_02's contract while the
contract's own logical passes; the canned `proc.py` and both broken `.py` variants stay clean
(`tests/test_canned_artifacts.py` must stay green).

## F3 (I2) — the session rule must not depend on the receiver's name
RULING: the name `Session` is refused outright (Name, Attribute.attr, from-import alias) under
`rule:session_scope` — a C4 procedure is HANDED a session. Additionally the attribute names `sql`,
`call`, `add_packages`, `add_import`, `add_requirements`, `udf`, `sproc`, `udtf`, `udaf`, `file`,
`query_history`, `use_database`, `use_schema`, `use_role`, `use_warehouse`, `close` are refused
wherever they appear as an Attribute.attr (`rule:no_session_sql` for `sql`/`call`, `rule:no_io` for
the rest), regardless of receiver. Tests: `from snowflake.snowpark import Session` →
`Session.builder.getOrCreate()` refused; `x.sql("...")` refused for any `x`; positive control clean.

## F4 (I3) — `target_check.py` descends into macro sub-DAGs
RULING: classification walks each node AND its `sub_dag["nodes"]` recursively; sub-DAG nodes appear
in `targets.json.nodes` under the id `"<macro_tool_id>/<sub_tool_id>"` (nested further as needed); a
segment's class is the max over its nodes including sub-DAG nodes; blockers name the nested id; a
macro whose `sub_dag` is null/absent (unresolved) classifies as `unknown` (exit 1, blocker
`unknown_node`). `plugin_map.node_class` keeps `macro → sql` for a resolved macro's OWN node; the
sub-DAG decides. Tests: the reviewer's synthetic macro (python + run_command + unknown inside) →
snowpark segment, `manual_node` and `unknown_node` blockers, exit 1; an unresolved macro → unknown.
THEN: run `scripts/target_check.py` for each of the six committed workflows in a scratch copy of
`workflows/` and diff every `segments/targets.json` against the committed one. If ANY differs
(wf_0004 has a resolved macro), re-run the whole README §6 sequence in a scratch root exactly as
Task 6B did (see `task-6b-report.md`), refresh ALL six committed trees, classify the old-vs-new diff
(must be: `targets.json` contents, `runtime_ms`, `updated_at` — anything else is a regression to
report, not commit), and re-do the independent reproducibility export. If none differs, say so with
the diff command and leave `workflows/` untouched. Document the nested-id form in
`docs/reference/output-targets.md` §3.

## F5 (I5) — column order
One sentence in `docs/reference/output-targets.md` §3.2, the translator agent's Snowpark bullets, and
the reviewer agent's Snowpark blocking checks: the `StructType` must list the output columns in the
contract's declared order; a different order is a schema FAIL. Add the same sentence to
`samples/wf_0006/canned/segments/seg_02/translation_notes.md`.

## F6 (Minor, all in)
- M1: `docs/reference/output-targets.md` §3 records that spec §3.3's interactive intake question for
  `output_target` is deferred (the committed `global.yaml` always carries a value; the seed carries
  `sample.json`'s); no code.
- M2: `scripts/lib/vocab.py` gains `DATA_LESS_TYPES = NON_DATA_TYPES | {"browse"}`, imported by
  `target_check.py` and `compile_check.py` (delete their local copies); one test.
- M3: `docs/reference/output-targets.md` §6 states that idempotency compares two independent runs,
  so an `append` in place of `overwrite` is not detected by the local double (same as the SQL twin).
- M4: `snowpark_rules._DUNDER_RE` becomes the simulator's rule (`startswith("__")`) so the
  "mirrors" claim at line 22 is exact; adjust tests if any pinned the regex.
- M5: `stageAnalyze` clears `m.reasons.analyze` at its top so a later attempt cannot park with a
  stale `target-*` reason; one node test.
- M6: the accident-guard sentence gains a second example (`numpy.ctypeslib`, `pandas.io.common`) in
  spec §7.1 is OFF LIMITS? — NO: the spec is ours (`docs/superpowers/specs/...`), only `docs/spec/**`
  is frozen; add the example in spec §7.1, `simulator-semantics.md`, `dag-contract.md`,
  `alteryx_sim.py`'s docstring.
- M7: `_guarded_import` returns the top-level package for a dotted plain `import a.b` (standard
  semantics); one test.
- M8: README wording "untouched" → "no answer is promoted into" for `mappings/global.yaml`.
- M9: the login-name check keeps a fixed deny-list (`<user>`) beside the probe so it never skips
  silently.
- M10: `translator.agent.md` keeps one "Done when" line (merge the two).

## Suites and commit
pytest (0 skipped; expect ≥ 1215 + new), node (≥ 177 + new), tsc clean; `find samples workflows
-name __pycache__` empty. Commit as `fix: final review wave — write surface, Session, macro sub-DAGs,
column order, hand-off hygiene` (plus `wip:` commits as you go; if F4 refreshes `workflows/`, that is
its own commit). Report to `final-fix-report.md` beside this file: per finding what changed, the
targets.json diff result, counts, concerns. No subagents; never git stash / checkout -- / reset --hard.
