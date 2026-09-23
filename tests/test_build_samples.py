"""Sample seeding and simulated golden-set builder tests (plan task 9).

`seed`/`build` are the pipeline's own entry point: every end-to-end and intake test starts from
what they produce, by chaining the parser, the segmenter and the Alteryx simulator over one
`samples/wf_000N/` fixture. The first five tests are the brief's Step 1 verbatim; the rest cover
behaviour the brief describes in prose but doesn't test directly (manifest preservation, the
`targets_before` copy, and the CLI's own exit codes -- implementer-rules.md's "CLI EXIT CODES").
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import parse
from dev import build_samples
from lib import typed_csv, yxdb
from lib.io import read_json, write_json
from lib.paths import Repo

SAMPLES = Path(__file__).parents[1] / "samples"


def _copy_sample(tmp_path: Path, wf_id: str) -> Path:
    """A private, mutable copy of one real sample fixture, so a test can edit the fixture (remove
    a golden input, rename a source file, change segmentation...) without ever touching the real
    `samples/` tree. Returns the samples directory containing it."""
    samples_dir = tmp_path / "samples_copy"
    shutil.copytree(SAMPLES / wf_id, samples_dir / wf_id)
    return samples_dir


# --- brief Step 1, verbatim ---

def test_seed_writes_yxdb_and_manifest(tmp_path):
    repo = Repo(tmp_path)
    manifest = build_samples.seed(repo, SAMPLES, "wf_0001")

    header = yxdb.read_header(repo.wf("wf_0001", "source", "data", "orders.yxdb"))
    normal = typed_csv.read_table(SAMPLES / "wf_0001" / "golden_inputs" / "normal" / "1.csv")
    assert len(header.fields) == 7
    assert header.num_records == len(normal["rows"])
    assert manifest["source"]["engine"] == "AMP"


def test_build_wf_0003_yields_two_segments_and_golden_files(tmp_path):
    repo = Repo(tmp_path)
    result = build_samples.build(repo, SAMPLES, "wf_0003")

    assert result["segments"] == ["seg_01", "seg_02"]
    assert repo.wf("wf_0003", "golden", "intermediates", "seg_01", "normal", "3_Output.csv").is_file()
    assert repo.wf("wf_0003", "golden", "outputs", "edge", "10.csv").is_file()


def test_empty_set_has_zero_rows_and_full_schema(tmp_path):
    repo = Repo(tmp_path)
    build_samples.build(repo, SAMPLES, "wf_0001")

    for tool_id in ("7", "8"):
        table = typed_csv.read_table(repo.wf("wf_0001", "golden", "outputs", "empty", f"{tool_id}.csv"))
        assert table["rows"] == []
        assert len(table["fields"]) > 0


def test_build_wf_0005_reports_no_golden_sets_without_raising(tmp_path):
    repo = Repo(tmp_path)
    result = build_samples.build(repo, SAMPLES, "wf_0005")

    assert result["parse"]["status"] == "INVARIANT_VIOLATION"
    assert result["segments"] == []
    assert result["golden_sets"] == []
    assert not repo.wf("wf_0005", "segments").exists()


def test_build_twice_is_byte_identical(tmp_path):
    repo = Repo(tmp_path)
    build_samples.build(repo, SAMPLES, "wf_0001")
    golden_before = _snapshot(repo.wf("wf_0001", "golden"))
    yxdb_before = _snapshot(repo.wf("wf_0001", "source", "data"))

    build_samples.build(repo, SAMPLES, "wf_0001", force=True)

    assert golden_before and golden_before == _snapshot(repo.wf("wf_0001", "golden"))
    assert yxdb_before and yxdb_before == _snapshot(repo.wf("wf_0001", "source", "data"))


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# --- additional coverage: prose-described behaviour the brief's own tests don't exercise ---

def test_seed_copies_targets_before(tmp_path):
    repo = Repo(tmp_path)
    build_samples.seed(repo, SAMPLES, "wf_0003")

    assert repo.wf("wf_0003", "golden", "targets_before", "normal", "GL_SUMMARY.csv").is_file()
    assert repo.wf("wf_0003", "golden", "inputs", "normal", "1.csv").is_file()


def test_seed_never_overwrites_existing_status_metrics_answers_or_accepted_diffs(tmp_path):
    repo = Repo(tmp_path)
    build_samples.seed(repo, SAMPLES, "wf_0001")
    manifest_path = repo.wf("wf_0001", "manifest.json")
    manifest = read_json(manifest_path)
    manifest["status"] = {"parse": "PARSED", "intake": "READY"}
    manifest["metrics"] = {"translator": {"toolCalls": 3}}
    manifest["answers"] = {"Q1": "FINANCE.RAW.GL_LEDGER"}
    manifest["accepted_diffs"] = [{"segment": "seg_01", "class": "ROUNDING"}]
    write_json(manifest_path, manifest)

    updated = build_samples.seed(repo, SAMPLES, "wf_0001", force=True)

    assert updated["status"] == {"parse": "PARSED", "intake": "READY"}
    assert updated["metrics"] == {"translator": {"toolCalls": 3}}
    assert updated["answers"] == {"Q1": "FINANCE.RAW.GL_LEDGER"}
    assert updated["accepted_diffs"] == [{"segment": "seg_01", "class": "ROUNDING"}]
    assert updated["source"]["engine"] == "AMP"  # source/segmentation are still refreshed


def test_seed_defaults_status_and_metrics_when_no_manifest_exists(tmp_path):
    repo = Repo(tmp_path)
    manifest = build_samples.seed(repo, SAMPLES, "wf_0001")

    assert manifest["status"] == {} and manifest["metrics"] == {}


def test_seed_copies_output_target_into_manifest_only_when_the_sample_carries_it(tmp_path):
    samples_dir = _copy_sample(tmp_path, "wf_0001")
    sample_path = samples_dir / "wf_0001" / "sample.json"
    sample = read_json(sample_path)
    sample["output_target"] = "dbt"
    write_json(sample_path, sample)
    repo = Repo(tmp_path / "repo")

    manifest = build_samples.seed(repo, samples_dir, "wf_0001")

    assert manifest["output_target"] == "dbt"

    repo_without = Repo(tmp_path / "repo_without")
    manifest_without = build_samples.seed(repo_without, SAMPLES, "wf_0001")

    assert "output_target" not in manifest_without


def test_build_raises_build_error_when_parse_status_is_not_clean(tmp_path, monkeypatch):
    repo = Repo(tmp_path)
    monkeypatch.setattr(parse, "run", lambda repo, wf_id, check:
                        {"status": "FAILED", "errors": ["boom"], "node_count": 0})

    with pytest.raises(build_samples.BuildError, match="FAILED"):
        build_samples.build(repo, SAMPLES, "wf_0001")
    assert not repo.wf("wf_0001", "segments").exists()


@pytest.mark.parametrize("wf_id", ["wf_0001", "wf_0002", "wf_0003", "wf_0004"])
def test_build_every_non_invariant_sample_produces_golden_sets(tmp_path, wf_id):
    """Smoke coverage for the samples the brief's own five tests never touch directly: wf_0002's
    csv (not yxdb) second input and db target, and wf_0004's macro."""
    repo = Repo(tmp_path)
    result = build_samples.build(repo, SAMPLES, wf_id)

    assert result["parse"]["status"] in ("PARSED", "RECOVERED")
    assert result["golden_sets"]
    assert result["segments"]


# --- CLI: exit codes (implementer-rules.md "CLI EXIT CODES") ---

def test_cli_build_only_exits_0_and_writes_artifacts(tmp_path):
    rc = build_samples.main(["build", "--only", "wf_0001", "--samples", str(SAMPLES), "--root", str(tmp_path)])

    assert rc == 0
    repo = Repo(tmp_path)
    assert repo.wf("wf_0001", "manifest.json").is_file()
    assert repo.wf("wf_0001", "parsed", "dag.json").is_file()
    assert repo.wf("wf_0001", "golden", "outputs", "normal", "7.csv").is_file()


def test_cli_seed_only_exits_0_and_does_not_parse(tmp_path):
    rc = build_samples.main(["seed", "--only", "wf_0001", "--samples", str(SAMPLES), "--root", str(tmp_path)])

    assert rc == 0
    repo = Repo(tmp_path)
    assert repo.wf("wf_0001", "source", "data", "orders.yxdb").is_file()
    assert not repo.wf("wf_0001", "parsed", "dag.json").exists()


def test_cli_unknown_only_id_exits_2_and_creates_nothing(tmp_path):
    with pytest.raises(SystemExit) as exc:
        build_samples.main(["build", "--only", "wf_9999", "--samples", str(SAMPLES), "--root", str(tmp_path)])

    assert exc.value.code == 2
    assert not (tmp_path / "workflows").exists()


def test_cli_missing_samples_dir_exits_2_and_creates_nothing(tmp_path):
    with pytest.raises(SystemExit) as exc:
        build_samples.main(["build", "--samples", str(tmp_path / "no_such_dir"), "--root", str(tmp_path)])

    assert exc.value.code == 2
    assert not (tmp_path / "workflows").exists()


def test_cli_no_arguments_exits_2():
    with pytest.raises(SystemExit) as exc:
        build_samples.main([])
    assert exc.value.code == 2


def test_cli_domain_failure_exits_1(tmp_path, capsys):
    broken_samples = tmp_path / "broken_samples"
    broken_wf = broken_samples / "wf_broken"
    broken_wf.mkdir(parents=True)
    write_json(broken_wf / "sample.json",
              {"id": "wf_broken", "schedule": "0 6 * * 1-5", "owner": "x", "segmentation": {}})
    (broken_wf / "source").mkdir()
    (broken_wf / "source" / "broken.yxmd").write_text("not xml", encoding="utf-8")

    rc = build_samples.main(["build", "--only", "wf_broken", "--samples", str(broken_samples),
                            "--root", str(tmp_path)])

    assert rc == 1
    assert "wf_broken" in capsys.readouterr().err


def test_cli_unexpected_exception_exits_2(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")
    monkeypatch.setattr(build_samples, "build", boom)

    rc = build_samples.main(["build", "--only", "wf_0001", "--samples", str(SAMPLES), "--root", str(tmp_path)])

    assert rc == 2


def test_cli_builds_every_sample_when_only_is_omitted(tmp_path):
    rc = build_samples.main(["build", "--samples", str(SAMPLES), "--root", str(tmp_path)])

    assert rc == 0
    repo = Repo(tmp_path)
    for wf_id in ("wf_0001", "wf_0002", "wf_0003", "wf_0004", "wf_0005"):
        assert repo.wf(wf_id, "manifest.json").is_file()


def test_samples_dir_resolves_relative_paths_against_root_and_keeps_absolute_ones(tmp_path):
    repo = Repo(tmp_path)
    assert build_samples._samples_dir(repo, "samples") == repo.root / "samples"
    assert build_samples._samples_dir(repo, str(SAMPLES)) == SAMPLES


# --- Fix round 1: reseed/rebuild replace the subtrees seed/build own, wholesale (review finding) ---
#
# Every test below edits a private tmp_path copy of a real fixture (`_copy_sample`) between two
# seed/build calls, so a shrunk or restructured sample can never leave its old files behind under
# a directory this script owns.

def test_reseed_removes_a_deleted_golden_input_csv(tmp_path):
    samples_dir = _copy_sample(tmp_path, "wf_0002")
    repo = Repo(tmp_path / "repo")
    build_samples.seed(repo, samples_dir, "wf_0002")
    assert repo.wf("wf_0002", "golden", "inputs", "edge", "3.csv").is_file()

    (samples_dir / "wf_0002" / "golden_inputs" / "edge" / "3.csv").unlink()
    (samples_dir / "wf_0002" / "golden_inputs" / "edge" / "3.schema.json").unlink()
    build_samples.seed(repo, samples_dir, "wf_0002", force=True)

    assert not repo.wf("wf_0002", "golden", "inputs", "edge", "3.csv").exists()
    assert repo.wf("wf_0002", "golden", "inputs", "edge", "1.csv").is_file()  # untouched sibling


def test_reseed_removes_a_dropped_golden_set_folder(tmp_path):
    samples_dir = _copy_sample(tmp_path, "wf_0002")
    repo = Repo(tmp_path / "repo")
    build_samples.seed(repo, samples_dir, "wf_0002")
    assert repo.wf("wf_0002", "golden", "inputs", "edge").is_dir()

    shutil.rmtree(samples_dir / "wf_0002" / "golden_inputs" / "edge")
    build_samples.seed(repo, samples_dir, "wf_0002", force=True)

    assert not repo.wf("wf_0002", "golden", "inputs", "edge").exists()
    assert repo.wf("wf_0002", "golden", "inputs", "normal").is_dir()  # sibling set is untouched


def test_reseed_removes_a_renamed_source_file(tmp_path):
    samples_dir = _copy_sample(tmp_path, "wf_0001")
    repo = Repo(tmp_path / "repo")
    build_samples.seed(repo, samples_dir, "wf_0001")
    assert repo.wf("wf_0001", "source", "sales_summary.yxmd").is_file()

    old = samples_dir / "wf_0001" / "source" / "sales_summary.yxmd"
    old.rename(old.with_name("renamed.yxmd"))
    manifest = build_samples.seed(repo, samples_dir, "wf_0001", force=True)

    assert not repo.wf("wf_0001", "source", "sales_summary.yxmd").exists()
    assert repo.wf("wf_0001", "source", "renamed.yxmd").is_file()
    assert manifest["source"]["file"] == "renamed.yxmd"


def test_reseed_removes_a_yxdb_whose_input_basename_changed(tmp_path):
    samples_dir = _copy_sample(tmp_path, "wf_0001")
    repo = Repo(tmp_path / "repo")
    build_samples.seed(repo, samples_dir, "wf_0001")
    assert repo.wf("wf_0001", "source", "data", "orders.yxdb").is_file()

    yxmd = samples_dir / "wf_0001" / "source" / "sales_summary.yxmd"
    yxmd.write_text(yxmd.read_text(encoding="utf-8").replace("orders.yxdb", "orders_v2.yxdb"),
                    encoding="utf-8")
    build_samples.seed(repo, samples_dir, "wf_0001", force=True)

    assert not repo.wf("wf_0001", "source", "data", "orders.yxdb").exists()
    assert repo.wf("wf_0001", "source", "data", "orders_v2.yxdb").is_file()


def test_rebuild_removes_a_stale_segment_after_a_segmentation_change(tmp_path):
    samples_dir = _copy_sample(tmp_path, "wf_0003")
    repo = Repo(tmp_path / "repo")
    sample_path = samples_dir / "wf_0003" / "sample.json"
    sample = read_json(sample_path)

    # min_tools=1/max_tools=3 splits wf_0003 into three segments, the middle one (seg_02, tools
    # 4-9) with real outbound intermediate files (verified by hand: golden/intermediates/seg_02
    # holds a "9_Output.csv" per set) -- unlike the default segmentation's seg_02, which is the
    # workflow's terminal segment and has no outbound stream at all.
    sample["segmentation"] = {"min_tools": 1, "max_tools": 3}
    write_json(sample_path, sample)
    first = build_samples.build(repo, samples_dir, "wf_0003")
    assert first["segments"] == ["seg_01", "seg_02", "seg_03"]
    assert repo.wf("wf_0003", "golden", "intermediates", "seg_02", "normal", "9_Output.csv").is_file()

    sample["segmentation"] = {"min_tools": 40, "max_tools": 40}  # collapses to a single segment
    write_json(sample_path, sample)
    second = build_samples.build(repo, samples_dir, "wf_0003", force=True)

    assert second["segments"] == ["seg_01"]
    assert not repo.wf("wf_0003", "golden", "intermediates", "seg_02").exists()
    assert not repo.wf("wf_0003", "golden", "intermediates", "seg_03").exists()


def test_build_wf_0005_leaves_no_stale_intermediates_or_outputs_from_an_earlier_state(tmp_path):
    """A workflow whose build short-circuits on INVARIANT_VIOLATION (wf_0005's design) must not
    leave behind `golden/intermediates`/`golden/outputs` from some earlier state -- e.g. a
    previous successful build under a since-changed fixture, or a hand run of the simulator.
    Both directories are `build`'s own, not `seed`'s, so seeding alone must not be relied on to
    clean them; the stale files are planted directly rather than by contriving a fixture that
    both builds cleanly and later trips the invariant, which is a second, unrelated concern."""
    samples_dir = _copy_sample(tmp_path, "wf_0005")
    repo = Repo(tmp_path / "repo")
    build_samples.seed(repo, samples_dir, "wf_0005")
    stale_intermediate = repo.wf("wf_0005", "golden", "intermediates", "seg_01", "normal", "1_Output.csv")
    stale_intermediate.parent.mkdir(parents=True)
    stale_intermediate.write_text("A\n1\n", encoding="utf-8")
    stale_output = repo.wf("wf_0005", "golden", "outputs", "normal", "4.csv")
    stale_output.parent.mkdir(parents=True)
    stale_output.write_text("A\n1\n", encoding="utf-8")

    result = build_samples.build(repo, samples_dir, "wf_0005", force=True)

    assert result["parse"]["status"] == "INVARIANT_VIOLATION"
    assert not repo.wf("wf_0005", "golden", "intermediates").exists()
    assert not repo.wf("wf_0005", "golden", "outputs").exists()


def test_reseed_leaves_files_it_does_not_own_untouched(tmp_path):
    samples_dir = _copy_sample(tmp_path, "wf_0001")
    repo = Repo(tmp_path / "repo")
    build_samples.seed(repo, samples_dir, "wf_0001")

    manifest_path = repo.wf("wf_0001", "manifest.json")
    manifest = read_json(manifest_path)
    manifest["answers"] = {"Q1": "FINANCE.RAW.GL_LEDGER"}
    write_json(manifest_path, manifest)

    mappings_path = repo.wf("wf_0001", "intake", "mappings.yaml")
    mappings_path.parent.mkdir(parents=True, exist_ok=True)
    mappings_path.write_text("sources: {}\n", encoding="utf-8")

    proc_path = repo.wf("wf_0001", "segments", "seg_01", "proc.sql")
    proc_path.parent.mkdir(parents=True, exist_ok=True)
    proc_path.write_text("-- hand written procedure\n", encoding="utf-8")

    build_samples.seed(repo, samples_dir, "wf_0001", force=True)

    assert read_json(manifest_path)["answers"] == {"Q1": "FINANCE.RAW.GL_LEDGER"}
    assert mappings_path.read_text(encoding="utf-8") == "sources: {}\n"
    assert proc_path.read_text(encoding="utf-8") == "-- hand written procedure\n"


def test_reset_dir_refuses_to_delete_outside_the_workflow_folder(tmp_path):
    # Fix round 3: lib.paths.Repo.wf now rejects a ".." *parts component outright (rule A), so
    # this particular construction dies there (ValueError) before _reset_dir's own resolve-and-
    # contain check would even run. It stays a useful regression test for the outcome (nothing
    # escapes); test_reset_dir_ownership_check_works_even_if_validate_part_does_not below proves
    # _reset_dir's own check is independently correct, not merely shadowed by rule A.
    repo = Repo(tmp_path)
    with pytest.raises((ValueError, build_samples.BuildError)):
        build_samples._reset_dir(repo, "wf_0001", "source", "..", "..", "escaped")
    assert not (tmp_path / "workflows" / "escaped").exists()


def test_reset_dir_is_a_no_op_when_the_target_does_not_exist(tmp_path):
    repo = Repo(tmp_path)
    result = build_samples._reset_dir(repo, "wf_0001", "golden", "inputs")
    assert result == repo.wf("wf_0001", "golden", "inputs")
    assert not result.exists()


# --- Fix round 2 (task 9 re-review, CRITICAL): wf_id path traversal defeated _reset_dir's guard
# ---
#
# `Repo.wf("..")` used to collapse back to the repo root itself (root/workflows/wf_id/.. is root
# when wf_id is ".."), moving `_reset_dir`'s own trusted anchor. `lib.paths.Repo.wf`/`Repo.seg`
# now reject a `wf_id`/`seg` that isn't `^[A-Za-z0-9][A-Za-z0-9_-]*$` outright (tested in
# tests/test_foundations.py); the tests below cover the two further, independent defences the
# ruling added on top of that: `_reset_dir`'s owned-top-level allowlist and zero-parts refusal,
# and `seed`'s validate-the-whole-fixture-before-deleting-anything ordering.

def test_reset_dir_refuses_zero_parts(tmp_path):
    repo = Repo(tmp_path)
    with pytest.raises(build_samples.BuildError):
        build_samples._reset_dir(repo, "wf_0001")


def test_reset_dir_refuses_a_directory_it_does_not_own(tmp_path):
    repo = Repo(tmp_path)
    with pytest.raises(build_samples.BuildError):
        build_samples._reset_dir(repo, "wf_0001", "intake")
    with pytest.raises(build_samples.BuildError):
        build_samples._reset_dir(repo, "wf_0001", "segments")
    with pytest.raises(build_samples.BuildError):
        build_samples._reset_dir(repo, "wf_0001", "golden", "capture_map.json")


def test_wf_id_path_traversal_is_rejected_before_any_deletion(tmp_path):
    """The exploit the re-reviewer reproduced live: with wf_id=".." (which used to collapse
    `repo.wf("..")` back to the repo root), a decoy `sample.json` + `source/` placed at `<root>`
    (i.e. `samples_dir/..`) used to let `seed` delete `<root>/source` outright. The decoy fixture
    here is a full, otherwise-valid copy of wf_0001 -- one that would sail through every content
    check `seed` now runs -- to prove the id itself is what gets rejected, not merely that an
    incomplete decoy tripped some other check first."""
    root = tmp_path / "root"
    samples_dir = root / "samples"
    samples_dir.mkdir(parents=True)
    shutil.copytree(SAMPLES / "wf_0001" / "source", root / "source")
    shutil.copytree(SAMPLES / "wf_0001" / "golden_inputs", root / "golden_inputs")
    shutil.copy(SAMPLES / "wf_0001" / "sample.json", root / "sample.json")
    marker = (root / "source" / "sales_summary.yxmd").read_bytes()

    repo = Repo(root)
    with pytest.raises((ValueError, build_samples.BuildError)):
        build_samples.seed(repo, samples_dir, "..")

    assert (root / "source" / "sales_summary.yxmd").is_file()
    assert (root / "source" / "sales_summary.yxmd").read_bytes() == marker


def test_reseed_leaves_prior_state_intact_when_sample_json_is_malformed(tmp_path):
    samples_dir = _copy_sample(tmp_path, "wf_0001")
    repo = Repo(tmp_path / "repo")
    build_samples.seed(repo, samples_dir, "wf_0001")
    assert repo.wf("wf_0001", "source", "sales_summary.yxmd").is_file()
    assert repo.wf("wf_0001", "golden", "inputs", "normal", "1.csv").is_file()

    (samples_dir / "wf_0001" / "sample.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(build_samples.BuildError, match="cannot parse"):
        build_samples.seed(repo, samples_dir, "wf_0001", force=True)

    assert repo.wf("wf_0001", "source", "sales_summary.yxmd").is_file()
    assert repo.wf("wf_0001", "golden", "inputs", "normal", "1.csv").is_file()


def test_reseed_leaves_prior_state_intact_when_a_yxdb_input_csv_is_missing(tmp_path):
    samples_dir = _copy_sample(tmp_path, "wf_0001")
    repo = Repo(tmp_path / "repo")
    build_samples.seed(repo, samples_dir, "wf_0001")
    assert repo.wf("wf_0001", "source", "data", "orders.yxdb").is_file()
    assert repo.wf("wf_0001", "golden", "inputs", "normal", "1.csv").is_file()

    (samples_dir / "wf_0001" / "golden_inputs" / "normal" / "1.csv").unlink()
    (samples_dir / "wf_0001" / "golden_inputs" / "normal" / "1.schema.json").unlink()

    with pytest.raises(build_samples.BuildError, match="golden_inputs/normal"):
        build_samples.seed(repo, samples_dir, "wf_0001", force=True)

    assert repo.wf("wf_0001", "source", "data", "orders.yxdb").is_file()
    assert repo.wf("wf_0001", "golden", "inputs", "normal", "1.csv").is_file()


# --- Fix round 3 (task 9 re-review): the owned-directory allowlist judged literal `parts` -------
#
# `_reset_dir(repo, wf_id, "golden", "inputs", "..", "..", "intake")` passed round 2's allowlist
# (`parts[0]` == "golden", `parts[1]` == "inputs" are both legal names) and the resolve-and-
# contain check (the resolved target is still strictly inside `wf_root`, just not inside
# `golden/inputs`) -- reproduced live by the re-reviewer. `_reset_dir` stopped string-matching
# `parts[0]`/`parts[1]` then; since round 4 it judges ownership on the *lexical* path (resolving
# was what let a linked owned directory pass, below) and refuses any `.`/`..` component in a part
# itself, which is what closes this particular spelling -- a lexical `golden/inputs/../../intake`
# still has `golden/inputs` among its parents, so the dot refusal, not the ownership comparison,
# is what stops it. The outcome these tests assert is unchanged: nothing is deleted.

def test_reset_dir_rejects_dot_crafted_paths_to_unowned_siblings(tmp_path):
    """The two live reproductions from the finding, run for real (rule A and rule B both active):
    rule A's `_validate_part` rejects the literal ".." component before `_reset_dir`'s own
    ownership check would even run, but either defence stops the deletion."""
    repo = Repo(tmp_path)
    intake = repo.wf("wf_0001", "intake")
    intake.mkdir(parents=True)
    (intake / "mappings.yaml").write_text("sources: {}\n", encoding="utf-8")
    segments = repo.wf("wf_0001", "segments")
    segments.mkdir(parents=True)
    (segments / "seg_01").mkdir()

    with pytest.raises((ValueError, build_samples.BuildError)):
        build_samples._reset_dir(repo, "wf_0001", "golden", "inputs", "..", "..", "intake")
    assert (intake / "mappings.yaml").exists()

    with pytest.raises((ValueError, build_samples.BuildError)):
        build_samples._reset_dir(repo, "wf_0001", "golden", "inputs", "..", "..", "segments")
    assert segments.exists() and (segments / "seg_01").exists()


def test_reset_dir_ownership_check_works_even_if_validate_part_does_not(tmp_path, monkeypatch):
    """Proves `_reset_dir`'s own refusal (rule B) is independently correct, not merely shadowed
    by `lib.paths._validate_part`'s dot-component rejection (rule A): monkeypatches
    `_validate_part` to a no-op so the same `..`-crafted parts from the finding reach
    `_reset_dir`'s own logic, unfiltered. Since round 4 the check that fires is `_reset_dir`'s own
    copy of the dot-component refusal (a path spelled with `..` is not one of the directories
    seed/build own, whatever it happens to reach); the assertions are unchanged."""
    import lib.paths as paths_module
    monkeypatch.setattr(paths_module, "_validate_part", lambda value: value)

    repo = Repo(tmp_path)
    intake = repo.wf("wf_0001", "intake")
    intake.mkdir(parents=True)
    (intake / "mappings.yaml").write_text("sources: {}\n", encoding="utf-8")
    segments = repo.wf("wf_0001", "segments")
    segments.mkdir(parents=True)
    (segments / "seg_01").mkdir()

    with pytest.raises(build_samples.BuildError, match="not one of the directories"):
        build_samples._reset_dir(repo, "wf_0001", "golden", "inputs", "..", "..", "intake")
    assert (intake / "mappings.yaml").exists()

    with pytest.raises(build_samples.BuildError, match="not one of the directories"):
        build_samples._reset_dir(repo, "wf_0001", "golden", "inputs", "..", "..", "segments")
    assert segments.exists() and (segments / "seg_01").exists()


def test_reset_dir_allows_a_legitimate_nested_target(tmp_path):
    repo = Repo(tmp_path)
    target = repo.wf("wf_0001", "golden", "inputs", "normal")
    target.mkdir(parents=True)
    (target / "1.csv").write_text("A\n1\n", encoding="utf-8")

    result = build_samples._reset_dir(repo, "wf_0001", "golden", "inputs", "normal")

    assert result == target
    assert not target.exists()
    assert repo.wf("wf_0001", "golden", "inputs").is_dir()  # only the nested target was removed


# --- Fix round 3 (task 9 re-review): samples_dir nested inside the workflow folder -------------
#
# With `samples_dir` under `workflows/<wf_id>/source/`, `seed`'s own `_reset_dir(repo, wf_id,
# "source")` used to delete the very fixture it was reading from partway through, and the
# `_copy_tree` right after it then crashed with an uncaught `FileNotFoundError`. `seed`/`build`
# now refuse this up front, before deleting anything.

def _write_fixture(fixture_dir: Path, wf_id: str) -> None:
    shutil.copytree(SAMPLES / wf_id / "source", fixture_dir / "source")
    shutil.copytree(SAMPLES / wf_id / "golden_inputs", fixture_dir / "golden_inputs")
    shutil.copy(SAMPLES / wf_id / "sample.json", fixture_dir / "sample.json")


def test_seed_refuses_a_samples_dir_nested_inside_the_workflow_folder(tmp_path):
    repo = Repo(tmp_path)
    nested_samples_dir = repo.wf("wf_0001", "source", "samples")
    nested_samples_dir.mkdir(parents=True)
    fixture_dir = nested_samples_dir / "wf_0001"
    _write_fixture(fixture_dir, "wf_0001")
    marker = (fixture_dir / "source" / "sales_summary.yxmd").read_bytes()

    with pytest.raises(build_samples.BuildError, match="inside"):
        build_samples.seed(repo, nested_samples_dir, "wf_0001")

    assert (fixture_dir / "source" / "sales_summary.yxmd").is_file()
    assert (fixture_dir / "source" / "sales_summary.yxmd").read_bytes() == marker


def test_seed_refuses_a_samples_dir_equal_to_the_workflow_folder(tmp_path):
    repo = Repo(tmp_path)
    wf_root = repo.wf("wf_0001")
    wf_root.mkdir(parents=True)
    fixture_dir = wf_root / "wf_0001"
    _write_fixture(fixture_dir, "wf_0001")
    marker = (fixture_dir / "source" / "sales_summary.yxmd").read_bytes()

    with pytest.raises(build_samples.BuildError, match="inside"):
        build_samples.seed(repo, wf_root, "wf_0001")

    assert (fixture_dir / "source" / "sales_summary.yxmd").is_file()
    assert (fixture_dir / "source" / "sales_summary.yxmd").read_bytes() == marker


def test_build_refuses_a_samples_dir_nested_inside_the_workflow_folder(tmp_path):
    """`build` calls `seed` first, so the same refusal protects it without its own copy of the
    check -- confirmed here, and that `build`'s own two later `_reset_dir` calls
    (`golden/intermediates`, `golden/outputs`) never run either."""
    repo = Repo(tmp_path)
    nested_samples_dir = repo.wf("wf_0001", "source", "samples")
    nested_samples_dir.mkdir(parents=True)
    fixture_dir = nested_samples_dir / "wf_0001"
    _write_fixture(fixture_dir, "wf_0001")

    with pytest.raises(build_samples.BuildError, match="inside"):
        build_samples.build(repo, nested_samples_dir, "wf_0001")

    assert (fixture_dir / "source" / "sales_summary.yxmd").is_file()
    assert not repo.wf("wf_0001", "golden", "intermediates").exists()
    assert not repo.wf("wf_0001", "golden", "outputs").exists()


# --- Fix round 4 (task 9 re-review, CRITICAL): deleting THROUGH a directory link ---------------
#
# `_owned_dirs` and `_reset_dir` both used to `.resolve()` their paths. When an owned directory was
# itself a link -- on Windows a directory junction, which any user can create without privileges --
# both sides dereferenced the same reparse point and so AGREED: the ownership check passed, the
# resolve-and-contain check passed (the link's target is inside the workflow folder too), and
# `shutil.rmtree` was then handed the already-DEREFERENCED real path, so rmtree's own refusal to
# act on a link never fired and the link's target (`intake/`) was deleted. `_reset_dir` now walks
# the LEXICAL path one component at a time and refuses any link on it, judges ownership lexically,
# and hands `rmtree` the lexical path.


def _is_dir_link(path: Path) -> bool:
    return os.path.islink(path) or os.path.isjunction(path)


@pytest.fixture
def make_dir_link():
    """Creates directory links under `tmp_path` and, at teardown, removes the LINKS themselves --
    never what they point at -- so neither this fixture nor pytest's own tmp_path clean-up can
    recurse through one. On Windows the default mechanism is a junction (no privilege needed, and
    what the finding was reproduced with); elsewhere, and for the `symlink=True` variant, it is
    `os.symlink(..., target_is_directory=True)`.
    """
    created: list[Path] = []

    def _make(link: Path, target: Path, *, symlink: bool = False) -> Path:
        if os.name == "nt" and not symlink:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
        created.append(link)
        return link

    yield _make

    for link in reversed(created):
        try:
            os.rmdir(link)  # removes the link itself; never follows it
        except OSError:
            try:
                os.unlink(link)  # a POSIX directory symlink
            except OSError:
                pass


def _workflow_with_intake(repo: Repo) -> Path:
    """`workflows/wf_0001/` with an `intake/` that `seed`/`build` do not own, `golden/` in place,
    and a sentinel file whose survival every test below asserts."""
    intake = repo.wf("wf_0001", "intake")
    (intake / "inputs").mkdir(parents=True)
    (intake / "mappings.yaml").write_text("sources: {}\n", encoding="utf-8")
    (intake / "inputs" / "keep.txt").write_text("keep\n", encoding="utf-8")
    repo.wf("wf_0001", "golden").mkdir(parents=True)
    return intake


def test_reset_dir_refuses_an_owned_dir_that_is_a_junction_to_a_sibling(tmp_path, make_dir_link):
    """The re-reviewer's reproduction: `golden/inputs` is a junction pointing at `intake/`, which
    `seed`/`build` do not own. Both the old ownership check and the old containment check
    dereferenced it and agreed, and `intake/mappings.yaml` was deleted."""
    repo = Repo(tmp_path)
    intake = _workflow_with_intake(repo)
    link = make_dir_link(repo.wf("wf_0001", "golden", "inputs"), intake)

    with pytest.raises(build_samples.BuildError, match="is a link") as exc:
        build_samples._reset_dir(repo, "wf_0001", "golden", "inputs")

    assert "inputs" in str(exc.value)
    assert (intake / "mappings.yaml").is_file()
    assert (intake / "inputs" / "keep.txt").is_file()
    assert _is_dir_link(link)  # the link itself is left exactly as it was found


def test_reset_dir_refuses_a_junction_at_an_intermediate_component(tmp_path, make_dir_link):
    """The link need not be the target: with `golden/` itself a junction to `intake/`, the target
    `golden/inputs` lexically names an owned directory but reaches `intake/inputs`."""
    repo = Repo(tmp_path)
    intake = _workflow_with_intake(repo)
    repo.wf("wf_0001", "golden").rmdir()
    link = make_dir_link(repo.wf("wf_0001", "golden"), intake)

    with pytest.raises(build_samples.BuildError, match="is a link") as exc:
        build_samples._reset_dir(repo, "wf_0001", "golden", "inputs")

    assert "golden" in str(exc.value)
    assert (intake / "inputs" / "keep.txt").is_file()
    assert (intake / "mappings.yaml").is_file()
    assert _is_dir_link(link)


def test_reset_dir_refuses_a_junction_pointing_outside_the_workflow(tmp_path, make_dir_link):
    repo = Repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("keep\n", encoding="utf-8")
    repo.wf("wf_0001").mkdir(parents=True)
    link = make_dir_link(repo.wf("wf_0001", "source"), outside)

    with pytest.raises(build_samples.BuildError, match="is a link"):
        build_samples._reset_dir(repo, "wf_0001", "source")

    assert (outside / "sentinel.txt").is_file()
    assert _is_dir_link(link)


def test_reset_dir_refuses_an_owned_dir_that_is_a_symlink(tmp_path, make_dir_link):
    """The true-symlink variant of the first test. Creating a directory symlink on Windows needs
    privileges this machine may not grant, so this one may skip; the junction tests above never do
    (junctions need none), which is why the finding is reproducible for every user."""
    repo = Repo(tmp_path)
    intake = _workflow_with_intake(repo)
    try:
        link = make_dir_link(repo.wf("wf_0001", "golden", "inputs"), intake, symlink=True)
    except OSError as exc:  # Windows without Developer Mode / SeCreateSymbolicLinkPrivilege
        pytest.skip(f"cannot create a directory symlink on this machine: {exc}")

    with pytest.raises(build_samples.BuildError, match="is a link"):
        build_samples._reset_dir(repo, "wf_0001", "golden", "inputs")

    assert (intake / "mappings.yaml").is_file()
    assert _is_dir_link(link)


def test_seed_refuses_a_linked_owned_dir_before_deleting_or_writing_anything(tmp_path, make_dir_link):
    """`seed` runs the same walk over all five owned directories before its first write, so a
    workflow tree with a linked owned directory is refused with everything -- the previously
    seeded `source/`, and the link's target -- left exactly as it was."""
    samples_dir = _copy_sample(tmp_path, "wf_0001")
    repo = Repo(tmp_path / "repo")
    build_samples.seed(repo, samples_dir, "wf_0001")
    source_before = _snapshot(repo.wf("wf_0001", "source"))
    assert source_before

    intake = repo.wf("wf_0001", "intake")
    intake.mkdir(parents=True)
    (intake / "mappings.yaml").write_text("sources: {}\n", encoding="utf-8")
    shutil.rmtree(repo.wf("wf_0001", "golden", "inputs"))
    link = make_dir_link(repo.wf("wf_0001", "golden", "inputs"), intake)

    with pytest.raises(build_samples.BuildError, match="is a link"):
        build_samples.seed(repo, samples_dir, "wf_0001", force=True)

    assert _snapshot(repo.wf("wf_0001", "source")) == source_before
    assert (intake / "mappings.yaml").read_text(encoding="utf-8") == "sources: {}\n"
    assert sorted(p.name for p in intake.iterdir()) == ["mappings.yaml"]  # nothing written through
    assert _is_dir_link(link)


def test_reset_dir_removes_a_link_nested_inside_an_owned_dir_without_following_it(tmp_path, make_dir_link):
    """What rule 2 buys on the inside: `rmtree` is handed the lexical owned directory, and a link
    *nested* in it is removed as the name it is -- the directory it points at keeps its files."""
    repo = Repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("keep\n", encoding="utf-8")
    source = repo.wf("wf_0001", "source")
    source.mkdir(parents=True)
    (source / "sales_summary.yxmd").write_text("<x/>", encoding="utf-8")
    link = make_dir_link(source / "data", outside)

    result = build_samples._reset_dir(repo, "wf_0001", "source")

    assert result == source and not source.exists()
    assert not link.exists()
    assert (outside / "sentinel.txt").is_file()


# --- F1e (coordinator ruling): seed/build refuse to silently overwrite an already-seeded workflow --
#
# `seed`/`build` own several subtrees under `workflows/<wf_id>/` outright and reset them wholesale
# on every call (`_reset_dir`) -- exactly what every "Fix round" test above exercises and relies
# on. That is fine for THIS suite (every test either builds a fresh `tmp_path` once, or
# deliberately reseeds to prove reseeding itself still works correctly -- those now pass
# `force=True` for the deliberate second call). But invoking the CLI against a real,
# already-seeded `workflows/<wf_id>/` a SECOND time by accident -- with no test harness protecting
# anyone -- would silently blow away `source/`/`golden/*` even if a human has since hand-edited a
# golden CSV directly under `workflows/`. `seed` (and `build`, which calls it) now refuses when
# `workflows/<wf_id>/manifest.json` already exists, unless the caller passes `force=True`
# (`--force` on the CLI): `BuildError` naming the workflow, nothing written -- checked before any
# other validation or `_reset_dir` call.

def test_seed_refuses_a_workflow_that_already_has_a_manifest_without_force(tmp_path):
    repo = Repo(tmp_path)
    build_samples.seed(repo, SAMPLES, "wf_0001")
    before = _snapshot(repo.wf("wf_0001"))

    with pytest.raises(build_samples.BuildError, match="wf_0001") as exc:
        build_samples.seed(repo, SAMPLES, "wf_0001")

    assert "force" in str(exc.value).lower()
    assert _snapshot(repo.wf("wf_0001")) == before  # nothing written


def test_seed_force_overwrites_an_already_seeded_workflow(tmp_path):
    repo = Repo(tmp_path)
    build_samples.seed(repo, SAMPLES, "wf_0001")

    manifest = build_samples.seed(repo, SAMPLES, "wf_0001", force=True)

    assert manifest["source"]["engine"] == "AMP"


def test_build_refuses_a_workflow_that_already_has_a_manifest_without_force(tmp_path):
    repo = Repo(tmp_path)
    build_samples.build(repo, SAMPLES, "wf_0001")
    before = _snapshot(repo.wf("wf_0001"))

    with pytest.raises(build_samples.BuildError, match="wf_0001"):
        build_samples.build(repo, SAMPLES, "wf_0001")

    assert _snapshot(repo.wf("wf_0001")) == before


def test_build_force_overwrites_an_already_built_workflow(tmp_path):
    repo = Repo(tmp_path)
    build_samples.build(repo, SAMPLES, "wf_0001")

    result = build_samples.build(repo, SAMPLES, "wf_0001", force=True)

    assert result["parse"]["status"] in ("PARSED", "RECOVERED")


def test_cli_seed_exits_1_and_names_the_workflow_when_manifest_exists_without_force(tmp_path, capsys):
    assert build_samples.main(["seed", "--only", "wf_0001", "--samples", str(SAMPLES),
                               "--root", str(tmp_path)]) == 0

    rc = build_samples.main(["seed", "--only", "wf_0001", "--samples", str(SAMPLES),
                             "--root", str(tmp_path)])

    assert rc == 1
    assert "wf_0001" in capsys.readouterr().err


def test_cli_force_flag_overwrites_an_already_seeded_workflow(tmp_path):
    assert build_samples.main(["seed", "--only", "wf_0001", "--samples", str(SAMPLES),
                               "--root", str(tmp_path)]) == 0

    rc = build_samples.main(["seed", "--force", "--only", "wf_0001", "--samples", str(SAMPLES),
                             "--root", str(tmp_path)])

    assert rc == 0
