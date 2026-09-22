# Parser extensions

Every module in this directory is imported by `parsers.registry.load_extensions` before a parse,
and registers handlers that `scripts/parse.py` consults before its own defaults. **This is the
only parser surface the `parser-recovery` agent writes** (program spec §6.4): `parse.py`,
`plugin_map.py`, `tool_config.py` and `registry.py` are reviewed files and an extension never
edits them. Extensions load from this directory and, when `--root` points at another tree, from
that tree's `scripts/parsers/ext/` as well.

A module is loaded once per process. Files whose name starts with `_` are skipped, so helpers can
live here too.

## The two handlers

```python
from parsers.registry import register_plugin, register_element

def dedupe(node_xml, node):          # node_xml: the whole <Node> element; node: the part built so far
    return {"type": "unknown",       # only these keys are read (registry.MERGEABLE_KEYS):
            "in_anchors": ["Input"], #   type, config, in_anchors, out_anchors, behavior, confidence
            "out_anchors": ["Output"],
            "behavior": "appears to dedupe on ACCT keeping the row with the largest UPDATED",
            "confidence": 0.6}

register_plugin("AcmeAnalytics.Dedupe.DedupeTool", dedupe)
```

`register_plugin(name, handler)` keys on the exact `GuiSettings Plugin` value. **The handler runs
before the parser classifies the tool**, and what it returns settles what follows:

1. It is called with the `<Node>` element and the part of the node that does not depend on the
   tool's type — `tool_id`, `plugin`, `container_id`, `annotation`, `raw_config`, `meta` (and
   `type` set to what `classify` would say, so a handler can look before it leaps).
2. The node's `type` is the handler's, or `classify(plugin, macro)` when it returns none.
3. `config` is the handler's, or what `tool_config.parse_config` reads **for the type that won** —
   so remapping a vendor plugin to a built-in type (say `unique`) gets that type's config, not the
   empty one `unknown` would have had, and `meta` is re-keyed to that type's anchors.
4. `in_anchors` / `out_anchors` are the handler's, or the type's defaults; for `unknown`, the
   anchors its connections actually use.

Keys outside `MERGEABLE_KEYS` are ignored: an extension may explain a tool, it may not rewrite a
node's identity (`tool_id`, `container_id`, `plugin`) or touch other nodes.

```python
def batch_flag(element, dag):        # element: one matching element; dag: the document being built
    dag["batch_macro"] = element.get("value") == "True"

register_element("RunBatch", batch_flag)
```

`register_element(tag, handler)` is called once per element with that tag anywhere in the
document, after the nodes and edges are built, and mutates `dag` in place. Its return value is
ignored. Use it for document-level facts, not for tools.

## What a handler must not do

- **Never invent semantics.** A tool that is structurally parsed but semantically opaque stays
  `type: "unknown"` with its `raw_config`, plus a plain-language `behavior` and a `confidence`.
  The analyzer decides what it means; a wrong `type` is a silent mistranslation later.
- **Never drop a node or an anchor.** If the anchors are unclear, give the ones the connections
  actually use — that is what invariant 2 checks.
- **Never write files or reach outside the process.** Handlers are pure functions of the XML.

## What ships with an extension

1. The module here.
2. A fixture directory `tests/parser_corpus/<name>/` holding a small, sanitized workflow that
   shows the variant, and a `README.md` saying what it guards and that it is synthetic.
3. A test in `tests/parser_corpus/test_corpus.py` asserting the fixture parses and
   `invariants.check` comes back empty (or, for a packaging variant, the status `parse.run`
   reports). Run the whole corpus: every earlier fixture must still pass.

An extension without a fixture is not finished — the corpus is the permanent regression gate, and
a human reviews both in the workflow's PR.
