"""The parser regression corpus: one directory per Alteryx variant the parser must survive.

Every fixture is a tiny synthetic workflow written by hand to `docs/reference/dag-contract.md`;
**no file here was produced or opened by Alteryx**. The corpus is the permanent gate for the
`parser-recovery` agent (program spec §6.4): a new extension must add a directory here and a test,
and every earlier fixture must keep passing.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

import invariants
import parse
from lib.io import read_json
from lib.paths import Repo

CORPUS = Path(__file__).resolve().parent
FIXTURES = {"bom", "cp1252", "nested_containers", "yxwz", "yxzp", "locked"}
PARSE_CLEAN = ["bom", "cp1252", "nested_containers", "yxwz"]


def workflow_file(name: str) -> Path:
    """The one workflow at the top of a fixture directory (macros sit in sub-directories)."""
    files = sorted(p for p in (CORPUS / name).glob("*.yx*"))
    assert len(files) == 1, f"{name} must hold exactly one top-level workflow, found {files}"
    return files[0]


def repo_with(tmp_path: Path, wf_id: str, files: dict[str, Path]) -> Repo:
    """A tmp repo whose workflows/<wf_id>/source/ holds `files` ({relative name: source path})."""
    repo = Repo(tmp_path)
    src = repo.wf(wf_id, "source")
    for name, path in files.items():
        dest = src / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, dest)
    return repo


@pytest.mark.parametrize("name", PARSE_CLEAN)
def test_fixture_parses_and_satisfies_every_invariant(name):
    path = workflow_file(name)
    dag, _ = parse.parse_file(path)
    assert dag["nodes"], f"{name} parsed to no nodes"
    assert invariants.check(parse.decode_xml(path.read_bytes()), dag) == []


def test_the_bom_fixture_really_starts_with_a_utf8_bom():
    path = workflow_file("bom")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    dag, _ = parse.parse_file(path)
    assert dag["yxmd_version"] == "2023.1"
    assert [n["type"] for n in dag["nodes"]] == ["input", "select", "formula", "output"]


def test_the_cp1252_fixture_is_not_valid_utf8_and_keeps_its_accent():
    path = workflow_file("cp1252")
    raw = path.read_bytes()
    assert b"\xe9" in raw and not raw.startswith(b"<?xml")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    dag, _ = parse.parse_file(path)
    annotations = [n["annotation"] for n in dag["nodes"]]
    assert "Contrôle des données" in annotations


def test_the_yxwz_fixture_is_an_analytic_app_with_interface_and_action_tools():
    path = workflow_file("yxwz")
    dag, _ = parse.parse_file(path)
    kinds = {n["tool_id"]: n["type"] for n in dag["nodes"]}
    assert dag["file_kind"] == "yxwz"
    assert kinds["10"] == "interface" and kinds["11"] == "action"
    wired = {e["src"] for e in dag["edges"]} | {e["dst"] for e in dag["edges"]}
    assert wired.isdisjoint({"10", "11"})


def test_the_nested_containers_fixture_is_three_containers_deep():
    dag, _ = parse.parse_file(workflow_file("nested_containers"))
    nodes = {n["tool_id"]: n for n in dag["nodes"]}
    assert nodes["120"]["container_id"] == "110" and nodes["110"]["container_id"] == "100"
    assert nodes["100"]["container_id"] is None
    assert nodes["1"]["container_id"] == "120" and nodes["3"]["container_id"] is None


def test_a_yxzp_package_is_unzipped_in_place_then_parsed(tmp_path):
    package = CORPUS / "yxzp" / "package"
    repo = Repo(tmp_path)
    src = repo.wf("wf_zip", "source")
    src.mkdir(parents=True)
    with zipfile.ZipFile(src / "nightly.yxzp", "w") as zf:
        for path in sorted(package.rglob("*.yx*")):
            zf.write(path, path.relative_to(package).as_posix())
    report = parse.run(repo, "wf_zip", check=True)
    assert report["status"] == "PARSED", report["errors"]
    assert (src / "nightly.yxmd").exists() and (src / "Supporting_Macros" / "trim_codes.yxmc").exists()
    dag = read_json(repo.wf("wf_zip", "parsed", "dag.json"))
    macro = next(n for n in dag["nodes"] if n["type"] == "macro")
    assert macro["unresolved"] is False and len(macro["sub_dag"]["nodes"]) == 3


def test_a_package_may_not_write_outside_the_source_directory(tmp_path):
    """A .yxzp is an untrusted archive: a member that escapes source/ fails the parse."""
    repo = Repo(tmp_path)
    src = repo.wf("wf_evil", "source")
    src.mkdir(parents=True)
    with zipfile.ZipFile(src / "evil.yxzp", "w") as zf:
        zf.writestr("../../escaped.yxmd", "<AlteryxDocument/>")
    report = parse.run(repo, "wf_evil", check=True)
    assert report["status"] == "FAILED" and "outside source/" in report["errors"][0]
    assert not (tmp_path / "workflows" / "escaped.yxmd").exists()


def test_a_locked_workflow_is_quarantined(tmp_path):
    repo = repo_with(tmp_path, "wf_locked", {"locked.yxmd": workflow_file("locked")})
    report = parse.run(repo, "wf_locked", check=True)
    assert report["status"] == "QUARANTINED" and report["reason"] == "locked"
    assert not repo.wf("wf_locked", "parsed", "dag.json").exists()
    assert parse.main(["wf_locked", "--root", str(tmp_path)]) == 1


def test_every_fixture_directory_is_covered_and_documented():
    found = {p.name for p in CORPUS.iterdir() if p.is_dir() and not p.name.startswith("__")}
    assert found == FIXTURES
    for name in sorted(FIXTURES):
        assert (CORPUS / name / "README.md").read_text(encoding="utf-8").strip(), name
