"""The pure function nodes: arithmetic, comparisons, logic, text and conversions.

Each one is a plain function of the node's settings and its pulled inputs, so
the interpreter only has to fetch the inputs and ask. A value that cannot be
computed - a division by zero, a number that is not one - degrades to a neutral
result and leaves a warning, the way a Blueprint logs and carries on.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

from code_ai.core.graph.models import NodeKind

# The first number in a piece of text: what "8/10" or "score: 7,5" means.
_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")
_PLACEHOLDER = re.compile(r"\{(\d+)\}")

Warn = Callable[[str], None]


def evaluate(kind: str, data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> Any:
    """The value on output ``pin`` of a function node of ``kind``."""

    handler = _HANDLERS.get(kind)
    if handler is None:
        raise KeyError(f"{kind} is not a function node")
    return handler(data, inputs, pin, warn)


def is_function(kind: str) -> bool:
    return kind in _HANDLERS


# -- value readers ----------------------------------------------------------


def text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    label = getattr(value, "label", None)
    if callable(label):
        return str(label())
    return value if isinstance(value, str) else str(value)


def number_of(value: Any) -> float:
    """A number from anything, reading the first number out of text."""

    exact = _exact_number(value)
    if exact is not None:
        return exact
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    found = _NUMBER.search(text_of(value))
    if found is None:
        return 0.0
    return float(found.group(0).replace(",", "."))


def flag_of(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        return value != 0
    return text_of(value).strip().lower() in ("true", "1", "yes", "y", "sim", "on")


def _exact_number(value: Any) -> float | None:
    """A number only when the whole value is one; text that merely holds one is not."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def unescape(text: str) -> str:
    """Let a one-line field say newline and tab."""

    return text.replace("\\n", "\n").replace("\\t", "\t")


def dynamic_values(inputs: dict[str, Any], prefix: str) -> list[Any]:
    """The values on a node's numbered inputs, in order."""

    found: list[tuple[int, Any]] = []
    for name, value in inputs.items():
        head, _, index = name.rpartition("_")
        if head == prefix and index.isdigit():
            found.append((int(index), value))
    return [value for _, value in sorted(found)]


# -- math and logic ---------------------------------------------------------


def _math(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> Any:
    op = str(data.get("op") or "add")
    as_int = str(data.get("type") or "float") == "int"
    a = number_of(inputs.get("a"))
    b = number_of(inputs.get("b"))
    if as_int:
        a, b = float(int(a)), float(int(b))
    result: Any
    if op == "add":
        result = a + b
    elif op == "subtract":
        result = a - b
    elif op == "multiply":
        result = a * b
    elif op in ("divide", "modulo"):
        if b == 0:
            warn("division by zero; the result is 0.")
            result = 0.0
        elif op == "divide":
            # Integer division truncates toward zero, as a Blueprint's does.
            result = float(int(a / b)) if as_int else a / b
        else:
            result = math.fmod(a, b)
    elif op == "power":
        try:
            result = a**b
        except (OverflowError, ZeroDivisionError):
            result = None
        if not isinstance(result, float | int):
            warn("that power has no real result; the result is 0.")
            result = 0.0
    elif op == "min":
        result = min(a, b)
    elif op == "max":
        result = max(a, b)
    else:
        warn(f"unknown operation {op!r}; the result is 0.")
        result = 0.0
    if not math.isfinite(result):
        warn("the result is too large to be a number; the result is 0.")
        result = 0.0
    return int(result) if as_int else float(result)


def _compare(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> bool:
    op = str(data.get("op") or "equal")
    a, b = inputs.get("a"), inputs.get("b")
    left: Any = _exact_number(a)
    right: Any = _exact_number(b)
    # Numbers compare as numbers ("10" > "9"); anything else compares as text.
    if left is None or right is None:
        left, right = text_of(a), text_of(b)
    if op == "equal":
        return left == right
    if op == "not_equal":
        return left != right
    if op == "less":
        return left < right
    if op == "less_equal":
        return left <= right
    if op == "greater":
        return left > right
    if op == "greater_equal":
        return left >= right
    warn(f"unknown comparison {op!r}; the result is false.")
    return False


def _logic(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> bool:
    op = str(data.get("op") or "and")
    a = flag_of(inputs.get("a"))
    b = flag_of(inputs.get("b"))
    if op == "and":
        return a and b
    if op == "or":
        return a or b
    if op == "xor":
        return a != b
    if op == "not":
        return not a
    warn(f"unknown operation {op!r}; the result is false.")
    return False


def _select(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> Any:
    return inputs.get("if_true") if flag_of(inputs.get("condition")) else inputs.get("if_false")


# -- text -------------------------------------------------------------------


def _append(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> str:
    separator = unescape(str(data.get("separator") or ""))
    return separator.join(text_of(value) for value in dynamic_values(inputs, "in"))


def _format(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> str:
    template = text_of(inputs.get("template"))
    args = [text_of(value) for value in dynamic_values(inputs, "arg")]

    def fill(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return args[index] if index < len(args) else match.group(0)

    return _PLACEHOLDER.sub(fill, template)


def _text_case(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> str:
    op = str(data.get("op") or "upper")
    text = text_of(inputs.get("text"))
    if op == "upper":
        return text.upper()
    if op == "lower":
        return text.lower()
    if op == "trim":
        return text.strip()
    if op == "capitalize":
        return text[:1].upper() + text[1:]
    if op == "title":
        return text.title()
    warn(f"unknown operation {op!r}; the text is unchanged.")
    return text


def _text_replace(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> str:
    text = text_of(inputs.get("text"))
    find = unescape(text_of(inputs.get("find")))
    replacement = unescape(text_of(inputs.get("replace_with")))
    if not find:
        return text
    if flag_of(data.get("case_sensitive", True)):
        return text.replace(find, replacement)
    return re.sub(re.escape(find), lambda _: replacement, text, flags=re.IGNORECASE)


def _text_check(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> bool:
    op = str(data.get("op") or "contains")
    text = text_of(inputs.get("text"))
    part = text_of(inputs.get("part"))
    if op == "is_empty":
        return not text.strip()
    if not flag_of(data.get("case_sensitive", False)):
        text, part = text.lower(), part.lower()
    if op == "contains":
        return part in text
    if op == "starts_with":
        return text.startswith(part)
    if op == "ends_with":
        return text.endswith(part)
    if op == "equals":
        return text.strip() == part.strip()
    warn(f"unknown check {op!r}; the result is false.")
    return False


def _text_length(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> int:
    return len(text_of(inputs.get("text")))


def _substring(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> str:
    text = text_of(inputs.get("text"))
    start = int(number_of(inputs.get("start")))
    length = int(number_of(inputs.get("length")))
    if start < 0:
        start = max(0, len(text) + start)
    return text[start:] if length <= 0 else text[start : start + length]


def _split(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> Any:
    text = text_of(inputs.get("text"))
    separator = unescape(text_of(inputs.get("separator")))
    pieces = text.split(separator) if separator else text.split()
    kept = [piece.strip() for piece in pieces if piece.strip()]
    return len(kept) if pin == "count" else "\n".join(kept)


# -- conversions ------------------------------------------------------------


def _to_int(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> int:
    value = number_of(inputs.get("value"))
    mode = str(data.get("mode") or "truncate")
    if mode == "round":
        # Half away from zero, not Python's half to even.
        return int(math.copysign(math.floor(abs(value) + 0.5), value))
    if mode == "floor":
        return math.floor(value)
    if mode == "ceil":
        return math.ceil(value)
    return int(value)


def _to_float(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> float:
    return number_of(inputs.get("value"))


def _to_text(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> str:
    return text_of(inputs.get("value"))


def _to_bool(data: dict[str, Any], inputs: dict[str, Any], pin: str, warn: Warn) -> bool:
    return flag_of(inputs.get("value"))


_HANDLERS: dict[str, Callable[[dict[str, Any], dict[str, Any], str, Warn], Any]] = {
    NodeKind.MATH: _math,
    NodeKind.COMPARE: _compare,
    NodeKind.LOGIC: _logic,
    NodeKind.SELECT: _select,
    NodeKind.APPEND: _append,
    NodeKind.FORMAT: _format,
    NodeKind.TEXT_CASE: _text_case,
    NodeKind.TEXT_REPLACE: _text_replace,
    NodeKind.TEXT_CHECK: _text_check,
    NodeKind.TEXT_LENGTH: _text_length,
    NodeKind.SUBSTRING: _substring,
    NodeKind.SPLIT: _split,
    NodeKind.TO_INT: _to_int,
    NodeKind.TO_FLOAT: _to_float,
    NodeKind.TO_TEXT: _to_text,
    NodeKind.TO_BOOL: _to_bool,
}

FUNCTION_KINDS = frozenset(_HANDLERS)
