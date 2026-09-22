// workflows/<id>/ paths, and the manifest read/merge/write cycle.
// Python scripts write manifest.json too, so the orchestrator re-reads it after every
// `py` call and merges rather than overwriting what a script just recorded.
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import path from "node:path";
import type { Manifest } from "./types.ts";

export function wfDir(root: string, id: string, ...rest: string[]): string {
  return path.join(root, "workflows", id, ...rest);
}

export function manifestPath(root: string, id: string): string {
  return wfDir(root, id, "manifest.json");
}

export function emptyManifest(id: string): Manifest {
  return { id, status: {}, metrics: {} };
}

export async function fileExists(file: string): Promise<boolean> {
  try {
    await readFile(file);
    return true;
  } catch {
    return false;
  }
}

export async function readJson<T>(file: string): Promise<T> {
  return JSON.parse(await readFile(file, "utf8")) as T;
}

export async function readJsonOr<T>(file: string, fallback: T): Promise<T> {
  try {
    return await readJson<T>(file);
  } catch {
    return fallback;
  }
}

const RENAME_ATTEMPTS = 5;

/** `fs.rename` replaces an existing destination atomically on POSIX; on Windows the same call can
 * transiently fail (EPERM/EBUSY/EACCES) when the destination is briefly locked -- antivirus
 * scanning a freshly-written temp file is the common cause -- so a short retry loop absorbs that
 * without giving up the atomicity the temp-file-then-rename pattern exists for (F12). */
async function renameOver(tmp: string, dest: string): Promise<void> {
  for (let attempt = 1; ; attempt++) {
    try {
      await rename(tmp, dest);
      return;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (attempt >= RENAME_ATTEMPTS || (code !== "EPERM" && code !== "EBUSY" && code !== "EACCES")) {
        await rm(tmp, { force: true }).catch(() => undefined);
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 20 * attempt));
    }
  }
}

/**
 * JSON as the rest of the pipeline writes it: UTF-8, two-space indent, trailing newline --
 * written ATOMICALLY (F12): the text lands in a temp file in the same directory first, and only
 * a rename (never a truncate-in-place) makes it visible at `file`. A reader can therefore never
 * observe a half-written file, and a crash mid-write leaves the previous good version (or
 * nothing) at `file`, never a corrupt partial one.
 */
export async function writeJson(file: string, value: unknown): Promise<void> {
  const dir = path.dirname(file);
  await mkdir(dir, { recursive: true });
  const tmp = path.join(dir, `.${path.basename(file)}.${randomUUID()}.tmp`);
  await writeFile(tmp, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await renameOver(tmp, file);
}

/** Thrown by `loadManifest` when `manifest.json` exists but cannot be trusted: unreadable JSON,
 * or JSON that parses to something other than an object (F12). Distinct from a MISSING manifest
 * (perfectly normal for a workflow nobody has run yet, and `loadManifest` returns
 * `emptyManifest` for that) -- a manifest that exists but is corrupt must never be silently
 * treated the same way, because `emptyManifest` would make an already-escalated, already-spent
 * workflow look brand new and re-run every stage and agent from `parse`. */
export class CorruptManifestError extends Error {
  readonly file: string;
  constructor(file: string, cause: unknown) {
    const detail = cause instanceof Error ? cause.message : String(cause);
    super(`manifest.json is corrupt and cannot be trusted: ${file} (${detail})`);
    this.name = "CorruptManifestError";
    this.file = file;
  }
}

export async function loadManifest(root: string, id: string): Promise<Manifest> {
  const file = manifestPath(root, id);
  let text: string;
  try {
    text = await readFile(file, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return emptyManifest(id);
    // Some other read failure (permissions, a locked file, ...) is just as untrustworthy as bad
    // JSON: this workflow's real state cannot be known, so it must not be treated as "never run".
    throw new CorruptManifestError(file, error);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new CorruptManifestError(file, error);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new CorruptManifestError(
      file,
      `expected a JSON object, got ${Array.isArray(parsed) ? "an array" : typeof parsed}`,
    );
  }
  const onDisk = parsed as Partial<Manifest>;
  const manifest = emptyManifest(id);
  Object.assign(manifest, onDisk, { id: onDisk.id ?? id });
  manifest.status ??= {};
  manifest.metrics ??= {};
  return manifest;
}

export async function saveManifest(root: string, manifest: Manifest): Promise<void> {
  manifest.updated_at = new Date().toISOString();
  await writeJson(manifestPath(root, manifest.id), manifest);
}

/** Disk wins per role for every field EXCEPT `toolCalls` (F12): `toolCalls` is monotonically
 * additive (hooks.ts's `recordMetrics` only ever adds to it, never replaces it), so between an
 * in-memory count and a disk count for the SAME role, the higher one is always the more complete
 * record -- taking disk unconditionally could silently erase a session's spend that was recorded
 * only in memory (CopilotRunner.run's crash-path `finally` block records metrics without itself
 * saving, relying on a LATER saveManifest -- see hooks.ts's `recordMetrics` doc comment) if a
 * reload raced ahead of that save. `lastMs` and everything else about a role still simply take
 * disk's value, same as before. */
function mergeMetrics(
  inMemory: Record<string, unknown> | undefined,
  onDisk: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...(inMemory ?? {}), ...(onDisk ?? {}) };
  const roles = new Set([...Object.keys(inMemory ?? {}), ...Object.keys(onDisk ?? {})]);
  for (const role of roles) {
    const a = (inMemory?.[role] as { toolCalls?: unknown } | undefined)?.toolCalls;
    const b = (onDisk?.[role] as { toolCalls?: unknown } | undefined)?.toolCalls;
    if (typeof a !== "number" && typeof b !== "number") continue;
    merged[role] = {
      ...(merged[role] as object),
      toolCalls: Math.max(typeof a === "number" ? a : 0, typeof b === "number" ? b : 0),
    };
  }
  return merged;
}

/**
 * Merge what is on disk into the in-memory manifest, in place, disk winning per key:
 * a script that just wrote `status.intake` or `golden_sets` must not be overwritten,
 * while statuses only this process knows about are kept. `metrics` is the one exception,
 * merged per-role by `mergeMetrics` (F12) rather than a blind disk-wins.
 *
 * Unlike `loadManifest`, an unreadable or corrupt file here is NOT a hard error: this is called
 * mid-stage, right after a Python script's own write, purely to pick up what that script just
 * recorded (`status.intake`, `golden_sets`, ...), and the in-memory manifest this process already
 * has is a perfectly good manifest to keep going with if the read fails for any reason. Only the
 * INITIAL load in `migrateWorkflow` (`loadManifest`) is where a corrupt file must stop the
 * workflow instead of silently being read as "never run" -- see `CorruptManifestError`.
 */
export async function reloadManifest(root: string, manifest: Manifest): Promise<Manifest> {
  const onDisk = await readJsonOr<Partial<Manifest> | null>(manifestPath(root, manifest.id), null);
  if (!onDisk) return manifest;
  const status = { ...manifest.status, ...(onDisk.status ?? {}) };
  const metrics = mergeMetrics(manifest.metrics, onDisk.metrics);
  const segmentStatus = { ...(manifest.segment_status ?? {}), ...(onDisk.segment_status ?? {}) };
  Object.assign(manifest, onDisk, { id: manifest.id, status, metrics });
  if (Object.keys(segmentStatus).length > 0) manifest.segment_status = segmentStatus;
  return manifest;
}
