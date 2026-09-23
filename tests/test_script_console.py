"""Every script prints its help and its errors in a Windows console's code page (Task P4 fix round 1, B4).

A Windows pipe (Git Bash, or anything that captures a child's output) gives Python the ANSI code page,
cp1252, and a help text holding a character cp1252 lacks (`→`) made `scripts/parse.py --help` and
`scripts/load_golden.py --help` die with UnicodeEncodeError. One mechanism fixes every script: each
entry point calls `lib.console.utf8_console()` first, which re-encodes stdout and stderr as UTF-8
(errors replaced), whatever the console's code page -- the encoding the orchestrator already decodes a
child's output with.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIN_BLOCK = '\nif __name__ == "__main__":'
#: The two modules under scripts/ and scripts/dev/ that are libraries, not command-line scripts.
LIBRARIES = {"scripts/dev/__init__.py", "scripts/dev/formula.py"}


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


SCRIPTS = sorted(_rel(path) for path in [*ROOT.glob("scripts/*.py"), *ROOT.glob("scripts/dev/*.py")]
                 if MAIN_BLOCK in path.read_text(encoding="utf-8"))


def _cp1252() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "cp1252"
    return env


def test_every_module_is_either_a_script_or_a_known_library():
    every = {_rel(path) for path in [*ROOT.glob("scripts/*.py"), *ROOT.glob("scripts/dev/*.py")]}
    assert every - set(SCRIPTS) == LIBRARIES
    assert len(SCRIPTS) >= 25


@pytest.mark.parametrize("script", SCRIPTS)
def test_help_works_in_a_cp1252_console(script):
    done = subprocess.run([sys.executable, str(ROOT / script), "--help"], capture_output=True, env=_cp1252(),
                          cwd=ROOT)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert done.stdout.decode("utf-8", "replace").startswith("usage:")


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_script_enters_through_the_one_console_helper(script):
    text = (ROOT / script).read_text(encoding="utf-8")
    assert "utf8_console()" in text[text.index(MAIN_BLOCK):], f"{script}'s __main__ block does not call utf8_console()"


def test_an_error_naming_a_non_ascii_path_reaches_stderr_intact(tmp_path):
    missing = tmp_path / "no_such_→"
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "survey_corpus.py"), str(missing)],
                          capture_output=True, env=_cp1252(), cwd=ROOT)
    assert done.returncode == 2
    assert "no_such_→" in done.stderr.decode("utf-8")
