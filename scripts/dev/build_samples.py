"""Seed a sample workflow into a working `workflows/<wf_id>/` tree and build its simulated golden
sets, chaining the parser, the segmenter and the Alteryx simulator (program spec §7.5/§8).

    python scripts/dev/build_samples.py seed  [--only wf_0001] [--samples samples] [--root .]
    python scripts/dev/build_samples.py build [--only wf_0001] [--samples samples] [--root .]

`seed` copies one `samples/<wf_id>/` fixture's source and golden inputs into `workflows/<wf_id>/`
and writes its `manifest.json`. `build` does that, then runs `parse.run`, `segment.run` and
`dev.alteryx_sim.run` over the result, so every end-to-end test and the intake tests have
something to start from. Every test-only fixture under `samples/` this script touches is a hand
written stand-in (dag-contract.md); nothing here has run against real Alteryx or Snowflake.

`--only` builds a single sample; without it, every `samples/wf_*` directory is processed (`_tools`
is a helper directory, not a workflow, and is skipped). Exit codes follow the plan's Global
Constraints: 0 on success (a workflow whose parse trips the invariants check, like `wf_0005`, is
the simulator's designed refusal path, not a failure, and still counts as success), 1 when a
sample cannot be built for a reason this script checks for (a missing sample fixture, a source
that fails to parse, a segmentation that cannot be ordered), and 2 for a usage error (an unknown
`--only` id, a missing `samples/` directory, or anything unexpected) with nothing created.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
import traceback
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):  # `python scripts/dev/build_samples.py` puts scripts/ on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parse
import segment
from dev import alteryx_sim
from lib.io import load_manifest, read_json, save_manifest
from lib.paths import Repo, add_root_arg
from lib.typed_csv import read_table
from lib.vocab import GOLDEN_SETS
from lib.yxdb import write_yxdb


class BuildError(Exception):
    """A sample that cannot be built for a reason this script checks for (plan Global Constraints:
    a domain failure, exit 1) -- as opposed to a usage error or an unexpected crash (exit 2)."""


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


# The five subtrees under `workflows/<wf_id>/` that `seed`/`build` own outright, as `Repo.wf`
# `*parts`. Everything else there (`intake/`, `segments/`, `parsed/`, `docs/`, `manifest.json`, …)
# belongs to other steps or to a human, and this script never deletes it.
_OWNED_PARTS: tuple[tuple[str, ...], ...] = (
    ("source",),
    ("golden", "inputs"),
    ("golden", "targets_before"),
    ("golden", "intermediates"),
    ("golden", "outputs"),
)


def _owned_dirs(repo: Repo, wf_id: str) -> list[Path]:
    """The five directories `seed`/`build` own outright, as *lexical* paths -- spelled out from
    the workflow folder down, never passed through `.resolve()`.

    `_reset_dir` compares its own lexical target against these rather than string-matching the
    literal `*parts` it was called with, because a literal-name allowlist checking only
    `parts[0]`/`parts[1]` (e.g. requiring `"golden"`, then `"inputs"`) was fooled by
    `_reset_dir(repo, wf_id, "golden", "inputs", "..", "..", "intake")`. The comparison is
    lexical, and safe to make lexically, because `Repo.wf` rejects absolute parts and `.`/`..`
    components outright and `_reset_dir` re-checks that itself: with no dot components in play,
    the lexical path is the path. Resolving here instead was the round-3 mistake -- when an owned
    directory is itself a link, both sides of the comparison dereference the same reparse point,
    agree, and the deletion lands on whatever the link points at.
    """
    return [repo.wf(wf_id, *parts) for parts in _OWNED_PARTS]


def _is_link(path: Path) -> bool:
    """True if `path` itself is a symlink, a directory junction, or any other reparse point -- a
    name whose contents live somewhere else. Never dereferences `path`, and answers False for a
    name that does not exist (`os.lstat` raising is the only way to learn that without a race).
    """
    if os.path.islink(path) or os.path.isjunction(path):
        return True
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not hasattr(info, "st_file_attributes"):  # not Windows: islink() above was the whole story
        return False
    return bool(info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _refuse_links_on_path(repo: Repo, wf_id: str, *parts: str) -> Path:
    """Walk the LEXICAL path from `repo.wf(wf_id)` down to `repo.wf(wf_id, *parts)`, one component
    at a time, and raise `BuildError` naming the offending component if any of them -- the
    workflow folder itself, any intermediate directory, or the target -- is a link. Returns the
    lexical target.

    Nothing here resolves anything: a check that dereferences the link it is looking for cannot
    see it. The walk starts at `repo.wf(wf_id)` and says nothing about the repo root or
    `workflows/` itself.
    """
    walked = repo.wf(wf_id)
    checked = [walked]
    for part in parts:
        for component in Path(part).parts:  # "golden/inputs" is two components, as `Repo.wf` joins it
            walked = walked / component
            checked.append(walked)
    for candidate in checked:
        if _is_link(candidate):
            raise BuildError(f"{wf_id}: refusing to touch {checked[-1]}: {candidate} is a link "
                             "(symlink, junction or other reparse point), and seed/build never "
                             "delete or write through one")
    return checked[-1]


def _refuse_linked_owned_dirs(repo: Repo, wf_id: str) -> None:
    """The same walk as `_refuse_links_on_path`, over all five owned directories (and so over
    `workflows/<wf_id>/` and `golden/` too), run once before `seed`/`build` writes ANYTHING.

    `_reset_dir` refuses to delete through a link, but the copies that follow a reset would still
    write through one (`shutil.copytree(..., dirs_exist_ok=True)` onto a junction writes into the
    junction's target), and so would the yxdb writer and `save_manifest`. Running the walk up
    front means a tree with a linked owned directory is refused before the first deletion instead
    of partway through the rewrite.
    """
    for parts in _OWNED_PARTS:
        _refuse_links_on_path(repo, wf_id, *parts)


def _reset_dir(repo: Repo, wf_id: str, *parts: str) -> Path:
    """Delete `repo.wf(wf_id, *parts)` if present, then return that (now-absent) path for the
    caller to repopulate.

    `seed`/`build` own several subtrees under `workflows/<wf_id>/` outright (`_OWNED_PARTS`):
    every run replaces them wholesale rather than overlaying, so a shrunk or restructured sample
    fixture (a source file renamed, a golden CSV or a whole set folder dropped, a yxdb input's
    basename changed, a segmentation change renaming segments) can never leave an old file behind
    for a downstream step to trip over.

    What this guarantees, exactly, about the path it deletes -- the *lexical* path, spelled out
    from the workflow folder down and never passed through `.resolve()`. Each check raises
    `BuildError` on its own, and all of them run before anything is deleted:
      1. `parts` is non-empty, so this never deletes a whole workflow folder;
      2. no part contains a `.` or `..` component (checked here, splitting on both separators,
         rather than trusting `lib.paths._validate_part` to have done it);
      3. the lexical path is one of `_owned_dirs` or lies lexically inside one of them, compared
         case-insensitively (`os.path.normcase`) -- never `intake/`, `segments/` or anything else;
      4. no component of it, from `repo.wf(wf_id)` down to the target itself, is a symlink, a
         junction or any other reparse point (`_refuse_links_on_path`);
      5. it still resolves to somewhere strictly inside the workflow folder.
    Only then is the LEXICAL path handed to `shutil.rmtree`, so rmtree's own refusal to act on a
    link is one more line of defence rather than something a pre-resolved path walks straight past
    (which is exactly what round 3 did: `rmtree` got the dereferenced path of `intake/`).

    What it does NOT claim: this is not a privilege boundary. Planting a link inside
    `workflows/<wf_id>/` already needs write access to the very tree this script rewrites, so the
    checks are robustness against a malformed or mis-provisioned tree (a junction left by a
    half-migrated checkout, a hand-made shortcut to another drive), not protection from someone
    who has that access. None of it is atomic: a link planted between check 4 and the `rmtree`
    would not be seen by the walk, only by `rmtree` itself.
    """
    if not parts:
        raise BuildError(f"{wf_id}: _reset_dir refuses to delete the whole workflow folder "
                         "(no parts given)")

    target = repo.wf(wf_id, *parts)
    for part in parts:
        if any(component in (".", "..") for component in re.split(r"[\\/]+", str(part))):
            raise BuildError(f"{wf_id}: refusing to delete {target}: part {part!r} contains a "
                             f"'.'/'..' component, so the path it spells is not one of the "
                             f"directories seed/build own")

    owned = {os.path.normcase(str(directory)) for directory in _owned_dirs(repo, wf_id)}
    lexical = {os.path.normcase(str(path)) for path in (target, *target.parents)}
    if not owned & lexical:
        raise BuildError(f"{wf_id}: refusing to delete {target}, which is not one of the "
                         f"directories seed/build own")

    _refuse_links_on_path(repo, wf_id, *parts)

    wf_root = repo.wf(wf_id).resolve()
    resolved = target.resolve()
    if wf_root not in resolved.parents:
        raise BuildError(f"{wf_id}: refusing to delete {target} (resolves to {resolved}, which "
                         f"is not strictly inside {wf_root})")

    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    return target


def _read_sample(samples_dir: Path, wf_id: str) -> dict:
    sample_path = samples_dir / wf_id / "sample.json"
    if not sample_path.is_file():
        raise BuildError(f"{wf_id}: no sample.json under {samples_dir / wf_id}")
    try:
        return read_json(sample_path)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        raise BuildError(f"{wf_id}: cannot parse {sample_path}: {exc}") from exc


def _basename(windows_path: str) -> str:
    """The file name off the end of a Windows path, splitting on both `\\` and `/` (dag-contract
    §4's input `source`; contract C8 does the same split for the normalized source key)."""
    return re.split(r"[\\/]+", windows_path)[-1]


def _parse_sample_source(wf_id: str, workflow_path: Path) -> dict:
    """Parse `workflow_path` (still inside `samples_dir`, never yet copied anywhere) purely to
    plan `seed`'s work -- which yxdb inputs it will generate, and the manifest's `yxmd_version`/
    `engine` -- translating any failure into `BuildError` up front, before `seed` deletes
    anything. `parse.run` (called later by `build`) is what actually persists `parsed/dag.json`.
    """
    try:
        dag, _ = parse.parse_file(workflow_path)
    except Exception as exc:  # a sample fixture whose source doesn't even parse: nothing to seed
        raise BuildError(f"{wf_id}: cannot read {workflow_path.name}: "
                          f"{type(exc).__name__}: {exc}") from exc
    return dag


def _validate_golden_csvs(sample_dir: Path, wf_id: str) -> list[Path]:
    """Every golden-input CSV `seed` will copy (both the plain sets and `targets_before`) must be
    readable via `typed_csv.read_table` -- checked up front, before anything is deleted. Returns
    the golden-input set directories (including `targets_before`, if present) for `seed` to copy
    from afterwards, so this doesn't have to scan the directory a second time."""
    golden_inputs = sample_dir / "golden_inputs"
    if not golden_inputs.is_dir():
        return []
    entries = sorted(p for p in golden_inputs.iterdir() if p.is_dir())
    for entry in entries:
        for csv_path in sorted(entry.rglob("*.csv")):
            try:
                read_table(csv_path)
            except Exception as exc:
                rel = csv_path.relative_to(sample_dir)
                raise BuildError(f"{wf_id}: cannot read golden input {rel}: "
                                 f"{type(exc).__name__}: {exc}") from exc
    return entries


def _plan_yxdb_inputs(sample_dir: Path, wf_id: str, dag: dict) -> list[tuple[str, Path]]:
    """Every `input` node whose config `format` is `yxdb`, resolved to `(basename, normal_csv)`
    pairs -- and checked to exist up front, before anything is deleted, so a fixture missing the
    CSV a yxdb needs is caught while the existing workflow tree is still untouched."""
    plan: list[tuple[str, Path]] = []
    for node in dag["nodes"]:
        if node["type"] != "input" or (node.get("config") or {}).get("format") != "yxdb":
            continue
        source = (node["config"].get("source") or "").strip()
        if not source:
            continue
        normal_csv = sample_dir / "golden_inputs" / "normal" / f"{node['tool_id']}.csv"
        if not normal_csv.is_file():
            raise BuildError(f"{wf_id}: input tool {node['tool_id']} reads a yxdb file but has no "
                             f"golden_inputs/normal/{node['tool_id']}.csv to build it from")
        plan.append((_basename(source), normal_csv))
    return plan


def _refuse_samples_dir_inside_workflow(repo: Repo, wf_id: str, samples_dir: Path) -> None:
    """Refuse, before anything is deleted, a `samples_dir` that resolves to `repo.wf(wf_id)` or
    somewhere inside it.

    If it did, `seed`'s own `_reset_dir(repo, wf_id, "source")` (etc.) would delete the very
    fixture `seed` is partway through reading from -- e.g. a `--samples` under
    `workflows/<wf_id>/source/` -- and the `_copy_tree` right after it would then crash with an
    uncaught `FileNotFoundError` trying to copy from a directory that no longer exists, instead of
    the clean `BuildError` every other bad-fixture case in this module raises.
    """
    wf_root = repo.wf(wf_id).resolve()
    resolved_samples = samples_dir.resolve()
    if resolved_samples == wf_root or wf_root in resolved_samples.parents:
        raise BuildError(f"{wf_id}: samples directory {samples_dir} resolves to {resolved_samples}, "
                         f"inside the workflow folder {wf_root} that seed/build would delete from "
                         "under it")


def _refuse_already_seeded(repo: Repo, wf_id: str, force: bool) -> None:
    """Coordinator ruling F1e: `seed`/`build` refuse to touch a workflow that already has a
    `manifest.json` -- i.e. has already been seeded at least once -- unless `force` is true.
    Reseeding on purpose (this suite's own "Fix round" tests, a deliberately shrunk or restructured
    fixture) is still fully supported; it just has to say so. Checked before anything else, so
    nothing is written when this refuses.
    """
    if force:
        return
    if repo.wf(wf_id, "manifest.json").is_file():
        raise BuildError(f"{wf_id}: already has a manifest.json (already seeded); pass --force "
                         "(or force=True) to overwrite it")


def seed(repo: Repo, samples_dir: Path, wf_id: str, *, force: bool = False) -> dict:
    """Copy `samples/<wf_id>/source/**` to `workflows/<wf_id>/source/`, its golden inputs to
    `golden/inputs/<set>/` and `golden/targets_before/<set>/`, generate any yxdb source data, and
    write `manifest.json`. Never overwrites an existing manifest's `status`, `metrics`, `answers`
    or `accepted_diffs`: `load_manifest` returns what is already on disk, and only `id`, `source`
    and `segmentation` are set unconditionally here.

    Refuses outright, before writing anything, when `workflows/<wf_id>/manifest.json` already
    exists and `force` is not true (coordinator ruling F1e) -- an already-seeded workflow is not
    silently rewritten by an ordinary second invocation.

    `seed` owns `source/`, `golden/inputs/` and `golden/targets_before/` outright, and validates
    the WHOLE fixture -- `samples_dir` doesn't resolve inside the workflow folder it would delete
    from, no owned directory (nor `workflows/<wf_id>/` or `golden/` above them) is a link that a
    delete or a copy would act through, `sample.json` parses, `source/` has a workflow file that
    itself parses, every golden-input CSV is readable, every yxdb input has its `normal` CSV --
    before deleting anything: an invalid fixture or tree raises `BuildError` and leaves whatever
    `seed` last successfully wrote completely untouched. Only once all of that has passed is each
    owned directory reset (`_reset_dir`) immediately before it is repopulated, one at a time, so a
    failure partway through the copying itself (already-validated content notwithstanding) leaves
    at most one directory incomplete rather than several.
    """
    _refuse_already_seeded(repo, wf_id, force)
    _refuse_samples_dir_inside_workflow(repo, wf_id, samples_dir)
    _refuse_linked_owned_dirs(repo, wf_id)
    sample_dir = samples_dir / wf_id
    sample = _read_sample(samples_dir, wf_id)

    source_src = sample_dir / "source"
    if not source_src.is_dir():
        raise BuildError(f"{wf_id}: no source/ under {sample_dir}")
    try:
        workflow_path = parse.workflow_file(source_src)
    except FileNotFoundError as exc:
        raise BuildError(f"{wf_id}: {exc}") from exc
    dag = _parse_sample_source(wf_id, workflow_path)

    golden_entries = _validate_golden_csvs(sample_dir, wf_id)
    yxdb_plan = _plan_yxdb_inputs(sample_dir, wf_id, dag)

    # --- the fixture is fully validated; nothing below this line can fail on bad fixture content,
    # so only now does anything this function owns get deleted. ---

    _reset_dir(repo, wf_id, "source")
    _copy_tree(source_src, repo.wf(wf_id, "source"))

    _reset_dir(repo, wf_id, "golden", "inputs")
    for entry in golden_entries:
        if entry.name != "targets_before":
            _copy_tree(entry, repo.wf(wf_id, "golden", "inputs", entry.name))

    _reset_dir(repo, wf_id, "golden", "targets_before")
    for entry in golden_entries:
        if entry.name == "targets_before":
            _copy_tree(entry, repo.wf(wf_id, "golden", "targets_before"))

    for basename, normal_csv in yxdb_plan:
        table = read_table(normal_csv)
        write_yxdb(repo.wf(wf_id, "source", "data", basename), table["fields"], table["rows"])

    manifest = load_manifest(repo, wf_id)
    manifest["id"] = wf_id
    manifest["source"] = {
        "file": workflow_path.name,
        "alteryx_version": dag.get("yxmd_version"),
        "engine": dag.get("engine"),
        "server_schedule": sample.get("schedule"),
        "owner": sample.get("owner"),
        "consumers": [],
    }
    manifest["segmentation"] = sample.get("segmentation") or {}
    if sample.get("output_target"):
        manifest["output_target"] = sample["output_target"]
    manifest.setdefault("status", {})
    manifest.setdefault("metrics", {})
    save_manifest(repo, manifest)
    return manifest


def _logical_by_tool(repo: Repo, wf_id: str, sample: dict) -> dict[str, str]:
    """`intake/mappings.yaml` (C6) when intake has already run, else this sample's own logical
    map -- read from `sample` (the fixture under `samples_dir`) rather than
    `alteryx_sim.read_logical_by_tool`'s own `--root`-relative fallback, since `--samples` may
    point somewhere other than `<root>/samples` (as it does in every test here)."""
    if repo.wf(wf_id, "intake", "mappings.yaml").is_file():
        return alteryx_sim.read_logical_by_tool(repo, wf_id)
    return {str(tool_id): name for tool_id, name in (sample.get("logical") or {}).items()}


def build(repo: Repo, samples_dir: Path, wf_id: str, *, force: bool = False) -> dict:
    """seed -> parse -> segment -> simulate. Returns `{"parse", "segments", "golden_sets"}`.

    A parse that trips the invariants check (`INVARIANT_VIOLATION`, e.g. `wf_0005`'s unknown
    vendor plugin) is the simulator's designed refusal trigger, not a build failure: segmentation
    and simulation are skipped, `golden_sets` comes back `[]`, and the caller gets `PARSED`'s
    sibling status back to act on -- nothing is raised. A parse that fails outright (`FAILED`,
    `QUARANTINED`) or a segmentation whose group graph has a cycle are real failures and raise
    `BuildError`.

    `build` owns `golden/intermediates/` and `golden/outputs/` outright, on top of what `seed`
    already owns: both are reset before parsing even runs, so a workflow whose parse now trips the
    invariants check (or whose segmentation renamed its segments) never keeps derived files from
    an earlier, different run -- including one whose build used to succeed and no longer does.

    `force` is forwarded to `seed`: without it, `build` refuses the same way `seed` does when
    `workflows/<wf_id>/manifest.json` already exists (coordinator ruling F1e).
    """
    sample = _read_sample(samples_dir, wf_id)
    seed(repo, samples_dir, wf_id, force=force)
    _reset_dir(repo, wf_id, "golden", "intermediates")
    _reset_dir(repo, wf_id, "golden", "outputs")

    report = parse.run(repo, wf_id, check=True)
    result: dict = {"parse": report, "segments": [], "golden_sets": []}
    if report["status"] not in ("PARSED", "RECOVERED", "INVARIANT_VIOLATION"):
        raise BuildError(f"{wf_id}: parse status {report['status']}: "
                         f"{'; '.join(report['errors']) or 'no further detail'}")
    if report["status"] == "INVARIANT_VIOLATION":
        return result  # designed trigger (wf_0005): report it and carry on, nothing more to build

    seg_result = segment.run(repo, wf_id)
    result["segments"] = sorted(seg_result["segments"])
    if any("cycle" in warning for warning in seg_result["warnings"]):
        raise BuildError(f"{wf_id}: segmentation could not order its group graph: "
                         f"{'; '.join(seg_result['warnings'])}")

    result["golden_sets"] = alteryx_sim.run(repo, wf_id, GOLDEN_SETS, _logical_by_tool(repo, wf_id, sample))
    return result


# --- CLI ---

def _samples_dir(repo: Repo, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else repo.root / path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["seed", "build"], help="seed only, or seed+parse+segment+simulate")
    parser.add_argument("--only", default=None, help="one sample workflow id, e.g. wf_0001")
    parser.add_argument("--samples", default="samples", help="samples directory (default: samples)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite a workflow that already has a manifest.json "
                             "(default: refuse and leave it untouched)")
    add_root_arg(parser)
    args = parser.parse_args(argv)

    repo = Repo(args.root)
    samples_dir = _samples_dir(repo, args.samples)
    if not samples_dir.is_dir():
        parser.error(f"samples directory not found: {samples_dir}")  # exit 2: nothing created
    available = sorted(p.name for p in samples_dir.iterdir() if p.is_dir() and p.name.startswith("wf_"))
    if args.only is not None and args.only not in available:
        parser.error(f"unknown sample workflow {args.only!r}: not found under {samples_dir}")
    wf_ids = [args.only] if args.only is not None else available

    action = seed if args.action == "seed" else build
    failed = False
    for wf_id in wf_ids:
        try:
            result = action(repo, samples_dir, wf_id, force=args.force)
        except BuildError as exc:
            print(str(exc), file=sys.stderr)
            failed = True
            continue
        except Exception:  # exit 2: a crash outside the checks above
            traceback.print_exc()
            return 2
        if args.action == "seed":
            print(f"{wf_id}: seeded ({result['source']['engine']})")
        else:
            sets = ", ".join(result["golden_sets"]) or "(none)"
            print(f"{wf_id}: parse {result['parse']['status']}, "
                  f"{len(result['segments'])} segments, golden sets {sets}")
    return 1 if failed else 0


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
