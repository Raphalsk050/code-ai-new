"""Argument handling every office-style tool repeats."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from code_ai.config.defaults import DEFAULT_CONFIG_DIRNAME
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolContext
from code_ai.tools.locations import for_context

# Programs Code-AI installs for itself (TeX Live, LibreOffice): per user, never
# system-wide, so no install needs a password.
MANAGED_TOOLS_ENV = "CODE_AI_TOOLS_DIR"


def managed_tools_dir() -> Path:
    override = os.environ.get(MANAGED_TOOLS_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_CONFIG_DIRNAME / "tools"


def require_str(arguments: dict[str, Any], key: str, tool: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError(f"{tool} requires '{key}'.")
    return value.strip()


def optional_str(arguments: dict[str, Any], key: str, default: str = "") -> str:
    value = arguments.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ToolArgumentError(f"'{key}' must be a string.")
    return value.strip() or default


def clamp_int(value: Any, *, default: int, low: int, high: int, name: str = "value") -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolArgumentError(f"'{name}' must be an integer, got {value!r}.") from None
    return max(low, min(high, number))


def clamp_float(
    value: Any, *, default: float, low: float, high: float, name: str = "value"
) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ToolArgumentError(f"'{name}' must be a number, got {value!r}.") from None
    return max(low, min(high, number))


def resolve_input(
    context: ToolContext, raw: str, *, location: object = None, suffixes: tuple[str, ...] = ()
) -> Path:
    """An existing file the tool reads, inside the workspace or the sandbox."""

    path = for_context(context, location).resolve(raw, must_exist=True)
    if not path.is_file():
        raise ToolExecutionError(f"{raw} is not a file.")
    if suffixes and path.suffix.lower() not in suffixes:
        raise ToolArgumentError(f"{raw} is not a {' / '.join(suffixes)} file.")
    return path


def resolve_output(
    context: ToolContext,
    raw: str,
    *,
    location: object = None,
    overwrite: bool = True,
    suffixes: tuple[str, ...] = (),
) -> Path:
    """A file the tool writes. Parent directories are created."""

    path = for_context(context, location).resolve(raw, must_exist=False)
    if suffixes and path.suffix.lower() not in suffixes:
        raise ToolArgumentError(f"{raw} must end in {' or '.join(suffixes)}.")
    if path.is_dir():
        raise ToolExecutionError(f"{raw} is a directory.")
    if path.exists() and not overwrite:
        raise ToolExecutionError(f"{raw} already exists; pass overwrite true to replace it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def display_path(context: ToolContext, path: Path) -> str:
    """The path as the model named it: relative to the workspace or the sandbox."""

    roots = [context.workspace.root]
    sandbox = getattr(context, "sandbox", None)
    if sandbox is not None:
        roots.append(sandbox.root)
    for root in roots:
        try:
            return path.resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def image_entry(png: bytes, media_type: str = "image/png") -> dict[str, str]:
    return {"data": base64.b64encode(png).decode("ascii"), "media_type": media_type}


def attach_images(payload: dict[str, Any], pngs: list[bytes]) -> dict[str, Any]:
    """Hand rendered pages to the model as pictures, not as JSON."""

    if pngs:
        payload[TOOL_IMAGES_KEY] = [image_entry(png) for png in pngs]
    return payload


def parse_page_spec(spec: Any, total: int) -> list[int]:
    """Zero-based page indexes from "1-3,5", "all", "last", "-2" or a list of numbers.

    Pages are one-based for the model, the way every viewer numbers them.
    Negative numbers count from the end. Order and repeats are kept: a spec is
    also how pages are reordered and duplicated.
    """

    if total <= 0:
        return []
    if spec is None or (isinstance(spec, str) and spec.strip().lower() in {"", "all"}):
        return list(range(total))
    if isinstance(spec, int):
        parts: list[str] = [str(spec)]
    elif isinstance(spec, list):
        parts = [str(item) for item in spec]
    elif isinstance(spec, str):
        parts = [part for part in spec.replace(";", ",").split(",")]
    else:
        raise ToolArgumentError(f"Unreadable page selection: {spec!r}.")

    pages: list[int] = []
    for part in (p.strip().lower() for p in parts):
        if not part:
            continue
        if "-" in part[1:]:
            split_at = part.index("-", 1)
            start = _page_number(part[:split_at], total)
            end = _page_number(part[split_at + 1 :], total)
            step = 1 if end >= start else -1
            pages.extend(range(start, end + step, step))
        else:
            pages.append(_page_number(part, total))
    return pages


def _page_number(text: str, total: int) -> int:
    text = text.strip()
    if text in {"last", "end", ""}:
        return total - 1
    if text == "first":
        return 0
    try:
        number = int(text)
    except ValueError:
        raise ToolArgumentError(f"Not a page number: {text!r}.") from None
    index = total + number if number < 0 else number - 1
    if not 0 <= index < total:
        raise ToolArgumentError(f"Page {text} is out of range: the document has {total} pages.")
    return index
