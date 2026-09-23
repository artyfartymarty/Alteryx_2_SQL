# Task D report — orchestrator dbt dispatch, mock replay, policy, agents, reference docs

Worktree `.worktrees/p2-D`, branch `wt/p2-D`, base `6282cce` (A, B, W3, P3 merged). Fresh start (the
earlier stopped dispatch had written nothing).

Commit: **`a8e7393`** feat: output_kind dbt — one translate iteration for the whole workflow, dbt lanes
and denies, mock replay of canned/dbt, agents and reference docs. (Three `wip:` commits made along the
way — `e12358e` orchestrator, `f96b008` agents, `0a41510` docs — were squashed into it with
`git reset --soft 6282cce`; the tree is identical, the wip SHAs are no longer on the branch.)

## What was built

**Orchestrator (`orchestrator/`)**

- `types.ts` — `export interface AgentCtx { segment?; iteration?; dbt? }`; `AgentRunner.run(role, wf, task,
  ctx?: AgentCtx)`. `OutputKind`'s doc comment no longer says dbt is "acted on in phase 2".
- `stages.ts` — `runAgent`'s ctx is `AgentCtx`. `stageTranslate` branches right after the no-segments
  check: `if (m.output_kind === "dbt") return await translateDbt(env, m, order)`. New, as the brief's
  code with the deviations listed below: `dbtReadme(wfId)` (exported), `dbtModelName(output)`
  (exported, mirror of `dbt_project.model_name`), `failingModels`, `migrateDbt` (translator/fixer →
  `compile_check.py <wf> --target dbt` → reviewer → validator, every agent `{ iteration, dbt: true }` and
  no segment, bounded by `maxFixIterations`), `translateDbt` (recorded-NEEDS_HUMAN park, all-PASS
  resume skip, per-segment statuses from the reports, `dbt: <reason>` park, then `rm procs/master.sql`
  and write `procs/README.md = dbtReadme(id)`, VALIDATED). `stageDocument`'s task is kind-aware
  (dbt: `dbt/translation_notes.md` + "Its Deployment section is the dbt run command in
  workflows/<id>/procs/README.md"; procedures: "+ Its Deployment section names procs/master.sql and every
  segments/<seg>/proc.sql"). The procedures path is untouched: the pinned phase-1 test "a SQL
  workflow's script calls are exactly what they were, plus the one target_check.py call" passes
  unchanged.
- `runner.ts` — `MockRunner.run(…, ctx?: AgentCtx)`; `replay` routes `ctx.dbt` first (translator/fixer →
  `replayDbt`; reviewer → `canned/review.json` to `dbt/review.json`; validator →
  `runValidator(wf, "scripts/validate_dbt.py", [wf.id])`; other roles fall through unchanged).
  `replayDbt` copies every file under `canned/dbt/` to `workflows/<wf>/dbt/<same relative path>`,
  lays the first `broken_sql/dbt/**` file in name order over it on `fix-loop:dbt` (translator iteration
  0) / `never-fixed:dbt` (translator 0 and every fixer), and appends a `## iteration N — dbt project`
  entry to `dbt/fix_log.md` on every fixer turn. `replayValidator`'s spawn-and-classify body is now
  `runValidator(wf, script, args)` (exit 2 → `{ok: false, error: "error"}`, 0/1 → OK), reused by both.
  `CopilotRunner.run` passes `ctx?.dbt` to `hooksFor`.
- `hooks.ts` — `hooksFor(role, wf, env, segment?, dbt?)`; the policy gets `{ sandboxDatabases,
  dbtProject: dbt }`; every audit line carries `"dbt": true` when the scope is on (no key otherwise).
- `policy.ts` — `PolicyOptions.dbtProject`; `writeLanes(role, id, segment, dbtProject)` swaps in the DV5
  lanes (translator/fixer: `dbt/{dbt_project.yml,profiles.yml,readme.md,translation_notes.md,fix_log.md}`
  and `dbt/models/**`; reviewer: `dbt/review.json`; validator: `segments/<any>/validation*.json`; every
  other role keeps its own lanes); `decide` threads `options?.dbtProject` into `decideWrite`;
  `ROLE_SCRIPTS.validator` gains `scripts/validate_dbt.py`; `export const DBT_EXECUTABLE =
  /^(?:.*\/)?dbt(?:\.exe)?$/` checked on the normalized first token right after the listing/git checks,
  plus `python|py|.venv… -m dbt[.…]` refused by the same rule in the `-m` branch; reason "dbt is run only
  by scripts/compile_check.py and scripts/validate_dbt.py, never by an agent".

**Tests (`orchestrator/test/`)** — `fakes.ts`: dbt scenarios (`isDbtScenario`: `dbt` or `<name>:dbt`)
seed `canned/dbt/{dbt_project.yml, profiles.yml, README.md, translation_notes.md, models/sources.yml,
models/schema.yml, models/wf0001_seg_01_out.sql, models/orders_out.sql}` (exported `CANNED_DBT`),
`canned/review.json` (PASS) and `broken_sql/dbt/models/orders_out.sql` = `BROKEN_DBT`; contracts gain
outputs (seg_01 its work stream `MIG_WORK.WF0001_SEG_01_OUT`, seg_02 the target `ORDERS_OUT`, stream
`2_T`, tool `5`); fake `compile_check.py … --target dbt` writes `dbt/compile_check.json` and returns
1/2/0 for `compile-fails:dbt`/`compile-crashes:dbt`/else (2 with no report, and 2 when there is no
project); fake `validate_dbt.py` writes every segment's `validation.json` with `"target": "dbt"`;
`recording.run` records `ctx?.dbt`. `stages.test.ts` +18, `policy.test.ts` +9, `runner.test.ts` +7
(all brief tests by their exact names, plus the extras listed under "Tests beyond the brief").

**Agents** (each amendment marked `<!-- amended: output targets phase 2 -->`):
- `translator.agent.md` — an Inputs bullet pointing at `cookbook/snowpark.md` / `cookbook/dbt.md`, and
  a trailing `## dbt projects (…"output_kind": "dbt")` section: called ONCE per workflow with no
  segment; layout; `profiles.yml` = the template, carried IN the section byte for byte (inserted from
  `dbt_project.PROFILES_TEMPLATE` by script, pinned by a test); sources; one `materialized='table'`
  model per work stream; one model per final target with `alias='<LOGICAL>'` in upper case and the
  config per write mode (overwrite/append/merge + `unique_key` = the mapping's keys); no
  `is_incremental()` filter; hooks against `{{ this }}`; schema.yml; `-- tool <id>:`; `source()`/`ref()`
  only; the closed Jinja list with every refused construct named; the orphan rule; all eleven
  `dbt:<check>` names; the narrowed lane; "Never run `dbt` yourself"; honesty line; done criterion
  `.venv/Scripts/python.exe scripts/compile_check.py <id> --target dbt` exits 0.
- `reviewer.agent.md` — `## Blocking checks for a dbt project`, writes `workflows/<id>/dbt/review.json`.
- `validator.agent.md` — step 5: `.venv/Scripts/python.exe scripts/validate_dbt.py <id>` once for the
  whole workflow, every segment's report with `"target": "dbt"`, exit codes, the dbt-duckdb caveats.
- `fixer.agent.md` — `## dbt projects`: the task's `Failing models:` / quoted compile failure, smallest
  change, **Never edit `profiles.yml`**, **Never run `dbt`**, log to `dbt/fix_log.md`.
- `documenter.agent.md` — `## Deployment` per output kind (procedures: `procs/master.sql` + each
  `segments/<seg>/proc.sql`, Snowpark wrapper, `RUNTIME_VERSION`/`PACKAGES`; dbt: the command in
  `procs/README.md`, `SNOWFLAKE_*`, `dbt-snowflake` not installed). The pinned spec sentence is kept.
- `.github/copilot-instructions.md` — the "phase 1 records but does not yet build" sentence replaced.

**Docs** — `docs/reference/output-targets.md`: honesty note names dbt-duckdb; §3.3 rewritten ("a dbt
project": layout table incl. who writes each file, the narrowed lane, `PROFILES_TEMPLATE` verbatim,
naming/alias with the S3 error text and the write-mode config table, the closed Jinja list, sandboxes
and `--vars` with the S2 facts (DV1, DV3), the eleven checks as a table, what `validate_dbt.py` does
incl. DV7, logs and sandboxes); §4 gains the dbt column, a "one loop" paragraph with the full
`dbt: <reason>` table, dbt permissions and mock replay; §5 "A dbt workflow" deployment steps; §6 the
dbt-duckdb bullet (types/case folding, MERGE `UPDATE BY NAME`/`INSERT BY NAME`, hooks on DuckDB,
schema.yml tests not executed, `snowflake` output never ran) and the idempotency bullet extended to
`validate_dbt.py`; §7 rows. `README.md` §1 "Three output targets": the dbt row is built (worked example
`workflows/wf_0007/`, §6), the phase-1 paragraph replaced, "None of the three validators…".

## Brief corrections

1. **`writeLanes`' `README\.md` could never match.** `normalizeToolPath` returns `normalized.toLowerCase()`
   (policy.ts, "result lower-cased and root-relative"), so a lane literal in upper case is dead. The
   brief's own test ("the translator and fixer may write … `dbt/README.md` → allow") was RED against the
   brief's regex after the rest was implemented: `{"permissionDecision":"deny","permissionDecisionReason":
   "translator may not write workflows/wf_0007/dbt/readme.md"}`. Smallest correction: the literal is
   `readme\.md` (commented in place). Nothing else in the lanes has upper case.
2. **Eleven checks, not nine.** The brief's doc step says "the nine `compile_check --target dbt`
   checks"; Task A's fix round 1 added `dbt:model_orphan` and `dbt:model_jinja` (spec §5.1 already says
   eleven). The docs list eleven, and `test_translator_names_every_dbt_check_compile_check_runs` derives
   the list from `scripts/compile_check.py`'s own source and asserts it has 11 entries.
3. **The fake `validate_dbt.py` FAILs only the segment that owns the broken model**, not "every segment".
   The brief's fake is FAIL-for-all; I gave seg_01 its own work output (so the canned
   `wf0001_seg_01_out.sql` is a contract output, as compile_check's orphan rule requires of a real
   project), and with a FAIL-for-all fake test 3's `Failing models: orders_out (seg_02)` would then read
   `wf0001_seg_01_out (seg_01), orders_out (seg_02)`. Owner-only failure is also what the real script
   does when the last model alone is wrong (Task B report: a wrong model fails its own segment and the
   ones downstream of it), and it makes test 4's "every segment_status is NEEDS_HUMAN except PASSing
   segments" non-vacuous: `{seg_01: "PASS", seg_02: "NEEDS_HUMAN"}`.

## Design decisions not spelled out in the brief

- **A verdict only counts for the project as it now stands.** The brief's `migrateDbt` keeps `verdicts`
  across iterations, so a project validated in iteration 0 (seg_01 PASS) whose fixer then broke
  compilation would park with seg_01 still `PASS` although nothing validated the project that is
  parked. `verdicts` is reset at the top of every iteration (every agent turn may change the project).
  Test: "a PASS is not kept for a segment once a later iteration changed the project and never
  re-validated it". Same for an agent failure mid-loop (every segment `NEEDS_HUMAN`).
- **`needs_human` wins over the verdict per segment** (task-15-int ruling 4, as `migrateSegment` does):
  a report `PASS_WITH_ACCEPTED_DIFF` + `needs_human: true` records that segment `NEEDS_HUMAN`, not its
  verdict. New fake scenario `needs-human-pass:dbt` and a test.
- **`python -m dbt…` gets the dbt reason too** (the brief only required a deny); `DBT_MODULE =
  /^dbt(\.|$)/` in the `-m` branch.
- **The translator agent carries `PROFILES_TEMPLATE` itself** (the dispatch: teach the fixed template
  "exactly as compile_check enforces"), rather than only pointing at the reference page — a translator
  can then write the file byte for byte without another read. Pinned against the Python constant.
- **Jinja pin runs compile_check's own check.** `test_translator_teaches_the_closed_jinja_allow_list_…`
  names 8 allowed and 7 refused constructs; each must appear in code ticks in the translator's
  `- **Model Jinja` bullet AND be accepted/refused by `compile_check._dbt_jinja_errors` over a one-model
  temp project (the exact function `compile_check_dbt` runs, without `dbt parse`).
- Documenter task: the Deployment sentence sits before "Restate only what those artifacts say." in
  both kinds (the brief's "existing text plus" is order-ambiguous; consistent placement read better).
- Log lines added in `migrateDbt`/`translateDbt` mirroring `migrateSegment`'s (`compile_check.py
  --target dbt exited 2 — …`, `dbt compile check failed on iteration N — …`, `review BLOCK on the dbt
  project …`, `validate_dbt says a diff is not in the project …`).
- Agent frontmatter `description`s were left as they were (the dbt behaviour is in the marked sections).

## Tests beyond the brief

stages: stale `master.sql` removed; reviewer BLOCK ×3 → `dbt: reviewer BLOCK after 3 iterations`, no
validator; an agent failure → `dbt: translator denied`, no compile check, every segment NEEDS_HUMAN; a
recorded NEEDS_HUMAN segment parks with `dbt: needs_human (recorded)` and no agent; `--from-stage
translate` re-translates the whole project in dbt scope; the PASS-not-kept test; needs_human over PASS;
a procedures workflow never gets a dbt flag or a dbt script. policy: dbt scope leaves every other role's
lanes alone; a file merely named dbt stays readable; traversal out of `dbt/models/`; the validator's
`--project ../../elsewhere` refused; eleven dbt spellings × 8 roles × with/without scope. runner:
never-fixed:dbt + name-order choice between two variants; a missing canned project/review is
`missing-output`, never invented; the audit line carries `"dbt": true` (and no key without the scope).
agents config: cookbook pointers; template byte for byte; eleven check names; Jinja allow/refuse against
compile_check; orphan + alias; the reference page (template verbatim, every check name, key facts, the
phase-1 dbt sentence gone); README section.

## TDD evidence

**Node RED** (tests + fakes written, no implementation):
```
$ fnm exec --using=22 npm.cmd test
# tests 131  # pass 118  # fail 13
# SyntaxError: The requested module '../stages.ts' does not provide an export named 'dbtModelName'
not ok 8 - orchestrator\test\stages.test.ts        (the whole stages suite: dbtReadme/dbtModelName missing)
not ok 98 - the translator and fixer may write the dbt project files and models
    {"permissionDecision":"deny","permissionDecisionReason":"translator may only write its own segment's files, and no segment is in context"}
not ok 99..101, 104 - the reviewer/validator dbt lanes, validate_dbt.py for the validator
not ok 105 - no role may run dbt, however it is spelled
    'intake may not run this command: dbt run --project-dir workflows/wf_0007/dbt'   (denied, but not with the dbt reason)
not ok 123..127, 129 - MockRunner dbt replay/broken variant/reviewer/validator; CopilotRunner dbt scope
```
Expected: nothing dbt-scoped existed. Four new tests passed at RED by design — they pin unchanged or
fail-closed behaviour: policy "without dbt scope and without a segment the translator may write
nothing", "dbt scope changes no other role's lanes", "a file merely NAMED dbt is still readable…", and
runner "MockRunner in dbt scope reports a missing canned project…". The stages suite could only be seen
RED as a whole (the module failed to import); some of its tests (e.g. "a procedures workflow never gets
dbt scope") guard unchanged behaviour and would pass on the old code.

**Node GREEN** after the implementation: first run 212/213 — the one failure was brief correction 1
(`translator may not write workflows/wf_0007/dbt/readme.md`); after lower-casing the literal:
```
# tests 213  # pass 213  # fail 0  # skipped 0
tsc --noEmit -p .  → clean
```

**Python RED** (`tests/test_agents_config.py`, new tests written, no agent/doc edits): `14 failed, 40
passed` — every new test failed (markers absent, sections absent, reference page/README still phase-1).
**GREEN**: `54 passed`.

## Verification (final, on the committed tree)

```
$ fnm exec --using=22 npm.cmd test
# tests 213  # pass 213  # fail 0  # skipped 0        (baseline 179; +34: stages 18, policy 9, runner 7)
$ fnm exec --using=22 node.exe <main checkout>/node_modules/typescript/bin/tsc --noEmit -p .
(clean)
$ .venv/Scripts/python.exe -m pytest tests/test_agents_config.py
54 passed                                             (40 before; +14)
$ .venv/Scripts/python.exe -m pytest
1391 passed in 275.43s (0:04:35)                      (baseline 1377 / 0 skipped; +14, 0 skipped)
```

## Files changed

`orchestrator/{types,stages,runner,hooks,policy}.ts`, `orchestrator/test/{fakes,stages.test,policy.test,runner.test}.ts`,
`.github/agents/{translator,reviewer,validator,fixer,documenter}.agent.md`, `.github/copilot-instructions.md`,
`tests/test_agents_config.py`, `docs/reference/output-targets.md`, `README.md` (§1 "Three output targets" only).

## Self-review findings

- Hand-off hygiene: the diff carries no machine path, login name or scratch pointer (grep over
  `git diff 6282cce`); the profile template contains none.
- Fixed during self-review: a missing blank line before `PYTEST_ROLES`' comment in policy.ts.
- The phase-1 pinned script-call test passes unchanged; `masterSql` untouched.

## Concerns

- **Pre-existing, not changed here — likely to bite the live test (H):** `policy.ts`'s
  `PATH_ARG_KEYS = /path|file|target|…/i` also matches a key named `file_text`, so a `create` call
  shaped `{path, file_text}` has its CONTENT judged as a path and is denied (probed:
  `decide("translator","wf_0001","create",{path:"…/seg_01/proc.sql", file_text:"select 1"},"seg_01")` →
  `deny: translator may not write select 1`; same in dbt scope). No live run has reached a write yet
  (policy.test.ts' Task 16 notes), so whether the CLI's create tool uses `file_text` is unverified. Not
  fixed: a change to the path scanner is a security-relevant rule outside this task's brief.
- **Pre-existing:** a role's script invocation is not checked against the session's workflow id
  (`validate_dbt.py wf_0001` is allowed in a wf_0007 session, like `compile_check.py wf_0002 …` in
  phase 1) — only `workflows/<other>/` path mentions are caught.
- A switch of output kind between runs (`--from-stage analyze` turning dbt into procedures) leaves a
  stale `procs/README.md` beside the new `master.sql`; the dbt path removes a stale `master.sql`, the
  procedures path was deliberately left byte-identical and does not remove `README.md`.
- `cookbook/dbt.md` and `cookbook/snowpark.md` (Task E) are not on this base; the translator task and
  agent point at them already.

## Things C, F, W1 and G must know

**C (sample wf_0007):**
- MockRunner in dbt scope copies EVERY file under `samples/wf_0007/canned/dbt/` into `workflows/wf_0007/dbt/`
  at the same relative path — put only translator-lane files there (`dbt_project.yml`, `profiles.yml`,
  `README.md`, `translation_notes.md`, `models/**`); never `fix_log.md` (the fixer appends to it),
  `review.json`, `compile_check.json`, `logs/` or `target/`.
- The reviewer replays `samples/wf_0007/canned/review.json` (canned ROOT) to `dbt/review.json`.
- On `fix-loop:dbt`/`never-fixed:dbt` the FIRST file in full-path name order under
  `broken_sql/dbt/` is laid over the project at its relative path — for your two variants that is
  `models/attainment_history.sql` (a < r). `broken_sql/broken.json` is outside `broken_sql/dbt/` and is
  never served.
- The orchestrator's verify needs `dbt/dbt_project.yml` after the translator, `dbt/review.json` after
  the reviewer, and EVERY segment's `validation.json` after `validate_dbt.py`; it reads `verdict` and
  `needs_human` from each. `procs/README.md` is `dbtReadme("wf_0007")` (stages.ts) — your canned
  `docs/migration.md` `## Deployment` should quote that exact `dbt run … --target snowflake --vars
  '{"src_schema": "<SRC>", "tgt_schema": "<TGT>"}'` line.
- The documenter's dbt task names `workflows/wf_0007/dbt/translation_notes.md` and `procs/README.md`.

**F (prompt_context, deny list, intake question):**
- `translator.agent.md` now ends with `## dbt projects …` (after the phase-1 "Done when" paragraph),
  and has a new Inputs bullet; tests slice that section from its heading to the next `\n## `, find a
  `- **Model Jinja` bullet in it, and require `PROFILES_TEMPLATE` inside it — keep the heading text
  starting `## dbt projects` and keep new sections after it or before it, not inside it.
- `output-targets.md` §3.3 is rewritten and §4's table has a dbt column; §3's "no interactive question …
  in phase 1" paragraph is untouched (yours). §6's first bullet is untouched.
- The dbt translator/fixer/reviewer/validator task texts live in `migrateDbt` (stages.ts); `runAgent`'s
  ctx is `AgentCtx`. Prompt context for intake/analyzer does not touch the dbt branch.
- `test_agents_config.py` gained a phase-2 block at the end (helpers `_section`, `_bullet`,
  `_jinja_refusals`, `_dbt_check_names`).

**W1 (chain validation):**
- The dbt branch is `translateDbt` → `migrateDbt`; VALIDATED is set at the end of `translateDbt` after
  `procs/README.md`. A chain check for dbt would slot in after the all-PASS return and before the
  README write; statuses are per segment from `segments/<seg>/validation.json` only.
- Park reasons for dbt are `dbt: <reason>` (table in output-targets.md §4). Keep new ones in that form.
- Fakes: `isDbtScenario`, `CANNED_DBT`, `BROKEN_DBT` exported from fakes.ts; `fakeCompileDbt` /
  `fakeValidateDbt` inside `makeEnv`; dbt scenarios need `twoWaves: true` (seg_02 owns `ORDERS_OUT`);
  the stages tests use a `DBT` options constant.

**G (offline run, committed wf_0007):**
- README §1's dbt row now says "the committed worked example is `workflows/wf_0007/` (§6)" — G makes
  that true (and §6's sample list); the SQL row now reads "every committed procedures workflow's segment
  except `wf_0006/seg_02`".
- A dbt workflow's run leaves `workflows/<wf>/dbt/**` (canned replay + `compile_check.json`,
  `review.json`, git-ignored `logs/`), git-ignored `dbt_sandbox_<set>.duckdb` files, every segment's
  `validation*.json` with `"target": "dbt"`, and `procs/README.md` — NO `procs/master.sql`.
  `tests/test_committed_workflows.py` has no master.sql assumption today; `EXPECTED_TERMINAL` needs
  `wf_0007: VALIDATED`, and its comment "`dbt` is phase 2 and no committed sample uses it yet" becomes
  stale.

## Fix round 1

Rulings in `task-D-fix1.md` (review APPROVED; the three pre-existing gaps confirmed and ruled in scope).
Commit: **`2072fda`** wip: fix round 1 (G1, G2, G3, M1-M3). RED first for every item.

### What changed

- **G1 — content keys are never paths** (`orchestrator/policy.ts`). New `export const CONTENT_ARG_KEYS =
  {file_text, content, new_str, old_str, text, insert_line, old_string, new_string}`; `collectPathValues`
  skips a STRING under one of them (key compared lower-cased) even when the key matches `PATH_ARG_KEYS`
  (only `file_text` actually did). An object/array under a content key is still walked, so a `path`
  nested inside it is judged; every other key — the real `path` above all — is scanned exactly as
  before; rule 1a (another workflow's folder mentioned anywhere, content included) is unchanged.
- **G2 — a workflow script names only its own workflow** (`decideShell`). New `export const
  WORKFLOW_ID_SCRIPTS` — every allow-listed script whose argparse declares `wf_id` first:
  intake_touchpoints, intake_prompt, segment, target_check, compile_check, render_snowpark,
  validate_segment, validate_snowpark, validate_dbt, parse (read from each script; `compare.py` declares
  only `--expected/--actual/--contract/--out/…` and is not in it). After the existing per-argument checks,
  the first token that is not a flag must equal the session's workflow id (lower-cased, as paths are),
  else `cross-workflow: <script> <token> in a <wf> session`. Only fires when a bare token exists
  (`validate_dbt.py --help` passes). A flag's value placed before the id is not skipped, so
  `compile_check.py --target dbt wf_0001` and `validate_dbt.py --set normal` are refused — the documented
  form puts the id first, and this can only deny more.
- **G3** (`stageTranslate`, procedures path): `rm(procs/README.md, {force: true})` before writing
  `master.sql`. The script calls are unchanged (the pinned phase-1 test still passes).
- **M1** (`migrateDbt`): `failing = ""` when an iteration ends in a compile failure or a reviewer BLOCK.
- **M2**: `DBT_EXECUTABLE = /^(?:.*\/)?dbt(?:\.exe|\.cmd|\.bat)?$/`.
- **M3**: `dbtModelName` throws when the key its kind is named by is missing or null (`logical` for
  `kind: "target"`, `table` otherwise) — the mirror of `model_name`'s KeyError; a target with only a
  `table` throws too, as in Python. In practice unreachable in the loop: `compile_check.py --target dbt`
  exits 2 on the same malformed contract first (`dbt: script-error`).
- `docs/reference/output-targets.md` §4 Permissions: the `.cmd`/`.bat` spellings, the `cross-workflow`
  rule and the content-key rule, one sentence each.
- M4 left as ruled.

### Tests (node +10)

policy: "G1: a write call's content is never judged as a path" (the probe `create {path: dbt lane,
file_text: "select 1 as ID"}` allowed; the segment-lane equivalent; every content key holding
`.github/agents/evil.md` beside a lane path allowed; `edit` with `insert_line`/`new_str`); "G1: the real path
is still judged, and so is any other path-like key" (`path` outside the lane with `file_text` naming a lane
path → denied; `dbt/review.json` → denied; six non-content path-like keys incl. unknown ones → denied; an
object under `content` still walked; another workflow mentioned inside content still denied); "G2: a
workflow script may only name the session's own workflow" (the probe; all 11 script/role pairs allowed with
`wf_0001`, denied with `wf_0002` with the exact reason; flag-value-first denied; `WF_0001` allowed;
`--help` allowed; `--set normal` denied); "G2: compare.py takes only flags and keeps working"; "G2: the
policy's workflow-script list is every allow-listed script whose argparse takes wf_id first" (reads each
`scripts/*.py` in ROLE_SCRIPTS and checks its first `add_argument` against `WORKFLOW_ID_SCRIPTS`); "M2:
dbt.cmd and dbt.bat get the dbt denial too". stages: "G3: … removes a stale procs/README.md …"; "M1: a
compile failure clears the failing-model list …"; "M1: a reviewer BLOCK clears the failing-model list too";
"M3: dbtModelName refuses an output that has no name …".

### RED (tests written, no fix)

```
# tests 223  # pass 214  # fail 9
not ok 106 - G1: a write call's content is never judged as a path
    {"permissionDecision":"deny","permissionDecisionReason":"translator may not write select 1 as id"}
not ok 107 - G1: the real path is still judged …      actual: 'translator may not write select 1'  (content judged first)
not ok 108 - G2: a workflow script may only name the session's own workflow   ('allow' where deny expected)
not ok 110 - G2: the policy's workflow-script list …  TypeError: Cannot read properties of undefined (reading 'includes')
not ok 111 - M2: dbt.cmd and dbt.bat …                 (denied, but not with the dbt reason)
not ok 219 - G3: … stale procs/README.md …             (README.md still there)
not ok 220 - M1: a compile failure clears …            (fixer 2's task still said "Failing models: orders_out (seg_02)")
not ok 221 - M1: a reviewer BLOCK clears …             (same)
not ok 222 - M3: dbtModelName refuses …                Missing expected exception.
```
The one new test that passes on the old code, "G2: compare.py takes only flags and keeps working", is a
guard, as intended.

### GREEN

First run after the fix: 222/223 — my own G2 test was wrong, not the code: I had used
`validate_dbt.py --set normal` as the "no bare token" example, but `normal` IS a bare token, and the
literal rule refuses it (`cross-workflow: scripts/validate_dbt.py normal in a wf_0001 session`). The
test now uses `--help` for "no bare token" and pins `--set normal` as a deny.

```
$ fnm exec --using=22 npm.cmd test
# tests 223  # pass 223  # fail 0  # skipped 0          (213 before this round; +10)
$ tsc --noEmit -p .                                       clean
$ .venv/Scripts/python.exe -m pytest tests/test_agents_config.py
54 passed
$ .venv/Scripts/python.exe -m pytest
1391 passed in 258.76s (0:04:18)                        (0 skipped; unchanged — this round is node-only)
```

### Files changed this round

`orchestrator/policy.ts`, `orchestrator/stages.ts`, `orchestrator/test/policy.test.ts`,
`orchestrator/test/stages.test.ts`, `docs/reference/output-targets.md` (§4 Permissions, three sentences).

### Concerns

- G2 reads "the first bare token", so a flag value written before the id is refused (fail-closed). An agent
  must write `<script> <wf_id> …` — which is the form every agent file and task already uses.
- Seen while doing G2, not ruled, not changed: every script also takes `--root`, and `--root <relative dir>`
  passes the argument check (`PLAIN_TOKEN`), so a script could be pointed at a nested directory as its
  root; `..` and absolute paths are already refused. Denying `--root` for agents would close it.

## Fix round 2

Ruling (coordinator): `--root` in every spelling is denied in every agent script call; keep G2's
literal first-bare-token rule and make sure every agent file and task text puts the id first.
Commit: **`73fb3af`** wip: fix round 2 — --root denied in agent script calls. RED first.

### What changed

- `orchestrator/policy.ts`: `export const SCRIPT_ROOT_FLAG = /^--r(?:o(?:ot?)?)?(?:=.*)?$/i`, checked in
  `decideShell` right after the script allow-list and BEFORE the per-argument and cross-workflow checks, so
  every spelling gets `script-root: <script> may not be given --root from an agent session`. It covers
  `--root X`, `--root=X`, `--root .`/`--root=.`, `--root=` and the prefix abbreviations `--r`/`--ro`/`--roo`
  — every allow-listed script gets `--root` from `lib.paths.add_root_arg`, none sets `allow_abbrev=False`,
  and none has another `--r…` flag (checked by grep), so argparse resolves each of them to `--root`. Applies
  to `compare.py` too. The orchestrator's own `env.py` calls are not agent tool calls and are unaffected.
- **Id first everywhere** — two texts put a flag before the id, and both were found by the new scans:
  - the dbt fixer's task (`migrateDbt`): "scripts/compile_check.py --target dbt failed" →
    "scripts/compile_check.py <wf> --target dbt failed";
  - `fixer.agent.md` (my own round-0 line): `` `scripts/compile_check.py --target dbt` `` →
    `` `scripts/compile_check.py <id> --target dbt` ``.
- `docs/reference/output-targets.md` §4 Permissions: the id-first consequence of G2 and the `--root`
  rule, one sentence each.

### Brief correction

Brief test 5 ("a dbt compile failure is quoted to the fixer") pinned the substring
`scripts/compile_check.py --target dbt failed`, which is exactly the flag-before-id shape this ruling
removes from task texts. The assertion now reads `scripts/compile_check.py wf_0001 --target dbt failed`
(one-line change, commented in place); the rest of test 5 is unchanged.

### Tests

- policy: "script-root: --root in any spelling is denied in every agent's script call" — 6 script calls
  (validate_dbt, compile_check ×2, target_check, intake_prompt, compare) × 9 spellings × `--root` after and
  before the id, each with the exact reason; `--set normal` on validate_segment is still allowed.
- stages: "every task text names a workflow script with the session's id first, never a flag" — runs nine
  scenarios (procedures, snowpark, render-fails, compile-fails, fix-loop, T3, and dbt fix-loop /
  compile-fails / plain) and scans every task text; a workflow script (`WORKFLOW_ID_SCRIPTS`) followed by a
  flag, or by another workflow's id, is an offender.
- `tests/test_agents_config.py`: `test_every_agent_command_example_puts_the_workflow_id_first` — every code
  span in the nine agent files and `copilot-instructions.md` that names a script whose argparse takes
  `wf_id` first with arguments must start them with `<id>`/`<wf_id>`/`<wf>` (at least 10 examples must be
  seen, so the scan cannot silently match nothing); `test_no_agent_file_shows_a_script_call_with_root`.

### RED

```
node:   # tests 225  # pass 224  # fail 1
        not ok 111 - script-root: --root in any spelling is denied in every agent's script call
          'validator may not pass this argument: .'          (denied, but not as script-root; `--root sub` was allowed)
pytest: test_every_agent_command_example_puts_the_workflow_id_first FAILED
          fixer.agent.md: `scripts/compile_check.py --target dbt`
```
The task-text scan's first version passed on the old text: I had written its token capture as a quantified
lookahead, `(?=\s+(\S+))?`, and in JS a quantified empty-width group never keeps its capture. Rewritten as a
consuming group, it went RED on the old task text as intended:
```
not ok 87 - every task text names a workflow script with the session's id first, never a flag
  'fixer: …iled before review: scripts/compile_check.py --target dbt failed; read workflows…'  (×3)
```
After the fix, one false positive in my own scan (`wf_0001;`, prose punctuation) was fixed by stripping
trailing punctuation from the token. The flag-first check is unaffected by that change.

### GREEN

```
$ fnm exec --using=22 npm.cmd test
# tests 225  # pass 225  # fail 0  # skipped 0          (223 before this round; +2)
$ tsc --noEmit -p .                                       clean
$ .venv/Scripts/python.exe -m pytest tests/test_agents_config.py
56 passed                                               (+2)
$ .venv/Scripts/python.exe -m pytest
1393 passed in 277.78s (0:04:37)                        (0 skipped; +2)
```

### Files changed this round

`orchestrator/policy.ts`, `orchestrator/stages.ts`, `orchestrator/test/policy.test.ts`,
`orchestrator/test/stages.test.ts`, `.github/agents/fixer.agent.md`, `docs/reference/output-targets.md`,
`tests/test_agents_config.py`.
