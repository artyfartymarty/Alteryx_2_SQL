# The permission policy, and what it is not

`orchestrator/policy.ts` decides every tool call an agent makes, from the `onPreToolUse` hook.
It is a pure function (`decide(role, wfId, toolName, toolArgs, segment?, root?, options?)`) so
every rule can be unit-tested exhaustively — `orchestrator/test/policy.test.ts` is the
specification.

## It is defence in depth, not a sandbox

This policy is a **conservative textual check**. It has no SQL parser and no shell parser, it sees
only the arguments a model passes to a tool, and it cannot see what the tool then does with them.
It will both **miss exotic constructs** and **deny legitimate ones**.

The real boundaries are elsewhere, and this file is the second line behind them:

| Boundary | What enforces it |
|---|---|
| No access to production data | The Snowflake role behind the MCP server. `MIGRATION_AGENT` has `USAGE` on `MIG_WORK`/`MIG_GOLDEN`, read on `INFORMATION_SCHEMA` and nothing on production (program spec §11.1). |
| A migrated procedure cannot exceed its caller | Contract C4: procedures are created `EXECUTE AS CALLER`. |
| No writes outside the repo, no network | The spec's container for shell tools: network allowlist, repo as the only writable mount (§11.1). |
| Nothing is deployed from an agent session | The orchestrator: agents produce files, `MIGRATION_CI` deploys (§11.1). |

Nothing in this repository has run against real Snowflake or real Alteryx, so none of the rules
below have been exercised against a live warehouse.

## What the policy decides

1. **Other workflows.** Any argument mentioning `workflows/<other id>/`, and any path that
   normalizes into another workflow, is denied for every tool — reads included. **Broad reads**
   (Task L1 fix round 1, P2): a read that names no workflow at all could still cover every workflow
   in the run root, so the file-read tools (`FILE_READ_TOOLS`: `view`, `grep`, `glob`, and the
   `read`/`read_file`/`ls`/`list_directory`/`search`/`search_files`/`find` names) are refused, with a
   `broad-read: …` reason naming `workflows/<own id>/…` or a folder outside `workflows/` to read
   instead, when:
   - a path is the repository root (`.`, or the root's absolute path) or `workflows` itself
     (`workflows`, `workflows/`, `workflows/*`); a path `workflows/<other id>` without a trailing
     separator is "no access to other workflows";
   - `grep` (or any of them but `glob`) has no path at all, which means the root;
   - a `glob` pattern evaluated at the root (no `paths`) does not start with a literal folder or file
     name (no `*`, `?`, `[…]`, `{…}` …), or starts with `workflows` and its second segment is not
     the own id, literally;
   - a `glob` pattern, or a `grep` `glob`/`include`/`includePattern` filter, is absolute or climbs
     out with `..`.

   **Only `paths` confines a search** (L6 fix round 1, X2b). The SDK's `grep` and `glob` take their
   paths under exactly `paths` (a string or an array) and ignore every other key. A `grep`/`glob` call
   that carries another path-like key (`path`, `file`, `directory`, `dir`, `target`, … any
   `PATH_ARG_KEYS` key but `paths`, with a string or string-array value) BESIDE `paths` is refused as
   `ambiguous search arguments: …`, a severe attempt like a shell call with two command keys: the decoy
   would count here as a confining path while the tool searched elsewhere. Such a key with NO `paths`
   (fix round 2, I1) is a model used to another tool's key -- the likeliest shape is `grep {path: <the
   spill file it was told about>}`, which would search the working directory. It is refused as
   `search-path-key: …` with a reason that ends "use paths", a `read`. Rule 1a still runs first, so such a key naming another workflow is severe. Both are judged
   before the spill allowance, and a search with no `paths` is at the root.

   The broad-read refusals are `read` denials (item 8), budgeted, not parking a session.
   `glob workflows/<own id>/intake/*`, `glob cookbook/*.md`, `view cookbook/index.md` and a `grep`
   whose `paths` name a file or folder are unaffected.
   **Shell listings obey the same rule** (fix round 2, S2). A listing that walks the tree is a
   broad read unless every path it names is inside this workflow or outside `workflows/`:
   `Get-ChildItem` with `-Recurse` or `-Depth`, `ls -R` (compared lower-cased, so `-r` too),
   `dir /s` / `dir -s`, `git status` and `git diff` (always), and `git log --stat`. With no path
   it runs at the root, and a path that is the root or `workflows` is refused too, with the same
   `broad-read: …` reason, as a `read`. A `-Filter`/`-Encoding` value is not a path. For git a
   path is only what follows `--`; a bare token before it is a revision. So `git diff HEAD` is
   refused and `git diff HEAD -- workflows/<own id>` is not. A listing that does not recurse is
   left alone: `Get-ChildItem`, `ls workflows` and `dir /b workflows` show names one level down
   only. `git log --oneline` shows commit subjects, not files. A listing argument
   `workflows/<other id>` without a trailing separator is "no access to other workflows".
2. **Paths.** `normalizeToolPath` unifies separators, strips `./`, resolves `.`/`..`, accepts an
   absolute path only inside the repository root, lower-cases the result, and denies anything that
   escapes. The one exception is a read of an SDK spill file this session recorded (item 7).
   A path with whitespace of any kind at either end is refused ("path has leading or trailing
   whitespace", fix round 2, S3), never trimmed. That covers ASCII blanks, tabs and line breaks,
   the Unicode spaces, the no-break space, the line and paragraph separators, the BOM and the
   zero-width characters (`EDGE_WHITESPACE`). The tool would open the name WITH it (NTFS keeps a
   trailing no-break space), which is not the path this policy judged.
   **Windows path aliases are refused** (L6 fix round 1, X2a; `windowsAliasReason`), with a
   `windows-alias: …` reason saying which: a path segment ending in a dot or a space (`workflows.`
   and `wf_0002 ` are `workflows` and `wf_0002` to Windows), an 8.3 short name (`~` then a digit,
   `WORKFL~1`), a `:` after the drive (an NTFS stream, `plan.md:x`, `::$DATA`), and a device name
   (`CON`, `PRN`, `AUX`, `NUL`, `COM0`-`COM9`, `LPT0`-`LPT9`, with or without an extension). None of
   them matches `workflows/<id>/…` literally, so rule 1 and the broad-read checks would not have seen
   the workflow they reach; the combined re-review read another workflow's files through
   `workflows./wf_0002/…` and `WORKFL~1/wf_0002/…`. They are refused, never canonicalized (an 8.3 name
   cannot be resolved without the disk), in every path argument, in every listing argument (with or
   without a separator), and in the literal parts of a glob pattern or a grep filter
   (`globAliasReason`) -- and, since fix round 2 (I2), in every argument of an allowed script (and the
   value of every `--x=value`) and in `pytest`'s target. How severe such a refusal is depends on what
   Windows makes of the name (fix round 2, I4; item 8): one that could name another workflow parks the
   session like the plain spelling would, and every other alias of a read is a `read`.
   **A leading `~` is the home directory** (fix round 2, P2): a path whose first segment starts with
   `~` (`~`, `~/x`, `~\.ssh`, `~user\x`) is refused as `path outside the repository (a leading ~ is the
   home directory)`, severe, in a tool argument, a script argument or a shell argument. To this policy
   it used to be an ordinary relative segment, which is harmless only if no tool expands it.
   Every path-like argument is judged (`path`, `file_path`, `filePath`, `target`,
   `destination`, `old_path`/`new_path`, arrays of edits). A write tool with no recognizable path
   is denied. Write lanes are fully anchored per role; shared read-only areas (`golden/`,
   `cookbook/`, `scripts/` outside `scripts/parsers/ext/`, `.github/`, `.git/`, `node_modules/`,
   `.venv/`, `orchestrator/`, `orchestrate.ts`, `orchestrator.config.json`, `config.json`,
   `docs/spec/`, `samples/`) are denied to every role.
   **A dbt workflow's project lanes** (translate stage, no segment in context): the translator and
   the fixer may write `dbt/dbt_project.yml`, `dbt/profiles.yml`, `dbt/README.md`,
   `dbt/translation_notes.md`, `dbt/fix_log.md`, a `.sql` model at any depth under `dbt/models/`, and
   exactly `dbt/models/sources.yml` and `dbt/models/schema.yml` — never a Python model
   (`dbt/models/x.py`), a macro (`dbt/macros/…`), a `packages.yml`, any other YAML dbt would read
   (`dbt/models/extra.yml`), `dbt/review.json` (the reviewer's) or `dbt/compile_check.json` (the
   script's). These lanes are the second line: `compile_check.py --target dbt` and
   `lib.dbt_project.run_dbt` enforce the same closed file set (`dbt:surface`), plus what every file
   in it may say (`dbt:project_yml`, `dbt:yaml`, `dbt:model_jinja`, `dbt:hook_sql`, `dbt:model_sql`),
   before any dbt process starts, whoever runs them (final fix wave C1).
3. **SQL.** Only the validator (sandbox schemas) and intake (`INFORMATION_SCHEMA`) may execute it.
   **The SDK's own `sql` tool is not Snowflake** (fix round 2, P3). A tool named exactly `sql` is the
   SDK's per-session SQLite store, whose description tells the model to track its todos there. It is
   refused for every role with `the session's SQL todo store is not used here; keep your plan in your
   notes file`, an `act` (budgeted), where any other SQL denial is severe; every other SQL-classified
   tool keeps the judgement below. **If the production Snowflake tool turns out to be named exactly
   `sql`, this rule must change before rung 2** (`docs/handoff-production.md` §1.5): as it stands,
   that tool's every statement would be refused as a todo.
   The statement is the one string under exactly one of `sql`, `query`, `statement`, `text` (any
   case). Two of them (`sql` and `query`, or `sql` and `SQL`), or none, is refused as `ambiguous
   SQL arguments`, an attempted action (fix round 2, S1): which key the tool runs is not known, so
   judging one would let the other through unjudged. Comments and string literals are stripped first; a second top-level statement is refused. A name
   in *object position* (after `FROM`, `JOIN`, `INTO`, `USING`, `TABLE`, `UPDATE`, `CALL`,
   `PROCEDURE`, `FUNCTION`, `VIEW`, `STAGE`, `CLONE`, `LIKE`, a stage `@…`, or a qualified name
   applied to `(`) must be schema-qualified in `MIG_WORK`, `MIG_GOLDEN` or `INFORMATION_SCHEMA`, or
   be a CTE declared in the same statement (in `FROM`/`JOIN` only). Elsewhere a qualified name is
   read as a column reference and must be explained by an alias, a CTE, a declared table name or a
   sandbox schema. `DROP`/`TRUNCATE`/`GRANT`/`REVOKE`/`USE`/`ALTER ACCOUNT|USER|ROLE` are denied
   even inside the sandbox. `IDENTIFIER(`, `EXECUTE IMMEDIATE` and `TABLE(` over a non-sandbox
   function are denied. A **three-part** name must also name a sandbox database
   (`policy.sandboxDatabases` in `orchestrator.config.json`, default `MIGDB`), so
   `FINANCE.MIG_WORK.GL_LEDGER` is refused. A `CREATE PROCEDURE … $$ … $$` body is **not** opaque:
   the header and every body statement are checked, and contract C4's table reference is the one
   `IDENTIFIER(` form allowed there (Task C4V): `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' ||
   SRC_SCHEMA || '.<LOGICAL>'` (or the `TGT` twin, `<LOGICAL>_TGT`) declares a variable, and
   `IDENTIFIER(:<LOGICAL>_SRC)` is accepted after that LET in the same body, nowhere else. Snowflake
   documents `IDENTIFIER(` with one value -- a string literal, session variable, bind variable or
   Snowflake Scripting variable -- not an expression, so an expression inside `IDENTIFIER(…)` (the
   concatenation these procedures used before) is denied like any other dynamic name. Inside the
   LET the arguments are named without a colon (Snowflake's expression syntax; the colon binds a
   variable inside a SQL statement) and a colon there is denied by name. Any other LET
   is denied. The variable is trusted only because nothing else in the body can give it -- or a
   parameter its LET reads -- another value, so a judged body must be **flat** (fix round 2): every
   statement other than a rule-conforming top-level LET is denied if it starts with a Snowflake
   Scripting block or control keyword (`BEGIN` other than the body's own, `END` other than its own,
   `IF`, `ELSEIF`, `ELSE`, `CASE`, `FOR`, `WHILE`, `REPEAT`, `LOOP`, `BREAK`, `CONTINUE`, `EXCEPTION`,
   `DECLARE`, `OPEN`, `FETCH`, `CLOSE`, `RAISE`, `AWAIT`, `CANCEL`, `NULL`); if its code, comments
   removed and strings blanked, contains `:=` or the word `LET` anywhere; if it is a `CALL` (a segment
   procedure never calls another; `procs/master.sql` is the orchestrator's, not an agent's); if it is
   a `RETURN` of anything but one string literal; if it writes `INTO :<var>`; or if it is not one of
   the SQL statements `SELECT`/`WITH`/`INSERT`/`UPDATE`/`DELETE`/`MERGE`/`CREATE`/`ALTER`/`TRUNCATE`/
   `DROP`/`COPY`, each then judged by B1-B3 as anywhere else (so `DROP` and `TRUNCATE` are denied).
   `ALTER SESSION SET …` is skipped. The header's parameters must be exactly `(SRC_DB STRING,
   SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)` (types case-insensitive; no
   other type, no `DEFAULT` -- `compile_check.py` requires the same), so the five arguments of a `CALL`
   mean what B5 below checks. The tokenizer reads a backslash-escaped quote inside a string and a
   `"quoted identifier"` the way Snowflake does, so neither can make the code after it look like
   string text. A `<LOGICAL>_SRC` name is only ever read (after `FROM`, `JOIN`, `USING`) and a
   `<LOGICAL>_TGT` name only ever written (after `CREATE … TABLE`, `INSERT`/`MERGE INTO`, `UPDATE`,
   `DELETE FROM`, `TRUNCATE`), as `compile_check.py`'s `c4:identifier_role` holds them (fix round 3).
   This is the documented form; nothing here has run on Snowflake, and the first
   real-account run confirms it. **External locations** (fix round 3), for every role, in and out of
   procedure bodies: a statement is denied with `external-location: … may not move data outside the
   sandbox` if it names credentials (`CREDENTIALS=`, `AWS_KEY_ID`, `AWS_SECRET_KEY`, `AWS_TOKEN`,
   `AZURE_SAS_TOKEN`, `PRIVATE_KEY`, `MASTER_KEY`); names a storage, API, notification, security or
   external-access integration, an external function or a network rule; is a `COPY INTO` a string
   literal (an external URL) or a `COPY … FROM` one; creates or alters a stage with `URL`,
   `STORAGE_INTEGRATION`, `CREDENTIALS` or `ENCRYPTION`; is a `GET` or `PUT` (a local file); or
   `LIST`s or `REMOVE`s anything but a stage. The object scanner never reads a string literal as an
   object, so before this rule `COPY INTO 's3://…' FROM MIG_WORK.T` passed every sandbox check. An
   internal named stage under a sandbox schema (`COPY INTO @MIG_WORK.x`, `LIST @MIG_WORK.x`) is
   allowed as before. A `CALL` must match the C4
   five-argument signature, with a sandbox database in arguments 1 and 3 and a `MIG_` schema in
   arguments 2 and 4. Intake reads `INFORMATION_SCHEMA` in a sandbox database, or the pipeline's
   own sanitized `MIG_WORK.CATALOG_COLUMNS`.
4. **Shell.** The command judged is the one the SDK's shell tool runs: the string under exactly
   `command` (Task L1 fix round 1, I1/P1). A shell call that carries any other command-like key
   beside it (`Command`, `cmd`, `script`, `input`, whatever its value), or no string `command` at
   all, is denied as `ambiguous shell arguments`, an attempted action: judging one key while the
   tool runs another would let an unjudged command through. The SDK's own shape (`command`,
   `description`, `initial_wait`, `mode`, `shellId`) is unaffected. Any metacharacter (`;` `|` `&`
   `<` `>` `` ` `` `$(` `${` newline) and any interpreter
   flag (`-c`, `-e`, `-Command`, `-EncodedCommand`, `-File`) ends the call, and so does any
   whitespace other than blanks and tabs (a no-break or zero-width space splits tokens differently
   here and in the shell; fix round 2, S3), judged before the command is trimmed. What remains must be
   exactly one shape: a listing command, the role's own `<python> scripts/<script>.py <args>`, or
   `<python> -m pytest tests/parser_corpus[/…] [-q]` for parser-recovery. `<python>` is any of the
   spellings in `PYTHON_EXES`: bare `python`, `python3`, `py`, or the project's own venv by its
   relative path — `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (Unix). **Agents run
   bare `python`** (Task L1, R1): a run root is a copy without `.venv` whose config names the
   interpreter by absolute path, so the orchestrator puts that interpreter's directory first on
   `PATH` for every agent session (`sessionEnvironment` in `orchestrator/cli.ts`) and every agent
   file says `python scripts/<name>.py …`. The venv spellings stay allowed (a human in the repository
   checkout may still type `.venv/Scripts/python.exe`); each is still exactly one interpreter
   invocation, subject to every check below. A path is matched case-insensitively with separators normalized first (`normalizeToken`
   lower-cases and turns `\` into `/`), so `.venv\Scripts\python.exe` (the literal form a real
   Windows `powershell` tool call carries) matches the same entry as the forward-slash spelling —
   there is no separate Windows-specific rule to maintain. Any OTHER interpreter path (a system
   Python, a different venv, an absolute path even to this same venv) is not in `PYTHON_EXES` and
   is denied like any unrecognized command.
   **The translator and the fixer validate their own work** (live hardening, Task L4). Besides
   `compile_check.py` and `render_snowpark.py`, both may run `validate_segment.py` and
   `validate_snowpark.py` with exactly `<wf> <the session's own segment>`, and `validate_dbt.py` (dbt
   scope only) with exactly `<wf>`, plus any number of `--set <name>` or `--set=<name>`
   (`SELF_VALIDATION_SCRIPTS`), and only in the shell tool's synchronous mode: a call with any other
   `mode` (`async`), or `detach`, is `self-validation: …`, because a background run could write its
   reports after the orchestrator cleared them. A sync command that outlives its `initial_wait` also
   moves to the background; no argument shows that, so the orchestrator's clear-then-validate order
   is what covers it. For every script, `--set=<name>` is judged like `--set <name>`: a flag, never
   the workflow id, with its value a plain argument.
   Another segment, or none, is `own-segment: …`. A segment validator in dbt scope, `validate_dbt.py`
   in a segment session, `--proc`, `--project` or any other flag is `self-validation: …`. `--root` and
   the backend flags keep their own refusals. Every one of these is an attempted action. The reports
   such a run writes are deleted by the orchestrator before its validator runs (`stages.ts`'s
   `clearValidationReports`), so they never stand in for the verdict. The validator's own script
   rules are unchanged.
   **A script's path flags stay in bounds** (fix round 2, P1). `scripts/compare.py` writes its report
   to `--out` and its DuckDB file to `--db`; before this rule the validator could point them at any
   repository file (`--out orchestrator/policy.ts` was allowed). Every path-valued flag of a script in
   `ROLE_SCRIPTS` (`SCRIPT_FLAGS`, taken from each script's `add_argument` calls; argparse's unambiguous
   prefixes and the `--flag=value` form included) is judged: what the script READS (`--expected`,
   `--contract`, `--tolerances`, `--manifest`, `--dag`, `--proc`, `--project`, `--yxdb-dir`) must pass
   rules 1 and 2; what it WRITES (`--out`, `--db`) must also lie inside `workflows/<own id>/` and the
   role's write lane. Otherwise it is refused as `script-path: …` -- severe when a written path lies
   outside the own workflow or a read one out of the repository or in another workflow, an `act` when a
   written path is inside the own workflow but outside the lane. The orchestrator's own script
   invocations do not pass through this policy.
   **Listing commands are not side-effect-free** — `git diff --output=<path>` writes a file — so
   each one carries an explicit flag allow-list and every other flag is denied
   (`flag not allowed for <command>: <flag>`), `--name=value` is judged by its name, numeric flags
   must be given digits, `git` accepts only the `status`/`diff`/`log` subcommands, and every
   remaining argument must pass the plain-token charset plus, when it looks like a path, rules 1
   and 2. The allow-lists live in `READ_ONLY_SHELL` in `policy.ts`:

   | Command | Flags |
   |---|---|
   | `ls` | `-l -a -la -al -lh -R -1` |
   | `dir` | `/b /s /a` and the same dash forms |
   | `cat`, `type` | none |
   | `Get-Content` | `-Path -LiteralPath -TotalCount -Tail -Raw -Encoding` (no `-Stream`, no `-Wait`) |
   | `Get-ChildItem` | `-Path -LiteralPath -Recurse -Name -File -Directory -Filter -Depth` (no `-Include`/`-Exclude`: nothing needs them, and default-deny is the rule) |
   | `git status` | `-s --short --porcelain -b --branch` |
   | `git diff` | `--stat --name-only --name-status --cached --staged --no-color` |
   | `git log` | `--oneline --stat --no-color -n --max-count`, and a bare `-<digits>` |
5. **The SDK's shell-session tools.** A shell command this policy allowed may keep running in the
   background after its `initial_wait`; the SDK then expects the model to read its output with
   `read_powershell`/`read_bash` (by `shellId`) and to list its shells with
   `list_powershell`/`list_bash`. Those four only read what an allowed command printed, so every
   role may use them (`SHELL_SESSION_READ_TOOLS`; live evidence 2026-09-23). `stop_powershell` and
   `stop_bash` are allowed for every role too (`SHELL_SESSION_STOP_TOOLS`, L6 fix round 1): a shell
   session exists only because this policy allowed the command that started it, and these tools
   address it by the session's own `shellId`, so they stop nothing the session did not start. Live
   evidence: a translator whose SQL passed all four golden sets made about twenty
   `stop_powershell {"shellId":"sh-sel"}` calls on its own shell. `write_powershell`, which types into
   a shell, is still caught by the write rule.
   `local_shell` is judged like every other shell tool.
   The SDK's sub-agent tools follow the same idea: `task` (allowed) may start a background sub-agent
   whose result the runtime tells the model to fetch with `read_agent`; `read_agent` and `list_agents`
   only read and are allowed for every role (`AGENT_READ_TOOLS`), and the sub-agent's own calls pass
   through this policy. `write_agent` (a follow-up message to a sub-agent) is denied with its own
   reason.
6. **Everything else is denied**, including any tool name that is not SQL, shell, write or on the
   read/planning allow-list (`READ_TOOLS`). Every such denial is audited as `unrecognized-tool`.
7. **The SDK's spill files** (Task L1, R2). When a tool result is large, the SDK writes the full
   output to a temporary file and tells the model to open it: `[Truncated — full output (…)
   temporarily saved to <path>]` or `… too large to read at once (…). Saved to: <path>`.
   `hooks.ts`'s `onPostToolUse` records such a path in the session's own state. It must appear in
   one of those two sentences (`SPILL_MESSAGES`), be canonical (resolving it changes nothing but
   separators), resolve inside the OS temp directory (`os.tmpdir()`), and have one of the SDK's
   three spill names as its base name (`SPILL_FILE_NAME`):
   - `<ms>-copilot-tool-output-<pid>-<uuid>.txt`: a millisecond timestamp and a process id, which
     can be guessed within a range, and a random UUID, which cannot;
   - `copilot-tool-output[-original]-<a>-<b>.txt`, the ripgrep output file: how the runtime picks
     `<a>` and `<b>` is not established (its "failed to allocate a unique ripgrep output file"
     suggests a retry loop, possibly over a counter), so these names may be guessable;
   - `original-output-<n>-<…>.txt`: not established either.

   Since fix round 1 (M5) the file must also exist, and its modification time must not be earlier
   than the session's start (less a 2-second clock tolerance, `SPILL_CLOCK_TOLERANCE_MS`). A path
   the result names anywhere else is not recorded. `decide`
   then allows two reads of a recorded file, for every role: `view` of exactly that one path, and
   `grep` whose every path is a recorded one (`PolicyOptions.readableSpillFiles`). Paths match
   exactly, with separators unified and case folded on Windows, and nothing else (fix round 1,
   M1): no whitespace is trimmed (`<spill>.txt` followed by a no-break space is another file), and
   no `.`/`..` is resolved. A recorded key must also be absolute and carry the spill name. Everything else stays denied: any write, edit, create or shell command on the
   file, `glob`, a `grep` that mixes a recorded path with any other, a spill-shaped path this session
   never recorded (another session's), and anything that names another workflow (rule 1 runs
   first). This is the one path outside the repository an agent may read.
8. **Denial classes** (Task L1, R3; Task L6, R1 and R2, and its fix rounds 1 and 2). Every deny carries a `denialClass`:
   `severe`, `act` or `read`. The class never changes a decision: a denied call is refused whatever
   its class. It only tells `CopilotRunner.run` what the refused call attempted, and so whether the
   session may go on.

   **`severe`** (Task L6, R2) is a refused call that tried to reach outside the session's workflow or
   the sandbox, or to do damage. One parks the session at once. `severeCategory` names which, and it
   is built from this policy's own deny reasons (the reason prefixes are exported constants,
   `OTHER_WORKFLOWS_REASON` and the rest, used both where a rule denies and where the class is read):
   - `sql`: any SQL-tool denial, an ambiguous statement key and an `external-location:` included --
     but not the SDK's own `sql` todo store (item 3, fix round 2 P3), which is an `act`;
   - `other-workflow`: a reason that names another workflow (`no access to other workflows`,
     `cross-workflow:`), whatever the tool -- a `view` of `workflows/<other id>/…` too -- unless the id
     it names is `.`, `..` or the own id with trailing dots or spaces (`workflows/./wf_0001/…`,
     `workflows/wf_0001./…`: fix round 2, minor 4; still refused, as the tool's class). A **broad**
     read that could reach other workflows (`broad-read: …`, item 1) is not severe: it is a read.
     **A Windows alias is judged by what Windows makes of it** (fix round 2, I4; `aliasReach`): each
     segment's trailing dots and spaces and any `:stream` suffix are stripped, an 8.3 segment (`~` then
     a digit) -- or in a glob a wildcard -- matches any name, and a glob's brace alternatives are tried
     one by one. If the result could name another workflow (`workflows` or an any-name segment, then
     another id or an any-name segment: `workflows./wf_0002/…`, `WORKFL~1\wf_0002\…`,
     `workflows/WF_000~1/…`, `workflows./*/intake/*`) the call is `other-workflow`; if it resolves out of
     the repository, `outside-repository`; otherwise it keeps its tool's class (a read tool's alias is
     a `read`). Without this an evasive spelling of a cross-workflow read was only budgeted;
   - `outside-repository` (fix round 2): a path whose first segment starts with `~` (item 2, P2), in a
     tool argument, a script argument or a shell token, and a Windows alias that resolves out of the
     repository;
   - `write-outside` and `tampering`: **a write is judged by its target** -- through a write tool, an
     `apply_patch`'s `*** Add File:`/`*** Update File:`/`*** Delete File:`/`*** Move to:` headers (fix
     round 2, minor 2), a shell write command or redirection (fix round 2, I5), a .NET file method
     (`[IO.File]::WriteAllText('x', …)`, `[IO.Directory]::Delete('x', $true)`) or a script's output flag
     (item 4, P1). The shell's write commands are `Set-Content`/`sc`, `Add-Content`/`ac`, `Out-File`,
     `Tee-Object`/`tee`, `Export-Csv`, `Clear-Content`/`clc`, `New-Item`/`ni`, `md`/`mkdir`, `touch`,
     `Remove-Item`/`ri`/`rm`/`del`/`erase`/`rd`/`rmdir`, `Move-Item`/`mv`/`move`/`mi`,
     `Rename-Item`/`ren`/`rni` and `Copy-Item`/`cp`/`copy`/`cpi` (its destination only: a copy's source is
     read), their `-Path`/`-LiteralPath`/`-FilePath`/`-Destination` values or positional targets, and
     `>`, `>>`, `2>`, `*>` (not to `$null`/`nul`/`/dev/null`) (`writeTargetCategory`). A write command
     that takes a pipeline's input and names no target (`gci -r workflows | Remove-Item`) cannot be
     placed and is `write-outside`. Where the target lands (fix round 2, I6):
     - `tampering`: the own workflow's `golden/**` (the answer key) or `audit.jsonl` (the record of
       every call);
     - `other-workflow`: another workflow's folder;
     - `write-outside`: one of the pipeline's own trees (`PIPELINE_TREES`: `scripts/`, `orchestrator/`,
       `mappings/`, `catalog/`, `cookbook/`, `docs/`, `tests/`, `samples/`, `snowflake/`, `workflows/`
       itself, `node_modules/`, and any dot-folder such as `.github/`, `.git/`, `.venv/`), one of its
       top-level files (`TOP_LEVEL_PIPELINE_FILES`: `README.md`, `orchestrate.ts`,
       `orchestrator.config.json`, `config.json`, `package.json`, `pyproject.toml`, `tsconfig.json`, …,
       and the files a tool loads on its own if they appear -- `conftest.py`, `sitecustomize.py`,
       `pytest.ini`, `setup.cfg`, … -- and every top-level dot-file), a path out of the repository or a
       UNC share, or a path that cannot be placed (an 8.3 name);
     - an ordinary `act`: a NEW scratch file or folder at the run root's top level (`_diag.py`,
       `diag.py`, `tmp/x.py`) -- the live translator's two DuckDB probes -- and a write inside the own
       workflow but outside the role's lane. A trailing dot or space and a stream suffix are read as
       Windows reads them (fix round 2, minor 5), so `proc.sql.` in the own lane is an `act`. A path is
       placed after removing whitespace at its ends (the policy refused it for that, but it still says
       where the write aimed). A write that names no path at all is `act`;
   - `destructive`: `destructive command:` (`DESTRUCTIVE_SHELL`: `rm -r`/`-f`, `del /s`, `format`,
     `git push`, `git reset --hard`, curl, wget, Invoke-WebRequest). The `format` pattern matches the
     word anywhere -- PowerShell's formatting cmdlets, `-Filter format*`, a file named `format.md` --
     and that refusal is left as it is; for the severe class only the disk command counts (fix round
     2, minor 12; `NOT_DISK_FORMAT`): `format <drive>:` (or `format.com`/`.exe`) and `Format-Volume`.
     Since fix round 1 also a delete command (`DELETE_COMMANDS`: `Remove-Item`, `ri`, `rm`, `del`,
     `erase`, `rd`, `rmdir`) with a recursive or force flag: `-Recurse`/`-Force` in any prefix
     PowerShell accepts (and `-Recurse:$true`), a POSIX bundle holding `r`, `R` or `f`,
     `--recursive`/`--force`, cmd's `/s` and `/f`. `-Filter`, `-First` and `-WhatIf` are none of them.
     A delete run through `Start-Process <exe> -ArgumentList …` counts too (fix round 2, minor 9), and
     so does an unrecognized tool whose name says it deletes (`DESTRUCTIVE_TOOL_NAME`: `delete_file`,
     `remove_dir`, …);
   - `script-root`, `script-backend`: a script given `--root` or a Snowflake-account flag;
   - `network`: a shell command that names a network tool or API -- `curl`, `wget`,
     `Invoke-WebRequest`/`iwr`, `Invoke-RestMethod`/`irm`, `Start-BitsTransfer`, `Net.WebClient`,
     `System.Net`, `ftp`, `scp`, `ssh`, `nc`, `Test-NetConnection` and its alias `tnc`,
     `Resolve-DnsName`, `bitsadmin`, `nslookup`, `ping`, `Enter-PSSession`/`New-PSSession`
     (`NETWORK_NAMES`; the last six since fix round 2, minor 9) -- or input typed into a running shell
     (`write_powershell`/`write_bash`) that does. Since fix round 1 also git's remote subcommands
     (`clone`, `fetch`, `pull`, `push`, `remote`, `ls-remote`, after any `-C <dir>`), an interpreter
     one-liner (`python -c`/`-m`, any spelling of the interpreter) using a network module
     (`NETWORK_MODULES`: `urllib.request`, `requests`, `http.client`, `http.server`, `socket`, `ftplib`,
     `smtplib`, `urlopen`, …) -- since fix round 2 (minor 6) by an import or a use (`import m`, `from m
     import …`, `__import__('m')`, `m.attr`), never by the bare word, so `print('open requests: 3')` and
     `import urllib.parse` are not network calls -- and an unrecognized tool whose name says it reaches
     the network (`NETWORK_TOOL_NAME`: `fetch`, `web`, `http`, `url`, `download`, `browse`, `curl`),
     such as `web_fetch` or `browser_open`. Since fix round 2 (minor 9) also `certutil -urlcache`,
     `Invoke-Command`/`icm` with `-ComputerName`/`-Session`/`-HostName`/…, a `node -e`/`--eval`/`-p`
     one-liner that requires or imports `http`/`https`/`net`/`dgram`/`tls`/`dns` or calls `fetch(`, and
     a UNC path (`\\host\share\…`, which opens an SMB connection) in any path, glob or shell token;
   - `install` (fix round 1): a package installer, which fetches code -- `pip`/`pip3`/`python -m
     pip` `install`/`download`/`wheel`, `npm`/`pnpm`/`yarn` `install`/`i`/`ci`/`add`/`update`/`exec`/…,
     `npx`, `pnpx`, `uvx`, `pipx`, and `uv add`/`sync`/`run`/`lock`, `uv pip install`, `uv tool
     install`; since fix round 2 (minor 9) also `Install-Module`/`Install-Package`/`Install-Script`,
     `Save-Module`/`Save-Package`, `Update-Module`, and `winget`/`choco`/`scoop`/`conda`/`mamba`/`gem`/
     `cargo` `install` (and their fetching subcommands);
   - `credential`: the same for a credential store -- `Get-Credential`, `cmdkey`, `vaultcmd`, the
     environment drive `env:` (so `$env:X`, `${env:X}`, `Get-ChildItem env:`),
     `[Environment]::GetEnvironmentVariable(s)` and `printenv` (`CREDENTIAL_NAMES`); since fix round 2
     (minor 9) also `cmd /c set` (cmd's environment listing), a Python one-liner reading `os.environ`/
     `os.getenv`, a `node -e` reading `process.env`, and a path through a credential folder or file
     (`CREDENTIAL_DIRS`: `.ssh`, `.aws`, `.azure`, `.snowflake`, `.kube`, `.docker`, `.gnupg`;
     `CREDENTIAL_FILES`: `.netrc`, `_netrc`, `.git-credentials`, `.pgpass`; the gh CLI's `hosts.yml`);
   - `ambiguous-arguments`: a shell call whose arguments carry TWO or more command-like keys (item 4;
     fix round 1, I1/P1) -- someone gaming the argument keys -- or a `grep`/`glob` call with a path key
     BESIDE `paths` (item 1, X2b). A lone command-like key that is not `command` (`{input}`, `{cmd}`,
     `{Command}`) is a confused call, not a gamed one (fix round 2, minor 8): `act`, like a shell call
     with no command-like key at all or a `command` that is not a string. A grep/glob with a lone
     singular path key is a `read` (item 1, fix round 2 I1).

   **Text is not a reach** (L6 fix rounds 1 and 2). A search's own literal pattern, a write's content
   and a planning tool's text are judged as text, never as what the call reaches (`severityView`,
   `sanitizeSearches`): the `grep` tool's `pattern`, the pattern of a `Select-String`/`sls` statement
   (under PowerShell's quoting, `-Pattern x`, `-Pattern:x`, an unambiguous prefix, or the first
   positional argument), of a `grep`/`egrep`/`fgrep` or `rg` statement (`-e x`, `--regexp=x`, or the
   first operand) under the bash tool's quoting or -- since fix round 2 (minor 3) -- PowerShell's, and
   of a `findstr` statement (`/c:x` or the first operand) under PowerShell's; a write's content keys
   (`CONTENT_ARG_KEYS`); and (fix round 2, minor 13) every string of a planning tool
   (`PLANNING_TEXT_TOOLS`: `report_intent`, `think`, `todo`, `update_todo`, `ask_user`, …; not `task`,
   whose prompt starts a sub-agent) are blanked before a call's severity is judged. So `Select-String
   'env:'`, `grep -rn "curl" scripts` in PowerShell, a `grep` for `workflows/wf_0002/`, a note that
   names another workflow and an own-lane write whose text names one are not severe; they are judged
   by what else they name, and a write by its target. Only a LITERAL
   pattern is blanked (`$env:X` expands), and only where the statement is read with confidence: a
   statement with a parameter or an option this reading does not know, a command PowerShell's quoting
   cannot split, and every shell but PowerShell and bash are left as they are. The decision itself is
   unchanged: the search is still refused, with the same reason.

   A name matches as a word: not preceded by a letter, a digit, `_`, `-` (or `.` for a name without
   a dot), not followed by a letter, a digit, `_` or `-` (a name ending in `:` may be followed by
   anything). So `curl.exe` and `[System.Net.Dns]` match; `sync`, `firmware`, `sftp` and `ssh_key` do
   not. Names match anywhere in the command, inside quotes too (`powershell -c "iwr …"` still runs).
   The first matching rule gives a call its reason, and a chained command (`…; echo done`) is refused
   as a metacharacter before the deeper rules see it, so a shell command is also judged on the call
   itself: every piece between separators and groupings -- and a command another one runs (`cmd /c`,
   `powershell -c`, `bash -c`, `iex`) -- is checked for a `DESTRUCTIVE_SHELL` match or a recursive
   delete, git's remote subcommands, an installer, a Python invocation given `--root` or a backend
   flag, a `WORKFLOW_ID_SCRIPTS` script run on another workflow, and a path into another workflow.
   `python scripts/check_seams.py wf_0001 --root .; echo x` is `script-root`, not an ordinary chained
   command.

   **`read`** means the call could only have read. That covers every tool that only reads
   (`READ_CLASS_TOOLS`, fix round 2, S4), for any reason that is not severe. The tools are `view`,
   `grep`, `glob`, `read`, `read_file`, `ls`, `list_directory`, `search`, `search_files`, `find`, and
   the SDK's `read_`/`list_` shell-session tools. The planning tools (`task`, `todo`, `ask_user`, …) do
   more than read and stay `act`. It covers a git listing (`isReadOnlyGitListing`): one `git
   status`/`git diff`/`git log` with only its own allowed flags. A git call refused for a flag, such
   as `--output`, stays `act`. It also covers a shell command (`isReadOnlyShellCommand`) in which
   every statement starts with a command from the fixed `READ_ONLY_COMMANDS` list. The list is
   `Get-ChildItem`/`gci`/`ls`/`dir`, `Get-Content`/`gc`/`cat`/`type`, `Select-String`/`sls`,
   `Test-Path`, `Get-Command`/`gcm`, `Get-Item`/`gi`, `Get-Location`/`pwd`, `Resolve-Path`,
   `Select-Object`, `Format-Table`, `Format-List`, `Sort-Object`, `Measure-Object`, `Out-String`,
   `Write-Host` and `Write-Output`/`echo`. Statements end at `;`, `|` and line breaks (a newline ends
   a statement exactly as `;` does). **For the `powershell`/`pwsh` tools only a separator OUTSIDE
   quotes ends one** (Task L6, R1; `scanPowerShell`), with PowerShell's quoting: `'…'` is literal
   and `''` inside it is one quote; inside `"…"` PowerShell interprets only `$` and the backtick, and
   `""` is one quote. So `Get-Content x -Raw | Select-String -Pattern "a|b|def "` -- the live
   analyzer's search -- is two read-only statements, while `"a|b" | Remove-Item` is still `act`. A
   command PowerShell's quoting cannot split with confidence is `act`: an unterminated quote; a `$` or
   a backtick anywhere inside a double-quoted string (expansion, escapes); a typographic quote
   (PowerShell reads U+2018-U+201E as quotes, so `'a’ ; Remove-Item y ; ’b'` runs Remove-Item); and
   outside quotes a `#` (a comment could hide a quote), an `@` (a here-string, splatting, an array
   expression), a backtick, or `--%`. The command must also hold none of `ACT_TOKENS` -- `{`, `}`,
   `$(`, `@(`, `>`, a backtick, `[`, `&`, `Invoke-` and `iex` -- and, on top of the ruling's list, a
   `(` must open with a read-only command, and a `(` written directly after a name, a quote or `)` is
   a method call and makes the command `act`. So `Write-Output (Remove-Item x)` and `(Get-Item
   x).Delete()` are `act`. For the PowerShell tools these are judged only on what PowerShell
   interprets -- the text outside quotes (L6 fix round 1) -- so a `(`, `[`, `{`, `>` or `&` inside a
   quoted regex is text: `Select-String -Pattern "def (read|write)_"` is a read. For every other shell
   tool (`bash`, `cmd`, …) no quote is trusted, as before: every separator splits, and the tokens and
   the `(` rule are judged over the whole command. Their quoting is not PowerShell's (in bash `\"` is
   an escaped quote, in cmd `'` is no quote), and PowerShell's rules would hide real separators there.
   An empty statement (`a || b`) is `act` too.

   **`act`** is every other refused call: an interpreter one-liner, a command the role may not run,
   a chained command, a write inside the own workflow but outside the role's lane, an unrecognized
   tool, `write_agent`, or arguments that cannot be read.

   `CopilotRunner.run` parks a session `denied` if it had any `severe` denial -- on EVERY exit path,
   judged before anything else (fix round 2, I3): a severe attempt followed by a timeout, a rate limit,
   a context overflow or a crash is still `denied`, never retried, and never kept by Task L7's rule
   that keeps a timed-out session whose output passes its check (`runAgent` also refuses to keep,
   verify or retry any result that carries `severeDenials`). It parks a session at its end if its
   `act` denials exceed `budgets.maxActDenialsPerSession` (default 20 since L6 fix round 1, the same
   as reads; it was 3; 0 restores the spec's rule for every attempted action), or if its `read`
   denials exceed `budgets.maxReadDenialsPerSession` (default 20). The budgets catch a session that
   thrashes; whether its output is good is for the stage's deterministic checks to say (a live
   translator whose SQL passed all four golden sets made 33 blocked attempts). Otherwise its outcome is decided by its outputs and the stage's own checks, as if
   nothing had been denied. That is a documented deviation from the program spec's "tool denied by
   policy → abort stage": design doc §4, item 12, and README §9, item 11. `hooks.ts` hands the SDK
   only its own two fields (`permissionDecision`, `permissionDecisionReason`). The class stays in the
   orchestrator, and in the audit: since fix round 1 (M9) a denied call's `pre` line in `audit.jsonl`
   also carries its `reason` (redacted and bounded like the arguments) and its `class`.
9. **Tools never offered at all** (live hardening, Task L9, R1). Every session's `createSession`
   call also carries the SDK's own `excludedTools`, "always takes precedence" over whatever else
   would have offered one of these tools (`node_modules/@github/copilot-sdk/dist/types.d.ts`
   ~2015-2021). The fixed list (`policy.ts`'s `ALWAYS_EXCLUDED_BUILTIN_TOOLS`, prefixed `builtin:` by
   `sessionExcludedTools` so it can only ever exclude the BUILT-IN tool of that name) is `web_fetch`,
   `web_search`, `sql` and `write_agent` -- exactly the tools `judge` already refuses outright for
   every role by name or by `NETWORK_TOOL_NAME` (live evidence, task-L9-brief.md: a documenter
   session parked on `web_fetch`, graded `severe`). `orchestrator.config.json`'s `session.excludedTools`
   extends the list; the operator's own entries are passed through exactly as written. **This is
   defence in depth, not a new rule**: the policy above still judges and refuses every one of these
   tools exactly as before if one reaches `onPreToolUse` anyway -- on a build that does not honour
   `excludedTools`, or under a name this list does not yet name. It only saves the round trip (and,
   for `web_fetch`, the parked session) a denial otherwise costs.

## Known limitations

These are the ones we know about. They are the reason this file is not the primary boundary.

- **A two-part name carries no database.** `MIG_WORK.T` is allowed without knowing which database
  it resolves in: that is the session's current database, so this case relies entirely on the
  `MIGRATION_AGENT` grants. Only three-part names are checked against `policy.sandboxDatabases`.
- **Listing flags are an allow-list, so a legitimate flag nobody listed is denied.** Adding one is
  a deliberate act: check what the flag can write before you add it (`--output`, `-o`, `--ext-diff`,
  `--textconv`, `--no-index`, `--git-dir`, `--work-tree`, `--exec-path`, `--upload-pack`,
  `--open-files-in-pager` are denied for exactly that reason). Flag names are compared
  case-insensitively, so `ls -R` and `ls -r` are both accepted.
- **A stage's definition is not visible.** `COPY INTO @MIG_WORK.x` is allowed because `@MIG_WORK.x`
  is a sandbox-qualified name; the policy denies creating or altering a stage with an external `URL`,
  but it cannot see a stage a human created outside it. The `MIGRATION_AGENT` role must hold no usage
  on any external stage and no integration — that grant, not this file, is what keeps sandbox data
  inside the sandbox.
- **No SQL parser.** Object position is decided from tokens and a finite keyword list. A construct
  the tokenizer does not model (unusual DDL, `PIVOT`/`UNPIVOT` variants, `AT`/`BEFORE` time travel
  with a qualified argument, table functions other than the `TABLE(` form) may be classified wrongly
  — usually into a denial, occasionally into a miss.
- **Inside a function call's parentheses, object introducers are ignored**, so that
  `EXTRACT(YEAR FROM POSTED_AT)` is not read as a FROM clause. An *unqualified* name in such a
  position is therefore not required to be a CTE. Qualified names there are still caught by the
  column-reference rule.
- **Unqualified names outside object position are never checked.** They resolve in the session's
  current schema, which for `MIGRATION_AGENT` is a `MIG_*` schema.
- **Comma joins are refused** (`FROM a, b`): the policy cannot reliably tell a FROM-list comma from
  a select-list one, so it denies and asks for an explicit `JOIN`.
- **`CALL` understands only the contract-C4 shape.** A legitimate zero- or one-argument utility
  procedure in `MIG_WORK` is denied.
- **Stage references must be schema-qualified**; user and table stages (`@~`, `@%t`) are denied.
- **The keyword list is finite.** A missing keyword can make the policy stricter (a keyword read as
  a name) or, where a keyword precedes `(`, let a paren be read as a function call, which skips
  introducer checks inside it — the column-reference rule still applies there.
- **Tool names are matched by regex.** Any tool whose name contains `query` is treated as SQL; a
  tool name nobody has seen yet is denied until `READ_TOOLS` is extended from the audit log.
- **Only string arguments under path-like keys are normalized.** A tool that hides a path under an
  unexpected key gets no path check (a write with no recognizable path is denied outright).
- **Path comparison is case-insensitive**, which matches Windows; on a case-sensitive filesystem two
  distinct files can normalize to the same string. That can only deny, never widen, because lanes
  are exact.
- **The policy cannot police what a tool does after it is allowed** — a permitted script that itself
  reads or writes elsewhere is outside its reach. That is what the container and the Snowflake role
  are for.
- **The Snowpark self-test runs agent-written Python on the host, and its sandbox is defence in
  depth, not a boundary.** `scripts/validate_snowpark.py` (which the translator and fixer may run on
  their own `proc.py`, live hardening L4 R2) executes the module in a child process under a static
  gate (`scripts/lib/snowpark_rules.py`) and a `sys.addaudithook` (`scripts/lib/snowpark_sandbox.py`,
  with a timeout, a minimal environment and a nonce-signed result). Both gates are ALLOW-lists (fix
  round 5): the static gate accepts only the Snowpark/pandas surface the benign corpus uses, and the
  audit hook is default-deny (only the recorded benign events allowed, native families `_winapi.*`/
  `winreg.*`/`ctypes.*`/`socket.*`/`subprocess.*`/`sqlite3.*` refused outright). That sandbox was
  attacked three times (fix rounds 2–5: a `sys.modules` reach, a `sqlite3` host-file write, and
  `pd.eval(..., engine="python")` reaching `_winapi` native calls, all closed), but agent code
  still shares the harness's Python process, so it depends on the static gate keeping `sys`, frames
  and every stdlib module out of reach — a textual AST check, not OS isolation. The parser-recovery
  agent's `scripts/parsers/ext/*.py`, which `scripts/parse.py` imports on the host, has no sandbox at
  all, by design. Real isolation is the container (`docs/spec/01-copilot-setup.md` §11.1) — see
  `docs/production-backlog.md`, "Isolate agent-code execution".
- **Bounds deny, they never skip.** The argument walker reaches `MAX_ARG_DEPTH` levels; arguments
  nested deeper are refused with `arguments too deeply nested to judge` rather than ignored.
- **A spill sentence can be quoted** (item 7). The hook reads result text, so a result that only
  QUOTES the SDK's sentence also names a path. An agent can author one: it writes the sentence into
  its own notes file and then `view`s that file. The path is still recorded only if it is canonical,
  inside the temp directory, has an SDK spill name, and names an existing file modified no earlier
  than this session's start (fix round 1, M5), so a name copied from an older session records
  nothing. What remains is a spill file another session wrote DURING this one. It becomes readable,
  read only, if the quoted text carries its exact name. For the `<ms>-copilot-tool-output-<pid>-<uuid>`
  form that includes a random UUID. For the other two forms it is not established that the name
  cannot be guessed (item 7). The recorded set belongs to one session and is never saved.
- **The runtime's temp directory must be `os.tmpdir()`.** If the SDK runtime writes its spill
  files somewhere else (another `TEMP`/`TMP`, a short 8.3 path form), nothing is recorded. The read
  then stays denied, as a `read` denial. Not verified live: the one live spill was under the same
  `AppData\Local\Temp` that `os.tmpdir()` returns there.
- **The `read` class is lexical** (item 8). It judges the text of a refused command and never runs
  it. A construct the lexical rules do not model could be classified `read` when it could have
  acted. That changes only whether the session goes on, never what is refused, and anything the
  rules do not recognise is `act`.
- **The `severe` class is lexical too** (Task L6, R2), and it errs toward parking. A network or
  credential name is a word anywhere in the command outside a search's own literal pattern (item 8),
  so a file named `ssh.md`, or a search whose statement this reading does not know (an unknown
  parameter or option), makes a command severe. A write is placed by its path alone: a write to the
  own workflow through a mangled absolute path (the third live test's shape) resolves outside the
  repository and is severe, and so is a write through an 8.3 name, which cannot be placed. A write
  that names no path at all (`apply_patch` with its patch text under an unknown key, or with no `***
  … File:` header) is `act`. A shell write is found by its command's name and parameters: a write the
  lexical reading does not recognise (a .NET call it does not list, a variable holding the path) is
  judged like any other refused command. Which top-level files are the pipeline's is a fixed list
  (`TOP_LEVEL_PIPELINE_FILES`), not a look at the disk: a NEW top-level file with another name is a
  scratch file. The lists are still lists: a Python one-liner using a module outside
  `NETWORK_MODULES`, `curl` spelled through a variable, `Invoke-Command` through a splatted
  parameter, or a tool the policy does not recognise whose name says nothing of the network is
  refused and budgeted as `act`.
- **`excludedTools` naming an unknown tool is now verified to be a harmless no-op** (item 9; verified
  live hardening, L9 fix round 2 review, `probe_excluded.mjs`). The SDK's own `excludedTools` is
  documented as a set of filter PATTERNS matched against the tools a session would otherwise offer
  (`types.d.ts` ~2004-2021). Corrected framing: `web_fetch`, `web_search`, `sql` and `write_agent` are
  NOT "hosted-runtime tools with no local type surface" -- the runtime that parses `excludedTools` is
  the bundled LOCAL binary (`@github/copilot-sdk-win32-x64/prebuilds/.../runtime.node`) for BOTH the
  `local` and `hosted` profiles alike; only the MODEL differs between them, and of the four only
  `web_search` is itself a server-side tool. Running the bundled runtime offline (a dead-loopback BYOK
  provider, no login, no message ever sent) against `createSession` confirmed: an unrecognised bare
  name (`["no_such_bare_zz", "mcp:no_such_zz", "custom:no_such_zz"]`) resolves with no error and no
  event, the harmless no-op this repo always assumed -- but a bare `"*"` is REJECTED outright
  (`Invalid excludedTools entry '*': there is no bare wildcard …`, `client.js`'s
  `validateToolFilterList`); `cli.ts`'s `loadConfig` refuses a bare `"*"` in `session.excludedTools` at
  config load (exit 2, by name) since L9 fix round 2, so this never reaches `createSession` for the
  committed list. For any OTHER entry a future SDK build might reject, the failure still lands inside
  ONE session's own `try`/`catch` (`CopilotRunner.run`) and that session ends `error` -- which is NOT
  in `RETRY_ONCE` (`stages.ts`), so the workflow PARKS (`NEEDS_HUMAN`) at that session, not retried;
  earlier text here and in `docs/handoff-production.md` §1.5 said "retried once, like any other",
  which was wrong.

## Changing a rule

Every regex and list is an exported constant at the top of the relevant section of `policy.ts`;
change it there and nowhere else, and add the case to `orchestrator/test/policy.test.ts` first. The
tool-name regexes and `READ_TOOLS` are provisional: the first live run (Task 16) logs every
`toolName` and every `unrecognized-tool` denial into `workflows/<id>/audit.jsonl`, and that log is
the evidence for tightening them.
