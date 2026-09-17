"""A .docx as Markdown: headings, lists, tables, emphasis, links and extracted images."""

from __future__ import annotations

from pathlib import Path

from docx.document import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from code_ai.tools.documents.inspect import heading_level
from code_ai.tools.documents.ooxml import list_info

_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_MONO = ("consolas", "courier", "mono", "menlo")


def document_to_markdown(document: Document, media_dir: Path, media_prefix: str) -> tuple[str, int]:
    lines: list[str] = []
    images = 0
    counters: dict[int, int] = {}
    in_code = False
    # A Title paragraph takes '#', so Heading 1 becomes '##'.
    shift = (
        1
        if any(p.style is not None and p.style.name == "Title" for p in document.paragraphs)
        else 0
    )
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            if in_code:
                lines.append("```")
                in_code = False
            lines.extend(["", *_table(item), ""])
            continue
        paragraph: Paragraph = item
        style = paragraph.style.name if paragraph.style is not None else ""
        if style == "Code Block" or style.lower().startswith(("html preformatted", "macro")):
            if not in_code:
                lines.append("```")
                in_code = True
            lines.append(paragraph.text)
            continue
        if in_code:
            lines.append("```")
            lines.append("")
            in_code = False
        text, found = _runs(paragraph, media_dir, media_prefix, images)
        images += found
        level = heading_level(paragraph)
        info = list_info(paragraph)
        if level is not None and text.strip():
            depth = 1 if level == 0 else min(6, level + shift)
            lines.extend([f"{'#' * depth} {text.strip()}", ""])
            counters.clear()
        elif info is not None:
            kind, depth = info
            indent = "   " * depth
            if kind == "number":
                counters[depth] = counters.get(depth, 0) + 1
                for deeper in [key for key in counters if key > depth]:
                    del counters[deeper]
                lines.append(f"{indent}{counters[depth]}. {text.strip()}")
            else:
                lines.append(f"{indent}- {text.strip()}")
        elif style in {"Quote", "Intense Quote"}:
            lines.extend([f"> {text.strip()}", ""])
        elif _has_page_break(paragraph) and not text.strip():
            lines.extend(["\\newpage", ""])
        elif text.strip():
            if lines and lines[-1] and _is_list_line(lines[-1]):
                lines.append("")
            lines.extend([text.strip(), ""])
            counters.clear()
    if in_code:
        lines.append("```")
    markdown = "\n".join(lines)
    while "\n\n\n" in markdown:
        markdown = markdown.replace("\n\n\n", "\n\n")
    return markdown.strip() + "\n", images


def _is_list_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("- ") or stripped[:1].isdigit()


def _has_page_break(paragraph: Paragraph) -> bool:
    return any(br.get(qn("w:type")) == "page" for br in paragraph._p.iter(qn("w:br")))


def _runs(paragraph: Paragraph, media_dir: Path, prefix: str, image_offset: int) -> tuple[str, int]:
    pieces: list[str] = []
    images = 0
    for child in paragraph._p:
        if child.tag == qn("w:r"):
            pieces.append(_run(child, paragraph))
            for blip in child.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip"):
                rel_id = blip.get(_EMBED)
                part = paragraph.part.related_parts.get(rel_id) if rel_id else None
                if part is None:
                    continue
                images += 1
                extension = Path(part.partname).suffix or ".png"
                name = f"image{image_offset + images}{extension}"
                media_dir.mkdir(parents=True, exist_ok=True)
                (media_dir / name).write_bytes(part.blob)
                alt = ""
                doc_pr = child.find(f".//{_WP}docPr")
                if doc_pr is not None:
                    alt = doc_pr.get("descr") or ""
                pieces.append(f"![{alt}]({prefix}/{name})")
        elif child.tag == qn("w:hyperlink"):
            text = "".join(_run(r, paragraph) for r in child.findall(qn("w:r")))
            rel_id = child.get(_R_ID)
            target = paragraph.part.rels[rel_id].target_ref if rel_id in paragraph.part.rels else ""
            anchor = child.get(qn("w:anchor"))
            url = target or (f"#{anchor}" if anchor else "")
            pieces.append(f"[{text}]({url})" if url else text)
    return "".join(pieces), images


def _run(r_element, paragraph: Paragraph) -> str:
    text = ""
    for node in r_element:
        if node.tag == qn("w:t"):
            text += node.text or ""
        elif node.tag == qn("w:tab"):
            text += "\t"
        elif node.tag == qn("w:br") and node.get(qn("w:type")) != "page":
            text += "  \n"
    if not text.strip():
        return text
    r_pr = r_element.find(qn("w:rPr"))
    bold = italic = code = strike = False
    if r_pr is not None:
        bold = _on(r_pr, "w:b")
        italic = _on(r_pr, "w:i")
        strike = _on(r_pr, "w:strike")
        fonts = r_pr.find(qn("w:rFonts"))
        font = (fonts.get(qn("w:ascii")) or "") if fonts is not None else ""
        style = r_pr.find(qn("w:rStyle"))
        style_name = (style.get(qn("w:val")) or "") if style is not None else ""
        code = any(m in font.lower() for m in _MONO) or "code" in style_name.lower()
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    core = text.strip()
    if code:
        core = f"`{core}`"
    else:
        core = core.replace("*", "\\*").replace("_", "\\_")
        if strike:
            core = f"~~{core}~~"
        if italic:
            core = f"*{core}*"
        if bold:
            core = f"**{core}**"
    return f"{lead}{core}{trail}"


def _on(r_pr, tag: str) -> bool:
    element = r_pr.find(qn(tag))
    return element is not None and element.get(qn("w:val")) not in {"0", "false", "off"}


def _table(table: Table) -> list[str]:
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            text = " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
            cells.append(text.replace("|", "\\|"))
        rows.append(cells)
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return out
