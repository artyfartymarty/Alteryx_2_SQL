# `yxwz` — an analytic app

`app.yxwz` is an analytic app: the root element is still `AlteryxDocument`, the body is an ordinary
input → filter → output chain, and two extra tools drive it — a
`AlteryxGuiToolkit.Questions.DropDown.DropDown` interface tool (10) and an
`AlteryxBasePluginsGui.Action.Action` tool (11) that rewrites the filter's expression at run time.

**Synthetic fixture written by hand to `docs/reference/dag-contract.md`. It has never been
produced or opened by Alteryx.**

What it guards: the packaging class of parse failure. The `.yxwz` extension must set
`file_kind` without changing anything else, the `AlteryxGuiToolkit.Questions.*` prefix must
classify as `interface` (the map has no entry for `DropDown` specifically), and interface and
action tools must appear in `nodes` while staying out of `edges` (dag-contract §2), which is what
keeps invariant 1 satisfied without inventing data flow.
