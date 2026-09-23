# Task C4V report — the documented `IDENTIFIER` form for every SQL procedure

Worktree `.worktrees/p2-C4V`, branch `wt/p2-C4V`, base `39120ae`, commit `a154488`. Status: DONE_WITH_CONCERNS
(two concerns, under "Concerns"; two scope additions, under "Brief corrections").

## Why

Snowflake's documentation (docs.snowflake.com/en/sql-reference/identifier-literal) gives
`IDENTIFIER( { string_literal | session_variable | bind_variable | snowflake_scripting_variable } )` —
a single value, not an expression. Every SQL procedure the pipeline wrote used
`IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')`, an expression. Every procedure now builds the
name first and passes the variable:

    LET ORDERS_SRC VARCHAR := :SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS';
    ... FROM IDENTIFIER(:ORDERS_SRC) ...

Nothing here has run on Snowflake. Every doc that mentions the new form says it is the DOCUMENTED form,
chosen because the old one is not in Snowflake's grammar, and that the first real-account run confirms it.

## What was built

**`scripts/lib/proc_runner.py` (the DuckDB double).**
- `parse_proc` now accepts `LET <VAR> VARCHAR := <expr>` where `<expr>` is the procedure's own
  `:<parameter>`s and string literals joined by `||`, nothing else. The LETs are returned in the new
  `ProcInfo.lets: list[Let]` (`Let(name, expression, text)`, body order; `text` has the comments in
  front of the statement removed). They are NOT in `ProcInfo.statements`, so the statement count, and
  every existing consumer of `statements`, is unchanged.
- Refused with `LetError` (a new `ProcError` subclass), with a message that quotes the LET and keeps the
  old "outside the supported procedure subset (plan contract C4)" wording: any other LET shape (no type,
  a type other than VARCHAR, `DEFAULT`, a function call, a bare name, a subquery, a name that is not a
  parameter of this procedure, no value), a LET that shadows a parameter, a variable declared twice, and a
  statement that uses `:<VAR>` before the LET that declares it.
- `let_values(proc, args)` evaluates every LET from the call arguments (parameter substitution, then the
  literals joined). `run_proc` evaluates them and never sends a LET to the backend; it then binds each
  statement with `{**args, **let_values}`. A variable is therefore bound exactly like a parameter — a SQL
  string literal — anywhere `:<VAR>` appears in code (not in strings, comments or `::` casts), and
  `IDENTIFIER(:<VAR>)` becomes `IDENTIFIER('MIGDB.S.T')`, which the existing fold turns into `MIGDB.S.T`.
  The bound text a backend receives is byte-identical to what the old form produced.
- `identifier_arguments(statement)`: every `IDENTIFIER(…)` argument in the code of a statement, unbound
  (used by compile_check and by the canned scan).
- `LET` left `_SCRIPTING_KEYWORDS`; every other keyword there is refused as before.
- The old expression form is still folded by `bind` (string literals joined by `||` inside
  `IDENTIFIER(…)`); `tests/test_compile_check_c4v.py::test_the_local_runner_still_folds_the_old_form_but_compile_check_refuses_it`
  is the explicit test that runs one old-form procedure on the double AND shows compile_check refusing it.
- P2 coupling (binding): `parse_proc` itself accepts the documented form, so
  `SnowflakeBackend.call_procedure`'s use of it reads the C4 order unchanged —
  `tests/test_proc_runner_let.py::test_parse_proc_reads_name_parameters_and_statements_of_a_procedure_in_the_documented_form`,
  and `tests/test_canned_artifacts.py::test_every_canned_procedure_has_the_c4_signature` now parses every
  canned procedure in the new form.

**`scripts/compile_check.py`.** Two named checks, part of check 1, run after the signature check and
before bind/parse/run (a refused table reference ends the check, like a wrong signature):
- `c4:let_form` — every LET's expression is exactly `:SRC_DB || '.' || :SRC_SCHEMA || '.<LOGICAL>'` or the
  TGT twin (same side on both parameters, `<LOGICAL>` matching intake's `^[A-Z_][A-Z0-9_$]*$`), and the
  variable is exactly `<LOGICAL>_SRC` / `<LOGICAL>_TGT` (upper case). A LET the runner refuses (`LetError`)
  is reported under the same name.
- `c4:identifier_expression` — every `IDENTIFIER(…)` argument is one `:<VAR>` a LET of the procedure
  declares, one string literal, or one session variable (`$X`). An expression, and also `IDENTIFIER(:SRC_DB)`
  (a parameter, not a LET variable — see "Design decisions"), are refused with a message that says what to
  write instead.
- `table_reference_errors(proc)` is public: the canned scan calls it.
- Binding uses `let_values`; the existing checks (sqlglot parse, contract-shaped dry run, output columns)
  run on the bound text exactly as before.

**`orchestrator/policy.ts` + `orchestrator/POLICY.md`.** In a `CREATE PROCEDURE … $$ … $$` body (B4):
- a LET is accepted only if it matches the rule exactly (keywords case-insensitive; `:SRC_DB`/`:SRC_SCHEMA`
  or `:TGT_DB`/`:TGT_SCHEMA`, same side; `<LOGICAL>_<SIDE>` naming); it records `VAR → MIG_WORK.CONTRACT_<SIDE>_<LOGICAL>`;
  any other LET → `a LET must build a contract-C4 name: …`; a second LET of the same variable → denied;
- `IDENTIFIER(:<VAR>)` is replaced by that sandbox name only for a VAR declared EARLIER in the same body
  (case-insensitive reference); everything else — including the old expression form — still hits
  `dynamic object name: IDENTIFIER( cannot be judged by this policy`;
- new, because variables now carry trust: a `DECLARE` section, an assignment `<var> := …` and
  `… INTO :<var>` are denied in a body (each could re-bind a LET variable to a non-contract name — see
  "Design decisions");
- `SqlRules.contractIdentifiers: boolean` became `contractVariables?: Map<string, string>`.

**Procedures (mechanical, minimal).** All 25 SQL procedures that used the old form — 12 canned
(`wf_0001` seg_01; `wf_0002` seg_01–03; `wf_0003` seg_01–02; `wf_0004` seg_01, seg_03; `wf_0006` seg_01,
seg_03) and 13 broken variants — were rewritten by one scratch converter: each old expression became
`IDENTIFIER(:<LOGICAL>_<SIDE>)`, and one LET per distinct (side, logical) — sources first, then targets,
each in order of first appearance — was inserted right after the `ALTER SESSION` line, under one comment
line. No other byte changed. The three `wf_0004/seg_02` variants, `wf_0004/seg_02/proc.sql` and the
Snowpark wrapper `wf_0006/seg_02/proc.sql` use no mapped table and are untouched. The same converter moved
the procedure fixtures in `tests/test_proc_runner.py`, `tests/test_compile_check.py`,
`tests/test_validate_segment.py`, `tests/test_validate_segment_fix_round_1.py` and `tests/chain_fixtures.py`
(LET lines at the start of the body); `orchestrator/test/fakes.ts` and the policy test's
`VALIDATOR_PROCEDURE` were edited by hand.

**Broken variants re-observed.** Every SQL broken variant (18 rows of `broken.json`, including the three
untouched `wf_0004/seg_02` ones) was run through `validate_segment` before and after the change by a
scratch script that prints verdict and every cluster's (class, columns, stream, scope, count). The two
outputs are byte-identical (`diff` empty); every row's recorded class/columns/stream is hit. No
`broken.json` changed.

**Docs and agents.**
- `.github/agents/translator.agent.md`: the C4 bullet states the LET rule, `IDENTIFIER(:<LOGICAL>_SRC|_TGT)`,
  the two named checks, the documented-form/first-real-account-run sentence, and "no other LET, no
  DECLARE, variable assignment, …" (the old sentence forbade every LET); the dbt paragraph's
  "the `IDENTIFIER` forms" became "its `LET` and `IDENTIFIER` table references". Marker
  `<!-- amended: output targets phase 2 -->` added. The only verbatim-pinned spec bullet in the file is
  §4.2's Snowpark bullet, which does not mention IDENTIFIER — untouched.
- `.github/agents/reviewer.agent.md`: one continuation line under the existing "IDENTIFIER with logical
  names" blocking check (not in the brief's list; added so the reviewer agrees with compile_check).
- `cookbook/input.md`, `cookbook/output.md`: the pattern, the "real procedure" paragraph and the "Do not"
  bullets; input.md gains a "Do not write an expression inside `IDENTIFIER(…)`" bullet. No cookbook
  example contains a full procedure (checked: `tests/cookbook_examples/**` has no `SRC_SCHEMA`).
- `docs/superpowers/specs/2026-09-18-alteryx-snowflake-pipeline-design.md` §3.1: the example uses the LET,
  the runner description says how LETs are evaluated, and a new paragraph "Table references use Snowflake's
  documented IDENTIFIER form (amended 2026-09-23, phase-2 Task C4V)" is the ONE place in the scanned tree
  that shows the old form (one sentence). It says the program spec never shows IDENTIFIER, so this amends
  our own contract C4, not the program spec.
- `docs/superpowers/plans/2026-09-18-pipeline/00-index.md` contract C4: amended in place with an italic,
  dated amendment note (the code cites "plan contract C4" everywhere, so its text had to stop teaching the
  undocumented form) — not in the brief's list; see "Brief corrections".
- `samples/wf_000{1,2,3,4,6}/canned/docs/migration.md` and three `translation_notes.md` (wf_0001 seg_01,
  wf_0003 seg_02, wf_0004 seg_03 — the last two also said "contract C4 allows no variable", now "… beyond
  the LET that builds the target's name" / "its only LETs build table names").
- Docstrings/comments: `scripts/gen_source_views.py`, `scripts/lib/validation.py`, `scripts/intake_prompt.py`,
  `tests/test_intake_fix_round_3.py`, `README.md` (the mappings.yaml paragraph).

## Tests (all written first; RED evidence below)

New files: `tests/test_proc_runner_let.py` (21), `tests/test_compile_check_c4v.py` (24),
`tests/test_documented_identifier_form.py` (3). Added to `tests/test_canned_artifacts.py` (3 tests × 5
procedure workflows = 15): `test_every_sql_procedure_uses_only_the_documented_identifier_form` (canned +
broken: `table_reference_errors == []`, every IDENTIFIER argument is `:<LOGICAL>_SRC|_TGT`, every LET used),
`test_every_canned_procedure_builds_one_name_per_mapped_source_and_target` (LET set == contract input
logicals `_SRC` ∪ target output logicals `_TGT`), `test_every_canned_procedure_passes_compile_check`
(every segment, its own target — there was no such test for SQL samples before). `tests/test_agents_config.py`
+2 (`test_translator_states_the_documented_identifier_form`, `test_reviewer_checks_the_documented_identifier_form`)
and its C4 token list now pins `IDENTIFIER(:<LOGICAL>_SRC)`/`_TGT)`. `orchestrator/test/policy.test.ts` +4
(`C4V: …`), and ruling B4's test now uses the new-form `VALIDATOR_PROCEDURE`.

The repo-wide scan (`tests/test_documented_identifier_form.py`) walks `git ls-files` (`.sql .md .py .ts`)
and flags an `IDENTIFIER(` whose argument — up to the first `)`, `"` or backtick — contains `||` or opens
with a function call. ALLOWED, with exact counts (a new occurrence anywhere, in these files too, fails):
`tests/test_compile_check_c4v.py` 2 (the refusal test's two constants), `orchestrator/test/policy.test.ts`
2 (same), `tests/test_documented_identifier_form.py` 3 (its self-test), the design spec 1 (the deviation
sentence). EXCLUDED, each with its reason in the file: `workflows/**` (Task G refreshes and removes the
exclusion), `docs/superpowers/build-reports/**`, `docs/superpowers/rulings/**` and the executed Task 6 plan
`docs/superpowers/plans/2026-09-18-pipeline/02-sql-runtime-compare.md` (dated historical records). A third
test asserts each exclusion still names a tracked path.

### RED
- `pytest tests/test_proc_runner_let.py` → `ImportError: cannot import name 'LetError'`; and, on the old
  code, `parse_proc(<the new-form fixture>)` → `ProcError outside the supported procedure subset (plan
  contract C4): -- contract C4: … LET ITEMS_SRC VARCHAR := …` — the P2 coupling, reproduced.
- `pytest tests/test_compile_check_c4v.py` → `22 failed, 2 passed` (the two that passed are the "string
  literal / session variable is a documented argument" cases, which assert the ABSENCE of a c4 error).
  The documented form did not compile: `IDENTIFIER(…) must be string literals joined by || once bound, got
  IDENTIFIER(:ITEMS_SRC)`; the old form was `OK`.
- `pytest tests/test_canned_artifacts.py -k "documented_identifier or one_name_per or passes_compile_check"`
  → `AttributeError: module 'compile_check' has no attribute 'table_reference_errors'` (×5), empty LET sets
  (`assert [] == ['GL_LEDGER_SRC']` …), and every canned `c4:identifier_expression: IDENTIFIER(:TGT_DB || …)`.
- `pytest tests/test_documented_identifier_form.py` → the offender list: translator agent, cookbook,
  plan C4, POLICY.md, fakes.ts, all 25 procedures, the sample docs, later `tests/test_intake_fix_round_3.py`.
- `node --test orchestrator/test/policy.test.ts` → `# fail 5` (B4 with the new fixture, and the four C4V
  tests; the old-form test was strengthened after its first RED run passed for the wrong reason — see
  "Self-review").
- `pytest tests/test_agents_config.py` → `3 failed, 58 passed`.

### GREEN — see "Verification".

## Design decisions not spelled out in the brief

1. **LETs are not statements.** `ProcInfo.statements` keeps only executable SQL, so compile_check's
   `statements` count and every consumer that iterates statements are unchanged; `ProcInfo.lets` carries the
   LETs. Evaluation is `let_values`, and `bind` stays the one function that substitutes `:<NAME>`: the brief's
   "bind evaluates LET … substitutes `:<VAR>`" is realised as `bind(statement, {**args, **let_values(proc,
   args)})` — the same substitution for a variable as for a parameter, as controller note (1) asks.
2. **Use before LET is refused by `parse_proc`**, not left to fail later, so the P2 Snowflake path refuses
   the same procedures the double does.
3. **`IDENTIFIER(:SRC_DB)` is a `c4:identifier_expression`** ("names no variable a LET of this procedure
   declares") although it is grammatical: the policy only trusts LET-declared variables, and the rule says
   every reference is `IDENTIFIER(:<VAR>)` from a LET. Without this compile_check would pass a procedure the
   policy then denies. String literals and session variables stay accepted by compile_check exactly as the
   brief says (the policy still denies them — it did before too).
4. **The policy denies DECLARE / `<var> :=` / `INTO :<var>` in procedure bodies.** Trusting
   `IDENTIFIER(:ORDERS_SRC)` means trusting that nothing between the LET and the use changes the variable;
   those three are the ways a body could (a nested `DECLARE` shadow, a re-assignment, `SELECT 'FINANCE.RAW.X'
   INTO :ORDERS_SRC`). A second LET of the same name is harmless in value (the rule fixes it) but denied for
   consistency with compile_check. None of these occur in any canned procedure.
5. **Strictness of the rule.** compile_check and the policy both require `:SRC_DB`/`:SRC_SCHEMA` in upper
   case and the exact variable name `<LOGICAL>_<SIDE>`; the runner accepts `let`/`varchar` in any case (they
   are keywords). References `IDENTIFIER(:orders_src)` are case-insensitive everywhere, as Snowflake's unquoted
   names are.
6. **One comment line above each LET block** in the procedures: `-- contract C4: each mapped table's name is
   built once, then referenced as IDENTIFIER(:<name>)`. The only non-mechanical line added to them.

## Brief corrections

- **Scan exclusions beyond `workflows/**`.** The controller's exception list is "the tests proving the
  refusal and the one design-spec sentence". Seven build reports, the 2026-09-18 rulings and the executed
  Task 6 plan quote the old form as the record of what was built and ruled at the time. Rewriting dated
  records would falsify them (the same reasoning `tests/test_committed_workflows.py` already applies to
  build reports for its scratchpad scan), so the scan excludes those three paths, with the reason in the
  file. The live contract text (plan `00-index.md` C4) was amended instead of excluded. If the controller
  prefers the records rewritten, it is three `EXCLUDED_PREFIXES` entries to drop plus the text edits.
- **The scan's own self-test is an allowed file** (3 occurrences, exact count) rather than building its
  samples by string concatenation to hide them.
- **`00-index.md` and `reviewer.agent.md`** were edited though not in the brief's file list (reasons above).

## Files changed

`scripts/lib/proc_runner.py`, `scripts/compile_check.py`, `scripts/gen_source_views.py`,
`scripts/intake_prompt.py`, `scripts/lib/validation.py`; `orchestrator/policy.ts`, `orchestrator/POLICY.md`,
`orchestrator/test/policy.test.ts`, `orchestrator/test/fakes.ts`; `.github/agents/translator.agent.md`,
`.github/agents/reviewer.agent.md`; `cookbook/input.md`, `cookbook/output.md`; `README.md`;
`docs/superpowers/specs/2026-09-18-alteryx-snowflake-pipeline-design.md`,
`docs/superpowers/plans/2026-09-18-pipeline/00-index.md`; 25 procedures under `samples/**/canned/**` and
`samples/**/broken_sql/**`, 5 `canned/docs/migration.md`, 3 `translation_notes.md`; tests:
`tests/test_proc_runner_let.py` (new), `tests/test_compile_check_c4v.py` (new),
`tests/test_documented_identifier_form.py` (new), `tests/test_canned_artifacts.py`, `tests/test_agents_config.py`,
`tests/test_proc_runner.py`, `tests/test_compile_check.py`, `tests/test_validate_segment.py`,
`tests/test_validate_segment_fix_round_1.py`, `tests/chain_fixtures.py`, `tests/test_intake_fix_round_3.py`.
No `broken.json` changed. `workflows/**` untouched.

## Self-review findings (fixed before reporting)

- `Let.text` first carried the comment line in front of the first LET; it is now the comment-free statement
  (a message quoting it would otherwise quote the comment), pinned by a test.
- The first version of the policy's old-form test passed on the OLD code for the wrong reason (the fixture's
  other reference was already undeclared, so the procedure was denied anyway). It now also denies a
  procedure written wholly in the old form, which the old policy allowed — a real RED.
- `String.prototype.replace` with a `"$$…"` replacement string turned `$$` into `$` in a TS test; switched to
  a replacer function.
- The scan's argument capture first ran to the first `)` across lines, which flagged a Python `assert
  "IDENTIFIER(:SRC_DB" not in body` followed by an unrelated `||` lines later; it now also stops at `"` and
  backticks (self-tested).

## Things P2, W4 and G must know

- **P2** (`SnowflakeBackend.call_procedure` calls `parse_proc`): `parse_proc` accepts the documented LET form
  and returns the same name/params/execute_as; `ProcInfo.statements` no longer contains LETs (they are in
  `ProcInfo.lets`). If P2 ever sends a procedure BODY statement-by-statement to Snowflake, it must not rely on
  `statements` alone — the LETs are needed there (Snowflake evaluates them); deploying the whole `CREATE
  PROCEDURE` text (the normal path) is unaffected. `LetError` is a `ProcError`, so existing `except ProcError`
  handlers keep working. A merge of P2 onto this branch should re-run `tests/test_proc_runner*.py`,
  `tests/test_canned_artifacts.py` and P2's own tests; any P2 fixture procedure written in the old form will
  fail compile_check (`c4:identifier_expression`) and the repo-wide scan — move it with the same LET block.
- **W4**: `orchestrator/policy.ts` changed in `checkProcedure` / `checkStatement` (`SqlRules.contractIdentifiers`
  → `contractVariables?: Map`); W4's policy lanes do not touch the SQL judge, but a merge conflict in
  `policy.ts` or `policy.test.ts` is possible — keep both sides. `.github/agents/{intake,analyzer,fixer}` were
  not touched.
- **G**: the committed `workflows/**` trees still show the old form (they are copies of the old canned
  artefacts). Refreshing them from the canned artefacts is G's; then remove `"workflows/"` from
  `EXCLUDED_PREFIXES` in `tests/test_documented_identifier_form.py` (its comment says so) — the scan must then
  pass with no change to ALLOWED. A committed `compile_check.json` refreshed from the new procedures reports
  the same `statements` counts as before (LETs are not counted).
- **H / P4 (verification ladder)**: the first real-account run should deploy one canned procedure and
  `CALL` it — that is what confirms the documented form compiles and runs; nothing here proves it.

## Concerns

- The new form is DOCUMENTED, not verified: no Snowflake account was used. One detail of the binding rule
  deserves the first real-account run's attention: the rule writes the procedure's arguments WITH a colon
  inside the LET expression (`LET X VARCHAR := :SRC_DB || …`). From memory (not re-checked here — no network
  use in this task), Snowflake's Scripting docs require the colon when a variable is used in a SQL statement
  and show variables in LET/RETURN expressions WITHOUT it; whether the colon is also accepted in a LET
  expression is exactly what that run confirms. If it is refused, the fix is mechanical and local: drop the
  two colons in each LET (the procedures' converter, `proc_runner._is_parameter_concatenation`,
  `compile_check._C4_LET_RE`, `policy.ts CONTRACT_LET`, the docs' rule text). I kept the brief's rule as
  written.
- Policy tightening (decision 4) is new behaviour for the validator's SQL; no canned procedure trips it.

## Verification (final, on the committed tree `a154488`)

- **pytest, whole suite** (`.venv/Scripts/python.exe -m pytest`, run on the tree of the last WIP commit
  `b12bf68`, whose tree is identical to `a154488` — checked with `git rev-parse <c>^{tree}`):
  `1695 passed in 378.17s`, 0 skipped (baseline 1630 + 65 new: 21 + 24 + 3 + 15 + 2).
- **node** (`fnm exec --using=22 npm.cmd test` on `a154488`): `# tests 282 / # pass 282 / # fail 0 /
  # skipped 0` (baseline 278 + 4 new).
- **tsc** (`--noEmit -p .` on `a154488`): exit 0.
- **Broken variants**: before/after observation of all 18 SQL rows byte-identical (see "What was built").
- **Every sample in a scratch root outside the repo** (a scratch script: `tests.helpers.prepare_workflow`
  into `<scratch>/<wf>`, then the real CLIs with `--root`): every segment's `compile_check.py` exit 0 `OK`;
  every segment's validator (`validate_segment.py`, `validate_snowpark.py` for `wf_0006/seg_02`) exit 0,
  `verdict=PASS`, all four sets PASS, `idempotent=True`; `validate_workflow.py` run TWICE per sample:

  | sample | chain run 1 | chain run 2 |
  |---|---|---|
  | wf_0001 | PASS, 4/4 sets, idempotent=True | PASS, 4/4, idempotent=True |
  | wf_0002 | PASS, 4/4, idempotent=True | PASS, 4/4, idempotent=True |
  | wf_0003 | PASS, 4/4, idempotent=True | PASS, 4/4, idempotent=True |
  | wf_0004 | PASS, 4/4, idempotent=True | PASS, 4/4, idempotent=True |
  | wf_0005 | n/a — parser-recovery sample, no translation, no chain | n/a |
  | wf_0006 | PASS, 4/4, idempotent=True | PASS, 4/4, idempotent=True |
  | wf_0007 | PASS, 4/4, idempotent=True (dbt; delegated to `validate_dbt.py`) | PASS, 4/4, idempotent=True |

  For `wf_0007` the scratch script records `manifest.output_kind = "dbt"` first, exactly as
  `tests/test_e2e_chain.py` does (the analyze stage writes it; `prepare_workflow` does not run analyze) —
  without it `validate_workflow.py` exits 2 "no procedure to chain", which is pre-existing and unrelated.
  `compile_check.py --target dbt` and `validate_dbt.py` also exit 0 for it.

## Commits

`a154488 fix: every SQL procedure uses Snowflake's documented IDENTIFIER form — names built with LET,
IDENTIFIER(:var) only` — the four `wip:` commits made along the way (`bbe4ab5`, `2698900`, `c052168`,
`b12bf68`) were squashed into it with `git reset --soft 39120ae` (no stash, no hard reset); the tree is
unchanged.

Status: **DONE_WITH_CONCERNS** — the concerns are the unverifiable-locally colon-in-LET detail and the
scan exclusions for historical records, both above.

## Fix round 1 — no colon inside LET expressions (coordinator ruling, 2026-09-23)

**Ruling applied.** Snowflake's Scripting documentation (developer-guide/snowflake-scripting/variables)
uses the colon to bind a variable inside a SQL statement; in an expression or a Scripting element a
variable, and a procedure argument (which behaves like a declared variable), is named without it. The
documented form is therefore `LET <VAR> VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>';` (TGT
twin likewise), with `IDENTIFIER(:<VAR>)` keeping its colon inside the SQL statement. This settles
concern 1 of the first report; the other choices were accepted as they were.

**What changed.**
- `scripts/lib/proc_runner.py`: a LET's right-hand side is the procedure's arguments by bare name
  (case-insensitive, like any unquoted name) and string literals joined by `||`
  (`_is_argument_concatenation`); `let_values` evaluates bare names. A colon-prefixed argument inside a
  LET is a `LetError` of its own: "a LET names the procedure's arguments without a colon (:SRC_DB, … here)
  -- in Snowflake's documented expression syntax the colon binds a variable inside a SQL statement, as in
  IDENTIFIER(:<VAR>), not in a LET; got …" (`_colon_arguments`). Docstring and `_LET_SHAPE` updated.
- `scripts/compile_check.py`: `_C4_LET_RE` accepts only `SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>'` (and
  TGT); a colon inside a LET surfaces as `c4:let_form: … without a colon …`; docstring and `_C4_LET_RULE`.
- `orchestrator/policy.ts`: `CONTRACT_LET` without colons; a LET whose right-hand side (strings stripped)
  names anything with a colon is denied with "a LET names the procedure's arguments without a colon …";
  `CONTRACT_LET_RULE` and the doc comment. `orchestrator/POLICY.md` likewise.
- Every canned and broken SQL procedure (25), the Python test fixtures, `orchestrator/test/fakes.ts` and
  the policy test fixtures: moved by one scratch converter that removes a colon only on the right-hand side
  of a `LET … VARCHAR :=` (up to the first `;`, `"` or backtick). In the sample SQL only the 38 LET lines
  changed (checked: no other changed line). The deliberate colon refusals were written by hand after it.
- Docs: translator agent (the C4 bullet says the arguments are named without a colon and that a colon there
  is refused as `c4:let_form`), reviewer agent (one clause), `cookbook/input.md`/`output.md` (why the LET
  has no colon and `IDENTIFIER(:…)` has one), design spec §3.1 (a paraphrase of the documentation with its
  URL, and the ruling), the `00-index.md` C4 amendment note, sample `migration.md` LET text,
  `gen_source_views.py`/`validation.py` docstrings. `docs/reference/*` contains no LET or IDENTIFIER text
  (grep) — nothing to change there.
- Scan (`tests/test_documented_identifier_form.py`): new `test_no_tracked_file_shows_a_colon_inside_a_let`
  (a `LET … VARCHAR :=` right-hand side that names anything with a colon), with `ALLOWED_COLON_LETS` exact
  counts — `tests/test_proc_runner_let.py` 2, `tests/test_compile_check_c4v.py` 2,
  `orchestrator/test/policy.test.ts` 2 (the refusal tests), `tests/test_documented_identifier_form.py` 3
  (its self-test: two samples and one expected result) — and `test_the_scan_recognises_a_colon_inside_a_let`.
  Same exclusions as the IDENTIFIER scan.

**Tests (RED first).** New: `test_a_colon_prefixed_argument_inside_a_let_is_refused_with_a_clear_message`
(×2) and `test_an_argument_inside_a_let_is_named_case_insensitively_like_any_unquoted_name`
(`tests/test_proc_runner_let.py`); `test_a_colon_inside_a_let_is_a_named_refusal` (×2,
`tests/test_compile_check_c4v.py`); the two scan tests; policy test "C4V fix round 1: a LET names the
procedure's arguments without a colon"; `test_translator_states_the_documented_identifier_form` now also
asserts `:= :SRC_DB`/`:= :TGT_DB` absent and "without a colon" present. Every positive fixture moved to the
no-colon form in the same RED commit.
- RED (`pytest` on those files, old code): `54 failed, 100 passed` — the no-colon LET was refused
  (`a LET must be \`LET <VAR> VARCHAR := <the procedure's parameters …>\`; got LET ITEMS_SRC VARCHAR :=
  SRC_DB || '.' || SRC_SCHEMA || '.ITEMS'`), and a colon LET gave the generic message, not "without a
  colon". Node (`policy.test.ts`): `# fail 5` (B4, three C4V tests on the moved fixtures, the new colon test).
- GREEN: see below.

**Broken variants re-observed.** The same scratch observer, all 18 SQL rows: output byte-identical to the
pre-C4V baseline (`diff` empty) — verdict and every cluster's class/columns/stream/scope/count. No
`broken.json` changed.

**Verification (on `3706fe5`; the pytest run was on the identical tree of the pre-squash WIP commit).**
- pytest whole suite: `1702 passed in 371.48s`, 0 skipped (+7 over the first round's 1695).
- node: `# tests 283 / # pass 283 / # fail 0 / # skipped 0` (+1).
- tsc: exit 0.
- Scratch root outside the repo, every sample (`prepare_workflow`, then the CLIs): every segment's
  `compile_check.py` exit 0 `OK` and its validator exit 0, `verdict=PASS`, 4/4 sets, `idempotent=True`
  (25 lines, all OK); `validate_workflow.py` twice per sample —
  wf_0001 PASS/PASS, wf_0002 PASS/PASS, wf_0003 PASS/PASS, wf_0004 PASS/PASS, wf_0006 PASS/PASS,
  wf_0007 PASS/PASS (dbt, `output_kind` recorded as `tests/test_e2e_chain.py` does), each 4/4 sets,
  idempotent=True; wf_0005 n/a (parser-recovery sample, no translation).

**Commit.** `3706fe5 wip: fix — no colon inside LET expressions (Snowflake's documented expression syntax)`
(two intermediate WIP commits, RED `432f0e6` and GREEN `75a8075`, squashed into it with `git reset --soft
a154488`; tree unchanged).

**Things P2, W4 and G must know (additions).** P2: any P2 fixture procedure must use the no-colon LET; a
colon inside a LET now fails `parse_proc` (`LetError`), `compile_check` and the scan. G: the refreshed
`workflows/**` procedures come from the canned ones and so carry the no-colon LET; the colon-in-LET scan
shares the `workflows/` exclusion G removes.

## Fix round 2 — the SQL judge denies every re-binding and control-flow form (rulings on the opus review of 3706fe5)

Status: DONE. Merge commit `169775e`, fix commit `a8ee12f`. I squashed this round's RED and GREEN WIP commits
into the fix commit with `git reset --soft 169775e`, and checked that the tree is unchanged.

**R0 — merge.** `git merge --no-edit feat/output-targets-phase2` (5078be1, which has Task P2) stopped on one
conflict: the `actual_table` docstring in `scripts/lib/validation.py`. P2 had added the `<database>` and
snowflake-backend wording; C4V had moved the target reference to the LET form. I kept both: P2's wording, with
C4V's `IDENTIFIER(:<LOGICAL>_TGT)` / `LET <LOGICAL>_TGT VARCHAR := TGT_DB || …`. Merge commit `169775e`.

The merge brought two old-form occurrences, converted in the fix commit:
- `tests/test_snowflake_conn.py`'s `PROC` now reads `LET ITEMS_SRC …; LET OUT_TGT …;` and references
  `IDENTIFIER(:OUT_TGT)`/`IDENTIFIER(:ITEMS_SRC)`. The fake runs it through `run_proc`, so the test still
  proves three things: the DDL is sent verbatim, the CALL binds the C4 order, and `SB.MIG_WORK.OUT` gets rows
  2 and 3.
- The §7 bullet of `docs/reference/snowflake-backend.md` now says the bodies use the documented LET +
  `IDENTIFIER(:<LOGICAL>_SRC)` form. Whether Snowflake accepts that form, and ALTER SESSION in a
  caller's-rights procedure, stays unverified until the first real-account run.

**C1 — `orchestrator/policy.ts` `checkProcedure`.** Every chunk of a judged body, other than a top-level LET
that follows the rule, is now checked in this order. Each denial gives a reason that names the construct.
1. The first word is a Snowflake Scripting block or control keyword: BEGIN, END, IF, ELSEIF, ELSE, CASE,
   FOR, WHILE, REPEAT, LOOP, BREAK, CONTINUE, EXCEPTION, DECLARE, OPEN, FETCH, CLOSE, RAISE, AWAIT, CANCEL
   or NULL. The word is read with comments removed, strings blanked and case ignored. The body's own outer
   BEGIN/END are stripped before splitting, as before, so any other BEGIN or END is nested. Reason:
   "`<KEYWORD>` cannot be judged by this policy: a contract-C4 procedure body is flat …".
2. The code contains `:=` anywhere. Reason: "assigning a variable (:=) …".
3. The code contains the word LET anywhere. Reason: "LET is accepted only as a whole statement of contract
   C4's form …".
4. Ruling (a): a RETURN whose code is not exactly `RETURN ''`, i.e. one string literal. Reason: "RETURN …
   must return one string literal".
5. Ruling (b): a CALL. Reason: "CALL inside a procedure body cannot be judged …".
6. ALTER SESSION SET is skipped and `INTO :<var>` is denied, both as before.
7. The first word is not on the allow-list SELECT/WITH/INSERT/UPDATE/DELETE/MERGE/CREATE/ALTER/TRUNCATE/
   DROP/COPY. Reason: "<word> is not a SQL statement this policy judges in a procedure body". This rule
   fails closed. It is what denies the review's `GL_SUMMARY_TGT DEFAULT 'PROD…'` row, which matched none
   of the keywords.
8. Anything left goes through B1–B3 with the declared contract variables, as before.

Ruling (c): after the body, the header's parameter list must be exactly `SRC_DB STRING, SRC_SCHEMA STRING,
TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING`, compared with comments stripped, whitespace normalised and
upper-cased. Any other list is denied with the expected list in the reason: reordered, missing, extra, a
DEFAULT, or VARCHAR. The header is checked after the body so the existing B4 tests, whose bodies fail first,
keep their reasons. The comment above the loop and `POLICY.md` §3 now state exactly these rules.

**Addition beyond the brief (same trust chain).** The policy's two tokenizers, `stripSqlNoise` and
`splitStatements`, misread two things. They took `\'` inside a string as the end of the string, and a
`"quoted identifier"` containing `'` as the start of one. Snowflake does neither, and neither does
`proc_runner`, so the judge could take code for string text. Both tokenizers now read a backslash escape
inside `'…'`, and a `"…"` identifier (with `""`), as Snowflake does.

My two crafted examples were still denied by the fix-1 judge, but only by accident: an unexplained qualified
reference happened to stay visible. With the fix, the denial names the assignment. Test: "C4V fix round 2:
a quote the judge misreads cannot hide a statement from it" — 2 denials, plus 1 allow of an identifier that
holds `--` and `'`.

**Bypass table → committed deny tests.** I ran the reviewer's three probes (`probe_policy.ts`,
`probe_policy2.ts`, `probe_policy3.ts`) on HEAD after the merge. Each of the 26 ALLOW rows that is a bypass
is now one entry of `REVIEW_BYPASSES` in `orchestrator/test/policy.test.ts`, denied and checked against a
reason regex:
- Assignments, as written: behind a block comment, behind a line comment, glued to a comment, to a quoted
  name, to a parameter behind a comment.
- Assignments inside a block: IF (to a source, to the target, to a parameter), a nested BEGIN, FOR, LOOP,
  CASE, WHILE, REPEAT, an EXCEPTION handler.
- A shadowing LET in a nested BEGIN (with and without a comment, with and without a type, and one for the
  target), and one inside IF.
- `NAME DEFAULT …`.
- A CALL in the body.
- A RETURN that reads another table.
- Three headers: reordered, SRC_DB moved to the RUN_ID slot, and one with a DEFAULT.

Further tests:
- Every keyword on the list is denied by name, plus a lower-case `begin` behind a comment.
- `:=` and LET are denied anywhere in code; the same words inside a string or a comment are allowed.
- RETURN of a string literal is accepted, including one with `''` and one with a block comment; an
  expression, a subquery and a variable are denied.
- A lower-case header is accepted; VARCHAR, four parameters and six parameters are denied.
- A top-level CALL with 'FINANCE' as RUN_ID is allowed: now that the header fixes what each argument means,
  RUN_ID is only a label. With 'FINANCE' as SRC_DB it is denied.

I re-ran the reviewer's probes after the fix. The rows still allowed are exactly the legitimate ones:
- the documented form, and the same with newlines;
- two statements whose strings merely contain `; LET …`;
- `let`/`varchar` in lower case, and `IDENTIFIER(:gl_ledger_src)`;
- the top-level CALL with 'FINANCE' as RUN_ID (above);
- the translator writing a `proc.sql`. That is a lane check on a write tool; the SQL is judged when a
  validator deploys it.

**M1 — RETURN in compile_check and the runner.** `parse_proc` no longer drops RETURN. `ProcInfo.returns`
holds each one as `Return(text, followed_by)`: the text without comments, and how many non-RETURN statements
come after it. `compile_check.return_form_errors` reports `c4:return_form` for:
- more than one RETURN;
- a RETURN that is not `RETURN '<string literal>'` (doubled or backslash-escaped quotes are allowed inside);
- a RETURN that is not the last statement. This one is my addition, in the same check: Snowflake stops at
  the RETURN, but the double would run what follows.

The old IDENTIFIER form hidden in a RETURN is caught. The test builds it from `OLD_SOURCE`, so the
IDENTIFIER-scan counts are unchanged.

**M2 — prose.** `wf_0001/seg_01/translation_notes.md` and `wf_0002/canned/docs/migration.md` now write the
LET arguments without colons. The new scan test
`tests/test_documented_identifier_form.py::test_no_markdown_names_a_c4_argument_with_a_colon` flags any
Markdown code span `:SRC_DB`, `:SRC_SCHEMA`, `:TGT_DB` or `:TGT_SCHEMA` in the scanned tree.

**M3 — a comment inside a LET.** `_parse_let` now strips only the comments IN FRONT of a LET. Any comment
left inside, between tokens or before the `;`, raises `LetError` ("a comment inside a LET is refused — write
it on its own line in front of the LET"). compile_check reports it as `c4:let_form`. The policy already
denied this case, because its LET regex is anchored on the text after the leading comments, so both now deny.

**(c) in compile_check.** `ProcInfo.param_declarations` keeps each declaration, whitespace-normalised.
Check 0 now also requires each one to be exactly `<NAME> STRING`, case-insensitive: no other type and no
DEFAULT. VARCHAR is accepted by neither the policy nor compile_check. Every canned SQL procedure and every
rendered Snowpark wrapper already declares `STRING`.

**Runner agreement (addition).** `proc_runner._SCRIPTING_KEYWORDS` now holds the policy's whole keyword list,
plus CALL, and any `:=` outside a LET is outside the subset. The double, compile_check and the policy
therefore refuse the same flat-body violations (a 19-case test). A closing `END …` is still skipped by
`_END_RE`. That is safe because every keyword that opens a block is refused.

**RED, before implementation.**
- node `policy.test.ts`: `# fail 5`. The first bypass shape, "assignment with a block comment before :=",
  came back ALLOW; so did BEGIN. The `:=`/LET, RETURN and header tests failed too.
- pytest runner and compile_check tests: `15 failed, 52 passed`. There was no LetError for a comment inside
  a LET, `ProcInfo` had no `returns` or `param_declarations`, `c4:return_form` did not exist, and headers
  with VARCHAR, DEFAULT or TEXT passed.
- The runner's scripting-form test: `19 failed`. Assignments reached DuckDB and the new keywords were not
  refused.
- The scan: `2 failed`. It found the merged old form in `snowflake-backend.md` and `test_snowflake_conn.py`,
  and the two prose colons.

**Verification, on the tree of `a8ee12f`.**
- pytest, whole suite: `1798 passed in 410.14s`, 0 skipped.
- node: `# tests 289 / # pass 289 / # fail 0 / # skipped 0`.
- tsc: exit 0.
- The reviewer's `probe_runner.py` byte-identity comparison: the base 39120ae runner on the base procedures
  against this runner on these procedures, with the same arguments. 30 procedures, `mismatches: 0`. The 29
  SQL procedures bind to byte-identical statements, and the Snowpark wrapper's header is identical.
- The same probe's compile_check section: every old-form, expression, colon, comment and RETURN shape is
  refused by name. The top-level assignment probe fails as a parse error, and is now also a runner refusal.
- Broken variants: the observer's output for all 18 SQL rows is byte-identical to the pre-C4V baseline
  (verdict, class, columns, stream, scope, count). No `broken.json` changed.
- Every sample, in a scratch root outside the repo: every segment's `compile_check.py` exits 0 OK, and its
  validator exits 0 with PASS on 4/4 sets, idempotent (25 lines). `validate_workflow.py` ran twice per
  sample:

  | Sample | Chain result |
  |---|---|
  | wf_0001 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0002 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0003 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0004 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0005 | n/a — parser-recovery sample, no translation |
  | wf_0006 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0007 | PASS / PASS, 4/4 sets, idempotent (dbt) |

**Concerns.**
- The §7 `procs/master.sql` bullet of `docs/reference/snowflake-backend.md` is stale. As merged from
  `feat/output-targets-phase2`, it still says the master's body has no `$$` delimiters, which has been
  untrue since the W2 follow-up. The ruling said that bullet was already fixed, but the merged text is not.
  I left it untouched because it is not mine.
- The SQL judge now holds a body the validator submits to a verb allow-list. A legitimate statement shape
  outside it would be denied. No canned or broken procedure has one, and neither does the validator's
  documented work. Denying is the fail-closed direction the policy's own header asks for.
- By ruling (b), `master.sql` (all CALLs) is now denied if anyone submits it through the SQL tool.

## Fix round 3 — external COPY/STAGE denied; _SRC/_TGT roles enforced; master bullet

Status: DONE. Merge commit `2826209`, fix commit `c225d88`. I squashed this round's RED and GREEN WIP
commits into the fix commit with `git reset --soft 2826209` and checked that the tree is unchanged.

**(4) Merge.** `git merge --no-edit feat/output-targets-phase2` brought in e1a5bdf, which has Task W4. It
merged cleanly with no conflicts, including W4's edits to the write-lane section of `policy.ts` and to
hooks, runner and stages. Merge commit `2826209`. The node suite passes on it; see Verification.

**(1) External locations (`orchestrator/policy.ts`).** This was a pre-existing gap. The object scanner
never reads a string literal as an object, so `COPY INTO 's3://…' FROM MIG_WORK.T` and a
sandbox-named stage created with an external `URL` passed every sandbox check.

The new function `externalLocationReason` runs inside `checkStatement`, straight after the
multiple-statements check. It therefore covers every role, top-level statements, procedure headers and
procedure body statements (a body's `COPY` and `CREATE` reach it through the body's verb allow-list).
Each denial reason reads `external-location: <what> may not move data outside the sandbox`. A statement
is denied if it:
- names credentials: `CREDENTIALS=`, `AWS_KEY_ID`, `AWS_SECRET_KEY`, `AWS_TOKEN`, `AZURE_SAS_TOKEN`,
  `PRIVATE_KEY` or `MASTER_KEY`;
- names a storage, API, notification, security or external-access integration, an external function or
  a network rule. This includes the `STORAGE_INTEGRATION`, `API_INTEGRATION` and
  `EXTERNAL_ACCESS_INTEGRATIONS` clauses;
- is a `COPY INTO` a string literal, or a `COPY … FROM` one. I deny ingress too: the reviewer's case 5
  was data coming IN from an attacker URL, and a URL is outside the sandbox either way;
- creates or alters a stage with `URL`, `STORAGE_INTEGRATION`, `CREDENTIALS` or `ENCRYPTION`;
- is a `GET` or `PUT` (these always involve a local `file://`);
- is a `LIST`/`LS` or `REMOVE`/`RM` of anything but a `@stage`.

The checks read the text with comments removed and strings blanked, so the same words inside a string
literal or a comment are only data (tested). An internal named stage under a sandbox schema is allowed
as before: `COPY INTO @MIG_WORK.STAGE FROM …`, `COPY INTO MIG_WORK.T FROM @MIG_WORK.STAGE FILES=(…)`,
`CREATE OR REPLACE STAGE MIG_WORK.STAGE` and `LIST @MIG_WORK.STAGE` all still pass, and B1 still
requires the stage to be sandbox-qualified.

The one thing the policy cannot see is a stage created by a human outside it with an external URL.
`POLICY.md` now lists that as a known limitation: the `MIGRATION_AGENT` role must hold no usage on any
external stage or integration.

Tests in `orchestrator/test/policy.test.ts`:
- "every move of data to or from an external location is denied". Every shape from the reviewer's
  `probe_exfil.ts` and the ruling's list gets a denial whose reason regex is
  `^external-location: .* may not move data outside the sandbox` — 22 shapes. The test adds two more:
  one inside a procedure body, and intake's catalog SELECT naming `AWS_KEY_ID`.
- "internal named stages under a sandbox schema stay allowed".

Re-running `probe_exfil.ts`, only one row is still allowed: `COPY INTO @MIG_WORK.MYSTAGE FROM …`. That
is the internal-stage case the ruling keeps, and the external stage that probe row depends on can no
longer be created through the policy.

**(2) Role lint.**
- `proc_runner.identifier_calls(statement)` returns, for each `IDENTIFIER(…)`, the code in front of it
  (strings and comments blanked) and its argument. `identifier_arguments` is now a thin wrapper around it.
- `compile_check.identifier_role(code_before)` classifies a reference:
  - **write:** `INTO` (INSERT / MERGE / COPY INTO), `UPDATE`, `DELETE FROM`, or `CREATE` or `TRUNCATE`
    followed only by table modifiers (`OR REPLACE`, `TRANSIENT`, `TEMPORARY`, `TABLE`, `IF NOT EXISTS`, …);
  - **read:** `FROM` (except after `DELETE`), `JOIN`, `USING`.
- `identifier_role_errors` reports `c4:identifier_role` for a `_SRC` name that is written to, or a
  `_TGT` name read as a source. It runs in the same early-refusal block as the other C4 checks.
- The policy has the same classifier (`identifierRole`, `contractRoleReason`). It runs at the start of
  `checkStatement` whenever contract variables are in scope, and uses the side recorded by the LET that
  declared the variable.
- The canned scan test now also asserts `identifier_role_errors == []` for every canned and broken SQL
  procedure. Every canned procedure still passes `compile_check`. wf_0003's PreSQL `DELETE FROM` and
  PostSQL `UPDATE` of the target are writes, and a test pins that both stay accepted.

Tests:
- `tests/test_compile_check_c4v.py`: 6 write shapes of `ITEMS_SRC` (CREATE … AS, INSERT INTO, MERGE
  INTO, UPDATE, DELETE FROM, TRUNCATE TABLE) and 3 read shapes of `ITEMS_OUT_TGT` (FROM, JOIN,
  MERGE … USING) are each refused with one `c4:identifier_role`. `DELETE FROM` and `UPDATE` of a target
  still compile.
- Policy: "a _SRC name is never written and a _TGT name never read" covers the same shapes. It also
  checks that the canned-style procedure and a target's `DELETE FROM` / `UPDATE` stay allowed.

**(3) Master bullet.** `docs/reference/snowflake-backend.md` §7 now says the master's body is
`$$`-delimited like every segment procedure's, as W2's follow-up made it. The fixture
`orchestrator/test/fixtures/master_wf_0003.sql` pins that. Creating the master has only run on the fake,
and the first real-account `deploy.py --execute` confirms it. Its body is only CALLs, which the local
runner refuses by design; `validate_workflow.py` runs the segments itself.

**Also:** the translator agent's C4 bullet names `c4:identifier_role`, and the `compile_check.py`
docstring describes it.

**RED, before implementation:**
- node `policy.test.ts`: `# fail 2`. These were the exfiltration test (the first shape, `COPY INTO`
  an s3 URL with credentials, came back ALLOW) and the role test (`CREATE OR REPLACE TABLE
  IDENTIFIER(:GL_LEDGER_SRC)` was allowed). The internal-stage test passed from the start; it is a guard.
- pytest `tests/test_compile_check_c4v.py`: `9 failed, 39 passed`. All nine role shapes compiled with
  no `c4:identifier_role`. The DELETE/UPDATE-of-a-target test passed from the start; it is a guard.

**Verification, on the tree of `c225d88`:**
- pytest, whole suite: `1809 passed in 401.43s`, 0 skipped.
- node: `# tests 311 / # pass 311 / # fail 0 / # skipped 0`. The count includes W4's tests from the merge.
- tsc: exit 0.
- The reviewer's `probe_runner.py` byte-identity comparison: `mismatches: 0` across all 30 procedures.
- Broken variants: the observer's output for all 18 SQL rows is byte-identical to the pre-C4V baseline.
- Every sample, in a scratch root outside the repo: every segment's `compile_check.py` exits 0 OK and
  its validator exits 0 with PASS on 4/4 sets, idempotent (25 lines). `validate_workflow.py` ran twice
  per sample:

  | Sample | Chain result |
  |---|---|
  | wf_0001 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0002 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0003 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0004 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0005 | n/a — parser-recovery sample, no translation |
  | wf_0006 | PASS / PASS, 4/4 sets, idempotent |
  | wf_0007 | PASS / PASS, 4/4 sets, idempotent (dbt) |
