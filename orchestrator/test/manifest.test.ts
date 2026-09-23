// F12 (final review): a manifest.json that EXISTS but does not parse must be a hard, per-workflow
// error -- not treated like a missing one (which silently returns emptyManifest and lets an
// already-escalated, already-spent workflow look brand new and re-run every stage and agent from
// parse). Also: saveManifest writes atomically, and reloadManifest's metrics merge must not erase
// an unsaved-but-fresher toolCalls count with a stale disk copy.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  CorruptManifestError,
  loadManifest,
  manifestPath,
  reloadManifest,
  saveManifest,
  writeJson,
} from "../manifest.ts";
import type { Manifest } from "../types.ts";

async function tmp(t: any): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), "orch-manifest-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

test("loadManifest returns emptyManifest for a workflow that has never run (ENOENT is fine)", async (t) => {
  const root = await tmp(t);
  const m = await loadManifest(root, "wf_0001");
  assert.deepEqual(m, { id: "wf_0001", status: {}, metrics: {} });
});

test("loadManifest throws CorruptManifestError (naming the file) for invalid JSON, and overwrites nothing", async (t) => {
  const root = await tmp(t);
  const file = manifestPath(root, "wf_0001");
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, "{ this is not valid json", "utf8");
  const before = await readFile(file, "utf8");

  await assert.rejects(loadManifest(root, "wf_0001"), (error: unknown) => {
    assert.ok(error instanceof CorruptManifestError);
    assert.match((error as Error).message, /wf_0001/);
    assert.match((error as Error).message, /manifest\.json/);
    return true;
  });

  const after = await readFile(file, "utf8");
  assert.equal(after, before, "a corrupt manifest is never overwritten by loadManifest itself");
});

test("loadManifest throws for JSON that parses but is not an object (an array, a string, a number)", async (t) => {
  const root = await tmp(t);
  for (const text of ['[1, 2, 3]', '"just a string"', "42"]) {
    const wf = `wf_${text.length}`;
    await mkdir(path.dirname(manifestPath(root, wf)), { recursive: true });
    await writeFile(manifestPath(root, wf), text, "utf8");
    await assert.rejects(loadManifest(root, wf), CorruptManifestError, text);
  }
});

test("a corrupt manifest for one workflow does not prevent loadManifest from working for another", async (t) => {
  const root = await tmp(t);
  await mkdir(path.dirname(manifestPath(root, "wf_bad")), { recursive: true });
  await writeFile(manifestPath(root, "wf_bad"), "not json at all", "utf8");
  await writeJson(manifestPath(root, "wf_good"), { id: "wf_good", status: { parse: "PARSED" }, metrics: {} });

  await assert.rejects(loadManifest(root, "wf_bad"), CorruptManifestError);
  const good = await loadManifest(root, "wf_good");
  assert.equal(good.status.parse, "PARSED");
});

test("saveManifest writes atomically: no leftover temp file, and the final content is the last write", async (t) => {
  const root = await tmp(t);
  const manifest: Manifest = { id: "wf_0001", status: { parse: "PARSED" }, metrics: {} };
  await saveManifest(root, manifest);

  const dir = path.dirname(manifestPath(root, "wf_0001"));
  const entries = await readdir(dir);
  assert.deepEqual(entries, ["manifest.json"], "no .tmp file left behind");

  const onDisk = JSON.parse(await readFile(manifestPath(root, "wf_0001"), "utf8"));
  assert.equal(onDisk.status.parse, "PARSED");

  // A second save (simulating a later stage) replaces the file cleanly -- this is the Windows
  // rename-over-an-existing-file path writeJson's retry loop exists for.
  manifest.status.intake = "READY";
  await saveManifest(root, manifest);
  const entriesAfter = await readdir(dir);
  assert.deepEqual(entriesAfter, ["manifest.json"]);
  const onDiskAfter = JSON.parse(await readFile(manifestPath(root, "wf_0001"), "utf8"));
  assert.equal(onDiskAfter.status.intake, "READY");
});

test("reloadManifest: disk wins per key, same as before, for everything except metrics.<role>.toolCalls", async (t) => {
  const root = await tmp(t);
  await writeJson(manifestPath(root, "wf_0001"), {
    id: "wf_0001",
    status: { intake: "READY" },
    metrics: {},
    golden_sets: ["normal"],
  });
  const manifest: Manifest = { id: "wf_0001", status: { parse: "PARSED" }, metrics: {} };
  await reloadManifest(root, manifest);
  assert.deepEqual(manifest.status, { parse: "PARSED", intake: "READY" }, "disk's intake merges with the in-memory parse");
  assert.deepEqual(manifest.golden_sets, ["normal"]);
});

// --- the metrics-merge race the reviewer flagged: does disk-wins ever erase an unsaved, fresher
// in-memory toolCalls count? mergeMetrics (F12) takes the max per role instead of blindly
// preferring disk, so this can no longer happen even though today's call graph never actually
// reaches this shape (every reloadManifest call site in stages.ts either runs before that role's
// agent has executed, or after a SUCCESSFUL call whose onSessionEnd already saved the same
// numbers) -- this test exercises reloadManifest directly, in isolation, as the hazard the
// reviewer described: an in-memory count ahead of a stale disk copy for the SAME role. ----------

test("reloadManifest never erases a higher in-memory toolCalls count with a stale disk copy for the same role", async (t) => {
  const root = await tmp(t);
  // Disk has an EARLIER snapshot: 5 tool calls recorded before a crash.
  await writeJson(manifestPath(root, "wf_0001"), {
    id: "wf_0001",
    status: {},
    metrics: { intake: { toolCalls: 5, lastMs: 100 } },
  });
  // In memory, a crashed session's finally block (hooks.ts's recordMetrics) has already added its
  // own spend on top -- 5 (already on disk) + 29 (this crashed session) = 34 -- but nothing has
  // saved that yet.
  const manifest: Manifest = { id: "wf_0001", status: {}, metrics: { intake: { toolCalls: 34, lastMs: 2000 } } };
  await reloadManifest(root, manifest);
  assert.equal(manifest.metrics.intake.toolCalls, 34, "the higher, in-memory count survives the reload");
});

test("reloadManifest still takes disk's toolCalls when disk is AHEAD (a concurrent writer recorded more)", async (t) => {
  const root = await tmp(t);
  await writeJson(manifestPath(root, "wf_0001"), {
    id: "wf_0001",
    status: {},
    metrics: { intake: { toolCalls: 50, lastMs: 100 } },
  });
  const manifest: Manifest = { id: "wf_0001", status: {}, metrics: { intake: { toolCalls: 10, lastMs: 2000 } } };
  await reloadManifest(root, manifest);
  assert.equal(manifest.metrics.intake.toolCalls, 50, "the higher count wins whichever side it's on");
});

test("reloadManifest still lets disk introduce a role the in-memory manifest never had", async (t) => {
  const root = await tmp(t);
  await writeJson(manifestPath(root, "wf_0001"), {
    id: "wf_0001",
    status: {},
    metrics: { analyzer: { toolCalls: 3, lastMs: 500 } },
  });
  const manifest: Manifest = { id: "wf_0001", status: {}, metrics: {} };
  await reloadManifest(root, manifest);
  assert.equal(manifest.metrics.analyzer.toolCalls, 3);
});

// --- Task W4 fix round 1: the reviewer reproduced the same disk-wins hazard for `compactions` and
// `peakInputTokens` -- both were added to `recordMetrics` alongside `toolCalls` (cumulative,
// respectively a running maximum) but `mergeMetrics` only ever protected `toolCalls`, so a mid-stage
// reload could LOWER either field back to a stale disk snapshot. Numbers below match the reviewer's
// own repro (compactions 3->1, peakInputTokens 9000->2000) exactly. -----------------------------

test("reloadManifest never erases a higher in-memory compactions count with a stale disk copy for the same role", async (t) => {
  const root = await tmp(t);
  // Disk has an EARLIER snapshot: one compaction, saved before a later session recorded more.
  await writeJson(manifestPath(root, "wf_0001"), {
    id: "wf_0001",
    status: {},
    metrics: { analyzer: { toolCalls: 5, compactions: 1, peakInputTokens: 2000 } },
  });
  // In memory, a later (unsaved) session has already seen two MORE compactions on top.
  const manifest: Manifest = {
    id: "wf_0001",
    status: {},
    metrics: { analyzer: { toolCalls: 5, compactions: 3, peakInputTokens: 2000 } },
  };
  await reloadManifest(root, manifest);
  assert.equal(manifest.metrics.analyzer.compactions, 3, "the higher, in-memory compactions count survives the reload");
});

test("reloadManifest never erases a higher in-memory peakInputTokens with a stale disk copy for the same role", async (t) => {
  const root = await tmp(t);
  // Disk has an EARLIER snapshot: a 2000-token peak, saved before a later session saw more.
  await writeJson(manifestPath(root, "wf_0001"), {
    id: "wf_0001",
    status: {},
    metrics: { analyzer: { toolCalls: 5, compactions: 1, peakInputTokens: 2000 } },
  });
  // In memory, a later (unsaved) session has already recorded a 9000-token peak.
  const manifest: Manifest = {
    id: "wf_0001",
    status: {},
    metrics: { analyzer: { toolCalls: 5, compactions: 1, peakInputTokens: 9000 } },
  };
  await reloadManifest(root, manifest);
  assert.equal(manifest.metrics.analyzer.peakInputTokens, 9000, "the higher, in-memory peak survives the reload");
});

test("reloadManifest still takes disk's compactions/peakInputTokens when disk is AHEAD (a concurrent writer recorded more)", async (t) => {
  const root = await tmp(t);
  await writeJson(manifestPath(root, "wf_0001"), {
    id: "wf_0001",
    status: {},
    metrics: { analyzer: { compactions: 4, peakInputTokens: 12000 } },
  });
  const manifest: Manifest = { id: "wf_0001", status: {}, metrics: { analyzer: { compactions: 1, peakInputTokens: 500 } } };
  await reloadManifest(root, manifest);
  assert.equal(manifest.metrics.analyzer.compactions, 4, "the higher count wins whichever side it's on");
  assert.equal(manifest.metrics.analyzer.peakInputTokens, 12000);
});
