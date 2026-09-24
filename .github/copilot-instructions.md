- This repo migrates Alteryx workflows to Snowflake. Never deploy, never write outside the current workflow's folder.
- Sandbox schemas: MIG_WORK (procedures under test), MIG_GOLDEN (golden data). Never reference production schemas by name in SQL.
- Sources come only from workflows/<id>/intake/mappings.yaml or mappings/global.yaml.
- Numbers about data (counts, diffs, tolerances) come only from scripts/compare.py output. Never estimate.
- One CTE per Alteryx tool, named t<toolid>_<tooltype>, with the tool id in a comment.
- If you are unsure what a tool does, mark it unknown and stop; do not guess semantics.
- Procedures follow docs/reference/dag-contract.md and plan contract C4: linear statement list, logical source and target names only.
- Three output targets (docs/reference/output-targets.md). `scripts/target_check.py` proposes one per segment in
  segments/targets.json; the orchestrator pre-fills it into contract.json as `"target"` and the analyzer may only
  LOWER it (sql → snowpark → manual, never back towards sql). `sql` is today's stored procedure. `snowpark` is a Python
  stored procedure whose source of truth is proc.py (DataFrame API only, no session.sql); its proc.sql is
  RENDERED by scripts/render_snowpark.py and is never hand-written or hand-edited. A `dbt` workflow
  (manifest.json's `output_kind`) is ONE dbt project under workflows/<id>/dbt/ for the whole workflow, checked by
  `scripts/compile_check.py <id> --target dbt` and validated by `scripts/validate_dbt.py <id>`; agents never run
  `dbt` themselves. Read manifest.json's `output_kind` and contract.json's `target` before writing anything.
- Run every Python script as `python scripts/<name>.py …`, never through a `.venv/…` path: the orchestrator puts the
  project's interpreter first on PATH for every agent session, so `python` is that interpreter (a run root has no
  `.venv` of its own).
- Session rules (the orchestrator appends the same seven to every agent task):
  1. In every tool call use a path relative to the repository root (workflows/<id>/…, where <id> is the workflow
     your task names); never type an absolute path. The one exception is a temporary file that a tool result
     itself says holds that result's full output: read it with view or grep, exactly as named.
  2. List files with glob, read them with view and search them with grep; the shell only runs the commands your
     agent file names, its scripts as `python scripts/<name>.py …`, one command per shell call: `;`, `&&`, `|`,
     redirection and `$` are refused, so read a script's report file with view afterwards. `python` is already the
     project's interpreter: never check it.
  3. Among the workflows, only workflows/<id>/ is yours; never open another workflow's folder.
  4. A refused tool call is final: do not retry it in another form or through another tool. Continue with what is
     allowed, and if you cannot finish without it, say so in your notes (or, if your role keeps none, in the file
     you were asked to write) and stop.
  5. Do not read this pipeline's own scripts/ or orchestrator/ source to debug a difference: the validation report,
     the contract, the cookbook and docs/reference/ are the evidence.
  6. Never read the golden data in bulk: the contract describes every column. When an example helps, read at most one
     golden set's inputs (e.g. normal); the validator compares the rest.
  7. There is no network in these sessions: every page you need is in the repository (`cookbook/`, `docs/reference/`).
