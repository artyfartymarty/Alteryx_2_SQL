# Task 15 — Orchestrator (TypeScript, Copilot SDK)

Worktree: `.worktrees/task-15`, branch `wt/task-15`, based on `09bcc53`.
Node 22.23.2 via fnm; TypeScript 7.0.2; `@github/copilot-sdk` 1.0.14 (types only — no live session was created).

## What I implemented

| File | Responsibility |
|------|----------------|
| `orchestrate.ts` | Entry point: `process.exit(await main(process.argv.slice(2)))` |
| `orchestrator.config.json` | python path, samplesDir, parallelism 3, maxFixIterations 3, maxParseRecovery 2, sessionTimeoutMs 1_200_000, `golden.producer: simulator`, `budgets.maxToolCallsPerWorkflow: 400`, `profiles.local` (BYOK llama.cpp) and `profiles.hosted` (role models) |
| `orchestrator/types.ts` | `Role`, `Stage`, `STAGES`, `Manifest`, `ShResult`, `AgentError`, `AgentResult`, `AgentRunner`, `Env`, `Profile`, `ProfileConfig`, `OrchestratorConfig`, `RunOptions` |
| `orchestrator/manifest.ts` | `wfDir`, `loadManifest`, `saveManifest` (indent 2 + trailing newline, `updated_at`), `reloadManifest` (disk-wins merge after every `py` call), `readJson(Or)`, `writeJson`, `fileExists` |
| `orchestrator/policy.ts` | Pure `decide(role, wfId, toolName, toolArgs, segment?)`; provisional tool-name regexes and the role→command / role→path tables are exported named constants |
| `orchestrator/hooks.ts` | `hooksFor(role, wf, env, segment?) → { hooks, state }`: session context, permission decisions, `audit.jsonl`, secret/oversize flagging, 429 classification, `metrics[role]` on session end |
| `orchestrator/agents.ts` | `parseAgentFile`, `loadAgents(root, profile)` → `CustomAgentConfig[]` (local drops per-agent `model`) |
| `orchestrator/runner.ts` | `MockRunner` (replays `samples/<wf>/canned/…`, runs the real `validate_segment.py`) and `CopilotRunner` (one session per call, `agent: role`, approve-once, readline or "user unavailable" for `ask_user`) |
| `orchestrator/stages.ts` | `migrateWorkflow`, `masterSql`, `clearFromStage`, `plannedStages`, `TERMINAL_GOOD`; the full parse/intake/analyze/golden/translate/document/pr machine with retries, budgets and error routing |
| `orchestrator/cli.ts` | `parseArgs`, `loadConfig`, `makeEnv` (spawn-based `py`/`sh`, `gh --version` probe), workflow selection, bounded pool, summary, exit codes, `main(argv, deps?)` |
| `orchestrator/test/*` | `fakes.ts` + `policy`, `agents`, `hooks`, `stages`, `cli` test files |

Stage semantics follow the brief and `docs/spec/01-copilot-setup.md` Part B: parse with up to 2 recovery rounds then `QUARANTINED`/T3; intake touchpoints → prompt (stdio inherited when interactive) → agent when `intake/plan.md` is missing → route on `manifest.status.intake` re-read from disk (`WAITING_FOR_ANSWERS` → `gh issue create` or a logged path); analyze with a contract-per-segment check and T3 → `MANUAL`; golden via the simulator or the `inject_outputs.py` instructions; translate in waves with segments concurrent and up to 3 fix iterations each (compile failure or review `BLOCK` ends an iteration, `needs_human` short-circuits), then `procs/master.sql`; document; PR only when `gh` exists. Agent errors: rate limit → `sleep(2^n·1000)` up to 3 retries, missing-output/timeout → one retry, denied → immediate `NEEDS_HUMAN`, tool-call total over budget → `NEEDS_HUMAN` with reason `budget`.

## Tests

`fnm exec --using=22 npm.cmd test` → **54/54 passing, output pristine** (no warnings, no stray output).
`fnm exec --using=22 node.exe ../../node_modules/typescript/bin/tsc --noEmit -p .` → **clean** against the installed SDK types (also clean with `--noUnusedLocals --noUnusedParameters`).

- `policy.test.ts` (11): the brief's five tests verbatim, plus every role's own lane, the shared read-only shell commands, cross-workflow reads through any tool, SQL denials across tool-name and statement-key spellings, and fall-through-to-allow.
- `agents.test.ts` (4): frontmatter split (including a `---` inside the body and a file with none), nine agents loaded, local drops `model`, hosted keeps it, missing folder → `[]`, name falls back to the file name.
- `hooks.test.ts` (6): redaction + truncation, the three session-start lines, allow/deny counting and audit lines, secret and >200 000-character flags, 429 → rate-limit, metrics written to the manifest on disk. (Not in the brief's file list; the hooks table is prose-only otherwise, and `MockRunner` never exercises hooks.)
- `stages.test.ts` (24): the brief's nine tests verbatim, plus artifacts on disk after the happy path, compile-error iteration end, `needs_human` short-circuit, both golden dead ends, intake `BLOCKED`, the gh/no-gh branches for issues and PRs, `--stop-after`, `--dry-run`, `--from-stage` clearing later stages only, missing-output retry-once, rate-limit exhaustion, the budget kill switch, script exit 2 → `script-error`, and `masterSql`'s C4 shape.
- `cli.test.ts` (9): every flag (and `--flag=value`), usage rejections, config defaults/merging, `--dry-run` calling neither `py` nor the runner, exit 0 / 1 (`NEEDS_HUMAN`, `QUARANTINED`) / 2 (unknown flag, unknown `--only`, bad stage, missing `workflows/`, unexpected crash), and `--tier` selection.

Smoke test of the real entry point (no Python, no network):
`node --experimental-strip-types orchestrate.ts --root <scratch> --runner mock --no-interactive --dry-run` → logs `would run parse → intake → … → pr`, exit 0; `--only wf_9999` → `unknown workflow`, exit 2. The `gh --version` probe reported `gh=no` and degraded without throwing.

### TDD evidence

RED (before `policy.ts` existed):
```
$ node --experimental-strip-types --test orchestrator/test/policy.test.ts
# Error [ERR_MODULE_NOT_FOUND]: Cannot find module '…\orchestrator\policy.ts'
not ok 1 - orchestrator\test\policy.test.ts
```
Same failure shape for `agents.test.ts` (`…\orchestrator\agents.ts`) before `agents.ts`, and for `stages.test.ts`/`cli.test.ts` before their modules. Expected: the test files are written first and import modules that do not exist yet.

GREEN, after each module: `# tests 11 / # pass 11` (policy), `4/4` (agents), `6/6` (hooks), `24/24` (stages), `9/9` (cli), `54/54` for the suite.

Two failures caught real defects rather than test typos:
1. `--from-stage` cleared statuses in memory only; the first `reloadManifest` inside the re-run stage merged them straight back from disk. Fixed by saving the cleared manifest before the stage loop.
2. The dispatch's secret regex never matched real `toolArgs` (see Brief corrections).

## Brief corrections and decisions

1. **Redaction pattern widened.** The dispatch's `/(password|pwd|token|secret|apikey|api_key)\s*[=:]\s*\S+/i` cannot match a JSON-quoted key (`"token":"sk-live-1"`), which is exactly the shape of `toolArgs`; a test proved the secret survived into `audit.jsonl`. `SECRET_ASSIGNMENT` now tolerates the quotes: `/(password|pwd|token|secret|apikey|api_key)"?\s*[=:]\s*"?[^\s",}\]]+"?/gi`, whole match replaced by `<redacted>`, redact-then-truncate to 500 characters. `SECRET_MENTION` (post-result flag) got the same optional quote.
2. **`status.parse` is always `PARSED` on success.** The brief's test asserts `PARSED` for `wf_0005` even though that workflow only parses after a recovery whose `parse_report.json` says `RECOVERED`. The recovery is recorded in `manifest.parse = { attempts, extensions }` instead, which is the schema's field for it.
3. **`manifest.reasons`** (stage → `budget` | `script-error` | `denied` | `missing-output` | `segmentation` | …) was added so a `NEEDS_HUMAN` status says why. The manifest schema has an open shape; nothing else writes this key.
4. **Runners get `attach(env)`.** `Env` owns the runner, so the runner cannot take the env in its constructor. `MockRunner`/`CopilotRunner` are built first and closed over the env afterwards; `cli.ts` and `fakes.ts` both do it.
5. **Segment roles are denied writes when no segment is in context** (`translator`, `fixer`, `reviewer`, `validator`). The orchestrator always passes one; a write without it cannot be checked against a lane, so it is refused rather than widened to `segments/*`.
6. **`format` destructive regex narrowed** to `(^|[\s;|&(])format\b` so `git log --format=…` and `--pretty=format:` are not denied. All tool-name regexes stay provisional and exported, per the dispatch.
7. **Defaults:** `--runner mock` (a bare run cannot spend model calls or contact the BYOK endpoint by accident — pass `--runner copilot` for live sessions), `--profile local`, `interactive` = stdin is a TTY unless `--interactive`/`--no-interactive` says otherwise, `--interactive` forces workflow parallelism 1.
8. **Exit codes** follow the coordinator's contract: 0 terminal/parked, 1 any `NEEDS_HUMAN`/`QUARANTINED`/`BLOCKED`, 2 usage (unknown flag, unknown `--only`, bad `--from-stage`/`--stop-after`/`--tier`/`--runner`/`--profile` value, missing `workflows/`) or an unexpected crash. Script exit 2 is never read as a domain verdict: the stage becomes `NEEDS_HUMAN` with reason `script-error` and the stdout/stderr tail is logged.
9. **Step 5 (integration run with real Python) was not run** — the dispatch assigns it to the controller after the other tasks land. No Python was executed in these tests.

### SDK type findings (types win over the brief)

Everything the brief listed as verified matched `@github/copilot-sdk@1.0.14` — `createSession({ workingDirectory, model, reasoningEffort, provider, mcpServers, customAgents, agent, hooks, onPermissionRequest, onUserInputRequest })`, `CustomAgentConfig`, the hook inputs/outputs, `{ kind: "approve-once" }`, `sendAndWait({ prompt }, timeoutMs)`, `session.disconnect()`, `client.start/stop()`, `ProviderConfig`. No `as any` is used on the `createSession` config. Three details the brief could not know:

- `UserInputRequest` / `UserInputResponse` / `UserInputHandler` are **not exported from the package root** (only `AskUserVariant` and the handler slot on `SessionConfig`). `runner.ts` derives them with `Parameters<NonNullable<SessionConfig["onUserInputRequest"]>>[0]` and `Awaited<ReturnType<…>>` rather than deep-importing `dist/types.js`.
- `PostToolUseHookInput.toolResult` is a `ToolResultObject`, never a string, so the spec skeleton's `typeof input.toolResult === "string"` branch is dead; the hook reads `toolResult.textResultForLlm` and `resultType`.
- `PreToolUseHookOutput.permissionDecision` is optional in the SDK; `Decision` in `policy.ts` always sets it, which is assignable.
- `ReasoningEffort` is `"low" | "medium" | "high" | "xhigh" | "max"`; `ProviderConfig.type` is optional (`"openai" | "azure" | "anthropic"`). `OrchestratorConfig` mirrors both, and carries an optional `mcpServers` typed as the SDK's `MCPServerConfig` so the Snowflake MCP server can be configured for Task 16 (left out of the committed config file, which would otherwise assert a server that does not exist).

## Test fakes (self-contained, as dispatched)

`orchestrator/test/fakes.ts` builds a temp root per test: `workflows/<wf>/{manifest.json,source/}` plus a synthetic `samples/<wf>/canned/{intake/plan.md, analysis.md, unsupported.json, segments/seg_01/{contract,proc.sql,translation_notes,review}, docs/migration.md}`, `samples/<wf>/broken_sql/seg_01/broken.sql`, and for `wf_0005` a `canned/parser-recovery/**` tree (extension, corpus fixture, diagnosis) with `unsupported.json` tier `T3`. The fake `py` performs each script's file effects itself and returns the documented exit codes; `validate_segment.py` writes `FAIL` when the segment's current `proc.sql` equals the broken SQL and `PASS` otherwise, so `MockRunner`'s own copying is what the fix-loop tests measure. Temp roots are removed on process exit. Scenarios: `fix-loop:<seg>`, `never-fixed:<seg>`, `recovery-fails`, plus `compile-fails:<seg>` and `needs-human:<seg>` added for the two prose-only branches.

## Self-review findings (fixed before reporting)

- `--from-stage` disk/memory divergence (above) — fixed and covered by a test.
- The secret regex miss (above) — fixed and covered by a test.
- Dropped a redundant `shouldRun` special case for `golden` and an unused `readText` helper in `runner.ts`; removed an unused test parameter (`tsc --noUnusedLocals` is clean).
- Dry-run summary said "nothing to do" for an unstarted workflow; now "not started".

## Concerns / open items

- **Tool-name regexes are provisional by design.** `SQL_TOOL`, `SHELL_TOOL`, `WRITE_TOOL` and the destructive lists must be tightened after the first live run logs real `toolName` values (Task 16). They are exported constants at the top of `policy.ts` for exactly that.
- **`.github/agents/*.agent.md` (Task 12) and `samples/*/canned/` (Task 13) do not exist on this branch**, so `loadAgents` and `MockRunner` are tested against fixtures that follow the documented layouts. When those tasks land, Step 5's integration run is the thing that proves the real artifacts match.
- **`CopilotRunner` has no automated test** — it cannot be exercised without a live CLI/model. It is type-checked against the real SDK types and is deliberately thin: build hooks, create a session, `sendAndWait`, classify the failure, disconnect.
- A session that completes but recorded a hook deny is reported as `denied` (the spec calls an agent leaving its lane a prompt bug); a session that completes after an internal 429 is reported as success.

---

# Fix round 1 — `orchestrator/policy.ts` rewritten fail-closed

Worktree `.worktrees/task-15-fix`, branch `wt/task-15-fix` (base `402d759`). Commits:
`f4a49bf` wip: fail-closed permission policy · `0d811c9` wip: hooks pass the repo root to the policy and audit unrecognized tools · plus the final commit below.

## RED first — every reproduction the reviewer sent

All ten new policy tests were written before any implementation change and failed against the old policy:

```
$ node --experimental-strip-types --test orchestrator/test/policy.test.ts
ok 1..11   (the brief's five tests and my earlier additions, unchanged)
not ok 12 - CRITICAL 1: directory traversal cannot reach a forbidden area from any lane
not ok 13 - CRITICAL 1: separators, case and absolute paths are normalized before any rule
not ok 14 - CRITICAL 1: every path-like argument is judged, and a write with none is refused
not ok 16 - CRITICAL 2: validator SQL cannot reach production through comments, literals or extra statements
not ok 17 - CRITICAL 2: unqualified and stage-shaped targets are refused, sandbox ones are not
not ok 18 - CRITICAL 3: a shell metacharacter or an interpreter flag ends the command
not ok 19 - CRITICAL 3: only the role's own script invocations are allowed, in one exact shape
not ok 20 - IMPORTANT 4: an unrecognized tool is denied, and the read allow-list is not
not ok 21 - IMPORTANT 5: lane components are real ids, and lane files are exact
not ok 22 - hostile inputs are denied for every role
# tests 22 / # pass 12 / # fail 10
```
(Test 15, "the repo's own machinery is off limits", passed already through the old lane check and stays as a regression guard.)

GREEN after the rewrite: `# tests 22 / # pass 22 / # fail 0` for the policy file, and for the whole suite

```
$ fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts
# tests 67 / # pass 67 / # fail 0
$ fnm exec --using=22 node.exe ../../node_modules/typescript/bin/tsc --noEmit -p . --noUnusedLocals --noUnusedParameters
(clean)
```
The brief's five policy tests are byte-for-byte unchanged and still pass; no fake or stage test needed a new tool name (the stage tests never reach the policy, and `hooks.test.ts` only ever used recognized tool names).

## What changed, ruling by ruling

**A · Paths.** New exported pure `normalizeToolPath(raw, root?) → {ok, path} | {ok:false, reason}`: `\`→`/`, leading `./` stripped, absolute (drive letter, leading `/`, UNC) accepted only when it is inside the `root` the caller supplies (else `path outside the repository`), `path.posix.normalize` for `.`/`..`, any result that is `..`/`../…`/still absolute → `path escapes the repository`, result lower-cased and root-relative. `collectPathArgs` walks the arg object (and arrays of edits) and returns every value under a path-like key (`path`, `file_path`, `filePath`, `target`, `destination`, `old_path`/`new_path`, …); **a write tool with no recognizable path is denied**. Lanes are fully anchored `^…$` with `[a-z0-9][a-z0-9_-]*` id components (never `[^/]+`) and a component pattern that cannot match `..`. `FORBIDDEN_WRITES` now covers `golden/` at any depth, all of `cookbook/` (proposals included — the curator is not an orchestrated role), `scripts/parse.py`, `scripts/` outside `scripts/parsers/ext/`, `.github/`, `.git/`, `node_modules/`, `.venv/`, `orchestrator/`, `orchestrate.ts`, `orchestrator.config.json`, `config.json`, `docs/spec/` and `samples/`. Rule 1 (other workflows) now runs twice: on the raw argument text (any tool, any key) and again on every normalized path, so `workflows/wf_0001/../wf_0002/x` is caught.

**B · SQL.** `stripSqlNoise` removes `--` and `/* */` comments and replaces every string literal (doubled `''` handled) before any rule; quoted identifiers are kept. Then: `;` followed by anything but whitespace → `multiple statements in one call`; `DROP|TRUNCATE|GRANT|REVOKE|USE|ALTER (ACCOUNT|USER|ROLE)` → `destructive SQL` even inside MIG schemas; every qualified reference (`ident(.ident){1,2}`, quoted idents and `@stage` references included) must have `MIG_WORK`, `MIG_GOLDEN` or `INFORMATION_SCHEMA` as its schema part, and the denial names the offender; a leading mutating/executing verb (or an `INTO`/`CALL` object) whose target is unqualified → `unqualified target`. Intake: single statement, must start `SELECT|SHOW|DESC|DESCRIBE`, every qualified reference in `INFORMATION_SCHEMA`. No SQL under `sql`/`query`/`statement`/`text` → denied.

**C · Shell.** `;` `|` `&` `<` `>` backtick `$(` `${` newline or CR anywhere → `shell metacharacter`. Interpreter flags `-c`, `-e`, `-Command`, `-EncodedCommand`, `-File` → denied. Then exactly one shape: a read-only listing command (`ls dir cat type Get-Content Get-ChildItem git status|diff|log`) whose every non-flag token passes ruling A and rule 1; or `<python> scripts/<the role's own script>.py <args>` where the script token is matched **literally** (so `scripts/../scripts/compile_check.py` is refused) and every argument is a flag or a plain token with no `..`; or, for parser-recovery only, `<python> -m pytest tests/parser_corpus[/…] [-q]`. The destructive-command list runs before the shape check so the brief's `/destructive/` messages still hold.

**D · Default deny.** An unclassified tool name is denied with `unrecognized tool: <name>`. The provisional read/planning allow-list is the exported `READ_TOOLS` (the 17 names from the ruling); those tools are still subject to rule 1 and path normalization.

**E · Hooks.** `onPreToolUse` now calls `decide(role, wf.id, toolName, toolArgs, segment, env.root)` and writes an extra `{"ev":"unrecognized-tool","tool":…}` audit line on every default-deny, so Task 16 can extend `READ_TOOLS` from evidence. `decide`'s signature gained an optional sixth parameter only; all existing call shapes still compile.

## New tests

`orchestrator/test/policy.test.ts` grew from 11 to 17 tests (67 across the suite): each of the reviewer's reproductions as its own assertion; Windows and mixed-separator variants of every traversal; absolute paths inside the root (allowed, made lane-relative) and outside it (denied); `.GitHub/Agents/x.md` case folding; `review.json.bak` and `xreview.json` denied for the reviewer; a write with no path denied; the repo's own machinery denied for every role; unqualified/stage/multi-statement/quoted-identifier SQL; metacharacter, interpreter-flag and literal-script-token shell cases; every allow-listed read tool allowed in-workflow and denied cross-workflow; and a property-style sweep of 8 roles × 6 traversal prefixes × 6 forbidden targets = 288 hostile inputs, all denied. `orchestrator/test/hooks.test.ts` gained a case proving the hook supplies the root (absolute path inside → allow, `C:/Windows/...` → deny) and audits `unrecognized-tool`.

## One ruling has a cost I could not remove — please decide

Ruling B as written ("the SCHEMA part of **every** qualified reference must be a sandbox schema") also matches alias- and table-qualified **columns**, which have the same `a.b` shape. Verified against the implemented policy:

```
deny  SELECT a.ACCT FROM MIG_WORK.T a                                   → sandbox … (a.ACCT)
deny  SELECT MIG_WORK.T.ACCT FROM MIG_WORK.T                            → sandbox … (MIG_WORK.T.ACCT)
deny  SELECT l.ACCT FROM MIG_WORK.L l JOIN MIG_WORK.R r ON l.ACCT=r.ACCT → sandbox … (l.ACCT)
allow SELECT ACCT, AMOUNT FROM MIG_WORK.WF0001_SEG_01_OUT
allow CALL MIG_WORK.WF0001_SEG_01('A','B','C','D','r')
```

So a validator session that deploys or inspects a join-bearing procedure through the SQL tool will be denied, and the stage will escalate to `NEEDS_HUMAN` — loud and safe, but likely to bite in Task 16's live run. I implemented the ruling as written rather than around it, and pinned the behaviour in a test named "known cost of ruling B" so any refinement changes that test. The refinement I would propose: apply the schema check only to references in **object position** (after `FROM`, `JOIN`, `INTO`, `USING`, `TABLE`, `CALL`, `COPY INTO`, `MERGE INTO`, `UPDATE`, `DELETE FROM`, `PROCEDURE`, `VIEW`, `STAGE`), keeping everything else in ruling B unchanged — that still denies all seven CRITICAL 2 reproductions (each one's production reference is in object position) while allowing alias-qualified columns. Say the word and I will make that change with the reproductions re-asserted.

Two smaller notes: `cookbook/proposals/` is denied for every orchestrated role per the ruling (only the non-orchestrated cookbook-curator would ever write there); and `python -m pytest` accepts only `-q` as an extra flag, per the ruling's `[-q]`.

## Ruling B final (accepted refinement) — implemented

Commits `79918e2` (policy + tests) and `d328bb2` (documentation), on top of the three above.

### RED first

The seven new/flipped SQL tests failed before the rewrite:

```
$ node --experimental-strip-types --test orchestrator/test/policy.test.ts
not ok 17 - CRITICAL 2: unqualified and stage-shaped targets are refused, sandbox ones are not
not ok 22 - ruling B: a qualified column is a column, not an object
not ok 23 - ruling B: object position covers joins, commas, clones, stages and qualified calls
not ok 24 - ruling B: a name the policy cannot resolve is refused
not ok 25 - ruling B4: a procedure body is checked statement by statement
not ok 26 - ruling B5: a CALL must match the contract signature and name MIG_ schemas
not ok 27 - ruling B6: intake may alias the catalog
# tests 28 / # pass 21 / # fail 7
```

GREEN after implementing B1–B6: `# tests 28 / # pass 28 / # fail 0` for the policy file, and for the whole suite

```
$ fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts
# tests 72 / # pass 72 / # fail 0
$ fnm exec --using=22 node.exe ../../node_modules/typescript/bin/tsc --noEmit -p . --noUnusedLocals --noUnusedParameters
(clean)
```

All seven CRITICAL-2 reproductions still deny, and all the round-1 path/shell/default-deny tests are untouched.

### What the SQL check does now

A small tokenizer (qualified names with quoted identifiers, `@stage`, punctuation) over the comment- and literal-stripped statement, then:

- **B1 object position** — a name is an object after `FROM`, `JOIN`, `INTO`, `USING`, `TABLE`, `UPDATE`, `CALL`, `PROCEDURE`, `FUNCTION`, `VIEW`, `STAGE`, `CLONE`, `LIKE`, `SWAP WITH`, as a stage `@…`, or as a qualified name applied to `(`. Objects must be schema-qualified in `MIG_WORK`/`MIG_GOLDEN`/`INFORMATION_SCHEMA`, except an unqualified CTE declared in the same statement, allowed in `FROM`/`JOIN` only; anything else unqualified is `unqualified target`. A comma at FROM-clause depth is refused with `comma join not supported by the policy; use JOIN` (the ruling's sanctioned fallback). Introducers inside a *function call's* parentheses are skipped so `EXTRACT(YEAR FROM POSTED_AT)` is not read as a FROM clause — the paren is a call only when the preceding token is a non-keyword name.
- **B2 elsewhere** — a qualified name is a column reference, allowed when its first component is a declared alias, a declared CTE, the table-name part of a declared sandbox object, or a sandbox schema; otherwise `unexplained qualified reference: <name>`. `a.ACCT`, `l.ACCT = r.ACCT` and `MIG_WORK.T.ACCT` are legal again; `FINANCE.RAW.GL.AMOUNT` is not.
- **B3 dynamic names** — `IDENTIFIER(`, `EXECUTE IMMEDIATE` and `TABLE(` over a non-sandbox function are denied.
- **B4 procedure bodies** — a `$$ … $$` body is split into statements (leading `BEGIN`/trailing `END` removed, `RETURN …` and `ALTER SESSION SET …` skipped) and each one goes through B1–B3 with the two contract-C4 `IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.NAME')` / `:TGT_…` forms substituted for a sandbox-qualified placeholder before literal stripping. The header is checked as its own statement and the procedure name must be `MIG_WORK`-qualified. The test deploys a realistic C4 procedure (CTEs, a two-alias JOIN, `QUALIFY`, a `MERGE INTO IDENTIFIER(:TGT_DB || …) t USING MIG_WORK.WF0003_SEG_02_OUT s ON t.ACCT = s.ACCT`) and it is allowed; swapping one joined table for `FINANCE.RAW.REGIONS`, or one `IDENTIFIER('FINANCE.RAW.GL')`, or the procedure's own schema, denies it.
- **B5 CALL** — the five-argument contract shape with literal `MIG_` schemas in arguments 2 and 4; any other argument count is `CALL does not match the contract signature`.
- **B6 intake** — the same object-position logic with `INFORMATION_SCHEMA` as the only allowed schema, so `SELECT c.COLUMN_NAME FROM ANALYTICS.INFORMATION_SCHEMA.COLUMNS c` is allowed.

### One brief assertion had to change (B5 conflict) — please confirm

Ruling B5 invalidates a line in the **brief's own** policy test #4:

```ts
allow(decide("validator", "wf_0001", "snowflake_query", { sql: "CALL MIG_WORK.WF0001_SEG_01('A','B','C','D','r')" }));
```

`'B'` and `'D'` are the SRC_SCHEMA and TGT_SCHEMA arguments and do not start with `MIG_`, so B5 denies it. I kept the assertion's intent (the validator may CALL a sandbox procedure) and changed only the argument list to the realistic C4 call from your own test list — `('MIGDB','MIG_GOLDEN_WF0001_NORMAL','MIGDB','MIG_WORK','r1')` — with a comment at the line explaining why. Say the word if you would rather relax B5 for placeholder arguments instead; every other brief assertion is still byte-for-byte unchanged.

### Documented limits

`policy.ts`'s header and the new `orchestrator/POLICY.md` both state that this hook is **defence in depth, not a sandbox**: a conservative textual check with no SQL or shell parser, which will miss exotic constructs and deny legitimate ones; the primary boundary is the `MIGRATION_AGENT` Snowflake role (program spec §11.1: `USAGE` on `MIG_WORK`/`MIG_GOLDEN`, read on `INFORMATION_SCHEMA`, nothing on production), with procedures created `EXECUTE AS CALLER` so they cannot exceed it, plus the spec's container for shell tools and `MIGRATION_CI` as the only deployer. `POLICY.md` lists the known limitations: no SQL parser; introducers skipped inside function-call parentheses (and what still catches names there); unqualified names outside object position unchecked; comma joins refused; `CALL` limited to the C4 shape; stage references must be schema-qualified; the keyword list is finite; tool names matched by regex with a provisional read allow-list; only path-like argument keys normalized; case-insensitive path comparison; and that the policy cannot police what an allowed tool then does. It also records that nothing here has run against real Snowflake or Alteryx.

---

# Fix round 2 — the re-reviewer's CRITICAL and two Important items

Worktree `.worktrees/task-15-fix2`, branch `wt/task-15-fix2` (base `be26ccc`). Commits:
`e25e7e8` wip: listing-command flag allow-lists, fail-closed arg depth, sandbox database rule ·
`bdcd619` wip: `policy.sandboxDatabases` flows from `orchestrator.config.json` through the hooks ·
`51b18bd` docs: listing commands are not side-effect-free, and what the database rule can see ·
`6a48837` fix: accept Windows-style listing paths, name the allowed git subcommands.

## RED first

```
$ node --experimental-strip-types --test orchestrator/test/policy.test.ts
not ok 27 - ruling B6: intake may alias the catalog, in a sandbox database
not ok 28 - CRITICAL: a listing command cannot write through one of its own flags
not ok 29 - listing flags are an allow-list, and the dangerous ones are named
not ok 30 - every listing command still works with its own flags
not ok 32 - IMPORTANT A: arguments nested deeper than the walker are denied, not dropped
not ok 33 - IMPORTANT B: a three-part name is judged on its database too
not ok 34 - IMPORTANT B: a CALL names sandbox databases as well as MIG_ schemas
not ok 36 - hostile listing flags are denied for every role and every listing command
# tests 36 / # pass 28 / # fail 8
```
(Test 31, "listing arguments are still paths in this workflow", passed already through rule 1 and stays as a regression guard.)

GREEN, whole suite and typecheck:

```
$ fnm exec --using=22 node.exe --experimental-strip-types --test orchestrator/test/*.test.ts
# tests 81 / # pass 81 / # fail 0
$ fnm exec --using=22 node.exe ../../node_modules/typescript/bin/tsc --noEmit -p . --noUnusedLocals --noUnusedParameters
(clean)
```

## CRITICAL — listing commands get per-command flag allow-lists

`READ_ONLY_SHELL` is no longer a list of word shapes but a list of `ListingCommand` records, each
with its own flag allow-list, its numeric flags and whether a bare `-<digits>` is valid. Any token
starting with `-` (or `/` for `dir`) that is not on that command's list is denied with
`flag not allowed for <command>: <flag>`; `--name=value` is judged by its name and its value is
judged as an argument; numeric flags must be given digits; `git` accepts only `status`, `diff` and
`log`, and anything else is `git <sub> is not a listing command`. Every remaining argument must
pass the plain-token charset (backslashes allowed so Windows paths work), contain no `..`, and —
when it looks like a path or follows a bare `--` — pass path normalization and the other-workflow
rule. All five `--output` reproductions now deny, as do `-o`, `-O`, `--ext-diff`, `--textconv`,
`--no-index`, `--git-dir`, `--work-tree`, `--exec-path`, `--upload-pack`,
`--open-files-in-pager` (and `-c`/`-C`, which the interpreter-flag rule catches first). The allow
side is asserted too: `ls -la`, `dir /b`, `cat`, `type`, `Get-Content -Path … -TotalCount 20`,
`Get-ChildItem -Path … -Recurse -Depth 2`, `git status --porcelain`, `git diff --stat` (the brief's
own assertion, unchanged), `git log --oneline -n 5`, `git log -5 --no-color`. The hostile sweep now
also runs 8 roles × 9 listing commands × 6 flag injections = 432 calls, all denied.

## IMPORTANT A — bounds deny instead of dropping

`collectPathArgs` returns `{ paths, tooDeep }`; hitting `MAX_ARG_DEPTH` sets `tooDeep` and `decide`
answers `arguments too deeply nested to judge`. The reviewer's seven-level reproduction denies, and
a normal `{edits:[{path:…}]}` still allows. That is the only bounded walk in the policy; the string
truncations that remain are in audit output (hooks.ts), not in any decision.

## IMPORTANT B — the database component

`SANDBOX_DATABASES` (default `["MIGDB"]`) is new, overridable from `orchestrator.config.json` as
`policy.sandboxDatabases`, merged by `loadConfig`, carried on `OrchestratorConfig.policy`, and
passed into `decide` by `hooksFor` next to `root` through a new optional `PolicyOptions` parameter
(the existing five- and six-argument call shapes, including every brief assertion, are unchanged).
A three-part object reference must now have both its schema in the sandbox set and its database in
`sandboxDatabases`: `FINANCE.MIG_WORK.GL_LEDGER` and `ANALYTICS.MIG_WORK.X` deny,
`MIGDB.MIG_WORK.T` allows, and a two-part `MIG_WORK.T` still allows. B5 gained the same rule for
the `SRC_DB`/`TGT_DB` arguments of a `CALL`. Intake follows it: `MIGDB.INFORMATION_SCHEMA.COLUMNS`
allows, `ANALYTICS.INFORMATION_SCHEMA.COLUMNS` now denies, and `MIG_WORK.CATALOG_COLUMNS` (the
sanitized catalog copy) is allowed through a named object allow-list rather than by opening
`MIG_WORK` to intake. A hooks test proves the plumbing end to end: with `sandboxDatabases: ["ALTDB"]`
in the config, `ALTDB.MIG_WORK.T` is allowed and `MIGDB.MIG_WORK.T` is denied by the hook itself.

`POLICY.md` now states that a two-part name carries no database, resolves against the session's
current database and therefore relies entirely on the `MIGRATION_AGENT` grants.

## One round-1 assertion of mine had to be re-worded

My own round-1 test `deny(CALL … ('MIGDB','RAW','ANALYTICS','CURATED','r1'), /MIG_/)` now fails its
regex because the denial reports the offending `TGT_DB` (`ANALYTICS`) before it reaches the schema
argument, and `MIGDB` contains no underscore. I split it into two assertions — a `MIG_ schema`
denial (`'MIGDB','RAW','MIGDB','CURATED'`) and a `MIG_ schema|sandbox database` one for the
original input. No brief assertion changed in this round; the only brief line ever amended remains
the already-adjudicated CALL placeholder from round 1.

## Documentation

The `policy.ts` header no longer implies the listing branch is safe by shape: it states that a
"read-only" listing command is **not** side-effect-free, names `git diff --output=<path>` as the
case that proved it, and says flags are an allow-list per command. `POLICY.md` gained the full
per-command flag table, the `git`-subcommand restriction, the database rule (three-part checked,
two-part not), the `-Include`/`-Exclude` decision (left out: nothing needs them and default-deny is
the rule), the case-insensitive flag comparison, and a limitation entry saying bounds deny rather
than skip.

