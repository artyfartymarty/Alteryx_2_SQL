# `yxzp` — a packaged workflow

A `.yxzp` is a zip holding a workflow and its dependencies. `package/` is the **unzipped** content
this fixture stands for: `nightly.yxmd` (input → macro → output) and
`Supporting_Macros/trim_codes.yxmc` (macro input → formula → macro output).

`tests/parser_corpus/test_corpus.py` zips `package/` into `source/nightly.yxzp` in a temporary
repo at test time rather than committing a binary, so the fixture stays reviewable in a diff and
git never has to treat it as an opaque blob.

**Synthetic fixture written by hand to `docs/reference/dag-contract.md`. It has never been
produced or opened by Alteryx.**

What it guards: the packaging class of parse failure. `parse.run` must unzip a `.yxzp` found in
`source/` **in place** before looking for a workflow, so the macro lands next to the `.yxmd` at the
relative path its `<EngineSettings Macro="Supporting_Macros\trim_codes.yxmc">` names and resolves
to a real `sub_dag` instead of `unresolved: true`.
