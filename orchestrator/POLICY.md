# The permission policy, and what it is not

`orchestrator/policy.ts` decides every tool call an agent makes, from the `onPreToolUse` hook.
It is a pure function (`decide(role, wfId, toolName, toolArgs, segment?, root?)`) so every rule can
be unit-tested exhaustively — `orchestrator/test/policy.test.ts` is the specification.

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
   normalizes into another workflow, is denied for every tool — reads included.
2. **Paths.** `normalizeToolPath` unifies separators, strips `./`, resolves `.`/`..`, accepts an
   absolute path only inside the repository root, lower-cases the result, and denies anything that
   escapes. Every path-like argument is judged (`path`, `file_path`, `filePath`, `target`,
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
   Comments and string literals are stripped first; a second top-level statement is refused. A name
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
4. **Shell.** Any metacharacter (`;` `|` `&` `<` `>` `` ` `` `$(` `${` newline) and any interpreter
   flag (`-c`, `-e`, `-Command`, `-EncodedCommand`, `-File`) ends the call. What remains must be
   exactly one shape: a listing command, the role's own `<python> scripts/<script>.py <args>`, or
   `<python> -m pytest tests/parser_corpus[/…] [-q]` for parser-recovery. `<python>` is any of the
   spellings in `PYTHON_EXES`: bare `python`, `python3`, `py`, or the project's own venv by its
   relative path — `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (Unix). Agents are
   now told to always use the venv path, but **bare `python` stays allowed too** (ruling, F16
   final review): it was the documented, working form before that instruction existed, allowing it
   costs nothing (it is still exactly one interpreter invocation, still subject to every other
   check below), and narrowing it would only break agents that had not yet picked up the new
   instruction. A path is matched case-insensitively with separators normalized first (`normalizeToken`
   lower-cases and turns `\` into `/`), so `.venv\Scripts\python.exe` (the literal form a real
   Windows `powershell` tool call carries) matches the same entry as the forward-slash spelling —
   there is no separate Windows-specific rule to maintain. Any OTHER interpreter path (a system
   Python, a different venv, an absolute path even to this same venv) is not in `PYTHON_EXES` and
   is denied like any unrecognized command.
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
5. **Everything else is denied**, including any tool name that is not SQL, shell, write or on the
   read/planning allow-list (`READ_TOOLS`). Every such denial is audited as `unrecognized-tool`.

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
- **Bounds deny, they never skip.** The argument walker reaches `MAX_ARG_DEPTH` levels; arguments
  nested deeper are refused with `arguments too deeply nested to judge` rather than ignored.

## Changing a rule

Every regex and list is an exported constant at the top of the relevant section of `policy.ts`;
change it there and nowhere else, and add the case to `orchestrator/test/policy.test.ts` first. The
tool-name regexes and `READ_TOOLS` are provisional: the first live run (Task 16) logs every
`toolName` and every `unrecognized-tool` denial into `workflows/<id>/audit.jsonl`, and that log is
the evidence for tightening them.
