"""Task P1: `scripts/dev/set_models.py` -- one command that sets a hosted-model id consistently
in `orchestrator.config.json`, every `.github/agents/<role>.agent.md` frontmatter and
`config.json` (docs/handoff-copilot-models.md §3). Every test operates on tmp copies of the
repo's real three files, so a run here never touches the repo's own committed config -- and so
`test_the_agent_frontmatter_contract_still_holds_after_set_models` is checking the real shape
`.github/agents/*.agent.md` actually has, not an invented fixture.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dev import set_models
from tests.test_agents_config import _split_frontmatter

ROOT = Path(__file__).resolve().parents[1]


def _seed(tmp_path: Path) -> Path:
    """Copies the repo's real orchestrator.config.json, config.json and every
    `.github/agents/<role>.agent.md` for the nine custom agents into tmp_path, byte for byte --
    the same three files `set_models.py` edits in place."""
    shutil.copy2(ROOT / "orchestrator.config.json", tmp_path / "orchestrator.config.json")
    shutil.copy2(ROOT / "config.json", tmp_path / "config.json")
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    for name in set_models.CUSTOM_AGENTS:
        shutil.copy2(ROOT / ".github" / "agents" / f"{name}.agent.md", agents_dir / f"{name}.agent.md")
    return tmp_path


def _run(tmp_path: Path, *args: str) -> int:
    return set_models.main([*args, "--root", str(tmp_path)])


def _orch(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "orchestrator.config.json").read_text(encoding="utf-8"))


def _cli(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


def _agent_text(tmp_path: Path, role: str) -> str:
    return (tmp_path / ".github" / "agents" / f"{role}.agent.md").read_text(encoding="utf-8")


LONG_CONTEXT_ROLES = "intake,analyzer,fixer,parser-recovery,validator"
LONG_CONTEXT_SET = {"intake", "analyzer", "fixer", "parser-recovery", "validator"}


def test_one_command_sets_every_place_consistently(tmp_path):
    _seed(tmp_path)
    before_orch = _orch(tmp_path)
    before_cli = _cli(tmp_path)
    before_bodies = {role: _split_frontmatter(_agent_text(tmp_path, role))[1] for role in set_models.CUSTOM_AGENTS}

    code = _run(
        tmp_path,
        "--default", "luna-x",
        "--role", "translator=astra-y",
        "--long-context-roles", LONG_CONTEXT_ROLES,
        "--effort", "documenter=low",
        "--effort", "reviewer=low",
    )
    assert code == 0

    after_orch = _orch(tmp_path)
    hosted = after_orch["profiles"]["hosted"]
    assert hosted["model"] == "luna-x"
    assert hosted["roleModels"] == {"translator": "astra-y"}
    assert hosted["roleContextTiers"] == {role: "long_context" for role in LONG_CONTEXT_SET}
    assert hosted["roleReasoningEffort"] == {"documenter": "low", "reviewer": "low"}
    # every other key of orchestrator.config.json is unchanged (parsed equality)
    assert after_orch["profiles"]["local"] == before_orch["profiles"]["local"]
    for key in before_orch:
        if key != "profiles":
            assert after_orch[key] == before_orch[key], key

    after_cli = _cli(tmp_path)
    agents = after_cli["subagents"]["agents"]
    for name in set_models.CUSTOM_AGENTS:
        expected_model = "astra-y" if name == "translator" else "luna-x"
        assert agents[name]["model"] == expected_model, name
        assert agents[name]["contextTier"] == ("long_context" if name in LONG_CONTEXT_SET else "default"), name
        assert "modelPolicy" not in agents[name], name
    for name in set_models.BUILTIN_AGENTS:
        assert agents[name]["model"] == "luna-x", name
        assert agents[name]["contextTier"] == "default", name
        assert "modelPolicy" not in agents[name], name
    # every other key of config.json is unchanged (parsed equality): maxConcurrency, maxDepth,
    # planModel, planEffortLevel, and every agent's own effortLevel
    assert after_cli["subagents"]["maxConcurrency"] == before_cli["subagents"]["maxConcurrency"]
    assert after_cli["subagents"]["maxDepth"] == before_cli["subagents"]["maxDepth"]
    assert after_cli["planModel"] == before_cli["planModel"]
    assert after_cli["planEffortLevel"] == before_cli["planEffortLevel"]
    for name in set_models.CUSTOM_AGENTS + set_models.BUILTIN_AGENTS:
        assert agents[name]["effortLevel"] == before_cli["subagents"]["agents"][name]["effortLevel"], name

    for role in set_models.CUSTOM_AGENTS:
        front, body = _split_frontmatter(_agent_text(tmp_path, role))
        expected_model = "astra-y" if role == "translator" else "luna-x"
        assert front["model"] == expected_model, role
        assert front["name"] == role, role
        assert body == before_bodies[role], f"{role}: body must be byte-identical"


def test_the_local_profile_is_never_touched(tmp_path):
    _seed(tmp_path)
    before = _orch(tmp_path)["profiles"]["local"]
    assert _run(tmp_path, "--default", "luna-x", "--role", "translator=astra-y") == 0
    after = _orch(tmp_path)["profiles"]["local"]
    assert after == before


def test_dry_run_writes_nothing_and_prints_the_changes(tmp_path, capsys):
    _seed(tmp_path)
    before_orch_bytes = (tmp_path / "orchestrator.config.json").read_bytes()
    before_cli_bytes = (tmp_path / "config.json").read_bytes()
    before_agent_bytes = {
        role: (tmp_path / ".github" / "agents" / f"{role}.agent.md").read_bytes() for role in set_models.CUSTOM_AGENTS
    }

    code = _run(tmp_path, "--default", "luna-x", "--role", "translator=astra-y", "--dry-run")
    assert code == 0
    out = capsys.readouterr().out
    assert "luna-x" in out
    assert "astra-y" in out
    assert "dry-run" in out.lower()

    assert (tmp_path / "orchestrator.config.json").read_bytes() == before_orch_bytes
    assert (tmp_path / "config.json").read_bytes() == before_cli_bytes
    for role in set_models.CUSTOM_AGENTS:
        assert (tmp_path / ".github" / "agents" / f"{role}.agent.md").read_bytes() == before_agent_bytes[role]


def test_an_unknown_role_or_effort_is_a_usage_error(tmp_path):
    _seed(tmp_path)
    before_orch_bytes = (tmp_path / "orchestrator.config.json").read_bytes()
    before_cli_bytes = (tmp_path / "config.json").read_bytes()

    with pytest.raises(SystemExit) as exc:
        set_models.main(["--default", "luna-x", "--role", "wizard=astra-y", "--root", str(tmp_path)])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        set_models.main(["--default", "luna-x", "--effort", "translator=extreme", "--root", str(tmp_path)])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        set_models.main(["--default", "luna-x", "--long-context-roles", "wizard", "--root", str(tmp_path)])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        set_models.main(["--default", "luna-x", "--role", "translatornoequals", "--root", str(tmp_path)])
    assert exc.value.code == 2

    # nothing was written by any of the four bad invocations above
    assert (tmp_path / "orchestrator.config.json").read_bytes() == before_orch_bytes
    assert (tmp_path / "config.json").read_bytes() == before_cli_bytes


def test_running_it_twice_is_a_no_op(tmp_path, capsys):
    _seed(tmp_path)
    args = (
        "--default", "luna-x",
        "--role", "translator=astra-y",
        "--long-context-roles", LONG_CONTEXT_ROLES,
        "--effort", "documenter=low",
        "--effort", "reviewer=low",
    )
    assert _run(tmp_path, *args) == 0
    after_first_orch = (tmp_path / "orchestrator.config.json").read_bytes()
    after_first_cli = (tmp_path / "config.json").read_bytes()
    after_first_agents = {
        role: (tmp_path / ".github" / "agents" / f"{role}.agent.md").read_bytes() for role in set_models.CUSTOM_AGENTS
    }

    capsys.readouterr()
    assert _run(tmp_path, *args) == 0
    out = capsys.readouterr().out
    assert "no changes" in out.lower()

    assert (tmp_path / "orchestrator.config.json").read_bytes() == after_first_orch
    assert (tmp_path / "config.json").read_bytes() == after_first_cli
    for role in set_models.CUSTOM_AGENTS:
        assert (tmp_path / ".github" / "agents" / f"{role}.agent.md").read_bytes() == after_first_agents[role]


# --- fix round 1 (M1): malformed input names the file (or agent), never a bare traceback ------

def test_a_malformed_orchestrator_config_json_is_a_usage_error_naming_the_file(tmp_path, capsys):
    _seed(tmp_path)
    before_cli_bytes = (tmp_path / "config.json").read_bytes()
    before_agent_bytes = {
        role: (tmp_path / ".github" / "agents" / f"{role}.agent.md").read_bytes() for role in set_models.CUSTOM_AGENTS
    }
    (tmp_path / "orchestrator.config.json").write_text("{ not valid json", encoding="utf-8")
    corrupted_bytes = (tmp_path / "orchestrator.config.json").read_bytes()

    with pytest.raises(SystemExit) as exc:
        set_models.main(["--default", "luna-x", "--root", str(tmp_path)])
    assert exc.value.code == 2

    err = capsys.readouterr().err
    assert "Traceback" not in err, f"a bare traceback leaked to stderr:\n{err}"
    assert "orchestrator.config.json" in err

    # nothing was written: the malformed file (and the other two) are byte-identical to before
    assert (tmp_path / "orchestrator.config.json").read_bytes() == corrupted_bytes
    assert (tmp_path / "config.json").read_bytes() == before_cli_bytes
    for role in set_models.CUSTOM_AGENTS:
        assert (tmp_path / ".github" / "agents" / f"{role}.agent.md").read_bytes() == before_agent_bytes[role]


def test_a_malformed_config_json_is_a_usage_error_naming_the_file(tmp_path, capsys):
    _seed(tmp_path)
    before_orch_bytes = (tmp_path / "orchestrator.config.json").read_bytes()
    before_agent_bytes = {
        role: (tmp_path / ".github" / "agents" / f"{role}.agent.md").read_bytes() for role in set_models.CUSTOM_AGENTS
    }
    (tmp_path / "config.json").write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        set_models.main(["--default", "luna-x", "--root", str(tmp_path)])
    assert exc.value.code == 2

    err = capsys.readouterr().err
    assert "Traceback" not in err, f"a bare traceback leaked to stderr:\n{err}"
    assert "config.json" in err

    assert (tmp_path / "orchestrator.config.json").read_bytes() == before_orch_bytes
    for role in set_models.CUSTOM_AGENTS:
        assert (tmp_path / ".github" / "agents" / f"{role}.agent.md").read_bytes() == before_agent_bytes[role]


def test_malformed_agent_frontmatter_is_a_usage_error_naming_the_agent(tmp_path, capsys):
    _seed(tmp_path)
    before_orch_bytes = (tmp_path / "orchestrator.config.json").read_bytes()
    before_cli_bytes = (tmp_path / "config.json").read_bytes()
    agent_path = tmp_path / ".github" / "agents" / "translator.agent.md"
    agent_path.write_text("no frontmatter fence here at all\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        set_models.main(["--default", "luna-x", "--root", str(tmp_path)])
    assert exc.value.code == 2

    err = capsys.readouterr().err
    assert "Traceback" not in err, f"a bare traceback leaked to stderr:\n{err}"
    assert "translator" in err

    assert (tmp_path / "orchestrator.config.json").read_bytes() == before_orch_bytes
    assert (tmp_path / "config.json").read_bytes() == before_cli_bytes


def test_the_agent_frontmatter_contract_still_holds_after_set_models(tmp_path):
    """tests/test_agents_config.py's frontmatter contract (exactly name/description/model, name
    == file stem) must still hold on the result -- run its own parser against the tmp copy."""
    _seed(tmp_path)
    assert _run(
        tmp_path,
        "--default", "luna-x",
        "--role", "translator=astra-y",
        "--long-context-roles", LONG_CONTEXT_ROLES,
    ) == 0
    for role in set_models.CUSTOM_AGENTS:
        front, _ = _split_frontmatter(_agent_text(tmp_path, role))
        assert set(front.keys()) == {"name", "description", "model"}, role
        assert front["name"] == role, role
        assert isinstance(front["description"], str) and front["description"]
