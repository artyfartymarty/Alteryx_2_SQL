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
// Task L1 (live hardening) added two things and loosened no rule. Every deny now carries a class
// (`read` / `act`, `denialClass`) that says what the refused call attempted; it never changes a
// decision. And the one path outside the repository an agent may read is a spill file the SDK
// itself named in THIS session's own tool results (`readableSpillFiles`), with view or grep only.
// Task L6 split the attempted actions: `severe` (outside the workflow or the sandbox, or damage --
// `severeCategory`) parks a session at once; every other `act` is budgeted. It loosened no rule either.
//
// The tool-name regexes and READ_TOOLS are PROVISIONAL: tool spellings differ between CLI builds.
// The first live run logs every `toolName` (hooks.ts audits an `unrecognized-tool` event for each
// default-deny) and these constants get tightened from that log. Change them here, nowhere else.
import path from "node:path";
import type { AnalyzerBatch, Role } from "./types.ts";

/**
 * What a denied call attempted (Task L1, R3; Task L6, R2). `severe`: it tried to reach outside the
 * session's workflow or the sandbox, or to do damage (`severeCategory` names how) -- the session parks
 * at once. `read`: the call could only have read -- a read-only tool, or a shell command
 * `isReadOnlyShellCommand` accepts. `act`: every other attempted action, and anything that cannot be
 * judged. The class never changes a decision: a denied call is refused whatever its class. It only
 * tells `CopilotRunner.run` whether the session may go on (reads within
 * `budgets.maxReadDenialsPerSession`, attempted actions within `budgets.maxActDenialsPerSession`) or
 * must park.
 */
export type DenialClass = "read" | "act" | "severe";

export type Decision =
  | { permissionDecision: "allow" }
  | { permissionDecision: "deny"; permissionDecisionReason: string; denialClass: DenialClass };

/** A rule's own verdict, before `decide` attaches the denial's class. */
type Verdict =
  | { permissionDecision: "allow" }
  | { permissionDecision: "deny"; permissionDecisionReason: string };

// ---------- tool classification ----------

export const SQL_TOOL = /snowflake|sql|query/i;
export const SHELL_TOOL = /^(bash|shell|powershell|pwsh|cmd|local_shell)/i;
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

/**
 * The SDK's shell-session tools (live evidence, 2026-09-23: an intake session called
 * `list_powershell` right after a `powershell` call with `initial_wait`; the runtime's own help
 * text: "If still running after initial_wait, continue with other work … Use read_<shell> with
 * shellId to retrieve the full output"). A command this policy allowed may keep running in the
 * background, and these two tools only read what it printed (`read_*`, by `shellId`) or list the
 * session's own shells (`list_*`) -- so every role may use them.
 */
export const SHELL_SESSION_READ_TOOLS = ["read_powershell", "list_powershell", "read_bash", "list_bash"];

/**
 * The SDK's tools that stop a shell (L6 fix round 1, live evidence: a translator whose SQL passed all four
 * golden sets made about twenty `stop_powershell {"shellId":"sh-sel"}` calls, stopping a shell it had
 * started itself). A shell session only exists because this policy allowed the command that started it,
 * and these tools address it by the session's own `shellId`, so stopping one ends nothing the session did
 * not start: every role may use them. (`write_powershell`/`write_bash`, which type into a shell, stay
 * refused by the write rule.)
 */
export const SHELL_SESSION_STOP_TOOLS = ["stop_powershell", "stop_bash"];

/**
 * The SDK's sub-agent tools (live evidence, 2026-09-23). `task` (allowed, READ_TOOLS) may start a
 * background sub-agent whose result the runtime tells the model to fetch with `read_agent`
 * ("Use read_agent with agent_id … to read the results"); `list_agents` lists them. Both only read,
 * and the sub-agent's own tool calls pass through this same policy. `write_agent` (a follow-up
 * message to a sub-agent) is denied with its own reason: nothing in the pipeline needs it.
 */
export const AGENT_READ_TOOLS = ["read_agent", "list_agents"];
export const AGENT_MESSAGE_TOOL = "write_agent";

export const UNRECOGNIZED_TOOL = "unrecognized tool";

/**
 * The deny reasons that say a refused call reached outside the session's workflow or the sandbox, or
 * tried to do damage (Task L6, R2). Every rule that gives one of these reasons builds it from the
 * constant, and `severeCategory` reads them back, so the two cannot drift apart.
 */
export const OTHER_WORKFLOWS_REASON = "no access to other workflows";
export const CROSS_WORKFLOW_REASON = "cross-workflow:";
export const DESTRUCTIVE_REASON = "destructive command:";
export const SCRIPT_ROOT_REASON = "script-root:";
export const SCRIPT_BACKEND_REASON = "script-backend:";
export const EXTERNAL_LOCATION_REASON = "external-location:";

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
// Live hardening, Task L9 fix round 2 (L9-m4): exported so hooks.ts's `outputFolders` can hold a
// segment/batch id to the same shape `writeLanes`/`analyzerBatchLanes` already do, rather than
// building a directory path from one the policy itself would never call a plain id.
export const ID_PATTERN = /^[a-z0-9][a-z0-9_-]*$/;

export type PathCheck = { ok: true; path: string } | { ok: false; reason: string };

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The one path normalizer every rule runs first: separators unified, `.`/`..` resolved,
 * absolutes accepted only inside the repository root, result lower-cased and root-relative.
 */
/**
 * Whitespace of any kind at either end of a string (Task L1 fix round 2, S3): JavaScript's `\s`
 * (ASCII blanks, tabs and line breaks, the Unicode spaces, no-break space, the line and paragraph
 * separators and the BOM) plus the zero-width characters `\s` leaves out (U+180E, U+200B-U+200D,
 * U+2060).
 */
export const EDGE_WHITESPACE = /^[\s\u180e\u200b-\u200d\u2060]|[\s\u180e\u200b-\u200d\u2060]$/u;

/** The reason prefix of a path Windows would read under another name (L6 fix round 1, X2a). */
export const WINDOWS_ALIAS_REASON = "windows-alias:";
/** A DOS device name, alone or with an extension (`CON`, `nul.txt`, `COM1`, `LPT\u00b2`, `CONIN$`). */
const WINDOWS_DEVICE = /^(?:con|prn|aux|nul|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3]|conin\$|conout\$)(?:\s*\..*)?$/i;

/**
 * Why a path would be read by Windows under another name than the one this policy judges, or
 * `undefined` (L6 fix round 1, X2a; the combined re-review probed each form). Windows strips a
 * segment's trailing dots and spaces (`workflows.` is `workflows`), resolves an 8.3 short name
 * (`WORKFL~1`), reads a `:` after the drive as an NTFS stream (`plan.md:x`, `::$DATA`), and opens a
 * device for a reserved name (`CON`, `NUL`, `COM1`, …, with or without an extension). None of them
 * matches `workflows/<id>/…` literally, so rule 1 and the broad-read checks would not see the
 * workflow they reach. `.` and `..` are ordinary segments (resolved elsewhere).
 */
export function windowsAliasReason(raw: string): string | undefined {
  const text = String(raw ?? "").replace(/\\/g, "/");
  const body = /^[A-Za-z]:/.test(text) ? text.slice(2) : text;
  if (body.includes(":")) return aliasSaid(STREAM_KIND, raw);
  for (const segment of body.split("/")) {
    const kind = aliasSegmentKind(segment);
    if (kind) return aliasSaid(kind, raw);
  }
  return undefined;
}

/** L6 fix round 2 (minor 11): what a `:` past the drive letter is -- the live shape was a JSON array passed as a
 * string, whose drive letter was not at the path's start. */
const STREAM_KIND = "a `:` inside the path (an NTFS stream suffix, or a drive letter that is not at the path's start)";

function aliasSaid(what: string, raw: string): string {
  return `${WINDOWS_ALIAS_REASON} ${what} -- Windows would read another name than the one judged: ${JSON.stringify(raw)}`;
}

/** What makes one path segment a Windows alias, or `undefined`. */
function aliasSegmentKind(segment: string): string | undefined {
  if (segment === "" || segment === "." || segment === "..") return undefined;
  if (/[. ]$/.test(segment)) return `a path segment ending in a dot or a space (${JSON.stringify(segment)})`;
  if (/~\d/.test(segment)) return `an 8.3 short name (${segment})`;
  if (WINDOWS_DEVICE.test(segment)) return `a device name (${segment})`;
  return undefined;
}

/**
 * The same for a glob pattern (L6 fix round 1, X2a): every LITERAL part -- a segment, or a brace
 * alternative -- is judged like a path segment; a part with a wildcard or a class matches real directory
 * entries by name and is left to the other glob rules. A `:` after the drive is a stream anywhere.
 */
export function globAliasReason(pattern: string): string | undefined {
  const text = String(pattern ?? "").replace(/\\/g, "/");
  const body = /^[A-Za-z]:/.test(text) ? text.slice(2) : text;
  if (body.includes(":")) return aliasSaid(STREAM_KIND, pattern);
  for (const segment of body.split("/")) {
    for (const piece of segment.split(/[{},]/)) {
      if (/[*?[\]!]/.test(piece)) continue;
      const kind = aliasSegmentKind(piece);
      if (kind) return aliasSaid(`${kind} in a glob pattern`, pattern);
    }
  }
  return undefined;
}

export function normalizeToolPath(raw: string, root?: string): PathCheck {
  const text = String(raw ?? "");
  if (!text) return { ok: false, reason: "empty path argument" };
  // Task L1 fix round 2 (S3): refused, never trimmed. The tool opens the name WITH the whitespace
  // (NTFS keeps a trailing no-break space), which is not the path this policy would have judged.
  if (EDGE_WHITESPACE.test(text)) return { ok: false, reason: `path has leading or trailing whitespace: ${JSON.stringify(text)}` };
  // L6 fix round 2 (P2): a leading `~` is the home directory to a shell, and possibly to the tool.
  if (startsAtHome(text)) return { ok: false, reason: `${HOME_REASON}: ${text}` };
  // L6 fix round 1 (X2a): refused, never canonicalized -- an 8.3 name cannot be resolved without the disk.
  const alias = windowsAliasReason(text);
  if (alias) return { ok: false, reason: alias };
  return resolveInRepository(text, root);
}

/** The path reason of a leading `~` (L6 fix round 2, P2); `severeCategory` reads it. */
export const HOME_REASON = "path outside the repository (a leading ~ is the home directory)";

/** Whether a path's first segment starts with `~` -- the home directory (`~`, `~/x`, `~user\x`). */
export function startsAtHome(raw: string): boolean {
  return /^['"]?~/.test(String(raw ?? ""));
}

/**
 * `normalizeToolPath` after its refusals (whitespace, `~`, Windows aliases): separators unified, `.`/`..`
 * resolved, an absolute path accepted only inside the root, lower-cased and root-relative. The severe class
 * uses it alone to PLACE a path it has canonicalized itself (L6 fix round 2); the decision never does.
 */
function resolveInRepository(text: string, root?: string): PathCheck {
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
  const reach = (what: string) => `${EXTERNAL_LOCATION_REASON} ${what} may not move data outside the sandbox`;
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
  /** Task L1 fix round 2 (S2): flags that make the listing walk the whole tree below its path. */
  recursiveFlags?: string[];
  /** The listing always walks the tree (git status, git diff). */
  alwaysRecursive?: boolean;
  /** Non-numeric flags whose next token is a value, never a path (Get-ChildItem -Filter). */
  valueFlags?: string[];
  /** Paths come only after `--`; a bare token before it is a revision (git). */
  pathsAfterDoubleDash?: boolean;
}

export const READ_ONLY_SHELL: ListingCommand[] = [
  // `-r` is compared lower-cased, so it is also `ls -R`: counted as recursive either way.
  { words: ["ls"], flags: ["-l", "-a", "-la", "-al", "-lh", "-r", "-1"], recursiveFlags: ["-r"] },
  // `/s` in cmd, `-s` in PowerShell (an alias of -Recurse).
  { words: ["dir"], flags: ["/b", "/s", "/a", "-b", "-s", "-a"], recursiveFlags: ["/s", "-s"] },
  { words: ["cat"], flags: [] },
  { words: ["type"], flags: [] },
  {
    words: ["get-content"],
    // No -Stream (alternate data streams) and no -Wait (follows a file forever).
    flags: ["-path", "-literalpath", "-totalcount", "-tail", "-raw", "-encoding"],
    numericFlags: ["-totalcount", "-tail"],
    valueFlags: ["-encoding"],
  },
  {
    words: ["get-childitem"],
    // -Include/-Exclude are left out: nothing in the pipeline needs them, and default-deny is
    // the rule. -Filter is a plain string and passes the argument charset check.
    flags: ["-path", "-literalpath", "-recurse", "-name", "-file", "-directory", "-filter", "-depth"],
    numericFlags: ["-depth"],
    recursiveFlags: ["-recurse", "-depth"],
    valueFlags: ["-filter"],
  },
  // git status and git diff walk the whole working tree; git log does with --stat.
  { words: ["git", "status"], flags: ["-s", "--short", "--porcelain", "-b", "--branch"], alwaysRecursive: true, pathsAfterDoubleDash: true },
  {
    words: ["git", "diff"],
    flags: ["--stat", "--name-only", "--name-status", "--cached", "--staged", "--no-color"],
    alwaysRecursive: true,
    pathsAfterDoubleDash: true,
  },
  {
    words: ["git", "log"],
    flags: ["--oneline", "--stat", "--no-color", "-n", "--max-count"],
    numericFlags: ["-n", "--max-count"],
    bareNumeric: true,
    recursiveFlags: ["--stat"],
    pathsAfterDoubleDash: true,
  },
];

/** The only git subcommands that are listings; anything else is denied outright. */
export const GIT_SUBCOMMANDS = ["status", "diff", "log"];
/** Commands nobody may run, whatever their role. A match is a severe attempt (Task L6, R2) -- except
 * that the `format` pattern matches the word anywhere, and the severe class counts only the disk command
 * (`NOT_DISK_FORMAT`, `severeShellText`; L6 fix round 2, minor 12). */
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
  // target_check.py proposes each segment's output target; the orchestrator pre-fills it into the
  // contracts (Task L3; output-targets design §3.2) and the analyzer may only lower it, so it must be
  // able to re-run the proposal it reads.
  // Task L3 (R3): the analyzer checks its own contracts (contract_check.py) and seams (check_seams.py)
  // before it finishes, instead of improvising a check the policy has to refuse.
  analyzer: ["scripts/segment.py", "scripts/target_check.py", "scripts/contract_check.py", "scripts/check_seams.py"],
  // render_snowpark.py turns a Snowpark segment's proc.py into its proc.sql wrapper — both files
  // are already in the translator/fixer write lane, so running it writes nothing new.
  // Task L4 (R2): the translator and the fixer also run the validator on their own work, narrowed by
  // SELF_VALIDATION_SCRIPTS below (own segment, or the whole project in dbt scope; --set only).
  translator: ["scripts/compile_check.py", "scripts/render_snowpark.py", "scripts/validate_segment.py", "scripts/validate_snowpark.py", "scripts/validate_dbt.py"],
  fixer: ["scripts/compile_check.py", "scripts/render_snowpark.py", "scripts/validate_segment.py", "scripts/validate_snowpark.py", "scripts/validate_dbt.py"],
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
  "scripts/contract_check.py",
  "scripts/check_seams.py",
  "scripts/compile_check.py",
  "scripts/render_snowpark.py",
  "scripts/validate_segment.py",
  "scripts/validate_snowpark.py",
  "scripts/validate_dbt.py",
  "scripts/parse.py",
];
/**
 * Task L4 (R2): the validator scripts the translator and the fixer may run on their own work, and
 * what each takes from them: `segment` -- exactly `<wf> <the session's own segment>` in a segment
 * session -- or `project` -- exactly `<wf>` in a dbt-scope session. The only flag is `--set <name>`
 * (repeatable): `--proc`/`--project` would validate a file or a project other than the session's own.
 * Everything else about the call is judged as for any script (own workflow, no `--root`, no backend
 * flag). What the script writes (the segment's `validation*.json`, or every segment's and the chain
 * report for a dbt project) is not authoritative: the orchestrator clears it before its validator
 * runs (`stages.ts`).
 */
export const SELF_VALIDATION_ROLES: Role[] = ["translator", "fixer"];
export const SELF_VALIDATION_SCRIPTS: Record<string, "segment" | "project"> = {
  "scripts/validate_segment.py": "segment",
  "scripts/validate_snowpark.py": "segment",
  "scripts/validate_dbt.py": "project",
};

/** `--set=<name>` (L4 fix round 1, M2): the value, for the caller to judge. */
const SET_EQUALS = /^--set=(.*)$/;

function selfValidationVerdict(
  role: Role,
  script: string,
  args: string[],
  segment: string | undefined,
  dbtProject: boolean,
  shellArgs?: unknown,
): Verdict | undefined {
  const scope = SELF_VALIDATION_SCRIPTS[script];
  if (!SELF_VALIDATION_ROLES.includes(role) || scope === undefined) return undefined;
  // L4 fix round 1 (M5): only the shell tool's synchronous form. An `async` (or detached) run could
  // outlive the session and write its reports after the orchestrator cleared them for the validator.
  // A sync command still running when `initial_wait` expires moves to the background -- the runtime
  // does that, and no argument shows it; the orchestrator's clear-then-validate ordering is what
  // covers that case.
  const record = shellArgs && typeof shellArgs === "object" ? (shellArgs as Record<string, unknown>) : {};
  const mode = record.mode;
  if ((mode !== undefined && (typeof mode !== "string" || mode.toLowerCase() !== "sync")) || (record.detach ?? false) !== false) {
    return denied(`self-validation: ${role} may run ${script} only in the shell tool's synchronous mode (mode "sync", not detached)`);
  }
  const shape = scope === "segment" ? "<wf> <seg>" : "<wf>";
  const positional: string[] = [];
  for (let i = 0; i < args.length; i++) {
    const token = args[i];
    const setValue = SET_EQUALS.exec(token)?.[1];
    if (setValue !== undefined) {
      if (!setValue || setValue.startsWith("-")) {
        return denied(`self-validation: ${role} may pass ${script} only ${shape} and --set <name>; --set needs a golden set name`);
      }
      continue;
    }
    if (token === "--set") {
      const value = args[i + 1];
      if (value === undefined || value.startsWith("-")) {
        return denied(`self-validation: ${role} may pass ${script} only ${shape} and --set <name>; --set needs a golden set name`);
      }
      i += 1;
      continue;
    }
    if (token.startsWith("-")) {
      return denied(`self-validation: ${role} may pass ${script} only ${shape} and --set <name>, not ${token.slice(0, 40)}`);
    }
    positional.push(token);
  }
  if (scope === "project") {
    if (!dbtProject) {
      return denied(
        `self-validation: ${script} validates the whole dbt project, and this ${role} session is segment ` +
          `${segment ?? "(none)"}'s: run scripts/validate_segment.py or scripts/validate_snowpark.py <wf> <seg>`,
      );
    }
    if (positional.length !== 1) {
      return denied(`self-validation: ${role} may pass ${script} only the workflow id (and --set <name>), not ${positional.slice(1).join(" ").slice(0, 60)}`);
    }
    return undefined;
  }
  if (dbtProject) {
    return denied(`self-validation: ${script} validates one segment, and this ${role} session is the dbt project's: run scripts/validate_dbt.py <wf>`);
  }
  if (segment === undefined) {
    return denied(`self-validation: ${script} validates one segment, and this ${role} session has no segment in context`);
  }
  const given = positional.slice(1).join(" ");
  if (positional.length !== 2 || positional[1].toLowerCase() !== segment.toLowerCase()) {
    return denied(`own-segment: ${role} may validate only its own segment ${segment}, not ${given.slice(0, 60) || "(no segment)"} (${script})`);
  }
  return undefined;
}

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

// ---------- denial classes (Task L1, R3) ----------

/** Every argument name that could carry a shell command, in any case. */
export const SHELL_COMMAND_KEYS = /^(command|cmd|script|input)$/i;
/** The one key the SDK's shell tool runs (`powershell`/`bash`: `command`, plus `description`,
 * `initial_wait`, `mode`, `shellId` -- never `input`). */
export const SHELL_COMMAND_KEY = "command";

export type ShellCommand = { ok: true; command: string } | { ok: false; reason: string };

/**
 * The command a shell call runs (Task L1 fix round 1, I1/P1), for `decideShell` and `denialClass`
 * alike: the string under exactly `command`, and nothing else command-like beside it. The model
 * controls key order and extra keys, so a call carrying another `SHELL_COMMAND_KEYS` key (`Command`,
 * `cmd`, `script`, `input`, whatever its value), or no string `command` at all, is AMBIGUOUS: judging
 * one key while the tool runs another would let an unjudged command through, so it is denied, as an
 * attempted action.
 */
export function shellCommandOf(args: unknown): ShellCommand {
  const entries = args && typeof args === "object" && !Array.isArray(args) ? Object.entries(args as Record<string, unknown>) : [];
  const commandLike = entries.filter(([key]) => SHELL_COMMAND_KEYS.test(key));
  const [only] = commandLike;
  if (commandLike.length === 1 && only[0] === SHELL_COMMAND_KEY && typeof only[1] === "string") {
    return { ok: true, command: only[1] };
  }
  const found = commandLike.length > 0 ? commandLike.map(([key]) => key).join(", ") : "no command key";
  return {
    ok: false,
    reason: `ambiguous shell arguments: a shell call must carry its command as one string under \`${SHELL_COMMAND_KEY}\` and no other command-like key (found: ${found})`,
  };
}

/** The tools whose refused call could only have read. Exact names, compared lower-cased. */
export const READ_CLASS_TOOLS = [
  "view", "grep", "glob",
  // Task L1 fix round 2 (S4): every other tool in READ_TOOLS that only reads, and the SDK's
  // read-only shell-session tools -- a refused call of any of them could only have read.
  "read", "read_file", "ls", "list_directory", "search", "search_files", "find",
  ...SHELL_SESSION_READ_TOOLS,
  ...AGENT_READ_TOOLS,
];

/**
 * The fixed list of commands that only list, read, search or print (R3), compared lower-cased.
 * A shell command is `read` only if EVERY statement in it starts with one of these.
 */
export const READ_ONLY_COMMANDS = [
  "get-childitem", "gci", "ls", "dir",
  "get-content", "gc", "cat", "type",
  "select-string", "sls",
  "test-path",
  "get-command", "gcm",
  "get-item", "gi",
  "get-location", "pwd",
  "resolve-path",
  "select-object",
  "format-table",
  "format-list",
  "sort-object",
  "measure-object",
  "out-string",
  "write-host",
  "write-output", "echo",
];

/**
 * Any of these anywhere in a shell command makes it `act` (R3), compared lower-cased as substrings:
 * a script block, a sub-expression or array expression, a redirect, PowerShell's escape and
 * continuation character, a type literal (`[System.IO.Directory]::…`), the call and background
 * operators, and the two ways PowerShell evaluates a string as code. A substring match can only
 * make more commands `act` (`iex` inside a longer word too), never fewer.
 */
export const ACT_TOKENS = ["{", "}", "$(", "@(", ">", "`", "[", "&", "invoke-", "iex"];

/** What may stand directly before a `(` that groups a command: nothing, blank space, `,`, `(`, `|`, `;`. */
const GROUPING_PAREN_PRECEDER = /[\s,(|;]/;

/**
 * How a shell tool's command is quoted (Task L6, R1). `powershell` (the `powershell`/`pwsh` tools):
 * PowerShell's quoting, so a separator inside quotes does not split a statement. `plain` (bash, cmd and
 * every other shell tool): no quote is trusted and every separator splits, as before Task L6 -- their
 * quoting differs from PowerShell's (in bash `\"` is an escaped quote, in cmd `'` is no quote at all), so
 * reading them with PowerShell's rules could hide a real separator.
 */
export type ShellDialect = "powershell" | "plain";

export function shellDialectOf(toolName: string): ShellDialect {
  return /^(powershell|pwsh)/i.test(String(toolName ?? "")) ? "powershell" : "plain";
}

/** What ends a statement: `;`, `|` and every line break (a newline ends a statement exactly as `;` does). */
const STATEMENT_SEPARATOR = /[;|\n\r\u0085\u2028\u2029]/;

/**
 * PowerShell's typographic quotes: it reads U+2018-U+201B as single quotes and U+201C-U+201E as double
 * quotes, interchangeably with the ASCII ones -- so `'a\u2019 ; Remove-Item y ; \u2019b'` is two strings and a
 * statement to PowerShell. A command that holds one is not split with confidence.
 */
const TYPOGRAPHIC_QUOTES = /[\u2018-\u201e]/;

/**
 * A command under PowerShell's quoting (Task L6, R1; fix round 1): its statements -- split on `;`, `|` and
 * line breaks OUTSIDE quotes only -- and `outside`, the same text with every quoted string's content
 * blanked (the quote characters kept), which is all PowerShell interprets. `'…'` is literal, and `''`
 * inside it is one quote. Inside `"…"` PowerShell interprets exactly two things, `$` (a variable, `$(…)`)
 * and the backtick (an escape), so a double-quoted string holding either cannot be judged as text; `""`
 * inside it is one quote. `undefined` when the command cannot be split with confidence -- that, an
 * unterminated quote, a typographic quote, and outside quotes a `#` (a comment could hide a quote the
 * scanner would otherwise pair), an `@` (a here-string, splatting or an array expression), a backtick, or
 * the stop-parsing token `--%`.
 */
export function scanPowerShell(command: string): { statements: string[]; outside: string } | undefined {
  if (TYPOGRAPHIC_QUOTES.test(command) || command.includes("--%")) return undefined;
  const statements: string[] = [];
  let current = "";
  let outside = "";
  let quote: "'" | '"' | undefined;
  for (let i = 0; i < command.length; i++) {
    const c = command[i];
    if (quote !== undefined) {
      if (quote === '"' && (c === "$" || c === "`")) return undefined;
      current += c;
      if (c === quote) {
        if (command[i + 1] === quote) {
          current += c;
          outside += "  ";
          i += 1;
        } else {
          quote = undefined;
          outside += c;
        }
      } else outside += " ";
      continue;
    }
    if (c === "'" || c === '"') {
      quote = c;
      current += c;
      outside += c;
      continue;
    }
    if (c === "#" || c === "@" || c === "`") return undefined;
    outside += c;
    if (c === "\r" && command[i + 1] === "\n") continue; // one line break, not an empty statement
    if (STATEMENT_SEPARATOR.test(c)) {
      statements.push(current);
      current = "";
      continue;
    }
    current += c;
  }
  if (quote !== undefined) return undefined;
  statements.push(current);
  return { statements, outside };
}

/** `scanPowerShell`'s statements (Task L6, R1). */
export function splitPowerShellStatements(command: string): string[] | undefined {
  return scanPowerShell(command)?.statements;
}

/** A command's statements with no quote trusted: every separator splits (the `plain` dialect). */
function splitPlainStatements(command: string): string[] {
  return command.split(/\r\n|[;|\n\r\u0085\u2028\u2029]/);
}

/**
 * Whether a refused shell command could only have read (R3). Every statement must start with a
 * `READ_ONLY_COMMANDS` entry, and the command must hold none of `ACT_TOKENS`. On top of the ruling's list
 * (Task L1 brief correction): a `(` groups a whole command that runs in place -- `Write-Output (Remove-Item
 * x)` starts with a read-only cmdlet and holds no listed token, yet deletes -- so every `(` must open with
 * a read-only command, and one written directly after a name, a quote or `)` is a method call
 * (`(Get-Item x).Delete()`), which is `act`. An empty statement (`a || b`, a trailing `|`) is `act`.
 * Unknown or unparseable is `act`.
 *
 * For the PowerShell dialect (Task L6, R1 and fix round 1) statements end only at separators outside
 * quotes, and the tokens and the `(` rule are judged only on what PowerShell interprets
 * (`scanPowerShell`'s `outside`): a `(`, `[`, `{`, `>` or `&` inside a quoted regex is text. A command it
 * cannot split with confidence is `act`. For every other shell no quote is trusted: separators split
 * wherever they stand, and the tokens and the `(` rule are judged over the whole command.
 */
export function isReadOnlyShellCommand(command: unknown, dialect: ShellDialect = "powershell"): boolean {
  if (typeof command !== "string" || command.trim() === "") return false;
  const lower = command.toLowerCase();
  let statements: string[];
  let interpreted: string;
  if (dialect === "powershell") {
    const scan = scanPowerShell(lower);
    if (scan === undefined) return false;
    statements = scan.statements;
    interpreted = scan.outside;
  } else {
    statements = splitPlainStatements(lower);
    interpreted = lower;
  }
  if (ACT_TOKENS.some((token) => interpreted.includes(token))) return false;
  for (const match of interpreted.matchAll(/\(/g)) {
    const index = match.index ?? 0;
    if (index > 0 && !GROUPING_PAREN_PRECEDER.test(interpreted[index - 1])) return false;
    const opened = /^\(\s*([^\s()]*)/.exec(interpreted.slice(index))?.[1] ?? "";
    if (!READ_ONLY_COMMANDS.includes(opened)) return false;
  }
  return statements.every((statement) => {
    const first = statement.trim().split(/\s+/)[0] ?? "";
    return READ_ONLY_COMMANDS.includes(first);
  });
}

// ---------- severe denials (Task L6, R2; fix rounds 1 and 2) ----------

/** Why a refused call is severe: it reached outside the session's workflow or the sandbox, or tried to do damage. */
export type SevereCategory =
  | "sql"
  | "other-workflow"
  | "write-outside"
  | "outside-repository"
  | "tampering"
  | "destructive"
  | "script-root"
  | "script-backend"
  | "external-location"
  | "network"
  | "install"
  | "credential"
  | "ambiguous-arguments";

/** What `severeCategory` and `denialClass` need besides the call itself. */
export interface DenialContext {
  /** The reason the policy gave for the denial. */
  reason?: string;
  /** The session's own workflow: a write outside `workflows/<it>/`, or a script or path naming another, is severe. */
  wfId?: string;
  /** The repository root, so an absolute write path can be placed. */
  root?: string;
}

/**
 * Network tools and APIs (R2), matched in a shell command as names: not preceded by a letter, digit,
 * `_`, `-` or `.`, and not followed by a letter, digit, `_` or `-` (so `curl.exe`, `[System.Net.Dns]` and
 * `(New-Object Net.WebClient)` match; `sync`, `firmware`, `sftp`, `ssh_key` and `ssh-keygen` do not).
 * Anywhere in the command, inside quotes too: a quoted command can still be run (`powershell -c "iwr …"`)
 * -- except a search's own literal pattern (fix round 1, `sanitizeSearches`). Fix round 2 (minor 9) adds
 * `tnc` (the alias of `Test-NetConnection`), `bitsadmin`, `nslookup`, `ping`, and PowerShell remoting's
 * session commands (`Invoke-Command` is judged by its `-ComputerName`/`-Session`/`-HostName`, in
 * `severeShellText`).
 */
export const NETWORK_NAMES = [
  "curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "start-bitstransfer",
  "net.webclient", "system.net", "ftp", "scp", "ssh", "nc", "test-netconnection", "resolve-dnsname",
  "tnc", "bitsadmin", "nslookup", "ping", "enter-pssession", "new-pssession", "etsn", "nsn",
];

/**
 * Credential stores (R2), matched like `NETWORK_NAMES`. `env:` is PowerShell's environment drive, so it
 * also covers `$env:X`, `${env:X}`, `Get-ChildItem env:` and `gci Env:\`; a name that ends in a colon may
 * be followed by anything. Fix round 2 (minor 9) adds `printenv`.
 */
export const CREDENTIAL_NAMES = [
  "get-credential", "cmdkey", "vaultcmd", "$env:", "env:", "getenvironmentvariable", "getenvironmentvariables",
  "printenv",
];

/**
 * The Python modules (and one function) an interpreter one-liner reaches the network with (fix round 1).
 * Since fix round 2 (minor 6) they are judged by an import or a use -- `import m`, `from m import …`,
 * `from a import b` for `a.b`, `__import__('m')`, `m.attr` -- never by the bare word, so
 * `print('open requests: 3')` and `import urllib.parse` are not network calls.
 */
export const NETWORK_MODULES = [
  "urllib.request", "urllib2", "urllib3", "requests", "http.client", "http.server", "socket", "ftplib", "smtplib",
  "httpx", "aiohttp", "telnetlib", "xmlrpc.client", "urlopen",
];

/** An unrecognized tool whose name says it reaches the network (L6 fix round 1). */
export const NETWORK_TOOL_NAME = /fetch|web|http|url|download|browse|curl/i;
/** An unrecognized tool whose name says it deletes (L6 fix round 2, minor 9): `delete_file`, `remove_dir`, … */
export const DESTRUCTIVE_TOOL_NAME = /delete|remove|rmdir|unlink|erase|wipe|truncate/i;

/**
 * Where credentials live on a machine (L6 fix round 2, minor 9): a path with one of these folders
 * (`~/.ssh`, `~/.aws`, `~/.snowflake/connections.toml`, …), one of these files, or the gh CLI's
 * `hosts.yml` is a credential store, whatever tool reads it.
 */
export const CREDENTIAL_DIRS = [".ssh", ".aws", ".azure", ".snowflake", ".kube", ".docker", ".gnupg"];
export const CREDENTIAL_FILES = [".netrc", "_netrc", ".git-credentials", ".pgpass"];

const NAME_PATTERNS = new Map<string, RegExp>();
function namePattern(name: string): RegExp {
  let pattern = NAME_PATTERNS.get(name);
  if (!pattern) {
    const before = name.includes(".") ? "(?<![a-z0-9_-])" : "(?<![a-z0-9_.-])";
    const after = name.endsWith(":") ? "" : "(?![a-z0-9_-])";
    pattern = new RegExp(`${before}${escapeRe(name)}${after}`, "i");
    NAME_PATTERNS.set(name, pattern);
  }
  return pattern;
}

/** Whether a command text names any of `names` (see `NETWORK_NAMES`). */
export function namesAny(text: string, names: readonly string[]): boolean {
  return names.some((name) => namePattern(name).test(text));
}

/** The SDK's tools that type input into a running shell. Their input is a shell command by another channel. */
export const SHELL_SESSION_WRITE_TOOLS = ["write_powershell", "write_bash"];

/**
 * Planning tools whose arguments are the model's own text (L6 fix round 2, minor 13): a note that names
 * `workflows/wf_0002/` reaches nothing, so it is judged with its text blanked, like a write's content. A
 * `task` (a sub-agent's prompt) is not one of them: it starts work.
 */
export const PLANNING_TEXT_TOOLS = ["report_intent", "think", "todo", "update_todo", "ask_user", "fetch_copilot_cli_documentation"];

/** Where a shell command is cut into the pieces `severeShellText` judges: every separator and grouping. */
const SEVERITY_SPLIT = /[;|&\n\r\u0085\u2028\u2029(){}]/;
/** Any Python interpreter, however it is spelled (compared on the `normalizeToken` form). */
const PYTHON_HEAD = /(^|\/)(python(3(\.\d+)?)?|py)(\.exe)?$/;
/**
 * L6 fix round 2 (minor 12): `DESTRUCTIVE_SHELL`'s `format` pattern matches the word anywhere -- PowerShell's
 * read-only formatting cmdlets, `-Filter format*`, a file named `format.md` -- and that refusal is left as it
 * is. For the severe class only the disk command counts: `format <drive>:` (or `format.com`/`.exe`) and
 * `Format-Volume`; every other `format` is blanked before the destructive patterns are tried.
 */
const NOT_DISK_FORMAT = /\bformat(?!(?:\.com|\.exe)?\s+[a-z]:|-volume\b)/gi;
/** Whitespace of any kind at either end, removed only to PLACE a refused write's path. */
const EDGE_WHITESPACE_RUN = /^[\s\u180e\u200b-\u200d\u2060]+|[\s\u180e\u200b-\u200d\u2060]+$/gu;

/** The commands that delete (L6 fix round 1): PowerShell's `Remove-Item` and its aliases, POSIX `rm`/`rmdir`, cmd's `del`/`erase`/`rd`/`rmdir`. */
export const DELETE_COMMANDS = ["remove-item", "ri", "rm", "del", "erase", "rd", "rmdir"];
/**
 * A recursive or forced delete's flag: `-Recurse` or `-Force` in any prefix PowerShell accepts (and
 * `-Recurse:$true`), a POSIX bundle holding `r`, `R` or `f` (`-rf`, `-fr`, `-Rv`), `--recursive`,
 * `--force`, and cmd's `/s` and `/f`. `-Filter`, `-First` and `-WhatIf` are none of them.
 */
const DELETE_FLAG = [
  /^-(?:r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?|fo(?:r(?:c(?:e)?)?)?)(?::.*)?$/i,
  /^-[fiIrRdv]*[rRf][fiIrRdv]*$/,
  /^--(?:recursive|force)$/i,
  /^\/[sf]$/i,
];
/** `Invoke-Command`'s parameters that send the command to another computer or a remote session (fix round 2,
 * minor 9), in any prefix PowerShell accepts. */
const REMOTING_PARAM = /^-(?:com(?:p(?:u(?:t(?:e(?:r(?:n(?:a(?:m(?:e)?)?)?)?)?)?)?)?)?|cn|se(?:s(?:s(?:i(?:o(?:n)?)?)?)?)?|ho(?:s(?:t(?:n(?:a(?:m(?:e)?)?)?)?)?)?|connectionuri|uri|vmn(?:a(?:m(?:e)?)?)?|vmi(?:d)?|cont(?:a(?:i(?:n(?:e(?:r(?:i(?:d)?)?)?)?)?)?)?)(?::.*)?$/i;
/** git's subcommands that reach a remote (L6 fix round 1). */
export const GIT_NETWORK_SUBCOMMANDS = ["clone", "fetch", "pull", "push", "remote", "ls-remote"];
const PIP_INSTALLS = ["install", "download", "wheel"];
const NODE_INSTALLS = ["install", "i", "in", "ci", "add", "update", "up", "upgrade", "exec", "x", "dlx", "create"];
const UV_INSTALLS = ["add", "sync", "run", "lock"];
/** PowerShell's own installers (L6 fix round 2, minor 9). */
const POWERSHELL_INSTALLERS = ["install-module", "install-package", "install-script", "save-module", "save-package", "update-module"];
/** Package managers and the subcommands of theirs that fetch (L6 fix round 2, minor 9). */
const PACKAGE_MANAGER_INSTALLS: Record<string, string[]> = {
  winget: ["install", "upgrade", "download"],
  choco: ["install", "upgrade"],
  scoop: ["install", "update"],
  conda: ["install", "create", "update"],
  mamba: ["install", "create", "update"],
  gem: ["install"],
  cargo: ["install"],
};

/** A command word as a name: lower-cased, its directory and a `.exe`/`.cmd`/`.bat`/`.com` suffix dropped. */
function commandName(token: string): string {
  return normalizeToken(token).split("/").pop()!.replace(/\.(?:exe|cmd|bat|com)$/, "");
}

/** The first token that is not an option, after `-C <dir>` / `-c <x>` pairs are skipped (git, pip, npm, uv). */
function firstOperand(tokens: string[]): string | undefined {
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i] === "-C" || tokens[i] === "-c") {
      i += 1;
      continue;
    }
    if (!tokens[i].startsWith("-")) return tokens[i].toLowerCase();
  }
  return undefined;
}

/** A command another one runs: `cmd /c …`, `powershell -Command …`, `bash -c …`, `iex …`, and (fix round 2,
 * minor 9) `Start-Process <exe> -ArgumentList …` / `start <exe> …`. */
function unwrapCommand(tokens: string[]): string[] {
  let rest = tokens;
  for (let guard = 0; guard < 4 && rest.length > 1; guard++) {
    const head = commandName(rest[0]);
    if (head === "start-process" || head === "saps" || head === "start") {
      const file = rest.findIndex((token, i) => i > 0 && /^-(?:filepath|file|path)$/i.test(token));
      const exe = file >= 0 ? file + 1 : rest.findIndex((token, i) => i > 0 && !token.startsWith("-"));
      if (exe < 0 || exe >= rest.length) break;
      const list = rest.findIndex((token, i) => i > 0 && /^-(?:argumentlist|args|a)$/i.test(token));
      rest = list >= 0 ? [rest[exe], ...rest.slice(list + 1)] : rest.slice(exe);
      continue;
    }
    let at = -1;
    if (head === "cmd") at = /^\/[ck]$/i.test(rest[1]) ? 1 : -1;
    else if (head === "powershell" || head === "pwsh") at = rest.findIndex((token, i) => i > 0 && /^-c(?:o(?:m(?:m(?:a(?:n(?:d)?)?)?)?)?)?$/i.test(token));
    else if (head === "bash" || head === "sh") at = rest.indexOf("-c");
    else if (head === "iex" || head === "invoke-expression") at = 0;
    if (at < 0 || at + 1 >= rest.length) break;
    rest = rest.slice(at + 1);
  }
  return rest;
}

/** Whether a statement installs a package -- fetching code from the network (L6 fix rounds 1 and 2). */
function installs(tokens: string[]): boolean {
  const head = commandName(tokens[0]);
  const rest = tokens.slice(1);
  if (/^pip\d*(?:\.\d+)?$/.test(head)) return PIP_INSTALLS.includes(firstOperand(rest) ?? "");
  if (PYTHON_HEAD.test(normalizeToken(tokens[0]))) {
    const m = rest.indexOf("-m");
    return m >= 0 && /^pip\d*$/i.test(rest[m + 1] ?? "") && PIP_INSTALLS.includes(firstOperand(rest.slice(m + 2)) ?? "");
  }
  if (head === "npx" || head === "pnpx" || head === "uvx" || head === "pipx") return true;
  if (head === "npm" || head === "pnpm" || head === "yarn") {
    const sub = firstOperand(rest);
    return (head === "yarn" && sub === undefined) || NODE_INSTALLS.includes(sub ?? "");
  }
  if (head === "uv") {
    const sub = firstOperand(rest);
    if (UV_INSTALLS.includes(sub ?? "")) return true;
    const after = rest.slice(rest.findIndex((token) => token.toLowerCase() === sub) + 1);
    if (sub === "pip") return ["install", "sync", "compile"].includes(firstOperand(after) ?? "");
    if (sub === "tool") return ["install", "run", "upgrade"].includes(firstOperand(after) ?? "");
  }
  if (POWERSHELL_INSTALLERS.includes(head)) return true;
  const subcommands = PACKAGE_MANAGER_INSTALLS[head];
  return subcommands !== undefined && subcommands.includes(firstOperand(rest) ?? "");
}

/** Whether Python code imports or uses a network module (L6 fix round 2, minor 6). */
function codeReachesNetwork(code: string): boolean {
  return NETWORK_MODULES.some((module) => {
    const m = escapeRe(module);
    const forms = [
      `\\bimport\\s+(?:[\\w.]+\\s*(?:as\\s+\\w+\\s*)?,\\s*)*${m}\\b`,
      `\\bfrom\\s+${m}\\b`,
      `__import__\\(\\s*['"]${m}\\b`,
      `\\b${m}\\s*[.(]`,
    ];
    if (module.includes(".")) {
      const [parent, child] = [module.slice(0, module.lastIndexOf(".")), module.slice(module.lastIndexOf(".") + 1)];
      forms.push(`\\bfrom\\s+${escapeRe(parent)}\\s+import\\s+[^;\\n]*\\b${escapeRe(child)}\\b`);
    }
    return forms.some((form) => new RegExp(form).test(code));
  });
}

/** Whether Python code reads the environment (L6 fix round 2, minor 9): `os.environ`, `os.getenv`, `getenv(`. */
function codeReadsEnvironment(code: string): boolean {
  return /\bos\.environ\b|\bgetenv\s*\(|\benviron\s*\[/.test(code);
}

/**
 * What an interpreter one-liner reaches (L6 fix rounds 1 and 2): the code of a `python -c` (and the module
 * of a `python -m`), read under the tool's own quoting, judged by `codeReachesNetwork` and
 * `codeReadsEnvironment`. A command whose statements cannot be read with confidence is judged whole.
 */
function oneLinerFinding(text: string, tool: string): SevereCategory | undefined {
  const judge = (code: string, js: boolean): SevereCategory | undefined =>
    (js ? jsReachesNetwork(code) : codeReachesNetwork(code))
      ? "network"
      : (js ? /\bprocess\.env\b/.test(code) : codeReadsEnvironment(code)) ? "credential" : undefined;
  const statements = shellDialectOf(tool) === "powershell"
    ? scanPowerShell(text)?.statements.map(powerShellWords)
    : /^bash/.test(tool) ? posixStatements(text) : undefined;
  if (statements === undefined) {
    const tokens = text.split(/[\s;|&(){}]+/).map((token) => token.replace(/^['"]+|['"]+$/g, ""));
    const python = tokens.findIndex((token) => PYTHON_HEAD.test(normalizeToken(token)));
    if (python >= 0 && tokens.slice(python + 1).some((token) => token === "-c" || token === "-m")) {
      const found = judge(text, false);
      if (found) return found;
    }
    const node = tokens.findIndex((token) => NODE_HEAD.test(normalizeToken(token)));
    if (node >= 0 && tokens.slice(node + 1).some((token) => NODE_EVAL.includes(token))) return judge(text, true);
    return undefined;
  }
  for (const words of statements) {
    // the interpreter wherever it stands in the statement (`& python -c …`, `cmd /c python -c …`)
    const python = words.findIndex((word) => PYTHON_HEAD.test(normalizeToken(word.value)));
    for (let i = python + 1; python >= 0 && i < words.length - 1; i++) {
      const next = words[i + 1].value;
      if (words[i].value === "-m" && NETWORK_MODULES.some((module) => next === module || next.startsWith(`${module}.`))) return "network";
      if (words[i].value === "-c") {
        const found = judge(next, false);
        if (found) return found;
      }
    }
    // (fix round 2, minor 9) a `node -e` one-liner
    const node = words.findIndex((word) => NODE_HEAD.test(normalizeToken(word.value)));
    for (let i = node + 1; node >= 0 && i < words.length - 1; i++) {
      if (NODE_EVAL.includes(words[i].value)) {
        const found = judge(words[i + 1].value, true);
        if (found) return found;
      }
    }
  }
  return undefined;
}

/** A JavaScript runtime, however it is spelled, and the flags that run code given on its command line. */
const NODE_HEAD = /(^|\/)(node|deno|bun)(\.exe)?$/;
const NODE_EVAL = ["-e", "--eval", "-p", "--print"];

/** Whether JavaScript code reaches the network (L6 fix round 2, minor 9): a network module required or
 * imported, `fetch(`, `XMLHttpRequest`, `WebSocket`. */
function jsReachesNetwork(code: string): boolean {
  const module = String.raw`['"](?:node:)?(?:http|https|http2|net|dgram|tls|dns)['"]`;
  return new RegExp(String.raw`\brequire\s*\(\s*${module}|\bimport\s*\(\s*${module}|\bfrom\s+${module}|\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\b`).test(code);
}

/** The command text a shell call carries: every string under a `SHELL_COMMAND_KEYS` key (an array joined). */
function shellTexts(args: unknown): string[] {
  if (!args || typeof args !== "object" || Array.isArray(args)) return [];
  const texts: string[] = [];
  for (const [key, value] of Object.entries(args as Record<string, unknown>)) {
    if (!SHELL_COMMAND_KEYS.test(key)) continue;
    if (typeof value === "string") texts.push(value);
    else if (Array.isArray(value)) texts.push(value.filter((item) => typeof item === "string").join(" "));
  }
  return texts;
}

/** Every string in the arguments, as deep as the argument walker goes. */
function stringsIn(value: unknown, out: string[] = [], depth = 0): string[] {
  if (depth > MAX_ARG_DEPTH) return out;
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) for (const item of value) stringsIn(item, out, depth + 1);
  else if (value && typeof value === "object") for (const item of Object.values(value)) stringsIn(item, out, depth + 1);
  return out;
}

/**
 * A shell call whose arguments are gamed (fix round 1, I1/P1): TWO or more command-like keys, so the
 * policy judges one while the tool may run another. Since fix round 2 (minor 8) a lone command-like key that
 * is not `command` (`{input}`, `{cmd}`, `{Command}`) is a confused call, not a gamed one: an ordinary act.
 */
function gamesShellKeys(args: unknown): boolean {
  if (!args || typeof args !== "object" || Array.isArray(args)) return false;
  return Object.keys(args).filter((key) => SHELL_COMMAND_KEYS.test(key)).length > 1;
}

/** A rule-1a id that names no other workflow (L6 fix round 2, minor 4): `.`, `..`, or the own id with
 * trailing dots or spaces (which Windows strips). The decision still refuses it; the class does not park on it. */
function namesNoOtherWorkflow(named: string, id: string): boolean {
  return named === "." || named === ".." || named.replace(/[. ]+$/, "") === id;
}

/** A token that is a path into `workflows/<another id>` (as a whole, or as the value of `--x=` / `-X:`). */
function namesOtherWorkflow(token: string, id: string, root?: string): boolean {
  const candidates = [token];
  if (token.includes("=")) candidates.push(token.slice(token.indexOf("=") + 1));
  if (token.startsWith("-") && token.includes(":")) candidates.push(token.slice(token.indexOf(":") + 1));
  return candidates.some((candidate) => {
    if (!/[\\/]/.test(candidate)) return false;
    const checked = normalizeToolPath(candidate, root);
    if (!checked.ok) return false;
    const [first, second] = checked.path.split("/");
    return first === "workflows" && second !== undefined && second !== id && ID_PATTERN.test(second);
  });
}

/** One piece of a shell command between separators and groupings: its quote-stripped tokens, and whether it
 * receives a pipeline's input (it follows a `|`). */
interface ShellPiece {
  tokens: string[];
  afterPipe: boolean;
}

/** Each piece of a shell command between separators and groupings. */
function shellPieces(text: string): ShellPiece[] {
  const pieces: ShellPiece[] = [];
  let current = "";
  let afterPipe = false;
  const push = (next: boolean) => {
    const tokens = current.trim().split(/[\s,]+/).filter(Boolean).map((token) => token.replace(/^['"]+|['"]+$/g, ""));
    if (tokens.length > 0) pieces.push({ tokens, afterPipe });
    current = "";
    afterPipe = next;
  };
  for (const c of text) {
    if (SEVERITY_SPLIT.test(c)) push(c === "|");
    else current += c;
  }
  push(false);
  return pieces;
}

/** Whether a piece runs a `WORKFLOW_ID_SCRIPTS` script on another workflow, or names a path into one. */
function pieceReachesOtherWorkflow(tokens: string[], id: string, root?: string): boolean {
  if (PYTHON_HEAD.test(normalizeToken(tokens[0]))) {
    const rest = tokens.slice(1);
    // the script wherever it stands after the interpreter (`py -3 scripts/segment.py wf_0002`), in any spelling
    const at = rest.findIndex((token) => WORKFLOW_ID_SCRIPTS.includes(path.posix.normalize(normalizeToken(token))));
    if (at >= 0) {
      const first = rest.slice(at + 1).find((token) => !FLAG_TOKEN.test(token))?.toLowerCase();
      // the own id with a trailing dot or space names no other workflow (fix round 2, minor 4)
      if (first !== undefined && first !== id && !namesNoOtherWorkflow(first, id)) return true;
    }
  }
  return tokens.some((token) => namesOtherWorkflow(token, id, root));
}

// ---------- where a refused write lands (L6 fix round 2, I5 and I6) ----------

/**
 * The repository's own trees (L6 fix round 2, I6): a write into one of them, or into another workflow, or
 * into any dot-folder (`.git`, `.venv`, `.github`), is severe. A write that lands in a NEW folder or file of
 * the run root's top level (`_diag.py`, `tmp/x.py`) is a scratch file, and an ordinary act.
 */
export const PIPELINE_TREES = [
  "scripts", "orchestrator", "mappings", "catalog", "cookbook", "docs", "tests", "samples", "snowflake", "workflows",
  "node_modules",
];
/** The repository's top-level files, and the ones a tool loads on its own if they appear (`conftest.py` is
 * imported by pytest, `sitecustomize.py` by Python, `setup.cfg`/`pytest.ini`/`tox.ini` configure them). Every
 * top-level dot-file counts too. Compared lower-cased. */
export const TOP_LEVEL_PIPELINE_FILES = [
  "readme.md", "orchestrate.ts", "orchestrator.config.json", "config.json", "package.json", "package-lock.json",
  "pyproject.toml", "requirements.txt", "tsconfig.json", "conftest.py", "sitecustomize.py", "usercustomize.py",
  "pytest.ini", "setup.cfg", "setup.py", "tox.ini",
];

/** Whether a path names a UNC share (`\\host\share`, `//host/share`), which opens a network connection --
 * not the local device forms `\\?\` and `\\.\`. */
export function isUncPath(raw: string): boolean {
  return /^['"]?(?:\\\\|\/\/)(?![?.][\\/])[^\\/]/.test(String(raw ?? ""));
}

/** Whether a path names a credential store (`CREDENTIAL_DIRS`, `CREDENTIAL_FILES`, the gh CLI's `hosts.yml`). */
export function isCredentialPath(raw: string): boolean {
  const segments = String(raw ?? "").replace(/\\/g, "/").toLowerCase().split("/");
  if (segments.some((segment) => CREDENTIAL_DIRS.includes(segment))) return true;
  const base = segments[segments.length - 1] ?? "";
  if (CREDENTIAL_FILES.includes(base)) return true;
  const parent = segments[segments.length - 2] ?? "";
  return base === "hosts.yml" && (parent === "gh" || parent === "github cli");
}

/** A canonical segment that could be any name: an 8.3 short name, or (in a glob) a wildcard. `<` and `>`
 * cannot appear in a Windows file name, so no real segment is ever this. */
const ANY_NAME = "<any>";

/**
 * A path as Windows would read it (L6 fix round 2, I4 and minor 5): a `:stream` suffix cut from each segment,
 * trailing dots and spaces stripped from each segment. `anyName` says a segment is an 8.3 short name (`~`
 * then a digit) -- or, in a glob, holds a wildcard -- and so could be any name; such a segment becomes
 * `ANY_NAME`.
 */
function canonicalWindowsPath(raw: string, glob = false): { text: string; anyName: boolean } {
  const text = String(raw ?? "").replace(/\\/g, "/");
  const drive = /^[A-Za-z]:/.exec(text)?.[0] ?? "";
  let anyName = false;
  const segments = text.slice(drive.length).split("/").map((segment) => {
    const cut = segment.includes(":") ? segment.slice(0, segment.indexOf(":")) : segment;
    const stripped = cut === "." || cut === ".." ? cut : cut.replace(/[. ]+$/, "");
    if (/~\d/.test(stripped) || (glob && /[*?[\]{}!]/.test(stripped))) {
      anyName = true;
      return ANY_NAME;
    }
    return stripped;
  });
  return { text: drive + segments.join("/"), anyName };
}

/**
 * Where a refused write would land, judged by its TARGET (L6 fix rounds 1 and 2):
 * - `tampering`: the own workflow's `golden/**` or `audit.jsonl`;
 * - `other-workflow`: another workflow's folder;
 * - `outside-repository`: a leading `~` (the home directory);
 * - `write-outside`: outside the repository, a UNC share, a path that cannot be placed (an 8.3 name), a
 *   pipeline tree (`PIPELINE_TREES`, any dot-folder), or a top-level pipeline file (`TOP_LEVEL_PIPELINE_FILES`,
 *   any dot-file);
 * - `undefined`: inside the own workflow, or a NEW scratch file or folder at the run root's top level.
 * A trailing dot or space and a stream suffix are read as Windows reads them.
 */
export function writeTargetCategory(raw: string, id: string, root?: string): SevereCategory | undefined {
  const text = String(raw ?? "").replace(EDGE_WHITESPACE_RUN, "").replace(/^['"]+|['"]+$/g, "");
  if (!text) return undefined;
  if (startsAtHome(text)) return "outside-repository";
  if (isUncPath(text)) return "write-outside";
  const canonical = canonicalWindowsPath(text);
  if (canonical.anyName) return "write-outside";
  const placed = resolveInRepository(canonical.text, root);
  if (!placed.ok) return "write-outside";
  const where = placed.path;
  const own = `workflows/${id}`;
  if (where === own || where.startsWith(`${own}/`)) {
    const rest = where.slice(own.length + 1);
    return rest === "audit.jsonl" || /(^|\/)golden(\/|$)/.test(rest) ? "tampering" : undefined;
  }
  const [first, second] = where.split("/");
  if (first === "workflows" && second !== undefined && ID_PATTERN.test(second)) return "other-workflow";
  if (second === undefined) {
    return TOP_LEVEL_PIPELINE_FILES.includes(first) || PIPELINE_TREES.includes(first) || first.startsWith(".") ? "write-outside" : undefined;
  }
  return PIPELINE_TREES.includes(first) || first.startsWith(".") ? "write-outside" : undefined;
}

/**
 * Whether a Windows alias could reach another workflow, or out of the repository (L6 fix round 2, I4). X2
 * refuses every alias; a read through one used to be a plain read, so `view workflows./wf_0002/…` was
 * budgeted where `view workflows/wf_0002/…` parked. The alias is read as Windows would (`canonicalWindowsPath`,
 * an 8.3 segment -- or a glob's wildcard -- matching any name), and if the result could name another
 * workflow -- `workflows` (or an any-name segment) followed by another id (or an any-name segment) -- it is
 * `other-workflow`; if it resolves out of the repository, `outside-repository`. Every other alias is left
 * to its tool's class. `kind` says what the string is: a tool's `path` argument, a `glob` (a glob tool's
 * pattern or a grep filter, which is relative to its search path, so a leading `..` only climbs out of
 * that), or a shell `token` (a path or a glob, judged against the run root).
 */
function aliasReach(raw: string, id: string, root: string | undefined, kind: "path" | "glob" | "token"): SevereCategory | undefined {
  const text = String(raw ?? "").replace(/^['"]+|['"]+$/g, "");
  // a shell token names a path only with a separator (`done.` or `...` is a word)
  if (kind === "token" && !/[\\/]/.test(text)) return undefined;
  if (!(kind === "path" ? windowsAliasReason(text) : globAliasReason(text))) return undefined;
  // a glob's brace alternatives are judged one by one (`{a.,b}/*` names `a/*` and `b/*`, no workflow); too
  // many of them, and the braces are read as any name
  const alternatives = kind === "path" ? [text] : (expandBraces(text) ?? [text]);
  let found: SevereCategory | undefined;
  for (const alternative of alternatives) {
    const canonical = canonicalWindowsPath(alternative, kind !== "path");
    const placed = placeInRepository(canonical.text, root);
    let where: string;
    if (placed.ok) where = placed.path;
    else if (kind === "glob") where = path.posix.normalize(canonical.text.toLowerCase()).replace(/^(?:\.\.\/)+/, "");
    else {
      found ??= "outside-repository";
      continue;
    }
    const [first, second] = where.split("/");
    const couldBeWorkflows = first === "workflows" || first === ANY_NAME;
    if (couldBeWorkflows && second !== undefined && (second === ANY_NAME || (second !== id && ID_PATTERN.test(second)))) return "other-workflow";
  }
  return found;
}

/** Where a path lands, for the severe class: `resolveInRepository`, except that the repository root itself
 * (`.`, `./`, or the root's absolute path) is inside the repository, at `""` -- a read of the root is no read
 * outside it. */
function placeInRepository(text: string, root?: string): PathCheck {
  const unified = path.posix.normalize(text.replace(/\\/g, "/")).replace(/\/+$/, "").toLowerCase();
  if (unified === "." || unified === "") return { ok: true, path: "" };
  if (root && unified === path.posix.normalize(root.replace(/\\/g, "/")).replace(/\/+$/, "").toLowerCase()) return { ok: true, path: "" };
  return resolveInRepository(text, root);
}

/** A glob's brace alternatives, expanded (`a/{b,c}` is `a/b` and `a/c`), or `undefined` past `limit`. */
function expandBraces(text: string, limit = 64): string[] | undefined {
  let items = [text];
  for (let guard = 0; guard < 16; guard++) {
    const expanded: string[] = [];
    let changed = false;
    for (const item of items) {
      const group = /\{([^{}]*)\}/.exec(item);
      if (!group) {
        expanded.push(item);
        continue;
      }
      changed = true;
      for (const choice of group[1].split(",")) expanded.push(item.slice(0, group.index) + choice + item.slice(group.index + group[0].length));
    }
    if (expanded.length > limit) return undefined;
    items = expanded;
    if (!changed) return items;
  }
  return undefined;
}

/** What a write-capable shell command writes (L6 fix round 2, I5): its targets are `first` (the first
 * positional), `all` (every positional), or `last` (a copy's destination; its sources are only read). */
const WRITE_COMMANDS: Record<string, "first" | "all" | "last"> = {
  "set-content": "first", sc: "first", "add-content": "first", ac: "first", "out-file": "first", "tee-object": "first",
  "export-csv": "first", "export-clixml": "first", "new-item": "first", ni: "first", "rename-item": "first", ren: "first",
  rni: "first",
  "clear-content": "all", clc: "all", md: "all", mkdir: "all", touch: "all", tee: "all",
  "remove-item": "all", ri: "all", rm: "all", del: "all", erase: "all", rd: "all", rmdir: "all",
  "move-item": "all", mv: "all", move: "all", mi: "all",
  "copy-item": "last", cp: "last", copy: "last", cpi: "last",
};
/** PowerShell parameters whose value is a written path (`-Path` of a copy is its source, and is skipped). */
const WRITE_PATH_PARAMS = ["path", "literalpath", "filepath", "destination", "pspath", "lp"];
/** PowerShell parameters that take a value that is not a written path. */
const WRITE_VALUE_PARAMS = [
  "value", "encoding", "itemtype", "type", "name", "filter", "include", "exclude", "credential", "stream", "delimiter",
  "inputobject", "width", "newname", "property", "attributes", "target", "erroraction", "ea", "outvariable", "ov",
];

/** The targets a write-capable piece names, or `undefined` when it is no write command. */
function writeTargetsOf(tokens: string[]): string[] | undefined {
  const head = commandName(tokens[0]);
  const mode = WRITE_COMMANDS[head];
  if (mode === undefined) return undefined;
  const targets: string[] = [];
  const positional: string[] = [];
  for (let i = 1; i < tokens.length; i++) {
    const token = tokens[i];
    if (/^--target-directory=/i.test(token)) {
      targets.push(token.slice(token.indexOf("=") + 1));
      continue;
    }
    if (token === "-t" || /^--target-directory$/i.test(token)) {
      if (tokens[i + 1] !== undefined) targets.push(tokens[i + 1]);
      i += 1;
      continue;
    }
    if (/^-[A-Za-z]/.test(token)) {
      const colon = token.indexOf(":");
      const name = (colon > 0 ? token.slice(1, colon) : token.slice(1)).toLowerCase();
      const inline = colon > 0 ? token.slice(colon + 1) : undefined;
      const pathParam = WRITE_PATH_PARAMS.find((param) => param === name || (name.length >= 2 && param.startsWith(name)));
      if (pathParam && !(mode === "last" && pathParam !== "destination")) {
        const value = inline ?? tokens[i + 1];
        if (value !== undefined) targets.push(value);
        if (inline === undefined) i += 1;
        continue;
      }
      if (pathParam || WRITE_VALUE_PARAMS.some((param) => param === name || (name.length >= 2 && param.startsWith(name)))) {
        if (inline === undefined) i += 1;
      }
      continue;
    }
    if (/^\/[a-z]$/i.test(token)) continue; // a cmd switch (`/s`, `/q`, `/y`)
    positional.push(token);
  }
  if (mode === "first" && positional.length > 0) targets.push(positional[0]);
  if (mode === "all") targets.push(...positional);
  if (mode === "last" && positional.length > 1) targets.push(positional[positional.length - 1]);
  return targets;
}

/** Where a piece redirects its output (`> x`, `>>x`, `2> x`, `*> x`), devices and handles excepted. */
function redirectTargets(tokens: string[]): string[] {
  const targets: string[] = [];
  tokens.forEach((token, i) => {
    const match = /^(?:\d|\*)?>>?(.*)$/.exec(token);
    if (!match) return;
    const target = match[1] || tokens[i + 1];
    if (target === undefined || target === "" || /^(?:\$null|nul|null|\/dev\/null)$/i.test(target) || target.startsWith("&")) return;
    targets.push(target);
  });
  return targets;
}

/** The paths a .NET file method writes (`[IO.File]::WriteAllText('x', …)`, `[IO.Directory]::Delete('x', $true)`). */
function dotNetWriteTargets(text: string): string[] {
  const targets: string[] = [];
  const calls = /::\s*(?:delete|writealltext|writealllines|writeallbytes|appendalltext|appendalllines|appendtext|create|createtext|createdirectory|move|copy|replace)\s*\(([^)]*)\)/gi;
  for (const call of text.matchAll(calls)) {
    for (const literal of call[1].matchAll(/'([^']*)'|"([^"]*)"/g)) targets.push(literal[1] ?? literal[2]);
  }
  return targets;
}

/**
 * What a shell command attempted, judged on the call and not on which rule refused it: the first
 * matching rule wins, and a chained command (`…; echo done`) is refused as a metacharacter before the
 * deeper rules ever see it. So each piece between separators and groupings is judged on its own -- a
 * command another one runs (`cmd /c`, `powershell -c`, `bash -c`, `iex`) included:
 * - `destructive`: a `DESTRUCTIVE_SHELL` match anywhere (a `format` only as the disk command), or (fix
 *   round 1) a delete command with a recursive or force flag;
 * - `network`: git's remote subcommands, `certutil -urlcache`, a Python one-liner using a network module;
 * - `install`: a package installer (`pip install`, `python -m pip`, `npm install`/`ci`, `npx`, `uv add`,
 *   `Install-Module`, `winget install`, …);
 * - `credential`: `cmd /c set` (cmd's environment listing), a Python one-liner reading the environment;
 * - `script-root` / `script-backend`: a Python invocation given `--root` or a Snowflake-account flag;
 * - a script's path flag (fix round 2, P1) or a write-capable command's target (fix round 2, I5: `Set-Content`,
 *   `Out-File`, `Remove-Item`, `Copy-Item`'s destination, `Move-Item`, `>`, `>>`, a .NET write, …) judged by
 *   `writeTargetCategory`; a write-capable command with no target that takes a pipeline's input (`gci -r
 *   workflows | Remove-Item`) cannot be placed, and is `write-outside`;
 * - `other-workflow`: a `WORKFLOW_ID_SCRIPTS` script run on another workflow, or a path into one.
 */
function severeShellText(text: string, id: string | undefined, root: string | undefined, tool: string): SevereCategory | undefined {
  if (DESTRUCTIVE_SHELL.some((pattern) => pattern.test(text.replace(NOT_DISK_FORMAT, " ")))) return "destructive";
  if (/\bcertutil(?:\.exe)?\b[^;|&\n]*[-/](?:urlcache|verifyctl|url)\b/i.test(text)) return "network";
  for (const { tokens: piece, afterPipe } of shellPieces(text)) {
    if ((commandName(piece[0]) === "cmd" && /^\/[ck]$/i.test(piece[1] ?? "") && (piece[2] ?? "").toLowerCase() === "set") ||
        (/^cmd/.test(tool) && commandName(piece[0]) === "set")) {
      return "credential";
    }
    const tokens = unwrapCommand(piece);
    const head = commandName(tokens[0]);
    if (DELETE_COMMANDS.includes(head) && tokens.slice(1).some((token) => DELETE_FLAG.some((flag) => flag.test(token)))) {
      return "destructive";
    }
    if (head === "git" && GIT_NETWORK_SUBCOMMANDS.includes(firstOperand(tokens.slice(1)) ?? "")) return "network";
    // (fix round 2, minor 9) PowerShell remoting: a command run on another computer or in a remote session
    if ((head === "invoke-command" || head === "icm") && tokens.slice(1).some((token) => REMOTING_PARAM.test(token))) return "network";
    if (installs(tokens)) return "install";
    if (PYTHON_HEAD.test(normalizeToken(tokens[0]))) {
      const rest = tokens.slice(1);
      if (rest.some((token) => SCRIPT_ROOT_FLAG.test(token))) return "script-root";
      if (rest.some(isScriptBackendFlag)) return "script-backend";
      const at = rest.findIndex((token) => Object.hasOwn(SCRIPT_FLAGS, path.posix.normalize(normalizeToken(token))));
      if (id !== undefined && at >= 0) {
        const script = path.posix.normalize(normalizeToken(rest[at]));
        for (const { kind, value } of scriptPathFlags(script, rest.slice(at + 1))) {
          if (kind === "write") {
            const found = writeTargetCategory(value, id, root);
            if (found) return found;
            const placed = resolveInRepository(canonicalWindowsPath(value).text, root);
            if (!placed.ok || !placed.path.startsWith(`workflows/${id}/`)) return "write-outside";
          } else {
            if (startsAtHome(value)) return "outside-repository";
            const placed = placeInRepository(canonicalWindowsPath(value).text, root);
            if (!placed.ok) return "outside-repository";
            const [first, second] = placed.path.split("/");
            if (first === "workflows" && second !== undefined && second !== id && ID_PATTERN.test(second)) return "other-workflow";
          }
        }
      }
    }
    if (id !== undefined) {
      const targets = [...(writeTargetsOf(tokens) ?? []), ...redirectTargets(tokens)];
      if (targets.length === 0 && afterPipe && writeTargetsOf(tokens) !== undefined) return "write-outside";
      for (const target of targets) {
        const found = writeTargetCategory(target, id, root);
        if (found) return found;
      }
      if (pieceReachesOtherWorkflow(tokens, id, root)) return "other-workflow";
    }
  }
  if (id !== undefined) {
    for (const target of dotNetWriteTargets(text)) {
      const found = writeTargetCategory(target, id, root);
      if (found) return found;
    }
  }
  return oneLinerFinding(text, tool);
}

// ---------- a search's own pattern is text (L6 fix rounds 1 and 2) ----------

/** A word of a shell statement: its raw text (quotes kept), its value to the command, and whether it is
 * a literal -- nothing in it expands (a variable, a sub-expression, a glob). */
interface ShellWord {
  raw: string;
  value: string;
  literal: boolean;
}

/** A PowerShell statement's words (`scanPowerShell` has already refused `$`/backtick inside double quotes,
 * and `#`/`@`/backtick outside quotes). Outside quotes, `$`, `(`, `)`, `{`, `}`, `[` or `]` is no literal. */
function powerShellWords(statement: string): ShellWord[] {
  const words: ShellWord[] = [];
  let word: ShellWord | undefined;
  let quote: "'" | '"' | undefined;
  for (let i = 0; i < statement.length; i++) {
    const c = statement[i];
    if (quote === undefined && /\s/.test(c)) {
      if (word) words.push(word);
      word = undefined;
      continue;
    }
    word ??= { raw: "", value: "", literal: true };
    word.raw += c;
    if (quote !== undefined) {
      if (c === quote) {
        if (statement[i + 1] === quote) {
          word.raw += c;
          word.value += c;
          i += 1;
        } else quote = undefined;
      } else word.value += c;
      continue;
    }
    if (c === "'" || c === '"') quote = c;
    else {
      if (/[$(){}[\]]/.test(c)) word.literal = false;
      word.value += c;
    }
  }
  if (word) words.push(word);
  return words;
}

/**
 * A POSIX command's statements, as words (the `bash` tool's quoting): `'…'` is literal; inside `"…"` a
 * backslash escapes the next character, and `$` or a backtick expands; outside quotes a backslash escapes
 * the next character, and `$`, a backtick, a glob character or a brace expands. `;`, `|`, `&`, `(`, `)` and
 * line breaks outside quotes end a statement; a redirection (`<`, `>`, `>>`) is kept as a word of its own, so
 * its target stays in the statement (L6 fix round 2). `undefined` for an unterminated quote or a comment.
 */
function posixStatements(command: string): ShellWord[][] | undefined {
  const statements: ShellWord[][] = [];
  let words: ShellWord[] = [];
  let word: ShellWord | undefined;
  let quote: "'" | '"' | undefined;
  const end = () => {
    if (word) words.push(word);
    word = undefined;
  };
  for (let i = 0; i < command.length; i++) {
    const c = command[i];
    if (quote === "'") {
      word!.raw += c;
      if (c === "'") quote = undefined;
      else word!.value += c;
      continue;
    }
    if (quote === '"') {
      word!.raw += c;
      if (c === "\\" && i + 1 < command.length) {
        word!.raw += command[i + 1];
        word!.value += command[i + 1];
        i += 1;
      } else if (c === '"') quote = undefined;
      else {
        if (c === "$" || c === "`") word!.literal = false;
        word!.value += c;
      }
      continue;
    }
    if (/[;|&()\n\r]/.test(c)) {
      end();
      if (words.length > 0) statements.push(words);
      words = [];
      continue;
    }
    if (c === "<" || c === ">") {
      end();
      let redirect = c;
      while (command[i + 1] === ">") {
        redirect += ">";
        i += 1;
      }
      words.push({ raw: redirect, value: redirect, literal: false });
      continue;
    }
    if (/\s/.test(c)) {
      end();
      continue;
    }
    if (c === "#" && word === undefined) return undefined;
    word ??= { raw: "", value: "", literal: true };
    word.raw += c;
    if (c === "'" || c === '"') quote = c;
    else if (c === "\\" && i + 1 < command.length) {
      word.raw += command[i + 1];
      word.value += command[i + 1];
      i += 1;
    } else {
      if (/[$`*?[\]{}]/.test(c)) word.literal = false;
      word.value += c;
    }
  }
  if (quote !== undefined) return undefined;
  end();
  if (words.length > 0) statements.push(words);
  return statements;
}

/** Select-String's parameters, and the common ones, by what they take. */
const SELECT_STRING_VALUES = [
  "pattern", "path", "literalpath", "inputobject", "include", "exclude", "context", "encoding", "culture",
  "erroraction", "warningaction", "informationaction", "progressaction", "errorvariable", "warningvariable",
  "informationvariable", "outvariable", "outbuffer", "pipelinevariable", "ea", "wa", "infa", "proga", "ev", "wv",
  "iv", "ov", "ob", "pv", "pspath", "lp",
];
const SELECT_STRING_SWITCHES = ["simplematch", "casesensitive", "quiet", "list", "notmatch", "allmatches", "raw", "noemphasis", "verbose", "debug", "vb", "db"];

/** A Select-String parameter as PowerShell resolves it: the exact name, or the one name the prefix fits. */
function selectStringParameter(name: string): string | undefined {
  const all = [...SELECT_STRING_VALUES, ...SELECT_STRING_SWITCHES];
  if (all.includes(name)) return name;
  const fits = all.filter((candidate) => candidate.startsWith(name));
  return fits.length === 1 ? fits[0] : undefined;
}

/** The index of a Select-String statement's pattern word (inline `-Pattern:x` included), or `undefined`
 * when the statement holds a parameter this reading does not know. */
function selectStringPattern(words: ShellWord[]): number | undefined {
  let pattern: number | undefined;
  const positional: number[] = [];
  for (let i = 1; i < words.length; i++) {
    const raw = words[i].raw;
    if (!raw.startsWith("-") || raw.length < 2) {
      positional.push(i);
      continue;
    }
    const colon = raw.indexOf(":");
    const parameter = selectStringParameter((colon > 0 ? raw.slice(1, colon) : raw.slice(1)).toLowerCase());
    if (parameter === undefined) return undefined;
    if (SELECT_STRING_SWITCHES.includes(parameter)) continue;
    if (parameter === "pattern") pattern = colon > 0 ? i : i + 1;
    if (colon < 0) i += 1;
  }
  return pattern ?? positional[0];
}

/** How a grep-like command's options are read: the short options that take a value, the ones that are
 * switches, the same for long options, the ones that mean the patterns come from a file, and the ones after
 * which there is no pattern at all (`rg --files`). */
interface SearchOptions {
  shortValues: string;
  shortSwitches: string;
  longValues: string[];
  longSwitches: string[];
  fromFile: { short: string; long: string[] };
  noPattern: string[];
}

const GREP_OPTIONS: SearchOptions = {
  shortValues: "ABCmdD",
  shortSwitches: "EFGPiyvwxcoqsbHhnTZzaIrRlLU0123456789",
  longValues: [
    "include", "exclude", "exclude-dir", "exclude-from", "after-context", "before-context", "context", "max-count",
    "directories", "devices", "binary-files", "label", "group-separator",
  ],
  longSwitches: [
    "recursive", "dereference-recursive", "line-number", "ignore-case", "no-ignore-case", "files-with-matches",
    "files-without-match", "count", "only-matching", "word-regexp", "line-regexp", "invert-match", "extended-regexp",
    "fixed-strings", "basic-regexp", "perl-regexp", "no-filename", "with-filename", "quiet", "silent", "null",
    "null-data", "text", "byte-offset", "initial-tab", "no-messages", "line-buffered", "color", "colour",
  ],
  fromFile: { short: "f", long: ["file"] },
  noPattern: [],
};

/** ripgrep's options (L6 fix round 2, minor 3). */
const RG_OPTIONS: SearchOptions = {
  shortValues: "gtTABCmMjrEd",
  shortSwitches: "isSnNlcvwxFuULHhopzqaI0.",
  longValues: [
    "glob", "iglob", "type", "type-not", "max-count", "max-depth", "replace", "context", "after-context",
    "before-context", "encoding", "max-columns", "threads", "sort", "sortr", "pre", "pre-glob", "color", "colors",
    "path-separator", "max-filesize", "context-separator", "engine", "type-add", "ignore-file",
  ],
  longSwitches: [
    "ignore-case", "smart-case", "case-sensitive", "line-number", "no-line-number", "files-with-matches",
    "files-without-match", "count", "count-matches", "invert-match", "word-regexp", "line-regexp", "fixed-strings",
    "hidden", "no-ignore", "follow", "with-filename", "no-filename", "only-matching", "pretty", "null", "quiet", "text",
    "multiline", "multiline-dotall", "json", "vimgrep", "heading", "no-heading", "column", "trim", "unrestricted",
    "no-messages", "crlf", "search-zip", "binary", "stats", "no-config", "one-file-system", "byte-offset",
    "line-buffered", "block-buffered",
  ],
  fromFile: { short: "f", long: ["file"] },
  noPattern: ["files", "type-list"],
};

/** The index of a grep-like statement's pattern word (`-e x`, `-ex`, `--regexp=x` included), or `undefined`
 * when it reads its patterns from a file, has none, or holds an option this reading does not know. */
function optionPattern(words: ShellWord[], spec: SearchOptions): number | undefined {
  let pattern: number | undefined;
  const positional: number[] = [];
  let options = true;
  for (let i = 1; i < words.length; i++) {
    const value = words[i].value;
    if (options && value === "--") {
      options = false;
      continue;
    }
    if (options && value.startsWith("--")) {
      const equals = value.indexOf("=");
      const name = (equals > 0 ? value.slice(2, equals) : value.slice(2)).toLowerCase();
      if (name === "regexp") {
        pattern = equals > 0 ? i : i + 1;
        if (equals < 0) i += 1;
      } else if (spec.fromFile.long.includes(name) || spec.noPattern.includes(name)) return undefined;
      else if (spec.longValues.includes(name)) {
        if (equals < 0) i += 1;
      } else if (!spec.longSwitches.includes(name)) return undefined;
      continue;
    }
    if (options && value.startsWith("-") && value.length > 1) {
      for (let j = 1; j < value.length; j++) {
        const flag = value[j];
        if (flag === "e") {
          pattern = j + 1 < value.length ? i : i + 1;
          if (j + 1 === value.length) i += 1;
          break;
        }
        if (spec.fromFile.short.includes(flag)) return undefined;
        if (spec.shortValues.includes(flag)) {
          if (j + 1 === value.length) i += 1;
          break;
        }
        if (!spec.shortSwitches.includes(flag)) return undefined;
      }
      continue;
    }
    positional.push(i);
  }
  return pattern ?? positional[0];
}

const grepPattern = (words: ShellWord[]) => optionPattern(words, GREP_OPTIONS);
const rgPattern = (words: ShellWord[]) => optionPattern(words, RG_OPTIONS);

/** The index of a findstr statement's pattern word (`/c:"x"` inline, or the first operand), or `undefined`
 * when its patterns or files come from a file (`/g:`, `/f:`) (L6 fix round 2, minor 3). */
function findstrPattern(words: ShellWord[]): number | undefined {
  for (let i = 1; i < words.length; i++) {
    const value = words[i].value;
    if (/^\/c:/i.test(value)) return i;
    if (/^\/[gf]:/i.test(value)) return undefined;
    if (/^\/[a-z](?::.*)?$/i.test(value)) continue;
    return i;
  }
  return undefined;
}

/**
 * A shell command with every search's own literal pattern blanked (L6 fix rounds 1 and 2): a search for the
 * text `env:`, `curl` or `workflows/wf_0002/` names nothing it reaches, so it is never severe by its pattern
 * text alone. Under PowerShell's quoting: `Select-String`/`sls`, and (fix round 2, minor 3) `grep`/`egrep`/
 * `fgrep`, `rg` and `findstr`; under the bash tool's POSIX quoting: `grep`/`egrep`/`fgrep` and `rg`. Only a
 * LITERAL pattern is blanked (`$env:X` expands, so it is not one), and only where the statement is read with
 * confidence: a command PowerShell's quoting cannot split, a statement with a parameter or an option this
 * reading does not know, and every other shell (cmd's quoting is not POSIX's) are left as they are. The
 * statements are rebuilt joined by `|`, which can only make a later write command count as taking a
 * pipeline's input. The result is used only to judge severity; the decision never sees it.
 */
export function sanitizeSearches(command: string, toolName: string): string {
  const tool = String(toolName ?? "").toLowerCase();
  let statements: ShellWord[][] | undefined;
  let searches: Record<string, (words: ShellWord[]) => number | undefined>;
  if (shellDialectOf(tool) === "powershell") {
    statements = scanPowerShell(command)?.statements.map(powerShellWords);
    searches = {
      "select-string": selectStringPattern, sls: selectStringPattern, grep: grepPattern, egrep: grepPattern,
      fgrep: grepPattern, rg: rgPattern, findstr: findstrPattern,
    };
  } else if (/^bash/.test(tool)) {
    statements = posixStatements(command);
    searches = { grep: grepPattern, egrep: grepPattern, fgrep: grepPattern, rg: rgPattern };
  } else return command;
  if (statements === undefined) return command;
  let changed = false;
  const rebuilt = statements.map((words) => {
    const find = words.length > 0 && words[0].literal ? searches[commandName(words[0].value)] : undefined;
    const at = find?.(words);
    if (at === undefined || words[at] === undefined || !words[at].literal) return words.map((word) => word.raw).join(" ");
    changed = true;
    return words.map((word, i) => (i !== at ? word.raw : word.raw.startsWith("-") ? `${word.raw.slice(0, word.raw.indexOf(":") + 1)}''` : "''")).join(" ");
  });
  return changed ? rebuilt.join(" | ") : command;
}

/**
 * The arguments a severity judgement reads (L6 fix rounds 1 and 2): a search's own pattern, a write's content
 * and a planning tool's text are text, never a reach. So the `grep` tool's `pattern` is blanked, a file
 * write's content keys (`CONTENT_ARG_KEYS`) are blanked -- a write is judged by its target --, every string of
 * a planning tool (`PLANNING_TEXT_TOOLS`) is blanked, and a shell command's search patterns are blanked
 * (`sanitizeSearches`). `changed` says whether anything was.
 */
function severityView(tool: string, args: unknown): { args: unknown; changed: boolean } {
  if (!args || typeof args !== "object" || Array.isArray(args)) return { args, changed: false };
  const copy: Record<string, unknown> = { ...(args as Record<string, unknown>) };
  let changed = false;
  if (tool === "grep" && typeof copy.pattern === "string") {
    copy.pattern = "";
    changed = true;
  }
  if (PLANNING_TEXT_TOOLS.includes(tool)) {
    for (const key of Object.keys(copy)) {
      copy[key] = "";
      changed = true;
    }
  }
  if (WRITE_TOOL.test(tool) && tool !== AGENT_MESSAGE_TOOL && !SHELL_SESSION_WRITE_TOOLS.includes(tool)) {
    for (const key of Object.keys(copy)) {
      if (CONTENT_ARG_KEYS.has(key.toLowerCase()) && typeof copy[key] === "string") {
        copy[key] = "";
        changed = true;
      }
    }
  }
  if (SHELL_TOOL.test(tool) && typeof copy.command === "string") {
    const sanitized = sanitizeSearches(copy.command, tool);
    if (sanitized !== copy.command) {
      copy.command = sanitized;
      changed = true;
    }
  }
  return { args: changed ? copy : args, changed };
}

/** Whether a call's arguments still reach another workflow: rule 1a's mention (not `.`, `..` or the own id
 * with a trailing dot, fix round 2 minor 4), a path argument, or (in a shell command) a script on another
 * workflow or a path into one. */
function reachesOtherWorkflow(tool: string, args: unknown, id: string, root?: string): boolean {
  const raw = JSON.stringify(args ?? {}).replace(/\\{1,2}/g, "/").toLowerCase();
  for (const match of raw.matchAll(OTHER_WORKFLOW)) if (match[1] !== id && !namesNoOtherWorkflow(match[1], id)) return true;
  for (const candidate of collectPathArgs(args).paths) {
    const checked = normalizeToolPath(candidate, root);
    if (!checked.ok) continue;
    const [first, second] = checked.path.split("/");
    if (first === "workflows" && second !== undefined && second !== id && ID_PATTERN.test(second)) return true;
  }
  if (SHELL_TOOL.test(tool)) {
    for (const text of shellTexts(args)) {
      if (shellPieces(text).some((piece) => pieceReachesOtherWorkflow(unwrapCommand(piece.tokens), id, root))) return true;
    }
  }
  return false;
}

/** The file headers of an `apply_patch` call (`*** Add File: x`, `*** Update File: x`, `*** Delete File: x`,
 * `*** Move to: x`): the files it writes (L6 fix round 2, minor 2). */
function patchTargets(args: unknown): string[] {
  const targets: string[] = [];
  for (const text of stringsIn(args)) {
    for (const match of text.matchAll(/^\*\*\* (?:(?:Add|Update|Delete) File|Move to): *(.+?) *$/gm)) targets.push(match[1]);
  }
  return targets;
}

/** A shell token or a glob that starts at the home directory (fix round 2, P2): `~`, `~/x`, `~\x`, `~user`,
 * `~user/x` -- a path whose first segment starts with `~`, and not a `~` inside other text (`--format=~%h`). */
const HOME_TOKEN = /^['"]?~(?:[\\/]|$|[\w.-]+(?:[\\/]|$))/;

/** Every string of a call that could name a path, for the path-shape checks of `severeCategory`: the
 * path arguments, a glob's pattern and a grep's filters, and every token of a shell command, with the value
 * of a `--x=` or `-X:` token. */
function pathStringsOf(tool: string, args: unknown, commands: string[]): { paths: string[]; globs: string[]; tokens: string[] } {
  const paths = collectPathArgs(args).paths;
  const globs: string[] = [];
  const tokens: string[] = [];
  if (tool === "glob" && args && typeof args === "object" && typeof (args as { pattern?: unknown }).pattern === "string") {
    globs.push((args as { pattern: string }).pattern);
  }
  if (tool === "grep") globs.push(...stringsUnder(args, /^(glob|include|includepattern)$/i));
  for (const text of commands) {
    for (const piece of shellPieces(text)) {
      for (const token of piece.tokens) {
        tokens.push(token);
        if (token.includes("=")) tokens.push(token.slice(token.indexOf("=") + 1));
        // a PowerShell `-Name:value` (not the `://` of a URL run into another word)
        if (/^-[A-Za-z][\w-]*:/.test(token) && !/^-[\w-]*(?:https?|ftp|file):\/\//i.test(token)) tokens.push(token.slice(token.indexOf(":") + 1));
      }
    }
  }
  return { paths, globs, tokens };
}

/**
 * Why a refused call is severe (Task L6, R2; fix rounds 1 and 2), or `undefined` if it is not. Built from
 * the policy's own deny reasons, plus the same judgement made on the call itself where a chained command
 * would hide the reason:
 * - `sql`: any SQL-tool denial (an ambiguous statement key and an external location included) -- but the
 *   SDK's own `sql` todo store is an ordinary act (fix round 2, P3);
 * - `ambiguous-arguments`: a shell call with two or more command-like keys (I1/P1; a lone wrong key is an act
 *   since fix round 2), or a grep/glob call with a path key BESIDE `paths` (fix round 1, X2b; a lone wrong key
 *   is a read since fix round 2, I1);
 * - `outside-repository`: a path whose first segment starts with `~`, the home directory (fix round 2, P2), or
 *   a Windows alias that resolves out of the repository (I4);
 * - `network`: a shell command -- or input typed into a running shell -- that names a network tool or API
 *   (`NETWORK_NAMES`), git's remote subcommands, `certutil -urlcache`, a Python one-liner using a network
 *   module, a UNC share, or an unrecognized tool whose name says it reaches the network (`NETWORK_TOOL_NAME`);
 * - `install`: a package installer; `credential`: a credential store (`CREDENTIAL_NAMES`, a credential file,
 *   `cmd /c set`, a Python one-liner reading the environment);
 * - `other-workflow`: the reason is another workflow (`no access to other workflows`, `cross-workflow:`) --
 *   unless its id is `.`, `..` or the own id with a trailing dot (fix round 2, minor 4) --, a shell command
 *   runs a script on another workflow or names a path into one, or a Windows alias could name another
 *   workflow (fix round 2, I4). A BROAD read that could reach other workflows (`broad-read: …`) is not severe:
 *   it is a read. A search's own pattern, a write's content and a planning tool's text never make a call
 *   severe: they are judged with that text blanked (`severityView`);
 * - `destructive`, `script-root`, `script-backend`, `external-location`: those reasons, or (for the first
 *   three) the same finding in any piece of a shell command -- and a recursive or forced delete, and an
 *   unrecognized tool whose name says it deletes;
 * - `tampering` / `write-outside`: a write -- through a write tool, an `apply_patch` header, a shell write
 *   command or redirection (fix round 2, I5), or a script's output flag (P1) -- whose target is the own
 *   workflow's `golden/**` or `audit.jsonl`, or lies outside the own workflow in the pipeline's own trees or
 *   files or out of the repository (`writeTargetCategory`). A new scratch file at the run root (fix round 2,
 *   I6), and a write inside the own workflow but outside the role's lane, are ordinary acts.
 */
export function severeCategory(toolName: string, toolArgs: unknown, context: DenialContext = {}): SevereCategory | undefined {
  const tool = String(toolName ?? "").toLowerCase();
  const reason = context.reason ?? "";
  const id = context.wfId?.toLowerCase();
  const root = context.root;
  if (tool === SQL_TODO_TOOL) return undefined;
  if (SQL_TOOL.test(tool)) return "sql";
  const shell = SHELL_TOOL.test(tool);
  const shellSessionWrite = SHELL_SESSION_WRITE_TOOLS.includes(tool);
  if (shell && gamesShellKeys(toolArgs)) return "ambiguous-arguments";
  if (reason.startsWith(AMBIGUOUS_SEARCH_REASON)) return "ambiguous-arguments";
  if (reason.startsWith(UNRECOGNIZED_TOOL)) {
    if (NETWORK_TOOL_NAME.test(tool)) return "network";
    if (DESTRUCTIVE_TOOL_NAME.test(tool)) return "destructive";
  }
  const view = severityView(tool, toolArgs);
  const commands = shell ? shellTexts(view.args) : shellSessionWrite ? stringsIn(toolArgs) : [];
  const { paths, globs, tokens } = pathStringsOf(tool, view.args, commands);
  const every = [...paths, ...globs, ...tokens];
  if (reason.includes(HOME_REASON) || paths.some(startsAtHome) || [...globs, ...tokens].some((text) => HOME_TOKEN.test(text))) {
    return "outside-repository";
  }
  if (every.some(isUncPath)) return "network";
  if (commands.some((text) => namesAny(text, NETWORK_NAMES))) return "network";
  if (every.some(isCredentialPath)) return "credential";
  if (commands.some((text) => namesAny(text, CREDENTIAL_NAMES))) return "credential";
  if (reason.startsWith(OTHER_WORKFLOWS_REASON) || reason.startsWith(CROSS_WORKFLOW_REASON)) {
    const named = /^no access to other workflows \((.*)\)$/.exec(reason)?.[1];
    const benign = id !== undefined && named !== undefined && namesNoOtherWorkflow(named, id);
    // A reason found only in blanked text (a search's pattern, a write's content, a note) is no reach.
    if ((!view.changed && !benign) || id === undefined || reachesOtherWorkflow(tool, view.args, id, root)) return "other-workflow";
  }
  if (id !== undefined) {
    for (const [strings, kind] of [[paths, "path"], [globs, "glob"], [tokens, "token"]] as [string[], "path" | "glob" | "token"][]) {
      for (const text of strings) {
        const found = aliasReach(text, id, root, kind);
        if (found) return found;
      }
    }
  }
  // A shell command's `destructive command:` is judged on its own text below (`severeShellText`): that
  // finds every command the reason names except `format` as a mere word and a search's own literal pattern.
  if (!shell && reason.startsWith(DESTRUCTIVE_REASON)) return "destructive";
  if (reason.startsWith(SCRIPT_ROOT_REASON)) return "script-root";
  if (reason.startsWith(SCRIPT_BACKEND_REASON)) return "script-backend";
  if (reason.includes(EXTERNAL_LOCATION_REASON)) return "external-location";
  if (shell || shellSessionWrite) {
    for (const text of commands) {
      const found = severeShellText(text, id, root, shell ? tool : "powershell");
      if (found) return found;
    }
  }
  const fileWrite = WRITE_TOOL.test(tool) && tool !== AGENT_MESSAGE_TOOL && !shellSessionWrite;
  if (fileWrite && id !== undefined) {
    const targets = [...collectPathArgs(toolArgs).paths, ...(tool === "apply_patch" ? patchTargets(toolArgs) : [])];
    const found = targets.map((target) => writeTargetCategory(target, id, root)).filter((category) => category !== undefined);
    const order: SevereCategory[] = ["tampering", "other-workflow", "outside-repository", "write-outside"];
    for (const category of order) if (found.includes(category)) return category;
  }
  return undefined;
}

/**
 * The class of a refused call (Task L1, R3; Task L6, R2). `severe` when `severeCategory` names one;
 * otherwise `read` for a read-only tool (`READ_CLASS_TOOLS`), and for a shell command
 * `isReadOnlyShellCommand` (in the tool's own dialect) or `isReadOnlyGitListing` accepts; `act` for
 * everything else -- a write inside the own workflow, any other shell command (a script the role may not
 * run, an interpreter one-liner, a chained command), a tool nobody recognised, or arguments that cannot
 * be read. `context.reason` is the policy's own reason for the denial: since Task L6 the severe class
 * reads it, so `decide` always passes it.
 */
export function denialClass(toolName: string, toolArgs: unknown, context: DenialContext = {}): DenialClass {
  if (severeCategory(toolName, toolArgs, context) !== undefined) return "severe";
  const tool = String(toolName ?? "").toLowerCase();
  if (READ_CLASS_TOOLS.includes(tool)) return "read";
  if (SHELL_TOOL.test(tool)) {
    // Judged on exactly the command the tool runs; ambiguous arguments are an attempted action.
    const shell = shellCommandOf(toolArgs);
    // Fix round 2 (S2): a git listing with only its own flags is a read too.
    return shell.ok && (isReadOnlyShellCommand(shell.command, shellDialectOf(tool)) || isReadOnlyGitListing(shell.command))
      ? "read"
      : "act";
  }
  return "act";
}

// ---------- the SDK's spill files (Task L1, R2) ----------

/**
 * The SDK runtime's spill-file names (`@github/copilot-sdk-win32-x64`'s `runtime.node`,
 * `src/runtime/src/tools/large_output.rs`): `<ms>-copilot-tool-output-<pid>-<uuid>.txt`,
 * `copilot-tool-output[-original]-<…>.txt` and `original-output-<n>-<…>.txt` -- the runtime's own
 * recognizer is `(?:^|/)(?:\d+-copilot-tool-output-|copilot-tool-output(?:-original)?-|original-output-\d+-)`.
 * Matched against a BASE name; no separator, dot or space can appear in the variable part.
 */
export const SPILL_FILE_NAME =
  /^(?:\d+-copilot-tool-output-|copilot-tool-output(?:-original)?-|original-output-\d+-)[A-Za-z0-9_-]+\.txt$/;

/** The two tools a recorded spill file may be read with. */
export const SPILL_READ_TOOLS = ["view", "grep"];

/** Windows paths compare case-insensitively; a POSIX filesystem's do not. */
const CASE_INSENSITIVE_PATHS = process.platform === "win32";

/** A spill path as the policy compares it: separators unified and, where the filesystem is
 * case-insensitive, lower-cased -- nothing else (Task L1 fix round 1, M1). No whitespace is trimmed
 * (NTFS keeps a trailing no-break space in a name, so `<spill>.txt\u00a0` is another file) and no
 * `.`/`..` is resolved (a posix normalization would collapse `D:/../C:/…`), so a path matches a
 * recorded one only in its exact spelling. `hooks.ts` records only canonical paths as keys; `decide`
 * looks them up. */
export function spillFileKey(raw: string, caseInsensitive = CASE_INSENSITIVE_PATHS): string {
  const unified = String(raw ?? "").replace(/\\/g, "/");
  return caseInsensitive ? unified.toLowerCase() : unified;
}

/** The last component of a path written with either separator. */
export function spillBaseName(raw: string): string {
  return String(raw ?? "").replace(/\\/g, "/").split("/").pop() ?? "";
}

/** `view` of exactly one recorded spill file, or `grep` whose every path is one (R2). A recorded
 * key must also be absolute and carry the SDK's spill name, so nothing else can be read this way
 * even if the recorded set were wrong. */
function isRecordedSpillRead(toolName: string, toolArgs: unknown, spills?: ReadonlySet<string>): boolean {
  if (!spills || spills.size === 0) return false;
  const tool = toolName.toLowerCase();
  if (!SPILL_READ_TOOLS.includes(tool)) return false;
  const scan = collectPathArgs(toolArgs);
  if (scan.tooDeep || scan.paths.length === 0) return false;
  if (tool === "view" && scan.paths.length !== 1) return false;
  return scan.paths.every((raw) => {
    const key = spillFileKey(raw);
    // The name is judged in the key's own case: lower-cased where paths compare case-insensitively.
    return /^(?:[A-Za-z]:\/|\/)/.test(key) && SPILL_FILE_NAME.test(spillBaseName(key)) && spills.has(key);
  });
}

// ---------- broad reads (Task L1 fix round 1, P2) ----------

/**
 * The read tools that take paths or patterns. Rule 1 finds another workflow only by a literal
 * `workflows/<id>/` in the arguments, so a read that names no workflow at all -- the repository root,
 * `workflows` itself, `grep` with no path, `glob **` at the root -- could still read every workflow
 * in the run root. For these tools such a read is refused (a `read` denial for view/grep/glob).
 */
export const FILE_READ_TOOLS = ["view", "read", "read_file", "grep", "glob", "ls", "list_directory", "search", "search_files", "find"];

/** A folder or file name with no glob syntax: no `*`, `?`, `[`, `]`, `{`, `}`, `!`, `(`, … at all. */
const LITERAL_SEGMENT = /^[a-z0-9_.-]+$/;

function broadRead(what: string, id: string): string {
  return (
    `broad-read: ${what} could reach other workflows; name workflows/${id}/… or a folder outside ` +
    `workflows/ (cookbook/, docs/, mappings/, scripts/)`
  );
}

/** Whether a raw path argument names the repository root itself (`.`, `./`, or the root's absolute path). */
function coversRepositoryRoot(raw: string, root?: string): boolean {
  // Not trimmed (fix round 2, S3): a path with whitespace at an end is normalizeToolPath's to refuse.
  const text = String(raw ?? "").replace(/\\/g, "/");
  if (!text) return false;
  const normalized = path.posix.normalize(text).replace(/\/+$/, "");
  if (normalized === "." || normalized === "") return true;
  if (!root) return false;
  const normalizedRoot = path.posix.normalize(root.replace(/\\/g, "/")).replace(/\/+$/, "");
  return normalized.toLowerCase() === normalizedRoot.toLowerCase();
}

/** A normalized (root-relative, lower-cased) path under `workflows/` must name this workflow as its
 * second segment, literally: `workflows`, `workflows/`, `workflows/*` and `workflows/<other>` are refused. */
function workflowsReach(normalized: string, id: string): string | undefined {
  const [first, second] = normalized.replace(/\/+$/, "").split("/");
  if (first !== "workflows" || second === id) return undefined;
  if (second === undefined || second === "") return broadRead("a read of workflows/ itself", id);
  if (ID_PATTERN.test(second)) return `${OTHER_WORKFLOWS_REASON} (${second})`;
  return broadRead(`a read of workflows/${second}`, id);
}

/**
 * A glob pattern (the `glob` tool's `pattern`, or `grep`'s `glob`/`include` filter). Anywhere, it may
 * not be absolute or climb out with `..`. Evaluated at the repository root, its first segment must be
 * a literal folder or file name, and if that is `workflows` the second must be this workflow's id,
 * literally -- no wildcard, brace or character class.
 */
function globPatternReason(pattern: string, id: string, atRoot: boolean): string | undefined {
  // L6 fix round 1 (X2a): `workflows./*` is `workflows/*` to Windows.
  const alias = globAliasReason(pattern);
  if (alias) return alias;
  const text = pattern.trim().replace(/\\/g, "/");
  if (/^[A-Za-z]:/.test(text) || text.startsWith("/")) return broadRead(`an absolute glob pattern (${pattern})`, id);
  const segments = text.split("/").filter((segment) => segment !== "" && segment !== ".");
  if (segments.includes("..")) return broadRead(`a glob pattern that climbs out with .. (${pattern})`, id);
  if (!atRoot) return undefined;
  const [first, second] = segments.map((segment) => segment.toLowerCase());
  if (!first || !LITERAL_SEGMENT.test(first)) {
    return broadRead(`a glob pattern at the repository root that does not start with a literal folder (${pattern})`, id);
  }
  if (first === "workflows" && second !== id) {
    return broadRead(`a glob pattern that reaches workflows/ beyond ${id} (${pattern})`, id);
  }
  return undefined;
}

/** The SDK's search tools: their paths stand under exactly `paths` (a string or an array). */
export const SEARCH_PATH_TOOLS = ["grep", "glob"];
/** The reason prefix of a search call that carries a path key the runtime ignores (L6 fix round 1, X2b). */
export const AMBIGUOUS_SEARCH_REASON = "ambiguous search arguments:";

/** A grep or glob call's path-like keys other than `paths` (compared exactly: the runtime's key). */
function searchDecoyKeys(toolName: string, args: unknown): string[] {
  if (!SEARCH_PATH_TOOLS.includes(String(toolName ?? "").toLowerCase())) return [];
  if (!args || typeof args !== "object" || Array.isArray(args)) return [];
  // L6 fix round 2 (minor 7): only a string, or an array of strings, is a path at all (`include_files: true` is not)
  const pathLike = (value: unknown) => typeof value === "string" || (Array.isArray(value) && value.some((item) => typeof item === "string"));
  return Object.entries(args as Record<string, unknown>)
    .filter(([key, value]) => key !== "paths" && PATH_ARG_KEYS.test(key) && !CONTENT_ARG_KEYS.has(key.toLowerCase()) && pathLike(value))
    .map(([key]) => key);
}

/** The reason prefix of a grep/glob call whose lone path key is not `paths` (L6 fix round 2, I1): a read. */
export const SEARCH_PATH_KEY_REASON = "search-path-key:";

/** Every string under one of `keys` at the top level of the arguments, arrays included. */
function stringsUnder(args: unknown, keys: RegExp): string[] {
  if (!args || typeof args !== "object" || Array.isArray(args)) return [];
  const out: string[] = [];
  for (const [key, value] of Object.entries(args as Record<string, unknown>)) {
    if (!keys.test(key)) continue;
    for (const item of Array.isArray(value) ? value : [value]) if (typeof item === "string") out.push(item);
  }
  return out;
}

/** The P2 judgement a file-read tool gets after its path arguments passed rule 1: no path at all
 * means the repository root (only a `glob` whose pattern is confined may run there), and every glob
 * pattern or filter is judged by `globPatternReason`. */
function broadReadReason(tool: string, toolName: string, toolArgs: unknown, pathCount: number, id: string): string | undefined {
  const atRoot = pathCount === 0;
  if (tool === "glob") {
    const pattern = (toolArgs as { pattern?: unknown } | null)?.pattern;
    if (typeof pattern !== "string") return atRoot ? broadRead("a glob with no pattern at the repository root", id) : undefined;
    return globPatternReason(pattern, id, atRoot);
  }
  if (atRoot) return broadRead(`${toolName} with no path (the whole repository)`, id);
  if (tool === "grep") {
    // (L6 fix round 1: the SDK's repository-search variant names its filter `includePattern`.)
    for (const filter of stringsUnder(toolArgs, /^(glob|include|includepattern)$/i)) {
      const reason = globPatternReason(filter, id, atRoot);
      if (reason) return reason;
    }
  }
  return undefined;
}

// ---------- the decision ----------

const ALLOW: Verdict = { permissionDecision: "allow" };
const denied = (why: string): Verdict => ({ permissionDecision: "deny", permissionDecisionReason: why });

const OTHER_WORKFLOW = /workflows\/([a-z0-9_.-]+)\//g;

/** Everything the policy needs that is not the call itself. */
export interface PolicyOptions {
  /** Databases a three-part object reference may live in (`orchestrator.config.json`). */
  sandboxDatabases?: string[];
  /** The role acts on the workflow's dbt project (output_kind dbt), not on one segment. */
  dbtProject?: boolean;
  /** One call of a batched analyzer: its lanes narrow to this batch (see `writeLanes`). */
  analyzerBatch?: AnalyzerBatch;
  /**
   * Task L1 (R2): the SDK spill files THIS session's own tool results named, as `spillFileKey`
   * keys (`hooks.ts`'s `onPostToolUse` records them). `view` of exactly one of them, and `grep`
   * whose every path is one of them, are allowed for every role; nothing else is.
   */
  readableSpillFiles?: ReadonlySet<string>;
}

/**
 * The one permission decision. Paths are normalized and compared case-insensitively, the first
 * matching rule wins, and anything that cannot be judged is denied. A denial carries its
 * `denialClass` (Task L1, R3) -- what the refused call attempted, never whether it is refused.
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
  const verdict = judge(role, wfId, toolName, toolArgs, segment, root, options);
  if (verdict.permissionDecision === "allow") return verdict;
  const context = { reason: verdict.permissionDecisionReason, wfId, root };
  return { ...verdict, denialClass: denialClass(toolName, toolArgs, context) };
}

function judge(
  role: Role,
  wfId: string,
  toolName: string,
  toolArgs: unknown,
  segment?: string,
  root?: string,
  options?: PolicyOptions,
): Verdict {
  const id = wfId.toLowerCase();
  const databases = options?.sandboxDatabases ?? SANDBOX_DATABASES;

  // L6 fix round 2 (P3): the SDK's built-in `sql` tool is its per-session todo store, not a warehouse.
  if (String(toolName ?? "").toLowerCase() === SQL_TODO_TOOL) return denied(SQL_TODO_REASON);

  // 1a. Any mention of another workflow's folder, in any argument, whatever the tool.
  const rawArgs = JSON.stringify(toolArgs ?? {}).replace(/\\{1,2}/g, "/").toLowerCase();
  for (const match of rawArgs.matchAll(OTHER_WORKFLOW)) {
    if (match[1] !== id) return denied(`${OTHER_WORKFLOWS_REASON} (${match[1]})`);
  }

  // L6 fix round 1 (X2b): grep and glob take their paths under exactly `paths`. Any other path-like key
  // is one the runtime ignores -- it would count here as a confining path while the tool searched the
  // working directory -- so the call is ambiguous, like a shell call with two command keys (I1/P1).
  // Judged before the spill allowance: `grep {path: <spill>}` would otherwise search the run root.
  // L6 fix round 2 (I1): a LONE wrong key (no `paths` at all) is a confused call, not a gamed one -- a read,
  // told to use `paths`. A wrong key BESIDE `paths` stays ambiguous: two keys, one judged, one run.
  const decoys = searchDecoyKeys(toolName, toolArgs);
  if (decoys.length > 0) {
    if (!Object.hasOwn(toolArgs as object, "paths")) {
      return denied(
        `${SEARCH_PATH_KEY_REASON} ${toolName} takes its paths only under \`paths\` and ignores ${decoys.join(", ")}; use paths`,
      );
    }
    return denied(
      `${AMBIGUOUS_SEARCH_REASON} ${toolName} takes its paths only under \`paths\`, and the runtime ignores ` +
        `every other path key (found: ${decoys.join(", ")})`,
    );
  }

  // Task L1 (R2): a read of a spill file this session's own tool results named. Judged after 1a
  // (it cannot name another workflow either) and before 1b, which would refuse the path as
  // outside the repository -- it is, in the OS temp directory.
  if (isRecordedSpillRead(toolName, toolArgs, options?.readableSpillFiles)) return ALLOW;

  // 1b. Every path-like argument must normalize inside the repo and stay in this workflow. A file
  // read (Task L1 fix round 1, P2) may not cover the repository root or `workflows` itself either.
  const tool = toolName.toLowerCase();
  const fileRead = FILE_READ_TOOLS.includes(tool);
  const scan = collectPathArgs(toolArgs);
  if (scan.tooDeep) return denied("arguments too deeply nested to judge");
  const paths: string[] = [];
  for (const raw of scan.paths) {
    if (fileRead && coversRepositoryRoot(raw, root)) return denied(broadRead(`a ${toolName} of the repository root`, id));
    const checked = normalizeToolPath(raw, root);
    if (!checked.ok) return denied(checked.reason);
    const owner = workflowOf(checked.path);
    if (owner !== undefined && owner !== id) return denied(`${OTHER_WORKFLOWS_REASON} (${owner})`);
    if (fileRead) {
      const reach = workflowsReach(checked.path, id);
      if (reach) return denied(reach);
    }
    paths.push(checked.path);
  }
  if (fileRead) {
    // L6 fix round 1 (X2b): for grep and glob only what stands under `paths` confines the search.
    const confining = SEARCH_PATH_TOOLS.includes(tool) ? stringsUnder(toolArgs, /^paths$/).length : paths.length;
    const broad = broadReadReason(tool, toolName, toolArgs, confining, id);
    if (broad) return denied(broad);
  }

  if (toolName.toLowerCase() === AGENT_MESSAGE_TOOL) {
    return denied("write_agent (a message to a sub-agent) is not allowed; read a sub-agent's result with read_agent");
  }
  if (SQL_TOOL.test(toolName)) return decideSql(role, toolArgs, databases);
  if (SHELL_TOOL.test(toolName)) return decideShell(role, id, toolArgs, root, segment, options?.dbtProject ?? false);
  if (WRITE_TOOL.test(toolName)) {
    return decideWrite(role, id, segment, paths, options?.dbtProject ?? false, options?.analyzerBatch);
  }
  if (READ_TOOLS.includes(toolName.toLowerCase())) return ALLOW;
  if (SHELL_SESSION_READ_TOOLS.includes(toolName.toLowerCase())) return ALLOW;
  if (SHELL_SESSION_STOP_TOOLS.includes(toolName.toLowerCase())) return ALLOW;
  if (AGENT_READ_TOOLS.includes(toolName.toLowerCase())) return ALLOW;
  return denied(`${UNRECOGNIZED_TOOL}: ${toolName}`);
}

function decideWrite(
  role: Role,
  id: string,
  segment: string | undefined,
  paths: string[],
  dbtProject: boolean,
  analyzerBatch?: AnalyzerBatch,
): Verdict {
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

/** Every argument name that could carry a SQL statement, in any case. */
export const SQL_STATEMENT_KEYS = /^(sql|query|statement|text)$/i;

/**
 * The SDK's built-in `sql` tool (L6 fix round 2, P3): a per-session SQLite store whose description tells the
 * model to track its todos there. It is not the warehouse, so it is refused with a plain reason, as an
 * ordinary act. Every other SQL-classified tool keeps its judgement and severity. If the production
 * Snowflake tool turns out to be named exactly `sql`, this rule must change first
 * (docs/handoff-production.md §1.5).
 */
export const SQL_TODO_TOOL = "sql";
export const SQL_TODO_REASON = "the session's SQL todo store is not used here; keep your plan in your notes file";

/**
 * Live hardening, Task L9 (R1): the SDK's own built-in tools this policy refuses for EVERY role,
 * whatever the call, so `CopilotRunner` never offers them to begin with -- defence in depth, not a
 * new rule (`judge` below still refuses each of these outright if one reaches it anyway; see
 * POLICY.md). Live evidence (task-L9-brief.md): a validated translation's documenter session parked
 * because the SDK offered it `web_fetch` and the model called it, refused as `severe` (a network
 * attempt, `NETWORK_TOOL_NAME`) -- one denial that parks a session at once. `sql` and `write_agent`
 * are named exactly as `judge` already refuses them (`SQL_TODO_TOOL` above, `AGENT_MESSAGE_TOOL`);
 * `web_search` is the SDK's own name for its hosted web-search tool
 * (node_modules/@github/copilot-sdk/dist/generated/session-events.d.ts, `AssistantServerToolProgressData.kind`:
 * "Kind of hosted server tool that is running. Only `web_search` is emitted today"). This list is
 * provisional the same way `READ_TOOLS` is: it names every built-in this codebase has SEEN offered
 * or has independent evidence of; `sessionExcludedTools` below is how `orchestrator.config.json`'s
 * `session.excludedTools` extends it without a code change.
 */
export const ALWAYS_EXCLUDED_BUILTIN_TOOLS: readonly string[] = ["web_fetch", "web_search", SQL_TODO_TOOL, AGENT_MESSAGE_TOOL];

/**
 * `builtin:<name>` for every entry of `ALWAYS_EXCLUDED_BUILTIN_TOOLS`, then `configured` (an
 * operator's own `orchestrator.config.json` `session.excludedTools`) verbatim -- Task L9 (R1). What
 * `CopilotRunner.run` passes to the SDK's `SessionConfig.excludedTools`, which "always takes
 * precedence" over whatever else would have offered one of these tools
 * (node_modules/@github/copilot-sdk/dist/types.d.ts ~2015-2021). The constant list is prefixed with
 * its source (types.d.ts ~2004-2007: "source-qualified filter patterns (`builtin:*`, `builtin:<name>`,
 * …)") so it can only ever exclude the BUILT-IN tool of that exact name, never a future custom or MCP
 * tool that happens to share it; an operator's own entries are passed through exactly as written,
 * since they may need any of the SDK's own forms (a bare name, or one of its three prefixes). Pure:
 * the caller resolves `configured` from the loaded config, so this stays unit-testable without a
 * fake SDK client.
 */
export function sessionExcludedTools(configured: readonly string[] = []): string[] {
  return [...ALWAYS_EXCLUDED_BUILTIN_TOOLS.map((name) => `builtin:${name}`), ...configured];
}

/**
 * The statement a SQL call runs (Task L1 fix round 2, S1), the I1/P1 rule for SQL: exactly ONE
 * `SQL_STATEMENT_KEYS` key, in any spelling, holding a string. Two of them (`sql` and `query`, or
 * `sql` and `SQL`), or none, is ambiguous: which one the tool runs is not known, so judging one
 * would let the other through unjudged.
 */
export function sqlStatementOf(args: unknown): { ok: true; statement: string } | { ok: false; reason: string } {
  const entries = args && typeof args === "object" && !Array.isArray(args) ? Object.entries(args as Record<string, unknown>) : [];
  const statementLike = entries.filter(([key]) => SQL_STATEMENT_KEYS.test(key));
  if (statementLike.length === 1 && typeof statementLike[0][1] === "string") return { ok: true, statement: statementLike[0][1] };
  const found = statementLike.length > 0 ? statementLike.map(([key]) => key).join(", ") : "no statement key";
  return {
    ok: false,
    reason: `ambiguous SQL arguments: a SQL call must carry its statement as one string under exactly one of sql, query, statement, text (found: ${found})`,
  };
}

function decideSql(role: Role, args: unknown, databases: string[]): Verdict {
  if (role !== "validator" && role !== "intake") return denied(`${role} may not execute SQL`);
  const sql = sqlStatementOf(args);
  if (!sql.ok) return denied(sql.reason);
  const statement = sql.statement;

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
function checkListing(listing: ListingCommand, args: string[], id: string, root?: string): Verdict {
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
function checkListingArgument(token: string, id: string, root?: string, isPath = false): Verdict | undefined {
  if (!LISTING_ARG_TOKEN.test(token) || hasDotDot(token)) {
    return denied(`listing argument not allowed: ${token.slice(0, 60)}`);
  }
  // L6 fix round 1 (X2a): a name Windows would rewrite (`workflows.`, `aux.md`) is judged even without a
  // separator -- `Get-ChildItem -Recurse workflows.` lists every workflow.
  const alias = windowsAliasReason(token);
  if (alias) return denied(alias);
  if (!isPath && !/[/\\]/.test(token)) return undefined;
  const checked = normalizeToolPath(token, root);
  if (!checked.ok) return denied(checked.reason);
  const owner = workflowOf(checked.path);
  if (owner !== undefined && owner !== id) return denied(`${OTHER_WORKFLOWS_REASON} (${owner})`);
  // Fix round 2 (S2): `workflows/<other id>` with no trailing separator names the other folder too.
  const [first, second] = checked.path.replace(/\/+$/, "").split("/");
  if (first === "workflows" && second !== undefined && second !== id && ID_PATTERN.test(second)) {
    return denied(`${OTHER_WORKFLOWS_REASON} (${second})`);
  }
  return undefined;
}

/**
 * Task L1 fix round 2 (S2): a listing that walks the tree (`recursiveFlags`, `alwaysRecursive`) is
 * a broad read unless every path it names is inside this workflow or outside `workflows/`. With no
 * path it runs at the repository root -- and for git, a path is only what follows `--` -- so it
 * would list every workflow in the run root. A listing that does not recurse is left alone: of the
 * root or of `workflows/` it shows the names one level down only. Runs after `checkListing` has
 * accepted every flag and argument.
 */
function recursiveListingReason(listing: ListingCommand, args: string[], id: string): string | undefined {
  let recursive = listing.alwaysRecursive === true;
  const paths: string[] = [];
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
      if (listing.recursiveFlags?.includes(flag)) recursive = true;
      if (listing.numericFlags?.includes(flag) || listing.valueFlags?.includes(flag)) {
        if (equals < 0) i += 1; // the next token is this flag's value, not a path
        continue;
      }
      if (equals > 0 && (flag === "-path" || flag === "-literalpath")) paths.push(token.slice(equals + 1));
      continue;
    }
    if (listing.pathsAfterDoubleDash && !afterDoubleDash) continue; // a revision, not a path
    paths.push(token);
  }
  if (!recursive) return undefined;
  const name = listing.words.join(" ");
  if (paths.length === 0) {
    return broadRead(
      listing.pathsAfterDoubleDash ? `${name} with no path after -- (the whole repository)` : `a recursive ${name} with no path (the whole repository)`,
      id,
    );
  }
  for (const raw of paths) {
    const normalized = path.posix.normalize(raw.replace(/\\/g, "/")).replace(/\/+$/, "").toLowerCase();
    if (normalized === "." || normalized === "") return broadRead(`a recursive ${name} of the repository root`, id);
    const reach = workflowsReach(normalized, id);
    if (reach) return reach;
  }
  return undefined;
}

/** Whether every flag of a listing's arguments is on its allow-list (numeric flags with digits). */
function listingFlagsAllowed(listing: ListingCommand, args: string[]): boolean {
  for (let i = 0; i < args.length; i++) {
    const token = args[i];
    if (token === "--") return true;
    if (!token.startsWith("-")) continue;
    if (listing.bareNumeric && /^-\d+$/.test(token)) continue;
    const equals = token.indexOf("=");
    const flag = (equals > 0 ? token.slice(0, equals) : token).toLowerCase();
    if (!listing.flags.includes(flag)) return false;
    if (listing.numericFlags?.includes(flag)) {
      const value = equals > 0 ? token.slice(equals + 1) : args[++i];
      if (value === undefined || !/^\d+$/.test(value)) return false;
    }
  }
  return true;
}

/**
 * Task L1 fix round 2 (S2): a refused git listing -- one `git status`/`git diff`/`git log`
 * invocation with only its own allowed flags -- could only have read, so its class is `read`. A git
 * call refused for a flag (`git diff --output=…` writes a file) or any other git subcommand stays
 * `act`.
 */
export function isReadOnlyGitListing(command: unknown): boolean {
  if (typeof command !== "string" || SHELL_METACHARACTERS.test(command) || UNUSUAL_WHITESPACE.test(command)) return false;
  const tokens = command.trim().split(/\s+/).filter(Boolean);
  if (tokens.some((token) => INTERPRETER_FLAGS.includes(token.toLowerCase()))) return false;
  const head = tokens.map(normalizeToken);
  const listing = READ_ONLY_SHELL.find((shape) => shape.words[0] === "git" && shape.words.every((word, i) => head[i] === word));
  return listing !== undefined && listingFlagsAllowed(listing, tokens.slice(listing.words.length));
}

/** Whitespace in a shell command other than blanks and tabs (fix round 2, S3): a no-break or
 * zero-width space splits tokens differently in this policy and in the shell that runs it. */
export const UNUSUAL_WHITESPACE = /[^\S \t]|[\u180e\u200b-\u200d\u2060]/u;

function decideShell(role: Role, id: string, args: unknown, root?: string, segment?: string, dbtProject = false): Verdict {
  // Task L1 fix round 1 (I1/P1): exactly the key the tool runs, or the call is ambiguous.
  const shell = shellCommandOf(args);
  if (!shell.ok) return denied(shell.reason);
  if (SHELL_METACHARACTERS.test(shell.command)) return denied(`shell metacharacter in command: ${shell.command.trim().slice(0, 80)}`);
  // Fix round 2 (S3): judged before any trim, which would drop a trailing no-break space.
  if (UNUSUAL_WHITESPACE.test(shell.command)) {
    return denied(`command holds whitespace other than blanks and tabs: ${JSON.stringify(shell.command.slice(0, 80))}`);
  }
  const command = shell.command.trim();
  if (!command) return denied(`${role} sent no command this policy can judge`);
  if (DESTRUCTIVE_SHELL.some((pattern) => pattern.test(command))) {
    return denied(`${DESTRUCTIVE_REASON} ${command.slice(0, 80)}`);
  }

  const tokens = command.split(/\s+/).filter(Boolean);
  const flagged = tokens.find((token) => INTERPRETER_FLAGS.includes(token.toLowerCase()));
  if (flagged) return denied(`interpreter flag ${flagged} is never allowed`);

  const refuse = denied(`${role} may not run this command: ${command.slice(0, 80)}`);
  const head = tokens.map(normalizeToken);

  // Listing commands, for every role — but only with their own flags (see ListingCommand).
  const listing = READ_ONLY_SHELL.find((shape) => shape.words.every((word, i) => head[i] === word));
  if (listing) {
    const listingArgs = tokens.slice(listing.words.length);
    const verdict = checkListing(listing, listingArgs, id, root);
    if (verdict.permissionDecision === "deny") return verdict;
    const broad = recursiveListingReason(listing, listingArgs, id);
    return broad ? denied(broad) : ALLOW;
  }
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
    // L6 fix round 2 (I2): the target is a path, and Windows reads `tests/parser_corpus/x.` as `…/x`
    const alias = windowsAliasReason(tokens[3] ?? "");
    if (alias) return denied(alias);
    return ALLOW;
  }

  // `<python> scripts/<allowed script>.py <args…>`
  const script = head[1] ?? "";
  if (!(ROLE_SCRIPTS[role] ?? []).includes(script)) return refuse;
  // Task D fix round 2: an agent never needs --root (its session's working directory IS the
  // workflow root), and a script given one reads and writes under another tree. Judged before any
  // other argument rule, so every spelling gets this reason.
  if (tokens.slice(2).some((token) => SCRIPT_ROOT_FLAG.test(token))) {
    return denied(`${SCRIPT_ROOT_REASON} ${script} may not be given --root from an agent session`);
  }
  // Follow-up to Task W2: an agent never points a script at a Snowflake account (Task P2's flags).
  if (tokens.slice(2).some(isScriptBackendFlag)) {
    return denied(`${SCRIPT_BACKEND_REASON} ${script} may not be pointed at a Snowflake account from an agent session`);
  }
  for (const token of tokens.slice(2)) {
    // L6 fix round 2 (I2): every script argument, and every `--x=value` value, is a possible path, and a
    // name Windows rewrites (`workflows./wf_0002/…`) would reach what rule 1 never saw.
    // (A `--x=value` token is judged by its value: the flag's own `=` is no path.)
    const alias = windowsAliasReason(token.startsWith("-") && token.includes("=") ? token.slice(token.indexOf("=") + 1) : token);
    if (alias) return denied(alias);
    if (FLAG_TOKEN.test(token)) continue;
    // L4 fix round 1 (M2): `--set=<name>`, the spelling argparse also accepts; its value is judged as
    // any plain argument is.
    const setValue = SET_EQUALS.exec(token)?.[1];
    if (setValue !== undefined && PLAIN_TOKEN.test(setValue) && !hasDotDot(setValue)) continue;
    if (!PLAIN_TOKEN.test(token) || hasDotDot(token)) {
      return denied(`${role} may not pass this argument: ${token.slice(0, 60)}`);
    }
  }
  // G2: the first bare token is the workflow the script will act on. A flag's value placed before
  // it is not skipped (the documented form is `<script> <wf_id> …`), which can only deny more.
  if (WORKFLOW_ID_SCRIPTS.includes(script)) {
    const first = tokens.slice(2).find((token) => !FLAG_TOKEN.test(token) && !SET_EQUALS.test(token));
    if (first !== undefined && first.toLowerCase() !== id) {
      return denied(`${CROSS_WORKFLOW_REASON} ${script} ${first.slice(0, 60)} in a ${id} session`);
    }
  }
  // L6 fix round 2 (P1): what a script's path flags read and write (`compare.py --out`, `--db`, …).
  const pathFlags = scriptPathVerdict(role, id, script, tokens.slice(2), root, segment, dbtProject);
  if (pathFlags) return pathFlags;
  // Task L4 (R2): a translator or fixer validating its own work -- its own segment, or its dbt project.
  return selfValidationVerdict(role, script, tokens.slice(2), segment, dbtProject, args) ?? ALLOW;
}

/**
 * Every flag of the scripts in `ROLE_SCRIPTS` that takes a path, and what the script does with it (L6 fix
 * round 2, P1; from each script's `add_argument` calls), with every other flag each script has -- argparse
 * accepts any unambiguous prefix, so a flag is resolved against the script's whole list.
 */
export const SCRIPT_FLAGS: Record<string, Record<string, "read" | "write" | "other">> = {
  "scripts/compare.py": {
    "--expected": "read", "--actual": "other", "--contract": "read", "--out": "write", "--stream": "other",
    "--tolerances": "read", "--manifest": "read", "--dag": "read", "--db": "write", "--golden-set": "other",
    "--sample-rows": "other", "--help": "other",
  },
  "scripts/validate_segment.py": { "--set": "other", "--proc": "read", "--help": "other" },
  "scripts/validate_snowpark.py": { "--set": "other", "--proc": "read", "--help": "other" },
  "scripts/validate_dbt.py": { "--set": "other", "--project": "read", "--help": "other" },
  "scripts/intake_touchpoints.py": { "--yxdb-dir": "read", "--help": "other" },
};
/** The reason prefix of a script path flag refused (L6 fix round 2, P1). */
export const SCRIPT_PATH_REASON = "script-path:";

/** A script's flag as argparse resolves it: the exact name, or the one flag the prefix fits. */
function resolveScriptFlag(script: string, token: string): { flag: string; kind: "read" | "write" | "other"; inline?: string } | undefined {
  const flags = SCRIPT_FLAGS[script];
  if (!flags || !token.startsWith("--")) return undefined;
  const equals = token.indexOf("=");
  const name = (equals > 0 ? token.slice(0, equals) : token).toLowerCase();
  const fits = name in flags ? [name] : Object.keys(flags).filter((flag) => flag.startsWith(name));
  if (fits.length !== 1) return undefined;
  return { flag: fits[0], kind: flags[fits[0]], inline: equals > 0 ? token.slice(equals + 1) : undefined };
}

/** The path flags of a script call, with their values, in order. */
export function scriptPathFlags(script: string, args: string[]): { flag: string; kind: "read" | "write"; value: string }[] {
  const found: { flag: string; kind: "read" | "write"; value: string }[] = [];
  for (let i = 0; i < args.length; i++) {
    const resolved = resolveScriptFlag(script, args[i]);
    if (!resolved) continue;
    const value = resolved.inline ?? args[i + 1];
    if (resolved.inline === undefined) i += 1;
    if (resolved.kind !== "other" && value !== undefined) found.push({ flag: resolved.flag, kind: resolved.kind, value });
  }
  return found;
}

/**
 * L6 fix round 2 (P1). A script's path flags stay in bounds: what it READS (`--expected`, `--contract`,
 * `--proc`, `--project`, `--yxdb-dir`, …) must lie inside the repository and not in another workflow (rule
 * 1); what it WRITES (`compare.py --out`, `--db`) must lie inside the own workflow AND in the role's write
 * lane, like any write -- `compare.py --out orchestrator/policy.ts` used to be allowed. The orchestrator's
 * own invocations do not pass through this policy and are unaffected.
 */
function scriptPathVerdict(
  role: Role,
  id: string,
  script: string,
  args: string[],
  root: string | undefined,
  segment: string | undefined,
  dbtProject: boolean,
): Verdict | undefined {
  for (const { flag, kind, value } of scriptPathFlags(script, args)) {
    const checked = normalizeToolPath(value, root);
    if (!checked.ok) return denied(`${SCRIPT_PATH_REASON} ${script} ${flag} ${value.slice(0, 60)}: ${checked.reason}`);
    const [first, second] = checked.path.split("/");
    if (first === "workflows" && second !== undefined && second !== id) {
      return denied(`${OTHER_WORKFLOWS_REASON} (${second})`);
    }
    if (kind === "read") continue;
    const inside = checked.path.startsWith(`workflows/${id}/`);
    const forbidden = FORBIDDEN_WRITES.find(([pattern]) => pattern.test(checked.path));
    const lanes = writeLanes(role, id, segment, dbtProject) ?? [];
    if (!inside || forbidden || !lanes.some((lane) => lane.test(checked.path))) {
      return denied(`${SCRIPT_PATH_REASON} ${script} ${flag} ${value.slice(0, 60)} must be inside workflows/${id}/ and ${role}'s write lane`);
    }
  }
  return undefined;
}
