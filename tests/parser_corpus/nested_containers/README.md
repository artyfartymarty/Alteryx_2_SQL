# `nested_containers` — three levels of `<ChildNodes>`

`workflow.yxmd` wraps an input (1) and a sort (2) in Tool Container 120 "Inner", which sits in 110
"Middle", which sits in 100 "Outer". The output (3) stays outside all three, so the file also
covers the mixed case where one edge crosses a container boundary.

**Synthetic fixture written by hand to `docs/reference/dag-contract.md`. It has never been
produced or opened by Alteryx.** `samples/wf_0002` only nests two deep; this goes one deeper so a
recursion that happens to work for two levels cannot pass.

What it guards: the structure class of parse failure. `parse.py` must recurse through
`<ChildNodes>` carrying the enclosing container's ToolID, so every node lands in `nodes` with the
right `container_id` — invariant 1 catches tools that a non-recursive walk would drop, and the
segmenter reads `container_id` for its soft cuts.
