"""A runtime sandbox for agent-written Snowpark code (live hardening L4 fix round 2, X1).

The Snowpark Local Testing Framework runs a segment's `proc.py` as ordinary in-process Python, so
the static Snowpark rules (`lib.snowpark_rules`) are the only thing between the agent's code and the
host -- and they allow `pandas`/`numpy` wholesale, which expose object-loading and file-reading entry
points a gate-passing module can still call. This module is the runtime boundary the SQL path got
under P (DuckDB's locked external access), for the Snowpark path:

* The agent's code runs in a **child process** (`run_segment`, the parent-side entry, spawns
  `python scripts/lib/snowpark_sandbox.py <spec>`). The parent keeps full rights to write reports;
  the child's rights are taken away by a `sys.addaudithook` armed BEFORE `proc.py` is imported.
  An audit hook cannot be removed and lives for the whole process, which is why a fresh child is
  spawned for every execution rather than arming the hook in the validator itself.
* The hook (`_arm`/`_hook`) is DEFAULT-DENY (fix round 5): a deny-list loses to the next unlisted
  event -- the third escape used `pd.eval(..., engine="python")` to reach `_winapi.CreateFile`/
  `WriteFile`/`CreateProcess`, native calls that fired none of the `os.*`/`subprocess.*` events the
  deny-list watched -- so the hook now refuses EVERY event that is not on the recorded benign
  allow-list (`sandbox_events.json`, re-recorded by `scripts/dev/record_sandbox_events.py` from every
  canned and cookbook Snowpark procedure run in the sandbox), and refuses whole native families
  (`_winapi.*`, `winreg.*`, `ctypes.*`, `socket.*`, `subprocess.*`, `msvcrt.*`, `sqlite3.*`, `_wmi.*`,
  `urllib.*`) outright. It raises `SandboxViolation` -- and records it, so a module that swallows the
  exception is still failed -- with these argument checks kept for the events that carry a path or a
  name: any `open` for writing outside the per-run temp directory; any `open` for reading outside the
  temp directory and the interpreter's own installation (stdlib + site-packages, so pandas/Snowpark
  load their own modules; once armed NO workflow file is a read root, so the expected outputs in
  `golden/outputs/` are never readable, fix round 3); `os.listdir`/`scandir`/`stat`/`walk`/`glob`
  outside those read roots; `os.remove`/`rename`/`mkdir`/... outside the temp directory; `import` of
  `ctypes`/`socket`/`subprocess`/`multiprocessing`/`pickle`/`shelve`/`marshal`/`sqlite3`/`shutil`/
  `glob` after arming (the harness imports what it needs first); and `pickle.find_class` (always) and
  `marshal.loads`/`compile` (except from the import machinery). `exec` is a recorded benign event (the
  Snowpark Local Testing Framework `exec`s generated code from its own frames), safe to allow because
  the static gate leaves agent code no way to invoke `exec`/`eval`/`compile` at all.
* The child runs (fix round 3) under a wall-clock **timeout** (`SANDBOX_TIMEOUT_SECONDS`, overridable
  by env or CLI; its process tree is killed on expiry), in a **minimal environment** built by
  `_child_env` (only `PATH`, the temp variables and the OS's own system variables -- never
  `SNOWFLAKE_*`, tokens or the real user profile), and it **signs its result** with a per-run nonce:
  the nonce comes over stdin (fix round 4, E2, never an environment variable), is read and stdin
  closed before agent code runs, and lives only in a signing closure's cell -- not a module global or
  any live frame's local, so a frame walk cannot reach it. The parent rejects any result not signed
  with the nonce (`unauthenticated`), so neither a forged `result.json` nor a rewritten output file
  can fake a PASS. A memory cap is applied on POSIX (`RLIMIT_AS`); Windows has no cheap per-process
  cap, so the timeout bounds a runaway allocation there.

A refused event, a timeout, a raise from `run()`, or a read-back failure is reported to the parent
through the signed result file, never a traceback: the parent turns it into that segment's FAIL
naming the reason (`v.fail_report`). This is **defence in depth, not a security boundary**: the
sandbox was attacked three times (fix rounds 2-5 -- both gates are now allow-lists: the static gate
`lib.snowpark_rules` keeps `sys`, `pd.eval` and every off-list method out of reach, and this hook is
default-deny), but agent code still shares the harness's Python process, and the hook depends on the
static gate.
Production runs the pipeline in the program spec's container, or validates Snowpark procedures only on
Snowflake (`docs/production-backlog.md`, "Isolate agent-code execution").
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import sysconfig
import tempfile
import threading
import types
from pathlib import Path
from typing import Sequence

#: The wall-clock ceiling for one sandboxed run (fix round 3): a `proc.py` that never returns, or
#: spins, is killed and the segment FAILs `sandbox: timeout`. A constant with a CLI override
#: (`validate_snowpark.py --sandbox-timeout`) and an env override (`MIG_SNOWPARK_SANDBOX_TIMEOUT`);
#: no dedicated validator-settings file exists to hold a key.
SANDBOX_TIMEOUT_SECONDS = 600


class SandboxViolation(BaseException):
    """A sandboxed run reached for a file, the network or a process it may not. A `BaseException`
    subclass so an agent's `except Exception` cannot swallow it; and `_arm` records the first one
    in a module global besides raising, so a swallowed violation still fails the run."""


# --- the parent side: spawn a child, read its result -------------------------------------------

_HARNESS = Path(__file__).resolve()
_SCRIPTS_DIR = _HARNESS.parents[1]


class ChildResult:
    """One sandboxed run's outcome, as the parent reads it back.

    `kind`: "ok" (the run finished; `outputs` maps each declared output fqn to its read-back table
    or None when the procedure did not create it), "run" (the procedure raised), "sandbox" (the
    hook refused an event), "read_back" (a declared output could not be read back), "usage" (a
    prerequisite -- a missing golden file -- failed while building the session, before the agent's
    code ran: the caller re-raises it, an exit-2 usage error), "timeout" (the run did not finish in
    time and was killed), "unauthenticated" (the result payload the child wrote was not signed with
    this run's nonce -- a possible in-process forgery), "exited" (the child signalled that agent code
    had started, then exited with no authenticated result -- a terminal FAIL that is NEVER re-spawned,
    E7), or "spawn" (the child failed BEFORE agent code started -- a pure infrastructure failure, the
    one kind that is re-spawned once). `error` is the message for every non-ok kind; `error_type`
    names the exception for a "usage" kind, so the caller re-raises the same class."""

    def __init__(self, kind: str, *, error: str | None = None, outputs: dict | None = None,
                 error_type: str | None = None):
        self.kind = kind
        self.error = error
        self.outputs = outputs or {}
        self.error_type = error_type


def _child_env(tmp_dir: str, python: str) -> dict:
    """A minimal environment for the child (fix round 3, J2): only what Python and the Snowpark
    local framework need, never the parent's credentials. `PATH` is the interpreter's own
    directories; `HOME`/`USERPROFILE`/`TEMP`/`TMP`/`TMPDIR` point at the per-run temp directory (so
    any config lookup lands in the empty sandbox dir, which the hook lets the child read); no
    `SNOWFLAKE_*`, tokens, or the user's real profile is passed. The result nonce is NOT here (fix
    round 4, E2): it goes over stdin, so it is never in an environment agent code could read."""
    interpreter_dirs = [os.path.dirname(python), os.path.dirname(sys.executable), sys.prefix,
                        sys.base_prefix, os.path.join(sys.base_prefix, "DLLs")]
    path = os.pathsep.join(dict.fromkeys(d for d in interpreter_dirs if d and os.path.isdir(d)))
    env = {
        "PATH": path,
        "PYTHONPATH": os.pathsep.join([str(_SCRIPTS_DIR), str(_SCRIPTS_DIR.parent)]),
        "TEMP": tmp_dir, "TMP": tmp_dir, "TMPDIR": tmp_dir,
        "HOME": tmp_dir, "USERPROFILE": tmp_dir,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING", "utf-8"),
        "PYTHONHASHSEED": "0",
    }
    # System variables the OS itself needs (sockets, crypto, process start on Windows) -- not secrets.
    for var in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
                "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "LC_CTYPE", "TZ"):
        if var in os.environ:
            env[var] = os.environ[var]
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the child and every process it started (fix round 3, J1). On Windows only `taskkill /T`
    reaches the tree; elsewhere the child's own process group is signalled."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), 9)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_segment(*, source: str, display_path: str, run_id: str, outputs: Sequence[str],
                inputs: dict, python: str | None = None, timeout: float | None = None,
                record_events_to: str | None = None) -> ChildResult:
    """Run `source` (an already gated `proc.py`) in a sandboxed child and return its result.

    `inputs` is the child's session recipe: `{"mode": "golden", "root", "wf_id", "seg",
    "golden_set", "database", "contract"}` to load a golden set itself, or `{"mode": "tables",
    "tables": [{"fqn", "csv"}], "args": {...}}` to load a caller-serialized set (the chain).
    `outputs` are the fqns to read back, in order. `timeout` (seconds) bounds the whole run; None
    resolves to `MIG_SNOWPARK_SANDBOX_TIMEOUT` or `SANDBOX_TIMEOUT_SECONDS`. Nothing here runs
    `source`; the child does, under the hook, in a minimal environment, and signs its result with a
    per-run nonce the parent verifies.

    `record_events_to` is for `scripts/dev/record_sandbox_events.py` ONLY: a path where the child writes
    the audit events a benign run fires, with a logging hook in place of the enforcing one. It travels
    in the spec file this function writes, never in an environment variable, so nothing an agent can
    influence -- no environment, no argument the validators accept -- can switch enforcement off; the
    validators never pass it."""
    python = python or sys.executable
    if timeout is None:
        timeout = float(os.environ.get("MIG_SNOWPARK_SANDBOX_TIMEOUT", SANDBOX_TIMEOUT_SECONDS))

    # A child that failed BEFORE agent code started (kind "spawn") ran no agent code at all -- a pure
    # infrastructure failure (a transient spawn/import hiccup under a loaded machine). Re-spawning it,
    # with fresh inputs, is the SAME deterministic computation, not a retry of any agent result. Once
    # the child has signalled that agent code started (E7), an exit with no authenticated result is
    # kind "exited" -- a terminal FAIL, NEVER re-spawned, so the retry cannot be bought by exiting
    # mid-run. Every genuine outcome (ok/run/sandbox/read_back/timeout/unauthenticated/exited) is
    # returned on the first attempt and never re-run. The bound is one extra attempt.
    result = _spawn(source, display_path, run_id, outputs, inputs, python, timeout, record_events_to)
    if result.kind == "spawn":
        result = _spawn(source, display_path, run_id, outputs, inputs, python, timeout, record_events_to)
    return result


def _spawn(source: str, display_path: str, run_id: str, outputs: Sequence[str], inputs: dict,
           python: str, timeout: float, record_events_to: str | None = None) -> ChildResult:
    from lib import typed_csv  # noqa: PLC0415

    nonce = secrets.token_hex(32)
    with tempfile.TemporaryDirectory(prefix="snowpark-sandbox-") as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / "proc_source.py").write_text(source, encoding="utf-8", newline="\n")
        spec_inputs = dict(inputs)
        if spec_inputs.get("mode") == "tables":
            serialized = []
            for index, entry in enumerate(spec_inputs.get("tables") or []):
                csv = tmp_dir / f"in_{index}.csv"
                typed_csv.write_table(csv, entry["table"])
                serialized.append({"fqn": entry["fqn"], "csv": str(csv)})
            spec_inputs = {**spec_inputs, "tables": serialized}
        spec = {
            "source_path": str(tmp_dir / "proc_source.py"),
            "display_path": display_path,
            "run_id": run_id,
            "tmp_dir": str(tmp_dir),
            "outputs": list(outputs),
            "inputs": spec_inputs,
            "record_events_to": record_events_to,
        }
        (tmp_dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")

        env = _child_env(str(tmp_dir), python)
        # A new process group / session so the whole tree can be killed on timeout (J1).
        popen_kwargs: dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
            popen_kwargs["preexec_fn"] = _memory_cap()
        proc = subprocess.Popen([python, str(_HARNESS), str(tmp_dir / "spec.json")],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=str(_SCRIPTS_DIR.parent), env=env, **popen_kwargs)
        try:
            # The nonce goes over stdin, which the child reads and closes before importing agent
            # code (fix round 4, E2): it is never in the environment or on a live stack frame. The
            # child's stdout carries the E7 "agent code started" sentinel back.
            out, err = proc.communicate(input=nonce + "\n", timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.communicate()
            return ChildResult("timeout", error=f"the procedure did not finish within {int(timeout)}s")

        result_path = tmp_dir / "result.json"
        if not result_path.is_file():
            tail = (err or "").strip()[-800:]
            # E7: if the child signalled that agent code had started, an exit with no result is a
            # terminal FAIL -- the retry cannot be bought by exiting mid-run. Only a failure BEFORE
            # that signal (a child that never reached agent code) is the re-spawnable "spawn".
            if _STARTED_SENTINEL in (out or ""):
                return ChildResult("exited", error=f"the sandbox child exited after agent code started "
                                                   f"(exit {proc.returncode}): {tail}")
            return ChildResult("spawn", error=f"the sandbox child wrote no result (exit {proc.returncode}): {tail}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not _verify(result, nonce, tmp_dir):
            return ChildResult("unauthenticated", error="the sandbox produced an unauthenticated result")
        kind = result["kind"]
        if kind != "ok":
            return ChildResult(kind, error=result.get("error"), error_type=result.get("error_type"))
        outputs_map: dict = {}
        for entry in result["outputs"]:
            outputs_map[entry["fqn"]] = typed_csv.read_table(tmp_dir / entry["csv"]) if entry["present"] else None
        return ChildResult("ok", outputs=outputs_map)


def _memory_cap():
    """A POSIX `preexec_fn` capping the child's address space, or None where it is not cheap (fix
    round 3, J1). Windows has no `resource` module and a Job-Object cap needs the native API, so the
    child there is bounded by time only -- documented in the report."""
    try:
        import resource  # noqa: PLC0415  (POSIX only)
    except ImportError:
        return None

    def limit():
        cap = 4 * 1024 * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        except (ValueError, OSError):
            pass
    return limit


def _result_mac(result: dict, nonce: str) -> str:
    """The HMAC the child signs its result with and the parent checks (fix round 3): over the result
    without its own `mac`, with each present output's file digest already inside it, so neither a
    forged `result.json` nor a rewritten `out_*.csv` passes without the run's nonce."""
    payload = {key: value for key, value in result.items() if key != "mac"}
    return hmac.new(nonce.encode("utf-8"), json.dumps(payload, sort_keys=True).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _verify(result: dict, nonce: str, tmp_dir: Path) -> bool:
    mac = result.get("mac")
    if not isinstance(mac, str) or not hmac.compare_digest(mac, _result_mac(result, nonce)):
        return False
    for entry in result.get("outputs") or []:
        if entry.get("present"):
            digest = hashlib.sha256((tmp_dir / entry["csv"]).read_bytes()).hexdigest()
            if not hmac.compare_digest(digest, entry.get("sha", "")):
                return False
    return True


# --- the child side: arm the hook, then load and run -------------------------------------------

_FIRST_VIOLATION: str | None = None
_TMP_REAL: str = ""
_READ_ROOTS: tuple[str, ...] = ()

# --- fix round 5: the runtime hook is DEFAULT-DENY --------------------------------------------------
# A deny-list loses to the next unlisted event (the second escape used `_winapi.CreateFile`/`WriteFile`
# /`ReadFile`/`CreateProcess`, native calls that fire no `os.*`/`subprocess.*` event this hook watched
# for -- so nothing stopped them). The hook now refuses EVERY event that is not on the recorded
# allow-list, with the argument checks below kept for the events that carry a path or a module name.
#
# `_ALLOWED_EVENTS` is recorded from the benign corpus by `scripts/dev/record_sandbox_events.py` (every
# canned and cookbook Snowpark procedure, run in the sandbox with a logging hook) and committed as
# `sandbox_events.json`; `tests/test_snowpark_sandbox.py` fails if a benign procedure fires an event
# not on it, so the list is maintained, not guessed.
try:
    _ALLOWED_EVENTS = frozenset(json.loads((_HARNESS.parent / "sandbox_events.json").read_text(encoding="utf-8")))
except (OSError, ValueError):
    _ALLOWED_EVENTS = frozenset()

_BLOCKED_IMPORTS = frozenset({"ctypes", "socket", "subprocess", "multiprocessing", "pickle", "shelve",
                              "marshal", "sqlite3", "shutil", "glob"})
# Whole families refused OUTRIGHT (fix round 5): a native module's own functions fire audit events
# under these prefixes, and none of them is on the benign allow-list. `os.*` is NOT here -- an `os.*`
# event is allowed only when it is one of the recorded benign ones (e.g. `os.mkdir` under the temp
# directory); every other `os.*` falls through to the default-deny below.
_HARD_DENY_PREFIXES = ("_winapi.", "winreg.", "ctypes.", "socket.", "subprocess.", "msvcrt.",
                       "sqlite3.", "_wmi.", "urllib.")
_ALWAYS_BLOCKED = frozenset({"os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile"})
# Reads that reveal host directory structure or file metadata below the `open` layer -- allowed only
# when their path is under the temp directory or a read root (the interpreter install, so the import
# machinery can still stat/scan its own files). Each carries a path as its first argument.
_PATH_READS = frozenset({"os.listdir", "os.scandir", "os.stat", "os.lstat", "os.chdir", "os.readlink",
                         "os.walk", "os.fwalk", "glob.glob", "glob.iglob"})
# `pickle.find_class` reconstructs an arbitrary object AT unmarshal time and is the real code-run
# vector, so it is refused ALWAYS -- the import machinery never calls it (it uses `marshal`, not
# `pickle`), so no frame trust is involved (fix round 3, open question 2). `marshal.loads` is how the
# import machinery loads every `.pyc`, so a lazy import inside `to_pandas` raises it; it only returns
# an inert code object (running it needs `exec`, which the static rules forbid), so it is refused
# unless the call comes from the import system. `exec`/`compile` are the same: allowed only from the
# import system, except the one `exec` of the agent module this harness performs itself.
_MARSHAL_IF_NOT_IMPORT = "marshal.loads"
_MUTATIONS = frozenset({"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.makedirs", "os.link",
                        "os.symlink", "os.chmod", "os.chown", "os.truncate", "os.utime", "os.replace",
                        "os.renames", "os.removedirs"})
_WRITE_MODE_CHARS = frozenset("wax+")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC | getattr(os, "O_EXCL", 0)
#: The child writes this to stdout (the reverse of the stdin the nonce arrives on) right before it
#: runs agent code (E7): once the parent has seen it, an exit with no authenticated result is a FAIL
#: `sandbox: agent code exited`, never a re-spawn.
_STARTED_SENTINEL = "MIG_SANDBOX_AGENT_STARTED"


def _real(path) -> str | None:
    if isinstance(path, (bytes, bytearray)):
        try:
            path = os.fsdecode(path)
        except (ValueError, UnicodeDecodeError):
            return None
    if not isinstance(path, str) or not path:
        return None
    try:
        return os.path.realpath(os.path.abspath(path))
    except (OSError, ValueError):
        return path


def _under(path: str | None, roots: Sequence[str]) -> bool:
    if path is None:
        return False
    for root in roots:
        if root and (path == root or path.startswith(root + os.sep)):
            return True
    return False


def _violate(reason: str) -> None:
    global _FIRST_VIOLATION
    if _FIRST_VIOLATION is None:
        _FIRST_VIOLATION = reason
    raise SandboxViolation(reason)


def _is_write(mode, flags) -> bool:
    if isinstance(mode, str) and any(char in _WRITE_MODE_CHARS for char in mode):
        return True
    if isinstance(flags, int) and (flags & _WRITE_FLAGS):
        return True
    return False


def _from_import_system() -> bool:
    """Whether the nearest Python frames belong to the interpreter's own import machinery (which
    unmarshals every `.pyc`), so an unmarshal from there is a legitimate import, not a payload."""
    frame = sys._getframe(1)
    for _ in range(8):
        if frame is None:
            return False
        name = frame.f_code.co_filename
        if "importlib" in name or "_bootstrap" in name or "<frozen" in name:
            return True
        frame = frame.f_back
    return False


#: Per-thread re-entrancy guard: the hook's own introspection (`_from_import_system` calls
#: `sys._getframe`, which itself fires a `sys._getframe` audit event) must not be audited by the hook,
#: or a default-deny hook would refuse its own frame walk. Thread-local so a framework thread's event
#: is never skipped while the main thread is mid-hook. Agent code cannot run while the hook runs, and
#: cannot start a thread (the static gate refuses `threading`), so this only ever elides the hook's
#: own nested events, never an agent's.
_HOOK_STATE = threading.local()


def _hook(event: str, args) -> None:
    if getattr(_HOOK_STATE, "active", False):
        return
    _HOOK_STATE.active = True
    try:
        _hook_body(event, args)
    finally:
        _HOOK_STATE.active = False


def _hook_body(event: str, args) -> None:
    """Default-deny (fix round 5): every event that is not handled below and not on the recorded
    benign allow-list (`_ALLOWED_EVENTS`) is refused, so an event nobody listed -- the next
    `_winapi.*`, a made-up name -- is refused rather than allowed by omission."""
    # Whole families refused outright, before anything else (never on the benign allow-list).
    if event.startswith(_HARD_DENY_PREFIXES) or event in _ALWAYS_BLOCKED or event == "pickle.find_class":
        _violate(f"{event} is not allowed")
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        real = _real(path)
        if real is None:                                 # a file descriptor, not a path
            return
        if _is_write(mode, flags):
            if not _under(real, (_TMP_REAL,)):
                _violate(f"open for writing outside the sandbox temp directory: {os.path.basename(real)}")
            return
        if not _under(real, (_TMP_REAL, *_READ_ROOTS)):
            _violate(f"open for reading outside the golden data: {os.path.basename(real)}")
        return
    # `marshal.loads` and `compile` load / build code objects; they are legitimate only from the
    # import machinery (which unmarshals and compiles modules). `exec` is different: the Snowpark
    # Local Testing Framework legitimately `exec`s generated code from its own (non-import) frames
    # during a benign run, so `exec` is a recorded benign event (allowed by the allow-list below).
    # Agent code has NO way to fire an `exec` event itself -- `exec`/`eval`/`compile` are refused as
    # names by the static gate, and every string-that-is-executed route with them -- so allowing the
    # event does not give agent code the capability; the static gate is the control (defence in depth).
    if event == _MARSHAL_IF_NOT_IMPORT or event == "compile":
        if not _from_import_system():
            _violate(f"{event} is not allowed")
        return
    if event in _MUTATIONS:
        for arg in args:
            real = _real(arg)
            if real is not None and not _under(real, (_TMP_REAL,)):
                _violate(f"{event} outside the sandbox temp directory")
        return
    if event in _PATH_READS:
        real = _real(args[0]) if args else None
        if real is not None and not _under(real, (_TMP_REAL, *_READ_ROOTS)):
            _violate(f"{event} outside the golden data and the interpreter's own installation")
        return
    if event == "import":
        module = args[0] if args else ""
        if isinstance(module, str) and module.split(".")[0] in _BLOCKED_IMPORTS:
            _violate(f"import of {module.split('.')[0]!r} is not allowed after the sandbox is armed")
        return
    # Default-deny: allow only the recorded benign events, refuse everything else by name.
    if event not in _ALLOWED_EVENTS:
        _violate(f"{event} is not on the sandbox's allow-list of benign events")


#: Recording mode, for `scripts/dev/record_sandbox_events.py` only: when the spec the parent wrote
#: carries `record_events_to`, `_arm` installs a *logging* hook instead of the enforcing one, so a
#: benign procedure runs to completion and every audit event it fires -- after arming, through
#: read-back -- is captured. Selected by the parent-written spec, never by an environment variable,
#: so no environment can switch enforcement off; the validators never set it.
_RECORDED: set[str] = set()


def _record_hook(event: str, args) -> None:
    _RECORDED.add(event)


def _dump_recorded(record_to: str | None) -> None:
    if record_to:
        Path(record_to).write_text(json.dumps(sorted(_RECORDED)), encoding="utf-8")


def _arm(tmp_dir: str, record_to: str | None = None) -> None:
    """Install the audit hook. Everything the harness needs -- including every golden file -- is
    loaded BEFORE this, so after arming the only reads allowed are under the interpreter's own
    installation (so pandas/Snowpark can load their modules) and the per-run temp directory. The
    workflow's `golden/` is deliberately NOT a read root: agent code can never read the expected
    outputs in `golden/outputs/` (fix round 3, R3)."""
    global _TMP_REAL, _READ_ROOTS
    _TMP_REAL = _real(tmp_dir) or tmp_dir
    interpreter = {os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix)}
    for name in ("stdlib", "platstdlib", "purelib", "platlib", "include", "data"):
        try:
            interpreter.add(os.path.realpath(sysconfig.get_path(name)))
        except (KeyError, OSError):
            pass
    _READ_ROOTS = tuple(sorted(interpreter))
    sys.addaudithook(_record_hook if record_to else _hook)


def _make_signer(key: str, result_path: Path):
    """A result-signing closure (fix round 4, E2). The nonce `key` lives ONLY in this closure's cell,
    never in a module global or a local of any frame live while agent code runs: `_make_signer`
    returns (its frame dies) before the agent runs, and its caller keeps only the returned function.
    A frame walk of `f_locals` finds a function object, not the key; reaching the cell needs
    `__closure__`, which the static gate refuses. Read from stdin, never an environment variable."""
    def sign(result: dict) -> None:
        result["mac"] = _result_mac(result, key)
        result_path.write_text(json.dumps(result), encoding="utf-8")
    return sign


def _execute(code, display_path: str, session, args):
    """Run the agent's compiled module in its OWN frame (fix round 4, E2), whose locals are only the
    code, the session and the run args -- never the signing key, which lives in a module global. The
    signing happens back in `_main` AFTER this returns, when no agent frame is live."""
    module = types.ModuleType("proc_sandboxed")
    module.__file__ = display_path
    exec(code, module.__dict__)  # noqa: S102 -- gated by the parent, sandboxed by the hook
    module.run(session, *args)
    return module


def _main(spec_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    tmp_dir = spec["tmp_dir"]
    result_path = Path(tmp_dir) / "result.json"
    # The nonce comes over stdin (fix round 4, E2), read and stdin closed BEFORE any agent code runs:
    # it is never in the environment, and `_make_signer` captures it into a closure cell so it is
    # never a live frame's local or a module global. Its own frame dies before the agent runs.
    sign = _make_signer(sys.stdin.readline().strip(), result_path)
    try:
        sys.stdin.close()
    except (OSError, ValueError):
        pass

    # Everything the harness needs, imported BEFORE the hook is armed (so pandas/numpy cache their
    # own internal imports of the blocked modules, and the agent's later imports of pandas/numpy/
    # Snowpark find them cached and fire no event).
    import numpy  # noqa: F401,PLC0415
    import pandas  # noqa: F401,PLC0415
    import validate_snowpark as vsp  # noqa: PLC0415
    from lib import handoff, typed_csv  # noqa: PLC0415
    from lib.paths import Repo  # noqa: PLC0415
    import snowflake.snowpark.functions  # noqa: F401,PLC0415
    import snowflake.snowpark.types  # noqa: F401,PLC0415

    inputs = spec["inputs"]
    session = vsp.new_local_session()
    # Building the session (loading golden data) is the prerequisite phase, before the agent's code:
    # a missing/malformed golden file is a usage error the parent re-raises (exit 2), never a FAIL.
    try:
        if inputs["mode"] == "golden":
            repo = Repo(inputs["root"])
            run_args = vsp.load_set_snowpark(session, repo, inputs["wf_id"], inputs["golden_set"],
                                             database=inputs["database"])
            vsp._load_intermediates(session, repo, inputs["wf_id"], inputs["golden_set"], inputs["contract"])
            args = (run_args["SRC_DB"], run_args["SRC_SCHEMA"], run_args["TGT_DB"], run_args["TGT_SCHEMA"],
                    spec["run_id"])
        else:
            for entry in inputs["tables"]:
                handoff.load_into_snowpark(session, entry["fqn"], typed_csv.read_table(entry["csv"]))
            a = inputs["args"]
            args = (a["SRC_DB"], a["SRC_SCHEMA"], a["TGT_DB"], a["TGT_SCHEMA"], spec["run_id"])
    except (FileNotFoundError, ValueError) as exc:
        sign({"kind": "usage", "error": str(exc), "error_type": type(exc).__name__})
        return 2

    code = compile(Path(spec["source_path"]).read_text(encoding="utf-8"), spec["display_path"], "exec")

    # E7: signal "agent code started" to the parent over stdout (the reverse of the stdin the nonce
    # arrives on), flushed, BEFORE any agent code runs. After the parent has seen this, an exit with
    # no authenticated result is a FAIL `sandbox: agent code exited`, never a re-spawn; the one
    # re-spawn is only for a failure BEFORE this signal (a child that never got this far).
    try:
        sys.stdout.write(_STARTED_SENTINEL + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass

    _arm(tmp_dir, spec.get("record_events_to"))

    try:
        _execute(code, spec["display_path"], session, args)
    except SandboxViolation as exc:
        sign({"kind": "sandbox", "error": str(exc)})
        return 1
    except BaseException as exc:  # noqa: BLE001 -- any failure from the agent's code is a domain FAIL
        if _FIRST_VIOLATION is not None:
            sign({"kind": "sandbox", "error": _FIRST_VIOLATION})
            return 1
        sign({"kind": "run", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    if _FIRST_VIOLATION is not None:                     # a violation the agent swallowed
        sign({"kind": "sandbox", "error": _FIRST_VIOLATION})
        return 1

    outputs = []
    for index, fqn in enumerate(spec["outputs"]):
        try:
            table = handoff.table_from_snowpark(session, fqn)
        except handoff.ReadBackError as exc:
            sign({"kind": "read_back", "error": str(exc)})
            return 1
        if table is None:
            outputs.append({"fqn": fqn, "present": False})
        else:
            name = f"out_{index}.csv"
            typed_csv.write_table(Path(tmp_dir) / name, table)
            digest = hashlib.sha256((Path(tmp_dir) / name).read_bytes()).hexdigest()
            outputs.append({"fqn": fqn, "present": True, "csv": name, "sha": digest})
    _dump_recorded(spec.get("record_events_to"))
    sign({"kind": "ok", "outputs": outputs})
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1]))
