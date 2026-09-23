# Final fix wave — implementer report

Worktree `.worktrees/ot-final-fix`, branch `wt/ot-final-fix`, base `204c98d`.
All sixteen rulings in `final-fix-wave.md` (F1–F6) applied in one pass, RED-first for every named
test. Two commits:

| commit | subject |
|---|---|
| **`f2b4b7d`** | `fix: final review wave — write surface, Session, macro sub-DAGs, column order, hand-off hygiene` |
| **`9d5255b`** | `fix: refresh the committed offline run for F5's canned translation note` |

(Five `wip:` commits were made as the work went — F1, F2+F3, F5, F6, F4 — and squashed into
`f2b4b7d` at the end. The squash was verified tree-hash identical: `e7800632…` before and after.)

Suites at `9d5255b`: **pytest 1277 passed, 0 skipped** (79.3 s); **node 178 pass / 0 fail / 0
skipped / 0 todo**; **tsc clean** (exit 0); `find samples workflows -name __pycache__` empty;
`git status` clean.

---

## F1 (I6) — no scratchpad or machine-path references in shipped files

**Changed**

- `scripts/lib/snowpark_rules.py:4` — the `see scratchpad/ot-rev3/probe_rules.py and probe_rules2.py`
  pointer now reads "…which the task-3 review's evasion probes demonstrated; every one of those
  probes is reproduced as a test in `tests/test_snowpark_rules.py`".
- `tests/test_snowpark_rules.py:70` — "Each reproduces probe_rules.py / probe_rules2.py exactly
  (scratchpad/ot-rev3/)" → "Each reproduces one of the task-3 review's probes exactly".
- `tests/test_committed_workflows.py:334-411` — the hand-off scan widened past `workflows/` to
  `scripts/`, `tests/`, `orchestrator/`, `docs/`, `samples/` (`HAND_OFF_TREES`), as three tests:
  - `test_no_shipped_file_carries_a_real_machine_path` — `_USER_PATH_RE` captures the *user
    segment* of any `C:\Users\…` / `C:/Users/…` / `/c/Users/…` path (doubled separators included,
    for a path quoted inside JSON in a build report) and fails unless it is a documented
    placeholder: `<user>`, `<you>`, `<name>`, `…`, `...`, `[a-za-z]` (a grep recipe's character
    class), or the plainly fictional `nobody` / `someone` that two orchestrator unit tests invent.
    That is exactly the review's "except the documented `<user>` redaction and the two Git-Bash
    path explanations", expressed as a rule rather than as a file list.
  - `test_no_shipped_file_carries_a_build_machine_login_name` — scans `BUILD_MACHINE_LOGIN_NAMES`.
  - `test_no_shipped_file_points_at_a_scratchpad` — scans for the literal `scratchpad`.
  - Two documented exemptions: `_SCRATCHPAD_EXEMPT = ("docs/superpowers/build-reports/",
    "docs/live-smoke-test.md")` — those files' *subject* is how and where the work ran, and all of
    them are `<user>`-redacted (the machine-path scan still holds them) — and `_CHECKS_OWN_FILE`,
    this test module, which must spell out the names and the word it bans everywhere else.
- `tests/test_committed_workflows.py:298-324` — **M9** folded in here: the login probe no longer
  skips. `BUILD_MACHINE_LOGIN_NAMES = ("<user>",)` is scanned unconditionally on every machine,
  and `getpass.getuser()` is *added* to it when it is specific enough to mean anything.
- `docs/superpowers/plans/2026-09-22-session-telemetry.md:1380` — one real leak found by the new
  scan: the plan's check line spelled the build machine's login name inside a `git grep` recipe. It
  now points at the three tests instead.

**RED** (`pytest tests/test_committed_workflows.py -p no:randomly`, before the rewordings):

```
E   AssertionError: a build machine's login name is committed:
E     docs/superpowers/plans/2026-09-22-session-telemetry.md: <user>
E   AssertionError: these files point at a scratchpad directory that exists on no other machine:
E     scripts/lib/snowpark_rules.py
E     tests/test_snowpark_rules.py
3 failed, 41 passed in 0.60s
```

**GREEN**: `44 passed` for that file; `104 passed` for it plus `test_snowpark_rules.py` and
`test_agents_config.py`.

**Final grep of the whole tracked tree** (889 files):

- `git grep -i <user>` → one hit, `tests/test_committed_workflows.py:304`
  (`BUILD_MACHINE_LOGIN_NAMES`), the deny-list's own home.
- `git grep -i scratchpad` → the test module itself (6 lines, the checks and their comments) plus
  the five documented build-report / smoke-test files, all `<user>`-redacted.
- Machine paths, scanned with the same regex over all 889 tracked files: **one** non-placeholder
  hit, `README.md` `C:/Users/….` — the capture swallowed the sentence's full stop after the
  ellipsis. It is the documented Git-Bash path explanation (`README.md:395-396`), not a real path.

---

## F2 (I1 + I4) — the write surface is closed

All in `scripts/lib/snowpark_rules.py` (constants at `:72-94`, walk at `:240-296`, table-name
helper at `:216-232`).

- `TABLE_NAME_CALLS = {"table", "save_as_table", "saveAsTable"}` — `saveAsTable` is a real
  `DataFrameWriter` alias, so it is **checked** like `save_as_table`, not banned.
- `FORBIDDEN_SINKS = {"insert_into", "insertInto", "copy_into_location", "copyIntoLocation",
  "csv", "json", "parquet", "orc", "save"}` — refused as `rule:no_io` wherever the name appears, as
  an `ast.Attribute.attr` or as a bare `ast.Name`, regardless of receiver.
- `CALL_ONLY_ATTRS = {"table", "save_as_table", "saveAsTable", "create_dataframe"}` — an Attribute
  with one of these names that is **not** the `func` of a `Call` is refused
  (`rule:table_names: \`X\` must be called directly, not referenced (line N)`). The set of
  call-func Attribute ids is precomputed by `_called_attribute_ids` (`:189-194`).
- **Contract-derived logical names.** `check_proc_py` now builds `src_logicals` from
  `inputs[].logical` and `tgt_logicals` from `outputs[].logical`, and the form regexes capture the
  identifier. `table_name_error()` accepts a literal in `allowed_tables`, a `{src_db}.{src_schema}.X`
  whose `X` is a declared input logical, or a `{tgt_db}.{tgt_schema}.X` whose `X` is a declared
  output logical — **no fallback**. Messages: `` `…` names no `inputs[].logical` of this segment's
  contract`` / `` …`outputs[].logical`… ``.

  *Field name confirmed as the ruling asked:* `grep logical samples/wf_000*/canned/segments/*/contract.json`
  — the field is literally `logical`, on both `inputs[]` and `outputs[]`. wf_0006/seg_02 declares
  `outputs[0].logical: null` and its input carries no `logical` key at all (it comes `from: seg_01`
  with a `table`), so **every** `{tgt_db}` form is refused for that contract, which is exactly
  probe B5's `SHADOW_COPY` case. A contract that declares a target logical (the wf_0006/seg_03
  shape) accepts its own name and refuses everything else — both halves are tested.

**Tests** (`tests/test_snowpark_rules.py`, all new): `CONTRACT_MAPPED` fixture, `_errors()` helper,
`test_the_good_procedure_also_passes_against_a_contract_that_declares_logicals`,
`test_save_as_table_camel_case_alias_is_checked_like_save_as_table`,
`test_save_as_table_camel_case_alias_is_accepted_when_it_names_the_right_table`,
`test_every_other_dataframewriter_sink_is_refused` ×9,
`test_a_forbidden_sink_is_refused_as_a_bare_name_too` ×3,
`test_a_write_method_that_is_referenced_instead_of_called_is_refused` ×4,
`test_a_write_to_an_undeclared_logical_is_refused`,
`test_a_write_to_the_contracts_own_target_logical_passes`,
`test_a_read_of_an_undeclared_logical_is_refused`,
`test_a_read_of_the_contracts_own_source_logical_passes`,
`test_the_positive_controls_survive_every_new_refusal`.

---

## F3 (I2) — the session rules ignore the receiver's name

Same file. `FORBIDDEN_SESSION_NAMES = {"Session"}` refused as an `ast.Name`, as an
`ast.Attribute.attr` and as a `from … import` alias, under `rule:session_scope`
("a C4 procedure is handed its session, it never builds one"). `NO_SESSION_SQL_ATTRS =
{"sql", "call"}` → `rule:no_session_sql` (the rule name the plan's own rule-name list already
carried); `FORBIDDEN_SESSION_ATTRS = {add_packages, add_import, add_requirements, udf, sproc, udtf,
udaf, file, query_history, use_database, use_schema, use_role, use_warehouse, close}` →
`rule:no_io`. All as an attribute of **any** receiver.

**Tests**: `test_the_name_session_class_is_refused_outright` (probe E1 reproduced verbatim),
`test_the_session_class_is_refused_at_the_import_even_if_never_used`,
`test_raw_sql_session_methods_are_refused_on_any_receiver` ×2,
`test_every_other_session_method_is_refused_on_any_receiver` ×14.

**RED for F2 + F3 together** — `pytest tests/test_snowpark_rules.py -p no:randomly` with the tests
in place and the rules unchanged:

```
39 failed, 27 passed in 0.20s
FAILED …::test_save_as_table_camel_case_alias_is_checked_like_save_as_table
FAILED …::test_every_other_dataframewriter_sink_is_refused[insert_into|insertInto|copy_into_location|
        copyIntoLocation|csv|json|parquet|orc|save]                                              (9)
FAILED …::test_a_forbidden_sink_is_refused_as_a_bare_name_too[insert_into|csv|save]              (3)
FAILED …::test_a_write_method_that_is_referenced_instead_of_called_is_refused[save_as_table|
        saveAsTable|table|create_dataframe]                                                      (4)
FAILED …::test_a_write_to_an_undeclared_logical_is_refused
FAILED …::test_a_write_to_the_contracts_own_target_logical_passes
FAILED …::test_a_read_of_an_undeclared_logical_is_refused
FAILED …::test_a_read_of_the_contracts_own_source_logical_passes
FAILED …::test_the_name_session_class_is_refused_outright
FAILED …::test_the_session_class_is_refused_at_the_import_even_if_never_used
FAILED …::test_raw_sql_session_methods_are_refused_on_any_receiver[sql|call]                     (2)
FAILED …::test_every_other_session_method_is_refused_on_any_receiver[add_packages|add_import|
        add_requirements|udf|sproc|udtf|udaf|file|query_history|use_database|use_schema|use_role|
        use_warehouse|close]                                                                    (14)
```

**GREEN**: `tests/test_snowpark_rules.py` → `66 passed` (72 after M4's six).

**The canned artefacts stay rules-clean**, as the ruling required:
`pytest tests/test_canned_artifacts.py tests/test_compile_check_snowpark.py
tests/test_validate_snowpark.py tests/test_committed_workflows.py` → **157 passed**. That covers
`samples/wf_0006/canned/segments/seg_02/proc.py`, the committed
`workflows/wf_0006/segments/seg_02/proc.py`, and the broken variant
`samples/wf_0006/broken_sql/seg_02/01_cancellation_reset_ignored.py`. (There is exactly **one**
broken `.py` variant in the tree, not two — `find . -name '*.py' -path '*broken*'` returns only
that file; the ruling's "both broken `.py` variants" appears to count the canned `proc.py` as one
of the pair.)

---

## F4 (I3) — `target_check.py` descends into macro sub-DAGs

**Changed** — `scripts/target_check.py`:

- `expand(nodes, prefix="")` (`:41-60`): every data-carrying node paired with the id it is known
  by, then recursively the nodes of a resolved macro's `sub_dag` under `"<macro_id>/<sub_id>"`,
  nested again for a macro inside a macro. `DATA_LESS_TYPES` filtering happens inside the walk, so
  it applies at every depth.
- `classify(node)` (`:63-70`): `plugin_map.node_class`, except that a `macro` with no `sub_dag`
  (unresolved) is `unknown`.
- `dbt_blockers` (`:73-…`) now takes `expand()`'s `(id, node)` pairs and puts the **nested id** in
  each blocker's `tool_id`. The `outputs`↔segment match keeps using the id as written, because
  `intake/mappings.yaml` only ever names top-level tool ids (`intake_touchpoints.py` deliberately
  does not look inside a macro) — commented in place.
- `target_check` (`:120-124`): `seg_nodes` and `nodes` both built through `expand`; the segment is
  `snowpark` if any of its nodes, sub-DAG nodes included, is.
- `plugin_map.node_class` **unchanged** — `macro → sql` for the macro's own node.
- Module docstring records the nested-id form; `docs/reference/output-targets.md §3` documents it
  with a worked `targets.json` fragment, the "highest of all its nodes" rule and the unresolved-macro
  case.

**RED** (`pytest tests/test_target_check.py -p no:randomly`, before the implementation):

```
5 failed, 16 passed in 0.36s
FAILED …::test_a_macro_hiding_a_python_tool_makes_its_segment_snowpark
        assert {'seg_02': 'sql'} != {'seg_02': 'snowpark'}
FAILED …::test_a_macro_hiding_an_unknown_tool_exits_1            assert 0 == 1
FAILED …::test_an_unresolved_macro_is_unknown_not_sql            assert 0 == 1
FAILED …::test_a_macro_inside_a_macro_is_reached_under_its_full_path
        assert {} == {'9/8/3': 'snowpark'}
FAILED …::test_an_output_tool_inside_a_macro_is_still_held_to_the_plain_sql_rule
```

**GREEN**: `21 passed`. Seven new tests: the reviewer's synthetic macro (python + run_command +
unknown inside, preference `dbt`) → `segments {seg_02: snowpark}`, `nodes {"9/3": snowpark,
"9/4": manual, "9/6": unknown}`, blockers `snowpark_segment`, `manual_node 9/4`, `unknown_node 9/6`,
exit 1; an unresolved macro → `nodes {"9": "unknown"}` + `unknown_node` blocker + exit 1; a macro
inside a macro → `"9/8/3"`; a macro of ordinary tools → unchanged (`nodes {}`, no blockers,
`output_kind dbt`); an Output tool inside a macro still held to the plain-SQL pre/post rule; and
`plugin_map.node_class("macro") == "sql"` re-pinned.

### The `targets.json` diff — **no committed proposal changes**

```bash
SCR=<scratch>/targets-probe
cp -r workflows mappings "$SCR/"
for wf in wf_0001 … wf_0006; do .venv/Scripts/python.exe scripts/target_check.py "$wf" --root "$SCR"; done
for wf in wf_0001 … wf_0006; do diff "workflows/$wf/segments/targets.json" "$SCR/workflows/$wf/segments/targets.json"; done
diff -rq workflows "$SCR/workflows"
```

All six `targets.json` **identical**, and the whole-tree `diff -rq` printed nothing at all. Exit
codes reproduced unchanged (0 for wf_0001–4 and wf_0006, 1 for wf_0005). wf_0004 is the workflow
with a resolved macro; its `sub_dag` holds `macro_input`, `regex`, `formula`, `filter`,
`macro_output` and one `interface` node, so every new nested id classifies `sql` (or is filtered as
data-less) and the `cls != "sql"` filter leaves the `nodes` map empty exactly as before.

### …but `workflows/` was refreshed anyway, for F5

F5's sentence had to be added to `samples/wf_0006/canned/segments/seg_02/translation_notes.md`,
and `MockRunner` copies that file verbatim into `workflows/wf_0006/segments/seg_02/`
(`orchestrator/runner.ts:202-203`), so the committed copy had to be **regenerated**, not edited.
The whole README §6 sequence was re-run in a scratch root exactly as Task 6B did it —
`scripts mappings catalog` copied from this worktree, `samplesDir` = this worktree's `samples/`,
`python` = the main checkout's venv — seed, pass 1, `answer_samples.py`, pass 2, pass 3.

- Terminal states: wf_0001–wf_0004 and wf_0006 `VALIDATED`, wf_0005 `MANUAL` — unchanged.
- Pass 3 was a no-op: six one-line `updated_at` diffs, nothing else in the tree.
- Hygiene before copying: `scanned=404 binary_skipped=5 hits=0` for `<user>`, `C:\Users`,
  `C:/Users`, `/c/Users`, `scratchpad`, `temp\claude`, `temp/claude` and `[a-zA-Z]:[\/]Users[\/]`;
  no `*.duckdb`, `audit.jsonl` or `__pycache__` anywhere; `confirmed_by: automation` 15/15.
- `git status` after the copy: 63 modified, **0 deleted, 0 untracked**; 409 files, as before.

**Old (`f2b4b7d`'s tree) vs new, classified key by key over all 409 files:**

```
--- text differs: wf_0006/segments/seg_02/translation_notes.md   (1 file)
--- value changed: runtime_ms                                    (56 files)
--- value changed: updated_at                                    (6 files)
```

Nothing else: no file added or removed, no other key added, removed or changed, **no
`targets.json` difference**. The one text difference is the F5 sentence and is the reason the
refresh exists; `runtime_ms` and `updated_at` are the two volatile fields `task-17-report.md` and
`task-6b-report.md` already name. This is the ruling's expected set with `targets.json` swapped for
the canned-note file, which I am flagging rather than passing over.

### Reproducibility, re-done independently

`git archive HEAD | tar -x` into a third scratch root, its own `workflows/` deleted first, its own
`samples/`/`scripts/`/`mappings/`/`catalog/`, and `scripts/parsers/ext/` containing only
`README.md` and `__init__.py` — so wf_0005's parser-recovery path really ran
(`parse.attempts: 2` reproduced on both sides). Same terminal states, then:

```
$ diff -rq -x audit.jsonl -x '*.duckdb' -x '*.duckdb.wal' workflows <repro>/workflows
59 lines, 0 of them "Only in …"
$ classify.py workflows <repro>/workflows
files compared: 409
--- value changed: runtime_ms  (53 files)
--- value changed: updated_at  (6 files)
$ diff -rq <norm>/a/workflows <norm>/b/workflows     # both volatile fields normalised
DIFF_EXIT=0   (no output)
```

All 409 committed files reproduce byte-for-byte from a clean export except
`manifest.json["updated_at"]` and `validation*.json["runtime_ms"]`.

---

## F5 (I5) — column order

One sentence, pinned in all four homes by
`tests/test_agents_config.py::test_the_column_order_rule_is_stated_wherever_a_snowpark_translation_is_described`
(`COLUMN_ORDER_SENTENCE` / `COLUMN_ORDER_HOMES`, compared with whitespace squeezed so each file
may wrap it its own way):

> …`StructType` must list the columns in the contract's declared `outputs[].columns` order: a
> different order is a schema FAIL, reported as a `TYPE` difference rather than as an ordering one.

- `docs/reference/output-targets.md §3.2` — a new bullet, "**Column order is part of the schema**",
  which also says why it rarely bites a SQL segment and why the fixer's diagnosis points at types.
- `.github/agents/translator.agent.md` — a new Snowpark bullet (the verbatim-from-the-spec
  `- Rules (…)` bullet is deliberately left untouched, so
  `test_translator_snowpark_rules_bullet_is_verbatim_from_the_design` still holds).
- `.github/agents/reviewer.agent.md` — a new Snowpark blocking check.
- `samples/wf_0006/canned/segments/seg_02/translation_notes.md` — the existing
  "written in the contract's own order" clause extended with the sentence. (For the record: that
  file *did* already carry the substance — "the columns are written in the contract's own order,
  because a column-order difference is a schema failure…" — which the review's grep for
  `contract's order` missed because it says "the contract's **own** order". The rule still had no
  home in the reference page or either agent file, so I5 stands.)

**RED**: `pytest tests/test_agents_config.py -k column_order` →
`AssertionError: docs/reference/output-targets.md does not state the column-order rule`,
`1 failed, 36 deselected`. **GREEN**: `tests/test_agents_config.py` + `tests/test_canned_artifacts.py`
→ `115 passed`.

---

## F6 — the ten minors

| # | What changed | Where | Test |
|---|---|---|---|
| M1 | The §3.3 interactive intake question for `output_target` is recorded as **deferred**, with why it is unreachable (the committed `global.yaml` always carries a value; the seed carries `sample.json`'s) and what an intake touchpoint would have to write. No code. | `docs/reference/output-targets.md §3` | — (doc only, as ruled) |
| M2 | `DATA_LESS_TYPES = NON_DATA_TYPES \| {"browse"}`, **derived** not retyped; `target_check.py` and `compile_check.py` import it and their local copies are deleted. | `scripts/lib/vocab.py:43-48`, `scripts/target_check.py:22`, `scripts/compile_check.py:55` | `test_foundations.py::test_vocab_owns_the_data_less_node_types_that_target_check_and_compile_check_share` (RED: `AttributeError: module 'lib.vocab' has no attribute 'DATA_LESS_TYPES'`) |
| M3 | "Idempotent" compares two **independent** runs, so `append`-where-`overwrite`-was-meant is idempotent by construction — same as the SQL twin, and now worth saying because the write mode is chosen in Python. | `docs/reference/output-targets.md §6` | — (doc only, as ruled) |
| M4 | `_DUNDER_RE` (`^__.*__$`) replaced by `_is_dunder(name) -> name.startswith("__")`, the simulator's own test, so the module docstring's "mirrors it" claim is exact. | `scripts/lib/snowpark_rules.py:99-105` | `test_any_name_mangled_or_dunder_attribute_is_refused` ×4, `…_bare_name_is_refused` ×2 (RED on `__private`, `__mangled`) |
| M5 | `stageAnalyze` clears `reasons.analyze` through `clearAnalyzeReason(m)`. **Note:** the review suggested "once at the top"; that does not work, because `reloadManifest` `Object.assign`s the whole on-disk manifest over `m` two lines later and brings the stale reason back. It is cleared immediately **after** that reload (before `target_check.py` and `runAgent`) and again after the post-`runAgent` reload, so a `DONE` analyze also carries no reason. The clear inside the verify callback is gone. | `orchestrator/stages.ts:316-330, 386-388` | `stages.test.ts` "an analyze attempt that fails before verify reports its own reason, not the last attempt's" (RED: got `target-mismatch: seg_02 raised snowpark to sql`, wanted `denied`) |
| M6 | The accident-guard sentence now reads "…can still reach the filesystem **and load native code** (for example `DataFrame.to_csv`, **or a submodule the allow-list admits by its top-level package alone, such as `numpy.ctypeslib` or `pandas.io.common`**)…" in all five places it appears. The `PYTHON_TOOL_ALLOWED_MODULES` bullet also had a factual error — "only as the exact top-level module" — corrected to say the test is `name.split(".")[0]`. | spec §7.1; `docs/reference/simulator-semantics.md` ×3 + the allow-list bullet; `docs/reference/dag-contract.md`; `scripts/dev/alteryx_sim.py` `run_python_tool` docstring | no test pins the sentence verbatim (checked); the doc correction is covered by M7's tests |
| M7 | `_guarded_import` returns `importlib.import_module(root)` when `fromlist` is empty — CPython's contract for a plain dotted `import a.b`. | `scripts/dev/alteryx_sim.py:785-797` | `test_a_dotted_plain_import_binds_the_top_level_package`, `test_a_dotted_import_of_a_module_outside_the_allow_list_is_still_refused` (RED: `pandas.io is pandas` failed) |
| M8 | "`mappings/global.yaml` is untouched by this whole sequence" → "no answer is promoted into `mappings/global.yaml` by this whole sequence", with a parenthesis stating that a scratch root's own copy *is* re-serialised (comments stripped, values identical). | `README.md:425-429` | existing `test_foundations.py` pin unchanged |
| M9 | Committed with F1 (above). | `tests/test_committed_workflows.py:298-324` | the test no longer skips on any machine |
| M10 | The two "Done when" lines merged into one that branches on the segment's target. | `.github/agents/translator.agent.md` (tail) | `grep -c '^Done when\|^Done for'` → 1; `test_agents_config.py` green |

---

## Counts

| suite | command | base `204c98d` | now `9d5255b` |
|---|---|---|---|
| pytest | `.venv/Scripts/python.exe -m pytest` | 1215 passed, 0 skipped | **1277 passed, 0 skipped** (79.3 s) |
| node | `fnm exec --using=22 npm.cmd test` | 177 pass | **178 pass**, 0 fail, 0 skipped, 0 todo |
| tsc | `fnm exec --using=22 node.exe …/tsc --noEmit -p .` | clean | **clean** (exit 0) |

Per-file python deltas (`pytest <file> --collect-only`, base vs now): `test_snowpark_rules.py`
24 → 72 (+48), `test_target_check.py` 14 → 21 (+7), `test_committed_workflows.py` 41 → 44 (+3),
`test_agents_config.py` 36 → 37 (+1), `test_foundations.py` 82 → 83 (+1),
`test_alteryx_sim_python.py` 21 → 23 (+2). 24 + … = **+62**, and 1215 + 62 = 1277: no other file's
count moved.

Hygiene: `find samples workflows -name __pycache__` → empty. `git status` → clean.

---

## Concerns

1. **The hand-off scan needs exemptions, and exemptions are holes.** `docs/superpowers/build-reports/**`
   and `docs/live-smoke-test.md` are exempt from the `scratchpad` scan because their subject is how
   and where the work ran; `tests/test_committed_workflows.py` is exempt from its own two name
   scans because it holds the deny-list. All three are named constants with a written reason, so a
   reviewer can disagree with each one individually — but a future `scratchpad/…` pointer added to
   a build report or to that test file will not be caught.
2. **I reworded a line in an unrelated plan.** `docs/superpowers/plans/2026-09-22-session-telemetry.md`
   (a different feature's plan) spelled the build machine's login name inside a `git grep` recipe.
   Leaving it would have meant exempting it, which defeats F1; I rewrote the check to point at the
   three new tests instead. It is one line, outside this wave's nominal scope, and flagged here.
3. **`workflows/` was refreshed for F5, not for F4.** F4 changes no committed `targets.json` — I
   verified that first, exactly as the ruling asked, and the whole-tree diff of the probe copy was
   empty. The refresh exists because F5 edits a canned artefact that `MockRunner` replays verbatim.
   The old-vs-new diff is therefore `translation_notes.md` + `runtime_ms` + `updated_at` rather than
   the `targets.json` + `runtime_ms` + `updated_at` the ruling anticipated. Nothing else moved.
4. **M5's placement differs from the review's literal suggestion**, for the reason in the table
   above (the reload). The test proves the behaviour the ruling wanted; the *line* is two
   statements lower than "the top" of the function.
5. **`FORBIDDEN_SINKS` includes `json`, `csv` and `save` as bare names.** That is what the ruling
   said ("wherever the name appears … or Name"), and it is conservative in the right direction, but
   it means a C4 procedure may not use a local variable called `save` or `json`. No canned or
   committed procedure does, and `json`/`csv` as modules were already off the import allow-list.
6. **Standing honesty note, unchanged:** nothing in this repository has run against a real
   Snowflake account or real Alteryx. Every verdict above is from DuckDB, the Snowpark Local
   Testing Framework, `MockRunner` and `scripts/dev/alteryx_sim.py`. In particular, F2's claim that
   `saveAsTable` / `insert_into` / a rebound writer method really write a table is the *review's*
   measurement against `snowflake-snowpark-python 1.55.0`'s local session, which I did not re-run —
   I implemented the refusals it asked for.

---

# Round 2

The scoped re-review of `9d5255b` confirmed F1–F5 and nine of the ten minors and left four
residuals, each with a controller ruling. All four applied, RED-first, in one commit:

| commit | subject |
|---|---|
| **`ccd7907`** | `fix: final wave round 2 — view/session/pandas write routes refused, last-attempt park reason, whole-tree hand-off scan, spec 4.2 re-synced` |

Suites at `ccd7907`: **pytest 1306 passed, 0 skipped** (81.5 s, was 1277); **node 179 pass / 0 fail
/ 0 skipped / 0 todo** (was 178); **tsc clean**; `find samples workflows -name __pycache__` empty;
`git status` clean. No committed `workflows/` artefact changed, so no refresh was needed this round.

---

## R1 — three more write routes, none of them a dunder

**Changed** — `scripts/lib/snowpark_rules.py`:

- `FORBIDDEN_SINKS` (`:76-88`) gains `create_or_replace_view`, `create_or_replace_temp_view`,
  `create_or_replace_dynamic_table`, `write_pandas`, `copy_into_table`, `cache_result`.
- `PANDAS_WRITERS` (`:89-95`), new: `to_csv`, `to_parquet`, `to_json`, `to_excel`, `to_pickle`,
  `to_sql`, `to_feather`, `to_hdf`, `to_clipboard`, `to_html`, `to_latex`, `to_markdown`, `to_xml`,
  `to_stata`, `to_gbq` — the 15 the ruling named. `to_string` is deliberately **not** on it (it
  writes nothing), and neither is `to_pandas`, which is how row-sequential logic is expressed at
  all (spec §4.2).
- Both refused as `rule:no_io` wherever the name appears, as an `ast.Attribute.attr` or as a bare
  `ast.Name`, whatever the receiver is called.
- The attribute `session` is refused on **any** receiver as `rule:session_scope`
  ("`session` may not be read off another object (line N); it is only ever the receiver of
  `session.table(...)` or `session.create_dataframe(...)` in `run`"). The bare-Name rule is
  untouched, so `session.table(...)` in `run` is still the one licensed shape — a test pins exactly
  that, because the new rule must fire on `.session` and never on the `table` of `session.table`.
- Module docstring records all three routes and why the view forms are the worst of them.

**RED** — `pytest tests/test_snowpark_rules.py -p no:randomly`, tests in place, rules unchanged:

```
23 failed, 75 passed in 0.22s
FAILED …::test_creating_a_view_or_dynamic_table_is_refused[create_or_replace_view|
        create_or_replace_temp_view|create_or_replace_dynamic_table]                             (3)
FAILED …::test_the_remaining_snowpark_write_routes_are_refused[write_pandas|copy_into_table|
        cache_result]                                                                            (3)
FAILED …::test_write_pandas_through_the_dataframes_own_session_is_refused
FAILED …::test_the_session_attribute_is_refused_on_any_receiver
FAILED …::test_every_pandas_writer_is_refused[to_csv|to_parquet|to_json|to_excel|to_pickle|to_sql|
        to_feather|to_hdf|to_clipboard|to_html|to_latex|to_markdown|to_xml|to_stata|to_gbq]     (15)
```

**GREEN**: `tests/test_snowpark_rules.py` 98 passed (was 72); together with
`tests/test_canned_artifacts.py` and `tests/test_compile_check_snowpark.py`, **184 passed** — so
the canned `wf_0006/seg_02/proc.py`, the committed `workflows/wf_0006/segments/seg_02/proc.py` and
the broken variant `samples/wf_0006/broken_sql/seg_02/01_cancellation_reset_ignored.py` all stay
rules-clean.

**The positive control.** `GOOD4` in `tests/test_snowpark_rules.py` is a procedure that uses 28
distinct DataFrame/Column methods — `filter`, `where`, `select`, `with_column`, `with_columns`,
`with_column_renamed`, `drop`, `dropna`, `fillna`, `distinct`, `sort`, `limit`, `group_by`, `agg`,
`join`, `cross_join`, `union`, `union_all`, `union_by_name`, `except_`, `intersect`, `rename`,
`count`, `sample`, `describe`, `first`, `collect`, `to_pandas`, plus `alias` / `asc` / `desc` /
`is_not_null` — and one sink. It is clean before R1 and clean after: the deny-lists ban write
routes, not the DataFrame API. `test_to_pandas_itself_is_not_a_writer` is the matching control for
the pandas list.

---

## R2 — a park reason describes the LAST attempt

Round 1 fixed the cross-run case; the re-review found the same defect *inside* one stage call,
because a failed `verify` is handed to `runAgent` as a generic `missing-output`, which `RETRY_ONCE`
retries. So attempt 1 recording `target-missing: seg_01` and attempt 2 dying before verify
(`denied`, `context-overflow`, budget) still parked with attempt 1's reason.

**Changed** — `orchestrator/stages.ts`: `runAgent` takes a `stage: Stage` and clears
`m.reasons[stage]` at the top of every loop iteration (`:100-120`), i.e. before each attempt,
covering both the retry-once and the rate-limit-backoff paths. All six call sites pass their stage
(`parse`, `intake`, `analyze`, `translate` ×3, `document`). The two stage-level
`clearAnalyzeReason` calls stay, as the ruling said: they are what handles the reason
`reloadManifest` copies back off disk at the start of a run.

`orchestrator/test/fakes.ts`: `agentErrors` widened from `AgentError[]` to `(AgentError | null)[]`,
`null` meaning "let this call run for real" — which is what a test needs to make attempt 1 fail
*verify* and attempt 2 fail before it. The fake's own `if (queued)` check already treated a falsy
entry that way; only the type changed.

**RED** — `fnm exec --using=22 npm.cmd test`:

```
not ok 163 - an analyze attempt that fails verify, then one that fails before it, parks with the LAST reason
    attempt 2's reason, not attempt 1's target-missing
    + actual - expected
    + 'target-missing: seg_01'
    - 'denied'
# tests 179 / # pass 178 / # fail 1
```

**GREEN**: `# tests 179 / # pass 179 / # fail 0 / # skipped 0`, tsc clean.

---

## R3 — the hand-off scans walk every tracked file

**Changed** — `tests/test_committed_workflows.py`:

- `_hand_off_files()` returns `_tracked()` with no arguments: every tracked file in the repository,
  binary ones skipped where they are read. `HAND_OFF_TREES` is gone — a tree that does not exist
  yet cannot be forgotten. The guard is now `len(tracked) > 500`.
- `_SEGMENT_PUNCTUATION = ".,;:)"` is stripped off the captured user segment before it is judged.
  This is what admits README §6's two documented Git-Bash path explanations — "`pwd` prints
  `/c/Users/…`" and "the drive-letter form `C:/Users/…`." — the second of which ends a sentence, so
  the regex captures `….` rather than `…`. It weakens nothing: a real login name followed by a full
  stop is still a real login name. (I chose this over listing the two README strings literally: one
  mechanism, named in the comment, that cannot go stale when the sentence is rewrapped.)
- The three exemptions are unchanged and still named: `docs/superpowers/build-reports/`,
  `docs/live-smoke-test.md`, and `_CHECKS_OWN_FILE` (this module, which must spell out the names
  and the word it bans).

**RED, in both directions, with a staged probe.** `cookbook/_r3_probe.md` (two lines carrying the
login name, `C:\Users\<user>\Desktop\…` and `scratchpad`) was written and `git add`ed, so
`git ls-files` could see it:

```
$ pytest tests/test_committed_workflows.py -k shipped_file      # five-tree scans, probe present
3 passed, 41 deselected in 0.19s          <- the gap R3 closes: cookbook/ was not scanned

$ pytest tests/test_committed_workflows.py -k shipped_file      # whole-tree scans, probe present
E  absolute paths of a real machine are committed:   cookbook/_r3_probe.md: 'C:\\Users\\<user>'
E  a build machine's login name is committed:        cookbook/_r3_probe.md: <user>
E  these files point at a scratchpad directory …:    cookbook/_r3_probe.md
3 failed, 41 deselected in 0.32s
```

The probe was then `git rm --cached`ed and deleted; `git status` confirmed it left nothing behind,
and the file appears in no commit.

**GREEN**: `tests/test_committed_workflows.py` 44 passed.

**Whole-tree greps at `ccd7907`** (889 tracked files):

- `<user>` → one hit, `tests/test_committed_workflows.py:304`, the deny-list's own home.
- `scratchpad` → six files: the test module and the five documented build-report / smoke-test
  exemptions.
- Machine paths with a non-placeholder user segment → **0**. (Round 1's single README hit is now
  admitted by the punctuation strip rather than by luck of not being scanned.)

---

## R4 — spec §4.2 re-synced, and the deny names pinned in three places

**Changed**:

- `docs/superpowers/specs/2026-09-22-output-targets-design.md` §4.2 — the `- Rules (checked by
  compile_check.py --target snowpark, §5.1)` bullet rewritten as seven sub-bullets that state the
  contract as it now is: the import/builtin/module/raw-SQL/dunder deny-lists; the session handed
  not fetched (`Session` refused outright, `session.sql`/`session.call` and the twelve other
  session methods refused on any receiver, `session` never read off another object because
  `DataFrame.session` is the live session); reads through a declared `inputs[].logical` or a
  literal work table, shape alone not enough; **exactly one sink**
  `.write.mode("overwrite" | "append").save_as_table(<one positional literal>)` with a declared
  `outputs[].logical`, `saveAsTable` held to the same rule, and every other write route listed by
  name; write methods called where they are written, never referenced, and never with keyword
  arguments; column order part of the schema; the tool-comment rule and the `pandas` justification.
- `.github/agents/translator.agent.md` — the same bullet, copied **byte-identically** by script
  rather than retyped, replacing both the old bullet and the standalone column-order bullet round 1
  had added (the rule now lives inside the copied text, so `COLUMN_ORDER_HOMES` still holds).
- `docs/reference/output-targets.md` §3.2 — restructured into three bullets (rules / the session /
  reads and the one sink) carrying the same deny names, with a sentence on why the view forms
  matter most: they *succeed* in the local double, so before R1 an undeclared object was created
  and the segment still PASSed. One duplicated "No `session.sql` is a deliberate ruling" paragraph
  removed.
- `tests/test_agents_config.py` — the verbatim pin's marker dropped its trailing colon (the
  bullet's first line changed); the pin itself is unchanged and still passes.

**Three new pins**, so this cannot drift again:
`test_the_spec_rules_bullet_names_every_refusal_the_rules_implement`,
`test_the_reference_page_names_every_refusal_the_rules_implement` and
`test_the_translator_carries_the_spec_bullet_and_therefore_every_refusal` assert that every name in
`FORBIDDEN_NAMES | FORBIDDEN_MODULES | RAW_SQL_NAMES | FORBIDDEN_SINKS | PANDAS_WRITERS |
CALL_ONLY_ATTRS | FORBIDDEN_SESSION_NAMES | NO_SESSION_SQL_ATTRS | FORBIDDEN_SESSION_ATTRS` appears
in code ticks in the spec bullet, the reference page and the translator (`sql` and `call` counted
as `session.sql` / `session.call`, which is how a reader meets them).

These three passed on their first run — the documents were already complete — so they are
regression pins rather than RED-first tests. **Proved discriminating** by temporarily adding a
`to_nowhere` entry to `PANDAS_WRITERS`:

```
E  AssertionError: design §4.2's rules bullet does not mention: ['to_nowhere']
E  AssertionError: docs/reference/output-targets.md does not mention: ['to_nowhere']
2 failed, 38 deselected in 0.06s
```

`scripts/lib/snowpark_rules.py` was restored from a scratch copy immediately after (verified by
`git diff --stat`, which showed only round 2's intended 44/6 line change).

---

## Round 2 counts

| suite | at `9d5255b` | at `ccd7907` |
|---|---|---|
| pytest | 1277 passed, 0 skipped | **1306 passed, 0 skipped** (81.5 s) |
| node | 179 tests defined, 178 pass + 1 RED | **179 pass**, 0 fail, 0 skipped, 0 todo |
| tsc | clean | **clean** |

Per-file python deltas: `test_snowpark_rules.py` 72 → 98 (+26), `test_agents_config.py` 37 → 40
(+3). 1277 + 29 = 1306; no other file's count moved. `workflows/` untouched this round.

---

## Round 2 concerns

1. **`session` as an attribute is now refused on any receiver**, so a procedure that reads
   `something.session` for a legitimate reason is refused. The ruling priced this ("none exists in
   C4"), and the canned, committed and broken procedures plus the 28-method positive control are
   all clean — but it is the one refusal in this round that could in principle bite a valid
   procedure, and `DataFrame.session` is a public attribute of the API.
2. **`cache_result` is a refusal with a cost.** It is a legitimate optimisation on a real account
   (it materialises a temp table to avoid re-computing a branch), and it is now banned outright
   rather than checked. That follows the ruling and is the conservative side, but it is the one
   name on the list a competent translator might reach for innocently.
3. **The whole-tree scan will fail on a future build report** that narrates where a run happened,
   unless its path is added to `_SCRATCHPAD_EXEMPT`. That is the intended trade (the exemption is a
   named constant, so adding to it is a deliberate act), but it is friction a future task will meet.
4. **R4's three deny-name pins were green on arrival.** I proved them discriminating with a
   throw-away entry rather than by leaving a real gap, which is the best evidence available once
   the documents are already correct — but they are not RED-first in the strict sense.
5. **Unchanged standing note:** nothing here has run against real Snowflake or real Alteryx. R1's
   premise — that the view/dynamic-table forms succeed in the Local Testing Framework and that
   `write_pandas` would write on a real account — is the re-reviewer's measurement against
   `snowflake-snowpark-python 1.55.0`, which I did not re-run; I implemented the refusals it asked
   for.
