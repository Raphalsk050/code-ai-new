"""Slides described as Markdown or JSON, normalised to one dict shape the layouts draw."""

from __future__ import annotations

import json
import re
from typing import Any

from code_ai.core.errors import ToolArgumentError

KINDS = (
    "title",
    "section",
    "bullets",
    "two_column",
    "comparison",
    "image",
    "image_text",
    "table",
    "chart",
    "quote",
    "stats",
    "timeline",
    "cards",
    "agenda",
    "code",
    "closing",
)

KIND_ALIASES = {
    "cover": "title",
    "divider": "section",
    "content": "bullets",
    "text": "bullets",
    "list": "bullets",
    "columns": "two_column",
    "two_columns": "two_column",
    "picture": "image",
    "image_left": "image_text",
    "image_right": "image_text",
    "numbers": "stats",
    "kpi": "stats",
    "big_number": "stats",
    "process": "timeline",
    "steps": "timeline",
    "grid": "cards",
    "team": "cards",
    "features": "cards",
    "toc": "agenda",
    "end": "closing",
    "thanks": "closing",
    "thank_you": "closing",
}

CHART_TYPES = (
    "column",
    "bar",
    "stacked_column",
    "stacked_bar",
    "line",
    "pie",
    "doughnut",
    "area",
    "scatter",
)

_DIRECTIVE = re.compile(r"<!--\s*([a-z_]+)\s*:\s*(.*?)\s*-->", re.IGNORECASE)
_SEPARATOR = re.compile(r"^\s*---+\s*$")
_BULLET = re.compile(r"^(\s*)(?:[-*+•]|\d+[.)])\s+(.*)$")
_IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")
_STAT = re.compile(r"^\*\*(.+?)\*\*\s*[:\-–—]?\s*(.*)$")
_CARD = re.compile(r"^\*\*(.+?)\*\*\s*[:\-–—]?\s*(.*)$")


def kind_name(value: Any, index: int) -> str:
    name = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    name = KIND_ALIASES.get(name, name)
    if name not in KINDS:
        raise ToolArgumentError(
            f"slide {index}: unknown kind {value!r}. Kinds: {', '.join(KINDS)}."
        )
    return name


def parse(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list | dict):
        return parse_json(content)
    if not isinstance(content, str) or not content.strip():
        raise ToolArgumentError("Give the slides as Markdown or a JSON array.")
    stripped = content.strip()
    if stripped.startswith(("[", "{")):
        try:
            return parse_json(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ToolArgumentError(f"The slides look like JSON but do not parse: {exc}") from None
    return parse_markdown(content)


# -- JSON --------------------------------------------------------------------------------


def parse_json(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get("slides", [data])
    if not isinstance(data, list) or not data:
        raise ToolArgumentError("The JSON must be an array of slide objects.")
    slides = []
    for index, raw in enumerate(data, start=1):
        if not isinstance(raw, dict):
            raise ToolArgumentError(f"slide {index} must be an object.")
        slide = dict(raw)
        slide["body"] = _body(raw.get("body", raw.get("bullets", raw.get("items", []))), index)
        for side in ("left", "right"):
            if isinstance(raw.get(side), dict):
                column = dict(raw[side])
                column["body"] = _body(column.get("body", column.get("bullets", [])), index)
                slide[side] = column
            elif isinstance(raw.get(side), list | str):
                slide[side] = {"body": _body(raw[side], index)}
        slide["stats"] = _pairs(raw.get("stats"), "value", "label")
        slide["steps"] = _pairs(raw.get("steps"), "title", "text")
        slide["cards"] = _pairs(raw.get("cards"), "title", "text")
        slide["kind"] = kind_name(
            raw.get("kind") or raw.get("layout") or _infer(slide, index), index
        )
        slides.append(validate(slide, index))
    return slides


def _body(value: Any, index: int) -> list[tuple[str, int]]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return _markdown_body(value.splitlines())
    out: list[tuple[str, int]] = []

    def walk(items, level):
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
            elif isinstance(item, dict):
                out.append((str(item.get("text", "")), int(item.get("level", level))))
            else:
                out.append((str(item), level))

    if not isinstance(value, list):
        raise ToolArgumentError(f"slide {index}: body is a list of strings (nested lists indent).")
    walk(value, 0)
    return out


def _pairs(value: Any, first: str, second: str) -> list[tuple[str, str]]:
    if not value:
        return []
    pairs = []
    for item in value:
        if isinstance(item, dict):
            pairs.append((str(item.get(first, "")), str(item.get(second, ""))))
        elif isinstance(item, list | tuple) and item:
            pairs.append((str(item[0]), str(item[1]) if len(item) > 1 else ""))
        else:
            pairs.append((str(item), ""))
    return pairs


# -- Markdown ----------------------------------------------------------------------------


def parse_markdown(text: str) -> list[dict[str, Any]]:
    chunks: list[list[str]] = [[]]
    fence = False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            fence = not fence
        if not fence and _SEPARATOR.match(line):
            chunks.append([])
            continue
        chunks[-1].append(line)
    chunks = [chunk for chunk in chunks if any(line.strip() for line in chunk)]
    if not chunks:
        raise ToolArgumentError("No slides found: separate slides with a line containing ---.")
    slides = []
    for index, chunk in enumerate(chunks, start=1):
        slide = _markdown_slide(chunk, index, first=index == 1)
        slides.append(validate(slide, index))
    return slides


def _markdown_slide(lines: list[str], index: int, *, first: bool) -> dict[str, Any]:
    slide: dict[str, Any] = {}
    content: list[str] = []
    notes: list[str] = []
    in_notes = False
    for line in lines:
        directive = _DIRECTIVE.search(line)
        if directive and not line.strip().replace(directive.group(0), "").strip():
            slide[directive.group(1).lower()] = directive.group(2)
            continue
        if re.match(r"^\s*(note|notes|nota|notas)\s*:", line, re.I) or line.strip() == "???":
            in_notes = True
            rest = line.split(":", 1)[1] if ":" in line else ""
            if rest.strip():
                notes.append(rest.strip())
            continue
        (notes if in_notes else content).append(line)
    if notes:
        slide["notes"] = "\n".join(notes).strip()

    body_lines: list[str] = []
    code: list[str] = []
    code_language = ""
    fence = False
    table_rows: list[list[str]] = []
    quote: list[str] = []
    columns: list[list[str]] = [[]]
    for line in content:
        stripped = line.strip()
        if stripped.startswith("```"):
            if fence:
                fence = False
                if code_language == "chart":
                    try:
                        slide["chart"] = json.loads("\n".join(code))
                    except json.JSONDecodeError as exc:
                        raise ToolArgumentError(
                            f"slide {index}: chart block is not JSON: {exc}"
                        ) from None
                    code = []
                continue
            fence = True
            code_language = stripped[3:].strip().lower()
            continue
        if fence:
            code.append(line)
            continue
        if stripped in {"|||", "<!-- column -->", "***column***"}:
            columns.append([])
            continue
        if stripped.startswith("# ") and "title" not in slide:
            slide["title"] = stripped[2:].strip()
            continue
        if stripped.startswith("## ") and "subtitle" not in slide and not any(columns[-1]):
            slide["subtitle"] = stripped[3:].strip()
            continue
        image = _IMAGE.match(line)
        if image and "image" not in slide:
            slide["caption"], slide["image"] = image.group(1), image.group(2)
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
                table_rows.append(cells)
            continue
        if stripped.startswith(">"):
            quote.append(stripped.lstrip("> ").rstrip())
            continue
        columns[-1].append(line)

    if code:
        slide["code"] = "\n".join(code).rstrip()
        slide["language"] = code_language
    if table_rows:
        slide["table"] = table_rows
    if quote:
        author = [q for q in quote if q.startswith(("—", "–", "-- ", "- "))]
        slide["quote"] = " ".join(q for q in quote if q not in author).strip()
        if author:
            slide["author"] = author[0].lstrip("—–- ").strip()

    if len(columns) > 1:
        sides = []
        for column in columns[:2]:
            heading = next((c.strip()[4:] for c in column if c.strip().startswith("### ")), "")
            rest = [c for c in column if not c.strip().startswith("### ")]
            sides.append({"heading": heading, "body": _markdown_body(rest)})
        slide["left"], slide["right"] = sides[0], sides[1] if len(sides) > 1 else {"body": []}
    else:
        body_lines = columns[0]
        slide["body"] = _markdown_body(body_lines)

    kind = slide.pop("kind", None) or slide.pop("layout", None)
    if kind:
        kind = kind_name(kind, index)
    else:
        kind = _infer(slide, index, first=first)
    slide["kind"] = kind
    body = slide.get("body", [])
    if kind == "stats":
        slide["stats"] = [_split(text, _STAT) for text, _ in body]
    elif kind == "timeline":
        slide["steps"] = [_split(text, _CARD) for text, level in body if level == 0]
    elif kind == "cards":
        slide["cards"] = [_split(text, _CARD) for text, level in body if level == 0]
    return slide


def _markdown_body(lines: list[str]) -> list[tuple[str, int]]:
    body: list[tuple[str, int]] = []
    paragraph: list[str] = []
    for line in lines:
        match = _BULLET.match(line)
        if match:
            if paragraph:
                body.append((" ".join(paragraph), 0))
                paragraph = []
            indent = len(match.group(1).replace("\t", "    "))
            body.append((match.group(2).strip(), min(4, indent // 2)))
        elif line.strip():
            if line.startswith("### "):
                body.append((f"**{line[4:].strip()}**", 0))
            else:
                paragraph.append(line.strip())
        elif paragraph:
            body.append((" ".join(paragraph), 0))
            paragraph = []
    if paragraph:
        body.append((" ".join(paragraph), 0))
    return body


def _split(text: str, pattern: re.Pattern) -> tuple[str, str]:
    match = pattern.match(text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    head, _, tail = text.partition(":")
    return (head.strip(), tail.strip()) if tail else (text.strip(), "")


def _infer(slide: dict[str, Any], index: int, *, first: bool = False) -> str:
    if slide.get("chart"):
        return "chart"
    if slide.get("table"):
        return "table"
    if slide.get("code"):
        return "code"
    if slide.get("left") or slide.get("right"):
        return "two_column"
    body = slide.get("body") or []
    if slide.get("quote") and not body:
        return "quote"
    if slide.get("image"):
        return "image_text" if body else "image"
    if slide.get("stats"):
        return "stats"
    if slide.get("steps"):
        return "timeline"
    if slide.get("cards"):
        return "cards"
    short = len(body) <= 3 and all(len(plain(text)) <= 80 for text, _ in body)
    if first and short and (slide.get("subtitle") or not body):
        return "title"
    if not body:
        return "title" if slide.get("subtitle") and first else "section"
    return "bullets"


def validate(slide: dict[str, Any], index: int) -> dict[str, Any]:
    kind = slide["kind"]
    need = {
        "image": ("image",),
        "image_text": ("image",),
        "table": ("table",),
        "chart": ("chart",),
        "quote": ("quote",),
        "code": ("code",),
        "stats": ("stats",),
        "timeline": ("steps",),
        "cards": ("cards",),
    }.get(kind, ())
    for key in need:
        if not slide.get(key):
            raise ToolArgumentError(f"slide {index} ({kind}) needs '{key}'.")
    if kind in {"two_column", "comparison"} and not (slide.get("left") or slide.get("right")):
        raise ToolArgumentError(
            f"slide {index} ({kind}) needs 'left' and 'right' (Markdown: split with a '|||' line)."
        )
    if kind == "table":
        rows = slide["table"]
        if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
            raise ToolArgumentError(f"slide {index}: table is a list of rows (lists of cells).")
    if kind == "chart":
        chart = slide["chart"]
        if not isinstance(chart, dict):
            raise ToolArgumentError(f"slide {index}: chart is an object.")
        chart_type = str(chart.get("type", "column")).lower()
        if chart_type not in CHART_TYPES:
            raise ToolArgumentError(
                f"slide {index}: chart type is one of {', '.join(CHART_TYPES)}."
            )
        series = chart.get("series")
        if not isinstance(series, list | dict) or not series:
            raise ToolArgumentError(
                f"slide {index}: chart needs series, e.g. "
                '{"type":"column","categories":["Q1","Q2"],"series":{"Revenue":[10,12]}}.'
            )
        if chart_type != "scatter" and not chart.get("categories"):
            raise ToolArgumentError(f"slide {index}: chart needs 'categories'.")
    if kind in {"stats", "timeline", "cards"}:
        key = {"stats": "stats", "timeline": "steps", "cards": "cards"}[kind]
        if len(slide[key]) > 8:
            raise ToolArgumentError(f"slide {index}: at most 8 {key} per slide - split the slide.")
    return slide


def plain(text: str) -> str:
    """Markdown emphasis removed, for measuring."""

    return re.sub(r"(\*\*|__|\*|_|`)", "", text)


def runs(text: str) -> list[tuple[str, bool, bool, bool]]:
    """(text, bold, italic, code) segments from **bold**, *italic* and `code`."""

    out: list[tuple[str, bool, bool, bool]] = []
    pattern = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)")
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            out.append((text[position : match.start()], False, False, False))
        token = match.group(0)
        if token.startswith("**"):
            out.append((token[2:-2], True, False, False))
        elif token.startswith("`"):
            out.append((token[1:-1], False, False, True))
        else:
            out.append((token[1:-1], False, True, False))
        position = match.end()
    if position < len(text):
        out.append((text[position:], False, False, False))
    return out or [("", False, False, False)]
