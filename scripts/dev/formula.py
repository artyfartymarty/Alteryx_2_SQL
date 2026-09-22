"""The Alteryx formula language, as far as the sample workflows and the cookbook need it.

Test tooling only: `scripts/dev/alteryx_sim.py` uses this to produce golden data, and nothing in
the production path imports it. **No Alteryx engine was available while this was written**, so
every rule here is a documented assumption rather than an observation; they are listed one per
line in `docs/reference/simulator-semantics.md`.

Three pieces make up the module:

* `compile_expr` / `evaluate` — a tokenizer, a recursive-descent parser over a tuple AST, and a
  three-valued evaluator. Arithmetic runs on `Decimal`, entering through `Decimal(repr(x))` for
  floats, and the result is widened back to the widest operand type.

  That is a **deliberate simplification, and it differs from real Alteryx**, which computes
  `Double` values in IEEE-754 binary64: `4094.9 * 0.95` is `3890.155` here and
  `3890.1549999999997` in binary, so rounding to cents gives 3890.16 against 3890.15. Measured
  over 200,000 random amounts times a typical factor, the two models land on a different cent in
  0.67% of cases. `docs/reference/simulator-semantics.md` §1 states why the simplification was
  chosen and what diff class it belongs to; `tests/test_formula.py` pins the examples.
* `RowWindow` — the `[Row-1:FIELD]` / `[Row+1:FIELD]` lookups a Multi-Row Formula needs.
* `coerce` — one value into one Alteryx field type, with that type's truncation and rounding.
"""
from __future__ import annotations

import datetime as _dt
import functools
import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any, Callable, Sequence


class FormulaError(Exception):
    """A formula that cannot be parsed, or that asks for something the simulator refuses."""


# Arithmetic is exact, but a quotient still has to stop somewhere.
_PRECISION = 40

INT_TYPES = frozenset({"Byte", "Int16", "Int32", "Int64"})
FLOAT_TYPES = frozenset({"Float", "Double"})
NUMERIC_TYPES = INT_TYPES | FLOAT_TYPES | {"FixedDecimal"}
FIXED_STRING_TYPES = frozenset({"String", "WString"})
STRING_TYPES = FIXED_STRING_TYPES | {"V_String", "V_WString"}
DATE_TYPES = frozenset({"Date", "Time", "DateTime"})

_PLAIN_DECIMAL_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\Z")


class _Constant(str):
    """A workflow constant's value.

    It is a string — `dag.constants` holds text — but becomes a number as soon as the other
    operand of an arithmetic or comparison operator is numeric.
    """

    __slots__ = ()


# --- values ---

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _is_plain_text(value: Any) -> bool:
    return isinstance(value, str) and not isinstance(value, _Constant)


def as_number(value: Any) -> Decimal | None:
    """`value` as an exact Decimal, or None when it is not a number.

    Floats enter through `repr`, the shortest text that round-trips to the same double, so the
    decimal a person wrote is the decimal that is computed with.
    """
    if value is None or isinstance(value, (list, dict)):
        return None
    if isinstance(value, bool):
        return Decimal(1 if value else 0)
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value)) if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, str):
        text = value.strip()
        return Decimal(text) if _PLAIN_DECIMAL_RE.match(text) else None
    return None


def _rank(value: Any) -> int:
    """How wide a value is: int (1) < Decimal (2) < float (3). Numeric text counts as Decimal."""
    if isinstance(value, float):
        return 3
    if isinstance(value, (bool, int)):
        return 1
    return 2


def _widen(number: Decimal, rank: int) -> Any:
    """The exact result, narrowed to the widest operand's type.

    A magnitude no `Double` can hold becomes NULL rather than an infinity: `typed_csv` refuses to
    write a non-finite float, so an overflow has to stop here, where it happens.
    """
    if rank >= 3:
        widened = float(number)
        return widened if math.isfinite(widened) else None
    if rank == 2:
        return number
    return int(number)


def to_string(value: Any) -> str | None:
    """Alteryx's `ToString`: integral numbers lose their fraction, nothing goes exponential."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Decimal)):
        number = as_number(value)
        if number is None:
            return str(value)
        if number == number.to_integral_value():
            return str(int(number))
        return format(number, "f")
    return str(value)


def _parse_stamp(value: Any) -> tuple[_dt.datetime | None, bool]:
    """ISO text (or a date/datetime object) → (datetime, date_only). Anything else → (None, False)."""
    if isinstance(value, _dt.datetime):
        return value, False
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day), True
    if not isinstance(value, str):
        return None, False
    text = value.strip()
    for fmt, date_only in (("%Y-%m-%d %H:%M:%S", False), ("%Y-%m-%dT%H:%M:%S", False), ("%Y-%m-%d", True)):
        try:
            return _dt.datetime.strptime(text, fmt), date_only
        except ValueError:
            continue
    return None, False


def _add_months(stamp: _dt.datetime, months: int) -> _dt.datetime:
    """Calendar month arithmetic, clamping the day: 31 January plus one month is 28 February."""
    index = stamp.year * 12 + (stamp.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    last = [31, 29 if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0 else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return stamp.replace(year=year, month=month, day=min(stamp.day, last))


# --- coercion into an Alteryx field type ---

def coerce(value: object, alteryx_type: str, size: int | None, scale: int | None = None) -> object:
    """One value into one Alteryx field type (dag-contract §7).

    `String`/`WString` truncate to `size` characters and the `V_` forms do not; integers round
    half away from zero; `FixedDecimal` quantizes to `scale` the same way; a `Bool` from a number
    is `!= 0`; text that is not a number or not a date becomes NULL rather than an error.
    """
    if value is None:
        return None
    if alteryx_type is None:
        return value
    if alteryx_type in STRING_TYPES:
        text = to_string(value)
        if alteryx_type in FIXED_STRING_TYPES and size:
            text = text[:size]
        return text
    if alteryx_type in INT_TYPES or alteryx_type == "FixedDecimal" or alteryx_type in FLOAT_TYPES:
        number = as_number(value)
        if number is None:
            return None
        if alteryx_type in FLOAT_TYPES:
            return float(number)
        places = Decimal(1) if alteryx_type in INT_TYPES else Decimal(1).scaleb(-(scale or 0))
        try:
            with localcontext() as ctx:
                ctx.prec = _PRECISION
                rounded = number.quantize(places, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None
        return int(rounded) if alteryx_type in INT_TYPES else rounded
    if alteryx_type == "Bool":
        if isinstance(value, bool):
            return value
        number = as_number(value)
        if number is not None:
            return number != 0
        text = str(value).strip().lower()
        if text in ("true", "t", "y", "yes"):
            return True
        if text in ("false", "f", "n", "no"):
            return False
        return None
    if alteryx_type in DATE_TYPES:
        stamp, _ = _parse_stamp(value)
        if stamp is None:
            if alteryx_type == "Time":
                try:
                    return _dt.datetime.strptime(str(value).strip(), "%H:%M:%S").strftime("%H:%M:%S")
                except ValueError:
                    return None
            return None
        shapes = {"Date": "%Y-%m-%d", "Time": "%H:%M:%S"}
        return stamp.strftime(shapes.get(alteryx_type, "%Y-%m-%d %H:%M:%S"))
    return value


# --- tokenizer ---

_NUMBER_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_OPERATOR_RE = re.compile(r"<>|<=|>=|==|!=|&&|\|\||[-+*/%(),<>=!]")
_ROW_REF_RE = re.compile(r"(?i)\ARow\s*([+-]\s*\d+)\s*:\s*(.+)\Z")

KEYWORDS = frozenset({"and", "or", "not", "in", "if", "then", "elseif", "else", "endif",
                      "true", "false"})


def _scan_string(text: str, start: int) -> tuple[int, str]:
    """A quoted literal. Backslashes are literal (regex patterns live in these); `""` is one quote."""
    quote = text[start]
    pieces: list[str] = []
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == quote:
            if index + 1 < len(text) and text[index + 1] == quote:
                pieces.append(quote)
                index += 2
                continue
            return index + 1, "".join(pieces)
        pieces.append(char)
        index += 1
    raise FormulaError(f"unterminated string literal in {text!r}")


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "\"'":
            index, value = _scan_string(text, index)
            tokens.append(("str", value))
            continue
        if char == "[":
            end = text.find("]", index)
            if end < 0:
                raise FormulaError(f"unclosed field reference in {text!r}")
            tokens.append(("name", text[index + 1:end]))
            index = end + 1
            continue
        match = _NUMBER_RE.match(text, index)
        if match and (char.isdigit() or (char == "." and match.end() > index + 1)):
            tokens.append(("num", match.group()))
            index = match.end()
            continue
        match = _IDENT_RE.match(text, index)
        if match:
            tokens.append(("ident", match.group()))
            index = match.end()
            continue
        match = _OPERATOR_RE.match(text, index)
        if match:
            tokens.append(("op", match.group()))
            index = match.end()
            continue
        raise FormulaError(f"unexpected character {char!r} at position {index} in {text!r}")
    tokens.append(("end", ""))
    return tokens


# --- parser ---

_COMPARISONS = ("<>", "<=", ">=", "==", "!=", "<", ">", "=")


class _Parser:
    """Recursive descent over the token list. Precedence low → high:
    OR, AND, NOT, comparison / IN, `+ -`, `* / %`, unary `-`."""

    def __init__(self, tokens: list[tuple[str, str]], text: str):
        self.tokens = tokens
        self.text = text
        self.position = 0

    # token helpers
    def _peek(self) -> tuple[str, str]:
        return self.tokens[self.position]

    def _take(self) -> tuple[str, str]:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def _at_op(self, *operators: str) -> bool:
        kind, value = self._peek()
        return kind == "op" and value in operators

    def _at_word(self, *words: str) -> bool:
        kind, value = self._peek()
        return kind == "ident" and value.lower() in words

    def _expect_op(self, operator: str) -> None:
        if not self._at_op(operator):
            raise FormulaError(f"expected {operator!r} in {self.text!r}, found {self._peek()[1]!r}")
        self._take()

    def _expect_word(self, word: str) -> None:
        if not self._at_word(word):
            raise FormulaError(f"expected {word.upper()} in {self.text!r}, found {self._peek()[1]!r}")
        self._take()

    # grammar
    def parse(self) -> tuple:
        node = self.expression()
        if self._peek()[0] != "end":
            raise FormulaError(f"unexpected {self._peek()[1]!r} after the end of {self.text!r}")
        return node

    def expression(self) -> tuple:
        node = self.conjunction()
        while self._at_word("or") or self._at_op("||"):
            self._take()
            node = ("or", node, self.conjunction())
        return node

    def conjunction(self) -> tuple:
        node = self.negation()
        while self._at_word("and") or self._at_op("&&"):
            self._take()
            node = ("and", node, self.negation())
        return node

    def negation(self) -> tuple:
        if self._at_word("not") or self._at_op("!"):
            self._take()
            return ("not", self.negation())
        return self.comparison()

    def comparison(self) -> tuple:
        node = self.sum()
        if self._at_word("in"):
            self._take()
            self._expect_op("(")
            items = [self.expression()]
            while self._at_op(","):
                self._take()
                items.append(self.expression())
            self._expect_op(")")
            return ("in", node, items)
        kind, value = self._peek()
        if kind == "op" and value in _COMPARISONS:
            self._take()
            return ("cmp", value, node, self.sum())
        return node

    def sum(self) -> tuple:
        node = self.product()
        while self._at_op("+", "-"):
            operator = self._take()[1]
            node = ("bin", operator, node, self.product())
        return node

    def product(self) -> tuple:
        node = self.unary()
        while self._at_op("*", "/", "%"):
            operator = self._take()[1]
            node = ("bin", operator, node, self.unary())
        return node

    def unary(self) -> tuple:
        if self._at_op("-"):
            self._take()
            return ("neg", self.unary())
        if self._at_op("+"):
            self._take()
            return self.unary()
        return self.primary()

    def primary(self) -> tuple:
        kind, value = self._take()
        if kind == "num":
            # Whole numbers stay ints so integer arithmetic stays integral; the rest are exact
            # decimals, which is what makes Round's halves land where a person expects.
            return ("lit", int(value) if value.isdigit() else Decimal(value))
        if kind == "str":
            return ("lit", value)
        if kind == "name":
            match = _ROW_REF_RE.match(value)
            if match:
                return ("row", int(match.group(1).replace(" ", "")), match.group(2).strip())
            return ("name", value.strip())
        if kind == "op" and value == "(":
            node = self.expression()
            self._expect_op(")")
            return node
        if kind == "ident":
            lowered = value.lower()
            if lowered == "if":
                return self.conditional()
            if lowered in ("true", "false"):
                return ("lit", lowered == "true")
            if self._at_op("("):
                self._take()
                arguments: list[tuple] = []
                if not self._at_op(")"):
                    arguments.append(self.expression())
                    while self._at_op(","):
                        self._take()
                        arguments.append(self.expression())
                self._expect_op(")")
                return ("call", value, arguments)
            raise FormulaError(f"unknown identifier {value!r} in {self.text!r}")
        raise FormulaError(f"unexpected {value!r} in {self.text!r}")

    def conditional(self) -> tuple:
        branches = []
        while True:
            condition = self.expression()
            self._expect_word("then")
            branches.append((condition, self.expression()))
            if self._at_word("elseif"):
                self._take()
                continue
            break
        otherwise = None
        if self._at_word("else"):
            self._take()
            otherwise = self.expression()
        self._expect_word("endif")
        return ("if", branches, otherwise)


class Expr:
    """A parsed formula. Immutable, so `compile_expr` can hand the same one to every row."""

    __slots__ = ("text", "node")

    def __init__(self, text: str, node: tuple):
        self.text = text
        self.node = node

    def evaluate(self, row: dict, constants: dict[str, str] | None = None,
                 rows: "RowWindow | None" = None) -> object:
        return _evaluate(self.node, _Env(row, constants, rows))

    def __repr__(self) -> str:
        return f"Expr({self.text!r})"


@functools.lru_cache(maxsize=1024)
def compile_expr(text: str) -> Expr:
    """Parse `text` once. Results are cached, so a per-row loop parses nothing."""
    if not isinstance(text, str):
        raise FormulaError(f"expression must be text, got {type(text).__name__}")
    return Expr(text, _Parser(_tokenize(text), text).parse())


# --- evaluation ---

class _Env:
    __slots__ = ("row", "constants", "rows")

    def __init__(self, row: dict, constants: dict[str, str] | None, rows: "RowWindow | None"):
        self.row = row if row is not None else {}
        self.constants = constants or {}
        self.rows = rows


def _lookup_insensitive(mapping: dict, name: str) -> tuple[bool, Any]:
    """Alteryx field and constant names are case-insensitive; exact hits are the common case."""
    if name in mapping:
        return True, mapping[name]
    lowered = name.lower()
    for key, value in mapping.items():
        if isinstance(key, str) and key.lower() == lowered:
            return True, value
    return False, None


def _resolve_name(name: str, env: _Env) -> Any:
    found, value = _lookup_insensitive(env.row, name)
    if found:
        return value
    found, value = _lookup_insensitive(env.constants, name)
    if found:
        return _Constant("" if value is None else str(value))
    raise FormulaError(f"no field or constant named [{name}]")


def truth(value: Any) -> bool | None:
    """Three-valued truth. A string that is not a number is an authoring error, not a NULL."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    number = as_number(value)
    if number is None:
        raise FormulaError(f"{value!r} is not a condition")
    return number != 0


def _concatenates(left: Any, right: Any) -> bool:
    """`+` joins text unless a constant meets a number, in which case the constant converts."""
    if _is_plain_text(left) or _is_plain_text(right):
        return True
    return isinstance(left, str) and isinstance(right, str)


def _arithmetic(operator: str, left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    if operator == "+" and _concatenates(left, right):
        return to_string(left) + to_string(right)
    first, second = as_number(left), as_number(right)
    if first is None or second is None:
        return None
    rank = 3 if operator == "/" else max(_rank(left), _rank(right))
    try:
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            if operator == "+":
                result = first + second
            elif operator == "-":
                result = first - second
            elif operator == "*":
                result = first * second
            elif operator == "/":
                result = first / second
            elif operator == "%":
                result = first % second
            else:
                raise FormulaError(f"unknown operator {operator!r}")
            return _widen(result, rank)
    except ArithmeticError:
        return None  # division by zero, and any overflow, is NULL


def _compare(operator: str, left: Any, right: Any) -> bool | None:
    if left is None or right is None:
        return None
    if isinstance(left, str) or isinstance(right, str):
        if _is_number(left) or _is_number(right):
            first, second = as_number(left), as_number(right)
            if first is None or second is None:
                return None
        else:
            first, second = str(left), str(right)  # code point order, case-sensitive
    else:
        first, second = as_number(left), as_number(right)
        if first is None or second is None:
            return None
    if operator in ("=", "=="):
        return first == second
    if operator in ("!=", "<>"):
        return first != second
    if operator == "<":
        return first < second
    if operator == "<=":
        return first <= second
    if operator == ">":
        return first > second
    return first >= second


def _evaluate(node: tuple, env: _Env) -> Any:
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "name":
        return _resolve_name(node[1], env)
    if kind == "row":
        if env.rows is None:
            raise FormulaError(f"[Row{node[1]:+d}:{node[2]}] used outside a multi-row formula")
        return env.rows.value(node[1], node[2])
    if kind == "bin":
        return _arithmetic(node[1], _evaluate(node[2], env), _evaluate(node[3], env))
    if kind == "cmp":
        return _compare(node[1], _evaluate(node[2], env), _evaluate(node[3], env))
    if kind == "neg":
        return _arithmetic("-", 0, _evaluate(node[1], env))
    if kind == "not":
        verdict = truth(_evaluate(node[1], env))
        return None if verdict is None else not verdict
    if kind == "and":
        left = truth(_evaluate(node[1], env))
        if left is False:
            return False
        right = truth(_evaluate(node[2], env))
        if right is False:
            return False
        return None if left is None or right is None else True
    if kind == "or":
        left = truth(_evaluate(node[1], env))
        if left is True:
            return True
        right = truth(_evaluate(node[2], env))
        if right is True:
            return True
        return None if left is None or right is None else False
    if kind == "in":
        value = _evaluate(node[1], env)
        if value is None:
            return None
        unknown = False
        for item in node[2]:
            result = _compare("=", value, _evaluate(item, env))
            if result is True:
                return True
            unknown = unknown or result is None
        return None if unknown else False
    if kind == "if":
        for condition, consequence in node[1]:
            if truth(_evaluate(condition, env)) is True:  # a NULL condition is false
                return _evaluate(consequence, env)
        return None if node[2] is None else _evaluate(node[2], env)
    if kind == "call":
        return _call(node[1], node[2], env)
    raise FormulaError(f"unknown expression node {kind!r}")


def _call(name: str, argument_nodes: Sequence[tuple], env: _Env) -> Any:
    entry = FUNCTIONS.get(name.lower())
    if entry is None:
        raise FormulaError(f"unknown function {name}()")
    function, minimum, maximum = entry
    arguments = [_evaluate(node, env) for node in argument_nodes]
    if len(arguments) < minimum or (maximum is not None and len(arguments) > maximum):
        raise FormulaError(f"{name}() takes {minimum}..{maximum} arguments, got {len(arguments)}")
    return function(*arguments)


def evaluate(expr: "Expr | str", row: dict[str, object], *, constants: dict[str, str] | None = None,
             rows: "RowWindow | None" = None) -> object:
    """Evaluate `expr` for one row. `expr` may be text (compiled and cached) or a compiled `Expr`."""
    compiled = expr if isinstance(expr, Expr) else compile_expr(expr)
    return compiled.evaluate(row, constants, rows)


# --- the row window a Multi-Row Formula reads ---

class RowWindow:
    """`[Row-1:FIELD]` / `[Row+1:FIELD]` within one group, in incoming order.

    `unknown` is the tool's `OtherRows` setting: `"null"`, `"zero"` (0 for numeric fields, the
    empty string otherwise) or `"nearest"` (the group's first or last row).
    """

    __slots__ = ("group_rows", "index", "unknown", "types")

    def __init__(self, group_rows: list[dict], index: int, unknown: str, types: dict[str, str]):
        self.group_rows = group_rows
        self.index = index
        self.unknown = (unknown or "null").lower()
        self.types = types or {}

    def value(self, offset: int, field: str) -> Any:
        target = self.index + offset
        if 0 <= target < len(self.group_rows):
            return _lookup_insensitive(self.group_rows[target], field)[1]
        if not self.group_rows:
            return None
        if self.unknown == "nearest":
            row = self.group_rows[0] if target < 0 else self.group_rows[-1]
            return _lookup_insensitive(row, field)[1]
        if self.unknown == "zero":
            return 0 if _lookup_insensitive(self.types, field)[1] in NUMERIC_TYPES else ""
        return None


# --- function library ---

def _text_of(value: Any) -> str | None:
    return None if value is None else to_string(value)


def _flag(value: Any, default: bool) -> bool:
    """An optional flag argument, such as `Contains`'s case-insensitivity switch."""
    if value is None:
        return default
    verdict = truth(value)
    return default if verdict is None else verdict


def _contains(haystack: Any, needle: Any, case_insensitive: Any = None,
              how: str = "in") -> bool | None:
    text, target = _text_of(haystack), _text_of(needle)
    if text is None or target is None:
        return None
    if _flag(case_insensitive, True):
        text, target = text.lower(), target.lower()
    if how == "starts":
        return text.startswith(target)
    if how == "ends":
        return text.endswith(target)
    return target in text


def _substring(value: Any, start: Any, length: Any = None) -> str | None:
    text = _text_of(value)
    begin = as_number(start)
    if text is None or begin is None:
        return None
    begin = max(int(begin), 0)
    if length is None:
        return text[begin:]
    size = as_number(length)
    if size is None:
        return None
    return text[begin:begin + max(int(size), 0)]


def _left(value: Any, count: Any) -> str | None:
    text, size = _text_of(value), as_number(count)
    if text is None or size is None:
        return None
    return text[:max(int(size), 0)]


def _right(value: Any, count: Any) -> str | None:
    text, size = _text_of(value), as_number(count)
    if text is None or size is None:
        return None
    size = max(int(size), 0)
    return text[len(text) - size:] if size else ""


def _pad(value: Any, width: Any, filler: Any, left: bool) -> str | None:
    text, size, char = _text_of(value), as_number(width), _text_of(filler)
    if text is None or size is None or not char:
        return None
    missing = max(int(size) - len(text), 0)
    return char[0] * missing + text if left else text + char[0] * missing


def _title_case(value: Any) -> str | None:
    text = _text_of(value)
    if text is None:
        return None
    return re.sub(r"\S+", lambda word: word.group()[0].upper() + word.group()[1:].lower(), text)


def _replace(value: Any, target: Any, replacement: Any) -> str | None:
    text, old, new = _text_of(value), _text_of(target), _text_of(replacement)
    if text is None or old is None or new is None:
        return None
    return text.replace(old, new)


@functools.lru_cache(maxsize=512)
def compiled_pattern(pattern: str, case_insensitive: bool) -> re.Pattern:
    try:
        return re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
    except re.error as error:
        raise FormulaError(f"bad regular expression {pattern!r}: {error}") from error


def regex_replacement(replacement: str) -> str:
    """Alteryx writes groups as `$1`; `re` wants `\\g<1>`, and a literal backslash must double."""
    pieces: list[str] = []
    index = 0
    while index < len(replacement):
        char = replacement[index]
        if char == "$" and index + 1 < len(replacement) and replacement[index + 1].isdigit():
            end = index + 1
            while end < len(replacement) and replacement[end].isdigit():
                end += 1
            pieces.append(f"\\g<{replacement[index + 1:end]}>")
            index = end
            continue
        pieces.append("\\\\" if char == "\\" else char)
        index += 1
    return "".join(pieces)


def _regex_match(value: Any, pattern: Any, case_insensitive: Any = None) -> bool | None:
    text, expression = _text_of(value), _text_of(pattern)
    if text is None or expression is None:
        return None
    return compiled_pattern(expression, _flag(case_insensitive, True)).search(text) is not None


def _regex_replace(value: Any, pattern: Any, replacement: Any, case_insensitive: Any = None) -> str | None:
    text, expression, target = _text_of(value), _text_of(pattern), _text_of(replacement)
    if text is None or expression is None or target is None:
        return None
    compiled = compiled_pattern(expression, _flag(case_insensitive, True))
    try:
        return compiled.sub(regex_replacement(target), text)
    except re.error as error:
        raise FormulaError(f"bad replacement {target!r}: {error}") from error


def _to_text(value: Any, places: Any = None) -> str | None:
    """`ToString(x)`, plus Alteryx's optional decimal-places argument."""
    if places is None:
        return _text_of(value)
    number, digits = as_number(value), as_number(places)
    if number is None or digits is None:
        return None
    return format(number.quantize(Decimal(1).scaleb(-int(digits)), rounding=ROUND_HALF_UP), "f")


def _to_number(value: Any) -> float | None:
    """Trimmed plain decimal text only: `1,200.50` is warn-and-null (program spec §8.5)."""
    if value is None:
        return None
    number = as_number(value)
    return None if number is None else float(number)


def _round(value: Any, multiple: Any = None) -> float | None:
    number = as_number(value)
    step = Decimal(1) if multiple is None else as_number(multiple)
    if number is None or step is None or step == 0:
        return None
    try:
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            steps = (number / step).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            return float(steps * step)
    except ArithmeticError:
        return None


def _absolute(value: Any) -> Any:
    number = as_number(value)
    return None if number is None else _widen(abs(number), _rank(value))


def _ceil(value: Any) -> int | None:
    number = as_number(value)
    return None if number is None else int(number.to_integral_value(rounding="ROUND_CEILING"))


def _floor(value: Any) -> int | None:
    number = as_number(value)
    return None if number is None else int(number.to_integral_value(rounding="ROUND_FLOOR"))


def _power(base: Any, exponent: Any) -> Any:
    """`Pow`, on the same exact decimals every other operator uses.

    An integral exponent is exact (a negative one as the exact reciprocal when it is
    representable, otherwise correctly rounded to `_PRECISION`). A fractional exponent is the one
    result that cannot be exact, so it is carried at `_PRECISION` too — a fixed, stated precision
    rather than whatever the platform's `libm` does, so the answer is the same everywhere. An
    undefined result — zero to a negative power, a negative base under a root — is NULL, as
    division by zero is.
    """
    first, second = as_number(base), as_number(exponent)
    if first is None or second is None:
        return None
    if first == 0 and second == 0:
        return 1  # Decimal signals invalid here; `math.pow` and IEEE-754 both say 1
    try:
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            result = first ** second
    except ArithmeticError:
        return None
    if not result.is_finite():
        return None
    # Never narrower than a Decimal: a negative or fractional exponent rarely lands on an integer.
    return _widen(result, max(2, _rank(base), _rank(exponent)))


def extreme(values: Sequence[Any], want_max: bool) -> Any:
    present = [value for value in values if value is not None]
    if not present:
        return None
    best = present[0]
    for value in present[1:]:
        if _compare(">" if want_max else "<", value, best) is True:
            best = value
    return best


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value == "")


def date_format(value: Any, fmt: Any) -> str | None:
    stamp, _ = _parse_stamp(value)
    pattern = _text_of(fmt)
    if stamp is None or pattern is None:
        return None
    try:
        return stamp.strftime(pattern)
    except ValueError:
        return None


def date_parse(value: Any, fmt: Any) -> str | None:
    text, pattern = _text_of(value), _text_of(fmt)
    if text is None or pattern is None:
        return None
    try:
        return _dt.datetime.strptime(text.strip(), pattern).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def _unit_of(unit: Any) -> str:
    name = str(_text_of(unit) or "").strip().lower().rstrip("s")
    if name not in _UNIT_SECONDS and name not in ("month", "year"):
        raise FormulaError(f"unknown date unit {unit!r}")
    return name


def _date_add(value: Any, amount: Any, unit: Any) -> str | None:
    stamp, date_only = _parse_stamp(value)
    count = as_number(amount)
    if stamp is None or count is None:
        return None
    name = _unit_of(unit)
    if name in ("month", "year"):
        moved = _add_months(stamp, int(count) * (12 if name == "year" else 1))
    else:
        moved = stamp + _dt.timedelta(seconds=int(count) * _UNIT_SECONDS[name])
    keeps_date = date_only and name in ("year", "month", "week", "day")
    return moved.strftime("%Y-%m-%d" if keeps_date else "%Y-%m-%d %H:%M:%S")


def _truncate_toward_zero(total: int, divisor: int) -> int:
    """Whole units of `divisor` in `total`, the remainder dropped on both sides of zero.

    Integer arithmetic on purpose: `total_seconds() / 86400` is a binary division, and this is
    the one place a fraction of a unit decides the answer.
    """
    whole = abs(total) // divisor
    return whole if total >= 0 else -whole


def _date_diff(first: Any, second: Any, unit: Any) -> int | None:
    later, _ = _parse_stamp(first)
    earlier, _ = _parse_stamp(second)
    if later is None or earlier is None:
        return None
    name = _unit_of(unit)
    if name in ("month", "year"):
        months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
        anchor = _add_months(earlier, months)
        if months > 0 and anchor > later:
            months -= 1
        elif months < 0 and anchor < later:
            months += 1
        return _truncate_toward_zero(months, 12) if name == "year" else months
    delta = later - earlier
    microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return _truncate_toward_zero(microseconds, _UNIT_SECONDS[name] * 1_000_000)


def _to_date(value: Any) -> str | None:
    stamp, _ = _parse_stamp(value)
    return None if stamp is None else stamp.strftime("%Y-%m-%d")


def _nondeterministic(name: str) -> Callable[..., Any]:
    def refuse(*_arguments: Any) -> Any:
        raise FormulaError(f"{name}() is nondeterministic: the simulator is a parity oracle "
                           "and refuses to invent a clock reading")
    return refuse


# name → (implementation, minimum arity, maximum arity or None for "any")
FUNCTIONS: dict[str, tuple[Callable[..., Any], int, int | None]] = {
    "iif": (lambda condition, yes, no: (yes if truth(condition) is True else no), 3, 3),
    "isnull": (lambda value: value is None, 1, 1),
    "isempty": (_is_empty, 1, 1),
    "null": (lambda: None, 0, 0),
    "tostring": (_to_text, 1, 2),
    "tonumber": (_to_number, 1, 1),
    "round": (_round, 1, 2),
    "abs": (_absolute, 1, 1),
    "ceil": (_ceil, 1, 1),
    "floor": (_floor, 1, 1),
    "mod": (lambda first, second: _arithmetic("%", first, second), 2, 2),
    "pow": (_power, 2, 2),
    "min": (lambda *values: extreme(values, False), 1, None),
    "max": (lambda *values: extreme(values, True), 1, None),
    "contains": (lambda text, target, flag=None: _contains(text, target, flag, "in"), 2, 3),
    "startswith": (lambda text, target, flag=None: _contains(text, target, flag, "starts"), 2, 3),
    "endswith": (lambda text, target, flag=None: _contains(text, target, flag, "ends"), 2, 3),
    "substring": (_substring, 2, 3),
    "left": (_left, 2, 2),
    "right": (_right, 2, 2),
    "padleft": (lambda text, width, filler: _pad(text, width, filler, True), 3, 3),
    "padright": (lambda text, width, filler: _pad(text, width, filler, False), 3, 3),
    "trim": (lambda text: None if _text_of(text) is None else _text_of(text).strip(), 1, 1),
    "trimleft": (lambda text: None if _text_of(text) is None else _text_of(text).lstrip(), 1, 1),
    "trimright": (lambda text: None if _text_of(text) is None else _text_of(text).rstrip(), 1, 1),
    "length": (lambda text: None if _text_of(text) is None else len(_text_of(text)), 1, 1),
    "uppercase": (lambda text: None if _text_of(text) is None else _text_of(text).upper(), 1, 1),
    "lowercase": (lambda text: None if _text_of(text) is None else _text_of(text).lower(), 1, 1),
    "titlecase": (_title_case, 1, 1),
    "replace": (_replace, 3, 3),
    "regex_match": (_regex_match, 2, 3),
    "regex_replace": (_regex_replace, 3, 4),
    "datetimeformat": (date_format, 2, 2),
    "datetimeparse": (date_parse, 2, 2),
    "datetimeadd": (_date_add, 3, 3),
    "datetimediff": (_date_diff, 3, 3),
    "todate": (_to_date, 1, 1),
    "datetimenow": (_nondeterministic("DateTimeNow"), 0, 0),
    "datetimetoday": (_nondeterministic("DateTimeToday"), 0, 0),
}
