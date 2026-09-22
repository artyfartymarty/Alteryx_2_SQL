"""`AcmeAnalytics.Dedupe.DedupeTool` — a third-party plugin the core parser does not know.

Written by the `parser-recovery` agent for `wf_0005` (program spec §6.4). The core parser already
keeps the node: `plugin_map.classify` gives it `type: "unknown"` and `raw_config` is preserved. The
only thing missing is an explanation, and without one invariant 8 fails — one unexplained unknown
among four data nodes is 25% against a 10% ceiling — so the parse reports `INVARIANT_VIOLATION`
and nothing downstream runs.

This extension supplies the explanation and nothing else.

**It deliberately does not say what the tool means.** `Keep=MaxDate` reads like "one row per
`KeyField`, the one with the largest `DateField`", which is close to a Unique after a Sort — but
"close to" is how a silent mistranslation starts, and three questions have no answer in the XML:

* what happens to a row whose `DateField` is NULL (`wf_0005`'s `D-400` is exactly that row);
* what happens when two rows tie on the date — first in, last in, or both;
* whether the comparison is by date only or by the full timestamp.

So the node stays `type: "unknown"` with a plain-language `behavior` and a `confidence` below one,
which is what `scripts/parsers/ext/README.md` requires of a tool that parses structurally but is
semantically opaque. The analyzer decides what to do with it; this file never guesses. For
`wf_0005` the answer is already settled by another tool — the Run Command at tool 3 makes the
workflow tier T3 and terminal `MANUAL` whatever this one turns out to mean.

`config` is filled in from the three elements the tool's `<Configuration>` actually carries. That
is a restatement of the XML, not an interpretation: the names are the vendor's own, and a reader
who wants the untouched text still has `raw_config`.

Nothing here has been checked against a real Alteryx engine: `AcmeAnalytics.Dedupe.DedupeTool` and
`AcmeDedupe.dll` are inventions of `samples/wf_0005`, and so is everything this file says about
them.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from parsers.registry import register_plugin

PLUGIN = "AcmeAnalytics.Dedupe.DedupeTool"

#: The `<Configuration>` children this tool carries, mapped to the keys `config` reports them under.
_FIELDS = {"KeyField": "key_field", "Keep": "keep", "DateField": "date_field"}


def _text(configuration: ET.Element | None, tag: str) -> str | None:
    if configuration is None:
        return None
    value = (configuration.findtext(tag) or "").strip()
    return value or None


def dedupe(node_xml: ET.Element, node: dict) -> dict:
    """Explain one Acme Dedupe node without deciding what it means."""
    properties = node_xml.find("Properties")
    configuration = properties.find("Configuration") if properties is not None else None
    config = {key: _text(configuration, tag) for tag, key in _FIELDS.items()}

    engine = node_xml.find("EngineSettings")
    dll = (engine.get("EngineDll") if engine is not None else None) or "an unrecorded DLL"

    key_field = config["key_field"] or "an unnamed field"
    keep = config["keep"] or "an unnamed rule"
    date_field = config["date_field"]
    by_date = f" by {date_field}" if date_field else ""

    behavior = (
        f"Third-party tool {PLUGIN} ({dll}). Its configuration names {key_field} as the key, "
        f"{keep} as what to keep and{by_date or ' no date field'}, which reads as one row per "
        f"{key_field} chosen{by_date}. The XML does not say what happens to a row whose date is "
        f"NULL, what happens when two rows tie on it, or whether the comparison is by date or by "
        f"full timestamp, so the tool is left unknown rather than mapped onto Unique or a window "
        f"function. A human who knows the vendor tool should settle those three questions before "
        f"anyone writes SQL for it."
    )
    return {
        "type": "unknown",
        "config": config,
        "in_anchors": ["Input"],
        "out_anchors": ["Output"],
        "behavior": behavior,
        "confidence": 0.5,
    }


register_plugin(PLUGIN, dedupe)
