"""Live hardening L4 fix round 2 (X1): the runtime sandbox for agent-written Snowpark code.

`lib.snowpark_sandbox.run_segment` runs a segment's `proc.py` in a child process whose audit hook is
armed BEFORE the module is imported. These tests drive that harness directly with malicious sources
(so they reach the sandbox regardless of the static gate, which `test_snowpark_rules.py` covers), and
end to end through `validate_snowpark` with the static gate defeated, to show the sandbox is the real
boundary: every attack is a FAIL naming the event, and the marker file is never written.

Nothing here runs on Snowflake; the double is the Snowpark Local Testing Framework, in the child.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import lib.validation as v
import validate_snowpark as vsp
from lib import snowpark_sandbox
from lib.io import read_json
from tests.helpers import prepare_workflow

WF, SEG = "wf_0006", "seg_02"


@pytest.fixture(scope="module")
def canned(tmp_path_factory):
    repo = prepare_workflow(tmp_path_factory.mktemp("sandbox"), WF)
    contract = read_json(repo.seg(WF, SEG, "contract.json"))
    return repo, contract


def _run(repo, contract, source, marker=None, timeout=None):
    if marker is not None and marker.exists():
        marker.unlink()
    fqns = [v.actual_table(WF, SEG, o) for o in contract["outputs"]]
    return snowpark_sandbox.run_segment(
        source=source, display_path=str(repo.seg(WF, SEG, "proc.py")), run_id="r1", outputs=fqns,
        timeout=timeout,
        inputs={"mode": "golden", "root": str(repo.root), "wf_id": WF, "seg": SEG,
                "golden_set": "normal", "database": vsp.SANDBOX_DB, "contract": contract})


def _canned_source(repo) -> str:
    return repo.seg(WF, SEG, "proc.py").read_text(encoding="utf-8")


def _with(repo, injected: str) -> str:
    return _canned_source(repo).replace("import pandas as pd\n", f"import pandas as pd\n{injected}\n", 1)


# --- the benign canned procedure runs through the child and passes -----------------------------

def test_the_canned_procedure_runs_in_the_child_and_reads_its_output_back(canned):
    repo, contract = canned
    result = _run(repo, contract, _canned_source(repo))
    assert result.kind == "ok", result.error
    table = result.outputs[v.actual_table(WF, SEG, contract["outputs"][0])]
    assert table is not None and [f["name"] for f in table["fields"]] == ["CUSTOMER", "PERIOD", "RECOGNIZED", "DEFERRED"]


def test_the_child_run_overhead_is_acceptable(canned):
    repo, contract = canned
    started = time.perf_counter()
    result = _run(repo, contract, _canned_source(repo))
    elapsed = time.perf_counter() - started
    assert result.kind == "ok"
    assert elapsed < 30, f"one sandboxed run took {elapsed:.1f}s"   # ~2s in practice; a generous ceiling


# --- every attack is refused, and the marker is never written ----------------------------------

def test_a_write_to_the_repo_root_is_refused(canned, tmp_path):
    repo, contract = canned
    marker = tmp_path / "MARKER.txt"
    result = _run(repo, contract, _with(repo, f'open(r"{marker}", "w").write("x")'), marker)
    assert result.kind == "sandbox" and "writing" in result.error
    assert not marker.exists()


def test_a_write_via_pandas_to_the_repo_root_is_refused(canned, tmp_path):
    """R2 refuses `to_csv` statically; here the static gate is not consulted (run_segment runs what it
    is given), so the runtime sandbox is what stops the pandas write."""
    repo, contract = canned
    marker = tmp_path / "PANDAS.csv"
    src = _with(repo, f'pd.DataFrame({{"a": [1]}}).to_csv(r"{marker}")')
    result = _run(repo, contract, src, marker)
    assert result.kind == "sandbox" and "writing" in result.error
    assert not marker.exists()


def test_a_read_of_a_file_outside_the_golden_directory_is_refused(canned, tmp_path):
    repo, contract = canned
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    result = _run(repo, contract, _with(repo, f'_s = open(r"{secret}").read()'))
    assert result.kind == "sandbox" and "reading" in result.error


def test_a_read_via_pandas_of_a_host_file_is_refused(canned, tmp_path):
    repo, contract = canned
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n", encoding="utf-8")
    result = _run(repo, contract, _with(repo, f'_s = pd.read_csv(r"{secret}")'))
    assert result.kind == "sandbox"


@pytest.mark.parametrize("inject, needle", [
    ("import socket\n_s = socket.socket()", "socket"),
    ("import subprocess\nsubprocess.Popen(['cmd'])", "subprocess.Popen"),
    ("import os\nos.system('echo hi')", "os.system"),
    # urllib reaches the network; which audit event fires first is OS-specific, so only the refusal
    # is asserted. (`ctypes.CDLL(None)` is not a reliable trigger on Windows -- it raises before any
    # `ctypes.*` event -- so the `ctypes.*` prefix block is exercised by the hook unit, not here.)
    ("import urllib.request\nurllib.request.urlopen('http://127.0.0.1:9')", None),
])
def test_process_and_network_reaches_are_refused(canned, inject, needle):
    repo, contract = canned
    result = _run(repo, contract, _with(repo, inject))
    assert result.kind == "sandbox"
    if needle is not None:
        assert needle in result.error


@pytest.mark.parametrize("event, args", [
    ("ctypes.dlopen", ("libc",)),
    ("ctypes.dlsym", (1, "system")),
    ("winreg.OpenKey", (1, "SOFTWARE")),
    ("socket.connect", (object(), ("1.2.3.4", 80))),
    ("os.exec", ("cmd", ["cmd"], {})),
])
def test_the_hook_refuses_native_network_and_process_events_directly(event, args, tmp_path):
    """Unit-level cover for events a subprocess cannot trigger portably (`ctypes.CDLL` raises before
    its event on Windows): the hook refuses each `ctypes.*`/`winreg.*`/`socket.*` and process event."""
    snowpark_sandbox._TMP_REAL = str(tmp_path)
    snowpark_sandbox._READ_ROOTS = (str(tmp_path),)
    with pytest.raises(snowpark_sandbox.SandboxViolation):
        snowpark_sandbox._hook(event, args)


def test_a_file_mutation_outside_the_temp_directory_is_refused(canned, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    repo, contract = canned
    result = _run(repo, contract, _with(repo, f'import os\nos.remove(r"{victim}")'))
    assert result.kind == "sandbox"
    assert victim.exists(), "the sandbox let agent code delete a host file"


def test_an_object_load_that_would_run_code_is_refused(canned, tmp_path):
    """The re-review's `probe_pickle`: a payload whose reconstruction runs code, unpickled at run
    time. `import pickle` is refused statically, so the static gate would already stop this; the
    sandbox stops it at `pickle.find_class` even with the gate defeated -- the marker never appears."""
    import pickle
    marker = tmp_path / "PICKLE.txt"

    class Payload:
        def __reduce__(self):
            return (exec, (f'open({str(marker)!r}, "w").write("x")',))
    blob = pickle.dumps(Payload(), protocol=0)
    repo, contract = canned
    result = _run(repo, contract, _with(repo, f"import pickle\npickle.loads({blob!r})"), marker)
    assert result.kind == "sandbox" and "pickle.find_class" in result.error
    assert not marker.exists()


def test_a_swallowed_violation_still_fails_the_run(canned, tmp_path):
    repo, contract = canned
    marker = tmp_path / "SWALLOW.txt"
    src = _with(repo, f'try:\n    open(r"{marker}", "w").write("x")\nexcept BaseException:\n    pass')
    result = _run(repo, contract, src, marker)
    assert result.kind == "sandbox"
    assert not marker.exists()


# --- end to end through validate_snowpark, with the static gate defeated -----------------------

def test_validate_snowpark_fails_and_writes_no_marker_when_the_gate_is_defeated(canned, tmp_path, monkeypatch):
    """Even if the static rules were somehow bypassed (they are not -- `test_snowpark_rules.py` keeps
    them), the runtime sandbox makes a malicious `proc.py` a FAIL with no host effect. The gate is
    monkeypatched to pass so the malicious source reaches the child, as the re-review's probe showed
    a gate-passing module reaching the host before this round."""
    repo, contract = canned
    marker = tmp_path / "E2E.txt"
    seg_dir = repo.seg(WF, SEG)
    original = seg_dir.joinpath("proc.py").read_text(encoding="utf-8")
    try:
        seg_dir.joinpath("proc.py").write_text(_with(repo, f'open(r"{marker}", "w").write("x")'),
                                               encoding="utf-8", newline="\n")
        monkeypatch.setattr(vsp.snowpark_rules, "segment_rule_errors", lambda *a, **k: [])
        report = vsp.validate_snowpark(repo, WF, SEG, ["normal"])
    finally:
        seg_dir.joinpath("proc.py").write_text(original, encoding="utf-8", newline="\n")
    assert report["verdict"] == "FAIL" and report["needs_human"] is False
    assert "sandbox refused" in report["error"]
    assert not marker.exists()


# --- fix round 3: time limit, minimal environment, no golden reads, authenticated results --------

def test_a_procedure_that_never_returns_is_killed_and_fails_with_a_timeout(canned):
    repo, contract = canned
    hang = _with(repo, "while True:\n    pass")
    started = time.perf_counter()
    result = _run(repo, contract, hang, timeout=3)
    assert result.kind == "timeout" and "3s" in result.error
    assert time.perf_counter() - started < 20, "the child was not killed promptly"


def test_a_secret_in_the_parent_environment_is_invisible_to_agent_code(canned, monkeypatch):
    repo, contract = canned
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "hunter2-topsecret")
    monkeypatch.setenv("MIG_SECRET_TOKEN", "abc123token")
    src = _with(repo, 'import os\nraise RuntimeError("ENV=" + os.environ.get("SNOWFLAKE_PASSWORD", "ABSENT")'
                      ' + "/" + os.environ.get("MIG_SECRET_TOKEN", "ABSENT"))')
    result = _run(repo, contract, src)
    assert result.kind == "run"
    assert "hunter2-topsecret" not in result.error and "abc123token" not in result.error
    assert "ENV=ABSENT/ABSENT" in result.error


def test_the_nonce_is_not_in_the_child_environment(canned):
    repo, contract = canned
    src = _with(repo, 'import os\nraise RuntimeError("NONCE=" + os.environ.get("MIG_SANDBOX_NONCE", "GONE"))')
    result = _run(repo, contract, src)
    assert result.kind == "run" and "NONCE=GONE" in result.error


def test_agent_code_cannot_read_an_expected_output_after_the_hook_is_armed(canned):
    repo, contract = canned
    expected = next(repo.wf(WF, "golden", "outputs", "normal").glob("*.csv"))
    result = _run(repo, contract, _with(repo, f'_x = open(r"{expected}").read()'))
    assert result.kind == "sandbox" and "reading" in result.error


def test_a_valid_result_authenticates_and_tampering_is_rejected(tmp_path):
    (tmp_path / "out_0.csv").write_bytes(b"a,b\n1,2\n")
    import hashlib
    nonce = "ab" * 32
    result = {"kind": "ok", "outputs": [{"fqn": "X", "present": True, "csv": "out_0.csv",
              "sha": hashlib.sha256((tmp_path / "out_0.csv").read_bytes()).hexdigest()}]}
    result["mac"] = snowpark_sandbox._result_mac(result, nonce)
    assert snowpark_sandbox._verify(result, nonce, tmp_path)
    assert not snowpark_sandbox._verify(result, "00" * 32, tmp_path)          # wrong nonce
    assert not snowpark_sandbox._verify({**result, "kind": "sandbox"}, nonce, tmp_path)  # forged payload
    assert not snowpark_sandbox._verify({"kind": "ok", "outputs": []}, nonce, tmp_path)  # no mac
    (tmp_path / "out_0.csv").write_bytes(b"a,b\n9,9\n")                        # rewritten output file
    assert not snowpark_sandbox._verify(result, nonce, tmp_path)


def test_pickle_find_class_is_refused_regardless_of_the_calling_frame(canned, tmp_path):
    """Open question 2: `pickle.find_class` (the code-run vector) no longer trusts the frame, so the
    `_from_import_system` allowance can never launder it. The benign run still passes (imports use
    `marshal`, not `pickle.find_class`)."""
    import pickle
    marker = tmp_path / "PK.txt"

    class Payload:
        def __reduce__(self):
            return (exec, (f'open({str(marker)!r}, "w").write("x")',))
    blob = pickle.dumps(Payload(), protocol=0)
    repo, contract = canned
    result = _run(repo, contract, _with(repo, f"import pickle\npickle.loads({blob!r})"), marker)
    assert result.kind == "sandbox" and "pickle.find_class" in result.error
    assert not marker.exists()
    assert _run(repo, contract, _canned_source(repo)).kind == "ok"


# --- fix round 4 (the sandbox attack): the hook covers sqlite/listings and the key is off the stack --

def test_sqlite_connect_to_a_host_file_is_refused(canned, tmp_path):
    """E1b: `sqlite3.connect` opens its file below the `open` layer; the hook now refuses a connect
    to a path outside the temp dir (`sqlite_pass_escape.py`'s runtime enabler)."""
    repo, contract = canned
    marker = tmp_path / "escape.db"
    src = _with(repo, f'import sqlite3\nsqlite3.connect(r"{marker}").execute("CREATE TABLE t(x)")')
    result = _run(repo, contract, src, marker)
    assert result.kind == "sandbox" and "sqlite3.connect" in result.error
    assert not marker.exists()


def test_an_in_memory_sqlite_connection_is_refused_by_the_default_deny_hook(tmp_path):
    """Fix round 5: the whole `sqlite3.*` family is refused outright now (an allow-list, not a
    deny-list), so even an in-memory connect is refused -- no benign Snowpark procedure uses sqlite."""
    snowpark_sandbox._TMP_REAL = str(tmp_path)
    with pytest.raises(snowpark_sandbox.SandboxViolation):
        snowpark_sandbox._hook("sqlite3.connect", (":memory:",))


def test_a_host_directory_listing_is_refused(canned):
    """E3: `os.listdir('C:/Users')` returned a real listing before; now refused."""
    repo, contract = canned
    result = _run(repo, contract, _with(repo, 'import os\n_x = os.listdir("C:/Users")'))
    assert result.kind == "sandbox" and "os.listdir" in result.error


@pytest.mark.parametrize("event, args", [
    ("sqlite3.connect", ("C:/x.db",)),
    ("os.listdir", ("C:/Users",)),
    ("os.scandir", ("C:/Windows",)),
    ("os.stat", ("C:/Windows/System32/drivers/etc/hosts",)),
    ("os.chdir", ("C:/",)),
    ("os.utime", ("C:/x",)),
])
def test_the_hook_refuses_sqlite_listing_and_metadata_events_directly(event, args, tmp_path):
    snowpark_sandbox._TMP_REAL = str(tmp_path)
    snowpark_sandbox._READ_ROOTS = (str(tmp_path),)
    with pytest.raises(snowpark_sandbox.SandboxViolation):
        snowpark_sandbox._hook(event, args)


def test_the_signing_nonce_is_on_no_live_frame_during_the_run(canned):
    """E2, strengthened by fix round 5's default-deny: a payload that tries to walk frames for the
    64-hex nonce cannot even begin -- `sys._getframe` is not on the benign allow-list, so the frame
    walk is refused at the runtime hook (on top of the nonce living only in a closure cell, and the
    static gate refusing `sys`/`_getframe`/`f_locals` outright)."""
    walk = ("import pandas as pd\nimport sys\n_found = []\n_f = sys._getframe(1)\n"
            "for _i in range(12):\n"
            "    if _f is None:\n        break\n"
            "    for _k, _val in list(_f.f_locals.items()):\n"
            "        if isinstance(_val, str) and len(_val) == 64:\n            _found.append(_k)\n"
            "    _f = _f.f_back\n"
            'raise RuntimeError("HEX64=" + ";".join(_found))\n')
    repo, contract = canned
    result = _run(repo, contract, _with(repo, walk).replace("import pandas as pd\nimport pandas as pd\n", "import pandas as pd\n"))
    assert result.kind == "sandbox" and "sys._getframe" in result.error, result.error


def test_a_gate_passing_module_cannot_reach_sys_end_to_end(canned, tmp_path, monkeypatch):
    """End to end through validate_snowpark: the attack's `T.sys.modules['sqlite3'].connect(host)` is
    refused at the static gate now, so it never even reaches the child. (Belt: with the gate defeated,
    the runtime hook still refuses the sqlite connect -- covered above.)"""
    repo, contract = canned
    marker = tmp_path / "E2E.db"
    body = _with(repo, f'import snowflake.snowpark.types as T\n_db = T.sys.modules["sqlite3"]\n_db.connect(r"{marker}")')
    seg_dir = repo.seg(WF, SEG)
    original = seg_dir.joinpath("proc.py").read_text(encoding="utf-8")
    try:
        seg_dir.joinpath("proc.py").write_text(body, encoding="utf-8", newline="\n")
        report = vsp.validate_snowpark(repo, WF, SEG, ["normal"])
    finally:
        seg_dir.joinpath("proc.py").write_text(original, encoding="utf-8", newline="\n")
    assert report["verdict"] == "FAIL"
    assert not marker.exists()


# --- fix round 4 (flaky-run addendum): a no-result child is re-spawned once, a verdict never is ----

def test_a_no_result_spawn_is_respawned_once(monkeypatch):
    """A child that wrote no result (kind "spawn") ran no agent code -- a transient infrastructure
    failure. `run_segment` re-spawns it once (the same deterministic computation), which is why a
    loaded machine no longer turns one hiccup into a chain FAIL. This is not a retry of any agent
    result: every genuine outcome is returned on the first attempt."""
    calls = []

    def fake_spawn(*args, **kwargs):
        calls.append(1)
        return (snowpark_sandbox.ChildResult("spawn", error="no result") if len(calls) == 1
                else snowpark_sandbox.ChildResult("ok", outputs={"X": None}))
    monkeypatch.setattr(snowpark_sandbox, "_spawn", fake_spawn)
    result = snowpark_sandbox.run_segment(source="", display_path="x", run_id="r", outputs=[], inputs={})
    assert result.kind == "ok" and len(calls) == 2


@pytest.mark.parametrize("kind", ["run", "sandbox", "read_back", "timeout", "unauthenticated", "exited", "ok"])
def test_a_genuine_child_outcome_is_never_respawned(monkeypatch, kind):
    calls = []

    def fake_spawn(*args, **kwargs):
        calls.append(1)
        return snowpark_sandbox.ChildResult(kind, error="e")
    monkeypatch.setattr(snowpark_sandbox, "_spawn", fake_spawn)
    result = snowpark_sandbox.run_segment(source="", display_path="x", run_id="r", outputs=[], inputs={})
    assert result.kind == kind and len(calls) == 1


def test_a_persistent_spawn_failure_is_reported_after_the_second_attempt(monkeypatch):
    calls = []

    def fake_spawn(*args, **kwargs):
        calls.append(1)
        return snowpark_sandbox.ChildResult("spawn", error="still no result")
    monkeypatch.setattr(snowpark_sandbox, "_spawn", fake_spawn)
    result = snowpark_sandbox.run_segment(source="", display_path="x", run_id="r", outputs=[], inputs={})
    assert result.kind == "spawn" and len(calls) == 2


# --- fix round 5: the runtime hook is default-deny, and the allow-list is recorded, not guessed ----

@pytest.mark.parametrize("event, args", [
    ("_winapi.CreateFile", ("C:/x", 0)),           # the second escape's native file route
    ("_winapi.CreateProcess", ("cmd", "cmd")),     # the second escape's native process route
    ("sqlite3.connect", ("C:/x.db",)),             # a whole family refused outright now
    ("os.system", ("echo hi",)),                   # a process reach
    ("winreg.OpenKey", (1, "SOFTWARE")),
    ("msvcrt.open_osfhandle", (1, 0)),
    ("totally.made.up.event", ("anything",)),      # an event nobody listed -- refused by default
])
def test_the_default_deny_hook_refuses_any_event_it_was_not_given(event, args, tmp_path):
    """The core of fix round 5: an event that is neither specifically handled nor on the recorded
    benign allow-list is refused -- so the next unlisted native call (`_winapi.*`) is stopped, unlike
    the deny-list that let it through. Called directly, as the round-4 unit tests do."""
    snowpark_sandbox._TMP_REAL = str(tmp_path)
    snowpark_sandbox._READ_ROOTS = (str(tmp_path),)
    with pytest.raises(snowpark_sandbox.SandboxViolation):
        snowpark_sandbox._hook(event, args)


@pytest.mark.parametrize("event", ["builtins.id", "object.__getattr__"])
def test_a_recorded_benign_event_is_allowed_by_the_hook(event, tmp_path):
    """The other side of default-deny: an event the benign corpus really fires (recorded in
    `sandbox_events.json`) is allowed, so a legitimate procedure is not refused."""
    snowpark_sandbox._TMP_REAL = str(tmp_path)
    snowpark_sandbox._READ_ROOTS = (str(tmp_path),)
    snowpark_sandbox._hook(event, ("anything",))   # must not raise


def test_the_committed_allow_list_matches_what_the_benign_corpus_records():
    """The list is maintained, not guessed: re-recording every benign Snowpark procedure fires no
    event the committed `sandbox_events.json` does not already list (fix round 5 acceptance)."""
    from dev.record_sandbox_events import record_events, _EVENTS_FILE
    recorded = record_events()
    committed = set(read_json(_EVENTS_FILE))
    assert recorded <= committed, f"benign runs fired unlisted events: {sorted(recorded - committed)}"
    # and the committed list carries nothing the corpus never fires (no dead grants beyond the
    # handful of specifically-checked events open/import/exec/marshal.loads/os.mkdir).
    assert committed == recorded, f"committed but never fired: {sorted(committed - recorded)}"


def test_no_environment_variable_can_switch_the_enforcing_hook_off(canned, tmp_path, monkeypatch):
    """Recording mode (a logging hook in place of the enforcing one) is selected only by the spec
    `run_segment(record_events_to=...)` writes -- never by the environment. With the variable an
    earlier draft of this round read set in the parent, a read outside the read roots is still
    refused and nothing is recorded."""
    record = tmp_path / "events.json"
    monkeypatch.setenv("MIG_SANDBOX_RECORD_EVENTS", str(record))
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    repo, contract = canned
    result = _run(repo, contract, _with(repo, f'_s = open(r"{secret}").read()'))
    assert result.kind == "sandbox" and "reading" in result.error
    assert not record.exists()


def test_a_benign_procedure_that_raises_after_starting_is_a_fail_not_a_respawn(canned):
    """E7 end to end: a procedure that starts and then raises is kind 'run' (a FAIL), returned on
    the first attempt -- the child signalled 'started', so `run_segment` never re-spawns it."""
    repo, contract = canned
    result = _run(repo, contract, _with(repo, 'raise ValueError("boom after start")'))
    assert result.kind == "run" and "boom after start" in result.error


def test_an_exit_after_the_started_signal_is_terminal_and_not_respawned(monkeypatch):
    """E7 unit: once the child has emitted the started sentinel, `_spawn` classifies a no-result exit
    as 'exited' (a terminal FAIL), and `run_segment` never re-spawns it -- the retry cannot be bought
    by exiting mid-run. A failure BEFORE the signal is still the re-spawnable 'spawn'."""
    calls = []

    def fake_spawn(*args, **kwargs):
        calls.append(1)
        return snowpark_sandbox.ChildResult("exited", error="child exited after start")
    monkeypatch.setattr(snowpark_sandbox, "_spawn", fake_spawn)
    result = snowpark_sandbox.run_segment(source="", display_path="x", run_id="r", outputs=[], inputs={})
    assert result.kind == "exited" and len(calls) == 1


def test_the_started_sentinel_distinguishes_exited_from_spawn():
    """The classification hinges on the sentinel being present in the child's stdout: present -> the
    terminal 'exited'; absent -> the re-spawnable 'spawn'. This pins the exact rule E7 turns on."""
    assert snowpark_sandbox._STARTED_SENTINEL == "MIG_SANDBOX_AGENT_STARTED"
