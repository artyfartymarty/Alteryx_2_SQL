# Task 6A — orchestrator dispatch, mock replay, policy, agents, docs

Implementer report. Worktree `.worktrees/ot-task-6`, branch `wt/ot-task-6`, base `a0f03d2`.
Scope: the plan's Task 6 **Steps 1 and 2 plus the docs**. Step 3 (the offline run of `wf_0006` and
the committed `workflows/wf_0006/`) is 6B and was not attempted; `tests/test_committed_workflows.py`
and `workflows/` were not touched.

## Commits

| SHA | Subject |
|---|---|
| `00b9a55` | wip: RED tests for per-segment target dispatch (stages, MockRunner, policy) |
| `4a5c868` | wip: per-segment target dispatch in the orchestrator (analyze verify, translate, mock, policy) |
| `08c7f51` | wip: agent files and copilot-instructions learn the three output targets |
| `adef710` | wip: docs for the three output targets (reference page, README section, hand-off pointer) |
| `f9b30dc` | feat: per-segment target dispatch — target_check in analyze, Snowpark render/check/validate in translate, mock replay of proc.py, agents and docs |

## Test results

| Suite | Command | Result |
|---|---|---|
| node | `fnm exec --using=22 npm.cmd test` | **170 pass, 0 fail, 0 skipped** (baseline was 150) |
| typecheck | `fnm exec --using=22 node.exe …/typescript/bin/tsc --noEmit -p .` | clean |
| agents config | `.venv/Scripts/python.exe -m pytest tests/test_agents_config.py` | **35 pass** (28 before; 7 new) |
| full pytest | `.venv/Scripts/python.exe -m pytest -p no:randomly` | **1153 collected, exit 0, 0 skipped** — run because the canned contracts changed (see "Ripple") |

The node suite's `integration.test.ts` (`wf_0001` end to end) runs the **real** Python scripts, so
its continued pass is direct evidence that the real `scripts/target_check.py --prefer auto` runs
under the new `stageAnalyze`, writes a real `segments/targets.json`, and that the verify accepts the
real canned contracts.

### TDD evidence

**RED** — after writing the tests and the fake-script extensions, before any implementation
(commit `00b9a55`):

```
# tests 169
# pass 153
# fail 16
not ok 157 - analyze runs target_check.py --prefer auto after segment.py and before the analyzer, and records output_kind
  error: 'target_check.py ran'        <- the call did not exist yet
not ok 110 - MockRunner replays canned proc.py for a Snowpark segment and never fabricates a proc.sql
not ok 95  - each new target script is allowed for exactly the roles that run it
… (16 in total; the three MockRunner/policy tests that already passed are the ones asserting
   unchanged behaviour: proc.sql replay, the reviewer's denial, the renderer's output path in lane)
```

**GREEN** — after `4a5c868`: `# tests 169 / # pass 169 / # fail 0`. One RED assertion of mine was
itself wrong and was corrected, see "Brief corrections" §2.

## What changed, per file

### `orchestrator/types.ts`
- new `OutputKind` (`procedures | dbt`) and `SegmentTarget` (`sql | snowpark | manual`) types;
- `Manifest.output_target` (what was asked for) and `Manifest.output_kind` (what was decided), both
  documented against design §2/§3.2.

### `orchestrator/stages.ts`
- `escalate` now prefers a reason a verify callback already recorded for that stage. Without this a
  target contradiction surfaces as the generic `missing-output` (which is how a failed verify is
  reported to `runAgent`) and the park reason would tell a human nothing.
- `scriptError`'s log line names the exit code it saw instead of hard-coding "exited 2", because
  `target_check.py` now routes a non-2 exit here too.
- **`TARGET_RANK` + `checkTargets(env, m, segments)`** — the §3.2 verify: every contract has a
  recognised `target`; none ranks above `targets.json`'s proposal; `output_kind` is `dbt` only when
  the script proposed `dbt` **and** every contract target is still `sql`. Returns the exact park
  reason and the list of legitimate lowerings to log.
- **`stageAnalyze`** runs `env.py("scripts/target_check.py", [m.id, "--prefer", "auto"])` immediately
  after `segment.py` succeeds; **any** non-zero exit is `script-error` (controller resolution 1).
  The analyzer task text gained the targets.json / lower-only sentence. The verify callback keeps
  its T3 short-circuit and its contract-existence check, then runs `checkTargets`; on failure it
  sets `m.reasons.analyze` and returns false, on success it stashes the verdict. After the agent
  succeeds and the manifest is reloaded, the lowerings and any dbt→procedures fallback are logged
  and `m.output_kind` is set.
- **`migrateSegment`** re-reads `contract.json` once per iteration and computes
  `target = contract.target === "snowpark" ? "snowpark" : "sql"`. For `snowpark`: the write-verify
  requires `proc.py` specifically; `render_snowpark.py <wf> <seg>` runs after the agent (exit 2 →
  `NEEDS_HUMAN` / `script-error`; exit 1 → `lastReason = "render_snowpark failed after N
  iterations"` and `continue`, exactly like a compile failure); `compile_check.py` gets
  `["--target", "snowpark"]`; the validator task names `scripts/validate_snowpark.py`; the
  translator and fixer tasks gain one sentence naming `proc.py` and forbidding hand edits to
  `proc.sql`. **For `sql` every call is byte-identical to before** — asserted by a test that pins
  the whole ordered `py` call list of a SQL run.

### `orchestrator/runner.ts` (`MockRunner`)
- `brokenSql` → `brokenVariant`: no longer filters on `.sql`; the first file in name order wins
  whatever its extension, and it is copied to `proc.py` or `proc.sql` by its own extension.
- `replaySql` copies `canned/segments/<seg>/proc.py` when the canned tree has one, else `proc.sql`
  as before. It never writes a `proc.sql` for a Snowpark segment, so in an offline run that file can
  only have come from `render_snowpark.py`.
- `replayValidator` reads the segment's `contract.json.target` and spawns
  `scripts/validate_snowpark.py` for `snowpark`, `scripts/validate_segment.py` otherwise.

### `orchestrator/policy.ts`
- `ROLE_SCRIPTS`: analyzer `+ scripts/target_check.py`; translator and fixer `+
  scripts/render_snowpark.py`; validator `+ scripts/validate_snowpark.py`; reviewer unchanged
  (empty). Write lanes untouched — `proc.sql` and `proc.py` were already both in the
  translator/fixer lane, which is what makes the renderer's output legitimate.

### `orchestrator/test/fakes.ts`
- `FakeCalls` gains `tasks` (role + segment + prompt, so a test can assert which script a role was
  told to run) and `order` (`py:<script>` / `agent:<role>` interleaved, so a test can assert a
  script ran *before* an agent).
- `targetShape(scenario)` maps a scenario to (a) which segments `target_check.py` proposes as
  `snowpark` and (b) what `target` the canned contract carries: `snowpark:` / `render-fails:` /
  `render-crashes:` (proposal and contract both snowpark), `target-raise:` (proposal snowpark,
  contract sql), `target-lower:` (proposal sql, contract snowpark), `target-missing:` (no key).
- The canned contract carries `"target"`; a snowpark segment's canned artefact is `proc.py` (a
  Snowpark `GOOD_PY`), never a canned `proc.sql`.
- Fake `py` gained `target_check.py` (faithful `--prefer auto` resolution, writes a real-shaped
  `targets.json` with `dbt_blockers`), `render_snowpark.py` (writes the real wrapper shape; exits
  2/1 on the two scenarios) and `validate_snowpark.py`; `validate_segment.py`'s body was extracted
  into a shared `fakeValidate(id, seg, target)` used by both, with `"target"` in the report.

### `samples/wf_000{1..4}/canned/segments/*/contract.json` (9 files, 1 line each)
`"target": "sql"` inserted after `"segment"`. See "Ripple" below.

### `.github/agents/*.agent.md` and `.github/copilot-instructions.md`
- **analyzer**: new procedure step 3 (later steps renumbered, and the `unsupported.json` cross-ref
  updated from "step 5" to "step 6") — `target_check.py`, the `targets.json` shape, copying the
  proposal into `contract.json.target`, the lower-only rule, and the two exact park reasons.
  `targets.json` added to Inputs.
- **translator**: a "Snowpark segments" section carrying the design §4.2 rule paragraph **verbatim**
  plus the rendered-`proc.sql` rule and the two-step done criterion (`render_snowpark.py` then
  `compile_check.py … --target snowpark`).
- **reviewer**: a "Blocking checks for a Snowpark segment" block (AST rules, the `# tool <id>:`
  comment, `proc.sql` ↔ `proc.py`, unjustified pandas is blocking); `proc.py` added to Inputs.
- **validator**: step 1 picks the script from `contract.json.target`; step 3 covers both scripts'
  exit codes; a new step 4 states what the Snowpark local double does not prove (design §9).
- **fixer**: a "Snowpark segments" section — repair `proc.py` under the same rules, never edit
  `proc.sql` by hand. Its unamended-body sentence is untouched, and it deliberately does **not**
  carry the `<!-- amended: plan Task 12 -->` marker (a different marker,
  `<!-- amended: output targets phase 1 -->`, is used) so `test_amended_paragraphs_are_marked`
  stays green.
- **copilot-instructions.md**: one paragraph on the three targets.
- Frontmatter keys are untouched in all five files; no machine path or user name anywhere (there is
  now a test for that).

### `tests/test_agents_config.py`
Seven new tests (the brief describes the agent content in prose and does not test it): analyzer's
`target_check`/lower-only statements, the translator carrying the §4.2 token list, the translator's
rendered-`proc.sql` rule, the reviewer's Snowpark blocking checks, the validator's per-target script
choice plus its honesty statement, the fixer's `proc.py`-only rule, the copilot-instructions
paragraph, and a hand-off check that no `.github/` file carries an absolute machine path or the OS
login name.

### Docs
- **`docs/reference/output-targets.md`** (new): vocabulary; the decision step by step with the
  verify table and the two park reasons; artefacts and layout per target (the §4.2 rules and the
  render template); "dbt: phase 2 — decided and recorded only"; what the orchestrator does per
  target (a per-step table), the policy allow-lists and the mock-replay rules; deployment
  (`proc.sql` *is* the DDL for a Python procedure, `RUNTIME_VERSION`/`PACKAGES`, `EXECUTE AS
  CALLER`, `MIGRATION_CI`); §6 "what the local doubles do not prove" (design §9); a where-it-lives
  table.
- **`README.md`**: a "Three output targets" subsection at the end of §1 (status table, how the
  decision is made, the dbt phase-2 caveat, the honesty paragraph); §8 gained a "Deploying a
  Snowpark Python procedure" block; the stage diagram gained one node (`target_check.py →
  segments/targets.json`) and its validator node label now names both validators; the repo map
  gained the new reference page. **The sample table was left alone** — the `wf_0006` row is 6B's,
  per controller resolution 7.
- **`docs/handoff-copilot-models.md`**: a §0 bullet — a segment is no longer always SQL, read the
  new reference page before a hosted run, the prompts and the policy allow-lists changed, and a
  Snowpark translator prompt is denser (which matters for the context-window budgeting in §1/§4).

## How the brief's six test bullets are covered

| Brief bullet | Tests |
|---|---|
| **1.** analyze runs `target_check.py` before the analyzer; `manifest.output_kind` from `targets.json` | `stages.test.ts`: "analyze runs target_check.py --prefer auto after segment.py and before the analyzer, and records output_kind" (asserts the exact args, the `segment.py → target_check.py → analyzer` order via `calls.order`, `targets.json` on disk, `m.output_kind`, and the analyzer prompt naming `targets.json`); "--prefer is always auto: target_check.py resolves a dbt preference itself, and output_kind follows"; "target_check.py exiting 1/2 parks analyze before the analyzer ever runs" (two tests, controller resolution 1) |
| **2.** a RAISED target parks with the exact reason; a LOWERED one is accepted and logged | "a contract that RAISES a target parks analyze NEEDS_HUMAN with the exact target-mismatch reason" (`reasons.analyze === "target-mismatch: seg_02 raised snowpark to sql"`, no translator); "a LOWERED target (sql -> snowpark) is accepted and logged"; "a contract with no target at all parks analyze with target-missing"; "a lowering to snowpark makes a dbt-preferring workflow fall back to procedures, and says so" |
| **3.** snowpark: render → `compile_check --target snowpark` → validator names `validate_snowpark.py`; render exit 2 → `script-error`, exit 1 → next iteration | "a snowpark segment renders, compiles with --target snowpark and is validated by validate_snowpark.py" (pins seg_02's whole ordered call list, asserts `proc.sql` contains `LANGUAGE PYTHON` so it can only be the renderer's, and pins seg_01's SQL calls in the same workflow); "the fixer's task for a snowpark segment names proc.py and forbids editing proc.sql by hand"; "render_snowpark.py exiting 2 is a script error for that segment"; "render_snowpark.py exiting 1 ends the iteration like a compile failure" |
| **4.** a sql segment's calls are byte-identical | "a SQL workflow's script calls are exactly what they were, plus the one target_check.py call" — `deepEqual` on the full ordered `[script, args]` list of a whole run, plus the validator prompt asserted to be the old wording and to contain no "snowpark" |
| **5.** `MockRunner`: `proc.py` replay, `.py` broken variant, target-aware validator | `runner.test.ts`, four tests against a `wf_0006`-shaped temp samples dir built in the test: "replays canned proc.py … and never fabricates a proc.sql"; "still replays proc.sql for a segment whose canned tree has no proc.py"; "serves the first broken variant in name order whatever its extension" (a `.py` sorting before a `.sql`, then the fixer's good artefact); "validator spawns validate_snowpark.py for a snowpark contract and validate_segment.py otherwise" (including a contract with no `target`) |
| **6.** policy lanes and the new scripts | `policy.test.ts`: "each new target script is allowed for exactly the roles that run it" (incl. `compile_check.py … --target snowpark`); "the reviewer may run none of the target scripts, and neither may the wrong role"; "render_snowpark.py's output path is inside the lane of the roles allowed to run it" |

## Brief corrections

**1. The lower-only comparison direction (load-bearing).** The brief's Step 2 prose and controller
resolution 2 both say *"rank `sql < snowpark < manual`; a contract whose target ranks ABOVE the
proposal is a raise"*. Taken literally that makes `snowpark → sql` legal and `sql → snowpark`
illegal — the exact opposite of what is wanted, and it contradicts three other statements:

- spec §3.2: "It may **lower** a target (`sql` → `snowpark`, or either → `manual`) … it may never
  raise one";
- the brief's own Step 1 test 2: *"a contract that RAISES a target (`targets.json` says snowpark,
  contract says sql) … `target-mismatch: seg_02 raised snowpark to sql`; a LOWERED one (`sql` →
  `snowpark`) is accepted"*;
- the reason template `"<seg> raised <proposal> to <contract>"` itself, whose required output for
  proposal `snowpark` / contract `sql` is only producible if that pair is the violation.

Implemented per the spec and the brief's test: `TARGET_RANK = { manual: 0, snowpark: 1, sql: 2 }`
("`sql` is the highest"), and a raise is `rank(contract) > rank(proposal)`. The prose ordering is
the only thing changed; every string the brief specifies is produced verbatim.

**2. One of my own RED assertions was wrong.** In "render_snowpark.py exiting 1 …" I first asserted
`!calls.roles.includes("reviewer")`, but the scenario uses `twoWaves`, so seg_01 (a plain SQL
segment) legitimately reaches the reviewer. Corrected to assert that no reviewer/validator task was
issued *for seg_02*, plus no `compile_check.py` for seg_02.

## Ripple: `samples/*/canned/segments/*/contract.json`

The verify refuses a contract with no `target` (controller resolution 2), and the canned analyzer
output is what a real analyzer would have written — so the nine existing canned contracts needed
`"target": "sql"`. Without it, `integration.test.ts` (the real end-to-end `wf_0001` run) would park
at `target-missing: seg_01`. Each file got exactly one inserted line, formatting otherwise
untouched. `tests/test_canned_artifacts.py`'s `CONTRACT_KEYS` is a required-keys check, not an
exact-set check, so nothing there needed changing; the full pytest suite confirms it.

## Things 6B must know

1. **The committed `workflows/wf_000{1..4}/segments/*/contract.json` now differ from their canned
   originals** — they have no `"target"`, because I was told not to touch `workflows/`. Nothing
   tests that equality today and `tests/test_committed_workflows.py` is unaffected, but a
   README-§6-style reproducibility re-run of wf_0001–4 would now produce a one-line diff per
   contract. Worth deciding deliberately: either 6B refreshes those four workflows' contracts, or
   the drift is accepted and noted.
2. **`wf_0006`'s canned tree must have `proc.py` and no canned `proc.sql`-as-agent-output for
   seg_02.** `MockRunner` prefers `proc.py` when it exists, and it never writes a `proc.sql` for
   that segment — the run-root `proc.sql` has to come from `render_snowpark.py`, which is the
   property that makes the offline run prove the render step. A committed canned `proc.sql`
   (for `test_canned_artifacts`'s `proc.sql` ↔ render check) is fine and is simply not copied.
3. **Broken variants for a Snowpark segment must be `.py`, and must sort first.** The chosen variant
   is the first file in *name order* in `broken_sql/<seg>/`, whatever the extension; a `.sql` that
   sorts before the intended `.py` would be served onto `proc.sql` and the render step would then
   use a stale `proc.py`. Name them so the intended one wins (`01_…`, `02_…`).
4. **`wf_0006`'s `contract.json` for every segment needs a `target`**, including the SQL ones, or
   analyze parks at `target-missing`.
5. `manifest.json` will carry `output_kind: "procedures"` for wf_0006 after analyze; the committed
   manifest should show it.
6. The README sample table row and `tests/test_committed_workflows.py`'s `EXPECTED_TERMINAL` entry
   are untouched and are 6B's.

## Self-review concerns

1. **`target_check.py` exit 1 is routed to `script-error`** per controller resolution 1, but spec
   §3.1 defines exit 1 as "written, but the workflow has `unknown` nodes (**still written, so the
   analyzer can see them**)". So the one case the spec designed that exit for — letting the analyzer
   look at the unknown nodes and record them in `analysis.md`/`unsupported.json` — can no longer
   happen: the workflow parks with reason `script-error`, which also misdescribes a file that was
   written correctly. I implemented the resolution as given (and tested it) rather than silently
   diverging, but this is the one decision I would ask the controller to re-confirm. If it should
   change, it is one line in `stageAnalyze` (`if (targets.code === 2)`) plus the paired test.
2. **A contract lowered to `manual` is translated as SQL.** `migrateSegment` maps anything that is
   not `snowpark` to `sql`, so a `manual` contract on a workflow whose tier is not T3 would be sent
   to the translator. In practice the analyzer sets tier T3 whenever a node is manual and translate
   goes straight to `MANUAL`, so the two would have to disagree — but nothing enforces that. The
   brief and the resolutions do not mention it, so I did not invent behaviour for it.
3. **`escalate`'s reason precedence is stage-wide, not verify-specific.** If a verify records
   `target-mismatch` on attempt 1 and attempt 2 then fails for an unrelated reason (rate-limit,
   budget), the park reason stays `target-mismatch`. I judged the first real diagnosis to be the
   more useful one, and the verify callback deletes `reasons.analyze` at the start of each attempt
   so a *fixed* contract leaves no stale reason. A reviewer may prefer the narrower "only a reason
   set during this attempt wins".
4. **`output_kind: "dbt"` is recorded but not acted on.** A workflow can legitimately end with
   `manifest.output_kind === "dbt"` and still get procedures, because phase 1 has no dbt translate
   path. That is the plan's phasing, and it is stated in both new docs and in the README, but it is
   a field a reader could misread.
5. **No test covers "verify fails, the retry succeeds, the stale reason is gone."** `MockRunner`
   replays the same canned contract on both attempts, so the scenario is not reachable without a
   new fake affordance. The delete is a one-liner with a comment; I chose not to build machinery
   for it.
6. **`orchestrator/policy.ts` is 927 lines and `stages.ts` is now ~700.** Both were already large;
   I added to them in place and did not split anything, per the plan's file list.

---

## Fix round 1

Rulings from `task-6a-fix1.md` (review of `f9b30dc`). All five applied, RED-first for every named
test. Commit: **`f5340af`** — *wip: fix round 1 (C1, I1, I2, M1, M2) — target_check exit 1 reaches the analyzer, manual segments never translate, pre-review failures quoted to the fixer*.

### Counts

| Suite | Command | Result |
|---|---|---|
| node | `fnm exec --using=22 npm.cmd test` | **176 pass, 0 fail, 0 skipped** (was 170) |
| typecheck | `… tsc --noEmit -p .` | clean |
| agents config | `.venv/Scripts/python.exe -m pytest tests/test_agents_config.py` | **36 pass** (was 35) |
| full pytest | `.venv/Scripts/python.exe -m pytest -p no:randomly` | exit 0, 0 skipped (1154 collected) |

**RED before implementing** (6 node + 1 pytest, each for its own finding):

```
not ok 168 - a segment targets.json does not propose at all parks analyze with target-missing      (M2)
not ok 169 - a contract lowered to manual never reaches the translator                             (I1)
not ok 170 - after a render failure the fixer is told which script failed and what it said         (I2)
not ok 171 - after a compile failure the fixer is pointed at compile_check.json and told what it said (I2)
not ok 172 - a diagnosis quoted to the fixer is redacted and bounded exactly like an audit line    (I2)
not ok 175 - target_check.py exiting 1 (unknown nodes) writes targets.json and still reaches the analyzer (C1)
# tests 176 / # pass 170 / # fail 6
FAILED tests/test_agents_config.py::test_translator_snowpark_rules_bullet_is_verbatim_from_the_design (M1)
```

**GREEN after**: `# tests 176 / # pass 176 / # fail 0`; pytest agents `36 passed`.

### C1 — exit 1 reaches the analyzer

`stageAnalyze` now parks only on `targets.code === 2`; a non-zero, non-2 exit logs
`target_check: unknown nodes in <wf>; the analyzer decides` and continues exactly as exit 0 does.
The parameterised test was split: the exit-2 test keeps its assertions (park, `script-error`, no
analyzer, `exited 2` logged), and a new `unknown-nodes` scenario drives the exit-1 case — it asserts
`status.analyze === "DONE"`, no `script-error` reason, the analyzer ran, the log line, and that
`targets.json` is on disk **complete with its `nodes: {"7": "unknown"}` entry**, which is the whole
point of that exit code. The fake `target_check.py` gained `TargetShape.unknownNodes`: it writes the
same complete `targets.json` and returns exit 1.

This closes the concern I raised in my own first report (self-review concern 1); the ruling matches
spec §3.1 and the reason string no longer misdescribes a file that was written correctly.

### I1 — a `manual` contract never reaches the translator

`migrateSegment` reads the contract at the top of each iteration and, when `target === "manual"`,
returns `{ verdict: "NEEDS_HUMAN", reason: "manual-segment" }` before any agent or script runs — the
same segment-level park path `script-error` uses, so `stageTranslate`'s existing rules set
`status.translate = "NEEDS_HUMAN"` and `reasons.translate = "seg_02: manual-segment"` unchanged. The
tier-T3 gate is untouched. The test (`manual:seg_02` scenario, two waves) asserts analyze still
`DONE` (lowering to `manual` is a legal lowering), the segment's verdict and reason, that seg_01 in
wave 1 still reached `PASS`, and that **no task and no `py` call at all** carries `seg_02`.

### I2 — a pre-review failure is quoted to the fixer

A render exit 1 or a compile-check failure sets `failedBeforeReview`, one sentence appended to the
next fixer's task:

- `The previous attempt failed before review: scripts/render_snowpark.py said: <diagnosis>`
- `The previous attempt failed before review: scripts/compile_check.py failed; read compile_check.json. It said: <diagnosis>`

`diagnosis(result)` is `auditArgs(`${err}\n${out}`.trim())` — `auditArgs` is the existing exported
helper in `hooks.ts` that `CopilotRunner` already uses for `AgentResult.detail`; it redacts **then**
truncates to `AUDIT_ARG_LIMIT` (500), so a cut-off secret cannot survive. Nothing was duplicated.
The sentence is cleared as soon as an iteration gets past the compile check, so a later
validation-FAIL fixer is not told a stale story. Four tests: the render wording + quoted stderr, the
compile wording + `compile_check.json` + quoted stderr, a secret-bearing 2000-character stderr
(asserts the secret is gone, `<redacted>` present, the task bounded), and a regression test that a
plain validation-FAIL fixer task carries **no** `failed before review` sentence and still names
`validation.json`. Script calls are untouched — the pinned byte-identical SQL call-list test passes
unchanged.

### M1 — translator §4.2 copy is verbatim

Restored `(checked by `compile_check.py --target snowpark`, §5.1)`. The new pytest
`test_translator_snowpark_rules_bullet_is_verbatim_from_the_design` slices the bullet out of
`docs/superpowers/specs/2026-09-22-output-targets-design.md` (from `- Rules (checked by …` to the
next `- Rendered artefact:`) and asserts it is a substring of the agent body with newlines
normalized — so the two can never drift again, not just this one parenthetical.

### M2 — no third reason format

A segment with no proposal now parks with `target-missing: <seg>`; the explanation
(`<seg> has no target proposal in segments/targets.json — nothing to verify against`) goes to
`env.log` only. New `no-proposal:<seg>` scenario in the fake `target_check.py` (it omits that
segment from `targets.json.segments`); the test pins both the reason and that the detail is in the
log. No existing test pinned the old wording.

### Docs touched in this round

- `docs/reference/output-targets.md`: exit 1 vs exit 2 in §2 step 2; the no-proposal row and the
  "these two strings are the only reason formats" note in the verify table; two new paragraphs in
  §4 (`manual` is never translated; a pre-review failure is quoted to the fixer, with the
  `auditArgs` bound named).
- `README.md` §1 "Three output targets": the no-proposal case and `manual-segment`.
- `.github/agents/analyzer.agent.md`: lowering to `manual` parks the segment, so only lower to
  `manual` when the workflow is meant to be tier T3.

### Concerns after this round

1. **`failedBeforeReview` is per-`migrateSegment`, not persisted.** A crash between iterations loses
   the sentence; the next run's fixer gets the standing task. That matches how `lastReason` already
   behaves and nothing in the rulings asked for persistence.
2. **The quoted diagnosis is a script's stderr going into a model prompt.** It is redacted and
   bounded by the same helper the audit trail uses, but that helper's `SECRET_ASSIGNMENT` pattern is
   the only filter — a secret in a shape it does not match would reach the prompt. Same exposure the
   audit trail and `AgentResult.detail` already carry; no new mechanism.
3. **I1 duplicates a gate the analyzer already owns.** A `manual` contract on a non-T3 workflow means
   the analyzer contradicted itself; the guard parks rather than reconciling. That is what the
   ruling asked for, and it is the safe direction, but the underlying inconsistency is still only
   visible in the log line.
