"""`tests/helpers.py::copy_pristine_mappings_and_catalog` (F1b, coordinator ruling): the ONE shared
fixture-copy helper every test that needs `mappings/`/`catalog/` now goes through. It must produce
a pristine `mappings/global.yaml` (`sources`/`outputs` blanked to `{}`, every other key -- and its
comments -- kept exactly as written) no matter what the SOURCE tree's own `global.yaml` currently
holds. Proven here against a scratch source tree with a deliberately polluted `global.yaml`, never
against the real repo tree: a test must not depend on the live tree's `mappings/global.yaml`
staying pristine to pass.
"""
from __future__ import annotations

from pathlib import Path

from lib import io
from tests import helpers

_POLLUTED_GLOBAL_YAML = """\
# a hand-written comment that must survive the copy
program:
  target_database: ANALYTICS
  raw_schema: RAW
session:
  TIMEZONE: America/New_York
sources:
  sales/orders.yxdb:
    snowflake: SALES.RAW.ORDERS
    logical: ORDERS
outputs:
  out/x.csv:
    snowflake: ANALYTICS.CURATED.X
    logical: X
"""


def _write_polluted_source_tree(root: Path) -> None:
    (root / "mappings").mkdir(parents=True)
    (root / "catalog").mkdir(parents=True)
    (root / "catalog" / "placeholder.yaml").write_text("tables: []\n", encoding="utf-8")
    (root / "mappings" / "global.yaml").write_text(_POLLUTED_GLOBAL_YAML, encoding="utf-8")


def test_a_polluted_source_global_yaml_is_copied_pristine(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    _write_polluted_source_tree(source_root)
    monkeypatch.setattr(helpers, "ROOT", source_root)

    dest = tmp_path / "dest"
    dest.mkdir()
    helpers.copy_pristine_mappings_and_catalog(dest)

    doc = io.read_yaml(dest / "mappings" / "global.yaml")
    assert doc["sources"] == {}
    assert doc["outputs"] == {}
    # every other key is kept exactly -- this is a copy-and-blank, not a fresh template.
    assert doc["program"] == {"target_database": "ANALYTICS", "raw_schema": "RAW"}
    assert doc["session"] == {"TIMEZONE": "America/New_York"}

    text = (dest / "mappings" / "global.yaml").read_text(encoding="utf-8")
    assert "# a hand-written comment that must survive the copy" in text
    assert (dest / "catalog" / "placeholder.yaml").is_file()


def test_the_source_tree_itself_is_left_untouched(tmp_path, monkeypatch):
    """The helper only ever writes into `dest_root`; it must never mutate the tree it copies
    from -- the whole point of proving this against a scratch source, not the live repo tree."""
    source_root = tmp_path / "source"
    _write_polluted_source_tree(source_root)
    monkeypatch.setattr(helpers, "ROOT", source_root)
    before = (source_root / "mappings" / "global.yaml").read_bytes()

    helpers.copy_pristine_mappings_and_catalog(tmp_path / "dest")

    assert (source_root / "mappings" / "global.yaml").read_bytes() == before


# --- `overlay_dbt_project` (output-targets phase 2, Task C) ------------------------------------------


def test_overlay_dbt_project_replaces_only_the_named_files_and_leaves_the_project_alone(tmp_path):
    """A broken dbt model is validated against a COPY of the workflow's project with that one file
    replaced: every other project file comes along unchanged, and the project under test is never
    touched."""
    from lib.paths import Repo

    repo = Repo(tmp_path / "root")
    project = repo.wf("wf_0009", "dbt")
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: wf_0009\n", encoding="utf-8")
    (project / "models" / "a.sql").write_text("select 1 as A\n", encoding="utf-8")
    (project / "models" / "b.sql").write_text("select 2 as B\n", encoding="utf-8")
    variant = tmp_path / "broken_b.sql"
    variant.write_text("select 3 as B\n", encoding="utf-8")

    dest = helpers.overlay_dbt_project(repo, "wf_0009", tmp_path / "overlay", {"models/b.sql": variant})

    assert dest == tmp_path / "overlay"
    assert (dest / "models" / "b.sql").read_text(encoding="utf-8") == "select 3 as B\n"
    assert (dest / "models" / "a.sql").read_text(encoding="utf-8") == "select 1 as A\n"
    assert (dest / "dbt_project.yml").read_text(encoding="utf-8") == "name: wf_0009\n"
    assert (project / "models" / "b.sql").read_text(encoding="utf-8") == "select 2 as B\n"
