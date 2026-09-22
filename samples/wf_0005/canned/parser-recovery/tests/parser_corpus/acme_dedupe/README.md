# `acme_dedupe` — an unrecognised third-party plugin

**What this fixture guards.** `scripts/parsers/ext/acme_dedupe.py`, the extension the
`parser-recovery` agent wrote for `wf_0005`. `fragment.yxmd` is the smallest workflow that shows
the variant: an Input tool, one node whose `GuiSettings Plugin` is
`AcmeAnalytics.Dedupe.DedupeTool`, and an Output tool.

**Why it is here.** The core parser keeps an unrecognised plugin as `type: "unknown"` with its
`raw_config`, which is right — but invariant 8 caps *unexplained* unknowns at 10% of a workflow's
data nodes, and one unknown out of three is over that. So the fragment fails to parse cleanly
without the extension and parses cleanly with it, and `test_acme_dedupe.py` asserts both
directions. It also asserts that the extension still refuses to say what the tool *means*: the
node keeps `type: "unknown"` and a confidence below one, because a wrong `type` here would become
a silent mistranslation in the generated SQL.

**It is synthetic.** This file was written by hand to `docs/reference/dag-contract.md` and has
never been produced or opened by Alteryx. `AcmeAnalytics.Dedupe.DedupeTool` and `AcmeDedupe.dll`
are inventions of `samples/wf_0005`; no such tool exists. The paths are relative and there are no
credentials, by the corpus's own rule that a fixture ships in the repository forever.

**Adding it to the permanent corpus.** `tests/parser_corpus/test_corpus.py` keeps a literal set of
fixture directory names (`FIXTURES`) and asserts that the directories on disk match it exactly, so
whoever accepts this fixture into the corpus has to add `acme_dedupe` to that set in the same
change. Until then this directory lives under
`samples/wf_0005/canned/parser-recovery/`, which is what the mock runner replays.
