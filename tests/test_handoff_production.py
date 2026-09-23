"""docs/handoff-production.md and docs/production-backlog.md are followed literally by an agent,
so nothing they name may rot."""
from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "handoff-production.md"
BACKLOG = ROOT / "docs" / "production-backlog.md"
DOCS = (GUIDE, BACKLOG)
_PATH = re.compile(r"`((?:scripts|orchestrator|docs|tests|mappings|samples|cookbook|workflows|\.github)/[^`\s*<>]+"
                   r"|orchestrate\.ts|config\.json|orchestrator\.config\.json|requirements\.txt)`")
_PY = re.compile(r"\.venv/Scripts/python\.exe (scripts/[\w/]+\.py)")
_FLAG = re.compile(r"orchestrate\.ts[^\n`]*?(--[a-z-]+)")
GUIDE_SECTIONS = ("## 1. Copilot Enterprise models", "## 2. Snowflake access", "## 3. Real Alteryx workflows",
                  "## 4. The verification ladder", "## 5. What has never been proven", "## Never do")
BACKLOG_ITEMS = ("Alteryx tool coverage", "Sources and sinks outside Snowflake", "Golden-data governance",
                 "Parallel run and reconciliation", "Scheduling", "Environment promotion",
                 "Continuous integration", "Reproducible installs", "Cost and status visibility",
                 "Model evaluation", "Prompt-injection tests")
BACKLOG_FIELDS = ("**What is missing.**", "**Why it matters.**", "**What exists to build on.**",
                  "**First concrete step.**")


def _all_text() -> str:
    return "\n".join(doc.read_text(encoding="utf-8") for doc in DOCS)


def test_the_guide_sections_are_present_in_order():
    text = GUIDE.read_text(encoding="utf-8")
    positions = [text.index(s) for s in GUIDE_SECTIONS]
    assert positions == sorted(positions)


def test_the_guide_links_the_backlog_as_the_first_week_checklist():
    text = GUIDE.read_text(encoding="utf-8")
    assert "`docs/production-backlog.md`" in text and "first-week checklist" in text


def test_every_backlog_item_has_its_four_fields():
    sections = re.split(r"^## ", BACKLOG.read_text(encoding="utf-8"), flags=re.MULTILINE)
    for item in BACKLOG_ITEMS:
        body = next((s for s in sections if s.startswith(item)), None)
        assert body is not None, f"production-backlog.md has no '## {item}' section"
        for field in BACKLOG_FIELDS:
            assert field in body, f"'{item}' lacks {field}"


def test_every_named_path_exists():
    missing = [p for p in sorted(set(_PATH.findall(_all_text()))) if not (ROOT / p.rstrip("/.,:")).exists()]
    assert not missing, missing


@pytest.mark.parametrize("script", sorted(set(_PY.findall(
    "\n".join(d.read_text(encoding="utf-8") for d in DOCS if d.exists())))))
def test_every_named_script_answers_help(script):
    done = subprocess.run([sys.executable, str(ROOT / script), "--help"], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr


def test_every_orchestrate_flag_is_parsed():
    cli = (ROOT / "orchestrator" / "cli.ts").read_text(encoding="utf-8")
    for flag in sorted(set(_FLAG.findall(_all_text()))):
        assert f'case "{flag}"' in cli, flag


# --- beyond the brief's six: what the brief asks for in prose ---------------------------------------

#: The two gaps the dispatch added to the ledger's eleven (the P3 review's scale observation, and the
#: adapter the dbt `snowflake` output needs); each carries the same four fields.
MORE_BACKLOG_ITEMS = ("Segmenter scale", "The dbt-snowflake adapter")

#: Top-level files and trees `_PATH` does not cover, but the two documents name.
_MORE_PATHS = re.compile(r"`((?:snowflake|catalog)/[^`\s*<>]+|README\.md|\.gitignore|package\.json"
                         r"|pyproject\.toml|tsconfig\.json)`")
#: A TypeScript entry point named on a Node command line (`--experimental-strip-types [--test] <file>.ts`).
_TS = re.compile(r"--experimental-strip-types (?:--test )?([\w/.-]+\.ts)\b")
#: One documented Python command: the script, then everything up to the end of its logical line
#: (a trailing backslash continues it -- tried first, so the backslash is not taken as plain text),
#: an inline-code backtick, a shell comment or a pipe.
_PY_COMMAND = re.compile(r"\.venv/Scripts/python\.exe (scripts/[\w/]+\.py)((?:\\\n|[^\n`#|])*)")
#: One documented orchestrate.ts command line, the same way.
_ORCHESTRATE_COMMAND = re.compile(r"orchestrate\.ts((?:\\\n|[^\n`#|])*)")
_OPTION = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")


def _section(text: str, heading: str) -> str:
    """The body of the `## `-level section whose heading starts with `heading`."""
    start = text.index(heading)
    following = re.search(r"^## ", text[start + len(heading):], flags=re.MULTILINE)
    return text[start:start + len(heading) + following.start()] if following else text[start:]


@functools.cache
def _help(script: str) -> str:
    done = subprocess.run([sys.executable, str(ROOT / script), "--help"], capture_output=True, text=True,
                          cwd=ROOT, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return done.stdout


def test_the_two_added_backlog_items_have_their_four_fields():
    sections = re.split(r"^## ", BACKLOG.read_text(encoding="utf-8"), flags=re.MULTILINE)
    for item in MORE_BACKLOG_ITEMS:
        body = next((s for s in sections if s.startswith(item)), None)
        assert body is not None, f"production-backlog.md has no '## {item}' section"
        for field in BACKLOG_FIELDS:
            assert field in body, f"'{item}' lacks {field}"


def test_the_backlog_says_it_is_written_down_not_built_and_where_its_numbers_come_from():
    opening = BACKLOG.read_text(encoding="utf-8").split("\n## ", 1)[0]
    assert "not built" in opening
    assert "copied from a command" in opening


def test_the_reproducible_installs_item_quotes_pip_freeze_lines_for_the_verified_stack():
    body = _section(BACKLOG.read_text(encoding="utf-8"), "## Reproducible installs")
    assert "pip freeze" in body
    for package in ("snowflake-snowpark-python", "snowflake-connector-python", "dbt-core", "dbt-duckdb",
                    "duckdb", "sqlglot", "PyYAML", "pandas", "pytest"):
        assert re.search(rf"^{re.escape(package)}==\d[\w.]*$", body, flags=re.MULTILINE), package


def test_every_other_named_repo_file_exists():
    missing = [p for p in sorted(set(_MORE_PATHS.findall(_all_text()))) if not (ROOT / p.rstrip("/.,:")).exists()]
    assert not missing, missing


def test_every_node_entry_point_named_exists():
    named = sorted(set(_TS.findall(_all_text())))
    assert "orchestrate.ts" in named and "scripts/dev/list_models.ts" in named, named
    missing = [p for p in named if not (ROOT / p).is_file()]
    assert not missing, missing


def test_every_option_on_a_documented_python_command_is_one_its_script_accepts():
    """Every `--option` a documented command passes appears in that script's own `--help`."""
    unknown = []
    for script, rest in _PY_COMMAND.findall(_all_text()):
        accepted = _help(script)
        assert accepted, f"{script} --help printed nothing"
        for option in _OPTION.findall(rest):
            if not re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", accepted):
                unknown.append(f"{script} {option}")
    assert not unknown, sorted(set(unknown))


def test_every_option_on_a_documented_orchestrate_command_is_parsed():
    """All of a command line's options, not only the first one `_FLAG` finds."""
    cli = (ROOT / "orchestrator" / "cli.ts").read_text(encoding="utf-8")
    options = {option for rest in _ORCHESTRATE_COMMAND.findall(_all_text()) for option in _OPTION.findall(rest)}
    assert "--check-models" in options and "--runner" in options, sorted(options)
    unknown = sorted(option for option in options if f'case "{option}"' not in cli)
    assert not unknown, unknown


def test_each_step_of_the_first_three_parts_ends_in_a_verify_checklist():
    text = GUIDE.read_text(encoding="utf-8")
    for heading in GUIDE_SECTIONS[:3]:
        steps = re.split(r"^### ", _section(text, heading), flags=re.MULTILINE)[1:]
        assert steps, f"{heading} has no ### steps"
        for step in steps:
            assert "**Verify.**" in step, f"{heading} step '{step.splitlines()[0]}' has no **Verify.** checklist"


def test_each_rung_of_the_ladder_has_pass_criteria_and_what_to_record():
    rungs = re.split(r"^### ", _section(GUIDE.read_text(encoding="utf-8"), GUIDE_SECTIONS[3]),
                     flags=re.MULTILINE)[1:]
    assert [rung.split(" ", 2)[:2] for rung in rungs] == [["Rung", f"{n}."] for n in range(1, 6)], \
        [rung.splitlines()[0] for rung in rungs]
    for rung in rungs:
        assert "**Pass.**" in rung and "**Record.**" in rung, rung.splitlines()[0]


def test_the_first_real_account_rung_deploys_wf_0001_into_the_sandbox_and_validates_it_there():
    rung_3 = re.split(r"^### ", _section(GUIDE.read_text(encoding="utf-8"), GUIDE_SECTIONS[3]),
                      flags=re.MULTILINE)[3]
    assert rung_3.startswith("Rung 3.")
    deploy = rung_3.index("scripts/deploy.py wf_0001")
    validate = rung_3.index("scripts/validate_segment.py wf_0001 seg_01")
    assert deploy < validate
    assert "--execute" in rung_3 and "--backend snowflake" in rung_3


def test_the_never_do_list_names_every_forbidden_action():
    never = _section(GUIDE.read_text(encoding="utf-8"), "## Never do")
    for must in ("credential", "token", "/login", "gh auth", "push", "`orchestrator/policy.ts`",
                 "`scripts/compare.py`", "repo root", "`docs/spec/`", "`--execute`", "sandbox"):
        assert must in never, must


def test_the_copilot_models_hand_off_opens_with_the_pointer():
    first_lines = (ROOT / "docs" / "handoff-copilot-models.md").read_text(encoding="utf-8").splitlines()[:6]
    assert any("Production hand-off in full: `docs/handoff-production.md`" in line for line in first_lines)


def test_the_readme_points_at_the_guide_from_the_targets_section_7_and_8_and_at_the_backlog_from_7():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = readme[readme.index("### Three output targets"):readme.index("## 2. ")]
    section_7 = _section(readme, "## 7. ")
    section_8 = _section(readme, "## 8. ")
    for name, body in (("§1 targets", targets), ("§7", section_7), ("§8", section_8)):
        assert "`docs/handoff-production.md`" in body, name
    assert "`docs/production-backlog.md`" in section_7


# --- fix round 2 ------------------------------------------------------------------------------------

#: The guide's execution order, as its `## Execution order` section must name it: the parts are
#: interleaved with the ladder's rungs, so reading top to bottom is NOT the order to run in (I1).
EXECUTION_ORDER = ("§0", "Rung 1", "§1.1–1.4", "Rung 2", "§1.5", "§2.1–2.5", "Rung 3", "§2.6", "§3.1–3.3",
                   "Rung 4", "§3.4–3.7", "Rung 5")
#: Options of tools other than this repository's scripts and orchestrate.ts that the prose names.
EXTERNAL_OPTIONS = {"--experimental-strip-types", "--test", "--noEmit", "--porcelain", "--git-dir", "--fill",
                    "--using", "--stat", "--version"}
_FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
#: An option in any case (`tsc --noEmit`), unlike `_OPTION`, which the command pins above share.
_ANY_OPTION = re.compile(r"(?<![\w-])(--[A-Za-z][A-Za-z-]*)")


def test_the_guide_opens_with_an_explicit_execution_order_naming_every_part_and_rung():
    text = GUIDE.read_text(encoding="utf-8")
    assert "top to bottom" not in text, "the parts are not run in reading order"
    assert text.index("## Execution order") < text.index("## 0. ")
    order = _section(text, "## Execution order")
    position = 0
    for step in EXECUTION_ORDER:
        found = order.find(step, position)
        assert found >= 0, f"'{step}' is missing from the execution order, or out of order"
        position = found + len(step)


def test_the_steps_that_belong_to_a_rung_say_when_to_run_them():
    text = GUIDE.read_text(encoding="utf-8")
    steps = {step.split(" ", 1)[0]: step for step in re.split(r"^### ", text, flags=re.MULTILINE)[1:]}
    assert "only at rung 2" in steps["1.5"]
    assert "only at rung 3" in steps["2.6"]
    for step in ("3.4", "3.5", "3.6", "3.7"):
        assert "only at rung 4" in steps[step], step


def test_every_option_named_in_prose_is_one_a_script_or_orchestrate_accepts():
    """The commands are pinned above; this pins the `--options` named inside inline code in prose."""
    cli = (ROOT / "orchestrator" / "cli.ts").read_text(encoding="utf-8")
    scripts = [path.relative_to(ROOT).as_posix() for path in [*ROOT.glob("scripts/*.py"), *ROOT.glob("scripts/dev/*.py")]
               if '\nif __name__ == "__main__":' in path.read_text(encoding="utf-8")]
    helps = "\n".join(_help(script) for script in scripts)
    prose = _FENCE.sub("", _all_text())
    options = {option for span in _INLINE.findall(prose) for option in _ANY_OPTION.findall(span)}
    assert "--execute" in options and "--gh" in options, sorted(options)
    unknown = sorted(option for option in options - EXTERNAL_OPTIONS
                     if f'case "{option}"' not in cli
                     and not re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", helps))
    assert not unknown, unknown
