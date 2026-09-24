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
  LET ORDERS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ORDERS';
  CREATE OR REPLACE TRANSIENT TABLE MIG_WORK.WF0001_SEG_01_OUT AS
  WITH
  -- tool 1: Input Data (orders.yxdb)
  t1_input AS (SELECT … FROM IDENTIFIER(:ORDERS_SRC)),
  …
  SELECT … FROM t9_select;
  RETURN 'OK';
END;
$$;
```

The body is one `LET` per mapped table, building its name, then a linear list of plain SQL
statements. The runner extracts the `$$` body, splits statements, evaluates each `LET` from the
bound parameters (a `LET` never reaches the backend), substitutes binds and `LET` variables,
resolves `IDENTIFIER(:<var>)`, records and skips `ALTER SESSION`, rewrites `DB.SCHEMA.T` to a
DuckDB schema `DB__SCHEMA`, transpiles, and executes.

**Table references use Snowflake's documented `IDENTIFIER` form (amended 2026-09-23, phase-2 Task
C4V).** Snowflake's documentation (docs.snowflake.com/en/sql-reference/identifier-literal) gives
`IDENTIFIER( { string_literal | session_variable | bind_variable | snowflake_scripting_variable } )`
-- a single value, not an expression. Until this amendment every procedure here wrote the name as
an expression inside the call, `IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.ORDERS')`, which that
grammar does not include and which a real account would likely refuse to compile. Every procedure
now builds the name first -- `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA ||
'.<LOGICAL>';` for a source, `LET <LOGICAL>_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA ||
'.<LOGICAL>';` for a final target, one per distinct table -- and references it only as
`IDENTIFIER(:<LOGICAL>_SRC)` or `IDENTIFIER(:<LOGICAL>_TGT)`. Inside the `LET` the procedure's
arguments are named without a colon: Snowflake's Scripting documentation
(docs.snowflake.com/en/developer-guide/snowflake-scripting/variables) uses the colon to bind a
variable inside a SQL statement, says a variable in an expression or a Scripting element needs
none, and treats a procedure argument like a declared variable -- so the `LET` has no colon and
`IDENTIFIER(:<LOGICAL>_SRC)`, inside a SQL statement, keeps it (coordinator ruling, 2026-09-23).
`compile_check.py` refuses anything else (`c4:let_form` -- a colon inside a `LET` included --
and `c4:identifier_expression`), and so does the orchestrator's SQL policy. This is
the DOCUMENTED form, chosen because the old one is not in Snowflake's grammar; it has not run on
Snowflake either (nothing here has), and the first real-account run -- the hand-off's verification
ladder -- confirms it. The program spec (`docs/spec/**`) never shows `IDENTIFIER`, so this amends
our own contract C4 (the 2026-09-18 plan's), not the program spec.

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
    *Amended by live hardening, Task L11:* the same path compares a stream whose declared keys
    repeat in the golden (expected) rows. Since Task L3 the analyzer declares keys as a judgment,
    and a real Alteryx output may legitimately repeat an id (the live run's `3_F` held two identical
    rows with `ORDER_ID` 103 in its `edge` set); the keyed path called that a `GOLDEN_DATA` defect
    with `needs_human` and parked a correct translation. Now the stream is compared exactly as if it
    declared no keys, the keyless verdict stands, and the report adds the advisory
    `checks.keys_not_unique` (`{"keys", "duplicate_groups", "examples"}`: detail, never a failure,
    never `needs_human` by itself) and a note in `normalizations_applied` that the contract's keys
    should be revisited. Keys unique in expected but repeated in actual stay a `LOGIC` cluster;
    `GOLDEN_DATA`'s other cause (a NULL in a `NOT NULL` column of the golden data) is unchanged.
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
12. **"Tool denied by policy → abort stage" applies at once to severe attempts, and to other
    attempted actions and blocked reads beyond a small budget** (live hardening, Task L1; amended by
    Task L6, whose text below supersedes L1's rule that any attempted action aborts the stage).
    Program spec `01-copilot-setup.md` §5 routes every denied tool to "log, abort stage,
    NEEDS_HUMAN" ("an agent trying to leave its lane is a prompt bug"). Every denial now carries a
    class (`orchestrator/policy.ts`'s `denialClass`), and every refused call is still refused,
    written to `audit.jsonl` first with its reason and class, and counted in
    `manifest.metrics.<role>` (`readDenials`, `actDenials`, `severeDenials`).
    - A `severe` denial (Task L6) is a refused call that tried to reach outside the session's
      workflow or the sandbox, or to do damage: any SQL-tool denial (not the SDK's own `sql` todo
      store); a reason that names another workflow, or a Windows alias that could (a broad read that
      could reach one is a read, not severe); a path that starts at the home directory (`~`); a
      write -- by a write tool, an `apply_patch` header, a shell command or redirection, or a
      script's output flag -- to the own workflow's `golden/` or `audit.jsonl`, into another
      workflow, into the pipeline's own trees or top-level files, or out of the repository (a new
      scratch file at the run root is an `act`, fix round 2); a destructive command or a
      recursive/forced delete; a script given `--root` or a Snowflake-account flag; an external
      location; a shell command that names a network tool, git's remote subcommands, a one-liner
      using a network module, a UNC share, a package installer or a credential store; an
      unrecognised tool whose name says it reaches the network or deletes; two command keys on a
      shell call, or a path key beside a search's `paths`. A search's own literal pattern, a write's
      content and a planning tool's text are text, never a reach (Task L6 fix rounds 1 and 2). One
      aborts the stage exactly as the spec says, whatever else ended the session (fix round 2: a
      severe attempt followed by a timeout or a rate limit is still `denied`, and never kept by Task
      L7's timeout rule, item 14).
    - An `act` denial is every other attempted action: an interpreter one-liner, a command the role
      may not run, a chained command, a write inside the own workflow but outside the role's lane, an
      unrecognised tool. Since Task L6 these are budgeted: more than `budgets.maxActDenialsPerSession`
      (default 20 since its fix round 1, like reads; it was 3; 0 restores the spec's rule) in one
      session aborts the stage as `denied`, with the reason `act-denials: <n> over the budget of <m>`.
      The budgets catch a session that thrashes; the stage's deterministic checks judge the output.
    - A `read` denial is a refused call of a tool that only reads (`view`, `grep`, `glob`, `read`,
      `read_file`, `ls`, `list_directory`, `search`, `search_files`, `find`, the SDK's
      `read_`/`list_` shell-session tools), a refused git listing with only its own flags, or a shell
      command whose every statement starts with a read-only cmdlet and holds no token that can run or
      write anything -- for the PowerShell tool with statements split only on separators outside
      quotes (Task L6). More than `budgets.maxReadDenialsPerSession` (default 20) aborts the stage,
      with the reason `read-denials: <n> over the budget of <m>`.

    Within both budgets and with no severe denial, the session's outcome is decided by its outputs
    and the stage's checks, as if nothing had been denied. Why: a refused call has no effect, and
    every output is verified afterwards by the stage's own checks and scripts. Live evidence: the
    three phase-2 intake sessions (`docs/live-smoke-test.md`, "Third live test") each wrote
    `intake/plan.md` and `mappings.yaml` and were then parked `denied`, 54 of 185 tool calls in all,
    most of them reads: a mangled absolute run-root path, a sibling workflow's file, a PowerShell
    listing, the SDK's own spill file (Task L1). The end-to-end run of wf_0001 through every stage
    (Task L6): its analyzer ran 112 calls, and its contract passed `contract_check.py` and
    `check_seams.py`; it still parked `denied` on three `act` denials -- a `Select-String` search
    misread as an action because of a `|` inside its quoted pattern, an interpreter one-liner, and an
    edit of intake's `open_questions.md` inside its own workflow. A later live translator on wf_0001
    wrote SQL that passed all four golden sets and made 33 blocked attempts, about twenty of them
    `stop_powershell` on a shell it had started itself -- which is why `stop_*` is allowed and the act
    budget is 20 (Task L6 fix round 1). The same run's translator wrote two throwaway DuckDB probes
    at the run root (`_diag.py`, `diag.py`); a new scratch file there is an `act`, while a write
    into the pipeline's own files is severe (fix round 2). What is refused is unchanged, except as
    those fix rounds ruled: `stop_*` is allowed; Windows path aliases (also in script arguments),
    decoy search keys and a leading `~` are refused; a script's path flags (`compare.py --out`/`--db`)
    must stay inside the own workflow and the role's lane; and the SDK's `sql` todo store is refused
    with its own reason. Otherwise only what a refusal costs the session changes.
13. **The analyzer no longer writes a contract's mechanical fields; code does, and checks the whole
    contract** (live hardening, Task L3; `docs/reference/contracts.md`). Program spec
    `01-copilot-setup.md`'s analyzer is told to "write contract.json: inputs (table FQN …; columns,
    types, nullability, keys), output schema, row relation, ordering keys, tolerance overrides". Now
    `scripts/contract_scaffold.py` derives every field that follows from files the pipeline already
    wrote -- `workflow`, `segment`, `target`, every input's and output's identity, table, write mode,
    and every column's name and type -- pre-fills it before the analyzer and re-applies it after, as
    the authority; the analyzer decides row relation, ordering, tolerances, normalizations, parity
    risks, nullability and keys, and may still only lower a target. `scripts/contract_check.py` then
    gates every contract (mechanical agreement, the shape of every field a script reads, judgment
    well-formed); a refusal parks analyze as `contract: <first problem>` after one retry. The spec's
    analyzer rule "Never write SQL or execute anything" also gains two scripts the analyzer may run on
    its own workflow: `contract_check.py` and `check_seams.py`. Why: in the fourth live test the
    analyzer wrote six mechanical fields wrong (an invented stream name, no `tool_id`, no
    `write_mode`, sizeless string types, no `workflow`, an `ordering` shape nothing reads), and
    nothing checked any of them before golden, translate and validate used the contract. All but one
    are now caught or re-derived: an unsized `VARCHAR` for a variable-length string is accepted as the
    type map's own spelling (a coordinator ruling; `wf_0007`'s committed contracts use it), so only a
    fixed-width string that lost its size is refused.
14. **A retry after a missing output is told why** (live hardening, Task L3; amended by Task L7, R2,
    by Task L7 fix round 1, R-a/R-b, and by Task L7 fix round 2, IMP-1/IMP-2/L7-m1). Program spec
    `01-copilot-setup.md` §5: "agent output file missing after session | retry once with the same
    prompt". The one retry is still one, but its prompt is the same task plus a fixed sentence and, in
    a data fence (escaped, redacted, bounded, never an instruction), the reason the failed verify
    recorded and the checker's report (`orchestrator/feedback.ts`). Why: the same prompt twice gives a
    model no way to fix the one thing the orchestrator refused. A timeout's retry, where no check
    failed, is still the same prompt. Task L7 (R2) narrows "no check failed": a session that ends
    `timeout` now has the stage's own `verify` run against it first, and a session that timed out but
    left an output that passes it is kept, not retried at all -- live evidence (`task-L7-brief.md`)
    showed a translator's compiled, once-validated `proc.sql` thrown away and retranslated from
    scratch after its session ran out of time reading source to debug a genuine semantics diff, work
    the fixer loop (not a from-scratch retry) is for.

    Fix round 1 (R-a) also has `CopilotRunner.run` call `session.abort()` on a timeout, awaited
    (bounded, so a hung `abort()` cannot itself hang `run`) BEFORE returning to `runAgent` -- the
    SDK's own docs say `sendAndWait`'s timeout "does not abort in-flight agent work" (session.d.ts
    ~154), so without this the turn judged by R2's `verify` could still be writing to the very file
    that `verify` is about to check, or to `compile_check`/the validator after it, whichever runs
    next. A failed or slow `abort()` is logged, never thrown; `disconnect()` still runs regardless.
    **Fix round 2 (IMP-1) corrects where this sat**: fix round 1 put the abort call AFTER L6 fix round
    2's severe-denial check, which returns at once -- so the one turn most worth stopping, one that
    already made a severe attempt, was exactly the one left running. The abort now runs on every
    timeout, severe or not, before any classification; grading the denials (severe, or (L7-m2) an
    over-budget act/read count) still decides the final result afterward, unchanged. **Fix round 2
    (L7-m1)** also waits for the session to actually report `session.idle` with `IdleData.aborted` true
    (generated/session-events.d.ts ~1417) after `abort()` itself resolves, within the same bound --
    `abort()` only means "acknowledged" (session.d.ts ~282), not that a shell command or an attached
    sub-agent task has actually stopped.

    Fix round 1 (R-b, review I2) also narrows R2's own "passes it": for the translator/fixer calls in
    `migrateSegment`/`migrateDbt`, `verify` alone is existence, and the translator's iteration 0
    already made that true for every later fixer attempt on the same segment -- a fixer, or a resumed
    translator, that timed out having changed NOTHING was still logged "kept". **Fix round 2 (IMP-2)
    replaces fix round 1's mechanism**: the first version judged "changed" by mtime (a small clock
    tolerance against the attempt's own start), which housekeeping the orchestrator or the agent
    itself writes near the same instant could satisfy without a real edit -- `dbt/compile_check.json`
    (written right before a fixer that follows a compile failure, or by the fixer's own permitted run
    of it) and `dbt/fix_log.md` (every dbt fixer logs every iteration there, per `fixer.agent.md`) both
    demonstrated this live. The two call sites now snapshot the stage's own work files by CONTENT
    before the attempt runs (the segment's own `proc.sql`/`proc.py`; for dbt, every model under
    `dbt/models/` plus `models/sources.yml`/`models/schema.yml`, recursively) and require that snapshot
    to differ afterward -- no clock or tolerance at all, so nothing outside the translation's own lane,
    however close in time, can be mistaken for a real edit. Every other role keeps existence alone as
    its whole check (fix round 1 review, M4). Both conditions are added beside `runAgent`'s existing
    timeout-keep check, not a rewrite of it, on the same round's instruction that a concurrent fix
    (Task L6, a no-severe-denial guard on the same block, for a different Critical the same review
    raised) must not be restructured around.

    A session that had a severe denial is never kept: it parks `denied` whatever ended it (Task L6 fix
    round 2, I3) -- `runAgent` itself also checks `result.severeDenials`, independent of what
    `CopilotRunner` reports, so this holds for any runner.
15. **The translator and the fixer run the validator on their own work** (live hardening, Task L4).
    Program spec `01-copilot-setup.md` Part A §5 lets only the validator and intake execute SQL, and
    has the translator and the fixer write only their segment's procedure files. Both may now also run
    `scripts/validate_segment.py` / `validate_snowpark.py <wf> <own segment>`, and in dbt scope
    `validate_dbt.py <wf>`, with `--set <name>` the only flag (`orchestrator/policy.ts`'s
    `SELF_VALIDATION_SCRIPTS`), `--set=<name>` accepted too, and only in the shell tool's synchronous
    mode. The script runs the procedure on a local double (DuckDB, the Snowpark Local Testing
    Framework, dbt-duckdb) and never on Snowflake: the backend flags stay refused to every agent. The
    double runs on the host, so the agent's own code runs there -- inside a sandbox for each target.
    A Snowpark `proc.py` is checked against the Snowpark rules before it is imported and never
    imported when it breaks one (fix round 1, I1). Both gates are **allow-lists** (fix round 5), after
    the deny-lists were defeated three times (a `sys.modules` reach, a `sqlite3` file write, and
    `pd.eval(..., engine="python")` reaching `_winapi` native calls): the static gate
    (`lib.snowpark_rules`) accepts only the Snowpark/pandas surface the benign corpus uses -- imports
    from a fixed list, `pd.`/`np.` attributes from a per-module allow-list (so `pd.eval`/`pd.read_csv`/
    `np.load` never resolve), and method calls whose name is on the Snowpark DataFrame/Column plus
    pandas carry-over allow-list (so `.eval`/`.query`/`.pipe`/every `to_*` writer/`read_*` reader are
    refused), with `engine="python"` refused outright. The module then runs in a **child process**
    under a `sys.addaudithook` armed before it is imported (`lib.snowpark_sandbox`) that is
    **default-deny**: it allows exactly the audit events a benign procedure records
    (`lib/sandbox_events.json`, re-recorded by `scripts/dev/record_sandbox_events.py`) -- keeping the
    argument checks that hold `open` inside a per-run temp directory (reads also under the interpreter's
    own installation), `import` off the blocked modules and `os.*` path operations inside the temp
    directory -- and refuses every other event, whole native families (`_winapi.*`, `winreg.*`,
    `ctypes.*`, `socket.*`, `subprocess.*`, `sqlite3.*`, `msvcrt.*`, `_wmi.*`) outright. A refused
    event is that segment's FAIL, naming the event. The child (fix round 3) runs under a wall-clock
    timeout (600 s by default, `MIG_SNOWPARK_SANDBOX_TIMEOUT` or `validate_snowpark.py
    --sandbox-timeout`; on expiry its process tree is killed and the segment FAILs `sandbox: timeout`),
    in a **minimal environment** (only `PATH`/`SYSTEMROOT` and the temp variables, never `SNOWFLAKE_*`,
    tokens or the real user profile), reads no workflow file at all once the hook is armed (the golden
    data, `golden/outputs` included, is loaded before arming and is not a read root afterward), and
    signs its result with a per-run nonce the parent verifies (`sandbox: unauthenticated result`
    otherwise), so neither a forged `result.json` nor a rewritten output file can fake a PASS. The
    child signals "agent code started" to the parent before it runs the module (fix round 5, E7): once
    that signal is seen, an exit with no authenticated result is a FAIL `sandbox: agent code exited`,
    never a re-spawn -- the one re-spawn is only for a failure before the signal. A memory cap is
    applied on POSIX (`RLIMIT_AS`) and not on Windows, which has no cheap per-process cap; the timeout
    bounds a runaway allocation there. Agent code shares the harness's process, so this is a defence,
    not a proof of isolation -- the container the spec describes is that proof.
    A SQL procedure runs on a DuckDB connection whose external (file, network, extension) access is
    switched off and locked before its first statement, after `compile_check.py` has refused any
    file-touching construct by name (`c4:external_access`, fix round 1, P); a dbt project stays inside
    the closed dbt surface. None of this is a substitute for running procedures only in Snowflake in
    production: it protects the machine the self-test runs on, nothing about Snowflake itself.
    It writes the segment's `validation*.json`, or for a dbt project every segment's and the chain
    report, plus `dbt/logs/validate_*.log` and `dbt_sandbox_*.duckdb` beside a dbt project (the next
    run removes the sandboxes; `dbt:surface` tolerates `logs/`). Those reports are not the verdict: the
    orchestrator deletes them before it dispatches the validator, whose own run is the only one the
    stage reads. The SQL tool is
    unchanged: the translator and the fixer still cannot execute SQL through it. Why: a live
    translator parked trying exactly this (`python scripts/validate_segment.py wf_0001 seg_01 --set
    normal`), and a failure it sees in its own session is repaired there instead of costing a fixer
    iteration and a validator session.

    Amended by Task L7 (R3): self-validation is bounded to one run per session, not "fix what fails
    within this session" without limit. Live evidence (`task-L7-brief.md`): a translator ran the
    validator once, saw a genuine translation-semantics FAIL (TOTAL_NET values, one filter-branch
    row), and spent the rest of its session reading 54 files -- many of them this pipeline's own
    `scripts/` source -- trying to debug the diff by reading the checker's implementation instead of
    handing off, until its session timed out. `translator.agent.md` / `fixer.agent.md` now run the
    validator for their own segment (or, for a dbt project, `validate_dbt.py`) at most once after
    `compile_check` passes; on a FAIL they write the evidence they have (the failing checks, the diff
    clusters, the suspect CTE) into `translation_notes.md` / `fix_log.md` and finish -- the
    orchestrator's own validator and the fixer iterations take it from there. `SESSION_RULE_LINES`
    (`orchestrator/stages.ts`) gained a fifth, general rule on the same evidence: no role may read
    this pipeline's own `scripts/` or `orchestrator/` source to debug a difference, only the
    validation report, the contract, the cookbook and `docs/reference/`.

16. **The local BYOK profile tells the SDK its own prompt budget** (live hardening, Task L7, R1;
    amended by Task L7 fix round 1, R-c/R-d). Program spec `01-copilot-setup.md` says nothing about a
    provider's context limits -- the SDK's own default compaction (triggered at its
    `InfiniteSessionConfig.backgroundCompactionThreshold`, 80%, of the resolved model's default
    `maxPromptTokens`) assumes it knows the model, which is true for a hosted Copilot model and false
    for a self-hosted one. Live evidence (`task-L7-brief.md`): a `llama-server` run with `-c 262144`
    and the default 4 parallel slots logged `Context size has been exceeded` at `n_tokens = 98490`,
    and the SDK logged `translator context compaction failed` -- omitting `-np` put this build into a
    single unified 262144-token cache shared by every slot (fix round 1 review, M1: this build's own
    help text ties the unified mode to `-np` being auto, i.e. omitted, not to the slot count), and the
    SDK's background compaction request (~140k tokens) landed beside the main conversation (~98k) in
    that same shared pool, exceeding it together, because nothing had told the SDK this model's actual
    budget. `orchestrator.config.json`'s `profiles.local.provider.maxPromptTokens` now reaches the
    SDK's `createSession` unchanged (`ProviderConfig.maxPromptTokens`,
    `node_modules/@github/copilot-sdk/dist/types.d.ts`), and `scripts/dev/serve_model.ps1` gained
    `-Parallel <n>` (always passed explicitly as `llama-server`'s `-np`, validated as a positive
    integer since fix round 1's review, M1). At `-np 2` this build reports, for `-c 262144`,
    `n_slots=2, n_ctx_slot=131072, kv_unified=false`: the context is SPLIT evenly, a fixed,
    independent 131072-token allocation per slot rather than one shared pool, so the session's own
    conversation and the concurrent compaction request each get their own budget instead of competing
    for one.

    Fix round 1 (R-c, R-d) makes the launcher's defaults and the committed number one pairing instead
    of two documents that happened to agree: `serve_model.ps1` now defaults to exactly `-Context
    262144 -Parallel 2 -CacheTypeK q4_0 -CacheTypeV q4_0` (the setup verified live on a 16 GB GPU),
    prints `n_ctx_slot = Context / Parallel` and a fitting `maxPromptTokens` (about 77% of
    `n_ctx_slot`) on every run, and -- reading `orchestrator.config.json` when it can (saying why not
    when it can't) -- warns if the configured value exceeds that fit. **Fix round 2 (L7-m3) corrects
    what the warning compared against**: fix round 1 compared the configured value to the raw
    `n_ctx_slot` (131072), not the fit (about 100925 at 77%) -- so the pre-fix-round-1 committed value,
    120000, printed "fits within n_ctx_slot (131072)" even though the wf_0007 probe below is exactly
    what proved 120000 overflows in practice. The committed value itself moved from 120000 to
    **100000** after that live dbt-workflow run (the wf_0007 probe) reached 131095 tokens against this
    same 131072-token slot with `maxPromptTokens: 120000` set and ZERO compactions. **Fix round 2
    (L7-m4) corrects the first-round reasoning for the number itself**: fix round 1 said the SDK's
    token estimate undercounts "by at least 9%", reading the gap only against `maxPromptTokens`
    itself. If the SDK's background-compaction (80%) and blocking (95%) thresholds are both relative
    to `maxPromptTokens`, as this repo assumes, a 131095-token request with zero compactions means the
    estimate was under roughly 114000 -- at least ~15% below the real count, or, if compaction never
    even started, as much as ~37% below it. 100000 is a conservative pull-back from 120000 under that
    range, not a value the 9% figure proved safe. The real check going forward is `CopilotRunner.run`'s
    own log line (the resolved `maxPromptTokens`/`maxOutputTokens`/model, once per session start)
    together with `assistant.usage`'s `peakInputTokens` (already recorded per role in
    `manifest.json.metrics`) on the next long session -- that ratio is the real number 77% only
    estimates. The hosted profile sets neither field: the SDK already knows Copilot's own models'
    limits.

17. **The orchestrator writes the first version of every translation: its skeleton** (live hardening,
    Task L8; `docs/reference/output-targets.md` §4). Program spec `01-copilot-setup.md` Part A §5
    ("Design notes on the roster"): "translator and fixer write `proc.sql` / `translation_notes.md` for
    one segment", and the
    translator prompt holds the DAG, the contract, the cookbook pages and the mappings. Now, before the
    translator's first session, `scripts/translation_scaffold.py` writes every line that follows from
    those files: a SQL procedure's C4 header, its session line, one documented `LET` per mapped source
    and target, one statement per contract output in the form its write mode needs (`c4:write_mode`,
    with a TODO statement for a PreSQL/PostSQL), the C3 work-table names, one CTE stub per data node in
    DAG order named as the canned procedures name them, the final `SELECT` and `RETURN 'OK'`; a Snowpark
    module's signature, reads and writes; a dbt project's `dbt_project.yml`, `profiles.yml`, `README.md`,
    `sources.yml`, `schema.yml`, model files and `config(...)` lines. Every place the transformation
    goes is exactly `TODO(scaffold)`, which `compile_check.py` refuses by name (`scaffold:todo`) on all
    three targets, so an unfilled skeleton never compiles. The translator replaces the TODO bodies and
    changes nothing else; the script never writes over a file and runs only where no translation exists
    yet (a resumed run keeps the translator's work); a translator that leaves the skeleton untouched has
    written nothing (`missing-output`, retried once with the reason, a timeout never kept). "Untouched"
    (fix round 1) is judged on the translation files alone as the session found them -- the procedure file,
    or every `models/**/*.sql` of a dbt project -- byte-identical and still holding a TODO; notes,
    `compile_check.json` and logs do not count as work, and a skeleton with no TODO (a pass-through segment)
    is a complete translation. Column names in the mechanical lines are quoted wherever Snowflake, DuckDB or
    YAML needs it, and the Output tool's PreSQL/PostSQL is now required by `c4:write_mode`, not only allowed.
    `SESSION_RULE_LINES` gained a sixth rule: the contract describes every column, so the golden data is
    not read in bulk (at most one set's inputs). Why: live translators spent their sessions on the
    mechanical lines -- a SQL translator needed a 45-minute timeout and a retry to reach a passing
    procedure, and a dbt translator worked 90 tool calls over 25 minutes, read every golden CSV of every
    set and overflowed its context without writing one file. The acceptance test reproduces the
    mechanical parts of every committed canned translation, and the canned bodies transplanted into the
    skeleton compile. Nothing here has run on Snowflake or Alteryx.

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
