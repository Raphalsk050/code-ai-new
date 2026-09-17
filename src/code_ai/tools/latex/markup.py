"""Markdown with math, citations and raw LaTeX, turned into LaTeX body text.

Math ($...$, $$...$$, \\[...\\]) and LaTeX commands are lifted out before the
Markdown parser sees them - otherwise an underscore in a subscript becomes
emphasis - and put back verbatim afterwards.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_SPECIALS = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "%": r"\%",
    "_": r"\_",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
}
_ESCAPE = re.compile(r"[\\{}$&#%_^~]")
_PROTECT = re.compile(
    r"(\$\$.+?\$\$|\\\[.+?\\\]|\\\(.+?\\\)|(?<![\\$])\$(?!\s)[^$\n]+?(?<!\s)\$"
    r"|\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|tikzpicture|algorithm\w*)\}.+?\\end\{\2\}"
    r"|\\[a-zA-Z@]+\*?(?:\[[^\]\n]*\])?(?:\{[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*\})*)",
    re.DOTALL,
)
_CITE = re.compile(r"\[(@[\w:.\-/]+(?:\s*[;,]\s*@[\w:.\-/]+)*)\]")
_CITE_TEXT = re.compile(r"(?<![\w@])@([A-Za-z][\w:.\-/]*\w)")
_REF = re.compile(r"@(fig|tab|sec|eq|lst):([\w\-]+)")
_ATTRS = re.compile(r"^\{([^}]*)\}")
_TABLE_CAPTION = re.compile(
    r"^(?:table|tabela)\s*:\s*(.+?)(?:\s*\{#(tab:[\w\-]+)\})?\s*$", re.IGNORECASE
)


def escape(text: str) -> str:
    return _ESCAPE.sub(lambda m: _SPECIALS[m.group(0)], text).replace("\u00a0", "~")


@dataclass
class Converter:
    """One document's conversion state: citation keys seen and figures to copy."""

    copy_image: Callable[[str], str]
    cite_command: str = "cite"
    language: str = "english"
    heading_commands: tuple[str, ...] = ("section", "subsection", "subsubsection", "paragraph")
    citations: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    _slots: dict[str, str] = field(default_factory=dict)

    def convert(self, markdown: str) -> str:
        from markdown_it import MarkdownIt

        protected = self._protect(markdown)
        parser = MarkdownIt("commonmark", {"html": True})
        parser.enable(["table", "strikethrough"])
        tokens = parser.parse(protected)
        return self._restore(self._blocks(tokens)).strip() + "\n"

    # -- protection ------------------------------------------------------------------------

    def _protect(self, text: str) -> str:
        def keep(match: re.Match) -> str:
            slot = f"XLATEXSLOT{len(self._slots)}X"
            self._slots[slot] = match.group(0)
            return slot

        # Fenced code stays exactly as written: no math, citation or command handling inside.
        parts = re.split(r"(^```.*?^```[ \t]*$)", text, flags=re.MULTILINE | re.DOTALL)
        out = []
        for part in parts:
            if part.startswith("```"):
                out.append(part)
                continue
            part = _CITE.sub(lambda m: keep_cite(m, keep, self), part)
            part = _REF.sub(lambda m: keep_ref(m, keep, self.language), part)
            out.append(_PROTECT.sub(keep, part))
        return "".join(out)

    def _restore(self, text: str) -> str:
        for _ in range(3):
            if "XLATEXSLOT" not in text:
                break
            text = re.sub(
                r"XLATEXSLOT\d+X", lambda m: self._slots.get(m.group(0), m.group(0)), text
            )
        return text

    # -- blocks ----------------------------------------------------------------------------

    def _blocks(self, tokens) -> str:
        out: list[str] = []
        lists: list[str] = []
        pending_caption: tuple[str, str | None] | None = None
        index = 0
        while index < len(tokens):
            token = tokens[index]
            kind = token.type
            if kind == "heading_open":
                level = int(token.tag[1])
                command = self.heading_commands[min(level, len(self.heading_commands)) - 1]
                text, label = _split_label(tokens[index + 1].content)
                out.append(
                    f"\n\\{command}{{{self._inline_text(text)}}}"
                    + (f"\\label{{{label}}}" if label else "")
                    + "\n"
                )
                index += 3
                continue
            if kind in {"bullet_list_open", "ordered_list_open"}:
                environment = "itemize" if kind == "bullet_list_open" else "enumerate"
                lists.append(environment)
                out.append(f"\\begin{{{environment}}}")
            elif kind in {"bullet_list_close", "ordered_list_close"}:
                out.append(f"\\end{{{lists.pop()}}}\n")
            elif kind == "list_item_open":
                out.append("  \\item ")
            elif kind == "paragraph_open":
                inline = tokens[index + 1]
                caption = _TABLE_CAPTION.match(inline.content.strip())
                if caption and index + 3 < len(tokens) and tokens[index + 3].type == "table_open":
                    pending_caption = (caption.group(1), caption.group(2))
                    index += 3
                    continue
                children = inline.children or []
                images = [c for c in children if c.type == "image"]
                if (
                    len(images) == 1
                    and not lists
                    and not inline.content.replace(_image_markup(inline.content), "").strip()
                ):
                    out.append(self._figure(images[0], inline.content))
                    index += 3
                    continue
                text = self._inline(children)
                out.append(text + ("\n" if lists else "\n\n"))
                index += 3
                continue
            elif kind == "blockquote_open":
                out.append("\\begin{quote}")
            elif kind == "blockquote_close":
                out.append("\\end{quote}\n")
            elif kind in {"fence", "code_block"}:
                language = (token.info or "").strip().lower()
                if language in {"latex", "tex", "raw"}:
                    out.append(token.content.rstrip() + "\n")
                else:
                    out.append(
                        "\\begin{verbatim}\n" + token.content.rstrip("\n") + "\n\\end{verbatim}\n"
                    )
            elif kind == "table_open":
                end = next(i for i in range(index, len(tokens)) if tokens[i].type == "table_close")
                out.append(self._table(tokens[index : end + 1], pending_caption))
                pending_caption = None
                index = end + 1
                continue
            elif kind == "hr":
                out.append("\n\\bigskip\\noindent\\rule{\\linewidth}{0.4pt}\\bigskip\n")
            elif kind == "html_block":
                content = token.content.strip()
                if re.match(r"<!--\s*(pagebreak|newpage)\s*-->", content, re.I):
                    out.append("\\clearpage\n")
            index += 1
        return "\n".join(part for part in out if part is not None)

    def _figure(self, image, raw: str) -> str:
        source = image.attrGet("src") or ""
        caption_text, label = _split_label(image.content or "")
        attributes = _ATTRS.match(raw[raw.find(")") + 1 :].strip()) if ")" in raw else None
        width = r"0.8\linewidth"
        if attributes:
            found = re.search(r"width\s*=\s*([\d.]+)(\\?\w+|%)?", attributes.group(1))
            if found:
                number, unit = found.group(1), found.group(2) or ""
                width = (
                    f"{float(number) / 100:g}\\linewidth"
                    if unit == "%"
                    else (f"{number}\\linewidth" if not unit else f"{number}{unit}")
                )
            label_match = re.search(r"#(fig:[\w\-]+)", attributes.group(1))
            if label_match:
                label = label_match.group(1)
        try:
            path = self.copy_image(source)
        except Exception as exc:  # noqa: BLE001 - reported; the rest of the article still builds
            self.notes.append(f"Figure {source!r} skipped: {exc}")
            return ""
        lines = [
            "\\begin{figure}[htbp]",
            "  \\centering",
            f"  \\includegraphics[width={width}]{{{path}}}",
        ]
        if caption_text:
            lines.append(f"  \\caption{{{self._inline_text(caption_text)}}}")
        if label:
            lines.append(f"  \\label{{{label}}}")
        lines.append("\\end{figure}\n")
        return "\n".join(lines)

    def _table(self, tokens, caption: tuple[str, str | None] | None) -> str:
        rows: list[list[str]] = []
        aligns: list[str] = []
        header_rows = 0
        in_head = False
        for position, token in enumerate(tokens):
            if token.type == "thead_open":
                in_head = True
            elif token.type == "thead_close":
                in_head = False
            elif token.type == "tr_open":
                rows.append([])
                header_rows += 1 if in_head else 0
            elif token.type in {"th_open", "td_open"}:
                style = token.attrGet("style") or ""
                if len(rows) == 1:
                    aligns.append("r" if "right" in style else "c" if "center" in style else "l")
                rows[-1].append(self._inline(tokens[position + 1].children or []))
        columns = max(len(row) for row in rows)
        aligns = (aligns + ["l"] * columns)[:columns]
        body = ["\\begin{table}[htbp]", "  \\centering"]
        if caption:
            body.append(f"  \\caption{{{self._inline_text(caption[0])}}}")
            if caption[1]:
                body.append(f"  \\label{{{caption[1]}}}")
        body += [f"  \\begin{{tabular}}{{{''.join(aligns)}}}", "    \\toprule"]
        for number, row in enumerate(rows):
            cells = row + [""] * (columns - len(row))
            if number < header_rows:
                cells = [f"\\textbf{{{cell}}}" if cell else cell for cell in cells]
            body.append("    " + " & ".join(cells) + r" \\")
            if number == header_rows - 1:
                body.append("    \\midrule")
        body += ["    \\bottomrule", "  \\end{tabular}", "\\end{table}\n"]
        return "\n".join(body)

    # -- inline ------------------------------------------------------------------------------

    def _inline_text(self, text: str) -> str:
        from markdown_it import MarkdownIt

        parser = MarkdownIt("commonmark")
        parser.enable(["strikethrough"])
        tokens = parser.parseInline(text)
        return self._inline(tokens[0].children or []) if tokens else escape(text)

    def _inline(self, children) -> str:
        out: list[str] = []
        link: str | None = None
        for child in children:
            kind = child.type
            if kind == "text":
                out.append(_escape_keeping_slots(child.content))
            elif kind == "strong_open":
                out.append(r"\textbf{")
            elif kind == "em_open":
                out.append(r"\emph{")
            elif kind == "s_open":
                out.append(r"\sout{")
                self.notes.append("Strikethrough needs \\usepackage[normalem]{ulem}.")
            elif kind in {"strong_close", "em_close", "s_close"}:
                out.append("}")
            elif kind == "code_inline":
                out.append(r"\texttt{" + escape(child.content) + "}")
            elif kind == "link_open":
                link = child.attrGet("href")
                out.append(r"\href{" + (link or "").replace("%", r"\%").replace("#", r"\#") + "}{")
            elif kind == "link_close":
                out.append("}")
                link = None
            elif kind == "softbreak":
                out.append("\n")
            elif kind == "hardbreak":
                out.append("\\\\\n")
            elif kind == "image":
                out.append(self._figure(child, ""))
            elif kind == "html_inline" and child.content.lower().startswith("<br"):
                out.append("\\\\\n")
        return "".join(out)


def _escape_keeping_slots(text: str) -> str:
    pieces = re.split(r"(XLATEXSLOT\d+X)", text)
    return "".join(piece if piece.startswith("XLATEXSLOT") else escape(piece) for piece in pieces)


def keep_cite(match: re.Match, keep: Callable, converter: Converter) -> str:
    keys = [key.strip().lstrip("@") for key in re.split(r"[;,]", match.group(1)) if key.strip()]
    converter.citations.update(keys)
    fake = re.match(r".*", f"\\{converter.cite_command}{{{','.join(keys)}}}")
    return keep(fake)


REF_WORDS = {
    "english": {
        "fig": "Figure",
        "tab": "Table",
        "sec": "Section",
        "eq": "Equation",
        "lst": "Listing",
    },
    "portuguese": {
        "fig": "Figura",
        "tab": "Tabela",
        "sec": "Seção",
        "eq": "Equação",
        "lst": "Listagem",
    },
}


def keep_ref(match: re.Match, keep: Callable, language: str = "english") -> str:
    kind, name = match.group(1), match.group(2)
    command = "eqref" if kind == "eq" else "ref"
    word = REF_WORDS[
        "portuguese" if language.lower().startswith(("pt", "port", "brazil")) else "english"
    ][kind]
    # A tie keeps "Figure" and its number on one line.
    fake = re.match(r".*", f"{word}~\\{command}{{{kind}:{name}}}")
    return keep(fake)


def _split_label(text: str) -> tuple[str, str | None]:
    match = re.search(r"\s*\{#((?:sec|fig|tab|chap):[\w\-]+)\}\s*$", text)
    if match:
        return text[: match.start()], match.group(1)
    return text, None


def _image_markup(content: str) -> str:
    match = re.search(r"!\[[^\]]*\]\([^)]*\)(\{[^}]*\})?", content)
    return match.group(0) if match else ""


def sanitize_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "item"


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()
