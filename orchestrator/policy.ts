// Per-role permission policy. Pure: no I/O, no clock, no filesystem — so every rule can be
// unit-tested exhaustively and the same function can back the onPreToolUse hook.
//
// THIS IS DEFENCE IN DEPTH, NOT A SANDBOX. It is a conservative textual check with no SQL parser
// and no shell parser: it will both miss exotic constructs and deny some legitimate ones. The
// PRIMARY boundary is the Snowflake role behind the MCP server — `MIGRATION_AGENT` has USAGE on
// MIG_WORK / MIG_GOLDEN, read on INFORMATION_SCHEMA and nothing on production (program spec
// §11.1) — and migrated procedures run `EXECUTE AS CALLER`, so they cannot exceed that role
// either. Shell tools are meant to run in the spec's container with the repo as the only writable
// mount. orchestrator/POLICY.md lists the known limitations; read it before trusting a rule here.
//
// FAIL-CLOSED. Anything the policy cannot judge is denied: an unrecognized tool name, a write
// with no readable path, arguments nested deeper than the walker reaches, SQL it cannot resolve to
// sandbox-only references, a shell command that is not exactly one allowed invocation, a flag that
// is not on that command's allow-list. Review round 1 closed three bypasses that came from judging
// raw substrings (traversal in paths, comments/literals in SQL, `includes()` on commands), so every
// rule below works on a NORMALIZED form of its argument; review round 2 closed a fourth — a
// "read-only" listing command is NOT side-effect-free (`git diff --output=<path>` writes a file),
// so listing flags are an allow-list per command, not an allowed shape.
//
// The tool-name regexes and READ_TOOLS are PROVISIONAL: tool spellings differ between CLI builds.
// The first live run logs every `toolName` (hooks.ts audits an `unrecognized-tool` event for each
// default-deny) and these constants get tightened from that log. Change them here, nowhere else.
import path from "node:path";
import type { AnalyzerBatch, Role } from "./types.ts";

export type Decision =
  | { permissionDecision: "allow" }
  | { permissionDecision: "deny"; permissionDecisionReason: string };

// ---------- tool classification ----------

export const SQL_TOOL = /snowflake|sql|query/i;
export const SHELL_TOOL = /^(bash|shell|powershell|pwsh|cmd)/i;
export const WRITE_TOOL = /^(edit|create|write|str_replace|apply_patch)/i;

/** Tools that only read or plan. Everything not classified at all is denied. */
export const READ_TOOLS = [
  "view",
  "read",
  "read_file",
  "grep",
  "glob",
  "ls",
  "list_directory",
  "search",
  "search_files",
  "find",
  "report_intent",
  "think",
  "todo",
  "update_todo",
  "ask_user",
  "task",
  "fetch_copilot_cli_documentation",
];

export const UNRECOGNIZED_TOOL = "unrecognized tool";

// ---------- paths ----------

/** Argument names that carry a path. Every match is judged, not just `path`. */
export const PATH_ARG_KEYS = /path|file|target|destination|dest|filename|dir(ectory)?$/i;
/**
 * Argument names that carry a write call's CONTENT, never a path (task-D fix round 1, G1): a
 * string under one of these is never path-scanned, even when the name matches `PATH_ARG_KEYS`
 * (`file_text` does — so the text of every `create` was judged as a path and denied). Compared
 * lower-cased; only a STRING is skipped — an object or array under one of them is still walked, so
 * a `path` nested inside it is judged as usual. Rule 1a (another workflow's folder mentioned
 * anywhere) still reads the content.
 */
export const CONTENT_ARG_KEYS = new Set(["file_text", "content", "new_str", "old_str", "text", "insert_line", "old_string", "new_string"]);

const COMPONENT = "[a-z0-9][a-z0-9_-]*";
const FILE_COMPONENT = "[a-z0-9][a-z0-9._-]*";
/** One or more path components ending in a file name; `..` cannot match it. */
const UNDER = `(?:${COMPONENT}/)*${FILE_COMPONENT}`;
const ID_PATTERN = /^[a-z0-9][a-z0-9_-]*$/;

export type PathCheck = { ok: true; path: string } | { ok: false; reason: string };

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The one path normalizer every rule runs first: separators unified, `.`/`..` resolved,
 * absolutes accepted only inside the repository root, result lower-cased and root-relative.
 */
export function normalizeToolPath(raw: string, root?: string): PathCheck {
  const text = String(raw ?? "").trim();
  if (!text) return { ok: false, reason: "empty path argument" };

  let candidate = text.replace(/\\/g, "/");
  const absolute = /^[A-Za-z]:(\/|$)/.test(candidate) || candidate.startsWith("/");
  if (absolute) {
    if (!root) return { ok: false, reason: `path outside the repository: ${text}` };
    const normalizedRoot = path.posix.normalize(root.replace(/\\/g, "/")).replace(/\/+$/, "").toLowerCase();
    const normalizedPath = path.posix.normalize(candidate).toLowerCase();
    if (!normalizedPath.startsWith(`${normalizedRoot}/`)) {
      return { ok: false, reason: `path outside the repository: ${text}` };
    }
    candidate = normalizedPath.slice(normalizedRoot.length + 1);
  }

  const normalized = path.posix.normalize(candidate).replace(/^\.\//, "");
  if (normalized === ".." || normalized.startsWith("../") || normalized === "." || normalized === "") {
    return { ok: false, reason: `path escapes the repository: ${text}` };
  }
  if (normalized.startsWith("/") || /^[A-Za-z]:/.test(normalized)) {
    return { ok: false, reason: `path outside the repository: ${text}` };
  }
  return { ok: true, path: normalized.toLowerCase() };
}

/** How deep the argument walker goes before it gives up — and denies. */
export const MAX_ARG_DEPTH = 6;

export interface PathScan {
  paths: string[];
  /** True when the walker hit its bound: the arguments cannot be judged, so they are denied. */
  tooDeep: boolean;
}

function collectPathValues(value: unknown, key: string, scan: PathScan, depth: number): void {
  if (depth > MAX_ARG_DEPTH) {
    scan.tooDeep = true;
    return;
  }
  if (typeof value === "string") {
    if (PATH_ARG_KEYS.test(key) && !CONTENT_ARG_KEYS.has(key.toLowerCase())) scan.paths.push(value);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectPathValues(item, key, scan, depth + 1);
    return;
  }
  if (value && typeof value === "object") {
    for (const [innerKey, innerValue] of Object.entries(value as Record<string, unknown>)) {
      collectPathValues(innerValue, innerKey, scan, depth + 1);
    }
  }
}

/** Every path-like argument, in argument order, and whether anything was out of reach. */
export function collectPathArgs(args: unknown): PathScan {
  const scan: PathScan = { paths: [], tooDeep: false };
  collectPathValues(args, "", scan, 0);
  return scan;
}

function firstString(args: unknown, keys: RegExp): string | undefined {
  if (!args || typeof args !== "object") return undefined;
  for (const [key, value] of Object.entries(args as Record<string, unknown>)) {
    if (typeof value === "string" && keys.test(key)) return value;
  }
  return undefined;
}

/** Which workflow folder a normalized path belongs to, if any. */
function workflowOf(normalized: string): string | undefined {
  return /^workflows\/([^/]+)\//.exec(normalized)?.[1];
}

// ---------- write lanes ----------

/** Write targets denied to every role, whatever its lane. First match wins. */
export const FORBIDDEN_WRITES: [RegExp, string][] = [
  [/(^|\/)golden\//, "golden data is read-only for every role"],
  [/^cookbook\//, "cookbook/ is read-only for every role; propose changes instead"],
  [/^scripts\/parse\.py$/, "scripts/parse.py is the core parser; extend it under scripts/parsers/ext/"],
  [/^scripts\/(?!parsers\/ext\/)/, "scripts/ is read-only for every role except scripts/parsers/ext/"],
  [/^\.github\//, ".github/ is read-only for every role"],
  [/^\.git\//, ".git/ is never written by an agent"],
  [/^node_modules\//, "node_modules/ is never written by an agent"],
  [/^\.venv\//, ".venv/ is never written by an agent"],
  [/^orchestrator\//, "the orchestrator's own code is read-only for every role"],
  [/^orchestrate\.ts$/, "the orchestrator's own code is read-only for every role"],
  [/^orchestrator\.config\.json$/, "the orchestrator's own configuration is read-only for every role"],
  [/^config\.json$/, "the Copilot CLI configuration is read-only for every role"],
  [/^docs\/spec\//, "the program spec is read-only for every role"],
  [/^samples\//, "samples/ is read-only for every role"],
];

/**
 * Fully anchored lanes; `null` means the role cannot be judged without a segment.
 *
 * `dbtProject` (a dbt workflow's translate-stage roles, which act on the whole project with no
 * segment — output-targets design §6, DV5) replaces the four segment lanes with the project's: the
 * translator/fixer get a NARROWED subset of `dbt/**` — the project files, every `.sql` model at any depth under `dbt/models/` and
 * exactly `dbt/models/sources.yml` and `dbt/models/schema.yml` (final fix wave C1.8: never a Python
 * model, a macro, a `packages.yml` or any other YAML dbt would read; `compile_check.py --target dbt`
 * and `lib.dbt_project.run_dbt` enforce the same closed file set, `dbt:surface`), never
 * `dbt/review.json` (the reviewer's verdict on them) nor `dbt/compile_check.json` (the script's) —
 * the reviewer gets `dbt/review.json` alone, and the validator every segment's reports, because
 * `validate_dbt.py` writes one per segment. Every other role keeps its own lanes.
 *
 * `analyzerBatch` (one call of a batched analyzer, Task W2) narrows the analyzer's lanes to that
 * batch's own segments' `contract.json` files and its two fragments, `analysis/<id>.md` and
 * `analysis/<id>.unsupported.json` — never another batch's contracts, never the stitched
 * `analysis.md` / `unsupported.json` (`scripts/stitch_analysis.py` writes those), never the manifest.
 *
 * Task W4: intake, analyzer and fixer each gain one more lane, `workflows/<wf>/notes/<role>.md` —
 * their own compaction memory aid, re-read on the orchestrator's say-so after a context
 * compaction (`CopilotRunner.run`, `hooks.ts`'s `notesPath`). The analyzer's notes lane is added
 * to BOTH its ordinary lanes and `analyzerBatchLanes` (a batched call must still be able to write
 * it), and the fixer's to both the segmented lane below and the dbt-project lane above.
 */
/** The dbt models lanes (final fix wave C1.8): SQL models at any depth under `dbt/models/`, plus exactly
 * the two YAML files — the closed file set `lib.dbt_project.check_surface` enforces (`dbt:surface`). */
function dbtModelLanes(wf: string): RegExp[] {
  return [
    new RegExp(`${wf}/dbt/models/(?:${COMPONENT}/)*${COMPONENT}\\.sql$`),
    new RegExp(`${wf}/dbt/models/(sources|schema)\\.yml$`),
  ];
}

function writeLanes(
  role: Role,
  id: string,
  segment?: string,
  dbtProject = false,
  analyzerBatch?: AnalyzerBatch,
): RegExp[] | null {
  const wf = `^workflows/${escapeRe(id)}`;
  if (dbtProject) {
    switch (role) {
      case "translator":
        // Lower case throughout: normalizeToolPath lower-cases every path before a lane sees it,
        // so `README.md` is matched as `readme.md`.
        return [
          new RegExp(`${wf}/dbt/(dbt_project\\.yml|profiles\\.yml|readme\\.md|translation_notes\\.md|fix_log\\.md)$`),
          ...dbtModelLanes(wf),
        ];
      case "fixer":
        // Same lanes as the translator's, plus the fixer's own notes file (Task W4) — the notes
        // path carries no segment or dbt scope, so it is the same lane in every mode the fixer runs.
        return [
          new RegExp(`${wf}/dbt/(dbt_project\\.yml|profiles\\.yml|readme\\.md|translation_notes\\.md|fix_log\\.md)$`),
          ...dbtModelLanes(wf),
          new RegExp(`${wf}/notes/fixer\\.md$`),
        ];
      case "reviewer":
        return [new RegExp(`${wf}/dbt/review\\.json$`)];
      case "validator":
        return [new RegExp(`${wf}/segments/${COMPONENT}/validation[a-z0-9._-]*\\.json$`)];
      default:
        break; // every other role keeps its own lanes
    }
  }
  const seg = segment?.toLowerCase();
  const segmented = seg !== undefined && ID_PATTERN.test(seg) ? escapeRe(seg) : null;
  switch (role) {
    case "intake":
      return [
        new RegExp(`${wf}/intake/${UNDER}$`),
        new RegExp(`${wf}/manifest\\.json$`),
        new RegExp(`^mappings/${UNDER}$`),
        new RegExp(`${wf}/notes/intake\\.md$`),
      ];
    case "analyzer":
      if (analyzerBatch) return analyzerBatchLanes(wf, analyzerBatch);
      return [
        new RegExp(`${wf}/segments/${COMPONENT}/contract\\.json$`),
        new RegExp(`${wf}/(analysis\\.md|unsupported\\.json|manifest\\.json)$`),
        new RegExp(`${wf}/notes/analyzer\\.md$`),
      ];
    case "translator":
      return segmented === null
        ? null
        : [new RegExp(`${wf}/segments/${segmented}/(proc\\.sql|proc\\.py|translation_notes\\.md|fix_log\\.md)$`)];
    case "fixer":
      return segmented === null
        ? null
        : [
            new RegExp(`${wf}/segments/${segmented}/(proc\\.sql|proc\\.py|translation_notes\\.md|fix_log\\.md)$`),
            new RegExp(`${wf}/notes/fixer\\.md$`),
          ];
    case "reviewer":
      return segmented === null ? null : [new RegExp(`${wf}/segments/${segmented}/review\\.json$`)];
    case "validator":
      return segmented === null
        ? null
        : [new RegExp(`${wf}/segments/${segmented}/validation[a-z0-9._-]*\\.json$`)];
    case "documenter":
      return [new RegExp(`${wf}/docs/${UNDER}$`)];
    case "parser-recovery":
      return [
        new RegExp(`^scripts/parsers/ext/${UNDER}$`),
        new RegExp(`^tests/parser_corpus/${UNDER}$`),
        new RegExp(`${wf}/parsed/${UNDER}$`),
      ];
    default:
      return null;
  }
}

/**
 * The batched analyzer's lanes (Task W2). Each id is judged in the same lower-cased form every path
 * is, and one that is not a plain id (`ID_PATTERN`: no separator, no dot, no regex metacharacter)
 * opens nothing — fail-closed, so a malformed batch can only deny more.
 */
function analyzerBatchLanes(wf: string, batch: AnalyzerBatch): RegExp[] {
  const lanes: RegExp[] = [];
  const segments = (batch.segments ?? [])
    .map((seg) => String(seg).toLowerCase())
    .filter((seg) => ID_PATTERN.test(seg))
    .map(escapeRe);
  if (segments.length > 0) lanes.push(new RegExp(`${wf}/segments/(${segments.join("|")})/contract\\.json$`));
  const batchId = String(batch.id ?? "").toLowerCase();
  if (ID_PATTERN.test(batchId)) lanes.push(new RegExp(`${wf}/analysis/${escapeRe(batchId)}\\.(md|unsupported\\.json)$`));
  // Task W4: the analyzer's notes file is not batch-scoped -- every batch of the same workflow
  // may write the same one lane.
  lanes.push(new RegExp(`${wf}/notes/analyzer\\.md$`));
  return lanes;
}

// ---------- SQL ----------

export const SANDBOX_SCHEMAS = ["MIG_WORK", "MIG_GOLDEN"];
export const CATALOG_SCHEMA = "INFORMATION_SCHEMA";
export const DESTRUCTIVE_SQL = /\b(DROP|TRUNCATE|GRANT|REVOKE|USE|ALTER\s+(ACCOUNT|USER|ROLE))\b/i;
export const CATALOG_READ_SQL = /^(select|show|desc|describe)\b/i;
export const VALIDATOR_SCHEMAS = [...SANDBOX_SCHEMAS, CATALOG_SCHEMA];
/**
 * Databases a three-part object reference may name. Overridden from `orchestrator.config.json`
 * (`policy.sandboxDatabases`), because the sandbox database's name is deployment-specific.
 */
export const SANDBOX_DATABASES = ["MIGDB"];
/** Intake also reads the pipeline's own sanitized catalog copy. */
export const INTAKE_CATALOG_OBJECTS = ["MIG_WORK.CATALOG_COLUMNS"];

/** A name in one of these positions IS an object: a table, view, stage or procedure. */
const OBJECT_INTRODUCERS = new Set([
  "FROM", "JOIN", "INTO", "USING", "TABLE", "UPDATE", "CALL", "PROCEDURE", "FUNCTION", "VIEW",
  "STAGE", "CLONE", "LIKE",
]);
/** Only these may name a CTE declared in the same statement instead of a qualified object. */
const CTE_INTRODUCERS = new Set(["FROM", "JOIN"]);
/** Ends a FROM list, so a comma after one of these is a select-list comma, not a comma join. */
const FROM_CLAUSE_END = new Set([
  "WHERE", "GROUP", "ORDER", "HAVING", "QUALIFY", "WINDOW", "LIMIT", "OFFSET", "UNION", "INTERSECT",
  "EXCEPT", "MINUS", "ON", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "NATURAL", "LATERAL",
  "USING", "SET", "VALUES", "WHEN", "RETURNING", "INTO", "SELECT",
]);
/**
 * Words that are never an object, an alias or a CTE name, and after which `(` opens a group
 * rather than a function call. Anything missing here can only make the policy stricter.
 */
const SQL_KEYWORDS = new Set([
  "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "HAVING", "QUALIFY", "WINDOW", "LIMIT",
  "OFFSET", "UNION", "INTERSECT", "EXCEPT", "MINUS", "ALL", "ANY", "SOME", "DISTINCT", "AS", "ON",
  "USING", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "NATURAL", "LATERAL", "AND",
  "OR", "NOT", "IN", "IS", "NULL", "LIKE", "ILIKE", "RLIKE", "BETWEEN", "CASE", "WHEN", "THEN",
  "ELSE", "END", "EXISTS", "WITH", "RECURSIVE", "VALUES", "SET", "INSERT", "UPDATE", "DELETE",
  "MERGE", "MATCHED", "INTO", "CREATE", "REPLACE", "TEMPORARY", "TEMP", "TRANSIENT", "SECURE",
  "MATERIALIZED", "TABLE", "VIEW", "PROCEDURE", "FUNCTION", "STAGE", "SCHEMA", "DATABASE",
  "WAREHOUSE", "SEQUENCE", "STREAM", "TASK", "PIPE", "FILE", "FORMAT", "COPY", "PUT", "GET", "CALL",
  "RETURNS", "RETURN", "LANGUAGE", "EXECUTE", "IMMEDIATE", "CALLER", "OWNER", "BEGIN", "DECLARE",
  "IF", "EXISTS", "CLONE", "SWAP", "TO", "OVER", "PARTITION", "ROWS", "RANGE", "PRECEDING",
  "FOLLOWING", "CURRENT", "ROW", "UNBOUNDED", "ASC", "DESC", "NULLS", "FIRST", "LAST", "SESSION",
  "ALTER", "DROP", "TRUNCATE", "GRANT", "REVOKE", "USE", "OF", "FOR", "AT", "BEFORE", "CHANGES",
  "ONLY", "TOP", "LIMIT", "SAMPLE", "TABLESAMPLE", "PIVOT", "UNPIVOT", "CONNECT", "START",
]);

const IDENT = '(?:"[^"]*"|[A-Za-z_][A-Za-z0-9_$]*)';
const QUALIFIED_NAME = new RegExp(`${IDENT}(?:\\.${IDENT})*`);
const SQL_TOKEN = new RegExp(`${IDENT}(?:\\.${IDENT})*|@[^\\s,;()]+|[(),;]|\\S`, "g");
/**
 * Contract C4's table reference (Task C4V; see the plan's C4). Snowflake documents
 * `IDENTIFIER( { string_literal | session_variable | bind_variable | snowflake_scripting_variable } )`
 * -- one value, not an expression -- so a procedure body builds each mapped table's name with
 * `LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>'` (or the `TGT` twin,
 * `<LOGICAL>_TGT`) and references it as `IDENTIFIER(:<LOGICAL>_SRC)`. Inside the LET the arguments
 * are named WITHOUT a colon -- Snowflake's Scripting documentation uses the colon to bind a variable
 * inside a SQL statement, not in an expression -- and `IDENTIFIER(:<LOGICAL>_SRC)`, inside a SQL
 * statement, keeps it (fix round 1). That is the only `IDENTIFIER(` form a body may use, and only
 * after its LET: the name's schema comes from the procedure's own SRC/TGT parameters, which a CALL is
 * checked against separately (B5). The keywords are read case-insensitively; the rest must be exactly
 * the rule, as `compile_check.py`'s `c4:let_form` reads it. Nothing here has run on Snowflake; the
 * first real-account run confirms the documented form.
 */
const CONTRACT_LET =
  /^[Ll][Ee][Tt]\s+([A-Z_][A-Z0-9_$]*)\s+[Vv][Aa][Rr][Cc][Hh][Aa][Rr]\s*:=\s*(SRC|TGT)_DB\s*\|\|\s*'\.'\s*\|\|\s*(SRC|TGT)_SCHEMA\s*\|\|\s*'\.([A-Z_][A-Z0-9_$]*)'$/;
const CONTRACT_LET_RULE =
  "LET <LOGICAL>_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.<LOGICAL>' (or the TGT twin, <LOGICAL>_TGT), the arguments named without a colon";
/**
 * Fix round 2 (C1). Contract C4 bodies are FLAT -- rule-conforming LETs, SQL statements and one
 * `RETURN '<literal>'` -- so no statement of a judged body may start with a Snowflake Scripting block or
 * control keyword (the same refusal as `proc_runner`'s `_SCRIPTING_KEYWORDS`): each of them opens a
 * scope or a branch in which a LET variable, or a parameter a LET reads, could be given another value.
 */
const SCRIPTING_KEYWORDS = new Set([
  "BEGIN", "END", "IF", "ELSEIF", "ELSE", "CASE", "FOR", "WHILE", "REPEAT", "LOOP", "BREAK", "CONTINUE",
  "EXCEPTION", "DECLARE", "OPEN", "FETCH", "CLOSE", "RAISE", "AWAIT", "CANCEL", "NULL",
]);
/** The SQL statements a judged body may hold besides its LETs and its RETURN (fail-closed allow-list). */
const BODY_STATEMENT_VERBS = new Set(["SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "TRUNCATE", "DROP", "COPY"]);
/** Fix round 2 (c): the exact contract-C4 parameter list (types case-insensitive), as compile_check requires it. */
const C4_PARAMETERS = ["SRC_DB STRING", "SRC_SCHEMA STRING", "TGT_DB STRING", "TGT_SCHEMA STRING", "RUN_ID STRING"];
/** A colon-prefixed name on a LET's right-hand side (after `:=`): the binding syntax, not an expression's. */
const LET_COLON_NAME = /:=[\s\S]*:\s*[A-Za-z_]/;
/** `IDENTIFIER(:<name>)`: the one argument shape a declared contract variable is referenced by. */
const IDENTIFIER_VARIABLE = /IDENTIFIER\s*\(\s*:([A-Za-z_][A-Za-z0-9_$]*)\s*\)/gi;

/** Removes line and block comments and replaces every string literal with `''`. */
export function stripSqlNoise(sql: string): string {
  let out = "";
  for (let i = 0; i < sql.length; i++) {
    const char = sql[i];
    if (char === "-" && sql[i + 1] === "-") {
      while (i < sql.length && sql[i] !== "\n") i++;
      out += " ";
      continue;
    }
    if (char === "/" && sql[i + 1] === "*") {
      i += 2;
      while (i < sql.length && !(sql[i] === "*" && sql[i + 1] === "/")) i++;
      i++;
      out += " ";
      continue;
    }
    if (char === "'") {
      i++;
      while (i < sql.length) {
        if (sql[i] === "\\") {
          i += 2; // Snowflake's backslash escape: \' does not close the string (fix round 2)
          continue;
        }
        if (sql[i] === "'") {
          if (sql[i + 1] === "'") {
            i += 2;
            continue;
          }
          break;
        }
        i++;
      }
      out += "''";
      continue;
    }
    if (char === '"') {
      // A quoted identifier is kept verbatim, but a quote or comment marker inside it is not one
      // (fix round 2): `"a'b"` must not open a string that hides the code after it.
      let end = i + 1;
      while (end < sql.length && !(sql[end] === '"' && sql[end + 1] !== '"')) end += sql[end] === '"' ? 2 : 1;
      out += sql.slice(i, end + 1);
      i = end;
      continue;
    }
    out += char;
  }
  return out;
}

const unquote = (ident: string): string => ident.replace(/^"|"$/g, "").toUpperCase();

/** `a.b.c` → ["A","B","C"], quotes removed. */
function components(name: string): string[] {
  return name.split(".").map(unquote).filter((part) => part.length > 0);
}

/** The schema part of a reference: `a.b` → a, `a.b.c` → b (database.schema.object). */
function schemaOf(reference: string): string | undefined {
  const parts = components(reference);
  return parts.length >= 2 ? parts[parts.length - 2] : undefined;
}

interface SqlToken {
  text: string;
  upper: string;
  kind: "name" | "stage" | "punct" | "other";
}

export function tokenizeSql(sql: string): SqlToken[] {
  const tokens: SqlToken[] = [];
  for (const match of sql.matchAll(SQL_TOKEN)) {
    const text = match[0];
    const kind =
      text.startsWith("@") ? "stage"
      : text.length === 1 && "(),;".includes(text) ? "punct"
      : /^["A-Za-z_]/.test(text) ? "name"
      : "other";
    tokens.push({ text, upper: text.toUpperCase(), kind });
  }
  return tokens;
}

/** Splits on top-level `;`, ignoring the ones inside comments and string literals. */
export function splitStatements(sql: string): string[] {
  const out: string[] = [];
  let current = "";
  for (let i = 0; i < sql.length; i++) {
    const char = sql[i];
    if (char === "-" && sql[i + 1] === "-") {
      while (i < sql.length && sql[i] !== "\n") current += sql[i++];
      current += "\n";
      continue;
    }
    if (char === "/" && sql[i + 1] === "*") {
      while (i < sql.length && !(sql[i] === "*" && sql[i + 1] === "/")) current += sql[i++];
      current += sql.slice(i, i + 2);
      i++;
      continue;
    }
    if (char === "'") {
      current += char;
      i++;
      while (i < sql.length) {
        current += sql[i];
        if (sql[i] === "\\" && i + 1 < sql.length) {
          current += sql[++i]; // Snowflake's backslash escape (fix round 2)
          i++;
          continue;
        }
        if (sql[i] === "'") {
          if (sql[i + 1] === "'") {
            current += sql[++i];
            i++;
            continue;
          }
          break;
        }
        i++;
      }
      continue;
    }
    if (char === '"') {
      // A quoted identifier: a `;`, quote or comment marker inside it is part of the name (fix round 2).
      let end = i + 1;
      while (end < sql.length && !(sql[end] === '"' && sql[end + 1] !== '"')) end += sql[end] === '"' ? 2 : 1;
      current += sql.slice(i, end + 1);
      i = end;
      continue;
    }
    if (char === ";") {
      out.push(current);
      current = "";
      continue;
    }
    current += char;
  }
  out.push(current);
  return out.filter((statement) => statement.trim().length > 0);
}

/** Every `IDENTIFIER(:<VAR>)` whose VAR a matching LET declared becomes the sandbox name it stands for. */
/**
 * Fix round 3 (1): EXTERNAL LOCATIONS. The object scanner below never reads a string literal as an
 * object, so a statement that names an external location by URL -- `COPY INTO 's3://…' FROM <sandbox
 * table>`, a stage created with a `URL` -- used to pass every sandbox check. Denied for every role, in
 * and out of procedure bodies: a statement that names credentials; an integration, an external function
 * or a network rule; `COPY INTO` a string literal, or `COPY … FROM` one; `CREATE`/`ALTER STAGE` with
 * `URL`, `STORAGE_INTEGRATION`, `CREDENTIALS` or `ENCRYPTION`; `GET`/`PUT` (a file on the machine that
 * runs the agent); `LIST`/`REMOVE` of anything but a stage. An internal named stage under a sandbox
 * schema (`@MIG_WORK.x`) stays as it was: B1 still requires it to be sandbox-qualified. What this cannot
 * see is a stage a human created outside the policy with an external URL -- the role behind the MCP
 * server must hold no usage on such a stage or on any integration (POLICY.md, known limitations).
 */
const CREDENTIAL_WORDS = /\bCREDENTIALS\s*=|AWS_KEY_ID|AWS_SECRET_KEY|AWS_TOKEN|AZURE_SAS_TOKEN|PRIVATE_KEY|MASTER_KEY/i;
const EXTERNAL_OBJECTS =
  /\b(?:STORAGE|API|NOTIFICATION|SECURITY|EXTERNAL\s+ACCESS)\s+INTEGRATION\b|\bEXTERNAL\s+FUNCTION\b|\bNETWORK\s+RULE\b|\b(?:STORAGE_INTEGRATION|API_INTEGRATION|EXTERNAL_ACCESS_INTEGRATIONS)\b/i;

function externalLocationReason(stripped: string): string | undefined {
  const reach = (what: string) => `external-location: ${what} may not move data outside the sandbox`;
  if (CREDENTIAL_WORDS.test(stripped)) {
    return reach("a statement that names credentials (CREDENTIALS=, AWS_KEY_ID, AZURE_SAS_TOKEN, PRIVATE_KEY, …)");
  }
  const external = EXTERNAL_OBJECTS.exec(stripped);
  if (external) return reach(`${external[0].toUpperCase().replace(/\s+/g, " ")} (an integration, external function or network rule)`);
  if (/^COPY\s+INTO\s+''/i.test(stripped)) return reach("COPY INTO an external URL");
  if (/^COPY\s+INTO\b[\s\S]*?\bFROM\s+''/i.test(stripped)) return reach("COPY … FROM an external URL (in either direction)");
  if (/^(?:CREATE(?:\s+OR\s+REPLACE)?(?:\s+(?:TEMPORARY|TEMP))?|ALTER)\s+STAGE\b/i.test(stripped) &&
      /\b(?:URL|STORAGE_INTEGRATION|CREDENTIALS|ENCRYPTION)\s*=/i.test(stripped)) {
    return reach("a stage with URL, STORAGE_INTEGRATION, CREDENTIALS or ENCRYPTION (an external or keyed stage)");
  }
  if (/^(?:GET|PUT)\b/i.test(stripped)) return reach("GET or PUT (a file on the machine that runs the agent)");
  if (/^(?:LIST|LS|REMOVE|RM)\s+(?!@)/i.test(stripped)) return reach("LIST or REMOVE of an external location");
  return undefined;
}

/** Fix round 3 (2): the words in front of an IDENTIFIER(…) that make it a write target or a source read. */
const TABLE_MODIFIERS = new Set(["OR", "REPLACE", "TRANSIENT", "TEMPORARY", "TEMP", "VOLATILE", "LOCAL", "GLOBAL", "TABLE", "IF", "NOT", "EXISTS"]);
function identifierRole(codeBefore: string): "write" | "read" | undefined {
  const words = (codeBefore.match(/[A-Za-z_][A-Za-z0-9_$]*|[(),;]/g) ?? []).map((word) => word.toUpperCase());
  const last = words[words.length - 1];
  const previous = words[words.length - 2];
  if (last === "INTO" || last === "UPDATE") return "write";
  if (last === "FROM") return previous === "DELETE" ? "write" : "read";
  if (last === "JOIN" || last === "USING") return "read";
  let position = words.length - 1;
  while (position >= 0 && TABLE_MODIFIERS.has(words[position])) position -= 1;
  return position >= 0 && (words[position] === "CREATE" || words[position] === "TRUNCATE") ? "write" : undefined;
}

/** A `<LOGICAL>_SRC` name is only ever read and a `<LOGICAL>_TGT` name only ever written, as compile_check's c4:identifier_role holds them. */
function contractRoleReason(original: string, declared: Map<string, string>): string | undefined {
  const code = stripSqlNoise(original);
  for (const match of code.matchAll(IDENTIFIER_VARIABLE)) {
    const standsFor = declared.get(match[1].toUpperCase());
    if (!standsFor) continue;
    const role = identifierRole(code.slice(0, match.index));
    if (standsFor.startsWith("MIG_WORK.CONTRACT_SRC_") && role === "write") {
      return `IDENTIFIER(:${match[1]}) is written to; a <LOGICAL>_SRC name is only ever read (contract C4)`;
    }
    if (standsFor.startsWith("MIG_WORK.CONTRACT_TGT_") && role === "read") {
      return `IDENTIFIER(:${match[1]}) is read as a source; a <LOGICAL>_TGT name is only ever written (contract C4)`;
    }
  }
  return undefined;
}

function replaceContractIdentifiers(text: string, declared: Map<string, string>): string {
  return text.replace(IDENTIFIER_VARIABLE, (whole, name: string) => declared.get(name.toUpperCase()) ?? whole);
}

/** The statement without the comments in front of it (string literals kept). */
function withoutLeadingComments(statement: string): string {
  let text = statement.trim();
  for (;;) {
    if (text.startsWith("--")) {
      const end = text.indexOf("\n");
      text = end < 0 ? "" : text.slice(end + 1).trim();
    } else if (text.startsWith("/*")) {
      const end = text.indexOf("*/");
      text = end < 0 ? "" : text.slice(end + 2).trim();
    } else {
      return text;
    }
  }
}

/** A LET in a procedure body: declared on a match with contract C4's rule, a denial reason otherwise. */
function declareContractVariable(statement: string, declared: Map<string, string>): string | undefined {
  if (LET_COLON_NAME.test(stripSqlNoise(statement))) {
    return (
      "a LET names the procedure's arguments without a colon (Snowflake's documented expression syntax; " +
      `the colon binds a variable inside a SQL statement, as in IDENTIFIER(:<VAR>)): ${CONTRACT_LET_RULE}`
    );
  }
  const match = CONTRACT_LET.exec(statement);
  const [, name, side, schemaSide, logical] = match ?? [];
  if (!match || side !== schemaSide || name !== `${logical}_${side}`) {
    return `a LET must build a contract-C4 name: ${CONTRACT_LET_RULE}`;
  }
  if (declared.has(name)) return `LET ${name} declares ${name} twice`;
  declared.set(name, `MIG_WORK.CONTRACT_${side}_${logical}`);
  return undefined;
}

/** Splits an argument list on top-level commas. */
function splitArguments(text: string): string[] {
  const args: string[] = [];
  let depth = 0;
  let current = "";
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === "'") {
      current += char;
      i++;
      while (i < text.length) {
        current += text[i];
        if (text[i] === "'") {
          if (text[i + 1] === "'") {
            current += text[++i];
            i++;
            continue;
          }
          break;
        }
        i++;
      }
      continue;
    }
    if (char === "(") depth++;
    if (char === ")") depth--;
    if (char === "," && depth === 0) {
      args.push(current);
      current = "";
      continue;
    }
    current += char;
  }
  if (current.trim().length > 0 || args.length > 0) args.push(current);
  return args;
}

interface SqlRules {
  /** Schemas an object reference may live in. */
  allowed: string[];
  /** How the denial names them. */
  label: string;
  /** Databases a three-part object reference may live in. */
  databases: string[];
  /** Fully-named objects allowed on top of the schema rule, as `SCHEMA.OBJECT`. */
  extraObjects?: string[];
  /**
   * The contract-C4 variables declared so far in a procedure body (upper-cased name → the sandbox
   * name it stands for): `IDENTIFIER(:<VAR>)` is accepted for these and nothing else. Undefined
   * outside a procedure body, where no `IDENTIFIER(` is accepted at all.
   */
  contractVariables?: Map<string, string>;
}

/**
 * One top-level statement against rules B1–B3. Returns a denial reason, or undefined.
 * This is a conservative textual check, not a SQL parser — see the file header.
 */
function checkStatement(original: string, rules: SqlRules): string | undefined {
  if (rules.contractVariables) {
    const role = contractRoleReason(original, rules.contractVariables);
    if (role) return role;
  }
  const text = rules.contractVariables ? replaceContractIdentifiers(original, rules.contractVariables) : original;
  const stripped = stripSqlNoise(text).trim();
  if (!stripped) return undefined;

  if (/;\s*\S/.test(stripped)) return "multiple statements in one call";
  const external = externalLocationReason(stripped);
  if (external) return external;
  if (DESTRUCTIVE_SQL.test(stripped)) return "destructive SQL is denied for every role";
  if (/\bEXECUTE\s+IMMEDIATE\b/i.test(stripped)) return "EXECUTE IMMEDIATE cannot be judged by this policy";
  if (/\bIDENTIFIER\s*\(/i.test(stripped)) return "dynamic object name: IDENTIFIER( cannot be judged by this policy";

  const tokens = tokenizeSql(stripped);

  // B3: TABLE( must wrap a function that is itself sandbox-qualified.
  for (let i = 0; i + 2 < tokens.length; i++) {
    if (tokens[i].upper !== "TABLE" || tokens[i + 1].text !== "(") continue;
    const inner = tokens[i + 2];
    if (inner.kind !== "name" || !rules.allowed.includes(schemaOf(inner.text) ?? "")) {
      return `TABLE( must wrap a function in ${rules.label} (${inner.text})`;
    }
  }

  const aliases = new Set<string>();
  const ctes = new Set<string>();
  const tableNames = new Set<string>();
  const objects = new Map<number, string>(); // token index → the introducer that made it an object
  const callContext: boolean[] = [];
  let depth = 0;
  let fromDepth = -1;

  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    const next = tokens[i + 1];

    if (token.text === "(") {
      const previous = tokens[i - 1];
      callContext.push(previous !== undefined && previous.kind === "name" && !SQL_KEYWORDS.has(previous.upper));
      depth += 1;
      continue;
    }
    if (token.text === ")") {
      callContext.pop();
      depth -= 1;
      if (fromDepth > depth) fromDepth = -1;
      continue;
    }
    const inCall = callContext[callContext.length - 1] === true;

    // B1: a comma inside a FROM list is a comma join, which this policy does not read.
    if (fromDepth >= 0 && depth === fromDepth) {
      if (token.text === ",") return "comma join not supported by the policy; use JOIN";
      if (token.kind === "name" && FROM_CLAUSE_END.has(token.upper)) fromDepth = -1;
    }

    // A CTE is `<name> AS (`.
    if (token.upper === "AS" && next?.text === "(") {
      const declared = tokens[i - 1];
      if (declared?.kind === "name" && !SQL_KEYWORDS.has(declared.upper) && !declared.text.includes(".")) {
        ctes.add(unquote(declared.text));
      }
    }

    // Every stage reference is an object reference.
    if (token.kind === "stage") objects.set(i, "@");

    // A qualified name applied to an argument list is a function or procedure call.
    if (token.kind === "name" && token.text.includes(".") && next?.text === "(") objects.set(i, "(");

    // `ALTER TABLE x SWAP WITH y` names its second object after WITH.
    const introduces = OBJECT_INTRODUCERS.has(token.upper) || (token.upper === "WITH" && tokens[i - 1]?.upper === "SWAP");
    if (token.kind !== "name" || inCall || !introduces) continue;
    if (token.upper === "FROM") fromDepth = depth;
    if (next === undefined) continue;
    if (next.kind !== "name" && next.kind !== "stage") continue;
    if (next.kind === "name" && SQL_KEYWORDS.has(next.upper)) continue;

    objects.set(i + 1, token.upper);
    const afterName = tokens[i + 2];
    const alias =
      afterName?.upper === "AS" ? tokens[i + 3]
      : afterName?.kind === "name" && !SQL_KEYWORDS.has(afterName.upper) ? afterName
      : undefined;
    if (alias?.kind === "name" && !SQL_KEYWORDS.has(alias.upper) && !alias.text.includes(".")) {
      aliases.add(unquote(alias.text));
    }
  }

  // B1: every object must be sandbox-qualified, or a CTE named in FROM/JOIN.
  for (const [index, introducer] of objects) {
    const token = tokens[index];
    const reference = token.kind === "stage" ? token.text.slice(1) : token.text;
    const parts = components(reference);
    if (parts.length < 2) {
      if (token.kind !== "stage" && CTE_INTRODUCERS.has(introducer) && ctes.has(parts[0] ?? "")) continue;
      if (token.kind === "stage") return `SQL must target ${rules.label} (${token.text})`;
      return `unqualified target ${token.text}`;
    }
    const schema = parts[parts.length - 2];
    const object = `${schema}.${parts[parts.length - 1]}`;
    if (!rules.allowed.includes(schema) && !(rules.extraObjects ?? []).includes(object)) {
      return `SQL must target ${rules.label} (${token.text})`;
    }
    // A three-part name says which database as well, and only the sandbox ones are allowed.
    // A two-part name resolves against the session's current database (see POLICY.md).
    if (parts.length >= 3 && !rules.databases.includes(parts[parts.length - 3])) {
      return `SQL must target the sandbox database ${rules.databases.join(", ")} (${token.text})`;
    }
    tableNames.add(parts[parts.length - 1]);
  }

  // B2: anything else qualified is a column reference, and its first part must be explained.
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token.kind !== "name" || objects.has(i) || !token.text.includes(".")) continue;
    const first = components(token.text)[0];
    if (aliases.has(first) || ctes.has(first) || tableNames.has(first) || rules.allowed.includes(first)) continue;
    return `unexplained qualified reference: ${token.text}`;
  }
  return undefined;
}

/**
 * B5: a CALL must match the contract-C4 signature, name a sandbox database in SRC_DB/TGT_DB and a
 * `MIG_` schema in SRC_SCHEMA/TGT_SCHEMA — the procedure resolves its sources and targets from
 * those four arguments, so they are how a sandbox procedure could otherwise reach production.
 */
function checkCallSignature(original: string, databases: string[]): string | undefined {
  const call = new RegExp(`^\\s*CALL\\s+${QUALIFIED_NAME.source}\\s*\\(([\\s\\S]*)\\)\\s*;?\\s*$`, "i").exec(
    original.trim(),
  );
  if (!call) return undefined;
  const args = splitArguments(call[1]).map((argument) => argument.trim());
  if (args.length !== 5) {
    return "CALL does not match the contract signature (SRC_DB, SRC_SCHEMA, TGT_DB, TGT_SCHEMA, RUN_ID)";
  }
  for (const [index, name] of [[0, "SRC_DB"], [2, "TGT_DB"]] as const) {
    const literal = /^'([^']*)'$/.exec(args[index]);
    if (!literal || !databases.includes(literal[1].toUpperCase())) {
      return `CALL ${name} must be a literal sandbox database ${databases.join(", ")} (${args[index]})`;
    }
  }
  for (const [index, name] of [[1, "SRC_SCHEMA"], [3, "TGT_SCHEMA"]] as const) {
    if (!/^'MIG_[^']*'$/i.test(args[index])) {
      return `CALL ${name} must be a literal MIG_ schema (${args[index]})`;
    }
  }
  return undefined;
}

const DOLLAR_QUOTED = /^([\s\S]*?)\$\$([\s\S]*?)\$\$\s*;?\s*$/;

/**
 * B4: a procedure body is not opaque. The header is checked as a statement, then every body
 * statement is checked in turn, with contract C4's `LET` + `IDENTIFIER(:<VAR>)` form allowed there.
 */
function checkProcedure(original: string, rules: SqlRules): string | undefined | null {
  const match = DOLLAR_QUOTED.exec(original.trim());
  if (!match) return null; // not a dollar-quoted statement: the caller checks it normally
  const [, header, body] = match;
  if (!/^\s*CREATE\s+(OR\s+REPLACE\s+)?PROCEDURE\b/i.test(header)) {
    return "a dollar-quoted body is only judged inside CREATE PROCEDURE";
  }
  const name = new RegExp(`PROCEDURE\\s+(${QUALIFIED_NAME.source})`, "i").exec(header)?.[1] ?? "";
  if (schemaOf(name) !== "MIG_WORK") {
    return `a procedure must be created as MIG_WORK.<name> (${name || "unnamed"})`;
  }

  const headerReason = checkStatement(header, { ...rules, contractVariables: undefined });
  if (headerReason) return headerReason;

  const inner = body
    .trim()
    .replace(/^BEGIN\b/i, "")
    .replace(/\bEND\s*;?\s*$/i, "")
    .trim();
  // Contract C4 (Task C4V, fix round 2): a LET declares a variable, and IDENTIFIER(:<VAR>) is trusted
  // only because NOTHING else in the body can give that variable -- or a parameter the LET reads --
  // another value. So the body must be flat: every statement other than a rule-conforming top-level
  // LET is denied if it starts with a Snowflake Scripting block or control keyword, if its code
  // (comments removed, strings blanked) contains `:=` or the word LET anywhere, if it is a CALL, if it
  // is a RETURN of anything but one string literal, if it writes `INTO :<var>`, or if it is not one of
  // the SQL statements BODY_STATEMENT_VERBS lists; what remains is judged by B1-B3 as before.
  const declared = new Map<string, string>();
  for (const statement of splitStatements(inner)) {
    const trimmed = statement.trim();
    const code = stripSqlNoise(trimmed).trim();
    if (!code) continue; // nothing but a comment
    const first = (/^[^\s(;]*/.exec(code)?.[0] ?? "").toUpperCase();
    if (first === "LET") {
      const reason = declareContractVariable(withoutLeadingComments(trimmed), declared);
      if (reason) return reason;
      continue;
    }
    if (SCRIPTING_KEYWORDS.has(first)) {
      return `${first} cannot be judged by this policy: a contract-C4 procedure body is flat (LETs, SQL statements and one RETURN '<literal>'), with no Snowflake Scripting block or control statement`;
    }
    if (code.includes(":=")) return "assigning a variable (:=) in a procedure body cannot be judged by this policy";
    if (/\bLET\b/i.test(code)) return "LET is accepted only as a whole statement of contract C4's form, never inside another";
    if (first === "RETURN") {
      if (!/^RETURN\s*''$/i.test(code)) return "RETURN in a procedure body must return one string literal (RETURN '<literal>')";
      continue;
    }
    if (first === "CALL") return "CALL inside a procedure body cannot be judged by this policy (a contract-C4 segment procedure never calls another)";
    if (/^ALTER\s+SESSION\s+SET\b/i.test(code)) continue;
    if (/\bINTO\s*:/i.test(code)) return "INTO :<variable> cannot be judged by this policy";
    if (!BODY_STATEMENT_VERBS.has(first)) {
      return `${first || code.slice(0, 20)} is not a SQL statement this policy judges in a procedure body (contract C4: LETs, SQL statements and one RETURN '<literal>')`;
    }
    const reason = checkStatement(trimmed, { ...rules, contractVariables: declared });
    if (reason) return reason;
  }
  return headerParametersReason(header);
}

/** Fix round 2 (c): the parameters are exactly contract C4's, so a CALL's five arguments mean what B5 checks. */
function headerParametersReason(header: string): string | undefined {
  const list = new RegExp(`PROCEDURE\\s+${QUALIFIED_NAME.source}\\s*\\(([^)]*)\\)`, "i").exec(stripSqlNoise(header))?.[1] ?? "";
  const found = list.split(",").map((parameter) => parameter.trim().split(/\s+/).join(" ").toUpperCase()).filter(Boolean);
  if (found.join(", ") === C4_PARAMETERS.join(", ")) return undefined;
  return `a procedure's parameters must be exactly (${C4_PARAMETERS.join(", ")}) -- contract C4; found (${found.join(", ")})`;
}

// ---------- shell ----------

/** Anything that can chain, redirect or substitute another command. */
export const SHELL_METACHARACTERS = /[;|&<>`\n\r]|\$\(|\$\{/;
export const INTERPRETER_FLAGS = ["-c", "-e", "-command", "-encodedcommand", "-file"];
export const PYTHON_EXES = ["python", "python3", "py", ".venv/scripts/python.exe", ".venv/bin/python"];
/**
 * Listing and inspection commands every role may run. They are NOT side-effect-free: several of
 * them will write a file if asked (`git diff --output=…` is the one review round 2 caught), so
 * each command carries an explicit flag allow-list and every other flag is denied.
 */
export interface ListingCommand {
  /** The command words, lower-cased, that start the invocation. */
  words: string[];
  /** Flags this command may be given, lower-cased; `--name=value` is judged by its name. */
  flags: string[];
  /** Flags whose value must be digits, given inline or as the next token. */
  numericFlags?: string[];
  /** Whether a bare `-<digits>` is a valid flag (git log -5). */
  bareNumeric?: boolean;
}

export const READ_ONLY_SHELL: ListingCommand[] = [
  { words: ["ls"], flags: ["-l", "-a", "-la", "-al", "-lh", "-r", "-1"] },
  { words: ["dir"], flags: ["/b", "/s", "/a", "-b", "-s", "-a"] },
  { words: ["cat"], flags: [] },
  { words: ["type"], flags: [] },
  {
    words: ["get-content"],
    // No -Stream (alternate data streams) and no -Wait (follows a file forever).
    flags: ["-path", "-literalpath", "-totalcount", "-tail", "-raw", "-encoding"],
    numericFlags: ["-totalcount", "-tail"],
  },
  {
    words: ["get-childitem"],
    // -Include/-Exclude are left out: nothing in the pipeline needs them, and default-deny is
    // the rule. -Filter is a plain string and passes the argument charset check.
    flags: ["-path", "-literalpath", "-recurse", "-name", "-file", "-directory", "-filter", "-depth"],
    numericFlags: ["-depth"],
  },
  { words: ["git", "status"], flags: ["-s", "--short", "--porcelain", "-b", "--branch"] },
  { words: ["git", "diff"], flags: ["--stat", "--name-only", "--name-status", "--cached", "--staged", "--no-color"] },
  {
    words: ["git", "log"],
    flags: ["--oneline", "--stat", "--no-color", "-n", "--max-count"],
    numericFlags: ["-n", "--max-count"],
    bareNumeric: true,
  },
];

/** The only git subcommands that are listings; anything else is denied outright. */
export const GIT_SUBCOMMANDS = ["status", "diff", "log"];
/** Commands nobody may run, whatever their role. */
export const DESTRUCTIVE_SHELL = [
  /\brm\s+-[a-z]*[rf]/i,
  /\bdel\s+\/s\b/i,
  /(^|[\s;|&(])format\b/i,
  /\bgit\s+push\b/i,
  /\bgit\s+reset\s+--hard\b/i,
  /\bcurl\b/i,
  /\bwget\b/i,
  /\binvoke-webrequest\b/i,
];
/** The scripts each role may run, matched literally against the command's second token. */
export const ROLE_SCRIPTS: Record<Role, string[]> = {
  intake: ["scripts/intake_touchpoints.py", "scripts/intake_prompt.py"],
  // target_check.py proposes each segment's output target; the analyzer copies it into the
  // contracts (output-targets design §3.2), so it must be able to re-run the proposal it reads.
  analyzer: ["scripts/segment.py", "scripts/target_check.py"],
  // render_snowpark.py turns a Snowpark segment's proc.py into its proc.sql wrapper — both files
  // are already in the translator/fixer write lane, so running it writes nothing new.
  translator: ["scripts/compile_check.py", "scripts/render_snowpark.py"],
  fixer: ["scripts/compile_check.py", "scripts/render_snowpark.py"],
  reviewer: [],
  // validate_dbt.py validates a dbt workflow's whole project and writes every segment's reports —
  // the validator's own lane in dbt scope. (compile_check.py --target dbt is already the
  // translator's and fixer's: the same script, one more flag value.)
  validator: ["scripts/validate_segment.py", "scripts/validate_snowpark.py", "scripts/validate_dbt.py", "scripts/compare.py"],
  "parser-recovery": ["scripts/parse.py"],
  documenter: [],
};

/**
 * dbt is run only by scripts/compile_check.py and scripts/validate_dbt.py (through
 * `scripts/lib/dbt_project.run_dbt`), never by an agent (design §6): the console script however it
 * is spelled — `dbt`, `dbt.exe`, `dbt.cmd`, `dbt.bat`, a path ending in any of them — matched on the
 * normalized first token.
 * `python -m dbt…` is refused by the same rule in `decideShell`'s `-m` branch.
 */
export const DBT_EXECUTABLE = /^(?:.*\/)?dbt(?:\.exe|\.cmd|\.bat)?$/;

/**
 * The allow-listed scripts whose first positional argument is the workflow id (each one's argparse
 * declares `wf_id` first; `scripts/compare.py` takes only `--flag value` pairs and is not here).
 * The first bare token after such a script must be the session's own workflow (task-D fix round 1,
 * G2) — the script writes under `workflows/<that id>/`, which no path check would otherwise see.
 */
export const WORKFLOW_ID_SCRIPTS = [
  "scripts/intake_touchpoints.py",
  "scripts/intake_prompt.py",
  "scripts/segment.py",
  "scripts/target_check.py",
  "scripts/compile_check.py",
  "scripts/render_snowpark.py",
  "scripts/validate_segment.py",
  "scripts/validate_snowpark.py",
  "scripts/validate_dbt.py",
  "scripts/parse.py",
];
const DBT_MODULE = /^dbt(\.|$)/;
const DBT_DENIAL = "dbt is run only by scripts/compile_check.py and scripts/validate_dbt.py, never by an agent";

/**
 * `--root` in every spelling argparse accepts: `--root X`, `--root=X`, and the prefix abbreviations
 * `--r`/`--ro`/`--roo` (no allow-listed script sets `allow_abbrev=False` or has another `--r…` flag,
 * so argparse resolves each of them to `--root`). Case-insensitive, as every token here is judged.
 */
export const SCRIPT_ROOT_FLAG = /^--r(?:o(?:ot?)?)?(?:=.*)?$/i;

/**
 * The flags that point a script at a real Snowflake account (Task P2: `--backend snowflake`,
 * `--connection NAME`, `--sandbox-database DB`). No agent session may pass one, whichever script it
 * runs and whether or not that script has the flag yet. Like `--root`, every spelling argparse
 * accepts is caught: `--flag x`, `--flag=x`, any case, and every prefix abbreviation down to one
 * letter (argparse resolves an unambiguous prefix; an ambiguous one is refused here all the same).
 */
export const SCRIPT_BACKEND_FLAGS = ["backend", "connection", "sandbox-database"];

export function isScriptBackendFlag(token: string): boolean {
  const name = /^--([A-Za-z0-9][A-Za-z0-9_-]*)(?:=.*)?$/.exec(token)?.[1]?.toLowerCase();
  return name !== undefined && SCRIPT_BACKEND_FLAGS.some((flag) => flag.startsWith(name));
}

/** Only parser-recovery runs the corpus regression suite. */
export const PYTEST_ROLES: Role[] = ["parser-recovery"];
export const PYTEST_TARGET = /^tests\/parser_corpus(\/[a-z0-9_][a-z0-9_.-]*)*$/;
export const PYTEST_FLAGS = ["-q"];
const FLAG_TOKEN = /^--?[A-Za-z][A-Za-z0-9-]*$/;
const PLAIN_TOKEN = /^[A-Za-z0-9_][A-Za-z0-9_./-]*$/;

/** A listing argument may be a Windows-style path; normalizeToolPath resolves the separators. */
const LISTING_ARG_TOKEN = /^[A-Za-z0-9_][A-Za-z0-9_.\\/-]*$/;

const normalizeToken = (token: string): string => token.replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase();
const hasDotDot = (token: string): boolean => /(^|[\\/])\.\.([\\/]|$)/.test(token);

// ---------- the decision ----------

const ALLOW: Decision = { permissionDecision: "allow" };
const denied = (why: string): Decision => ({ permissionDecision: "deny", permissionDecisionReason: why });

const OTHER_WORKFLOW = /workflows\/([a-z0-9_.-]+)\//g;

/** Everything the policy needs that is not the call itself. */
export interface PolicyOptions {
  /** Databases a three-part object reference may live in (`orchestrator.config.json`). */
  sandboxDatabases?: string[];
  /** The role acts on the workflow's dbt project (output_kind dbt), not on one segment. */
  dbtProject?: boolean;
  /** One call of a batched analyzer: its lanes narrow to this batch (see `writeLanes`). */
  analyzerBatch?: AnalyzerBatch;
}

/**
 * The one permission decision. Paths are normalized and compared case-insensitively, the first
 * matching rule wins, and anything that cannot be judged is denied.
 *
 * @param root repository root, so an absolute path argument can be proved to be inside it.
 * @param options policy configuration; see {@link PolicyOptions}.
 */
export function decide(
  role: Role,
  wfId: string,
  toolName: string,
  toolArgs: unknown,
  segment?: string,
  root?: string,
  options?: PolicyOptions,
): Decision {
  const id = wfId.toLowerCase();
  const databases = options?.sandboxDatabases ?? SANDBOX_DATABASES;

  // 1a. Any mention of another workflow's folder, in any argument, whatever the tool.
  const rawArgs = JSON.stringify(toolArgs ?? {}).replace(/\\{1,2}/g, "/").toLowerCase();
  for (const match of rawArgs.matchAll(OTHER_WORKFLOW)) {
    if (match[1] !== id) return denied(`no access to other workflows (${match[1]})`);
  }

  // 1b. Every path-like argument must normalize inside the repo and stay in this workflow.
  const scan = collectPathArgs(toolArgs);
  if (scan.tooDeep) return denied("arguments too deeply nested to judge");
  const paths: string[] = [];
  for (const raw of scan.paths) {
    const checked = normalizeToolPath(raw, root);
    if (!checked.ok) return denied(checked.reason);
    const owner = workflowOf(checked.path);
    if (owner !== undefined && owner !== id) return denied(`no access to other workflows (${owner})`);
    paths.push(checked.path);
  }

  if (SQL_TOOL.test(toolName)) return decideSql(role, toolArgs, databases);
  if (SHELL_TOOL.test(toolName)) return decideShell(role, id, toolArgs, root);
  if (WRITE_TOOL.test(toolName)) {
    return decideWrite(role, id, segment, paths, options?.dbtProject ?? false, options?.analyzerBatch);
  }
  if (READ_TOOLS.includes(toolName.toLowerCase())) return ALLOW;
  return denied(`${UNRECOGNIZED_TOOL}: ${toolName}`);
}

function decideWrite(
  role: Role,
  id: string,
  segment: string | undefined,
  paths: string[],
  dbtProject: boolean,
  analyzerBatch?: AnalyzerBatch,
): Decision {
  if (paths.length === 0) return denied(`${role} wrote no path this policy can judge`);
  const lanes = writeLanes(role, id, segment, dbtProject, analyzerBatch);
  if (lanes === null) {
    return denied(`${role} may only write its own segment's files, and no segment is in context`);
  }
  for (const target of paths) {
    for (const [pattern, why] of FORBIDDEN_WRITES) if (pattern.test(target)) return denied(why);
    if (!lanes.some((lane) => lane.test(target))) return denied(`${role} may not write ${target}`);
  }
  return ALLOW;
}

function decideSql(role: Role, args: unknown, databases: string[]): Decision {
  if (role !== "validator" && role !== "intake") return denied(`${role} may not execute SQL`);
  const statement = firstString(args, /^(sql|query|statement|text)$/i);
  if (statement === undefined) return denied(`${role} sent no SQL this policy can judge`);

  if (role === "intake") {
    // B6: one catalog read, nothing else. The live catalog is INFORMATION_SCHEMA in a sandbox
    // database, or the sanitized copy the pipeline keeps in MIG_WORK.CATALOG_COLUMNS.
    const rules: SqlRules = {
      allowed: [CATALOG_SCHEMA],
      label: CATALOG_SCHEMA,
      databases,
      extraObjects: INTAKE_CATALOG_OBJECTS,
    };
    const stripped = stripSqlNoise(statement).trim();
    const reason = CATALOG_READ_SQL.test(stripped)
      ? checkStatement(statement, rules)
      : "it is not a SELECT, SHOW or DESCRIBE";
    if (reason) return denied(`intake may only read the catalog (${CATALOG_SCHEMA}): ${reason}`);
    return ALLOW;
  }

  const rules: SqlRules = {
    allowed: VALIDATOR_SCHEMAS,
    label: `sandbox schemas ${VALIDATOR_SCHEMAS.join(", ")}`,
    databases,
  };
  const procedure = checkProcedure(statement, rules);
  if (procedure !== null) return procedure === undefined ? ALLOW : denied(procedure);

  const reason = checkStatement(statement, rules);
  if (reason) return denied(reason);
  const call = checkCallSignature(statement, databases);
  if (call) return denied(call);
  return ALLOW;
}

/**
 * A listing invocation: every flag must be on that command's allow-list, every numeric flag must
 * be given digits, and every other argument is a revision or a path in this workflow.
 */
function checkListing(listing: ListingCommand, args: string[], id: string, root?: string): Decision {
  const name = listing.words.join(" ");
  let afterDoubleDash = false;

  for (let i = 0; i < args.length; i++) {
    const token = args[i];
    if (token === "--") {
      afterDoubleDash = true;
      continue;
    }

    const isFlag = !afterDoubleDash && (token.startsWith("-") || (listing.words[0] === "dir" && token.startsWith("/")));
    if (isFlag) {
      if (listing.bareNumeric && /^-\d+$/.test(token)) continue;
      const equals = token.indexOf("=");
      const flag = (equals > 0 ? token.slice(0, equals) : token).toLowerCase();
      const inline = equals > 0 ? token.slice(equals + 1) : undefined;
      if (!listing.flags.includes(flag)) return denied(`flag not allowed for ${name}: ${flag}`);
      if (listing.numericFlags?.includes(flag)) {
        const value = inline ?? args[++i];
        if (value === undefined || !/^\d+$/.test(value)) return denied(`${flag} needs a number (${value ?? "nothing"})`);
        continue;
      }
      if (inline === undefined) continue;
      const bad = checkListingArgument(inline, id, root);
      if (bad) return bad;
      continue;
    }

    const bad = checkListingArgument(token, id, root, afterDoubleDash);
    if (bad) return bad;
  }
  return ALLOW;
}

/** A revision or path argument to a listing command. */
function checkListingArgument(token: string, id: string, root?: string, isPath = false): Decision | undefined {
  if (!LISTING_ARG_TOKEN.test(token) || hasDotDot(token)) {
    return denied(`listing argument not allowed: ${token.slice(0, 60)}`);
  }
  if (!isPath && !/[/\\]/.test(token)) return undefined;
  const checked = normalizeToolPath(token, root);
  if (!checked.ok) return denied(checked.reason);
  const owner = workflowOf(checked.path);
  if (owner !== undefined && owner !== id) return denied(`no access to other workflows (${owner})`);
  return undefined;
}

function decideShell(role: Role, id: string, args: unknown, root?: string): Decision {
  const command = firstString(args, /^(command|cmd|script|input)$/i)?.trim();
  if (!command) return denied(`${role} sent no command this policy can judge`);
  if (SHELL_METACHARACTERS.test(command)) return denied(`shell metacharacter in command: ${command.slice(0, 80)}`);
  if (DESTRUCTIVE_SHELL.some((pattern) => pattern.test(command))) {
    return denied(`destructive command: ${command.slice(0, 80)}`);
  }

  const tokens = command.split(/\s+/).filter(Boolean);
  const flagged = tokens.find((token) => INTERPRETER_FLAGS.includes(token.toLowerCase()));
  if (flagged) return denied(`interpreter flag ${flagged} is never allowed`);

  const refuse = denied(`${role} may not run this command: ${command.slice(0, 80)}`);
  const head = tokens.map(normalizeToken);

  // Listing commands, for every role — but only with their own flags (see ListingCommand).
  const listing = READ_ONLY_SHELL.find((shape) => shape.words.every((word, i) => head[i] === word));
  if (listing) return checkListing(listing, tokens.slice(listing.words.length), id, root);
  if (head[0] === "git") {
    return denied(`git ${head[1] ?? "(no subcommand)"} is not a listing command: ${GIT_SUBCOMMANDS.join(", ")} only`);
  }
  if (DBT_EXECUTABLE.test(head[0])) return denied(DBT_DENIAL);

  if (!PYTHON_EXES.includes(head[0])) return refuse;

  // `python -m pytest tests/parser_corpus[/…] [-q]`, parser-recovery only.
  if (head[1] === "-m") {
    if (DBT_MODULE.test(head[2] ?? "")) return denied(DBT_DENIAL);
    if (!PYTEST_ROLES.includes(role) || head[2] !== "pytest") return refuse;
    if (!PYTEST_TARGET.test(head[3] ?? "")) return refuse;
    for (const token of head.slice(4)) if (!PYTEST_FLAGS.includes(token)) return refuse;
    return ALLOW;
  }

  // `<python> scripts/<allowed script>.py <args…>`
  const script = head[1] ?? "";
  if (!(ROLE_SCRIPTS[role] ?? []).includes(script)) return refuse;
  // Task D fix round 2: an agent never needs --root (its session's working directory IS the
  // workflow root), and a script given one reads and writes under another tree. Judged before any
  // other argument rule, so every spelling gets this reason.
  if (tokens.slice(2).some((token) => SCRIPT_ROOT_FLAG.test(token))) {
    return denied(`script-root: ${script} may not be given --root from an agent session`);
  }
  // Follow-up to Task W2: an agent never points a script at a Snowflake account (Task P2's flags).
  if (tokens.slice(2).some(isScriptBackendFlag)) {
    return denied(`script-backend: ${script} may not be pointed at a Snowflake account from an agent session`);
  }
  for (const token of tokens.slice(2)) {
    if (FLAG_TOKEN.test(token)) continue;
    if (!PLAIN_TOKEN.test(token) || hasDotDot(token)) {
      return denied(`${role} may not pass this argument: ${token.slice(0, 60)}`);
    }
  }
  // G2: the first bare token is the workflow the script will act on. A flag's value placed before
  // it is not skipped (the documented form is `<script> <wf_id> …`), which can only deny more.
  if (WORKFLOW_ID_SCRIPTS.includes(script)) {
    const first = tokens.slice(2).find((token) => !FLAG_TOKEN.test(token));
    if (first !== undefined && first.toLowerCase() !== id) {
      return denied(`cross-workflow: ${script} ${first.slice(0, 60)} in a ${id} session`);
    }
  }
  return ALLOW;
}
