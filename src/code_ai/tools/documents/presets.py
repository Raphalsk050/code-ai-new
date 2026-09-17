"""Named looks, applied through Word styles so the document stays editable by hand."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from docx.document import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.documents.ooxml import hex_color, shade

PAGE_SIZES_CM = {
    "a4": (21.0, 29.7),
    "letter": (21.59, 27.94),
    "legal": (21.59, 35.56),
    "a5": (14.8, 21.0),
    "a3": (29.7, 42.0),
}


@dataclass(frozen=True)
class Preset:
    name: str
    body_font: str
    heading_font: str
    body_size: float
    line_spacing: float
    space_after: float
    alignment: str = "left"
    first_line_indent_cm: float = 0.0
    heading_sizes: tuple[float, ...] = (20, 16, 13, 12, 11, 11)
    heading_color: str = "1F2937"
    heading_bold: bool = True
    heading_caps_level1: bool = False
    heading_space_before: float = 18
    heading_numbering: bool = False
    title_size: float = 28
    title_color: str = "111827"
    accent: str = "2563EB"
    caption_size: float = 9
    quote_indent_cm: float = 1.0
    quote_size: float | None = None
    quote_line_spacing: float | None = None
    code_font: str = "Consolas"
    table_header_fill: str = "F1F5F9"
    table_border: str = "CBD5E1"
    margins_cm: tuple[float, float, float, float] = (2.5, 2.5, 2.5, 2.5)
    page_size: str = "a4"
    page_number_position: str = "footer-center"
    extra: dict = field(default_factory=dict)


PRESETS: dict[str, Preset] = {
    "default": Preset(
        name="default",
        body_font="Calibri",
        heading_font="Calibri",
        body_size=11,
        line_spacing=1.15,
        space_after=8,
        heading_color="1F3864",
        accent="2F5496",
    ),
    "corporate": Preset(
        name="corporate",
        body_font="Arial",
        heading_font="Arial",
        body_size=10.5,
        line_spacing=1.2,
        space_after=8,
        heading_sizes=(18, 14, 12, 11, 10.5, 10.5),
        heading_color="0B3D91",
        title_color="0B3D91",
        accent="0B3D91",
        table_header_fill="E8EEF8",
        table_border="B7C4DA",
        margins_cm=(2.5, 2.2, 2.5, 2.2),
        page_number_position="footer-right",
    ),
    "academic": Preset(
        name="academic",
        body_font="Times New Roman",
        heading_font="Times New Roman",
        body_size=12,
        line_spacing=1.5,
        space_after=6,
        alignment="justify",
        first_line_indent_cm=1.25,
        heading_sizes=(14, 13, 12, 12, 12, 12),
        heading_color="000000",
        title_color="000000",
        title_size=18,
        accent="000000",
        heading_numbering=True,
        table_header_fill="F2F2F2",
        table_border="7F7F7F",
        margins_cm=(2.5, 2.5, 2.5, 2.5),
    ),
    # ABNT NBR 14724: A4, margins 3/2/2/3, 12pt, 1.5 spacing, long quotes 10pt
    # indented 4cm single-spaced, page number top right.
    "abnt": Preset(
        name="abnt",
        body_font="Arial",
        heading_font="Arial",
        body_size=12,
        line_spacing=1.5,
        space_after=0,
        alignment="justify",
        first_line_indent_cm=1.25,
        heading_sizes=(12, 12, 12, 12, 12, 12),
        heading_color="000000",
        heading_caps_level1=True,
        heading_space_before=12,
        heading_numbering=True,
        title_size=14,
        title_color="000000",
        accent="000000",
        caption_size=10,
        quote_indent_cm=4.0,
        quote_size=10,
        quote_line_spacing=1.0,
        table_header_fill="FFFFFF",
        table_border="000000",
        margins_cm=(3.0, 2.0, 2.0, 3.0),
        page_number_position="header-right",
    ),
    "report": Preset(
        name="report",
        body_font="Cambria",
        heading_font="Calibri",
        body_size=11,
        line_spacing=1.25,
        space_after=8,
        alignment="justify",
        heading_sizes=(22, 16, 13, 12, 11, 11),
        heading_color="17365D",
        title_size=32,
        title_color="17365D",
        accent="C0504D",
        heading_numbering=True,
        table_header_fill="DBE5F1",
        table_border="95B3D7",
    ),
    "minimal": Preset(
        name="minimal",
        body_font="Segoe UI",
        heading_font="Segoe UI",
        body_size=10.5,
        line_spacing=1.3,
        space_after=10,
        heading_sizes=(20, 15, 12.5, 11, 10.5, 10.5),
        heading_color="111111",
        heading_bold=False,
        title_color="111111",
        accent="111111",
        table_header_fill="FAFAFA",
        table_border="E5E5E5",
        margins_cm=(2.8, 2.8, 2.8, 2.8),
    ),
}

_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}

CODE_STYLE = "Code Block"
INLINE_CODE_STYLE = "Inline Code"
LONG_QUOTE_STYLE = "Quote"


def get_preset(name: str | None) -> Preset:
    key = (name or "default").strip().lower()
    if key not in PRESETS:
        raise ToolArgumentError(f"Unknown preset {name!r}. Choose from {sorted(PRESETS)}.")
    return PRESETS[key]


def with_overrides(preset: Preset, *, font: str | None = None, size: float | None = None) -> Preset:
    changes = {}
    if font:
        changes.update(body_font=font, heading_font=font)
    if size:
        changes["body_size"] = float(size)
    return replace(preset, **changes) if changes else preset


def _set_font(style, name: str) -> None:
    style.font.name = name
    r_pr = style.element.get_or_add_rPr()
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = r_pr.makeelement(qn("w:rFonts"), {})
        r_pr.append(fonts)
    for attribute in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attribute), name)
    for theme in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
        fonts.attrib.pop(qn(theme), None)


def _style(document: Document, name: str, kind=WD_STYLE_TYPE.PARAGRAPH, base: str = "Normal"):
    try:
        return document.styles[name]
    except KeyError:
        style = document.styles.add_style(name, kind)
        if kind == WD_STYLE_TYPE.PARAGRAPH:
            style.base_style = document.styles[base]
        return style


def apply_preset(document: Document, preset: Preset) -> None:
    normal = document.styles["Normal"]
    _set_font(normal, preset.body_font)
    normal.font.size = Pt(preset.body_size)
    normal.font.color.rgb = hex_color("000000" if preset.name in {"abnt", "academic"} else "1F2328")
    fmt = normal.paragraph_format
    fmt.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    fmt.line_spacing = preset.line_spacing
    fmt.space_after = Pt(preset.space_after)
    fmt.space_before = Pt(0)
    fmt.alignment = _ALIGN[preset.alignment]
    fmt.first_line_indent = Cm(preset.first_line_indent_cm) if preset.first_line_indent_cm else None
    fmt.widow_control = True

    for level in range(1, 7):
        style = _style(document, f"Heading {level}")
        _set_font(style, preset.heading_font)
        style.font.size = Pt(preset.heading_sizes[min(level, len(preset.heading_sizes)) - 1])
        style.font.bold = preset.heading_bold or level >= 3
        style.font.italic = False
        style.font.color.rgb = hex_color(preset.heading_color)
        style.font.all_caps = preset.heading_caps_level1 and level == 1
        heading_fmt = style.paragraph_format
        heading_fmt.space_before = Pt(preset.heading_space_before if level <= 2 else 12)
        heading_fmt.space_after = Pt(6 if preset.space_after else 12)
        heading_fmt.keep_with_next = True
        heading_fmt.first_line_indent = Cm(0)
        heading_fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
        heading_fmt.line_spacing = 1.0 if preset.name != "abnt" else 1.5

    for name, size, color, align in (
        ("Title", preset.title_size, preset.title_color, "left"),
        ("Subtitle", preset.body_size + 3, "555555", "left"),
    ):
        style = _style(document, name)
        _set_font(style, preset.heading_font)
        style.font.size = Pt(size)
        style.font.color.rgb = hex_color(color)
        style.font.bold = name == "Title"
        style.paragraph_format.alignment = _ALIGN["center" if preset.name == "abnt" else align]
        style.paragraph_format.first_line_indent = Cm(0)
        style.paragraph_format.space_after = Pt(12)
        _clear_borders(style)

    # TOC Heading inherits Heading 1, numbering included; the contents title is never numbered.
    toc_heading = _style(document, "TOC Heading")
    _set_font(toc_heading, preset.heading_font)
    toc_heading.font.size = Pt(preset.heading_sizes[0])
    toc_heading.font.bold = True
    toc_heading.font.all_caps = preset.heading_caps_level1
    toc_heading.font.color.rgb = hex_color(preset.heading_color)
    toc_pr = toc_heading.element.get_or_add_pPr()
    for old in toc_pr.findall(qn("w:numPr")):
        toc_pr.remove(old)
    no_number = toc_pr.makeelement(qn("w:numPr"), {})
    no_number.append(no_number.makeelement(qn("w:numId"), {qn("w:val"): "0"}))
    toc_pr.insert(0, no_number)
    toc_heading.paragraph_format.first_line_indent = Cm(0)
    toc_heading.paragraph_format.space_after = Pt(12)

    caption = _style(document, "Caption")
    _set_font(caption, preset.body_font)
    caption.font.size = Pt(preset.caption_size)
    caption.font.italic = preset.name not in {"abnt"}
    caption.font.bold = False
    caption.font.color.rgb = hex_color("404040")
    caption.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption.paragraph_format.first_line_indent = Cm(0)
    caption.paragraph_format.space_after = Pt(10)
    caption.paragraph_format.line_spacing = 1.0

    quote = _style(document, LONG_QUOTE_STYLE)
    _set_font(quote, preset.body_font)
    quote.font.size = Pt(preset.quote_size or preset.body_size)
    quote.font.italic = preset.name not in {"abnt"}
    quote.font.color.rgb = hex_color("000000" if preset.name == "abnt" else "4B5563")
    quote_fmt = quote.paragraph_format
    quote_fmt.left_indent = Cm(preset.quote_indent_cm)
    quote_fmt.right_indent = Cm(0 if preset.name == "abnt" else preset.quote_indent_cm)
    quote_fmt.first_line_indent = Cm(0)
    quote_fmt.line_spacing = preset.quote_line_spacing or preset.line_spacing
    quote_fmt.space_after = Pt(12)
    quote_fmt.alignment = _ALIGN["justify" if preset.alignment == "justify" else "left"]

    code = _style(document, CODE_STYLE)
    _set_font(code, preset.code_font)
    code.font.size = Pt(max(8.0, preset.body_size - 1.5))
    code.font.color.rgb = hex_color("1F2328")
    code_fmt = code.paragraph_format
    code_fmt.line_spacing = 1.0
    code_fmt.space_after = Pt(0)
    code_fmt.space_before = Pt(0)
    code_fmt.first_line_indent = Cm(0)
    code_fmt.left_indent = Cm(0.3)
    code_fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
    code_fmt.keep_with_next = True
    shade(code.element.get_or_add_pPr(), "F3F4F6")

    inline = _style(document, INLINE_CODE_STYLE, WD_STYLE_TYPE.CHARACTER)
    _set_font(inline, preset.code_font)
    inline.font.size = Pt(max(8.0, preset.body_size - 1))
    inline.font.color.rgb = hex_color(
        "B42318" if preset.name not in {"abnt", "academic"} else "000000"
    )

    listing = _style(document, "List Paragraph")
    listing.paragraph_format.first_line_indent = Cm(0)
    listing.paragraph_format.space_after = Pt(max(2.0, preset.space_after / 2))
    listing.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT

    for header_style in ("Header", "Footer"):
        style = _style(document, header_style)
        _set_font(style, preset.body_font)
        style.font.size = Pt(max(8.0, preset.body_size - 2))
        style.font.color.rgb = hex_color("000000" if preset.name == "abnt" else "6B7280")
        style.paragraph_format.first_line_indent = Cm(0)


def _clear_borders(style) -> None:
    p_pr = style.element.pPr
    if p_pr is not None:
        for border in p_pr.findall(qn("w:pBdr")):
            p_pr.remove(border)


def apply_page_setup(
    document: Document,
    preset: Preset,
    *,
    page_size: str | None = None,
    orientation: str | None = None,
    margins: tuple[float, float, float, float] | None = None,
) -> None:
    size_key = (page_size or preset.page_size).strip().lower()
    if size_key not in PAGE_SIZES_CM:
        raise ToolArgumentError(f"Unknown page_size {page_size!r}. Use {sorted(PAGE_SIZES_CM)}.")
    width, height = PAGE_SIZES_CM[size_key]
    top, right, bottom, left = margins or preset.margins_cm
    for section in document.sections:
        landscape = (orientation or "").lower() == "landscape"
        section.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
        section.page_width = Cm(height if landscape else width)
        section.page_height = Cm(width if landscape else height)
        section.top_margin, section.right_margin = Cm(top), Cm(right)
        section.bottom_margin, section.left_margin = Cm(bottom), Cm(left)
        section.header_distance = Cm(1.25)
        section.footer_distance = Cm(1.25)


def parse_margins(value) -> tuple[float, float, float, float] | None:
    """'3cm 2cm 2cm 3cm' (top right bottom left), '2.5cm', or numbers in cm."""

    if value in (None, ""):
        return None
    parts = str(value).replace(",", " ").split()
    numbers = []
    for part in parts:
        text = part.lower()
        try:
            if text.endswith("mm"):
                numbers.append(float(text[:-2]) / 10)
            elif text.endswith("cm"):
                numbers.append(float(text[:-2]))
            elif text.endswith("in"):
                numbers.append(float(text[:-2]) * 2.54)
            else:
                numbers.append(float(text))
        except ValueError:
            raise ToolArgumentError(
                f"Unreadable margin {part!r}; use e.g. '3cm 2cm 2cm 3cm'."
            ) from None
    if len(numbers) == 1:
        numbers *= 4
    elif len(numbers) == 2:
        numbers = [numbers[0], numbers[1], numbers[0], numbers[1]]
    if len(numbers) != 4:
        raise ToolArgumentError("margins takes 1, 2 or 4 values (top right bottom left).")
    return tuple(numbers)  # type: ignore[return-value]


def table_text_width_cm(document: Document) -> float:
    section = document.sections[-1]
    return (section.page_width - section.left_margin - section.right_margin) / 360000


def ensure_styles(document: Document) -> None:
    """Create the styles the Markdown writer uses when a document lacks them.

    Files saved by Word keep most built-in styles latent, so "Caption" or
    "List Paragraph" may not exist yet. Existing styles are left untouched.
    """

    names = {style.name for style in document.styles}
    for name in ("Title", "Subtitle", "Quote", "Caption", "List Paragraph", "TOC Heading"):
        if name not in names:
            _style(document, name)
    for level in range(1, 7):
        if f"Heading {level}" not in names:
            style = _style(document, f"Heading {level}")
            style.font.bold = True
            style.font.size = Pt(max(11, 18 - 2 * level))
            style.paragraph_format.keep_with_next = True
            p_pr = style.element.get_or_add_pPr()
            outline = p_pr.makeelement(qn("w:outlineLvl"), {qn("w:val"): str(level - 1)})
            p_pr.append(outline)
    if CODE_STYLE not in names:
        code = _style(document, CODE_STYLE)
        _set_font(code, "Consolas")
        code.font.size = Pt(9.5)
        code.paragraph_format.space_after = Pt(0)
        code.paragraph_format.line_spacing = 1.0
        code.paragraph_format.first_line_indent = Cm(0)
        shade(code.element.get_or_add_pPr(), "F3F4F6")
    if INLINE_CODE_STYLE not in names:
        inline = _style(document, INLINE_CODE_STYLE, WD_STYLE_TYPE.CHARACTER)
        _set_font(inline, "Consolas")
