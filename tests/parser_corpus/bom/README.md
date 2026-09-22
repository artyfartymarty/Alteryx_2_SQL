# `bom` — UTF-8 with a byte order mark

`workflow.yxmd` is four tools (input → select → formula → output) saved **with a UTF-8 BOM**
(`EF BB BF`) in front of its XML declaration, which is what Alteryx Designer writes on Windows.

**Synthetic fixture written by hand to `docs/reference/dag-contract.md`. It has never been
produced or opened by Alteryx.**

What it guards: `parse.decode_xml` must strip the BOM before handing the text to the XML parser.
A BOM left in place makes `ET.fromstring` fail with "XML or text declaration not at start of
entity", which is the encoding class of parse failure in program spec §6.4.
