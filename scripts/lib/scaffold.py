"""The translation skeleton's TODO marker (live hardening, Task L8).

`scripts/translation_scaffold.py` writes every mechanical line of a translation -- a SQL procedure's
header, `LET` lines, write statements and `RETURN`; a Snowpark module's signature, reads and writes; a
dbt project's fixed files, YAML, model files and `config(...)` lines -- and leaves the transformation
itself to the translator: each tool's body is exactly `TODO_MARKER`. `scripts/compile_check.py` refuses
any file that still holds the marker, by name (`scaffold:todo`), before it checks anything else, so an
unfilled (or half-filled) skeleton can never compile.
"""
from __future__ import annotations

import re

#: The one fixed string every stub body is. Never valid code in any of the three targets on its own
#: (a CTE body, a statement, a Python line), and never written by anything but the scaffold.
TODO_MARKER = "TODO(scaffold)"

#: The marker as `todo_errors` finds it: any case, any spacing (fix round 1, M2) -- `todo(scaffold)` is
#: valid Python and `TODO (scaffold)` a SQL function call, so a respelled marker would otherwise reach
#: validation as a NameError or a parse error instead of being named here.
TODO_PATTERN = re.compile(r"todo\s*\(\s*scaffold\s*\)", re.IGNORECASE)

#: A `-- tool <id>: …` (SQL, dbt) or `# tool <id>: …` (Python) comment line: the tool the TODO under
#: it belongs to.
_TOOL_LINE = re.compile(r"^\s*(?:--|#)\s*tool\s+(?P<id>[^\s:]+)\s*:")


def todo_errors(text: str, where: str) -> list[str]:
    """One `scaffold:todo` error per line of `text` that still holds the marker, naming `where` (the
    file), the line and the tool whose stub it is (the nearest `tool <id>:` comment above it)."""
    errors = []
    tool = None
    for number, line in enumerate(text.splitlines(), 1):
        match = _TOOL_LINE.match(line)
        if match:
            tool = match.group("id")
        if TODO_PATTERN.search(line):
            owner = f" (tool {tool})" if tool else ""
            errors.append(f"scaffold:todo: {where} line {number}{owner} still holds the orchestrator's "
                          f"{TODO_MARKER} marker; replace it with the transformation and keep every "
                          f"mechanical line around it as it is")
    return errors
