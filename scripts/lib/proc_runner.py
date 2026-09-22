"""Runs the one stored-procedure shape the migration produces (plan contract C4).

Snowflake Scripting has no local equivalent, so translated procedures are restricted to a shape
this module can execute anywhere:

    CREATE OR REPLACE PROCEDURE MIG_WORK.<WF>_<SEG>(SRC_DB STRING, …, RUN_ID STRING)
    RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
    $$
    BEGIN
      ALTER SESSION SET TIMEZONE = '…';
      <plain SQL statement>;
      …
      RETURN 'OK';
    END;
    $$;

`parse_proc` pulls the header, the session settings and the statement list out of that text;
`bind` substitutes the call arguments and resolves `IDENTIFIER(…)`; `run_proc` hands each bound
statement to a backend. Anything else — `LET`, `DECLARE`, `IF`, loops, `EXECUTE IMMEDIATE`,
`CALL` — is rejected rather than approximated, because the reviewer agent has to be able to trust
that what ran locally is what Snowflake would run. None of this has been executed on a real
Snowflake account.

Quoting, comments and the `;` separator are handled by one small scanner (`_next_span`) so a
semicolon in a comment, a quote in a comment and a `:SRC_DB` inside a string literal all behave.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator


class ProcError(Exception):
    """The procedure text is outside the subset, or an argument is missing."""


@dataclass
class ProcInfo:
    name: str
    params: list[str]
    execute_as: str
    statements: list[str]
    session: dict[str, str] = field(default_factory=dict)


_HEADER_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\s+(?P<name>[A-Za-z0-9_$.\"]+)\s*\((?P<params>[^)]*)\)",
    re.IGNORECASE)
_EXECUTE_AS_RE = re.compile(r"\bEXECUTE\s+AS\s+(CALLER|OWNER)\b", re.IGNORECASE)
_BEGIN_RE = re.compile(r"^\s*BEGIN\b\s*", re.IGNORECASE)
_END_RE = re.compile(r"^\s*END(\s+[A-Za-z0-9_$.\"]+)?\s*$", re.IGNORECASE)
_ALTER_SESSION_RE = re.compile(r"^ALTER\s+SESSION\s+SET\s+(?P<assignments>.+)$", re.IGNORECASE | re.DOTALL)
_IDENTIFIER_CALL_RE = re.compile(r"\bIDENTIFIER\s*\(", re.IGNORECASE)
_PARAM_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*")

# Snowflake Scripting keywords that put a statement outside the subset (contract C4).
_SCRIPTING_KEYWORDS = frozenset({"LET", "DECLARE", "IF", "FOR", "WHILE", "CALL", "BEGIN"})


# --- a scanner that knows where strings and comments are -------------------------------------

def _next_span(text: str, index: int) -> tuple[str, int]:
    """(kind, end) of the span starting at `index`: "code", "string" or "comment"."""
    if text.startswith("--", index):
        end = text.find("\n", index)
        return "comment", len(text) if end < 0 else end
    if text.startswith("/*", index):
        end = text.find("*/", index + 2)
        return "comment", len(text) if end < 0 else end + 2
    char = text[index]
    if char in "'\"":
        cursor = index + 1
        while cursor < len(text):
            if char == "'" and text[cursor] == "\\":      # Snowflake's backslash escapes
                cursor += 2
                continue
            if text[cursor] == char:
                if text.startswith(char * 2, cursor):     # '' and "" are escaped quotes
                    cursor += 2
                    continue
                return "string", cursor + 1
            cursor += 1
        return "string", len(text)
    cursor = index
    while cursor < len(text):
        if text[cursor] in "'\"" or text.startswith("--", cursor) or text.startswith("/*", cursor):
            break
        cursor += 1
    return "code", cursor


def _spans(text: str) -> Iterator[tuple[str, int, int]]:
    index = 0
    while index < len(text):
        kind, end = _next_span(text, index)
        yield kind, index, end
        index = end


def _code_text(text: str) -> str:
    """The statement with strings and comments blanked out, for keyword detection only."""
    return "".join(text[start:end] if kind == "code" else " " for kind, start, end in _spans(text))


def _split_code(text: str, separator: str) -> list[str]:
    """Splits on `separator` where it appears outside strings and comments."""
    parts: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(text):
        kind, end = _next_span(text, index)
        chunk = text[index:end]
        if kind == "code" and separator in chunk:
            pieces = chunk.split(separator)
            current.append(pieces[0])
            for piece in pieces[1:]:
                parts.append("".join(current))
                current = [piece]
        else:
            current.append(chunk)
        index = end
    parts.append("".join(current))
    return parts


# --- parse_proc -------------------------------------------------------------------------------

def parse_proc(sql_text: str) -> ProcInfo:
    """The procedure text → its name, parameters, session settings and executable statements."""
    header_end = sql_text.find("$$")
    header = sql_text if header_end < 0 else sql_text[:header_end]
    match = _HEADER_RE.search(header)
    if match is None:
        raise ProcError("not a CREATE PROCEDURE statement: no "
                        "'CREATE [OR REPLACE] PROCEDURE <name>(<params>)' header found")
    params = [p.split()[0] for p in match.group("params").split(",") if p.split()]
    execute_as_match = _EXECUTE_AS_RE.search(header)
    # Snowflake's documented default is owner's rights when the clause is absent.
    execute_as = execute_as_match.group(1).upper() if execute_as_match else "OWNER"

    body_end = sql_text.rfind("$$")
    if header_end < 0 or body_end == header_end:
        raise ProcError(f"procedure {match.group('name')} has no $$-quoted body")
    body = _BEGIN_RE.sub("", sql_text[header_end + 2:body_end], count=1)

    session: dict[str, str] = {}
    statements: list[str] = []
    for chunk in _split_code(body, ";"):
        code = _code_text(chunk).strip()
        if not code or _END_RE.match(code):
            continue
        words = code.split()
        keyword = words[0].upper()
        if keyword in _SCRIPTING_KEYWORDS or (keyword == "EXECUTE" and words[1:2] == ["IMMEDIATE"]):
            raise ProcError("outside the supported procedure subset (plan contract C4): "
                            f"{chunk.strip()}")
        if keyword == "RETURN":
            continue
        alter = _ALTER_SESSION_RE.match(chunk.strip())
        if alter is not None:
            session.update(_session_settings(alter.group("assignments")))
            continue
        statements.append(chunk.strip())
    return ProcInfo(name=match.group("name"), params=params, execute_as=execute_as,
                    statements=statements, session=session)


def _session_settings(assignments: str) -> dict[str, str]:
    settings = {}
    for assignment in _split_code(assignments, ","):
        key, separator, value = assignment.partition("=")
        if not separator:
            raise ProcError(f"cannot read ALTER SESSION setting {assignment.strip()!r}")
        settings[key.strip().upper()] = _unquote(value.strip())
    return settings


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'").replace("\\'", "'")
    return text


# --- bind -------------------------------------------------------------------------------------

def bind(statement: str, args: dict[str, str]) -> str:
    """Substitutes `:PARAM` (case-insensitively) and folds `IDENTIFIER('a' || '.' || 'b')` to `a.b`.

    Parameters inside string literals and comments are left alone, and `::` casts are not mistaken
    for a parameter. Values become SQL string literals with their quotes doubled.
    """
    by_upper_name = {name.upper(): value for name, value in args.items()}
    bound = "".join(_substitute(statement[start:end], by_upper_name) if kind == "code"
                    else statement[start:end]
                    for kind, start, end in _spans(statement))
    return _fold_identifiers(bound)


def _substitute(code: str, args: dict[str, str]) -> str:
    out: list[str] = []
    index = 0
    while index < len(code):
        if code.startswith("::", index):        # a cast, not a parameter
            out.append("::")
            index += 2
            continue
        if code[index] == ":":
            match = _PARAM_RE.match(code, index + 1)
            if match is not None and match.group(0).upper() in args:
                out.append(_sql_literal(args[match.group(0).upper()]))
                index = match.end()
                continue
        out.append(code[index])
        index += 1
    return "".join(out)


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _fold_identifiers(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        kind, end = _next_span(text, index)
        chunk = text[index:end]
        if kind != "code":
            out.append(chunk)
            index = end
            continue
        call = _IDENTIFIER_CALL_RE.search(chunk)
        if call is None:
            out.append(chunk)
            index = end
            continue
        out.append(chunk[:call.start()])
        open_paren = index + call.end()
        close_paren = _matching_paren(text, open_paren)
        out.append(_fold_one_identifier(text[open_paren:close_paren]))
        index = close_paren + 1
    return "".join(out)


def _matching_paren(text: str, index: int) -> int:
    depth = 1
    while index < len(text):
        kind, end = _next_span(text, index)
        if kind == "code":
            for offset in range(index, end):
                if text[offset] == "(":
                    depth += 1
                elif text[offset] == ")":
                    depth -= 1
                    if depth == 0:
                        return offset
        index = end
    raise ProcError("unbalanced parentheses after IDENTIFIER(")


def _fold_one_identifier(inner: str) -> str:
    texts = [_literal_text(part) for part in _split_code(inner, "||")]
    if not texts or any(text is None for text in texts):
        raise ProcError("IDENTIFIER(…) must be string literals joined by || once bound, got "
                        f"IDENTIFIER({inner.strip()})")
    return "".join(texts)


def _literal_text(part: str) -> str | None:
    text = part.strip()
    if not text.startswith("'"):
        return None
    kind, end = _next_span(text, 0)
    if kind != "string" or end != len(text):
        return None
    return _unquote(text)


# --- run_proc ---------------------------------------------------------------------------------

def run_proc(backend, sql_text: str, args: dict[str, str]) -> ProcInfo:
    """Parses, binds and executes the procedure body. `ALTER SESSION` is recorded, not applied."""
    proc = parse_proc(sql_text)
    supplied = {name.upper() for name in args}
    missing = [name for name in proc.params if name.upper() not in supplied]
    if missing:
        raise ProcError(f"no argument supplied for parameter(s) {', '.join(missing)} "
                        f"of {proc.name}")
    for statement in proc.statements:
        backend.execute(bind(statement, args))
    return proc
