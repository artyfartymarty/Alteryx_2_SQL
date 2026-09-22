import pytest
from dev.formula import evaluate, coerce, RowWindow, FormulaError

@pytest.mark.parametrize("expr,row,expected", [
    ('[A] + [B] * 2', {"A": 1, "B": 3}, 7),
    ('[A] + 1', {"A": None}, None),
    ('[A] > 5', {"A": None}, None),
    ('IIF([A] > 5, "hi", "lo")', {"A": None}, "lo"),
    ('IF [A] >= 1000 THEN "LARGE" ELSEIF [A] >= 100 THEN "MEDIUM" ELSE "SMALL" ENDIF', {"A": 100}, "MEDIUM"),
    ('[S] = "abc"', {"S": "ABC"}, False),
    ('Contains([S], "bc")', {"S": "ABC"}, True),
    ('Contains([S], "bc", 0)', {"S": "ABC"}, False),
    ('ToNumber([S])', {"S": " 12.50 "}, 12.5),
    ('ToNumber([S])', {"S": "1,200.50"}, None),
    ('ToNumber([S])', {"S": "abc"}, None),
    ('Round(2.675, 0.01)', {}, 2.68),
    ('Round(-2.5, 1)', {}, -3),
    ('Round([A] * IIF([Q] >= 10, 0.9, 1), 0.01)', {"A": 33.35, "Q": 10}, 30.02),
    ('Substring("abcdef", 1, 3)', {}, "bcd"),
    ('PadLeft(ToString(7), 3, "0")', {}, "007"),
    ('ToString(12.0)', {}, "12"),
    ('IsEmpty([S])', {"S": "  "}, False),
    ('IsEmpty([S]) OR IsNull([S])', {"S": None}, True),
    ('[A] / 0', {"A": 1}, None),
    ('"x" + [S]', {"S": None}, None),
    ('[S] IN ("a", "b")', {"S": "b"}, True),
    ('NOT ([A] = 1) AND [B] = 2', {"A": 2, "B": 2}, True),
    ('REGEX_Replace([S], "^\\s*([A-Za-z]+)[- ]?(\\d+)\\s*$", "$1-$2")', {"S": " ab 12 "}, "ab-12"),
    ('DateTimeFormat([D], "%Y-%m-%d")', {"D": "2026-08-31 00:00:00"}, "2026-08-31"),
    ('DateTimeParse([S], "%d/%m/%Y")', {"S": "31/02/2026"}, None),
    ('DateTimeParse([S], "%d/%m/%Y")', {"S": "29/02/2024"}, "2024-02-29 00:00:00"),
    ('DateTimeDiff("2026-03-01", "2026-02-27", "days")', {}, 2),
])
def test_evaluate(expr, row, expected):
    got = evaluate(expr, row)
    assert got == pytest.approx(expected) if isinstance(expected, float) else got == expected

def test_constants_convert_by_context():
    assert evaluate('[REGION] = [User.Region]', {"REGION": "EMEA"}, constants={"User.Region": "EMEA"}) is True
    assert evaluate('[QTY] >= [User.Min]', {"QTY": 5}, constants={"User.Min": "5"}) is True

def test_row_window_unknown_rows():
    rows = [{"AMT": 10.0, "RUN": 10.0}, {"AMT": 5.0, "RUN": None}]
    types = {"AMT": "Double", "RUN": "Double"}
    assert evaluate('[Row-1:RUN] + [AMT]', rows[1], rows=RowWindow(rows, 1, "zero", types)) == 15.0
    assert evaluate('[Row-1:RUN] + [AMT]', rows[0], rows=RowWindow(rows, 0, "zero", types)) == 10.0
    assert evaluate('[Row-1:RUN] + [AMT]', rows[0], rows=RowWindow(rows, 0, "null", types)) is None

def test_coerce():
    assert coerce("Chandrasekhar", "String", 10) == "Chandrasek" and coerce("Chandrasekhar", "V_String", 10) == "Chandrasekhar"
    assert coerce(2.5, "Int32", 4) == 3 and coerce(-2.5, "Int32", 4) == -3 and str(coerce(1.005, "FixedDecimal", 19, 2)) == "1.01"

def test_nondeterministic_functions_are_refused():
    with pytest.raises(FormulaError, match="nondeterministic"): evaluate("DateTimeNow()", {})

def test_syntax_error_is_reported():
    with pytest.raises(FormulaError): evaluate("IF [A] THEN 1", {"A": True})


# --- behaviour the brief states in prose but does not test ---

@pytest.mark.parametrize("expr,row,expected", [
    # Three-valued AND / OR / NOT.
    ('[A] AND [B]', {"A": None, "B": False}, False),
    ('[A] AND [B]', {"A": None, "B": True}, None),
    ('[A] OR [B]', {"A": None, "B": True}, True),
    ('[A] OR [B]', {"A": None, "B": False}, None),
    ('NOT [A]', {"A": None}, None),
    ('!([A])', {"A": True}, False),
    ('[A] && [B] || [C]', {"A": True, "B": False, "C": True}, True),
    # Comparison spellings.
    ('[A] <> 1', {"A": 2}, True),
    ('[A] == 1', {"A": 1}, True),
    ('[S] IN ("a", "b")', {"S": None}, None),
    # String `+` concatenates; NULL propagates through it.
    ('[S] + 1', {"S": "x"}, "x1"),
    ('1 + [S]', {"S": "x"}, "1x"),
    # Division and modulo.
    ('[A] / 4', {"A": 3}, 0.75),
    ('Mod(-7, 3)', {}, -1),
    ('[A] % 0', {"A": 1}, None),
    ('-[A] * 2', {"A": 3}, -6),
    # Case-insensitive keywords and function names.
    ('if [a] then "y" else "n" endif', {"A": True}, "y"),
    ('iif(TRUE, 1, 2)', {}, 1),
    # String functions.
    ('StartsWith([S], "ab")', {"S": "ABcd"}, True),
    ('StartsWith([S], "ab", 0)', {"S": "ABcd"}, False),
    ('EndsWith([S], "CD")', {"S": "abcd"}, True),
    ('Left([S], 2) + "|" + Right([S], 2)', {"S": "abcd"}, "ab|cd"),
    ('PadRight("7", 3, "0")', {}, "700"),
    ('Trim([S])', {"S": "  a b  "}, "a b"),
    ('TrimLeft([S]) + "|"', {"S": "  a  "}, "a  |"),
    ('TrimRight([S]) + "|"', {"S": "  a  "}, "  a|"),
    ('Length([S])', {"S": "abc"}, 3),
    ('Uppercase([S]) + Lowercase([S])', {"S": "aB"}, "ABab"),
    ('TitleCase([S])', {"S": "don't stop now"}, "Don't Stop Now"),
    ('Replace([S], "a", "X")', {"S": "banana"}, "bXnXnX"),
    ('Substring("abcdef", 2)', {}, "cdef"),
    ('REGEX_Match([S], "^[a-z]+$")', {"S": "ABC"}, True),
    ('REGEX_Match([S], "^[a-z]+$", 0)', {"S": "ABC"}, False),
    # NULL handling.
    ('IsNull(Null())', {}, True),
    ('IsEmpty([S])', {"S": ""}, True),
    ('Uppercase([S])', {"S": None}, None),
    # Numeric functions; Min/Max are scalar and ignore NULL.
    ('Abs(-2.5) + Ceil(1.2) + Floor(1.8)', {}, 5.5),
    ('Pow(2, 10)', {}, 1024),
    ('Min([A], [B], [C])', {"A": None, "B": 3, "C": 2}, 2),
    ('Max([A], [B])', {"A": None, "B": None}, None),
    ('ToString(2.5)', {}, "2.5"),
    # Dates.
    ('DateTimeAdd([D], 1, "months")', {"D": "2026-01-31"}, "2026-02-28"),
    ('DateTimeAdd([D], -2, "days")', {"D": "2026-03-01 06:30:00"}, "2026-02-27 06:30:00"),
    ('DateTimeDiff("2026-03-01", "2026-02-27", "months")', {}, 0),
    ('DateTimeDiff("2026-02-27", "2026-03-01", "days")', {}, -2),
    ('DateTimeDiff("2026-03-01 00:00:10", "2026-03-01 00:00:00", "seconds")', {}, 10),
    ('ToDate([D])', {"D": "2026-08-31 13:45:00"}, "2026-08-31"),
    ('DateTimeFormat([D], "%d %b %Y")', {"D": "2026-08-31"}, "31 Aug 2026"),
    ('DateTimeFormat([D], "%Y-%m-%d")', {"D": None}, None),
])
def test_more_semantics(expr, row, expected):
    got = evaluate(expr, row)
    assert got == pytest.approx(expected) if isinstance(expected, float) else got == expected


def test_arithmetic_rounds_halves_away_from_zero():
    # These three are the hand-computed cases in samples/wf_0001/README.md. They are about
    # half-away-from-zero rounding, NOT about binary-versus-decimal: for all three the binary
    # product and the exact decimal product agree, and both round the same way. The cases where
    # the two models really do disagree are pinned in the next test.
    from decimal import Decimal
    assert evaluate("[A] * 0.9", {"A": 33.35}) == pytest.approx(30.015)
    assert evaluate("Round([A] * 0.9, 0.01)", {"A": 2500.75}) == pytest.approx(2250.68)
    assert evaluate("Round([A] * 1, 0.01)", {"A": 250.005}) == pytest.approx(250.01)
    # The widest operand type wins: int < Decimal < float.
    assert isinstance(evaluate("[A] + 1", {"A": 1}), int)
    assert evaluate("[A] + 1", {"A": Decimal("0.10")}) == Decimal("1.10")
    assert isinstance(evaluate("[A] * 2", {"A": Decimal("0.10")}), Decimal)
    assert isinstance(evaluate("[A] + 1", {"A": 1.5}), float)
    assert isinstance(evaluate("[A] / 2", {"A": 1}), float)


def test_exact_decimal_arithmetic_differs_from_binary64_in_these_documented_cases():
    """Pin the divergence `docs/reference/simulator-semantics.md` §1 discloses, on both sides.

    The simulator computes in exact decimal; Alteryx computes `Double` values in IEEE-754
    binary64. That is a deliberate simplification, so the difference has to be a measured fact in
    the test suite rather than a claim in a comment.
    """
    import math
    # What plain Python binary floats give — i.e. what real Alteryx is expected to give:
    assert repr(4094.9 * 0.95) == "3890.1549999999997"
    assert repr(4640.5 * 0.85) == "3944.4249999999997"
    assert repr(575.4 * 0.075) == "43.154999999999994"
    assert math.floor(4.35 * 100) == 434
    # What the simulator gives instead: the exact decimal product, one cent higher once rounded.
    assert evaluate("[A] * 0.95", {"A": 4094.9}) == pytest.approx(3890.155)
    assert evaluate("Round([A] * 0.95, 0.01)", {"A": 4094.9}) == pytest.approx(3890.16)
    assert evaluate("Round([A] * 0.85, 0.01)", {"A": 4640.5}) == pytest.approx(3944.43)
    assert evaluate("Round([A] * 0.075, 0.01)", {"A": 575.4}) == pytest.approx(43.16)
    assert evaluate("Floor([A] * 100)", {"A": 4.35}) == 435
    # And the counter-example: 33.35 * 0.9 is NOT one of these cases. The binary product is
    # 30.015 (just above the half), so binary and decimal both round it to 30.02.
    assert repr(33.35 * 0.9) == "30.015"


def test_pow_uses_the_same_exact_arithmetic_as_multiplication():
    from decimal import Decimal
    assert evaluate("Pow(1.15, 2)", {}) == Decimal("1.3225")
    # The same quantity must not depend on which function computed it.
    assert evaluate("Pow([A], 2)", {"A": 1.15}) == evaluate("[A] * [A]", {"A": 1.15})
    assert evaluate("Pow([A], 2)", {"A": 1.15}) == pytest.approx(1.3225)
    assert evaluate("Pow(2, 10)", {}) == 1024
    assert evaluate("Pow(2, -2)", {}) == Decimal("0.25")
    assert evaluate("Pow([A], 3)", {"A": Decimal("0.1")}) == Decimal("0.001")


def test_pow_with_a_fractional_exponent_is_deterministic():
    from decimal import Decimal
    # A root is the one result that cannot be exact, so it is carried at the module's fixed
    # 40-digit decimal precision, which is the same on every platform.
    assert evaluate("Pow(2, 0.5)", {}) == Decimal("1.414213562373095048801688724209698078570")
    assert evaluate("Pow(2, 0.5)", {}) == evaluate("Pow(2, 0.5)", {})


def test_pow_undefined_results_are_null():
    assert evaluate("Pow(0, -1)", {}) is None          # division by zero, as `/ 0` is
    assert evaluate("Pow(-2, 0.5)", {}) is None        # no real root
    assert evaluate("Pow([A], 2)", {"A": None}) is None


def test_zero_to_the_zero_is_one():
    # The IEEE-754 convention `math.pow` follows, kept when Pow moved onto Decimal.
    assert evaluate("Pow(0, 0)", {}) == 1
    assert evaluate("Pow([A], 0)", {"A": 10.0}) == 1


def test_a_result_no_double_can_hold_is_null():
    # `float(Decimal("1e400"))` is `inf`, and typed_csv refuses to write a non-finite float, so a
    # `Double` overflow has to become NULL at the point it happens — on every path alike.
    assert evaluate("Pow([A], 400)", {"A": 10.0}) is None
    assert evaluate("[A] * [B]", {"A": 1e200, "B": 1e200}) is None
    assert evaluate("[A] + [B]", {"A": 1e308, "B": 1e308}) is None


def test_datetime_diff_truncates_whole_units_toward_zero():
    # One second short of a day, in both directions: neither is a whole day.
    assert evaluate('DateTimeDiff("2026-03-01 00:00:00", "2026-02-28 23:59:59", "days")', {}) == 0
    assert evaluate('DateTimeDiff("2026-02-28 23:59:59", "2026-03-01 00:00:00", "days")', {}) == 0
    assert evaluate('DateTimeDiff("2026-03-01 00:00:00", "2026-02-27 23:59:59", "days")', {}) == 1
    assert evaluate('DateTimeDiff("2027-01-01", "2026-02-01", "years")', {}) == 0
    assert evaluate('DateTimeDiff("2026-02-01", "2027-01-01", "years")', {}) == 0
    assert evaluate('DateTimeDiff("2026-02-01", "2027-03-01", "years")', {}) == -1


def test_nondeterministic_today_is_refused():
    with pytest.raises(FormulaError, match="nondeterministic"):
        evaluate("DateTimeToday()", {})


def test_unknown_function_and_unknown_name_are_errors():
    with pytest.raises(FormulaError, match="Frobnicate"):
        evaluate("Frobnicate(1)", {})
    with pytest.raises(FormulaError, match="NOPE"):
        evaluate("[NOPE] + 1", {"A": 1})


def test_compiled_expression_is_reusable():
    from dev.formula import compile_expr
    expr = compile_expr("[A] * 2")
    assert evaluate(expr, {"A": 3}) == 6 and evaluate(expr, {"A": 4}) == 8


def test_row_window_nearest_and_forward_offsets():
    rows = [{"V": 1}, {"V": 2}, {"V": 3}]
    types = {"V": "Int32"}
    assert evaluate("[Row-1:V]", rows[0], rows=RowWindow(rows, 0, "nearest", types)) == 1
    assert evaluate("[Row+1:V]", rows[2], rows=RowWindow(rows, 2, "nearest", types)) == 3
    assert evaluate("[Row+1:V]", rows[0], rows=RowWindow(rows, 0, "null", types)) == 2
    assert evaluate("[Row+1:V]", rows[2], rows=RowWindow(rows, 2, "null", types)) is None
    assert evaluate("[Row-1:S]", {"S": "a"}, rows=RowWindow([{"S": "a"}], 0, "zero", {"S": "V_String"})) == ""


def test_coerce_types():
    from decimal import Decimal
    assert coerce(None, "Int32", 4) is None
    assert coerce("12", "Int32", 4) == 12 and coerce("abc", "Int32", 4) is None
    assert coerce(3, "Double", 8) == 3.0 and isinstance(coerce(3, "Double", 8), float)
    assert coerce(Decimal("2.345"), "FixedDecimal", 19, 2) == Decimal("2.35")
    assert coerce(12.0, "V_String", None) == "12"
    assert coerce(0, "Bool", None) is False and coerce(2, "Bool", None) is True
    assert coerce("2026-08-31 13:45:00", "Date", 10) == "2026-08-31"
    assert coerce("2026-08-31", "DateTime", 19) == "2026-08-31 00:00:00"
    assert coerce("2026-02-30", "Date", 10) is None
    assert coerce("kept", "SpatialObj", None) == "kept"
