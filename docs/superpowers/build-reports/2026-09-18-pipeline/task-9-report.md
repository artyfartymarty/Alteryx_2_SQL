# Task 9 report: `scripts/dev/build_samples.py`

Status: DONE

## What was implemented

`scripts/dev/build_samples.py` (new) with:

- `seed(repo, samples_dir, wf_id) -> dict` -- copies `samples/<wf_id>/source/**` to
  `workflows/<wf_id>/source/`, `golden_inputs/<set>/` to `golden/inputs/<set>/`,
  `golden_inputs/targets_before/<set>/` to `golden/targets_before/<set>/`; parses the copied
  source in memory (`parse.parse_file`, not `parse.run` -- nothing is persisted by this step) to
  find every `input` node whose `config["format"] == "yxdb"`, and writes
  `source/data/<basename>.yxdb` from that tool's `normal` golden CSV via `lib.yxdb.write_yxdb`.
  The basename is taken by splitting the Windows source path on both `\` and `/`. Writes
  `manifest.json` via `lib.io.load_manifest`/`save_manifest`, which is what makes "never overwrite
  an existing manifest's `status`/`metrics`/`answers`/`accepted_diffs`" work for free: the existing
  manifest (if any) is loaded whole, only `id`/`source`/`segmentation` are set unconditionally, and
  `status`/`metrics` are defaulted with `setdefault` (so `{}` only the first time).
- `build(repo, samples_dir, wf_id) -> dict` -- `seed` then `parse.run(..., check=True)` then
  `segment.run` then `dev.alteryx_sim.run`. Returns `{"parse": <parse report>, "segments": [seg
  ids, sorted], "golden_sets": [...]}`. `INVARIANT_VIOLATION` (the `wf_0005` design trigger) short
  circuits after reporting it in the returned dict, skipping segmentation and simulation entirely,
  `golden_sets: []`, no exception. `FAILED`/`QUARANTINED` parse status, an unparsable sample
  source, a missing sample fixture, or a segmentation whose group graph has a cycle each raise
  `BuildError` (a new exception distinct from bare `Exception`, used only for "a reason the script
  checks for").
- `_logical_by_tool(repo, wf_id, sample)` -- deliberately **does not** call
  `alteryx_sim.read_logical_by_tool` for its sample.json fallback, because that function hardcodes
  `repo.root / "samples"`, which is wrong whenever `--samples` points somewhere other than
  `<root>/samples` -- true in every test here, since tests use `tmp_path` as `--root` while
  reading the real fixtures from the worktree's `samples/`. It reuses
  `alteryx_sim.read_logical_by_tool` only for the `intake/mappings.yaml` branch (which is
  `--root`-relative and therefore fine), and otherwise reads `logical` straight from the `sample`
  dict `build` already has in hand.
- CLI: `build_samples.py {seed,build} [--only wf_id] [--samples samples] [--root .]`. Without
  `--only`, every `samples/wf_*` directory is processed (`_tools` is skipped: it doesn't match
  `wf_*`). Exit codes, per the rules file's CLI EXIT CODES addendum:
  - **2** (usage, nothing created): `--samples` directory doesn't exist, or `--only` names a
    workflow id not found under it -- both checked with `parser.error` *before* the seed/build
    loop starts.
  - **1** (domain failure, one-line message to stderr): any `BuildError` from `seed`/`build` for
    the workflow(s) being processed; the loop keeps going for the remaining workflows (so a `build`
    of "everything" still reports every failure), and `main` returns 1 if any occurred.
  - **2** (unexpected): any other exception -- `traceback.print_exc()`, no partial success text.
  - **0**: every processed workflow built without a `BuildError` (an `INVARIANT_VIOLATION` is not
    one, by design).

## Brief corrections

None. The five Step-1 tests are implemented as literally as their prose allows (the brief gives
prose, not exact assertions, for task 9 unlike task 8). No test in the brief needed changing.

One thing worth flagging as a **mismatch between the brief and the actual code**, per the
dispatch's instruction to check `alteryx_sim`'s real behaviour: the brief says `alteryx_sim.run`
"raises/handles `alteryx_sim.UnsupportedTool`". Reading `scripts/dev/alteryx_sim.py::run`, it never
raises `UnsupportedTool` to its caller -- it catches it internally (`except UnsupportedTool as
error: print(...); results = []`) and returns `[]`. `build_samples.py` does not catch
`UnsupportedTool` anywhere because there is nothing to catch; `wf_0005`'s `golden_sets == []`
outcome for `build` actually comes from the earlier `INVARIANT_VIOLATION` short-circuit in
`build()` itself (parse never gets far enough to hand a dag with the unknown tool to the
simulator), not from `alteryx_sim.run` swallowing `UnsupportedTool`. Both paths land on the same
observable behaviour for `wf_0005`, so no code change was needed, but the report calls this out per
the dispatch's "report any mismatch" instruction.

## What was tested and results

`tests/test_build_samples.py`, 22 tests:

- Brief Step 1 (a)-(e), one test each:
  - (a) `seed` on `wf_0001`: `source/data/orders.yxdb` header has 7 fields, `num_records` equals
    the normal CSV's row count, manifest `source.engine == "AMP"`.
  - (b) `build` on `wf_0003`: `result["segments"] == ["seg_01", "seg_02"]`,
    `golden/intermediates/seg_01/normal/3_Output.csv` and `golden/outputs/edge/10.csv` exist.
  - (c) empty set: `golden/outputs/empty/{7,8}.csv` for `wf_0001` have zero rows and a non-empty
    field list.
  - (d) `build` on `wf_0005`: `golden_sets == []`, `parse.status == "INVARIANT_VIOLATION"`,
    `segments == []`, no exception, and `segments/` was never created.
  - (e) `build` run twice on `wf_0001`: every file under `golden/` and `source/data/` is byte
    identical between the two runs (manifest.json is deliberately excluded from this comparison --
    it carries `updated_at`, which is expected to change).
- Prose-described behaviour not covered by (a)-(e):
  - `seed` copies `golden/targets_before/<set>/<LOGICAL>.csv` (`wf_0003`/`GL_SUMMARY`).
  - `seed` never overwrites an existing manifest's `status`/`metrics`/`answers`/`accepted_diffs`,
    while still refreshing `source`/`segmentation`.
  - `seed` defaults `status`/`metrics` to `{}` when no manifest exists yet.
  - `build` raises `BuildError` (not a bare exception) when `parse.run` reports a non-clean,
    non-`INVARIANT_VIOLATION` status (`FAILED`, via `monkeypatch`), and writes no `segments/`.
  - `build` on each of `wf_0001`/`wf_0002`/`wf_0003`/`wf_0004` (parametrized) produces a clean
    parse, at least one segment and at least one golden set -- smoke coverage for `wf_0002`'s
    second (csv, not yxdb) input plus its db-target output, and `wf_0004`'s macro, neither of which
    the brief's five tests touch.
  - CLI exit codes, one test per code plus the extra scenarios the rules file calls out:
    `build --only wf_0001` exits 0 and writes the full tree; `seed --only wf_0001` exits 0 and
    stops before `parsed/dag.json`; an unknown `--only` id and a missing `--samples` directory both
    exit 2 via `SystemExit` and create nothing; no arguments exits 2; a hand-built unparsable
    sample fixture exits 1 with the workflow id on stderr; a monkeypatched crash in `build` exits 2;
    running `build` with no `--only` processes all five real samples and exits 0.
  - `_samples_dir` resolves a relative `--samples` against `--root` and leaves an absolute one
    alone.

Commands run:

```
.venv/Scripts/python.exe -m pytest tests/test_build_samples.py -q   # 22 passed
.venv/Scripts/python.exe -m pytest -q                                # 446 passed, full suite, pristine
```

Both runs are clean: no warnings, no skips, no stray stdout captured by pytest (the underlying
`alteryx_sim`/`segment`/`parse` modules' own informational `print`s do appear on the console during
a `build`, same as running `alteryx_sim.py` directly -- pre-existing behaviour, not something this
task's script introduces or should suppress).

Also ran the CLI by hand (not just via pytest) against a scratch directory outside the repo:

```
python scripts/dev/build_samples.py build --samples "<worktree>/samples" --root /tmp/manual_build
```

Result: all five samples processed, exit 0, printed one summary line per workflow (`wf_0005`'s
being `parse INVARIANT_VIOLATION, 0 segments, golden sets (none)`), and the resulting
`workflows/wf_0001/manifest.json` matches `docs/spec/02-schemas-reference.md`'s `manifest.json`
shape field-for-field (`id`, `status`, `metrics`, `source.{file,alteryx_version,engine,
server_schedule,owner,consumers}`, `segmentation`, `updated_at`, `segments`, `golden_sets`). Scratch
directory was deleted after inspection; nothing was written under the worktree's own `workflows/`.

## Files changed

- `scripts/dev/build_samples.py` (new)
- `tests/test_build_samples.py` (new)

## Self-review findings

- Reused `lib.io.load_manifest`/`save_manifest` instead of hand-rolling the "preserve existing
  keys" logic -- `load_manifest` already returns the on-disk manifest verbatim (or the
  `{"id", "status": {}, "metrics": {}}` default), so `seed` only ever *adds* `id`/`source`/
  `segmentation` and defaults the two required-empty keys; nothing bespoke to get wrong.
  Cross-checked against the coordinator's added instruction ("never clobber status, metrics,
  answers or accepted_diffs") with a dedicated test that sets all four on an existing manifest and
  reseeds.
- Caught and fixed a real directory-mismatch bug during design, before it ever hit a test failure:
  `alteryx_sim.read_logical_by_tool`'s `sample.json` fallback is `repo.root`-relative, which is
  wrong once `--samples` differs from `<root>/samples` (the case in every test here, and for any
  real invocation with a non-default `--samples`). Fixed by reading `logical` from the `sample`
  dict `build` already has, only delegating to `alteryx_sim.read_logical_by_tool` for the
  `intake/mappings.yaml` branch where the `--root`-relative path is actually correct.
  See "Brief corrections" above for the one documented mismatch between the brief's prose and
  `alteryx_sim`'s actual (already-correct) behaviour around `UnsupportedTool`; no code change
  followed from it.
- CLI EXIT CODES: verified against `scripts/parse.py`'s pattern per the rules file -- `parser.error`
  before any side effect for usage errors, `BuildError` -> one-line stderr message -> 1 for domain
  failures, bare `except Exception` -> `traceback.print_exc()` -> 2 for anything else, and every
  code has a dedicated test (including the "no arguments" and "unexpected exception via
  monkeypatch" cases the rules file calls out by name for other scripts).
- Considered whether `build`'s `FAILED`/`QUARANTINED`-status branch is reachable given `seed`
  already wraps its own in-memory parse in a try/except that raises `BuildError` on any parse
  failure -- in practice it is very hard to reach for real sample fixtures (the two calls parse the
  same file the same way), but kept it as defensive code matching the plan's Global Constraints
  ("domain failure = FAIL verdict...") and covered it directly with a `monkeypatch`-based unit test
  rather than skip it.
- No YAGNI concerns: didn't add `--min-tools`/`--max-tools` overrides (brief doesn't ask for them;
  `segment.run` already reads `manifest["segmentation"]`, which `seed` populates from
  `sample.json`).

## Issues or concerns

None outstanding. wt/task-9 is otherwise clean (only the two new files, per `git status`).

## Fix round 1

Working directory changed: `wt/task-9` was merged and the worktree removed, so this round's work
was done directly in the main checkout, `/c/Users/<user>/Desktop/Alteryx to Snowflake` (branch
`feat/pipeline-m0-m2`), against the merged `scripts/dev/build_samples.py` and
`tests/test_build_samples.py`.

### The finding

Reviewer (Important): `_copy_tree` (`shutil.copytree(..., dirs_exist_ok=True)`) overlays new files
onto an existing destination but never removes one that's in the destination but no longer in the
(possibly changed) source. Re-running `seed`/`build` against an existing `workflows/<wf_id>/` tree
after a sample fixture shrinks or is restructured -- a source file removed, a golden CSV deleted, a
tool renumbered, a whole golden set folder dropped, an input's yxdb basename changed, or a
segmentation change renaming segments -- silently leaves the old files behind in `source/`,
`golden/inputs/<set>/`, `golden/targets_before/`, the generated `source/data/*.yxdb` files, and
(for `build`) `golden/intermediates/`/`golden/outputs/`. None of the original 22 tests exercised a
changed-fixture reseed.

### What changed

`scripts/dev/build_samples.py`:

- Added `_reset_dir(repo, wf_id, *parts) -> Path`: deletes `repo.wf(wf_id, *parts)` if present,
  after verifying the resolved target is inside `repo.wf(wf_id)` (`resolved == wf_root or wf_root
  in resolved.parents`, both sides `.resolve()`d) -- raising `BuildError` instead of ever deleting
  outside the workflow's own folder, whatever `--root`/`--samples`/a symlink says. Returns the
  (now-absent) path so the caller's next line repopulates it.
- `seed` now calls `_reset_dir` for `source/`, `golden/inputs/` and `golden/targets_before/` before
  repopulating each, in place of overlaying onto whatever `dirs_exist_ok=True` left there. Deleting
  `source/` wholesale also removes `source/data/`, so a yxdb input whose basename or path changed
  between reseeds no longer leaves the old `.yxdb` behind -- no separate handling was needed for
  that case once `source/` itself is owned outright.
- `build` now calls `_reset_dir` for `golden/intermediates/` and `golden/outputs/` immediately
  after `seed` and *before* `parse.run`, unconditionally -- including on the path that short
  circuits on `INVARIANT_VIOLATION`. That ordering is what makes "a workflow whose build now
  short-circuits must not keep derived files from an earlier, successful run" true: the two
  directories are gone before the parse status is even known, so the short-circuit return leaves
  them gone rather than needing its own separate cleanup call.
- Ruling followed exactly on scope: `manifest.json`'s own fields other than the ones `seed` already
  sets, `parsed/`, `intake/`, `segments/`, and anything else this script doesn't own are never
  touched by `_reset_dir` -- `seed`/`build` simply never call it on those paths.

`tests/test_build_samples.py`: added a `_copy_sample(tmp_path, wf_id)` helper (a private, mutable
copy of a real fixture under `tmp_path`, so edits never touch `samples/`) and a new "Fix round 1"
section with 10 tests:

- `test_reseed_removes_a_deleted_golden_input_csv` -- delete `wf_0002`'s `golden_inputs/edge/3.csv`
  (+ schema) from the copy; reseed; `golden/inputs/edge/3.csv` is gone, `golden/inputs/edge/1.csv`
  (an untouched sibling) survives.
- `test_reseed_removes_a_dropped_golden_set_folder` -- `rmtree` the whole `golden_inputs/edge/`
  folder from the copy; reseed; `golden/inputs/edge/` is gone entirely, `golden/inputs/normal/`
  survives.
- `test_reseed_removes_a_renamed_source_file` -- rename `wf_0001`'s `sales_summary.yxmd` to
  `renamed.yxmd` in the copy; reseed; the old name is gone, the new one is present, and
  `manifest["source"]["file"] == "renamed.yxmd"`.
- `test_reseed_removes_a_yxdb_whose_input_basename_changed` -- rewrite the `<File>` path text in
  the copy's yxmd from `orders.yxdb` to `orders_v2.yxdb`; reseed; `source/data/orders.yxdb` is
  gone, `source/data/orders_v2.yxdb` exists.
- `test_rebuild_removes_a_stale_segment_after_a_segmentation_change` -- first build `wf_0003` with
  `segmentation = {min_tools: 1, max_tools: 3}` (verified by hand to produce three segments, with
  `seg_02` = tools 4-9 actually holding outbound intermediate files, e.g.
  `golden/intermediates/seg_02/normal/9_Output.csv` -- unlike the *default* segmentation's
  `seg_02`, which is the workflow's terminal segment and never gets an intermediates folder at
  all); change `segmentation` to `{min_tools: 40, max_tools: 40}` (collapses to one segment) and
  rebuild; `golden/intermediates/seg_02` and `seg_03` are both gone.
- `test_build_wf_0005_leaves_no_stale_intermediates_or_outputs_from_an_earlier_state` -- plant a
  fake stale file directly under `golden/intermediates/seg_01/normal/1_Output.csv` and
  `golden/outputs/normal/4.csv` after seeding `wf_0005` (rather than contriving a fixture that
  builds cleanly once and later trips the invariant, which would be testing two things at once);
  `build` (which always hits `INVARIANT_VIOLATION` for this fixture) leaves neither directory
  behind.
- `test_reseed_leaves_files_it_does_not_own_untouched` -- after seeding `wf_0001`, hand-write a
  manifest `answers` entry, an `intake/mappings.yaml`, and a `segments/seg_01/proc.sql`; reseed;
  all three are byte-for-byte unchanged (covers the ruling's "never touched" list).
- `test_reset_dir_refuses_to_delete_outside_the_workflow_folder` -- calls
  `_reset_dir(repo, "wf_0001", "..", "..", "escaped")` directly and asserts it raises `BuildError`
  matching `"outside"`, and that nothing was actually deleted.
- `test_reset_dir_is_a_no_op_when_the_target_does_not_exist` -- calling it on a path that was never
  created returns the path without raising.
- Existing `test_build_twice_is_byte_identical` was kept as-is and re-verified to still pass (the
  reset-then-repopulate cycle is itself deterministic for an unchanged fixture).

### TDD evidence

RED -- ran the 8 new "reseed/rebuild" and `_reset_dir` tests against the *unfixed* code (before
adding `_reset_dir` or wiring it in):

```
.venv/Scripts/python.exe -m pytest tests/test_build_samples.py \
  -k "reseed_removes or rebuild_removes or leaves_no_stale or reset_dir or leaves_files_it_does_not_own" -q
```

```
FAILED tests/test_build_samples.py::test_reseed_removes_a_deleted_golden_input_csv
FAILED tests/test_build_samples.py::test_reseed_removes_a_dropped_golden_set_folder
FAILED tests/test_build_samples.py::test_reseed_removes_a_renamed_source_file
FAILED tests/test_build_samples.py::test_reseed_removes_a_yxdb_whose_input_basename_changed
FAILED tests/test_build_samples.py::test_rebuild_removes_a_stale_segment_after_a_segmentation_change
FAILED tests/test_build_samples.py::test_build_wf_0005_leaves_no_stale_intermediates_or_outputs_from_an_earlier_state
FAILED tests/test_build_samples.py::test_reset_dir_refuses_to_delete_outside_the_workflow_folder
FAILED tests/test_build_samples.py::test_reset_dir_is_a_no_op_when_the_target_does_not_exist
8 failed, 1 passed, 22 deselected in 0.85s
```

(The one pass, `test_reseed_leaves_files_it_does_not_own_untouched`, was expected to pass even
before the fix -- the old code never touched those paths either; it's there to guard against a
regression the fix could plausibly introduce, e.g. an over-broad reset.) The first four failures
are genuine assertion failures (stale file/dir still present); the `_reset_dir` ones are
`AttributeError: module 'dev.build_samples' has no attribute '_reset_dir'`, confirming the helper
didn't exist yet.

Also had to fix my own first draft of `test_rebuild_removes_a_stale_segment_after_a_segmentation_change`
mid-round: my first version assumed the *default* segmentation's `seg_02` would have intermediate
files, matched against `test_build_wf_0003_yields_two_segments_and_golden_files`'s own
`golden/intermediates/seg_01/...` (never `seg_02`) -- checked by hand with `segment.segment(...)`
directly, confirmed `seg_02` under the default segmentation is the workflow's terminal segment (no
outbound edges, no intermediates folder at all), and rewrote the test to use a segmentation that
demonstrably produces a non-terminal `seg_02` first.

GREEN -- full test file, then the whole suite, after the fix:

```
.venv/Scripts/python.exe -m pytest tests/test_build_samples.py -v
```
```
collected 31 items
tests\test_build_samples.py ...............................            [100%]
31 passed in 3.07s
```

```
.venv/Scripts/python.exe -m pytest -q
```
```
........................................................................ [ 14%]
........................................................................ [ 28%]
........................................................................ [ 43%]
........................................................................ [ 57%]
........................................................................ [ 72%]
........................................................................ [ 86%]
...................................................................      [100%]
PYTEST_EXIT=0
```
499 tests collected across the suite (up from 446 at the original report, from other tasks' work
landing on `feat/pipeline-m0-m2` since then), all passing, no warnings, no skips.

### Files changed (this round)

- `scripts/dev/build_samples.py` -- added `_reset_dir`; wired it into `seed` (`source/`,
  `golden/inputs/`, `golden/targets_before/`) and `build` (`golden/intermediates/`,
  `golden/outputs/`).
- `tests/test_build_samples.py` -- added `_copy_sample` helper and 10 new tests (9 shown above
  plus the byte-identical determinism re-check, which was already present and needed no change).

### Self-review (this round)

- Double-checked the containment guard fires on the resolved path, not the literal one: `repo.wf`
  never itself normalizes `".."` segments (`Path.joinpath` doesn't), so the guard has to resolve
  both sides before comparing -- confirmed via the dedicated test, which passes literal `".."`
  segments into `repo.wf` the same way a hostile or mistaken `--samples`/symlink combination could
  produce a path that only escapes after resolution.
- Verified the fix doesn't change what a *successful, unchanged-fixture* rebuild produces:
  `test_build_twice_is_byte_identical` still passes, and the other 20 pre-existing tests are
  unmodified and still pass.
- Deliberately did not reset `golden/inputs`/`golden/targets_before` and then re-derive them from
  `_seed_yxdb_inputs`'s own pass -- `_seed_yxdb_inputs` only ever *writes* `source/data/*.yxdb`; it
  was never the thing leaving golden CSVs stale, `_copy_tree`'s overlay semantics were, so the fix
  is scoped to the copy call sites, not to yxdb generation.
- Confirmed by hand (three ad hoc `python -c` invocations, not committed) which of wf_0003's
  segments actually get outbound intermediate files under several `min_tools`/`max_tools`
  combinations, rather than guessing -- this is what caught my first draft's wrong assumption
  before it became a silently-weak test.
- No changes to the CLI, `_logical_by_tool`, or any of the exit-code paths from the original round;
  the reviewer confirmed those, and the ruling explicitly scoped this round to the one finding.

## Fix round 2

Working directory changed again: this round's dispatch put the work in a fresh worktree,
`.worktrees/task-9-fix2` (branch `wt/task-9-fix2`), because another agent was now writing in the
main checkout. Confirmed at the start that `import lib` resolved inside the worktree and that the
worktree's `HEAD` (`402d759`) already contained fix round 1's commit (`3f945b2`).

### The finding (CRITICAL)

Re-reviewer, verbatim (summarized): `wf_id` path traversal defeats `_reset_dir`'s containment
guard. `repo.wf(wf_id)` (`root.joinpath("workflows", wf_id, *parts)`) never validated `wf_id`, so
`wf_id=".."` made `wf_root = repo.wf("..").resolve()` collapse to `repo.root` itself -- moving the
guard's own trusted anchor out from under it. `_reset_dir(repo, "..", "source")` then deleted
`<root>/source`, entirely outside `<root>/workflows/`, with no `BuildError`. Reachable through the
public entry point: with a `sample.json` + `source/` placed at `<root>` (i.e. `samples_dir/..`),
`seed(repo, samples_dir, "..")` reached `_reset_dir`, deleted `<root>/source`, and only then raised
an uncaught `FileNotFoundError` from `shutil.copytree` trying to copy from the directory it had
just destroyed -- deletion happened before the sample was validated at all. The only guard test
from round 1 exercised a `parts`-based escape, never a `wf_id`-based one. Dormant via the CLI
today (its `--only` allowlist restricts ids to discovered `wf_*` names), but `seed`/`build` are
public functions other tasks will call directly.

Two Minors from the same review were ruled into this round as safety properties of the same
helper: (M1) `_reset_dir`'s `resolved == wf_root` arm would let it delete the *entire* workflow
folder if ever called with zero `parts`; (M2) `golden/inputs/` and `golden/targets_before/` were
both reset up front, before the single copy loop that repopulates both, so a copy failure partway
through could leave both incomplete.

### What changed

**`scripts/lib/paths.py`** (shared infrastructure -- protects every script that builds a path
through `Repo`, not only `build_samples.py`):

- Added `_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")` and `_validate_id(value, label)`,
  which `Repo.wf`, `Repo.seg`, `wf_token` and `seg_token` all now call on every `wf_id`/`seg`
  argument, raising `ValueError` naming the offending value if it doesn't match. This rejects
  `".."`, `"."`, empty, anything containing `/` or `\`, a drive letter, and a name with a leading
  or trailing dot/space/other non-`[A-Za-z0-9_-]` character -- and accepts ordinary ids like
  `"wf_0001"`, `"seg_01"`, `"WF-12_a"`.
- Added `_validate_part(value)`, applied to every `*parts` element of `Repo.wf`/`Repo.seg`, which
  rejects only an **absolute** part (a leading `/` or `\`, or a drive letter -- checked explicitly
  because `Path("/abs").is_absolute()` is `False` under `WindowsPath`, confirmed by hand before
  writing the check). An ordinary relative segment like `"golden"`, `"inputs"` or
  `".sandbox.duckdb"` (used by `load_golden.py`) stays legal, exactly as the ruling scoped it --
  these are always script-authored literals, never external input.
- Ran the full suite with this alone in place (before touching `build_samples.py` at all): **0
  failures**. No existing test or script relied on an id this now rejects, so the rule did not
  need loosening.

**`scripts/dev/build_samples.py`** -- three independent layers, per the ruling:

- `_reset_dir` now refuses zero `parts` (`BuildError`, "no parts given") and requires `parts[0]`
  to be one of `_OWNED_TOP_LEVEL = {"source", "golden"}` (and, under `"golden"`, `parts[1]` to be
  one of `_OWNED_GOLDEN_SUBDIRS = {"inputs", "targets_before", "intermediates", "outputs"}`) --
  independently of the resolve-and-contain check, so a future caller can never point it at
  `intake/`, `segments/`, or the workflow folder itself even if containment alone would have
  allowed it. The resolve-and-contain check itself was tightened from `resolved == wf_root or
  wf_root in resolved.parents` to just `wf_root in resolved.parents` (M1: `resolved.parents` never
  includes `resolved` itself, so equality is no longer a legal outcome -- the target must now be
  *strictly* inside `wf_root`) and kept as defence in depth against a symlink, per the ruling,
  even though `Repo.wf` can no longer move `wf_root` via the id.
- `seed` was restructured to validate the whole fixture before deleting anything (M2's ordering
  fix, folded into the same pass since both are about `seed`'s atomicity): three new helpers,
  `_parse_sample_source` (parses the *original* fixture's workflow file, not yet copied anywhere,
  translating any parse failure to `BuildError`), `_validate_golden_csvs` (reads every golden-input
  CSV `seed` will copy via `typed_csv.read_table` up front, returning the validated set
  directories so the later copy loop doesn't rescan), and `_plan_yxdb_inputs` (resolves every
  yxdb-format input to its `(basename, normal_csv)` pair, checking the CSV exists, without writing
  anything yet -- this replaces the old `_seed_yxdb_inputs`, which both validated *and* wrote in
  the same pass, after the destination had already been reset). Only once `sample.json` has
  parsed, `source/` has a workflow file that itself parses, every golden CSV is readable, and
  every yxdb input's CSV exists does `seed` call `_reset_dir` at all -- and each owned directory
  is now reset immediately before the loop that repopulates it (reset `golden/inputs` → copy
  inputs; reset `golden/targets_before` → copy targets), addressing M2 directly: a failure at that
  point (already-validated content notwithstanding) can leave at most one directory incomplete,
  never two.
- `_read_sample` now catches `json.JSONDecodeError` (a `ValueError` subclass) around `read_json`
  and re-raises as `BuildError` naming the file -- previously a malformed `sample.json` crashed
  `seed`/`build` with an unhandled exception instead of the domain failure the ruling requires.

**`tests/test_foundations.py`**: added `BAD_IDS`/`GOOD_IDS` parametrized tests covering every id
ruling 1 lists (`..`, `.`, `""`, `a/b`, `a\b`, `C:\x`, `/abs`, `wf_0001/../wf_0002`, a trailing dot,
a trailing space, a leading space, an embedded space → `ValueError`; `wf_0001`, `seg_01`, `WF-12_a`
→ accepted) against `Repo.wf`, `Repo.seg` (both the `wf_id` and `seg` position), `wf_token` and
`seg_token`, plus one test confirming ordinary relative parts stay legal while absolute ones
(`/abs`, `\abs`, `C:\evil`, `C:/evil`) are rejected on both `Repo.wf` and `Repo.seg`.

**`tests/test_build_samples.py`**: updated the two round-1 `_reset_dir` tests to match the new
allowlist (the "outside the workflow folder" test now uses `parts=("source", "..", "..",
"escaped")` -- `"source"` passes the new allowlist, so the trailing `".."`s are what the
resolve-and-contain check has to catch; matches `"not strictly inside"` rather than `"outside"`)
and added 6 new tests: `_reset_dir` with zero parts and with an unowned directory
(`"intake"`/`"segments"`/`"golden", "capture_map.json"`) both raising `BuildError`; the live
reproduction of the finding (`seed(repo, samples_dir, "..")` against a decoy that is a full,
otherwise-valid copy of `wf_0001` -- built to prove the id itself is rejected, not merely that an
incomplete decoy tripped some other check first -- raises, and the decoy `<root>/source` survives
byte-for-byte); and two "leaves prior state intact" tests (a malformed `sample.json`, and a yxdb
input whose `normal` CSV was deleted from the fixture) that seed once successfully, break the
fixture, reseed and confirm both the raise and that the first seed's `source/`/`golden/inputs/`
are untouched.

### TDD evidence

RED (paths.py) -- wrote the `test_foundations.py` additions, then temporarily reverted
`scripts/lib/paths.py` to `HEAD` (`git show HEAD:scripts/lib/paths.py > scripts/lib/paths.py`) and
ran just the new tests:

```
.venv/Scripts/python.exe -m pytest tests/test_foundations.py \
  -k "repo_wf_rejects or repo_seg_rejects or wf_token_and_seg_token_reject or accept_ordinary_relative" -v
```
```
37 failed, 28 deselected in 0.50s
```
(All 37 failures were `Failed: DID NOT RAISE ValueError` -- the pre-fix `Repo`/`wf_token`/
`seg_token` silently accepted every bad id.) Restored the fixed `paths.py` and reran:
```
.venv/Scripts/python.exe -m pytest tests/test_foundations.py -v
```
```
collected 65 items
65 passed in 0.37s
```

RED (build_samples.py) -- wrote the six new/updated `_reset_dir`/`seed` tests, then reverted
*both* `scripts/lib/paths.py` and `scripts/dev/build_samples.py` to `HEAD` and ran them:

```
.venv/Scripts/python.exe -m pytest tests/test_build_samples.py \
  -k "reset_dir or wf_id_path_traversal or reseed_leaves_prior_state" -v
```
```
FAILED tests/test_build_samples.py::test_reset_dir_refuses_to_delete_outside_the_workflow_folder
FAILED tests/test_build_samples.py::test_reset_dir_refuses_zero_parts
FAILED tests/test_build_samples.py::test_reset_dir_refuses_a_directory_it_does_not_own
FAILED tests/test_build_samples.py::test_wf_id_path_traversal_is_rejected_before_any_deletion
FAILED tests/test_build_samples.py::test_reseed_leaves_prior_state_intact_when_sample_json_is_malformed
FAILED tests/test_build_samples.py::test_reseed_leaves_prior_state_intact_when_a_yxdb_input_csv_is_missing
6 failed, 1 passed, 29 deselected in 0.55s
```
Notably, `test_wf_id_path_traversal_is_rejected_before_any_deletion` failed against the pre-fix
code with an *unhandled* `FileNotFoundError` from `shutil.copytree` --
`WinError 3: ... root\samples\..\source` -- raised from inside `seed`'s own `_copy_tree` call,
proving the exploit is real in this checkout too: `_reset_dir` had already deleted the decoy
`<root>/source`, and the very next line then tried to copy *from* the directory it had just
destroyed. This is the live reproduction the re-reviewer described. Restored both fixed files and
reran:

```
.venv/Scripts/python.exe -m pytest tests/test_build_samples.py -v
```
```
collected 36 items
36 passed in 3.08s
```

GREEN -- the three commands the dispatch asked for, run once each after both files were restored
to their fixed state:

```
.venv/Scripts/python.exe -m pytest tests/test_foundations.py tests/test_build_samples.py -v
```
```
collected 101 items
101 passed in 3.06s
```

```
.venv/Scripts/python.exe -m pytest -q
```
```
........................................................................ [ 12%]
........................................................................ [ 24%]
........................................................................ [ 36%]
........................................................................ [ 49%]
........................................................................ [ 61%]
........................................................................ [ 73%]
........................................................................ [ 86%]
........................................................................ [ 98%]
.........                                                                [100%]
FULL_SUITE_EXIT=0
```
585 tests collected across the suite (up from 499 at the end of fix round 1, from other tasks'
work landing on `feat/pipeline-m0-m2` since then -- e.g. `tests/test_agents_config.py` and
`tests/test_compare.py` are new since that snapshot), all passing, no warnings, no skips.

### Files changed (this round)

- `scripts/lib/paths.py` -- `_validate_id`/`_validate_part`, applied in `Repo.wf`, `Repo.seg`,
  `wf_token`, `seg_token`.
- `scripts/dev/build_samples.py` -- `_reset_dir`'s owned-top-level allowlist and zero-parts
  refusal, `_read_sample` catching malformed JSON, and `seed` restructured into
  validate-then-delete-then-repopulate via three new helpers (`_parse_sample_source`,
  `_validate_golden_csvs`, `_plan_yxdb_inputs`) replacing `_seed_yxdb_inputs`.
- `tests/test_foundations.py` -- id-validation coverage for `Repo.wf`/`Repo.seg`/`wf_token`/
  `seg_token`.
- `tests/test_build_samples.py` -- updated 2 round-1 `_reset_dir` tests for the new allowlist,
  added 6 round-2 tests.

### Self-review (this round)

- Traced the exact call path of the live exploit by hand before writing the reproduction test:
  confirmed `_read_sample`, `parse.workflow_file`, `_parse_sample_source`, `_validate_golden_csvs`
  and `_plan_yxdb_inputs` in the *new* `seed` all operate on plain `pathlib.Path` arithmetic off
  `samples_dir`/`sample_dir`, never through `Repo`, so none of them would have caught
  `wf_id=".."` on their own -- the id validation only bites at the *first* `repo.wf(...)` call,
  which is `_reset_dir(repo, wf_id, "source")`'s `wf_root = repo.wf(wf_id).resolve()`. Built the
  reproduction test's decoy to be a fully valid `wf_0001` copy specifically so it would sail past
  every one of those content checks, proving the id check itself is what stops it -- not an
  incidental content failure.
- Deliberately ran the full suite with only the `paths.py` change in place first (a separate,
  isolated pytest invocation before touching `build_samples.py`), per the ruling's explicit
  instruction to report rather than loosen the rule if anything broke. Nothing did.
- Checked every existing call site of `Repo.wf`/`Repo.seg`/`wf_token`/`seg_token` across
  `scripts/` for a `*parts` value that might now be wrongly rejected (`load_golden.py`'s
  `".sandbox.duckdb"` was the one part starting with a character worth double-checking) before
  relying on the full-suite run alone; found no false positive.
- Confirmed the M1 fix (`wf_root not in resolved.parents` instead of `resolved == wf_root or
  wf_root in resolved.parents`) doesn't change behaviour for any of the five real
  `_reset_dir(repo, wf_id, ...)` call sites in `seed`/`build` -- all pass at least one part naming
  an owned subdirectory, so the target is always strictly inside `wf_root`, never equal to it.
- Re-verified round 1's own tests (the byte-identical determinism check, the segmentation-change
  and dropped-golden-set-folder tests) still pass unmodified against the restructured `seed` --
  the validate-then-delete reordering changes *when* `BuildError` can be raised, not what a
  successful `seed`/`build` produces.
- Scope check against the ruling: made no changes to `build`'s own two `_reset_dir` calls
  (`golden/intermediates`, `golden/outputs`), the CLI, `_logical_by_tool`, or any exit-code path --
  all out of scope for this round and already covered by round 1.

## Fix round 3

Working directory changed again: a fresh worktree, `.worktrees/task-9-fix3` (branch
`wt/task-9-fix3`), at the head containing round 2's commit (`52d3fa7`), because another agent was
now writing in the main checkout.

**Session interruption note:** this round was killed by a usage limit partway through (after RED
evidence had been captured for rules A and C but before `tests/test_build_samples.py`'s own rule
B/C tests were written), then resumed. The resume left `scripts/lib/paths.py` and
`scripts/inject_outputs.py` reverted to round 2's `HEAD` (the deliberate revert used to capture
RED evidence) with the fixed versions saved alongside in the scratch temp directory, while
`scripts/dev/build_samples.py` and the `test_foundations.py`/`test_inject_outputs.py` test
additions were already complete and uncommitted. Per the resume instructions, a `wip:` commit
(`679e12e`) captured that state first, before restoring the two reverted files and finishing
`test_build_samples.py`'s tests. Both commits are on `wt/task-9-fix3`; the section below covers
the round's work as a whole, noting where the interruption falls.

### The finding (re-review of round 2)

Re-reviewer, verbatim (summarized): the owned-top-level allowlist
(`_OWNED_TOP_LEVEL`/`_OWNED_GOLDEN_SUBDIRS`) added in round 2 was not the independent line of
defence its own ruling required -- it inspected `parts[0]`/`parts[1]` by literal string equality,
and round 2's `_validate_part` rejected only *absolute* parts, never a relative `..` component.
Reproduced live: `_reset_dir(repo, "wf_0001", "golden", "inputs", "..", "..", "intake")` and the
same with `"segments"` both passed the allowlist (`"golden"`, `"inputs"` are legal first parts)
and the resolve-and-contain check (the resolved target is still strictly inside `wf_root`, just
not inside `golden/inputs`), deleting `intake/mappings.yaml` and `segments/` respectively with no
exception. Round 2's own tests never covered a `..`-crafted path that passes the allowlist and
reaches an unowned sibling. Also flagged, and formally ruled into this round: (i) with
`samples_dir` nested inside `workflows/<wf_id>/source/`, `_reset_dir(repo, wf_id, "source")`
deletes the sample itself and `_copy_tree` then crashes with an uncaught `FileNotFoundError`; (ii)
`inject_outputs.py`'s `import_captures` passes a capture-map row's own `of_tool`/`segment`/
`stream` into `Repo.wf`'s `*parts`, which round 2's absolute-only check would let a `..` through.

### What changed

**`scripts/lib/paths.py` (rule A, central)**: `_validate_part` now also rejects an empty part and
any part that, once split on both `/` and `\`, contains a `.` or `..` *component* -- checked by
splitting the string and comparing each piece for exact equality to `"."`/`".."`, not by searching
for a dot substring, so a dot *inside* a component (a filename like `"3_Output.csv"`, a literal
like `".sandbox.duckdb"` used by `load_golden.py`, or a multi-component literal like
`"golden/inputs"`) stays legal. Ran the full suite with this alone in place, before touching
`build_samples.py`: **0 failures** -- no existing caller relied on a dot-component in parts.

**`scripts/dev/build_samples.py` (rule B)**: replaced the literal-name allowlist
(`_OWNED_TOP_LEVEL`/`_OWNED_GOLDEN_SUBDIRS`) with `_owned_dirs(repo, wf_id)`, which resolves the
five directories `seed`/`build` actually own (`source`; `golden/inputs`, `golden/targets_before`,
`golden/intermediates`, `golden/outputs`), and `_reset_dir` now requires its *resolved* target to
equal or land inside one of them -- judged on where the path actually points, not on the literal
strings it was spelled with, so no number of `..` components smuggled through a future
`_validate_part` (or reached by monkeypatching it out, as the isolation test does) can walk back
out to a sibling. The zero-parts refusal and the strict-inside-`wf_root` resolve-and-contain check
(kept as defence in depth against a symlink, per the ruling) are unchanged in behaviour. The
module comment above the old allowlist constants (which claimed the allowlist was "independent of
the resolve-and-contain check", the exact claim the finding showed was false) is replaced by
`_owned_dirs`'s own docstring, which states what the code now actually guarantees.

**`scripts/dev/build_samples.py` (rule C)**: added `_refuse_samples_dir_inside_workflow(repo,
wf_id, samples_dir)`, called as the very first line of `seed` (and therefore protecting `build`
too, which calls `seed` first): raises `BuildError` when `samples_dir` resolves to `repo.wf(wf_id)`
or somewhere inside it, before anything is deleted. This closes finding (i) -- previously,
`seed`'s own `_reset_dir(repo, wf_id, "source")` would delete the very fixture being read from
when `samples_dir` was nested under `workflows/<wf_id>/source/`, and the `_copy_tree` immediately
after it then crashed with an uncaught `FileNotFoundError` (reproduced live in the RED run below).

**`scripts/inject_outputs.py` (finding ii, one-line fix as the ruling anticipated)**: the
`--import-set` CLI handler's `except (FileNotFoundError, yxdb.YxdbError)` now also catches
`ValueError`, routing the same domain failure `Repo.wf` now raises for a malformed
`of_tool`/`segment`/`stream` through the existing one-line-message exit-1 path, alongside the
already-handled "capture file missing" and "capture file unreadable" cases -- rather than falling
through to the generic exit-2 crash handler. Investigated which row field is actually exploitable
through `_golden_csv_path`: `of_tool` and `stream` are always suffixed with `".csv"` before being
passed to `Repo.wf` (`f"{row['of_tool']}.csv"`), so a bare `".."` there becomes the single filename
component `"...csv"` -- odd, but not a `.`/`..` component and therefore not rejected, and not a
traversal either (it can't resolve outside the target directory). Only `segment` (used for the
`"intermediate"` kind, passed to `Repo.wf` on its own, unconcatenated) is a genuine vector. This is
noted in both the code comment and the test docstring so the distinction isn't lost.

**Tests** -- `tests/test_foundations.py`: `BAD_PARTS`/`GOOD_PARTS` parametrized tests (`".."`,
`"."`, `""`, `"a/../b"`, a backslash variant built with `chr(92)` to avoid a quoting accident,
`"golden/./inputs"` → `ValueError` for both `Repo.wf` and `Repo.seg`; `"golden"`, `"golden/inputs"`,
`"seg_01"`, `"3_Output.csv"`, `"validation.normal.json"` → accepted).

`tests/test_build_samples.py`: updated the round-2 `test_reset_dir_refuses_to_delete_outside_the_
workflow_folder` test to accept either `ValueError` (rule A, which now catches this literal
construction first) or `BuildError`, with a comment explaining why; added
`test_reset_dir_rejects_dot_crafted_paths_to_unowned_siblings` (both live reproductions from the
finding, run for real, both defences active); `test_reset_dir_ownership_check_works_even_if_
validate_part_does_not` (rule B *in isolation*, per the ruling's own suggestion: monkeypatches
`lib.paths._validate_part` to a no-op so the same `..`-crafted parts reach `_reset_dir`'s own
ownership check unfiltered, proving it independently correct rather than merely shadowed by rule
A); `test_reset_dir_allows_a_legitimate_nested_target` (`"golden", "inputs", "normal"` → allowed,
and only that nested target is removed); and three rule-C tests
(`test_seed_refuses_a_samples_dir_nested_inside_the_workflow_folder`,
`..._equal_to_the_workflow_folder`, `test_build_refuses_a_samples_dir_nested_inside_the_workflow_
folder`), each building a full, otherwise-valid fixture copy under the dangerous location and
confirming both the raise and that the fixture survives byte-for-byte.

`tests/test_inject_outputs.py`: `test_import_captures_rejects_a_traversal_segment` (a capture-map
row with `segment: ".."` raises `ValueError` out of `import_captures`, and nothing is written --
Pass 1 resolves every row's path before Pass 2 writes anything) and
`test_cli_import_set_with_a_traversal_segment_exits_1_and_writes_nothing` (the same, through the
CLI: exit 1, no traceback on stderr, nothing written).

### TDD evidence

RED (rule A, `paths.py`) -- reverted `scripts/lib/paths.py` to `HEAD` and ran the new
`test_foundations.py` tests:
```
.venv/Scripts/python.exe -m pytest tests/test_foundations.py -k "dot_component" -v
```
```
12 failed, 5 passed, 65 deselected in 0.15s
```
All 12 failures were `Failed: DID NOT RAISE ValueError` (six `BAD_PARTS` cases × `Repo.wf` and
`Repo.seg`); the 5 `GOOD_PARTS` cases already passed, as expected. Restored the fix and reran:
```
.venv/Scripts/python.exe -m pytest tests/test_foundations.py -v
```
```
collected 82 items
82 passed in 0.16s
```
Ran the full suite with only this change in place (before touching anything else) per the
ruling's explicit "report rather than loosen" instruction: **0 failures**.

RED (findings ii / rule A reaching `inject_outputs.py`) -- with `scripts/lib/paths.py` fixed but
`scripts/inject_outputs.py` still at `HEAD`, ran the new `test_inject_outputs.py` tests:
```
.venv/Scripts/python.exe -m pytest tests/test_inject_outputs.py -k "traversal" -v
```
```
2 failed, 19 deselected in 0.19s
```
`test_import_captures_rejects_a_traversal_segment` failed with `Failed: DID NOT RAISE ValueError`
-- confirming that, without rule A, a malicious `segment` value silently succeeds and would write
a golden CSV to whatever the traversed path resolves to (a `..`-based directory swap the test
doesn't need to observe directly, since the point is that it *doesn't raise*). The CLI-level test
(first written against `stream: ".."` before being corrected to `segment: ".."` -- see below)
failed with `assert 0 == 1`, having printed three real CSV paths to stdout, one of them
`golden\intermediates\..\normal\3_Output.csv` -- a literal `..` segment landing in a real, if
oddly named, path on disk, i.e. the traversal actually happening. Restored the
`inject_outputs.py` fix and reran: both tests pass.

**Correction made mid-round**: the first draft of the CLI test mutated a captured row's `stream`
field to `".."`, matching the finding's literal wording ("segment or stream"). Investigation (see
"What changed" above) showed this doesn't exercise real protection, because `_golden_csv_path`
always appends `".csv"` to `stream`/`of_tool` before handing them to `Repo.wf`, so `".."` becomes
the single component `"...csv"` -- not a `.`/`..` component, and not a traversal since it can't
escape the target directory either. Confirmed by running the original test against the *fixed*
code: it failed (`assert rc == 1` got `0`), proving the test itself was asserting behaviour that
was never implemented (or needed). Rewrote it to mutate `segment` instead, which
`_golden_csv_path` passes to `Repo.wf` on its own for the `"intermediate"` kind -- the actual
vector -- and reran both RED and GREEN for the corrected test (both shown above/below).

RED (rules B and C, `build_samples.py`) -- with `paths.py` and `inject_outputs.py` fixed,
temporarily reverted `scripts/dev/build_samples.py` to round 2's committed state (`52d3fa7`) and
ran the new/changed tests:
```
.venv/Scripts/python.exe -m pytest tests/test_build_samples.py -k "ownership_check or legitimate_nested_target or samples_dir_nested or samples_dir_equal or dot_crafted_paths" -v
```
```
FAILED tests/test_build_samples.py::test_reset_dir_ownership_check_works_even_if_validate_part_does_not
FAILED tests/test_build_samples.py::test_seed_refuses_a_samples_dir_nested_inside_the_workflow_folder
FAILED tests/test_build_samples.py::test_seed_refuses_a_samples_dir_equal_to_the_workflow_folder
FAILED tests/test_build_samples.py::test_build_refuses_a_samples_dir_nested_inside_the_workflow_folder
4 failed, 2 passed, 36 deselected in 0.25s
```
The 2 passes (`test_reset_dir_rejects_dot_crafted_paths_to_unowned_siblings` and
`test_reset_dir_allows_a_legitimate_nested_target`) were expected: the first accepts either
`ValueError` or `BuildError` and rule A alone (already restored at this point) already raises
`ValueError` for the literal reproductions; the second doesn't touch anything rule B/C changed.
`test_build_refuses_a_samples_dir_nested_inside_the_workflow_folder` failed with the same live
crash pattern the finding described -- an uncaught `FileNotFoundError` from `shutil.copytree`
trying to read `workflows/wf_0001/source/samples/wf_0001/source`, a directory `_reset_dir` had
just deleted out from under it. Restored the round-3 `build_samples.py` and reran:
```
.venv/Scripts/python.exe -m pytest tests/test_build_samples.py -v
```
```
collected 42 items
42 passed in 2.97s
```

GREEN -- the three files together, then the full suite, both run once after every file was
restored to its final, fixed state:
```
.venv/Scripts/python.exe -m pytest tests/test_foundations.py tests/test_build_samples.py tests/test_inject_outputs.py -v
```
```
collected 145 items
145 passed in 3.61s
```
```
.venv/Scripts/python.exe -m pytest -q
```
```
........................................................................ [ 11%]
........................................................................ [ 23%]
........................................................................ [ 34%]
........................................................................ [ 46%]
........................................................................ [ 57%]
........................................................................ [ 69%]
........................................................................ [ 80%]
........................................................................ [ 92%]
...............................................                          [100%]
EXIT=0
```
623 tests collected across the suite (up from 585 at the end of fix round 2, from other tasks'
work landing on `feat/pipeline-m0-m2` since then -- e.g. `tests/test_snowflake_ddl.py` is new since
that snapshot), all passing, no warnings, no skips.

### Files changed (this round)

- `scripts/lib/paths.py` -- `_validate_part` rejects an empty part and a `.`/`..` component.
- `scripts/dev/build_samples.py` -- `_owned_dirs` replaces the literal-name allowlist;
  `_refuse_samples_dir_inside_workflow`, called first thing in `seed`.
- `scripts/inject_outputs.py` -- `--import-set`'s exception tuple also catches `ValueError`.
- `tests/test_foundations.py` -- dot-component coverage for `Repo.wf`/`Repo.seg` parts.
- `tests/test_build_samples.py` -- updated 1 round-2 test, added 6 round-3 tests (2 for rule B, 1
  reused for the isolation proof, 3 for rule C).
- `tests/test_inject_outputs.py` -- 2 tests for the capture-map traversal.

Commits: `679e12e` (`wip: red tests for fix round 3` -- the state a usage-limit interruption left
mid-round, made per the resume instructions before restoring/finishing anything) and `c0d3316`
(the completed fix, all rules A/B/C plus the `inject_outputs.py` one-liner).

### Self-review (this round)

- Resumed correctly after the interruption: read `git status` and diffed the on-disk files against
  the scratch-directory backups from the killed session before touching anything, confirmed
  `scripts/dev/build_samples.py` was already complete (byte-identical to its backup) and only
  `scripts/lib/paths.py`/`scripts/inject_outputs.py` needed restoring from backups, and only then
  proceeded -- rather than re-deriving work that was already done and risking a divergent second
  implementation.
- Caught my own test bug (the `stream` vs `segment` mix-up above) by actually running the "RED"
  test against the *fixed* code and seeing it still fail, rather than trusting that "it raises
  `ValueError` under the old code" was sufficient evidence the test exercised the real fix -- the
  old code raised nothing for that case either (it printed three successfully-written CSV paths),
  which in hindsight was the tell.
- Verified rule B is provably independent of rule A, not merely redundant with it, via the
  `_validate_part`-monkeypatch test the ruling specifically asked for -- without it, every test
  targeting `_reset_dir`'s ownership logic would pass purely because rule A intercepts first,
  masking a regression in `_owned_dirs` itself.
- Confirmed `build`'s existing two `_reset_dir` calls (`golden/intermediates`, `golden/outputs`)
  still pass rule B's ownership check without modification -- both are among the five directories
  `_owned_dirs` returns, so no behavioural change for the already-covered round-1/round-2 tests
  (all 42 of `test_build_samples.py`'s tests, including every round-1 and round-2 test, still pass
  unmodified).
- Scope check: made no changes to the CLI's own exit-code paths in `build_samples.py`, to
  `_logical_by_tool`, or to anything in `inject_outputs.py` beyond the one exception-tuple line the
  ruling anticipated.

---

## Fix round 4 (fresh implementer)

Worktree `/.worktrees/task-9-fix4`, branch `wt/task-9-fix4`. Commits:

- `38f80b4` `fix: never delete or write through a link; rmtree gets the lexical path`
  (`scripts/dev/build_samples.py`, `tests/test_build_samples.py`)
- `1a67c9f` `docs: correct round-3 test comments that still described resolved-path ownership`
  (`tests/test_build_samples.py`, comments only -- no assertion changed)

### The finding, reproduced live first

Junction `workflows/wf_0001/golden/inputs` -> `workflows/wf_0001/intake`, then
`_reset_dir(repo, "wf_0001", "golden", "inputs")`, run against `HEAD` (`7996fe3`) with
`.venv/Scripts/python.exe`:

```
before: mappings.yaml exists = True
_reset_dir returned normally: ...\workflows\wf_0001\golden\inputs
after : intake exists = False | mappings.yaml exists = False
after : junction still there = True
```

Both `_owned_dirs` and `_reset_dir` called `.resolve()`, so both dereferenced the same reparse
point and agreed; `wf_root in resolved.parents` held too (`intake/` is inside the workflow); and
`shutil.rmtree` was handed `resolved` -- the real path of `intake/` -- so rmtree's own link
refusal never fired.

### What changed in `scripts/dev/build_samples.py`

1. `_OWNED_PARTS` (new module constant) lists the five owned subtrees once; `_owned_dirs` builds
   them from it **without `.resolve()`** -- lexical paths.
2. `_is_link(path)` (new): `os.path.islink(p) or os.path.isjunction(p)`, plus
   `os.lstat(p).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT` as a belt-and-braces check
   that catches other reparse-point kinds. The `st_file_attributes` read is `hasattr`-guarded, so
   the module still imports and behaves on non-Windows; a missing path answers False.
3. `_refuse_links_on_path(repo, wf_id, *parts)` (new): walks the **lexical** path from
   `repo.wf(wf_id)` down, one component at a time -- `wf_root`, each intermediate (`golden`), and
   the target -- and raises `BuildError` naming the offending component. Nothing is resolved: a
   check that dereferences the link it is looking for cannot see it.
4. `_refuse_linked_owned_dirs(repo, wf_id)` (new): the same walk over all five owned paths (so
   over `workflows/<wf_id>/` and `golden/` too), called at the top of `seed` -- i.e. before the
   first deletion *or* write of any `seed`/`build` run (`build` calls `seed` first).
5. `_reset_dir` rewritten: refuses zero parts; refuses any part containing a `.`/`..` component
   after splitting on both separators (its own check, not `lib.paths`'s); judges ownership
   **lexically** with `os.path.normcase` (target equals an owned dir or has one among its lexical
   parents); runs the link walk; keeps the resolved-containment check as an extra guard; then
   hands **the lexical path** to `shutil.rmtree` / `Path.unlink`.

Order matters and is deliberate: the dot-component refusal must precede the ownership comparison,
because a lexical `golden/inputs/../../intake` still has `golden/inputs` among its `parents` and
would otherwise be judged owned. That is exactly the case the round-3 isolation test covers.

### The guarantee the docstring now states (and what it does not claim)

`_reset_dir`'s docstring now says, of the *lexical* path it deletes (spelled from the workflow
folder down, never passed through `.resolve()`), that each of these runs before anything is
deleted and raises `BuildError` on its own: (1) `parts` is non-empty; (2) no part contains a `.`
or `..` component; (3) the path is one of `_owned_dirs` or lies lexically inside one, compared
case-insensitively; (4) no component from `repo.wf(wf_id)` down to the target is a symlink,
junction or other reparse point; (5) it still resolves strictly inside the workflow folder. Only
then does `shutil.rmtree` get the lexical path, so rmtree's own link refusal is one more line of
defence instead of something a pre-resolved path walks past.

And explicitly, correcting the earlier rounds' over-claiming: **this is not a privilege
boundary.** Planting a link inside `workflows/<wf_id>/` already needs write access to the very
tree the script rewrites, so the checks are robustness against a malformed or mis-provisioned tree
(a junction left by a half-migrated checkout, a hand-made shortcut to another drive), not
protection from someone who has that access. None of it is atomic: a link planted between the walk
and the `rmtree` would be seen only by `rmtree` itself. `_refuse_links_on_path`'s own docstring
adds that the walk starts at `repo.wf(wf_id)` and says nothing about the repo root or `workflows/`
being links.

### Covering tests (all in `tests/test_build_samples.py`, new "Fix round 4" section)

A `make_dir_link` fixture picks the mechanism per platform -- `_winapi.CreateJunction` on Windows
(no privilege needed), `os.symlink(..., target_is_directory=True)` elsewhere and for the symlink
variant -- and at teardown removes the **links** with `os.rmdir`/`os.unlink`, never following
them, so neither the fixture nor pytest's tmp_path clean-up can recurse into a target. Every link
fixture lives under `tmp_path`.

- `test_reset_dir_refuses_an_owned_dir_that_is_a_junction_to_a_sibling` (a): the reviewer's
  reproduction -- raises, `intake/mappings.yaml` and `intake/inputs/keep.txt` survive, junction
  untouched.
- `test_reset_dir_refuses_a_junction_at_an_intermediate_component` (b): `golden/` itself is the
  junction; raises naming `golden`, nothing deleted.
- `test_reset_dir_refuses_a_junction_pointing_outside_the_workflow` (c): `source` -> a tmp dir
  with a sentinel; raises, sentinel survives.
- `test_reset_dir_refuses_an_owned_dir_that_is_a_symlink` (e): true directory symlink, with
  `pytest.skip` if `os.symlink` raises `OSError`. It did **not** skip on this machine (symlink
  creation is permitted here), and the junction tests a-d never skip on Windows regardless.
- `test_seed_refuses_a_linked_owned_dir_before_deleting_or_writing_anything` (d): reseed with
  `golden/inputs` a junction to `intake/` -- `BuildError`, `source/` byte-identical afterwards
  (snapshot equality), `intake/` still holds exactly `mappings.yaml` (nothing written through),
  junction intact.
- `test_reset_dir_removes_a_link_nested_inside_an_owned_dir_without_following_it`: locks in what
  rule 2 buys on the inside -- a junction *nested* in `source/` is removed as the name it is and
  its target keeps its files.

### Commands and output

RED (new tests against the unmodified module):

```
$ .venv/Scripts/python.exe -m pytest tests/test_build_samples.py -q -k "junction or symlink or linked or nested_inside"
..FFFFF.                                                                 [100%]
FAILED tests/test_build_samples.py::test_reset_dir_refuses_an_owned_dir_that_is_a_junction_to_a_sibling
    - Failed: DID NOT RAISE BuildError
FAILED tests/test_build_samples.py::test_reset_dir_refuses_a_junction_at_an_intermediate_component
    - Failed: DID NOT RAISE BuildError
FAILED tests/test_build_samples.py::test_reset_dir_refuses_a_junction_pointing_outside_the_workflow
    - AssertionError: Regex pattern did not match. Expected regex: 'is a link'
      Actual message: '...refusing to delete ...\workflows\wf_0001\source (resolves to
      ...\outside, which is not strictly inside ...\workflows\wf_0001)'
FAILED tests/test_build_samples.py::test_reset_dir_refuses_an_owned_dir_that_is_a_symlink
    - Failed: DID NOT RAISE BuildError
FAILED tests/test_build_samples.py::test_seed_refuses_a_linked_owned_dir_before_deleting_or_writing_anything
    - seed deleted the junction's target and then crashed writing through the dangling junction:
      scripts\dev\build_samples.py:244: in seed -> shutil.copytree(...) ->
      FileNotFoundError: [WinError 3] ...\workflows\wf_0001\golden\inputs\edge
```

Honest notes on the RED run: (c) already raised under the old code -- the old resolve-and-contain
check catches a link that points *outside* the workflow, just with the wrong reason -- so it was
red only on the message, and the nested-link test was green from the start (it documents existing
`rmtree` behaviour the fix must not lose). (a), (b), (d) and (e) are the real reproductions:
(a)/(b)/(e) returned normally after deleting, and (d) shows `seed` deleting `intake/` and then
crashing while writing through what was left of it.

GREEN:

```
$ .venv/Scripts/python.exe -m pytest tests/test_build_samples.py tests/test_foundations.py tests/test_inject_outputs.py -p no:cacheprovider
151 passed in 3.45s

$ .venv/Scripts/python.exe -m pytest -p no:cacheprovider
639 passed in 20.91s
```

The reviewer's reproduction re-run against the fix:

```
BuildError: wf_0001: refusing to touch ...\workflows\wf_0001\golden\inputs:
  ...\workflows\wf_0001\golden\inputs is a link (symlink, junction or other reparse point),
  and seed/build never delete or write through one
after : mappings.yaml exists = True | junction intact = True
```

### Point 5: the other delete/overwrite sites

Surveyed every write or delete in `build_samples.py`, and probed the two library behaviours it
depends on with throwaway junctions (Python 3.14.2, Windows 10):

- `_copy_tree` -> `shutil.copytree(src, dst, dirs_exist_ok=True)` (3 calls: `source/`,
  `golden/inputs/<set>/`, `golden/targets_before/`). **Verified: copytree onto a junction writes
  straight through into the junction's target.** This is the hazard rule 5 anticipated, and it is
  what `_refuse_linked_owned_dirs` at the top of `seed` now covers. A link nested *deeper* (e.g.
  `golden/inputs/normal`) cannot be written through either, because each owned directory is
  `_reset_dir`'d immediately before it is copied into, and...
- ...**verified: `shutil.rmtree` on a directory containing a nested junction deletes the junction
  as a name and leaves its target's files alone** (3.14 treats reparse points as files during the
  walk), and **`shutil.rmtree` called on a junction itself raises `OSError("Cannot call rmtree on
  a symbolic link")`**. So rule 2 (hand rmtree the lexical path) is a real second layer, not a
  theoretical one. Both behaviours now have a regression test.
- `write_yxdb(repo.wf(wf_id, "source", "data", basename))` -- `source/` has just been reset and
  re-copied at that point, and `write_yxdb` creates `source/data/` itself via
  `parent.mkdir(parents=True, exist_ok=True)`, so no pre-existing link can be in that path.
- `save_manifest` -> `write_json(repo.wf(wf_id, "manifest.json"))`, `open(..., "w")`. Residual,
  reported not fixed (scope): `manifest.json` is not an owned path and is never deleted, but if
  that *file* were itself a symlink, the write would follow it. The walk covers
  `workflows/<wf_id>/` (its parent), not the file. Closing it would mean extending the walk to
  non-owned leaf files, which the ruling did not ask for.
- `build`'s downstream steps: `alteryx_sim.run` writes only under `golden/intermediates/` and
  `golden/outputs/` (both owned, both covered by the pre-walk, and both `_reset_dir`'d first);
  `segment.run` writes under `segments/` and does its own `shutil.rmtree` of stale segment dirs
  (`scripts/segment.py:385`) -- another script's directory and another task's guard, untouched
  here and not covered by this walk.
- Not checked, by design and stated in the docstring: the repo root and `workflows/` itself.
  `Repo.root` is `.resolve()`d at construction, and a junction at `workflows/` redirects the whole
  tree consistently rather than aiming an owned name at an unowned sibling, so refusing it would
  reject a legitimate "workflows on another drive" layout for no gain.

### Self-review / concerns

- **One message deliberately worded to keep an existing test green.** The round-3 isolation test
  asserts `match="not one of the directories"`. With `_validate_part` monkeypatched off, the check
  that now fires for `("golden", "inputs", "..", "..", "intake")` is `_reset_dir`'s own
  dot-component refusal, so its message reads "... part '..' contains a '.'/'..' component, so the
  path it spells is not one of the directories seed/build own". That is a true statement, not a
  contrivance to pass a regex, but it is worth a reviewer's eye. The test body and assertions are
  unchanged; commit `1a67c9f` updates that test's docstring and the round-3 section comment, which
  still claimed ownership was judged on the resolved path.
- Ownership being lexical is only sound because dot components are refused twice (in `Repo.wf` and
  again in `_reset_dir`); removing either would reopen the round-3 hole. Both are tested, the
  second with `_validate_part` disabled.
- The checks are not atomic (stated in the docstring). A link planted between the walk and the
  `rmtree` is caught only by `rmtree`'s own refusal -- which, per the probe above, does fire for
  the target path itself.
- Scope: no changes outside `_owned_dirs`/`_reset_dir`/`seed`'s first lines plus the three new
  helpers; `_refuse_samples_dir_inside_workflow` still uses `.resolve()` (rule 4); no CLI,
  exit-code or `lib/paths.py` changes.
