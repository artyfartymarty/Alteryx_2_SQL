"""JSON/YAML I/O and manifest read/write, normalized to the plan's Global Constraints:
UTF-8, LF line endings, JSON indent=2 with a trailing newline.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from .paths import Repo


def read_json(path: str | os.PathLike) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | os.PathLike, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read_yaml(path: str | os.PathLike) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def write_yaml(path: str | os.PathLike, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(obj, sort_keys=False, allow_unicode=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):")


def _preserved_global_mappings_preamble(text: str) -> str | None:
    """The prefix of `text` up to (not including) its top-level `sources:` line, or `None` if
    `text` doesn't have the shape `write_global_mappings` can safely round-trip: exactly one
    top-level `sources:` line, with no top-level key other than `outputs:` following it (and
    `outputs:` not appearing earlier, before `sources:`).
    """
    lines = text.splitlines(keepends=True)
    top_level = [(i, m.group(1)) for i, line in enumerate(lines)
                for m in (_TOP_LEVEL_KEY_RE.match(line),) if m]
    sources_positions = [i for i, key in top_level if key == "sources"]
    if len(sources_positions) != 1:
        return None
    sources_idx = sources_positions[0]
    if any(key == "outputs" for i, key in top_level if i < sources_idx):
        return None
    trailing = [key for i, key in top_level if i > sources_idx]
    if trailing not in ([], ["outputs"]):
        return None
    return "".join(lines[:sources_idx])


def write_global_mappings(path: str | os.PathLike, obj: dict) -> None:
    """Writes `mappings/global.yaml` (coordinator ruling F1d). Everything above the top-level
    `sources:` key -- comments included -- is preserved VERBATIM; only `sources`/`outputs` are
    regenerated, from `obj["sources"]`/`obj["outputs"]`. Falls back to a full `yaml.safe_dump` of
    `obj` -- and reports it on stderr -- when an existing file's shape won't allow that: no
    top-level `sources:` line, or some other top-level key appearing after it. A path that doesn't
    exist yet has nothing to preserve, so it is simply written in full, without a warning.
    """
    path = Path(path)
    preamble: str | None = None
    if path.is_file():
        preamble = _preserved_global_mappings_preamble(path.read_text(encoding="utf-8"))
        if preamble is None:
            print(f"warning: {path} does not have the expected mappings/global.yaml shape (one "
                 "top-level 'sources:' key, followed only by 'outputs:'); rewriting the whole "
                 "file instead of preserving its comments", file=sys.stderr)

    if preamble is not None:
        tail = yaml.safe_dump({"sources": obj.get("sources") or {}, "outputs": obj.get("outputs") or {}},
                              sort_keys=False, allow_unicode=True)
        text = preamble + tail
    else:
        text = yaml.safe_dump(obj, sort_keys=False, allow_unicode=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def load_manifest(repo: Repo, wf_id: str) -> dict:
    """Loads workflows/<wf_id>/manifest.json, or the default shape if it doesn't exist yet."""
    path = repo.wf(wf_id, "manifest.json")
    if not path.exists():
        return {"id": wf_id, "status": {}, "metrics": {}}
    return read_json(path)


def save_manifest(repo: Repo, manifest: dict) -> None:
    """Stamps manifest["updated_at"] (UTC ISO-8601, "…Z") and writes it to workflows/<id>/manifest.json."""
    manifest["updated_at"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json(repo.wf(manifest["id"], "manifest.json"), manifest)
