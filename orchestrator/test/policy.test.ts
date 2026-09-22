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
  CREATE OR REPLACE TABLE MIG_WORK.WF0003_SEG_02_OUT AS
  WITH t12_input AS (
    -- tool 12: Input Data
    SELECT ACCT, PERIOD, AMOUNT, POSTED_AT FROM IDENTIFIER(:SRC_DB || '.' || :SRC_SCHEMA || '.GL_LEDGER')
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
  MERGE INTO IDENTIFIER(:TGT_DB || '.' || :TGT_SCHEMA || '.GL_SUMMARY') t
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
