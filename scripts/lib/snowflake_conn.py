"""Named Snowflake connections -- the only way this repository reaches a real account (plan Task P2).

A connection is chosen by NAME and nothing else: the name of an entry in the human's own
`connections.toml` (the file `snowflake-connector-python` and Snowpark read; see
`docs/reference/snowflake-backend.md`), given as `--connection NAME` or in `MIG_SNOWFLAKE_CONNECTION`.
Account, user, role, warehouse and the authenticator (key pair or SSO) live in that file on the
human's machine. No password, token or private key is ever accepted as an argument, read from this
repository or written to it; `SnowflakeBackend` refuses one outright.

Every error text that comes back from the connector or a Snowpark session passes through `redact`
before it reaches a report, a log or stderr (`scrubbed` wraps an exception that way).

Nothing here has run against a real Snowflake account: every test patches a DuckDB-backed fake in as
`snowflake.connector` (`tests/fake_snowflake.py`).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import re

from .backend import BackendError

#: The environment variable naming the connection when `--connection` is not given.
CONNECTION_ENV = "MIG_SNOWFLAKE_CONNECTION"
#: The environment variable naming the sandbox database when `--sandbox-database` is not given.
SANDBOX_DB_ENV = "MIG_SANDBOX_DATABASE"

#: A `connections.toml` entry name: a plain word, never `key=value` or anything path-like.
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

_REDACTED = "<redacted>"
_SECRET_KEY = r"[A-Za-z_]*?(?:password|passwd|pwd|passcode|passphrase|token|secret|private_key)[A-Za-z_]*"
#: A quoted value ends at its closing quote; an UNQUOTED one may contain spaces or delimiters
#: (`My Secret Passphrase`, a key path with a space), so it is masked to the end of its line (fix
#: round 1, C1) -- conservative: a connector message may carry anything after it.
_KEY_VALUE = re.compile(
    r"""(?P<key>["']?\b""" + _SECRET_KEY + r"""\b["']?[ \t]*[=:][ \t]*)(?P<value>"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n]*)""",
    re.IGNORECASE)
#: `scheme://user:password@host` -- the password in URL userinfo (a Snowflake URL, an https proxy, …).
_URL_USERINFO = re.compile(r"(?P<head>\b[A-Za-z][A-Za-z0-9+.-]*://[^\s:/?#@]+:)(?P<password>[^\s/?#@]+)@")
_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*\Z)",
                        re.DOTALL)


class ConnectionRefused(ValueError):
    """No usable connection name (or sandbox database): a usage error, raised before anything connects."""


def connection_name(explicit: str | None) -> str:
    """The connection to use: the argument, else `MIG_SNOWFLAKE_CONNECTION`, else `ConnectionRefused`.
    Nothing else is consulted -- not the connector's own default-connection settings -- so which
    account a run touches is always an explicit choice."""
    name = explicit or os.environ.get(CONNECTION_ENV)
    if not name:
        raise ConnectionRefused(
            f"no Snowflake connection named: pass --connection NAME or set {CONNECTION_ENV}; NAME is an "
            f"entry in your own connections.toml (key-pair or SSO; a password is never accepted here)")
    if not _NAME.match(name):
        raise ConnectionRefused(f"{name!r} is not a connection name: give the name of a connections.toml "
                                f"entry, never connection parameters")
    return name


def connect(name: str):
    """`snowflake.connector.connect(connection_name=name)` and nothing else."""
    connector = importlib.import_module("snowflake.connector")
    return connector.connect(connection_name=name)


def snowpark_session(name: str):
    """A Snowpark session built from the named connection only."""
    try:
        snowpark = importlib.import_module("snowflake.snowpark")
        return snowpark.Session.builder.config("connection_name", name).create()
    except Exception as exc:  # noqa: BLE001 -- whatever it says goes through redact first
        raise scrubbed(exc) from None


def redact(text: str) -> str:
    """`text` with every `password=…`, `token: …`, `private_key…=…` (`passwd`, `pwd`, `passcode`,
    `passphrase`, `secret` and the like) value masked -- a quoted value up to its closing quote, an
    unquoted one to the END OF ITS LINE -- the password of a URL's userinfo masked (`scheme://user:
    <redacted>@host`), and every PEM private-key block removed. Other lines are left as they are."""
    text = _KEY_BLOCK.sub(_REDACTED, str(text))
    text = _URL_USERINFO.sub(lambda match: f"{match.group('head')}{_REDACTED}@", text)
    return _KEY_VALUE.sub(lambda match: match.group("key") + _REDACTED, text)


def scrubbed(exc: BaseException) -> BackendError:
    """A connector or Snowpark exception as a `BackendError` whose text went through `redact` (the
    original is never chained: raise it `from None`)."""
    error = BackendError(redact(f"{type(exc).__name__}: {exc}"))
    error.errno = getattr(exc, "errno", None)
    error.sqlstate = getattr(exc, "sqlstate", None)
    return error


def _find_spec(name: str):
    try:
        return importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None


def require_dbt_snowflake() -> None:
    """`dbt_project.DbtUnavailable` naming dbt-snowflake when its adapter is not installed beside this
    interpreter (it is not in requirements.txt)."""
    from .dbt_project import DbtUnavailable  # noqa: PLC0415
    if _find_spec("dbt.adapters.snowflake") is None:
        raise DbtUnavailable("dbt-snowflake is not installed beside this interpreter (it is not in "
                             "requirements.txt): install it into the same environment to run a dbt "
                             "workflow against Snowflake")
