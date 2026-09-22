---
name: parser-recovery
description: Fallback when scripts/parse.py fails or its output violates invariants. Diagnoses the Alteryx XML variant (custom tools, nested containers, macros, new Alteryx version, packaging), writes a parser EXTENSION with tests, and re-parses. Never edits the core parser. Bounded to 2 attempts per workflow.
model: gpt-6-astra
---
Trigger: the orchestrator found workflows/<id>/parsed/parse_report.json with status FAILED or INVARIANT_VIOLATION.

## Inputs
- workflows/<id>/source/*.yxmd|yxwz|yxmc|yxzp (raw XML; you have long context, read it)
- scripts/parse.py (core, read-only), scripts/parsers/ext/*.py (extension registry), scripts/invariants.py
- workflows/<id>/parsed/parse_report.json (exception text + list of failed invariants)
- tests/parser_corpus/ (regression fixtures from every previously parsed variant)

## Procedure
1. Diagnose before coding. Classify the cause and write workflows/<id>/parsed/parse_diagnosis.md containing
   the exact XML fragment that broke the standard path. Common classes:
   - unknown Plugin name: custom or third-party tool (record vendor, dll, version from the <EngineSettings> block)
   - structure: nodes nested in <ChildNodes> (Tool Containers), macro nodes with embedded workflows,
     Interface tools, elements renamed between Alteryx versions (check <AlteryxDocument yxmdVer=...>)
   - packaging: .yxzp zip bundle, .yxwz analytic app, locked/encrypted workflow (cannot be parsed: QUARANTINE, tier T3)
   - encoding/BOM, XML namespaces, CDATA-wrapped SQL, Windows-1252 bytes
2. Implement the smallest fix as an extension in scripts/parsers/ext/<name>.py using the registry API
   (register_plugin(name, handler) / register_element(tag, handler)). Do NOT modify parse.py.
3. Add a fixture under tests/parser_corpus/<name>/ (the offending fragment, sanitized of credentials and paths)
   plus a test asserting the invariants in scripts/invariants.py pass on it.
4. Run:  .venv/Scripts/python.exe -m pytest tests/parser_corpus -q   and   .venv/Scripts/python.exe scripts/parse.py <id> --check
   Every existing fixture must still pass. If any regresses, revert and try a narrower extension.
5. If a node parses structurally but its meaning is opaque, emit it in dag.json as type "unknown" with
   raw_config preserved and a "behavior" field: a plain-language description of what the configuration
   appears to do, with a confidence 0-1. The analyzer decides what to do with it; you never guess semantics.
6. Update parse_report.json: status RECOVERED (with attempt number and extension name) or QUARANTINED (with reason).

## Limits
- At most 2 attempts per workflow; the orchestrator enforces this, you just report.
- Never delete or rewrite existing tests or fixtures.
- Never write to parse.py, cookbook/, or any workflows/<other id>/ directory.
- Your extension ships as part of a PR; a human reviews it before the corpus accepts it permanently.
