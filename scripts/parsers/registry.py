"""The extension seam for the `parser-recovery` agent (program spec §6.2, §6.4).

Modules under `scripts/parsers/ext/` call `register_plugin` / `register_element` at import time;
`parse.py` loops over what they registered before falling back to its own defaults. Extensions are
the only parser surface the agent may write, so `parse.py` itself stays a reviewed file.
"""
from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Iterable

# A plugin handler gets the whole <Node> element and the node dict the parser has built so far,
# and returns the keys to merge over it. Anything else it returns is ignored: an extension may
# explain a tool, it may not rewrite a node's identity (tool_id, container_id, plugin, meta).
MERGEABLE_KEYS: tuple[str, ...] = ("type", "config", "in_anchors", "out_anchors", "behavior", "confidence")

PluginHandler = Callable[[ET.Element, dict], dict]
ElementHandler = Callable[[ET.Element, dict], None]

_PLUGIN_HANDLERS: dict[str, PluginHandler] = {}
_ELEMENT_HANDLERS: dict[str, ElementHandler] = {}
_LOADED: set[str] = set()


def register_plugin(name: str, handler: PluginHandler) -> None:
    """Handle one `GuiSettings Plugin` value. `handler(node_xml, node)` returns a dict of
    `MERGEABLE_KEYS` to merge over the node the parser built."""
    _PLUGIN_HANDLERS[name] = handler


def register_element(tag: str, handler: ElementHandler) -> None:
    """Handle a document-level element. `handler(element, dag)` is called once per matching
    element anywhere in the document and mutates `dag` in place."""
    _ELEMENT_HANDLERS[tag] = handler


def plugin_handler(name: str | None) -> PluginHandler | None:
    return _PLUGIN_HANDLERS.get(name) if name else None


def element_handlers() -> dict[str, ElementHandler]:
    return dict(_ELEMENT_HANDLERS)


def mergeable(result: dict | None) -> dict:
    """A plugin handler's result, narrowed to the keys it is allowed to set."""
    return {key: value for key, value in (result or {}).items() if key in MERGEABLE_KEYS}


def load_extensions(dirs: Iterable[Path]) -> list[str]:
    """Import every `*.py` in `dirs` (except `__init__` and `_`-prefixed helpers) once.

    Returns the module names those directories offer, whether or not this call was the one that
    imported them; `reset()` forgets both the handlers and what was imported.
    """
    names: list[str] = []
    for directory in dirs:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.stem.startswith("_"):
                continue
            names.append(path.stem)
            key = str(path.resolve()).lower()
            if key in _LOADED:
                continue
            _load(path)
            _LOADED.add(key)
    return sorted(dict.fromkeys(names))


def reset() -> None:
    """Forget every registered handler and loaded module. Tests call this; `parse.py` does not."""
    _PLUGIN_HANDLERS.clear()
    _ELEMENT_HANDLERS.clear()
    _LOADED.clear()


def _load(path: Path) -> None:
    module_name = f"parsers_ext_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - only on an unreadable file
        raise ImportError(f"cannot load parser extension {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
