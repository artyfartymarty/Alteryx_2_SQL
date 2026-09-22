"""Alteryx XML parsing pieces used by `scripts/parse.py`.

- `plugin_map` — Plugin attribute → canonical tool type, and the anchor names per type.
- `tool_config` — one `<Configuration>` element → the `config` dict dag-contract §4 fixes, plus
  the credential scrubbing of §6.
- `registry` — the extension seam the `parser-recovery` agent writes against (`ext/*.py`).
"""
