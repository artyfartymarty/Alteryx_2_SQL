"""Named connections only, redaction, the sandbox, and `SnowflakeBackend` against the fake (plan Task P2).

Every test here runs against `tests/fake_snowflake.py`, patched in as `snowflake.connector`; nothing
connects to a real account and no real `connections.toml` is read.
"""
from __future__ import annotations

import sys
import types
from decimal import Decimal

import pytest

from lib import snowflake_conn, snowflake_sandbox
from lib.backend import BackendError, DuckDBBackend, SnowflakeBackend, get_backend
from lib.io import write_json
from lib.proc_runner import ProcError
from tests.fake_snowflake import FakeConnectorModule

PROC = """CREATE OR REPLACE PROCEDURE MIG_WORK.WF0099_SEG_01(SRC_DB STRING, SRC_SCHEMA STRING, TGT_DB STRING, TGT_SCHEMA STRING, RUN_ID STRING)
RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
$$
BEGIN
  LET ITEMS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ITEMS';
  LET OUT_TGT VARCHAR := TGT_DB || '.' || TGT_SCHEMA || '.OUT';
  CREATE OR REPLACE TABLE IDENTIFIER(:OUT_TGT) AS
  SELECT ID FROM IDENTIFIER(:ITEMS_SRC) WHERE ID > 1;
  RETURN 'OK';
END;
$$;"""

ARGS = {"SRC_DB": "SB", "SRC_SCHEMA": "MIG_GOLDEN", "TGT_DB": "SB", "TGT_SCHEMA": "MIG_WORK", "RUN_ID": "r1"}
ITEMS = {"fields": [{"name": "ID", "type": "Int32", "size": 4, "scale": None}], "rows": [[1], [2], [3]]}


@pytest.fixture
def fake(monkeypatch):
    module = FakeConnectorModule()
    monkeypatch.setitem(sys.modules, "snowflake.connector", module)
    monkeypatch.delenv(snowflake_conn.CONNECTION_ENV, raising=False)
    monkeypatch.delenv(snowflake_conn.SANDBOX_DB_ENV, raising=False)
    return module


def _config(root, databases):
    write_json(root / "orchestrator.config.json", {"policy": {"sandboxDatabases": databases}})


# --- the brief's four ---------------------------------------------------------------------------------

def test_connect_passes_only_the_connection_name(fake):
    connection = snowflake_conn.connect("sandbox")
    assert fake.connect_calls == [{"connection_name": "sandbox"}]
    assert connection is fake.connections[0]


def test_the_name_comes_from_the_argument_or_the_environment_and_nothing_else(fake, monkeypatch):
    assert snowflake_conn.connection_name("explicit") == "explicit"
    monkeypatch.setenv(snowflake_conn.CONNECTION_ENV, "from_env")
    assert snowflake_conn.connection_name(None) == "from_env"
    assert snowflake_conn.connection_name("explicit") == "explicit"      # the argument wins
    monkeypatch.delenv(snowflake_conn.CONNECTION_ENV)
    # the connector's own default-connection variable is NOT a source: the choice is always explicit
    monkeypatch.setenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME", "default")
    with pytest.raises(snowflake_conn.ConnectionRefused) as refused:
        snowflake_conn.connection_name(None)
    assert "MIG_SNOWFLAKE_CONNECTION" in str(refused.value) and "connections.toml" in str(refused.value)
    assert isinstance(refused.value, ValueError)
    assert fake.connect_calls == []


def test_redact_masks_passwords_tokens_and_keys():
    # (fix round 1, C1: an unquoted value is masked to the end of its line, so each shape is on its own)
    text = ("connect failed: user=ann password=hunter2\n"
            "token: abc.def.ghi\n"
            "private_key_file_pwd='s3 cr3t' after the quoted one\n"
            '{"password": "p@ss", "oauth_token": "tok"}\n'
            "PASSWORD = Upper\n"
            "-----BEGIN ENCRYPTED PRIVATE KEY-----\nMIIFHzBJBgkqhkiG9w0BBQ0w\n-----END ENCRYPTED PRIVATE KEY----- after")
    out = snowflake_conn.redact(text)
    for secret in ("hunter2", "abc.def.ghi", "s3 cr3t", "p@ss", '"tok"', "Upper", "MIIFHzBJBgkqhkiG9w0BBQ0w"):
        assert secret not in out, (secret, out)
    for kept in ("connect failed", "user=ann", "after the quoted one", "after", "password", "token",
                 "private_key_file_pwd"):
        assert kept in out, (kept, out)
    # a key block cut off before its END line is masked to the end
    assert "MIIB" not in snowflake_conn.redact("x -----BEGIN PRIVATE KEY-----\nMIIBsecret")
    assert snowflake_conn.redact("nothing to hide here") == "nothing to hide here"


@pytest.mark.parametrize("text, leaked", [
    ("connect failed: private_key_file_pwd=My Secret Passphrase end", ["My", "Secret", "Passphrase", "end"]),
    ("password=hunter2 secret end", ["hunter2", "secret end"]),
    ("250001: private_key_file=C:\\Users\\<you>\\my keys\\rsa key.p8 could not be read", ["my keys", "rsa key.p8",
                                                                                       "could not be read"]),
    ("password=ab,cd", ["ab", "cd"]),
    ("token=ab;cd", ["ab", "cd"]),
    ("pwd=ab&cd next=1", ["ab", "cd", "next=1"]),
    ("passwd: ab cd", ["ab", "cd"]),
], ids=["passphrase", "password", "key-path", "comma", "semicolon", "ampersand", "passwd"])
def test_redact_masks_an_unquoted_secret_to_the_end_of_its_line(text, leaked):
    """Fix round 1, C1: an unquoted value may contain spaces or delimiters -- everything after the key
    on that line is masked, since a connector message may carry anything after it."""
    out = snowflake_conn.redact(text)
    for fragment in leaked:
        assert fragment not in out.split("<redacted>")[-1] and fragment not in out.replace("<redacted>", ""), (fragment, out)
    assert out.endswith("<redacted>")


@pytest.mark.parametrize("text, expected", [
    ("snowflake://ann:secretpw@myorg-acct/MIGDB/PUBLIC?warehouse=WH",
     "snowflake://ann:<redacted>@myorg-acct/MIGDB/PUBLIC?warehouse=WH"),
    ("https://svc_user:p4ss%21word@example.com/path", "https://svc_user:<redacted>@example.com/path"),
    ("https://ann@example.com/x", "https://ann@example.com/x"),
    ("https://example.com:443/x", "https://example.com:443/x"),
    ("250001: could not reach snowflake://ann:secretpw@acct/db (timeout); retrying in 5s",
     "250001: could not reach snowflake://ann:<redacted>@acct/db (timeout); retrying in 5s"),
], ids=["snowflake-url", "https-url", "no-password", "port-not-password", "inside-a-message"])
def test_redact_masks_the_password_in_url_userinfo(text, expected):
    """Follow-up to fix round 1: `scheme://user:pass@host` keeps the scheme, the user and the host."""
    assert snowflake_conn.redact(text) == expected


def test_redact_touches_only_the_line_that_carries_the_secret():
    text = "Failed to connect.\n  details: password=my pass word here, retrying\nSee the log for more."
    assert snowflake_conn.redact(text) == ("Failed to connect.\n  details: password=<redacted>\n"
                                           "See the log for more.")
    crlf = "first\r\ntoken=a b c\r\nlast"
    assert snowflake_conn.redact(crlf) == "first\r\ntoken=<redacted>\r\nlast"


def test_a_password_argument_is_refused_by_the_backend(fake):
    with pytest.raises(BackendError) as refused:
        SnowflakeBackend(password="hunter2")
    assert str(refused.value) == "passwords are never passed as arguments; use a named connection"
    for key in ("token", "private_key", "private_key_file", "passcode"):
        with pytest.raises(BackendError, match="never passed as arguments"):
            SnowflakeBackend(connection_name="sandbox", **{key: "x"})
    with pytest.raises(BackendError, match="named connection"):
        SnowflakeBackend(account="acme", user="ann")                  # anything but a name is refused
    assert "hunter2" not in str(refused.value)
    assert fake.connect_calls == []


# --- further coverage ---------------------------------------------------------------------------------

def test_a_connection_name_that_is_not_a_plain_name_is_refused(fake):
    for bad in ("", "a b", "x;password=y", "../etc", "name=value"):
        with pytest.raises(snowflake_conn.ConnectionRefused):
            snowflake_conn.connection_name(bad or None)
    assert snowflake_conn.connection_name("prod-sandbox.v2") == "prod-sandbox.v2"


def test_snowpark_session_is_built_from_the_connection_name_only(monkeypatch):
    built = []

    class Builder:
        def __init__(self):
            self.options = {}

        def config(self, key, value):
            self.options[key] = value
            return self

        def configs(self, options):                      # never used: one key, the name
            raise AssertionError("snowpark_session must not pass a configs dict")

        def create(self):
            built.append(dict(self.options))
            return "session"

    stub = types.ModuleType("snowflake.snowpark")
    stub.Session = type("S", (), {"builder": property(lambda self: Builder())})()
    monkeypatch.setitem(sys.modules, "snowflake.snowpark", stub)
    assert snowflake_conn.snowpark_session("sandbox") == "session"
    assert built == [{"connection_name": "sandbox"}]


def test_a_connector_error_is_redacted_and_never_chained(fake):
    backend = SnowflakeBackend(connection_name="sandbox")
    fake.account.fail["SELECT 42"] = "authentication failed for password=hunter2-secret token=abc"
    with pytest.raises(BackendError) as failed:
        backend.query("SELECT 42")
    assert "hunter2-secret" not in str(failed.value) and "abc" not in str(failed.value).split("token")[-1]
    assert failed.value.__cause__ is None and failed.value.__suppress_context__
    assert failed.value.errno == 100132


def test_the_backend_opens_its_connection_by_name_and_runs_statements(fake, monkeypatch):
    monkeypatch.setenv(snowflake_conn.CONNECTION_ENV, "from_env")
    backend = get_backend("snowflake")
    assert fake.connect_calls == [{"connection_name": "from_env"}]
    backend.execute("USE DATABASE SB")
    backend.execute("CREATE OR REPLACE SCHEMA SB.MIG_GOLDEN")
    backend.load_table("MIG_GOLDEN.ITEMS", ITEMS)
    assert backend.table_exists("MIG_GOLDEN.ITEMS") and backend.table_exists("SB.MIG_GOLDEN.ITEMS")
    assert not backend.table_exists("MIG_GOLDEN.NOPE")                 # errno 2003 is "no", not an error
    assert backend.table_columns("MIG_GOLDEN.ITEMS") == [{"name": "ID", "type": "DECIMAL(38,0)", "nullable": True}]
    assert backend.query("SELECT ID FROM MIG_GOLDEN.ITEMS ORDER BY ID") == (
        ["ID"], [(Decimal(1),), (Decimal(2),), (Decimal(3),)])
    assert ("INSERT INTO MIG_GOLDEN.ITEMS VALUES (%s)", [[1], [2], [3]]) in fake.statements
    backend.close()
    assert fake.connections[0].closed


def test_call_procedure_creates_it_then_calls_it_with_the_c4_arguments(fake):
    backend = SnowflakeBackend(connection_name="sandbox")
    for statement in ("USE DATABASE SB", "CREATE OR REPLACE SCHEMA SB.MIG_GOLDEN", "CREATE OR REPLACE SCHEMA SB.MIG_WORK"):
        backend.execute(statement)
    backend.load_table("MIG_GOLDEN.ITEMS", ITEMS)
    backend.call_procedure(PROC, dict(reversed(list(ARGS.items()))))   # any dict order: C4 order is the proc's
    assert fake.statements[-2] == (PROC, None)
    assert fake.statements[-1] == ("CALL MIG_WORK.WF0099_SEG_01(%s, %s, %s, %s, %s)",
                                   ["SB", "MIG_GOLDEN", "SB", "MIG_WORK", "r1"])
    assert fake.rows("SB.MIG_WORK.OUT") == [(Decimal(2),), (Decimal(3),)]

    with pytest.raises(ProcError, match="RUN_ID"):
        backend.call_procedure(PROC, {k: v for k, v in ARGS.items() if k != "RUN_ID"})
    fake.account.fail["CALL MIG_WORK"] = "Uncaught exception: password=hunter2-secret"
    with pytest.raises(BackendError) as failed:
        backend.call_procedure(PROC, ARGS)
    assert "hunter2-secret" not in str(failed.value)


def test_the_backends_refuse_a_name_or_a_type_size_that_would_rewrite_their_sql(fake):
    """Fix round 1, C2: the backstop behind the validators' up-front checks -- a name that is not
    `SCHEMA.TABLE` / `DB.SCHEMA.TABLE` of plain identifiers, a procedure name outside C4's shape, or a
    type size that is not an integer never reaches a statement."""
    backend = SnowflakeBackend(connection_name="sandbox")
    backend.execute("USE DATABASE SB")
    sent = len(fake.statements)
    for bad in ("MIG_WORK.X AS SELECT 1 --", "MIG_WORK.x", 'MIG_WORK."X"', "X", "A.B.C.D", "MIG_WORK.X;DROP"):
        with pytest.raises(ValueError, match="identifier"):
            backend.load_table(bad, ITEMS)
        with pytest.raises(ValueError, match="identifier"):
            backend.create_view("SB.V.X", bad)
        with pytest.raises(ValueError, match="identifier"):
            backend.create_view(bad, "SB.MIG_WORK.T")
        with pytest.raises(ValueError, match="identifier"):
            backend.table_columns(bad)
    sized = {"fields": [{"name": "S", "type": "V_String", "size": "20); DROP TABLE X; --", "scale": None}],
             "rows": []}
    with pytest.raises(ValueError, match="size"):
        backend.load_table("MIG_WORK.T", sized)
    with pytest.raises(ValueError, match="size"):
        DuckDBBackend().load_table("MIG_WORK.T", sized)
    with pytest.raises(ProcError, match="MIG_WORK"):
        backend.call_procedure(PROC.replace("PROCEDURE MIG_WORK.WF0099_SEG_01(", 'PROCEDURE "MIG_WORK".X('), ARGS)
    assert len(fake.statements) == sent


def test_ident_accepts_plain_upper_case_identifiers_only():
    assert snowflake_sandbox.ident("MIGDB_SANDBOX") == "MIGDB_SANDBOX"
    assert snowflake_sandbox.ident("_X$1") == "_X$1"
    for bad in ("migdb", "1DB", "DB.SCHEMA", "DB; DROP DATABASE PROD", '"DB"', "", "DB-1"):
        with pytest.raises(ValueError):
            snowflake_sandbox.ident(bad)


def test_fresh_replaces_the_three_sandbox_schemas_and_the_extras_in_its_database(fake):
    sandbox = snowflake_sandbox.SnowflakeSandbox("sandbox", "MIGDB_SANDBOX")
    assert fake.connect_calls == []                                    # nothing opens until fresh()
    first = sandbox.fresh(["MIG_GOLDEN_WF0001_NORMAL"])
    first.load_table("MIG_WORK.LEFTOVER", ITEMS)
    assert fake.sql() == ["USE DATABASE MIGDB_SANDBOX",
                          "CREATE OR REPLACE SCHEMA MIGDB_SANDBOX.MIG_GOLDEN",
                          "CREATE OR REPLACE SCHEMA MIGDB_SANDBOX.MIG_WORK",
                          "CREATE OR REPLACE SCHEMA MIGDB_SANDBOX.MIG_COMPARE",
                          "CREATE OR REPLACE SCHEMA MIGDB_SANDBOX.MIG_GOLDEN_WF0001_NORMAL",
                          'CREATE OR REPLACE TABLE MIG_WORK.LEFTOVER ("ID" NUMBER(38,0))',
                          "INSERT INTO MIG_WORK.LEFTOVER VALUES (%s)"]
    second = sandbox.fresh()
    assert not second.table_exists("MIG_WORK.LEFTOVER")                # the schema was replaced
    assert fake.connect_calls == [{"connection_name": "sandbox"}] * 2
    with pytest.raises(ValueError):
        sandbox.fresh(["bad name"])
    assert len(fake.connect_calls) == 2                                # refused before connecting
    with pytest.raises(ValueError):
        snowflake_sandbox.SnowflakeSandbox("sandbox", "prod db")


def test_a_sandbox_database_must_be_listed_in_the_policy_before_anything_connects(fake, tmp_path, monkeypatch):
    _config(tmp_path, ["MIGDB", "MIGDB_SANDBOX"])
    sandbox = snowflake_sandbox.sandbox_for(tmp_path, "sandbox", "MIGDB_SANDBOX")
    assert (sandbox.connection_name, sandbox.database) == ("sandbox", "MIGDB_SANDBOX")
    monkeypatch.setenv(snowflake_conn.SANDBOX_DB_ENV, "MIGDB")
    monkeypatch.setenv(snowflake_conn.CONNECTION_ENV, "env_name")
    assert snowflake_sandbox.sandbox_for(tmp_path).database == "MIGDB"
    assert snowflake_sandbox.sandbox_for(tmp_path).connection_name == "env_name"

    with pytest.raises(snowflake_conn.ConnectionRefused) as refused:
        snowflake_sandbox.sandbox_for(tmp_path, "sandbox", "ANALYTICS")
    assert "ANALYTICS" in str(refused.value) and "sandboxDatabases" in str(refused.value)
    assert "CREATE OR REPLACE SCHEMA" in str(refused.value)
    monkeypatch.delenv(snowflake_conn.SANDBOX_DB_ENV)
    with pytest.raises(snowflake_conn.ConnectionRefused, match="MIG_SANDBOX_DATABASE"):
        snowflake_sandbox.sandbox_for(tmp_path, "sandbox", None)
    with pytest.raises(snowflake_conn.ConnectionRefused):
        snowflake_sandbox.sandbox_for(tmp_path, "sandbox", "migdb")    # not a plain upper-case identifier
    (tmp_path / "orchestrator.config.json").unlink()
    with pytest.raises(snowflake_conn.ConnectionRefused, match="orchestrator.config.json"):
        snowflake_sandbox.sandbox_for(tmp_path, "sandbox", "MIGDB")
    assert fake.connect_calls == []


def test_the_dbt_snowflake_adapter_is_required_by_name(monkeypatch):
    from lib.dbt_project import DbtUnavailable
    monkeypatch.setattr(snowflake_conn, "_find_spec", lambda name: None)
    with pytest.raises(DbtUnavailable, match="dbt-snowflake"):
        snowflake_conn.require_dbt_snowflake()
    monkeypatch.setattr(snowflake_conn, "_find_spec", lambda name: object())
    snowflake_conn.require_dbt_snowflake()
