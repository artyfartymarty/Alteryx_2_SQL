# `cp1252` — no encoding declaration, and not valid UTF-8

`workflow.yxmd` is three tools (input → formula → output) with **no XML declaration at all** and a
tool annotation that holds `Contrôle des données` encoded as windows-1252, so the file contains the
raw bytes `F4` and `E9`. Decoding it as UTF-8 raises `UnicodeDecodeError`.

**Synthetic fixture written by hand to `docs/reference/dag-contract.md`. It has never been
produced or opened by Alteryx.** It is committed with `-text` in `.gitattributes` so git never
rewrites its bytes.

What it guards: `parse.decode_xml`'s last fallback. With no BOM and no declared encoding the
parser tries UTF-8 first and drops to cp1252 when that fails, instead of raising or replacing the
accented characters — the annotation is what the analyzer and the documenter read.
