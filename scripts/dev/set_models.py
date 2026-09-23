"""Sets a hosted-model id consistently in the three places this repo configures one
(docs/handoff-copilot-models.md §3): `orchestrator.config.json`'s `profiles.hosted`, every
`.github/agents/<role>.agent.md` frontmatter `model:`, and `config.json`'s (the Copilot CLI
config) `subagents.agents.<name>.model` / `contextTier`.

    python scripts/dev/set_models.py --default <id> [--role <role>=<id>]...
        [--long-context-roles r1,r2] [--effort <role>=<low|medium|high|xhigh|max>]...
        [--root .] [--dry-run]

Never contacts the network: checking an id against the live catalog is `orchestrate.ts
--check-models`'s job (docs/handoff-copilot-models.md §2), never this script's. `--dry-run`
prints exactly what would change and writes nothing.

Nine roles accept a model id: the eight orchestrator roles (`orchestrator/types.ts`'s `Role`)
plus `cookbook-curator`, which has a Copilot custom agent but no orchestrator session of its own.
Every one of the nine reaches its `.github/agents/*.agent.md` frontmatter and its `config.json`
sub-agent entry; only the eight orchestrator roles ever reach `orchestrator.config.json`'s
per-role maps (`roleModels`, `roleContextTiers`, `roleReasoningEffort`) -- `cookbook-curator`
never runs inside an orchestrator session, so it has no `Role`-keyed entry to set there.

Each run REPLACES the three per-role maps in `orchestrator.config.json` and the per-agent
`model`/`contextTier` in `config.json` wholesale, from this run's flags alone -- it does not
merge with whatever a previous run left behind. That is what makes running the same command
twice a no-op, and what makes "one command sets every place consistently" true: the maps always
reflect exactly what was asked for, never a previous run's leftovers plus this run's changes.

Exit codes follow the plan's Global Constraints: 0 written (or, with --dry-run, would be written)
exactly as asked; 2 for a bad role name, a bad --effort level, a malformed ROLE=VALUE, or any
other usage error or unexpected failure -- nothing is written. There is no domain-failure (1)
case: this script only writes what it is told, it never judges an id against anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):  # `python scripts/dev/set_models.py` puts scripts/ on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.io import read_json, write_json
from lib.paths import Repo, add_root_arg

#: The eight roles `orchestrator/types.ts`'s `Role` type lists -- the only ones that ever reach
#: orchestrator.config.json's per-role maps.
ORCHESTRATOR_ROLES = [
    "intake", "analyzer", "translator", "reviewer", "validator",
    "fixer", "parser-recovery", "documenter",
]
#: Every role with a Copilot custom agent (tests/test_agents_config.py's CUSTOM_AGENTS) -- the
#: full domain --role, --long-context-roles and --effort validate role names against.
CUSTOM_AGENTS = ORCHESTRATOR_ROLES + ["cookbook-curator"]
#: config.json's five built-in sub-agents (tests/test_agents_config.py's BUILTIN_AGENTS): never a
#: valid --role target, always set to --default.
BUILTIN_AGENTS = ["task", "explore", "research", "rubber-duck", "general-purpose"]

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

_FRONTMATTER_RE = re.compile(r"^(---\r?\n)(.*?)(\r?\n---\r?\n)", re.DOTALL)
_MODEL_LINE_RE = re.compile(r"(?m)^(model:[ \t]*)(.*)$")


class UsageError(Exception):
    """A bad invocation: exit 2, nothing written."""


# --- parsing ROLE=VALUE flags ---------------------------------------------------------------

def _parse_role_value(raw: str, flag: str) -> tuple[str, str]:
    role, sep, value = raw.partition("=")
    if not sep or not role or not value:
        raise UsageError(f"{flag} needs ROLE=VALUE, got {raw!r}")
    if role not in CUSTOM_AGENTS:
        raise UsageError(f"{flag}: unknown role {role!r} (one of {', '.join(CUSTOM_AGENTS)})")
    return role, value


def _parse_effort(raw: str) -> tuple[str, str]:
    role, value = _parse_role_value(raw, "--effort")
    if value not in EFFORT_LEVELS:
        raise UsageError(f"--effort: unknown level {value!r} for role {role!r} (one of {', '.join(EFFORT_LEVELS)})")
    return role, value


def _parse_roles_csv(raw: str) -> list[str]:
    roles = [r for r in raw.split(",") if r]
    for role in roles:
        if role not in CUSTOM_AGENTS:
            raise UsageError(f"--long-context-roles: unknown role {role!r} (one of {', '.join(CUSTOM_AGENTS)})")
    return roles


# --- the agent frontmatter rewrite: only the model: LINE changes, nothing else -------------------

def rewrite_agent_model(text: str, model_id: str) -> tuple[str, bool]:
    """`(new_text, changed)`. Rewrites only the `model:` frontmatter line's value; every other
    byte -- key order, other frontmatter keys, the whole Markdown body, the trailing newline --
    is copied through untouched."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("agent file has no `---` frontmatter fence")
    front = match.group(2)
    model_line = _MODEL_LINE_RE.search(front)
    if not model_line:
        raise ValueError("agent frontmatter has no `model:` line")
    if model_line.group(2) == model_id:
        return text, False
    new_front = front[: model_line.start(2)] + model_id + front[model_line.end(2):]
    new_text = text[: match.start(2)] + new_front + text[match.end(2):]
    return new_text, True


def _read_text(path: Path) -> str:
    # newline="" disables Python's universal-newline translation on both read and write, so
    # whatever line endings the file already has (this repo's agent files are LF) survive a
    # round-trip byte for byte -- text mode's default translation would rewrite LF to the
    # platform's os.linesep (CRLF) on Windows.
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# --- computing the new state of each file, recording a human-readable change list ----------------

def _set_or_clear(d: dict, key: str, value, changes: list[str], label: str) -> None:
    """Sets `d[key] = value`, or removes `key` entirely when `value` is falsy (`None` or `{}`) --
    matching write_json's parsed-equality contract: an empty map and an absent key mean the same
    thing, so a run with no overrides for a map must not leave a stale one behind."""
    old = d.get(key)
    if old == value or (not old and not value):
        return
    if value:
        d[key] = value
    else:
        d.pop(key, None)
    changes.append(f"{label}: {old!r} -> {value!r}")


def updated_orchestrator_config(
    config: dict,
    default_id: str,
    role_overrides: dict[str, str],
    long_context_roles: list[str],
    efforts: dict[str, str],
    changes: list[str],
) -> dict:
    """Mutates and returns `config` (`orchestrator.config.json`, already `json.loads`ed):
    `profiles.hosted.model`, and the three per-role maps replaced wholesale from THIS run's
    flags. `profiles.local` is never touched."""
    hosted = config.setdefault("profiles", {}).setdefault("hosted", {})
    _set_or_clear(hosted, "model", default_id, changes, "orchestrator.config.json profiles.hosted.model")

    role_models = {role: role_overrides[role] for role in ORCHESTRATOR_ROLES if role in role_overrides}
    _set_or_clear(hosted, "roleModels", role_models, changes, "orchestrator.config.json profiles.hosted.roleModels")

    context_tiers = {role: "long_context" for role in ORCHESTRATOR_ROLES if role in long_context_roles}
    _set_or_clear(hosted, "roleContextTiers", context_tiers, changes, "orchestrator.config.json profiles.hosted.roleContextTiers")

    role_effort = {role: efforts[role] for role in ORCHESTRATOR_ROLES if role in efforts}
    _set_or_clear(hosted, "roleReasoningEffort", role_effort, changes, "orchestrator.config.json profiles.hosted.roleReasoningEffort")
    return config


def updated_cli_config(
    config: dict,
    default_id: str,
    role_overrides: dict[str, str],
    long_context_roles: list[str],
    changes: list[str],
) -> dict:
    """Mutates and returns `config` (`config.json`, already `json.loads`ed): every one of the nine
    custom agents' `model` is its override or `--default`; every built-in's `model` is always
    `--default`; every agent's `contextTier` is `long_context` exactly for a role named in
    `--long-context-roles`, `default` otherwise; `modelPolicy` is removed everywhere it appears
    (docs/handoff-copilot-models.md §3: hosted routing is Luna Max for every role by policy, never
    a `modelPolicy: required` escalation). `effortLevel` and every other key are left untouched."""
    agents = config.setdefault("subagents", {}).setdefault("agents", {})
    for name in CUSTOM_AGENTS + BUILTIN_AGENTS:
        agent = agents.setdefault(name, {})
        model_id = role_overrides.get(name, default_id) if name in CUSTOM_AGENTS else default_id
        _set_or_clear(agent, "model", model_id, changes, f"config.json subagents.agents.{name}.model")
        tier = "long_context" if name in long_context_roles else "default"
        _set_or_clear(agent, "contextTier", tier, changes, f"config.json subagents.agents.{name}.contextTier")
        if "modelPolicy" in agent:
            del agent["modelPolicy"]
            changes.append(f"config.json subagents.agents.{name}.modelPolicy: removed")
    return config


def updated_agent_text(role: str, text: str, model_id: str, changes: list[str]) -> str:
    new_text, changed = rewrite_agent_model(text, model_id)
    if changed:
        changes.append(f".github/agents/{role}.agent.md model: -> {model_id}")
    return new_text


# --- CLI -------------------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--default", required=True, metavar="ID", help="model id for every role unless overridden")
    parser.add_argument("--role", action="append", default=[], metavar="ROLE=ID", help="override one role's model id (repeatable)")
    parser.add_argument("--long-context-roles", default="", metavar="ROLE[,ROLE...]", help="roles pinned to the long-context tier")
    parser.add_argument("--effort", action="append", default=[], metavar="ROLE=LEVEL", help="override one role's reasoning effort (repeatable)")
    add_root_arg(parser)
    parser.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    args = parser.parse_args(argv)

    try:
        role_overrides = dict(_parse_role_value(raw, "--role") for raw in args.role)
        long_context_roles = _parse_roles_csv(args.long_context_roles)
        efforts = dict(_parse_effort(raw) for raw in args.effort)

        repo = Repo(args.root)
        orch_path = repo.root / "orchestrator.config.json"
        cli_path = repo.root / "config.json"
        agents_dir = repo.root / ".github" / "agents"

        # A parse failure here (malformed JSON, or an agent file with no `---` fence / no
        # `model:` line) is converted to a UsageError naming the file -- and the agent, for
        # frontmatter -- rather than reaching the generic handler below, which would print a
        # bare traceback instead of one clean line.
        try:
            orch_config = read_json(orch_path)
        except json.JSONDecodeError as exc:
            raise UsageError(f"{orch_path} is not valid JSON: {exc}") from exc
        try:
            cli_config = read_json(cli_path)
        except json.JSONDecodeError as exc:
            raise UsageError(f"{cli_path} is not valid JSON: {exc}") from exc
        agent_texts = {role: _read_text(agents_dir / f"{role}.agent.md") for role in CUSTOM_AGENTS}

        changes: list[str] = []
        new_orch = updated_orchestrator_config(orch_config, args.default, role_overrides, long_context_roles, efforts, changes)
        new_cli = updated_cli_config(cli_config, args.default, role_overrides, long_context_roles, changes)
        new_agent_texts: dict[str, str] = {}
        for role, text in agent_texts.items():
            try:
                new_agent_texts[role] = updated_agent_text(role, text, role_overrides.get(role, args.default), changes)
            except ValueError as exc:
                raise UsageError(f"{role}: {agents_dir / f'{role}.agent.md'}: {exc}") from exc

        if changes:
            for line in changes:
                print(line)
        else:
            print("set_models: no changes")

        if args.dry_run:
            print("set_models: --dry-run, nothing written")
            return 0

        write_json(orch_path, new_orch)
        write_json(cli_path, new_cli)
        for role, text in new_agent_texts.items():
            _write_text(agents_dir / f"{role}.agent.md", text)
        return 0
    except UsageError as exc:
        parser.error(str(exc))  # exit 2: bad role name, bad effort level, malformed ROLE=VALUE, or a file that failed to parse
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    from lib.console import utf8_console
    utf8_console()
    sys.exit(main())
