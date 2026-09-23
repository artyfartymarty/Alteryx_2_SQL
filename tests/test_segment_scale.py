"""`scripts/dev/segment_scale.py` times `segment.segment()` on a synthetic chain (Task P4 fix round 2, M14).

The production backlog's "Segmenter scale" item quotes its output; this pins that the command it names
runs, prints one line per size in the documented shape, and refuses a size too small to be a chain.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev" / "segment_scale.py"
LINE = re.compile(r"^tools=(\d+) segments=(\d+) seconds=\d+\.\d$")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=ROOT)


def test_one_line_per_size_in_the_documented_shape():
    done = _run("--tools", "30", "--tools", "60")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert [LINE.match(line).group(1) for line in lines] == ["30", "60"], done.stdout
    assert all(int(LINE.match(line).group(2)) >= 1 for line in lines)


def test_the_chain_is_deterministic():
    first, second = _run("--tools", "45"), _run("--tools", "45")
    segments = [LINE.match(done.stdout.strip()).group(2) for done in (first, second)]
    assert segments[0] == segments[1]


def test_a_chain_needs_at_least_three_tools():
    done = _run("--tools", "2")
    assert done.returncode == 2
    assert "--tools" in done.stderr


def test_no_size_is_a_usage_error():
    assert _run().returncode == 2
