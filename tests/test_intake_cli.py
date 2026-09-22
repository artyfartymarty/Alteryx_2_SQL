"""CLI-level behaviour the brief describes in prose but the verbatim test files don't exercise:
exit codes for both scripts (plan Global Constraints / implementer rules' "CLI EXIT CODES" note),
`--yxdb-dir`, the TTY-default for interactive vs `--no-interactive`/`--interactive`, the "local
copy" fallback re-scoring candidates, an interrupted interactive session still persisting partial
answers, and `logical_name`'s collision handling.
"""
from __future__ import annotations

import io as py_io
from pathlib import Path

import pytest

import intake_prompt as ip
import intake_touchpoints as tpx
from dev import answer_samples, build_samples
from lib import io
from lib.paths import Repo
from lib.yxdb import write_yxdb
from tests.helpers import copy_pristine_mappings_and_catalog

ROOT = Path(__file__).parents[1]


def _repo(tmp_path, wf_id="wf_0001"):
    copy_pristine_mappings_and_catalog(tmp_path)
    repo = Repo(tmp_path)
    build_samples.build(repo, ROOT / "samples", wf_id)
    return repo


def scripted(answers):
    it = iter(answers)
    said = []
    return (lambda prompt: (said.append(prompt), next(it))[1]), said


# --- intake_touchpoints.py CLI ------------------------------------------------------------------

def test_touchpoints_cli_exits_0_and_writes_the_file(tmp_path):
    repo = _repo(tmp_path)
    assert tpx.main(["wf_0001", "--root", str(tmp_path)]) == 0
    assert repo.wf("wf_0001", "intake", "touchpoints.json").is_file()


def test_touchpoints_cli_exits_2_for_a_workflow_that_was_never_parsed(tmp_path):
    (tmp_path / "workflows" / "wf_0009").mkdir(parents=True)
    with pytest.raises(SystemExit) as exc:
        tpx.main(["wf_0009", "--root", str(tmp_path)])
    assert exc.value.code == 2


def test_touchpoints_cli_exits_2_for_a_malformed_workflow_id(tmp_path):
    with pytest.raises(SystemExit) as exc:
        tpx.main(["../escape", "--root", str(tmp_path)])
    assert exc.value.code == 2


def test_touchpoints_cli_accepts_extra_yxdb_dirs(tmp_path):
    repo = _repo(tmp_path)
    # Delete the seeded copy so `find_yxdb` must fall through to --yxdb-dir.
    seeded = repo.wf("wf_0001", "source", "data", "orders.yxdb")
    header_fields = [{"name": "ORDER_ID", "type": "Int32", "size": 4, "scale": None}]
    seeded.unlink()
    extra = tmp_path / "elsewhere"
    extra.mkdir()
    write_yxdb(extra / "orders.yxdb", header_fields, [[1]])
    touchpoints = tpx.run(repo, "wf_0001", extra_dirs=[extra])
    t = next(x for x in touchpoints if x["key"] == "sales/orders.yxdb")
    assert t["field_source"] == "yxdb_header" and t["fields"] == ["ORDER_ID"]
    assert tpx.main(["wf_0001", "--yxdb-dir", str(extra), "--root", str(tmp_path)]) == 0


# --- intake_prompt.py CLI: exit codes -------------------------------------------------------------

def test_prompt_cli_exits_2_when_touchpoints_have_not_been_enumerated(tmp_path):
    repo = _repo(tmp_path)  # build_samples parses/segments but never runs intake_touchpoints
    with pytest.raises(SystemExit) as exc:
        ip.main(["wf_0001", "--no-interactive", "--root", str(tmp_path)])
    assert exc.value.code == 2


def test_prompt_cli_exits_2_for_a_malformed_workflow_id(tmp_path):
    with pytest.raises(SystemExit) as exc:
        ip.main(["not/a/valid/id", "--no-interactive", "--root", str(tmp_path)])
    assert exc.value.code == 2


def test_prompt_cli_rejects_conflicting_interactive_flags(tmp_path):
    with pytest.raises(SystemExit) as exc:
        ip.main(["wf_0001", "--interactive", "--no-interactive", "--root", str(tmp_path)])
    assert exc.value.code == 2


def test_prompt_cli_exits_1_when_waiting_for_answers(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")
    monkeypatch.setattr("builtins.input", lambda *_: "")  # never actually called (--no-interactive)
    # Nothing answered yet: every blocking touchpoint stays unresolved -> WAITING_FOR_ANSWERS -> exit 1.
    assert ip.main(["wf_0001", "--no-interactive", "--user", "wf_owner", "--root", str(tmp_path)]) == 1


def test_prompt_cli_exits_0_once_every_blocking_touchpoint_is_mapped(tmp_path):
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")
    ask, _ = scripted(["n", "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "ANALYTICS.CURATED.EXCLUDED_ORDERS", ""])
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "READY"
    assert ip.main(["wf_0001", "--no-interactive", "--root", str(tmp_path)]) == 0


def test_prompt_cli_defaults_to_non_interactive_off_a_tty(tmp_path, monkeypatch):
    """No --interactive/--no-interactive flag given, and stdin isn't a TTY (the pytest/CI case):
    the CLI must not block waiting for input -- it resumes exactly like --no-interactive."""
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def _boom(_prompt=""):
        raise AssertionError("input() must not be called when stdin is not a TTY")

    monkeypatch.setattr("builtins.input", _boom)
    assert ip.main(["wf_0001", "--root", str(tmp_path)]) == 1  # WAITING_FOR_ANSWERS: nothing answered


# --- `--user`'s default must never leak the OS login name into a non-interactive resume ----------
#
# `intake_prompt.py --no-interactive` is exactly the call the orchestrator makes on every
# automated pass (and what this project's own offline sample runs use to populate `workflows/`),
# with nobody at a keyboard. Defaulting `confirmed_by` to `getpass.getuser()` there would bake
# whatever OS account happens to run the resuming process into a committed artifact -- a
# machine-specific value, and not even an accurate one, since that account did not confirm
# anything (a human answered earlier, via open_questions.md or manifest.answers). These three
# tests replace `run` with a spy: what changed is `main`'s own default-selection logic, and
# `run`'s handling of a given `user` value is already covered elsewhere in this file.

def _spy_run(monkeypatch, result="READY"):
    captured: dict = {}

    def fake_run(repo, wf_id, *, interactive, user=None, **kwargs):
        captured["interactive"] = interactive
        captured["user"] = user
        return result

    monkeypatch.setattr(ip, "run", fake_run)
    return captured


def _forbid_getuser(monkeypatch, why):
    def _boom():
        raise AssertionError(why)

    monkeypatch.setattr("getpass.getuser", _boom)


def test_prompt_cli_defaults_user_to_automation_when_resuming_non_interactively(tmp_path, monkeypatch):
    _forbid_getuser(monkeypatch, "getpass.getuser() must not be called for a non-interactive resume")
    captured = _spy_run(monkeypatch)

    assert ip.main(["wf_0001", "--no-interactive", "--root", str(tmp_path)]) == 0
    assert captured == {"interactive": False, "user": "automation"}


def test_prompt_cli_user_flag_overrides_the_non_interactive_default(tmp_path, monkeypatch):
    _forbid_getuser(monkeypatch, "getpass.getuser() must not be called when --user is given")
    captured = _spy_run(monkeypatch)

    assert ip.main(["wf_0001", "--no-interactive", "--user", "morgan", "--root", str(tmp_path)]) == 0
    assert captured == {"interactive": False, "user": "morgan"}


def test_prompt_cli_defaults_user_to_the_os_login_name_when_interactive(tmp_path, monkeypatch):
    """The interactive case is the mirror image: a human really is at this keyboard, so the OS
    login name is still the sensible default -- only the non-interactive resume path changes."""
    monkeypatch.setattr("getpass.getuser", lambda: "fake_login")
    captured = _spy_run(monkeypatch)

    assert ip.main(["wf_0001", "--interactive", "--root", str(tmp_path)]) == 0
    assert captured == {"interactive": True, "user": "fake_login"}


# --- F1c (coordinator ruling): promotion to mappings/global.yaml -----------------------------------
#
# Program-wide answers are intended, reviewed state to commit -- they are promoted only for an
# interactive session or an explicitly-given `--user`, never for the default non-interactive
# `automation` identity. The consistency check against `mappings/global.yaml` is not the same
# thing as promotion, and keeps running regardless.

def _answer_wf_0001(tmp_path):
    assert answer_samples.main(["--root", str(tmp_path), "--samples", str(ROOT / "samples"),
                                "--only", "wf_0001"]) == 0


def test_prompt_cli_automation_default_never_promotes_leaves_global_yaml_byte_identical(tmp_path):
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")
    _answer_wf_0001(tmp_path)
    before = repo.global_mappings.read_bytes()

    assert ip.main(["wf_0001", "--no-interactive", "--root", str(tmp_path)]) == 0

    assert repo.global_mappings.read_bytes() == before
    # the workflow's own file still gets the answers -- nothing here was silently dropped.
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert m["sources"]["sales/orders.yxdb"]["snowflake"] == "SALES.RAW.ORDERS"


def test_prompt_cli_explicit_user_flag_promotes_to_global_yaml(tmp_path):
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")
    _answer_wf_0001(tmp_path)

    assert ip.main(["wf_0001", "--no-interactive", "--user", "jdoe", "--root", str(tmp_path)]) == 0

    g = io.read_yaml(repo.global_mappings)
    assert g["sources"]["sales/orders.yxdb"]["snowflake"] == "SALES.RAW.ORDERS"
    assert g["sources"]["sales/orders.yxdb"]["confirmed_by"] == "jdoe"


def test_prompt_cli_interactive_promotes_to_global_yaml(tmp_path, monkeypatch):
    # `run`'s `ask` parameter defaults to the real builtin `input`, bound once at module import
    # time, so monkeypatching `builtins.input` after the fact has no effect on it. Feeding
    # `sys.stdin` a scripted `StringIO` instead works because `input()` itself reads from
    # `sys.stdin` dynamically -- this drives the CLI exactly as `main()` would really be invoked
    # with a human piping answers in, rather than calling `ip.run` directly (this suite's usual
    # shortcut, which would bypass `main`'s own promote=interactive-or-explicit-user computation).
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")
    scripted_stdin = "n\n\nANALYTICS.CURATED.SALES_SUMMARY\n\nANALYTICS.CURATED.EXCLUDED_ORDERS\n\n"
    monkeypatch.setattr("sys.stdin", py_io.StringIO(scripted_stdin))

    assert ip.main(["wf_0001", "--interactive", "--root", str(tmp_path)]) == 0

    g = io.read_yaml(repo.global_mappings)
    assert g["sources"]["sales/orders.yxdb"]["snowflake"] == "SALES.RAW.ORDERS"


def test_prompt_cli_automation_default_still_detects_conflicts(tmp_path):
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")
    _answer_wf_0001(tmp_path)

    # A colleague's workflow already claimed this key for a different table.
    g = io.read_yaml(repo.global_mappings)
    g["sources"]["sales/orders.yxdb"] = {"snowflake": "OTHER.RAW.ORDERS", "logical": "ORDERS",
                                         "tool_ids": [], "confirmed_by": "someone",
                                         "load_path": "already_there"}
    io.write_yaml(repo.global_mappings, g)
    before = repo.global_mappings.read_bytes()

    assert ip.main(["wf_0001", "--no-interactive", "--root", str(tmp_path)]) == 1  # NEEDS_HUMAN

    oq = repo.wf("wf_0001", "intake", "open_questions.md").read_text(encoding="utf-8")
    assert "CONFLICT" in oq and "OTHER.RAW.ORDERS" in oq
    assert repo.global_mappings.read_bytes() == before  # the conflict is reported, not promoted


# --- the "local copy" fallback re-scores candidates -----------------------------------------------

def test_local_copy_of_a_missing_yxdb_updates_fields_and_rescoring(tmp_path):
    repo = _repo(tmp_path)
    repo.wf("wf_0001", "source", "data", "orders.yxdb").unlink()
    tpx.run(repo, "wf_0001")

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_path = local_dir / "my_copy.yxdb"
    fields = [{"name": n, "type": "V_String", "size": 20, "scale": None}
              for n in ("ORDER_ID", "CUSTOMER", "REGION", "AMOUNT_TXT", "QTY", "ORDER_DATE", "STATUS")]
    write_yxdb(local_path, fields, [["1", "Acme", "EAST", "10.00", "1", "2026-01-01", "OPEN"]])

    ask, said = scripted(["n", str(local_path), "", "ANALYTICS.CURATED.SALES_SUMMARY", "", "?"])
    out = []
    ip.run(repo, "wf_0001", interactive=True, ask=ask, out=out.append, user="wf_owner")
    # The local-copy question was asked (Q1's yxdb was deleted, never found on disk).
    assert any("Local copy of orders.yxdb" in s for s in said)
    # Candidates were re-scored off the local copy's header: SALES.RAW.ORDERS is a 7/7 match again.
    assert any("7/7 columns" in line for line in out)
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert m["sources"]["sales/orders.yxdb"]["snowflake"] == "SALES.RAW.ORDERS"


def test_unreadable_local_copy_is_reported_and_skipped_not_fatal(tmp_path):
    repo = _repo(tmp_path)
    repo.wf("wf_0001", "source", "data", "orders.yxdb").unlink()
    tpx.run(repo, "wf_0001")

    bogus = tmp_path / "not_a_yxdb.yxdb"
    bogus.write_text("definitely not a yxdb file", encoding="utf-8")

    ask, _ = scripted(["n", str(bogus), "?", "?", "?"])
    out = []
    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=out.append, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    assert any("Could not read" in line for line in out)


# --- an interrupted interactive session still persists what was answered -------------------------

def test_eof_during_prompting_persists_partial_answers(tmp_path):
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")

    answers = iter(["n", ""])  # answers the opening question and Q1, then stdin "closes"

    def ask(_prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError

    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    # Q1 (accepted via Enter, a columns-basis candidate) made it in before the interruption.
    assert m["sources"]["sales/orders.yxdb"]["snowflake"] == "SALES.RAW.ORDERS"
    # Q2/Q3 were never reached, so nothing was invented for them.
    assert m["outputs"] == {}


def test_keyboard_interrupt_during_prompting_persists_partial_answers(tmp_path):
    repo = _repo(tmp_path)
    tpx.run(repo, "wf_0001")

    def ask(_prompt):
        raise KeyboardInterrupt

    status = ip.run(repo, "wf_0001", interactive=True, ask=ask, out=lambda s: None, user="wf_owner")
    assert status == "WAITING_FOR_ANSWERS"
    m = io.read_yaml(repo.wf("wf_0001", "intake", "mappings.yaml"))
    assert m["sources"] == {} and m["outputs"] == {}


# --- logical_name -------------------------------------------------------------------------------

def test_logical_name_collision_then_further_collision():
    taken = set()
    assert ip.logical_name("SALES.RAW.ORDERS", taken) == "ORDERS"
    # Same table name, different database: <SCHEMA>_<TABLE>.
    assert ip.logical_name("ANALYTICS.RAW.ORDERS", taken) == "RAW_ORDERS"
    # A third source that collides even on <SCHEMA>_<TABLE>: numbered suffix.
    assert ip.logical_name("OTHER.RAW.ORDERS", taken) == "RAW_ORDERS_2"
    assert taken == {"ORDERS", "RAW_ORDERS", "RAW_ORDERS_2"}
