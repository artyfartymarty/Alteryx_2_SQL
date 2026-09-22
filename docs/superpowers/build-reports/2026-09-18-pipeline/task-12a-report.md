# Task 12a report — Copilot agents, config.json, Snowflake ops DDL

Status: DONE. Both parts (agents/config.json and Snowflake DDL) are implemented, tested and
committed; see "Status: DONE" at the end of this file for the closing self-review.

## Environment note

The session's launch environment initially pointed at `.worktrees/task-15` (branch `wt/task-15`),
which has no `.superpowers/` directory (untracked, main-checkout-only) and is on the wrong branch.
The dispatch text and `.superpowers/sdd/2026-09-18-pipeline/implementer-rules.md` both say to work
from the main checkout `C:\Users\<user>\Desktop\Alteryx to Snowflake` on branch
`feat/pipeline-m0-m2`, so that is where all work below happened. A later environment update
confirmed the primary working directory had moved to the main checkout.

The session was also killed once by a usage limit before writing any files (only reading). The
coordinator resumed it; no work was lost since nothing had been written yet at that point.

## Part 1 — Agent definitions, config.json, tests/test_agents_config.py (DONE, committed)

### What I implemented

Nine agent definition files under `.github/agents/*.agent.md`, each copied verbatim from
`docs/spec/01-copilot-setup.md` Part A §4, with the amendments the phase-04 brief
(`.superpowers/sdd/2026-09-18-pipeline/task-12-brief.md`) calls for, plus amendments the
coordinator's mid-task update required to match facts from completed tasks (compile_check.py's
signature rejection, compare.py's verdict rule, the money-in-NUMBER rounding rule, and script exit
codes). Every amended or added paragraph carries a trailing `<!-- amended: plan Task 12 -->` HTML
comment so a reader can diff the file against the spec and immediately see what changed and why.

- `.github/agents/intake.agent.md` — frontmatter drops the spec's `# tools:` comment line (kept
  only `name`/`description`/`model`, per the brief). Procedure step 1 replaced with "Run
  `python scripts/intake_touchpoints.py <id>` and read `intake/touchpoints.json`; do not
  re-enumerate by hand." Two new steps appended: (8) ask via `ask_user` one touchpoint at a time
  with the top candidate as default when a human is present, otherwise
  `python scripts/intake_prompt.py <id> --no-interactive`; (9) every source/output needs a
  `logical` name (contract C6). The rest of the body (Inputs, Outputs, steps 2–7, Rules) is
  untouched spec text.
- `.github/agents/analyzer.agent.md` — Procedure step 3 (contract.json) gets one appended sentence
  stating contract C5's additions: `outputs[]`, `inputs[].logical`, `ordering.order_dependent_columns`.
  Everything else verbatim.
- `.github/agents/translator.agent.md` — the Rules bullet that used to say "the procedure takes
  SRC_DB and SRC_SCHEMA parameters..." is replaced with contract C4 stated in full (signature,
  IDENTIFIER-with-logical-name sourcing/targeting, literal `MIG_WORK.…` for upstream/`_OUT` tables,
  the `BEGIN`/linear-statements/`RETURN 'OK';`/`END;` body shape, no `LET`/`DECLARE`/loops/`IF`/
  `CALL`/`EXECUTE IMMEDIATE`). Two more amendments the coordinator's update required, added as new
  Rules bullets: (a) `compile_check.py` rejects the procedure outright if its name, parameter list
  or `EXECUTE AS CALLER` clause is wrong, and this project is always `EXECUTE AS CALLER` (never
  `OWNER`, since owner's-rights procedures cannot `ALTER SESSION`); (b) money arithmetic must be
  done in `NUMBER` (cast before multiply/divide, round the `NUMBER` expression), with the
  `ROUND(1.005::FLOAT, 2)` → `1.00` vs. the exact `1.01` example given verbatim. The closing
  "Done when compile_check.py..." line now says "exits 0" (was "passes") and gained a sentence
  distinguishing exit 1 (real compile error) from exit 2 (script couldn't run — stop and report).
- `.github/agents/reviewer.agent.md` — two blocking checks appended per the brief ("procedure
  matches the C4 signature and is a linear statement list"; "no literal reference to a mapped
  Snowflake table; sources and targets use IDENTIFIER with logical names"), plus two more the
  coordinator's update required: a blocking check spelling out the exact compile_check.py signature
  test (name/params/EXECUTE AS CALLER) so the reviewer catches it before compile_check ever runs,
  and an advisory check for FLOAT-rounded money arithmetic (same `1.005` example as the translator).
- `.github/agents/validator.agent.md` — the whole six-step Procedure section is replaced with the
  brief's condensed step ("Run `python scripts/validate_segment.py <id> seg_NN`; it deploys, runs
  every golden set, runs the first twice, and calls `compare.py`. Then read `validation.json` and
  add your interpretation under `interpretation`. Never edit numbers.") plus two more steps the
  coordinator's update required: compare.py's exact verdict rule (PASS = every check passed and
  nothing truncated; PASS_WITH_ACCEPTED_DIFF = nothing truncated and every failing check accounted
  for by an approved cluster; anything else FAIL — the validator interprets, never overrides), and
  the exit-code convention (0/1/2, with exit 2 meaning "stop and report", not a verdict).
- `.github/agents/fixer.agent.md`, `parser-recovery.agent.md`, `documenter.agent.md`,
  `cookbook-curator.agent.md` — copied verbatim, no amendments (not named in the brief's amendment
  list); frontmatter already had exactly `name`/`description`/`model`.
- `config.json` at the repo root — the exact JSON block from program spec 01 §A.1 (five built-in
  agents untouched, nine custom agents, `maxConcurrency: 4`, `maxDepth: 2`, `planModel`/
  `planEffortLevel`).

### Tests: `tests/test_agents_config.py`

24 tests. Frontmatter contract: nine files exist and nothing else; every file has exactly
`name`/`description`/`model`; `name` equals the file stem (`intake.agent.md` → `intake`, computed
by stripping the literal `.agent.md` suffix, not `Path.stem` which would leave `intake.agent`);
intake's frontmatter has no `tools` key/comment. Per-agent amendment checks (both the brief's and
the coordinator's): intake mentions `intake_touchpoints.py`/`touchpoints.json`/`ask_user`/
`intake_prompt.py --no-interactive`/`logical`/C6, and no longer says "Enumerate every external
touchpoint in dag.json" by hand; analyzer mentions `outputs[]`, `inputs[].logical`,
`ordering.order_dependent_columns`, C5; translator mentions `TGT_SCHEMA` and `compile_check.py`,
states C4 in full (checked token-by-token: signature, IDENTIFIER forms, `RETURN 'OK';`), no longer
contains the old "SRC_DB and SRC_SCHEMA parameters" sentence, documents the compile_check.py
signature rejection and `EXECUTE AS CALLER`, documents money-in-NUMBER rounding with the `1.005`
example, documents exit 0/2; validator mentions `validate_segment.py`, the interpretation/never-edit
wording, no longer contains the old deploy-with-snowflake-tool wording, documents the
PASS/PASS_WITH_ACCEPTED_DIFF/FAIL rule and exit 2; reviewer has both brief-mandated blocking checks
plus the compile_check-signature blocking check and the money-arithmetic advisory check. A
traceability test asserts the `<!-- amended: plan Task 12 -->` marker appears in exactly the five
amended agents and in none of the four untouched ones. `config.json` tests: parses; `maxConcurrency
== 4`; `maxDepth == 2`; agent set equals the nine custom + five built-in names; each custom agent's
`config.json` model matches its own frontmatter `model` (a consistency check the brief didn't ask
for but the spec's own table implies).

**Brief correction (frontmatter parsing).** The brief doesn't specify how `test_agents_config.py`
should parse frontmatter; I initially used `yaml.safe_load`. That fails on `documenter.agent.md`'s
spec description — "...for one workflow after validation passes: tool-to-CTE map, ..." — because
a colon-space inside an unquoted plain YAML scalar is ambiguous with a nested mapping
(`yaml.scanner.ScannerError: mapping values are not allowed here`). This text is copied verbatim
from the spec as instructed, so I did not reword it. Instead the test parses frontmatter as flat
`key: value` lines split on the *first* `": "` per line, not full YAML — which also matches the
spec's own description of the contract ("exact frontmatter keys your CLI version loads: name,
description, model as a string, optional tools" — three flat strings, not nested structures).

### TDD evidence

RED: the files did not exist yet; `pytest tests/test_agents_config.py` would have failed on
collection (`ModuleNotFoundError`/`FileNotFoundError`) before any content was written — not
captured verbatim since the test file and the content were written together in this pass, but the
directory listing before writing (`.github/agents` did not exist; `config.json` did not exist) is
recorded above under "Environment note" verification.

GREEN:
```
$ .venv/Scripts/python.exe -m pytest tests/test_agents_config.py -q
........................                                               [100%]
24 passed
```
(one intermediate RED from the YAML frontmatter bug above, fixed by switching to the flat-line
parser; re-ran green after the fix.)

### Commit

`402d759` — `wip: nine Copilot agent definitions, config.json, and their tests (Task 12a)`

Files: the 9 `.github/agents/*.agent.md` files, `config.json`, `tests/test_agents_config.py`.

## Part 2 — snowflake/*.sql and tests/test_snowflake_ddl.py (DONE, committed)

### What I implemented

Five DDL files under `snowflake/`, reproducing program spec `docs/spec/00-README.md` §10.2–§10.4
and §11.1. Every file opens with a header comment stating it has not been executed against any
Snowflake account and must be reviewed before use (per the honesty rule and the brief).

- `snowflake/01_ops_tables.sql` — `OPS.RUN_LOG` and `OPS.RECON_RESULTS`, reproduced verbatim from
  §10.2 (plus `CREATE SCHEMA IF NOT EXISTS OPS;` so the file is self-contained). No placeholders.
- `snowflake/02_shadow_table_template.sql` — the `CREATE TABLE ... LIKE ...` shadow-table pattern
  from §10.2/§10.5, genericized with `<WF_ID>`/`<TARGET>` placeholders (the spec's own example used
  the concrete `wf_0042`/`GL_SUMMARY`, which I did not carry over — see the "no production schema"
  test below).
- `snowflake/03_reconciliation_task_template.sql` — wraps §10.4's reconciliation INSERT in a
  `CREATE OR REPLACE TASK`, same two placeholders. The spec's own SQL ends the SELECT list in "...",
  which isn't valid SQL to commit as-is; I completed it with `NULL` for the four columns a simple
  count+hash check cannot populate (`ONLY_IN_ALTERYX`, `ONLY_IN_SNOWFLAKE`, `COLUMN_MISMATCHES`,
  `EXAMPLES`), with a comment explaining why and pointing at the fuller EXCEPT-based approach the
  spec describes in prose but doesn't write out.
- `snowflake/04_alerts.sql` — `CREATE OR REPLACE ALERT ... IF (EXISTS (SELECT 1 FROM
  OPS.RECON_RESULTS WHERE VERDICT = 'FAIL')) THEN CALL SYSTEM$SEND_EMAIL(...)`, per §10.4's
  "`CREATE ALERT` on `RECON_RESULTS.VERDICT = 'FAIL'` → notification integration." No placeholders
  (this alert is program-wide, not per-workflow). Commented that the condition as written re-fires
  on old failures every tick and should be narrowed (or driven off a stream) before real use.
- `snowflake/05_roles.sql` — the three roles from §11.1. `MIGRATION_AGENT`: `USAGE` on
  `MIG_WORK`/`MIG_GOLDEN`, the table/view/procedure privileges the validator and
  `gen_source_views.py` need inside them, and `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` for
  metadata-only `INFORMATION_SCHEMA` access (commented as a design choice to verify on the real
  account, since the spec names no concrete mechanism); nothing named on any production schema.
  `MIGRATION_CI`: deploy privileges on `MIG_WORK`/`MIG_GOLDEN`/`OPS`/`ANALYTICS`. `MIGRATION_RUN`:
  execution-adjacent privileges on `OPS`/`ANALYTICS`. Per-workflow production-schema grants (a
  migrated procedure's own target schema, cutover's `SWAP WITH`) are documented as travelling with
  that workflow's PR rather than enumerated here, since no concrete production schema name exists
  to write down safely — spelled out in comments at both role sections.

### Tests: `tests/test_snowflake_ddl.py`

13 tests. File inventory (exactly the 5 expected files, each non-empty, each with the
not-executed/review header). `CREATE TABLE` parsing: extracts `CREATE TABLE` statements from each
file (a comment-aware splitter, see "Brief correction" below) and parses each with
`sqlglot.parse_one(stmt, read="snowflake")`, asserting a real `exp.Create` came back and not a
`Command` fallback; exactly 3 such statements exist across all 5 files (`RUN_LOG`, `RECON_RESULTS`,
the shadow table). A follow-up test parses `01_ops_tables.sql`'s two tables and checks a few
expected columns landed in each. Tasks/alerts/grants: confirms none of 03/04/05 contain a
`CREATE TABLE` statement (so this suite never hands sqlglot something it isn't asked to parse) and
checks their shape by substring instead (`CREATE OR REPLACE TASK`, `CREATE OR REPLACE ALERT`,
`GRANT` count). Placeholders: `<WF_ID>`/`<TARGET>` appear only in the two template files, and
neither template hardcodes the spec's own worked example (`wf_0042`, `GL_SUMMARY`) — a test I added
beyond the brief's own wording, since genericizing the example *is* the point of a template.
Production-schema boundary: a per-statement scanner (see below) asserts every schema/database
qualifier used as an object location is in `{MIG_WORK, MIG_GOLDEN, OPS, ANALYTICS, CURATED,
SNOWFLAKE, INFORMATION_SCHEMA}`, with a sanity check that it isn't so strict it also rejects
`MIG_WORK`/`MIG_GOLDEN` themselves. Roles: all three `CREATE ROLE` statements exist;
`MIGRATION_AGENT`'s own grant lines mention `MIG_WORK`/`MIG_GOLDEN`/`INFORMATION_SCHEMA`-or-`SNOWFLAKE`
and never `ANALYTICS`/`MIGRATION_CI`/`MIGRATION_RUN`; `MIGRATION_CI`/`MIGRATION_RUN` are described
as deploying/executing in production and `MIGRATION_RUN` has `USAGE` on `ANALYTICS`.

**Brief correction (statement splitting for the DDL test).** Two problems came up writing this
test, both fixed in the test itself, not in the DDL content (except where noted):

1. My first attempt split each file on literal `;` characters to isolate statements. Several of my
   own header comments contain a semicolon inside prose ("... real Snowflake; review and adapt...
   "), which split one statement into two and produced garbage "statements" starting mid-sentence.
   Fixed by stripping `-- ...` line comments (outside single-quoted strings) before splitting on
   `;`, rather than trying to filter comment lines out *after* an already-wrong split.
2. A naive "keyword TABLE/VIEW/PROCEDURE/TASK/ALERT followed by an identifier" regex for the
   production-schema scanner misfired on `GRANT CREATE PROCEDURE, CREATE TABLE, CREATE VIEW ON
   SCHEMA MIG_WORK ...`: here "CREATE VIEW" is the last item in a privilege list, immediately
   followed by "ON", which the regex mistook for "CREATE VIEW `<name>`". Fixed by deciding
   per-statement whether those five keywords are object-introducing at all: only when the
   *statement itself* starts with `CREATE` (a real `CREATE TABLE/TASK/ALERT/VIEW/PROCEDURE ...`),
   never inside a `GRANT` statement, where they are privilege nouns. `SCHEMA`/`DATABASE`/`FROM`/
   `JOIN`/`INTO`/`LIKE` remain unconditional since none of my files use them ambiguously.

The brief's Step 1 description ("mentions no production schema other than OPS/ANALYTICS
placeholders") doesn't specify an implementation; I chose a keyword-anchored scanner over a fixed
blocklist because it verifies the actual invariant (every schema-qualified reference is one of the
allowed ones) rather than just the absence of a few specific strings I happened to think of.

Placeholder parsing note: `<WF_ID>`/`<TARGET>` are not valid SQL syntax
(`sqlglot.parse_one` chokes on the bare `<`/`>` characters), so `02_shadow_table_template.sql`'s
`CREATE TABLE` statement is parsed after substituting both tokens for dummy valid identifiers
(`WF_ID_PLACEHOLDER`/`TARGET_PLACEHOLDER`); the committed file itself keeps the human-readable
angle-bracket form the brief specifies.

### TDD evidence

RED: `snowflake/` did not exist before this part of the task; the same reasoning as Part 1 applies
(no re-run captured since the test and content were developed together), confirmed by directory
listing before writing (`snowflake/` absent).

GREEN:
```
$ .venv/Scripts/python.exe -m pytest tests/test_snowflake_ddl.py -q
.............
13 passed
```
Two intermediate REDs during development, both from test bugs (not DDL bugs), fixed as described
above under "Brief correction": (a) `AssertionError: assert {''} == {'RECON_RESULTS', 'RUN_LOG'}`
from using `tree.this.name` instead of `tree.find(exp.Table).name` on a `CREATE TABLE (...)`
statement (its `this` is a `Schema` node wrapping the `Table`, not the `Table` itself); (b) the two
splitter bugs above. Re-ran green after each fix.

### Full-suite verification

```
$ .venv/Scripts/python.exe -m pytest
550 passed in 13.69s
```
Output is pristine (dots only, no warnings) across the whole repo, not just the two new files.

### Commit

`23620ea` — `wip: Snowflake ops DDL (roles, ops tables, shadow/reconciliation templates, alert) and their tests (Task 12a)`

Files: the 5 `snowflake/*.sql` files, `tests/test_snowflake_ddl.py`.

## Self-review

- Completeness: both brief-scoped deliverables (agents+config, DDL) are implemented with their
  named tests; cookbook and its tests were correctly left untouched (out of scope for 12a).
- The coordinator's mid-task update (compile_check.py signature rejection, compare.py verdict rule,
  money-in-NUMBER rounding, script exit codes) is folded into translator/reviewer/validator, each
  new paragraph marked `<!-- amended: plan Task 12 -->` alongside the brief's own amendments, and
  covered by new tests (not just prose) in `test_agents_config.py`.
- Discipline: I did not touch `cookbook/`, `tests/cookbook_examples/`, or write
  `tests/test_cookbook_examples.py`, per the explicit scope boundary. I did not touch any other
  task's files; `git status` before each commit showed only my own new files.
- The `05_roles.sql` design choice to document (rather than hard-code) per-workflow production
  grants, and the `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` grant for metadata access, are
  judgment calls where the spec is directional but not fully mechanical; both are called out in
  file comments as needing verification against a real account, consistent with the honesty rule.

## Concerns

- `05_roles.sql`'s `GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE MIGRATION_AGENT` is my
  own design choice for "read access to INFORMATION_SCHEMA" (the spec/brief name the requirement
  but not a mechanism); I flagged in-file that this should be verified against a real account
  before use, since Snowflake's exact metadata-visibility semantics for a role with no direct
  object grants weren't something I could verify locally.
- `03_reconciliation_task_template.sql` leaves `ONLY_IN_ALTERYX`/`ONLY_IN_SNOWFLAKE`/
  `COLUMN_MISMATCHES`/`EXAMPLES` as `NULL` rather than implementing the fuller EXCEPT-based
  row/column diff the spec describes in prose ("EXCEPT both ways on keys; column-level mismatch
  counts") but doesn't write out as SQL; implementing that would have been inventing SQL semantics
  the spec doesn't specify, which the honesty rule and YAGNI both argue against, so I left it as a
  documented gap instead.

## Status: DONE

## Fix round 1

Reviewer findings: two Important, two Minor, all ruled in. Fixed in the main checkout
(`C:\Users\<user>\Desktop\Alteryx to Snowflake`, branch `feat/pipeline-m0-m2`; HEAD had moved on
with unrelated merges since the initial report — staged only my own files).

### IMPORTANT 1 — analyzer's C5 amendment omitted `inputs[].stream`

`.github/agents/analyzer.agent.md:24-29` restated contract C5's contract.json additions but
dropped `inputs[].stream` (for inputs fed by an upstream segment: which golden intermediate stream
feeds them) — load-bearing since the validation driver loads a segment-chained input by its
`stream`. Re-read C5 verbatim from `docs/superpowers/plans/2026-09-18-pipeline/00-index.md:63` and
rewrote the paragraph to state all four additions accurately: `outputs[]` (with its full per-entry
shape and the `output == outputs[0]` equivalence), `inputs[].logical`, `inputs[].stream`, and
`ordering.order_dependent_columns`.

`test_analyzer_amendments` (`tests/test_agents_config.py`) now also asserts `"inputs[].stream" in
body`, with a docstring explaining why that field matters.

TDD:
```
$ git show HEAD:.github/agents/analyzer.agent.md > old_analyzer_check.md
$ .venv/Scripts/python.exe -c "print('inputs[].stream' in open('old_analyzer_check.md').read())"
False   # RED: confirms the field was genuinely missing before this fix
$ .venv/Scripts/python.exe -m pytest tests/test_agents_config.py -q
........................                                               [100%]
24 passed   # GREEN after the fix + extended assertion
```

### IMPORTANT 2 — `MIGRATION_AGENT` over-granted via `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE`

`snowflake/05_roles.sql` granted `MIGRATION_AGENT` `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` to
serve intake's `INFORMATION_SCHEMA.COLUMNS` lookups. That grant exposes all of
`SNOWFLAKE.ACCOUNT_USAGE` account-wide (every role's query text, login history, object metadata) to
a role reachable from agent/MCP sessions — not least privilege (program spec §11.1 principle 5),
and unnecessary: a database's `INFORMATION_SCHEMA` is visible to any role with `USAGE` on that
database already, and validator's "credits from QUERY_HISTORY" can come from
`TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION())`, which needs no extra grant.

Per the controller's ruling I:
- Removed the `IMPORTED PRIVILEGES` grant and its rationale comment entirely.
- Added explicit `GRANT USAGE ON DATABASE <DB>` and `GRANT USAGE ON WAREHOUSE MIGRATION_WH` to all
  three roles (a real gap in the original file — none of the three roles could have run anything
  without database- and warehouse-level `USAGE` in addition to the schema-level grants they already
  had; `<DB>` is a new placeholder, introduced and explained in the file's header, standing in for
  this program's one sandbox/ops database — distinct from the `<WF_ID>`/`<TARGET>` placeholders,
  which stay confined to the two per-workflow template files).
- Documented the recommended alternative as a fully commented block: a separate job (human- or
  `MIGRATION_CI`-run, never an agent session) publishes a sanitized column catalog — structure only,
  no row data — into `MIG_WORK.CATALOG_COLUMNS`, which `MIGRATION_AGENT` already reads via its
  existing `MIG_WORK` grants; mirrors the repo's local stand-in `catalog/columns.csv`. Gave the
  `INSERT ... SELECT ... FROM <SOURCE_DB>.INFORMATION_SCHEMA.COLUMNS` job as a commented template
  with a `<SOURCE_DB>` placeholder (a third placeholder, confined to this one commented block, never
  uncommented). Noted `QUERY_HISTORY_BY_SESSION()` for the validator's credits, needing no grant.
- Did not otherwise broaden `MIGRATION_AGENT`'s `MIG_WORK`/`MIG_GOLDEN` table grants: they already
  had no `TRUNCATE`/`DROP`, and the existing `MIG_GOLDEN` = read-only / `MIG_WORK` = read-write split
  already matches contract C3 (sources read from `MIG_GOLDEN`, test targets written to `MIG_WORK`)
  more precisely than treating both schemas identically would, so I kept it and added a comment
  explaining why.

Added two tests to `tests/test_snowflake_ddl.py`:
- `test_imported_privileges_and_account_usage_do_not_appear_uncommented` — scans every file's
  comment-stripped statements for `IMPORTED PRIVILEGES` / `ACCOUNT_USAGE`; both may still appear in
  a comment (the documented alternative explains why they're avoided) but never in an executable
  statement.
- `test_migration_agent_grants_name_only_sandbox_databases_and_schemas` — every uncommented
  `GRANT ... TO ROLE MIGRATION_AGENT` may only reference the sandbox schemas or the `<DB>`
  placeholder, never a production database or the `<SOURCE_DB>` placeholder (which is reserved for
  the commented, non-`MIGRATION_AGENT` catalog job).

This also required widening `_schema_refs`' capture regex to allow a reference to *start* with a
placeholder (`DATABASE <DB>`, not only carry one as a later dotted segment like
`ANALYTICS.CURATED.<TARGET>`), and tightening `ALLOWED_SCHEMAS` by removing `SNOWFLAKE` and
`INFORMATION_SCHEMA` now that fixing this finding means neither appears in an executable statement
anywhere in `snowflake/` any more.

TDD:
```
$ git show HEAD:snowflake/05_roles.sql > old_roles_check.sql
$ .venv/Scripts/python.exe -c "
text = open('old_roles_check.sql').read()
print('IMPORTED PRIVILEGES present:', 'IMPORTED PRIVILEGES' in text)
print('ACCOUNT_USAGE present:', 'ACCOUNT_USAGE' in text)
"
IMPORTED PRIVILEGES present: True
ACCOUNT_USAGE present: True   # RED: confirms the over-grant was genuinely present before this fix
$ .venv/Scripts/python.exe -m pytest tests/test_snowflake_ddl.py -v -k "imported_privileges or migration_agent_grants_name_only"
tests\test_snowflake_ddl.py ..                                          [100%]
2 passed   # GREEN after the fix
$ .venv/Scripts/python.exe -m pytest tests/test_snowflake_ddl.py -q
...............                                                        [100%]
15 passed   # full DDL suite (13 original + 2 new), still pristine
```

### MINOR 1 — stale `EXECUTE AS OWNER` comment in `05_roles.sql`

The `MIGRATION_RUN` section said procedures run "`EXECUTE AS OWNER` unless the workflow needs
caller context", copied from the spec's general text but inconsistent with this project's own
contract C4 (`mappings/global.yaml` `program.execute_as: CALLER`; `scripts/compile_check.py`
rejects anything but `EXECUTE AS CALLER`). Rewrote the comment to state procedures here are always
`EXECUTE AS CALLER`, and spelled out the consequence for grants: since the procedure runs with the
*caller's* rights, `MIGRATION_RUN` itself must hold the privileges on every source/target object a
procedure touches (it cannot inherit them from a procedure owner), which is why this role's grants
include per-workflow production table/procedure access documented alongside `MIGRATION_CI`'s.
`snowflake/05_roles.sql:100-110`. Kept the "verify on your Snowflake account" caveat.

### MINOR 2 — `04_alerts.sql` promised diff detail the template never populates

The alert's email body said "See `OPS.RECON_RESULTS` for diff examples", but
`03_reconciliation_task_template.sql` always writes `NULL` for `EXAMPLES`,
`ONLY_IN_ALTERYX`/`ONLY_IN_SNOWFLAKE`/`COLUMN_MISMATCHES` (a decision the reviewer explicitly
agreed with in the first round, but the alert text hadn't been updated to match). Reworded the
message to promise only what the template actually populates — verdict and the row-count delta
from the count+hash comparison — and pointed at re-running `scripts/compare.py` against the live
table and its `__SHADOW` pair for row-level detail. `snowflake/04_alerts.sql:26-29`.

### Verification

```
$ .venv/Scripts/python.exe -m pytest tests/test_agents_config.py tests/test_snowflake_ddl.py -v
tests\test_agents_config.py ........................                   [ 61%]
tests\test_snowflake_ddl.py ...............                            [100%]
39 passed
$ .venv/Scripts/python.exe -m pytest
........................................................................ [ 11%]
...
608 passed in 21.51s
```
Full repo suite is green and pristine (608 tests — the difference from Part 2's 550 reflects other
agents' unrelated merges since then, not anything in this change).

### Files changed (fix round 1)

`.github/agents/analyzer.agent.md`, `snowflake/04_alerts.sql`, `snowflake/05_roles.sql`,
`tests/test_agents_config.py`, `tests/test_snowflake_ddl.py`.

### Status after fix round 1: DONE
