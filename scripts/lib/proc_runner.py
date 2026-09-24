"""Runs the one stored-procedure shape the migration produces (plan contract C4).

Snowflake Scripting has no local equivalent, so translated procedures are restricted to a shape
this module can execute anywhere:

    CREATE OR REPLACE PROCEDURE MIG_WORK.<WF>_<SEG>(SRC_DB STRING, …, RUN_ID STRING)
    RETURNS STRING LANGUAGE SQL EXECUTE AS CALLER AS
    $$
    BEGIN
      ALTER SESSION SET TIMEZONE = '…';
      LET ORDERS_SRC VARCHAR := SRC_DB || '.' || SRC_SCHEMA || '.ORDERS';
      <plain SQL statement reading IDENTIFIER(:ORDERS_SRC)>;
      …
      RETURN 'OK';
    END;
    $$;

`parse_proc` pulls the header, the session settings, the `LET`s and the statement list out of that
text; `let_values` evaluates the `LET`s; `bind` substitutes the call arguments and the `LET`
variables and resolves `IDENTIFIER(…)`; `run_proc` hands each bound statement to a backend.
Anything else — a `LET` of any other shape, `DECLARE`, `IF`, loops, `EXECUTE IMMEDIATE`, `CALL` —
is rejected rather than approximated, because the reviewer agent has to be able to trust that what
ran locally is what Snowflake would run. None of this has been executed on a real Snowflake account.

The one `LET` shape (Task C4V). Snowflake documents `IDENTIFIER( { string_literal |
session_variable | bind_variable | snowflake_scripting_variable } )` — a single value, not an
expression — so a procedure builds each mapped table's name first and passes the variable:
`LET <VAR> VARCHAR := <the procedure's arguments and string literals joined by ||>;` then
`IDENTIFIER(:<VAR>)`. Inside the `LET` the arguments are named WITHOUT a colon: Snowflake's Scripting
documentation uses the colon to bind a variable inside a SQL statement, not in an expression, and
says an argument behaves like a declared variable; `IDENTIFIER(:<VAR>)` sits inside a SQL statement
and keeps its colon (fix round 1). That is the DOCUMENTED form, chosen because the concatenation
inside `IDENTIFIER(…)` that these procedures used before is not in Snowflake's grammar; it has not
run on Snowflake either, and the first real-account run confirms it. Here a `LET` is evaluated from
the call arguments and never sent to the backend; its variable is then substituted wherever `:<VAR>`
appears, exactly like a parameter (a SQL string literal), so `IDENTIFIER(:<VAR>)` folds to the
table name. A `LET` of any other shape (a colon-prefixed argument included), one that shadows a
parameter, one declared twice, or a variable used before its `LET`, is a `LetError`. `scripts/compile_check.py` adds contract C4's
own naming rule on top (`c4:let_form`) and refuses an expression inside `IDENTIFIER(…)`
(`c4:identifier_expression`); `bind` itself still folds string literals joined by `||` inside
`IDENTIFIER(…)`, which only a procedure `compile_check` has already refused can contain.

Quoting, comments and the `;` separator are handled by one small scanner (`_next_span`) so a
semicolon in a comment, a quote in a comment and a `:SRC_DB` inside a string literal all behave.

A Snowpark procedure (`LANGUAGE PYTHON` in the header, `render_snowpark.py`'s output) shares the
same C4 header -- `parse_proc` still returns its name, params and `EXECUTE AS` -- but its `$$…$$`
body is a Python module, not the SQL statement subset above: `ProcInfo.language` is `"PYTHON"` and
`.statements` is that body kept whole, unsplit and unchecked for the scripting keywords the SQL
shape rejects. `bind`/`run_proc` are SQL-only; nothing here executes a Snowpark procedure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator


class ProcError(Exception):
    """The procedure text is outside the subset, or an argument is missing."""


class LetError(ProcError):
    """A `LET` outside the one shape the subset has (see the module docstring)."""


@dataclass
class Let:
    """One `LET <name> VARCHAR := <expression>;` of a procedure body, in body order."""
    name: str
    expression: str
    text: str


@dataclass
class Return:
    """One `RETURN …` of a procedure body (comment-free) and how many statements come after it.
    The double never executes a RETURN; `compile_check.py` holds it to `RETURN '<literal>'`, last."""
    text: str
    followed_by: int


@dataclass
class ProcInfo:
    name: str
    params: list[str]
    execute_as: str
    statements: list[str]
    session: dict[str, str] = field(default_factory=dict)
    language: str = "SQL"
    lets: list[Let] = field(default_factory=list)
    returns: list[Return] = field(default_factory=list)
    param_declarations: list[str] = field(default_factory=list)


_HEADER_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\s+(?P<name>[A-Za-z0-9_$.\"]+)\s*\((?P<params>[^)]*)\)",
    re.IGNORECASE)
_EXECUTE_AS_RE = re.compile(r"\bEXECUTE\s+AS\s+(CALLER|OWNER)\b", re.IGNORECASE)
_LANGUAGE_RE = re.compile(r"\bLANGUAGE\s+(SQL|PYTHON)\b", re.IGNORECASE)
_BEGIN_RE = re.compile(r"^\s*BEGIN\b\s*", re.IGNORECASE)
_END_RE = re.compile(r"^\s*END(\s+[A-Za-z0-9_$.\"]+)?\s*$", re.IGNORECASE)
_ALTER_SESSION_RE = re.compile(r"^ALTER\s+SESSION\s+SET\s+(?P<assignments>.+)$", re.IGNORECASE | re.DOTALL)
_IDENTIFIER_CALL_RE = re.compile(r"\bIDENTIFIER\s*\(", re.IGNORECASE)
_PARAM_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*")
_LET_RE = re.compile(r"^LET\s+(?P<name>[A-Za-z_][A-Za-z_0-9$]*)\s+VARCHAR\s*:=\s*(?P<expression>\S.*)$",
                     re.IGNORECASE | re.DOTALL)
_LET_SHAPE = ("`LET <VAR> VARCHAR := <the procedure's arguments, named without a colon, and string "
              "literals joined by ||>`, then IDENTIFIER(:<VAR>)")

# Snowflake Scripting keywords that put a statement outside the subset (contract C4): every block
# and control keyword the orchestrator's SQL policy also denies (fix round 2), plus CALL. `LET` is not
# here: its one supported shape is read by `_parse_let`, and every other shape is refused there. A
# closing `END …` is skipped (`_END_RE`), which is safe because every keyword that opens a block is here.
_SCRIPTING_KEYWORDS = frozenset({
    "DECLARE", "IF", "ELSEIF", "ELSE", "CASE", "FOR", "WHILE", "REPEAT", "LOOP", "BREAK", "CONTINUE",
    "EXCEPTION", "OPEN", "FETCH", "CLOSE", "RAISE", "AWAIT", "CANCEL", "NULL", "CALL", "BEGIN"})


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


def _without_comments(text: str) -> str:
    """The statement with its comments blanked out and its string literals kept."""
    return "".join(text[start:end] if kind != "comment" else " " for kind, start, end in _spans(text))


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
    declarations = [" ".join(p.split()) for p in match.group("params").split(",") if p.split()]
    params = [declaration.split()[0] for declaration in declarations]
    execute_as_match = _EXECUTE_AS_RE.search(header)
    # Snowflake's documented default is owner's rights when the clause is absent.
    execute_as = execute_as_match.group(1).upper() if execute_as_match else "OWNER"
    language_match = _LANGUAGE_RE.search(header)
    language = language_match.group(1).upper() if language_match else "SQL"

    body_end = sql_text.rfind("$$")
    if header_end < 0 or body_end == header_end:
        raise ProcError(f"procedure {match.group('name')} has no $$-quoted body")
    body = _BEGIN_RE.sub("", sql_text[header_end + 2:body_end], count=1)

    if language == "PYTHON":
        # A Snowpark procedure body is a Python module, not the SQL statement subset below: it
        # is kept whole (the C4 signature parsing above is the only thing the two shapes share).
        return ProcInfo(name=match.group("name"), params=params, execute_as=execute_as,
                        statements=[body], session={}, language=language,
                        param_declarations=declarations)

    chunks = [chunk for chunk in _split_code(body, ";")
              if (code := _code_text(chunk).strip()) and not _END_RE.match(code)]
    let_names = {_let_name(chunk) for chunk in chunks} - {None}
    session: dict[str, str] = {}
    statements: list[str] = []
    lets: list[Let] = []
    returns: list[tuple[str, int]] = []   # (comment-free text, chunks seen before it)
    for position, chunk in enumerate(chunks):
        words = _code_text(chunk).split()
        keyword = words[0].upper()
        if keyword == "LET":
            lets.append(_parse_let(chunk, params, lets))
            continue
        if keyword in _SCRIPTING_KEYWORDS or (keyword == "EXECUTE" and words[1:2] == ["IMMEDIATE"]):
            raise ProcError("outside the supported procedure subset (plan contract C4): "
                            f"{chunk.strip()}")
        if ":=" in _code_text(chunk):
            raise ProcError("outside the supported procedure subset (plan contract C4): a variable "
                            f"assignment -- only a LET gives a name its value: {chunk.strip()}")
        if keyword == "RETURN":
            returns.append((_without_comments(chunk).strip(), position))
            continue
        _refuse_use_before_let(chunk, let_names - {let.name.upper() for let in lets})
        alter = _ALTER_SESSION_RE.match(chunk.strip())
        if alter is not None:
            session.update(_session_settings(alter.group("assignments")))
            continue
        statements.append(chunk.strip())
    return_positions = {position for _, position in returns}
    recorded = [Return(text=text, followed_by=sum(1 for later in range(position + 1, len(chunks))
                                                  if later not in return_positions))
                for text, position in returns]
    return ProcInfo(name=match.group("name"), params=params, execute_as=execute_as,
                    statements=statements, session=session, language=language, lets=lets,
                    returns=recorded, param_declarations=declarations)


def _after_leading_comments(chunk: str) -> str:
    """`chunk` from its first character that is neither whitespace nor part of a comment."""
    for kind, start, end in _spans(chunk):
        if kind == "comment":
            continue
        offset = len(chunk[start:end]) - len(chunk[start:end].lstrip())
        if kind != "code" or offset < end - start:
            return chunk[start + offset:]
    return ""


def _let_name(chunk: str) -> str | None:
    """The upper-cased variable a `LET` chunk declares, whatever its shape; None for any other."""
    words = _code_text(chunk).split()
    if len(words) >= 2 and words[0].upper() == "LET" and _PARAM_RE.fullmatch(words[1]):
        return words[1].upper()
    return None


def _parse_let(chunk: str, params: list[str], earlier: list[Let]) -> Let:
    """The one supported `LET` shape, or a `LetError` that names the statement."""
    statement = _after_leading_comments(chunk)   # a comment in front of the LET is not the LET
    if any(kind == "comment" for kind, _, _ in _spans(statement)):
        raise LetError(f"outside the supported procedure subset (plan contract C4): a comment inside a LET "
                       f"is refused -- write it on its own line in front of the LET; got {statement.strip()}")
    text = statement.strip()
    match = _LET_RE.match(text)
    if match is not None and _colon_arguments(match.group("expression"), params):
        named = ", ".join(f":{name}" for name in _colon_arguments(match.group("expression"), params))
        raise LetError(f"outside the supported procedure subset (plan contract C4): a LET names the "
                       f"procedure's arguments without a colon ({named} here) -- in Snowflake's documented "
                       f"expression syntax the colon binds a variable inside a SQL statement, as in "
                       f"IDENTIFIER(:<VAR>), not in a LET; got {text}")
    if match is None or not _is_argument_concatenation(match.group("expression"), params):
        raise LetError(f"outside the supported procedure subset (plan contract C4): a LET must be "
                       f"{_LET_SHAPE}; got {text}")
    name = match.group("name")
    if name.upper() in {param.upper() for param in params}:
        raise LetError(f"LET {name} shadows the procedure parameter of the same name: {text}")
    if name.upper() in {let.name.upper() for let in earlier}:
        raise LetError(f"LET {name} declares {name} twice: {text}")
    return Let(name=name, expression=match.group("expression").strip(), text=text)


def _is_argument_concatenation(expression: str, params: list[str]) -> bool:
    """`expression` is the procedure's arguments, by bare name, and string literals joined by `||`."""
    upper_params = {param.upper() for param in params}
    for part in _split_code(expression, "||"):
        text = part.strip()
        if _literal_text(text) is None and not (_PARAM_RE.fullmatch(text) and text.upper() in upper_params):
            return False
    return True


def _colon_arguments(expression: str, params: list[str]) -> list[str]:
    """The procedure arguments `expression` names WITH a colon (`:SRC_DB`) -- refused in a LET."""
    upper_params = {param.upper() for param in params}
    return [name for name in _variable_references(expression) if name.upper() in upper_params]


def _refuse_use_before_let(chunk: str, undeclared: set[str]) -> None:
    for name in _variable_references(chunk):
        if name.upper() in undeclared:
            raise LetError(f"a statement uses :{name} before the LET that declares it: "
                           f"{chunk.strip()}")


def _variable_references(statement: str) -> list[str]:
    """Every `:<name>` in the code of `statement` (not in a string, a comment or a `::` cast)."""
    names = []
    for kind, start, end in _spans(statement):
        if kind != "code":
            continue
        code = statement[start:end]
        index = 0
        while index < len(code):
            if code.startswith("::", index):
                index += 2
                continue
            if code[index] == ":" and (match := _PARAM_RE.match(code, index + 1)) is not None:
                names.append(match.group(0))
                index = match.end()
                continue
            index += 1
    return names


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

def let_values(proc: ProcInfo, args: dict[str, str]) -> dict[str, str]:
    """Every `LET` of `proc` evaluated from the call arguments: {upper-cased variable: value}.

    This is where a `LET` "runs" — it never reaches a backend. Its value is its arguments (named
    without a colon, case-insensitively like any unquoted name) replaced by their values and its
    string literals joined, so `SRC_DB || '.' || SRC_SCHEMA || '.ORDERS'` with
    `SRC_DB=MIGDB, SRC_SCHEMA=MIG_WORK` is `MIGDB.MIG_WORK.ORDERS`.
    """
    by_upper_name = {name.upper(): value for name, value in args.items()}
    values: dict[str, str] = {}
    for let in proc.lets:
        pieces = []
        for part in _split_code(let.expression, "||"):
            text = part.strip()
            literal = _literal_text(text)
            if literal is not None:
                pieces.append(literal)
            elif text.upper() in by_upper_name:
                pieces.append(str(by_upper_name[text.upper()]))
            else:
                raise ProcError(f"no argument supplied for parameter {text} of LET {let.name}")
        values[let.name.upper()] = "".join(pieces)
    return values


def bind(statement: str, args: dict[str, str]) -> str:
    """Substitutes `:NAME` (case-insensitively) and folds `IDENTIFIER('a.b.c')` to `a.b.c`.

    `args` holds the call arguments and, for a procedure with `LET`s, their `let_values`: a
    variable is bound exactly like a parameter, as a SQL string literal, which is what makes
    `IDENTIFIER(:ORDERS_SRC)` fold to the table name. String literals joined by `||` inside
    `IDENTIFIER(…)` are folded too, but `compile_check.py` refuses any procedure that writes an
    expression there (`c4:identifier_expression`). Names inside string literals and comments are
    left alone, and `::` casts are not mistaken for a name. Values become SQL string literals with
    their quotes doubled.
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


def identifier_arguments(statement: str) -> list[str]:
    """The argument text of every `IDENTIFIER(…)` in the code of `statement`, unbound, in order."""
    return [argument for _, argument in identifier_calls(statement)]


def identifier_calls(statement: str) -> list[tuple[str, str]]:
    """Every `IDENTIFIER(…)` in the code of `statement` as (the code in front of it, with strings
    and comments blanked, its argument text), in order -- what `compile_check`'s role check reads."""
    calls = []
    index = 0
    while index < len(statement):
        kind, end = _next_span(statement, index)
        call = _IDENTIFIER_CALL_RE.search(statement, index, end) if kind == "code" else None
        if call is None:
            index = end
            continue
        close_paren = _matching_paren(statement, call.end())
        calls.append((_code_text(statement[:call.start()]), statement[call.end():close_paren].strip()))
        index = close_paren + 1
    return calls


def masked_code(statement: str, *, strings: bool = True) -> str:
    """`statement` with every comment -- and, unless `strings=False`, every single-quoted string
    literal -- replaced by spaces, character for character, so an offset in the result is the same
    offset in `statement`. A `"quoted identifier"` is kept: it is a name, not text (Task L4's
    `c4:write_mode` reads column names out of a MERGE's ON clause)."""
    out: list[str] = []
    for kind, start, end in _spans(statement):
        chunk = statement[start:end]
        blank = kind == "comment" or (strings and kind == "string" and chunk.startswith("'"))
        out.append(re.sub(r"[^\n]", " ", chunk) if blank else chunk)
    return "".join(out)


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
    """Parses, binds and executes the procedure body. `ALTER SESSION` is recorded, not applied;
    each `LET` is evaluated (`let_values`), not executed."""
    proc = parse_proc(sql_text)
    supplied = {name.upper() for name in args}
    missing = [name for name in proc.params if name.upper() not in supplied]
    if missing:
        raise ProcError(f"no argument supplied for parameter(s) {', '.join(missing)} "
                        f"of {proc.name}")
    values = {**args, **let_values(proc, args)}   # the LETs run here, never on the backend
    # Live hardening L4 fix round 1 (P): the procedure is agent-written SQL, and DuckDB can read and
    # write the host's files. Its connection loses external access -- for good -- before the first
    # statement runs. A backend without the switch (the Snowflake one never runs this) is left as is.
    lock = getattr(backend, "lock_external_access", None)
    if callable(lock):
        lock()
    for statement in proc.statements:
        backend.execute(bind(statement, values))
    return proc
