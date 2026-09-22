"""Regression test for `scripts/parsers/ext/acme_dedupe.py` (program spec §6.4).

`fragment.yxmd` is the smallest workflow that shows the variant: an unrecognised third-party
plugin between an Input and an Output tool. Without the extension it is one unexplained `unknown`
among three data nodes — 33% against invariant 8's 10% ceiling — so the parse reports the violation
that triggers the recovery agent. With the extension loaded the same file satisfies every
invariant, and the node is still `unknown`: the extension explains the tool, it does not decide
what it means.

The fixture is hand-written and sanitized (relative paths, no credentials, no host names). **No
file here was produced or opened by Alteryx**, and `AcmeAnalytics.Dedupe.DedupeTool` is an
invention of `samples/wf_0005`.

The extension is located relative to this file — `<root>/scripts/parsers/ext` from
`<root>/tests/parser_corpus/acme_dedupe/` — so the test works both in the repository and in a tree
the mock runner has replayed the recovery artifacts into.
"""
from __future__ import annotations

import sys
from pathlib import Path

CORPUS = Path(__file__).resolve().parent
ROOT = CORPUS.parents[2]
EXT_DIR = ROOT / "scripts" / "parsers" / "ext"
FRAGMENT = CORPUS / "fragment.yxmd"

# In the repository, pytest's own `pythonpath` has already done this. In a tree the mock runner
# replayed the recovery artifacts into there is no conftest, so the scripts directory is added
# here -- but only when it really holds the parser, so that a tree carrying nothing but the
# extension falls back to whatever `parse` the session already imported.
_SCRIPTS = ROOT / "scripts"
if (_SCRIPTS / "parse.py").is_file() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import invariants  # noqa: E402
import parse  # noqa: E402
from parsers import registry  # noqa: E402

PLUGIN = "AcmeAnalytics.Dedupe.DedupeTool"


def _parse_with_extension():
    """Parse the fragment with the extension loaded, then clear the registry.

    `registry` state is process-global, so every test that loads an extension resets it in a
    `finally` — otherwise the handler would still be registered for whatever runs next.
    """
    try:
        dag, _ = parse.parse_file(FRAGMENT, ext_dirs=[EXT_DIR])
        assert registry.plugin_handler(PLUGIN) is not None, (
            f"{EXT_DIR}/acme_dedupe.py did not register a handler for {PLUGIN}")
        return dag
    finally:
        registry.reset()


def test_the_fragment_fails_invariant_8_without_the_extension():
    """The state that triggers the recovery agent in the first place."""
    try:
        dag, _ = parse.parse_file(FRAGMENT)
    finally:
        registry.reset()
    node = next(n for n in dag["nodes"] if n["tool_id"] == "2")
    assert node["type"] == "unknown" and "behavior" not in node
    errors = invariants.check(FRAGMENT.read_text(encoding="utf-8"), dag)
    assert any("unknown tools without a behavior" in error for error in errors), errors


def test_the_extension_explains_the_tool_and_every_invariant_passes():
    dag = _parse_with_extension()
    assert invariants.check(FRAGMENT.read_text(encoding="utf-8"), dag) == []
    node = next(n for n in dag["nodes"] if n["tool_id"] == "2")
    assert node["behavior"].strip(), "the handler must leave a plain-language behavior"
    assert 0.0 <= node["confidence"] <= 1.0


def test_the_extension_does_not_guess_what_the_tool_means():
    """The whole point of the `unknown` + `behavior` shape: a wrong `type` is a silent
    mistranslation later, so the node keeps its raw configuration and its refusal to decide."""
    node = next(n for n in _parse_with_extension()["nodes"] if n["tool_id"] == "2")
    assert node["type"] == "unknown"
    assert node["confidence"] < 1.0
    assert node["raw_config"] and "KeyField" in node["raw_config"]
    assert node["config"] == {"key_field": "ACCT", "keep": "MaxDate", "date_field": "UPDATED"}


def test_the_extension_keeps_the_node_wired_into_the_dag():
    """Invariant 2: an extension may never drop a node or an anchor."""
    dag = _parse_with_extension()
    node = next(n for n in dag["nodes"] if n["tool_id"] == "2")
    assert node["in_anchors"] == ["Input"] and node["out_anchors"] == ["Output"]
    wired = {(e["src"], e["dst"]) for e in dag["edges"]}
    assert wired == {("1", "2"), ("2", "3")}


def test_the_fragment_carries_no_credentials_or_host_paths():
    """A corpus fixture is sanitized: it ships in the repository forever."""
    text = FRAGMENT.read_text(encoding="utf-8")
    for secret in ("PWD=", "Password=", "UID=", "User ID=", "odbc:", "aka:", "C:\\", "\\\\"):
        assert secret not in text, f"fragment.yxmd still contains {secret!r}"
