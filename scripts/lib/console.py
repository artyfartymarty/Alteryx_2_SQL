"""One console encoding for every script: UTF-8 on stdout and stderr, whatever the terminal's code page.

On Windows a pipe (Git Bash, a CI log, the orchestrator capturing a child's output) hands Python the
ANSI code page, cp1252, which has no `→`: a help text or a message holding one killed the script with
UnicodeEncodeError before this existed (`scripts/parse.py --help` and `scripts/load_golden.py --help`,
found by the production hand-off, Task P4 fix round 1). Every script's `__main__` block calls
`utf8_console()` before `main()`; nothing calls it on import, so a test that runs `main()` in-process
keeps its own captured streams. UTF-8 is also what the orchestrator decodes a child's output as
(`String(chunk)` in orchestrator/cli.ts).
"""
from __future__ import annotations

import sys


def utf8_console() -> None:
    """Re-encode `sys.stdout` and `sys.stderr` as UTF-8, replacing anything that still cannot be
    encoded (a lone surrogate) instead of raising. A stream that cannot be reconfigured (absent under
    pythonw, or replaced by something that is not a text wrapper) is left alone."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
