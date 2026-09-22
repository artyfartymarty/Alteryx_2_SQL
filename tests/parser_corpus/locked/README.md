# `locked` — a workflow whose body cannot be read

`locked.yxmd` has no `<Nodes>` element at all; the body is a `<Locked>` element holding base64.

**This fixture is invented.** We have no locked or encrypted Alteryx workflow to copy, and none
could be committed here even if we had one, so the base64 is noise we wrote by hand — it decodes
to an English sentence saying exactly that, not to an encrypted workflow. The shape (no `<Nodes>`,
a `<Locked>` sibling of `<Properties>`) is our reading of program spec §6.1, **not** something
observed from Alteryx Designer. If a real locked file ever turns up and disagrees, this fixture is
the thing to correct.

What it guards: `parse.run` must report `QUARANTINED` with reason `locked` and exit non-zero,
rather than emitting an empty `dag.json` that later stages would treat as a workflow with no
tools. Program spec §6.1 sends these to tier T3 and asks the owner for the unlocked original;
`<EncryptedNodes>` is handled the same way.
