"""The pure function nodes, one computation at a time."""

from __future__ import annotations

from typing import Any

import pytest

from code_ai.core.graph.functions import evaluate, is_function, number_of, text_of


def run(kind: str, pin: str = "result", **inputs: Any) -> tuple[Any, list[str]]:
    data = inputs.pop("data", {})
    warnings: list[str] = []
    return evaluate(kind, data, inputs, pin, warnings.append), warnings


def value(kind: str, pin: str = "result", **inputs: Any) -> Any:
    return run(kind, pin, **inputs)[0]


# -- math ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "a", "b", "expected"),
    [
        ("add", 2, 3, 5.0),
        ("subtract", 2, 3, -1.0),
        ("multiply", 2.5, 4, 10.0),
        ("divide", 7, 2, 3.5),
        ("modulo", 7, 3, 1.0),
        ("power", 2, 10, 1024.0),
        ("min", 2, 3, 2.0),
        ("max", 2, 3, 3.0),
    ],
)
def test_float_math(op: str, a: float, b: float, expected: float) -> None:
    assert value("math", a=a, b=b, data={"op": op}) == pytest.approx(expected)


def test_int_math_works_in_whole_numbers_and_truncates_division() -> None:
    assert value("math", a=7, b=2, data={"op": "divide", "type": "int"}) == 3
    assert value("math", a=-7, b=2, data={"op": "divide", "type": "int"}) == -3
    assert value("math", a=2.9, b=1, data={"op": "add", "type": "int"}) == 3
    assert isinstance(value("math", a=1, b=1, data={"op": "add", "type": "int"}), int)


def test_text_on_a_math_pin_gives_up_its_first_number() -> None:
    assert value("math", a="nota 8/10", b="1", data={"op": "add"}) == pytest.approx(9.0)


def test_division_by_zero_is_zero_and_says_so() -> None:
    result, warnings = run("math", a=1, b=0, data={"op": "divide"})

    assert result == 0.0
    assert warnings and "division by zero" in warnings[0]


def test_a_power_with_no_real_result_is_zero_and_says_so() -> None:
    result, warnings = run("math", a=-8, b=0.5, data={"op": "power"})

    assert result == 0.0
    assert warnings


# -- logic ---------------------------------------------------------------------


def test_numbers_compare_as_numbers_and_text_as_text() -> None:
    assert value("compare", a="10", b="9", data={"op": "greater"}) is True
    assert value("compare", a="banana", b="apple", data={"op": "greater"}) is True
    assert value("compare", a=3, b="3.0", data={"op": "equal"}) is True
    assert value("compare", a="ok", b="OK", data={"op": "equal"}) is False


@pytest.mark.parametrize(
    ("op", "a", "b", "expected"),
    [
        ("and", True, False, False),
        ("or", True, False, True),
        ("xor", True, True, False),
        ("not", False, True, True),
    ],
)
def test_logic(op: str, a: bool, b: bool, expected: bool) -> None:
    assert value("logic", a=a, b=b, data={"op": op}) is expected


def test_select_picks_by_the_condition() -> None:
    assert value("select", "value", condition=True, if_true="sim", if_false="nao") == "sim"
    assert value("select", "value", condition="false", if_true="sim", if_false="nao") == "nao"


# -- text ------------------------------------------------------------------------


def test_append_joins_its_parts_in_pin_order() -> None:
    joined = value("append", in_1="Mendel", in_0="Goodall", in_2="!", data={"separator": "\\n"})

    assert joined == "Goodall\nMendel\n!"


def test_format_fills_numbered_placeholders_and_leaves_unknown_ones() -> None:
    filled = value("format", template="{0} disse: {1} {2}", arg_0="Ana", arg_1="oi")

    assert filled == "Ana disse: oi {2}"


def test_text_case() -> None:
    assert value("text_case", text=" ola ", data={"op": "trim"}) == "ola"
    assert value("text_case", text="ola", data={"op": "upper"}) == "OLA"
    assert value("text_case", text="ola mundo", data={"op": "capitalize"}) == "Ola mundo"


def test_replace_honours_case_sensitivity() -> None:
    assert value("text_replace", text="Oi oi", find="oi", replace_with="tchau") == "Oi tchau"
    assert (
        value(
            "text_replace",
            text="Oi oi",
            find="oi",
            replace_with="tchau",
            data={"case_sensitive": False},
        )
        == "tchau tchau"
    )


def test_text_check_ignores_case_unless_told() -> None:
    assert value("text_check", text="Status: APPROVED", part="approved") is True
    assert (
        value(
            "text_check",
            text="Status: APPROVED",
            part="approved",
            data={"case_sensitive": True},
        )
        is False
    )
    assert value("text_check", text="  ", data={"op": "is_empty"}) is True
    assert value("text_check", text="abc", part="ab", data={"op": "starts_with"}) is True


def test_length_substring_and_split() -> None:
    assert value("text_length", "length", text="ola") == 3
    assert value("substring", text="orquestrador", start=0, length=5) == "orque"
    assert value("substring", text="orquestrador", start=-3, length=0) == "dor"
    assert value("split", "lines", text="a, b,,c", separator=",") == "a\nb\nc"
    assert value("split", "count", text="a, b,,c", separator=",") == 3
    assert value("split", "lines", text="um dois\ntres", separator="") == "um\ndois\ntres"


# -- conversions -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "given", "expected"),
    [
        ("truncate", "7.9", 7),
        ("round", 2.5, 3),
        ("round", -2.5, -3),
        ("floor", -1.2, -2),
        ("ceil", 1.2, 2),
        ("truncate", "Nota final: 8/10", 8),
        ("truncate", "nada aqui", 0),
    ],
)
def test_to_int(mode: str, given: Any, expected: int) -> None:
    assert value("to_int", value=given, data={"mode": mode}) == expected


def test_the_other_conversions() -> None:
    assert value("to_float", value="7,5") == pytest.approx(7.5)
    assert value("to_text", value=5.0) == "5"
    assert value("to_text", value=True) == "true"
    assert value("to_bool", value="sim") is True
    assert value("to_bool", value="0") is False


def test_readers_and_the_kind_list() -> None:
    assert number_of(None) == 0.0
    assert text_of(None) == ""
    assert is_function("math") and not is_function("print")
