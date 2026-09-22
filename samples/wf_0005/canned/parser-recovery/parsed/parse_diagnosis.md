# wf_0005 — parse diagnosis

Written by the `parser-recovery` agent, attempt 1, after
`workflows/wf_0005/parsed/parse_report.json` came back
`INVARIANT_VIOLATION`. Nothing in this project has run on a real Alteryx engine: the plugin below
is an invention of `samples/wf_0005` and this diagnosis is about the XML, not about a vendor tool
anyone has met.

## What `parse.py` reported

```
status: INVARIANT_VIOLATION
errors: ["unknown tools without a behavior: 1 of 4 data nodes (max 10%): ['2']"]
```

The parse itself did not fail. `scripts/parse.py` read every node, resolved every connection and
wrote `parsed/dag.json`; what failed was invariant 8 in `scripts/invariants.py`, which caps
*unexplained* `unknown` tools at 10% of a workflow's data nodes. One unknown out of four is 25%.

## Classification

**An unknown Plugin name — a custom or third-party tool.** Not a structure problem (no nested
containers beyond what the parser already handles, no macro, no Interface tools), not a packaging
problem (a plain `.yxmd`, not a `.yxzp` or a locked document), not an encoding problem (UTF-8, no
BOM, no CDATA).

## The exact XML fragment that broke the standard path

```xml
<Node ToolID="2">
  <GuiSettings Plugin="AcmeAnalytics.Dedupe.DedupeTool">
    <Position x="162" y="66" />
  </GuiSettings>
  <Properties>
    <Configuration>
      <KeyField>ACCT</KeyField>
      <Keep>MaxDate</Keep>
      <DateField>UPDATED</DateField>
    </Configuration>
    ...
  </Properties>
  <EngineSettings EngineDll="AcmeDedupe.dll" EngineDllEntryPoint="AcmeDedupe" />
</Node>
```

Vendor: `AcmeAnalytics`, from the plugin namespace. DLL: `AcmeDedupe.dll`, entry point
`AcmeDedupe`, from `<EngineSettings>`. No version is recorded anywhere in the document — Alteryx
does not write one for a third-party tool — so there is nothing to pin this diagnosis to but the
plugin name itself.

`scripts/parsers/plugin_map.py` has no entry for that plugin name, which is the correct outcome:
the parser never guesses, so the node became `type: "unknown"` with its `raw_config` kept intact.

## The fix

`scripts/parsers/ext/acme_dedupe.py`, one `register_plugin` handler keyed on the exact plugin
name. It adds three things and changes nothing else:

- `behavior` — a plain-language description of what the configuration *appears* to do, which is
  what invariant 8 is actually asking for;
- `confidence` `0.5`;
- `config` — the three `<Configuration>` children read into named keys, which is a restatement of
  the XML rather than an interpretation of it. `raw_config` is untouched either way.

The node stays `type: "unknown"`.

## What was deliberately not done

`Keep=MaxDate` over `KeyField=ACCT` by `DateField=UPDATED` reads like "one row per `ACCT`, the one
with the largest `UPDATED`" — a Sort followed by a Unique, or a `ROW_NUMBER` window. The extension
does **not** remap the plugin onto `unique`, and the registry would have let it:
`registry.MERGEABLE_KEYS` includes `type`, and `parse.py` would then have read `unique`'s own
config and anchors for it.

Three questions the XML does not answer are why not, and all three would show up as data
differences rather than as errors:

1. **A NULL date.** `wf_0005`'s `normal` golden input has account `D-400` with `UPDATED` NULL.
   Does the tool keep that row, drop it, or treat NULL as the smallest value?
2. **A tie.** Two rows with the same key and the same date — first in, last in, or both?
3. **Date or timestamp.** `UPDATED` is an Alteryx `Date`, but the tool may well compare something
   wider if the field were a `DateTime`.

`scripts/parsers/ext/README.md` is explicit about this case: a tool that parses structurally but
is semantically opaque stays `unknown` with a `behavior` and a `confidence`, because a wrong
`type` is a silent mistranslation later. The analyzer decides what to do with it.

## What this does and does not unblock

The re-parse comes back clean: `python scripts/parse.py wf_0005 --check` reports `PARSED` with no
errors, and `parse.run` promotes that to `RECOVERED` on the next run because this report exists.

It does **not** make `wf_0005` migratable, and it was never going to. Tool 3 is a
`AlteryxBasePluginsGui.RunCommand.RunCommand`, which the program spec puts in tier **T3** on its
own: it shells out to `C:\scripts\notify.bat`, which has no Snowflake equivalent at all. The
workflow's terminal state is `MANUAL` whatever tool 2 turns out to mean, and
`scripts/dev/alteryx_sim.py` refuses both tools, so this workflow has no golden outputs and no
procedure to validate. The recovery's whole job here is to let the pipeline *reach* that verdict
instead of stopping at a parse failure.

## Regression fixture

`tests/parser_corpus/acme_dedupe/` — `fragment.yxmd` (a sanitized three-tool workflow showing the
variant, relative paths, no credentials), `test_acme_dedupe.py` (the fragment fails invariant 8
without the extension and passes every invariant with it; the node is still `unknown`; the node is
still wired), and a `README.md` saying what the fixture guards and that it is synthetic.

Run the whole corpus before accepting this: `python -m pytest tests/parser_corpus`. Every earlier
fixture must still pass. Note for whoever accepts it permanently:
`tests/parser_corpus/test_corpus.py` keeps a literal `FIXTURES` set and asserts the directories on
disk match it exactly, so `acme_dedupe` has to be added to that set in the same change.
