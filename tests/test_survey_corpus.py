"""survey_corpus.py: triage a directory of real Alteryx workflow files (task P3).

Every test builds its own tmp corpus directory (a copy of the committed samples' `source/` trees,
or a small synthetic fixture) -- `survey_corpus.survey` never touches `workflows/` or any repo
state, so it can run over a directory nobody prepared for this pipeline at all.
"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

import pytest

import survey_corpus as sc

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
FIXTURES = Path(__file__).resolve().parent / "corpus_fixtures"

# An absolute path of this machine: `C:\...`, `C:/...`, or Git Bash's `/c/...`.
_ABS_PATH_RE = re.compile(r"[a-zA-Z]:[\\/]|(?:^|[\"'])/c/", re.IGNORECASE)

# Whatever samples this worktree actually has -- wf_0007 (the dbt sample) lands in a later task
# and must not make this test suite depend on it existing yet.
SAMPLE_IDS = sorted(p.name for p in SAMPLES.iterdir() if p.is_dir() and (p / "source").is_dir())

_DOC_TEMPLATE = """<?xml version="1.0"?>
<AlteryxDocument yxmdVer="2023.1">
  <Nodes>
{nodes}
  </Nodes>
  <Connections>
{connections}
  </Connections>
  <Properties>
    <Constants />
    <RuntimeProperties><Actions /><Questions /><ModuleType>Wizard</ModuleType><RunE2 value="False" /></RuntimeProperties>
  </Properties>
</AlteryxDocument>
"""

_NODE_TEMPLATE = """    <Node ToolID="{tid}">
      <GuiSettings Plugin="{plugin}">
        <Position x="0" y="0" />
      </GuiSettings>
      <Properties>
        <Configuration />
        <Annotation DisplayMode="0"><Name /><DefaultAnnotationText>{plugin}</DefaultAnnotationText></Annotation>
      </Properties>
      <EngineSettings EngineDll="Dummy.dll" EngineDllEntryPoint="Dummy" />
    </Node>
"""


def _write_workflow(path: Path, plugins: list[str]) -> None:
    """A minimal, hand-written, never-opened-by-Alteryx workflow: one unconnected node per plugin
    string in `plugins` -- enough for `expand`/`classify`/`segment.segment` to run over, and
    exactly as many plugin occurrences as the test asks for."""
    nodes = "".join(_NODE_TEMPLATE.format(tid=i + 1, plugin=p) for i, p in enumerate(plugins))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DOC_TEMPLATE.format(nodes=nodes, connections=""), encoding="utf-8")


def _copy_samples(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    for wf in SAMPLE_IDS:
        shutil.copytree(SAMPLES / wf / "source", corpus / wf / "source")
    return corpus


def _row_for(report: dict, prefix: str) -> dict:
    matches = [row for row in report["workflows"] if row["path"].startswith(prefix)]
    assert len(matches) == 1, f"expected exactly one row starting {prefix!r}, found {matches}"
    return matches[0]


def test_the_committed_samples_survey_as_expected(tmp_path):
    corpus = _copy_samples(tmp_path)
    report = sc.survey(corpus)

    assert len(report["workflows"]) == len(SAMPLE_IDS)
    assert all(row["status"] == "ok" for row in report["workflows"]), report["workflows"]

    wf_0005 = _row_for(report, "wf_0005/")
    assert wf_0005["unknown_plugins"] == ["AcmeAnalytics.Dedupe.DedupeTool"]
    assert wf_0005["tier"] == "T3"

    wf_0006 = _row_for(report, "wf_0006/")
    assert wf_0006["tier"] == "T2"

    if "wf_0007" in SAMPLE_IDS:
        # After Task C lands the dbt sample: every segment sql, every output dbt-expressible, so
        # `--prefer dbt` should propose `output_kind: "dbt"` with no blockers.
        dbt_report = sc.survey(corpus, prefer="dbt")
        wf_0007 = _row_for(dbt_report, "wf_0007/")
        assert wf_0007["output_kind"] == "dbt", wf_0007


def test_an_unknown_plugin_is_listed_by_name_and_counted(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(FIXTURES / "unknown_plugin.yxmd", corpus / "unknown_plugin.yxmd")

    report = sc.survey(corpus)

    assert len(report["workflows"]) == 1
    row = report["workflows"][0]
    assert row["path"] == "unknown_plugin.yxmd"
    assert row["status"] == "ok"
    assert row["unknown_plugins"] == ["ExampleVendor.SentimentScore.SentimentScore"]
    assert row["classes"]["unknown"] == 1

    plugin_row = next(p for p in report["plugins"]
                      if p["plugin"] == "ExampleVendor.SentimentScore.SentimentScore")
    assert plugin_row == {"plugin": "ExampleVendor.SentimentScore.SentimentScore",
                          "type": "unknown", "count": 1}


def test_an_unparsable_file_is_an_error_row_not_a_crash(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "broken.yxmd").write_text("<AlteryxDocument><Nodes>", encoding="utf-8")

    report = sc.survey(corpus)

    assert len(report["workflows"]) == 1
    row = report["workflows"][0]
    assert row["status"] == "error"
    assert row["error"]
    assert "tools" not in row
    assert "classes" not in row


def test_the_plugin_table_is_sorted_by_count_then_name(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # "Vendor.Bbb" and "Vendor.Aaa" tie at count 2: the tie breaks on name, not discovery order.
    _write_workflow(corpus / "one.yxmd", ["Vendor.Ccc.Ccc", "Vendor.Bbb.Bbb"])
    _write_workflow(corpus / "two.yxmd", ["Vendor.Bbb.Bbb", "Vendor.Aaa.Aaa"])
    _write_workflow(corpus / "three.yxmd", ["Vendor.Aaa.Aaa"])

    report = sc.survey(corpus)

    counts = [(p["plugin"], p["count"]) for p in report["plugins"]]
    assert counts == [("Vendor.Aaa.Aaa", 2), ("Vendor.Bbb.Bbb", 2), ("Vendor.Ccc.Ccc", 1)]
    assert all(p["type"] == "unknown" for p in report["plugins"])


def test_the_report_carries_no_absolute_path(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(FIXTURES / "unknown_plugin.yxmd", corpus / "unknown_plugin.yxmd")
    (corpus / "broken.yxmd").write_text("<Alteryx", encoding="utf-8")

    report = sc.survey(corpus)
    blob = json.dumps(report)

    assert str(tmp_path) not in blob
    assert str(corpus) not in blob
    assert not _ABS_PATH_RE.search(blob), blob
    for row in report["workflows"]:
        assert "\\" not in row["path"]


def test_output_is_deterministic(tmp_path):
    corpus = _copy_samples(tmp_path)
    first = sc.survey(corpus)
    second = sc.survey(corpus)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_cli_writes_markdown_and_json(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(FIXTURES / "unknown_plugin.yxmd", corpus / "unknown_plugin.yxmd")
    out_md = tmp_path / "report.md"
    out_json = tmp_path / "report.json"

    rc = sc.main([str(corpus), "--out", str(out_md), "--json", str(out_json)])

    assert rc == 0
    assert out_md.exists() and out_json.exists()
    md_text = out_md.read_text(encoding="utf-8")
    assert "ExampleVendor.SentimentScore.SentimentScore" in md_text
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["workflows"][0]["path"] == "unknown_plugin.yxmd"


def test_cli_exits_2_on_a_missing_directory(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        sc.main([str(tmp_path / "does_not_exist")])
    assert excinfo.value.code == 2


def test_cli_exits_2_with_no_arguments_at_all():
    with pytest.raises(SystemExit) as excinfo:
        sc.main([])
    assert excinfo.value.code == 2


def test_a_crash_outside_the_survey_exits_2(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(FIXTURES / "unknown_plugin.yxmd", corpus / "unknown_plugin.yxmd")

    def boom(*args, **kwargs):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(sc, "survey", boom)
    assert sc.main([str(corpus)]) == 2


def test_cli_exits_0_with_no_output_files_requested(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(FIXTURES / "unknown_plugin.yxmd", corpus / "unknown_plugin.yxmd")
    assert sc.main([str(corpus)]) == 0


def test_render_markdown_names_every_workflow_and_the_plugin_table(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shutil.copy(FIXTURES / "unknown_plugin.yxmd", corpus / "unknown_plugin.yxmd")
    report = sc.survey(corpus)
    md = sc.render_markdown(report)
    assert "unknown_plugin.yxmd" in md
    assert "ExampleVendor.SentimentScore.SentimentScore" in md


def test_a_yxzp_package_is_extracted_to_a_temp_dir_and_surveyed(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    package_src = SAMPLES / "wf_0004" / "source"
    with zipfile.ZipFile(corpus / "inventory.yxzp", "w") as zf:
        for path in sorted(package_src.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(package_src).as_posix())

    report = sc.survey(corpus)

    assert len(report["workflows"]) == 1
    row = report["workflows"][0]
    assert row["path"] == "inventory.yxzp"
    assert row["status"] == "ok"
    assert row["tier"] == "T1"


def test_a_merge_mode_without_keys_is_a_dbt_blocker(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    doc = """<?xml version="1.0"?>
<AlteryxDocument yxmdVer="2023.1">
  <Nodes>
    <Node ToolID="1">
      <GuiSettings Plugin="AlteryxBasePluginsGui.DbFileInput.DbFileInput" />
      <Properties>
        <Configuration><File FileFormat="19">data\\in.yxdb</File></Configuration>
      </Properties>
      <EngineSettings EngineDll="AlteryxBasePluginsEngine.dll" EngineDllEntryPoint="AlteryxDbFileInput" />
    </Node>
    <Node ToolID="2">
      <GuiSettings Plugin="AlteryxBasePluginsGui.DbFileOutput.DbFileOutput" />
      <Properties>
        <Configuration>
          <File FileFormat="19">data\\out.yxdb</File>
          <FormatSpecificOptions><OutputOption>Update; Insert if New</OutputOption></FormatSpecificOptions>
        </Configuration>
      </Properties>
      <EngineSettings EngineDll="AlteryxBasePluginsEngine.dll" EngineDllEntryPoint="AlteryxDbFileOutput" />
    </Node>
  </Nodes>
  <Connections>
    <Connection><Origin ToolID="1" Connection="Output" /><Destination ToolID="2" Connection="Input" /></Connection>
  </Connections>
  <Properties>
    <Constants />
    <RuntimeProperties><Actions /><Questions /><ModuleType>Wizard</ModuleType><RunE2 value="False" /></RuntimeProperties>
  </Properties>
</AlteryxDocument>
"""
    (corpus / "merge_no_keys.yxmd").write_text(doc, encoding="utf-8")

    report = sc.survey(corpus, prefer="dbt")
    row = report["workflows"][0]
    assert row["output_kind"] == "procedures"
    assert any("merge_without_keys" in b for b in row["dbt_blockers"]), row["dbt_blockers"]
