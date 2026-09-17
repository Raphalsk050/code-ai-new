"""Reading a LaTeX log into errors, warnings and the files that were missing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_FILE_LINE_ERROR = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:\n]+\.(?:tex|sty|cls|bbl|aux|ltx|def))"
    r":(?P<line>\d+): (?P<message>.+)$"
)
_BANG = re.compile(r"^! (?P<message>.+)$")
_LINE_REF = re.compile(r"^l\.(?P<line>\d+)\s?(?P<context>.*)$")
_MISSING = re.compile(
    r"File `(?P<file>[^']+)' not found"
    r"|! I can't find file `(?P<file2>[^']+)'"
    r"|Package fontspec Error: The font \"(?P<font>[^\"]+)\" cannot be found"
)
_UNDEFINED_CITATION = re.compile(
    r"(?:LaTeX|Package natbib) Warning: Citation [`'](?P<key>[^']+)' (?:on page \d+ )?undefined"
)
_UNDEFINED_REFERENCE = re.compile(
    r"LaTeX Warning: Reference [`'](?P<key>[^']+)' on page \d+ undefined"
)
_BOX = re.compile(
    r"^(?P<kind>Overfull|Underfull) \\(?P<box>[hv])box \((?P<amount>[^)]*)\) "
    r"(?:in paragraph|detected|has occurred while \\output is active)"
    r"(?: at lines? (?P<lines>[\d-]+))?"
)
_WARNING = re.compile(
    r"^(?:LaTeX|Package (?P<package>\S+)|Class (?P<class>\S+)) Warning: (?P<message>.+)$"
)
_FONT = re.compile(r"^LaTeX Font Warning: (?P<message>.+)$")
_RERUN = re.compile(
    r"Rerun to get|rerunfilecheck Warning: File .* has changed"
    r"|There were undefined references|Label\(s\) may have changed"
)
_BIBTEX_MISSING = re.compile(r"I couldn't open (?:database|style) file (?P<file>\S+)")


@dataclass
class LogReport:
    errors: list[dict] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    undefined_citations: list[str] = field(default_factory=list)
    undefined_references: list[str] = field(default_factory=list)
    overfull: int = 0
    underfull: int = 0
    worst_boxes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_rerun: bool = False

    def to_dict(self, *, limit: int = 20) -> dict:
        data: dict = {}
        if self.errors:
            data["errors"] = self.errors[:limit]
        for key in ("missing_files", "undefined_citations", "undefined_references"):
            values = getattr(self, key)
            if values:
                data[key] = values[:limit]
        if self.overfull or self.underfull:
            data["boxes"] = {
                "overfull": self.overfull,
                "underfull": self.underfull,
                "worst": self.worst_boxes[:5],
            }
        if self.warnings:
            data["warnings"] = self.warnings[:limit]
        return data


def _joined(text: str) -> list[str]:
    """TeX wraps log lines at 79 characters; glue them back."""

    lines: list[str] = []
    previous_length = 0
    for raw in text.splitlines():
        # Only a physical line that hit the 79-column limit continues on the next one.
        continues = previous_length == 79 and not raw.startswith(
            ("!", "l.", "Overfull", "Underfull", "LaTeX", "Package")
        )
        # TeX prints error paths as ./x, /x or C:/x; a match without that prefix is the tail
        # of a long path that wrapped.
        starts_error = _FILE_LINE_ERROR.match(raw) and re.match(r"^(\./|\.\./|/|[A-Za-z]:)", raw)
        if lines and continues and not starts_error:
            lines[-1] += raw
        else:
            lines.append(raw)
        previous_length = len(raw)
    return lines


def parse(text: str, *, source_root: Path | None = None) -> LogReport:
    report = LogReport()
    lines = _joined(text)
    seen_errors: set[tuple] = set()
    for index, line in enumerate(lines):
        match = _FILE_LINE_ERROR.match(line)
        bang = _BANG.match(line) if not match else None
        if match or bang:
            message = (match or bang).group("message").strip()
            file = match.group("file") if match else None
            number = int(match.group("line")) if match else None
            help_lines = []
            for follow in lines[index + 1 : index + 8]:
                if _BANG.match(follow) or _FILE_LINE_ERROR.match(follow):
                    break  # that line number belongs to the next error
                ref = _LINE_REF.match(follow)
                if ref:
                    number = number or int(ref.group("line"))
                    if ref.group("context").strip():
                        help_lines.append(ref.group("context").strip())
                    break
            key = (file, number, message)
            if key in seen_errors:
                continue
            seen_errors.add(key)
            entry: dict = {"message": message}
            if file:
                entry["file"] = file.replace("\\", "/").lstrip("./")
            if number:
                entry["line"] = number
            if help_lines:
                entry["near"] = help_lines[0][:160]
            hint = _hint(message)
            if hint:
                entry["hint"] = hint
            excerpt = _excerpt(source_root, entry.get("file"), number)
            if excerpt:
                entry["source"] = excerpt
            report.errors.append(entry)
        missing = _MISSING.search(line)
        if missing:
            name = missing.group("file") or missing.group("file2") or missing.group("font")
            if name and name not in report.missing_files:
                report.missing_files.append(name)
        bib_missing = _BIBTEX_MISSING.search(line)
        if bib_missing and bib_missing.group("file") not in report.missing_files:
            report.missing_files.append(bib_missing.group("file"))
        citation = _UNDEFINED_CITATION.search(line)
        if citation and citation.group("key") not in report.undefined_citations:
            report.undefined_citations.append(citation.group("key"))
        reference = _UNDEFINED_REFERENCE.search(line)
        if reference and reference.group("key") not in report.undefined_references:
            report.undefined_references.append(reference.group("key"))
        box = _BOX.match(line)
        if box:
            if box.group("kind") == "Overfull":
                report.overfull += 1
                amount = box.group("amount")
                where = f" at lines {box.group('lines')}" if box.group("lines") else ""
                report.worst_boxes.append(f"{amount}{where}")
            else:
                report.underfull += 1
        font = _FONT.match(line)
        if font:
            _add_warning(report, f"Font: {font.group('message')}")
        warning = _WARNING.match(line)
        if (
            warning
            and not _UNDEFINED_CITATION.search(line)
            and not _UNDEFINED_REFERENCE.search(line)
        ):
            owner = warning.group("package") or warning.group("class")
            _add_warning(report, f"{owner + ': ' if owner else ''}{warning.group('message')}")
        if _RERUN.search(line):
            report.needs_rerun = True
    report.worst_boxes.sort(key=_points, reverse=True)
    return report


def _add_warning(report: LogReport, message: str) -> None:
    if message not in report.warnings and len(report.warnings) < 40:
        report.warnings.append(message[:240])


def _points(entry: str) -> float:
    match = re.search(r"([\d.]+)pt", entry)
    return float(match.group(1)) if match else 0.0


_HINTS = (
    ("Undefined control sequence", "A command is misspelled or its package is not loaded."),
    (
        "Missing $ inserted",
        "Math-only syntax (_ ^ \\alpha) is outside $...$, or a _ or & in text needs escaping.",
    ),
    (
        "not found",
        "A file or package is missing; latex_compile installs packages when Code-AI "
        "manages the TeX Live installation.",
    ),
    ("Misplaced alignment tab character &", "An & outside a table: write \\& in text."),
    ("Extra alignment tab", "A table row has more cells than the column spec allows."),
    (
        "Unicode character",
        "A character pdflatex cannot typeset: use xelatex/lualatex or a LaTeX macro for it.",
    ),
    ("Environment", "An environment is undefined (missing package) or \\begin/\\end do not match."),
    ("Runaway argument", "A { or } is unbalanced."),
    ("File ended while scanning", "A { or } is unbalanced."),
    ("Too many }'s", "A closing brace has no opening one."),
    ("Emergency stop", "LaTeX stopped at an earlier error; fix the first error listed."),
)


def _hint(message: str) -> str | None:
    for needle, hint in _HINTS:
        if needle.lower() in message.lower():
            return hint
    return None


def _excerpt(root: Path | None, file: str | None, line: int | None) -> str | None:
    if root is None or not file or not line:
        return None
    path = (root / file) if not Path(file).is_absolute() else Path(file)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    start, end = max(0, line - 3), min(len(lines), line + 2)
    return "\n".join(
        f"{'>' if n + 1 == line else ' '} {n + 1}: {lines[n]}" for n in range(start, end)
    )
