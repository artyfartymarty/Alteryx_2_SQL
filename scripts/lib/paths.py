"""Repo-relative path helpers shared by every script (plan contract: --root PATH, default ".").

Every script touches only paths under its Repo's root. Tests pass tmp_path as root so nothing
writes into the real repo's workflows/.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

# Workflow and segment ids become filesystem path components straight away (`Repo.wf`/`Repo.seg`),
# and a script like dev/build_samples.py deletes and recreates whole subtrees keyed on them, so an
# id is rejected outright -- not trusted -- unless it is exactly this shape: no dots, no path
# separators, no drive letters, not empty, no leading/trailing whitespace. In particular "..",
# which would otherwise let `Repo.wf("..")` collapse back to the repo root itself.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# A leading separator ("/x", "\x") isn't flagged by pathlib's own is_absolute() on every platform
# (a POSIX-style leading "/" is NOT absolute under WindowsPath), so it's checked explicitly too.
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]?")


def _validate_id(value: str, label: str) -> str:
    """A workflow or segment id: see `_ID_RE`'s comment for exactly what this allows."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def _validate_part(value: str) -> str:
    """A path segment after the id(s). Rejected: empty, absolute (a drive letter or a leading
    separator), or containing a `.`/`..` path *component* once split on both `/` and `\\` --
    relative traversal through `*parts` is never legitimate in this codebase (it is what let
    `_reset_dir(repo, wf_id, "golden", "inputs", "..", "..", "intake")` walk out to a sibling
    directory `_reset_dir`'s own literal-name allowlist didn't anticipate). A dot *inside* a
    component -- a filename like `"3_Output.csv"`, `".sandbox.duckdb"`, or a multi-component
    literal like `"golden/inputs"` -- stays legal; these are script-authored, never external
    input.
    """
    text = str(value)
    if not text:
        raise ValueError(f"invalid path segment (must not be empty): {value!r}")
    if text.startswith(("/", "\\")) or _DRIVE_RE.match(text) or Path(text).is_absolute():
        raise ValueError(f"invalid path segment (must be relative): {value!r}")
    if any(component in (".", "..") for component in re.split(r"[\\/]+", text)):
        raise ValueError(f"invalid path segment (must not contain '.' or '..'): {value!r}")
    return value


class Repo:
    """Resolves the well-known locations under a repo root.

    root is resolved (absolute, symlinks/`..` collapsed) so path comparisons are stable
    regardless of the caller's current working directory.
    """

    def __init__(self, root: str | os.PathLike = "."):
        self.root = Path(root).resolve()
        self.global_mappings = self.root / "mappings" / "global.yaml"

    def wf(self, wf_id: str, *parts: str) -> Path:
        """root/workflows/<wf_id>/<parts…>"""
        _validate_id(wf_id, "workflow id")
        for part in parts:
            _validate_part(part)
        return self.root.joinpath("workflows", wf_id, *parts)

    def seg(self, wf_id: str, seg: str, *parts: str) -> Path:
        """root/workflows/<wf_id>/segments/<seg>/<parts…>"""
        _validate_id(wf_id, "workflow id")
        _validate_id(seg, "segment id")
        for part in parts:
            _validate_part(part)
        return self.root.joinpath("workflows", wf_id, "segments", seg, *parts)


def add_root_arg(parser: argparse.ArgumentParser) -> None:
    """Adds the standard --root PATH argument (default ".") every script accepts."""
    parser.add_argument("--root", default=".", help="Repo root (default: current directory)")


def wf_token(wf_id: str) -> str:
    """"wf_0001" -> "WF0001" (contract C3: <WF> is the id upper-cased without underscores)."""
    _validate_id(wf_id, "workflow id")
    return wf_id.upper().replace("_", "")


def seg_token(seg: str) -> str:
    """"seg_01" -> "SEG_01" (contract C3: <SEG> is "SEG_01")."""
    _validate_id(seg, "segment id")
    return seg.upper()
