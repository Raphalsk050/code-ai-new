from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.locations import LOCATION_SCHEMA, for_context
from code_ai.tools.office.common import (
    attach_images,
    clamp_float,
    clamp_int,
    display_path,
    optional_str,
    parse_page_spec,
    require_str,
    resolve_input,
    resolve_output,
)
from code_ai.tools.pdf.common import parse_json_argument
from code_ai.tools.schema import tool_schema
from code_ai.tools.slides import deck as decks
from code_ai.tools.slides import formatting, operations, spec
from code_ai.tools.slides.inspect import inspect_deck
from code_ai.tools.slides.themes import THEMES, get_theme

_PPTX = (".pptx", ".potx", ".pptm")
_THEME_MARK = "code-ai-theme:"

_THEME_FIELD = {
    "type": "string",
    "description": (
        "Visual theme: corporate (white, navy, blue cover), minimal (black on white, light "
        "titles), dark (slate background, cyan), vibrant (purple, pink, orange), academic "
        "(ivory, maroon, Georgia titles), ocean (teal). Default corporate, or the theme the "
        "deck was created with."
    ),
}
_ACCENT_FIELD = {
    "type": "string",
    "description": "Brand colour replacing the theme accent, e.g. '#E30613'.",
}
_FONT_FIELD = {"type": "string", "description": "Font family replacing the theme's fonts."}
_RENDER_FIELD = {
    "type": "string",
    "description": (
        "Slides to render as images to check the result, e.g. '1-3' or 'all' (at most 12). "
        "Needs LibreOffice or PowerPoint; see install_converter."
    ),
}
_INSTALL_FIELD = {
    "type": "boolean",
    "description": (
        "When nothing can render the deck: install LibreOffice for this user (~350 MB "
        "download, no admin rights)."
    ),
}

_SLIDES_DOC = (
    "Markdown (slides separated by a line with ---) or a JSON array of slide objects. "
    "Markdown per slide: '# Title', '## Subtitle', bullets ('-', indent 2 spaces to nest), "
    "**bold**/*italic*/`code`, one image '![caption](path)', a table '| a | b |', a quote "
    "'> text' plus '> — Author', a code fence, a chart as a fenced block ```chart with JSON "
    '{"type":"column|bar|stacked_column|stacked_bar|line|pie|doughnut|area|scatter",'
    '"categories":[...],"series":{"Name":[...]}}, a \'|||\' line splitting two columns '
    "(each may start with '### Heading'), 'Note:' then speaker notes, and directives like "
    "'<!-- kind: stats -->' or '<!-- number: 01 -->'. Kinds (inferred when omitted): title, "
    "section, bullets, two_column, comparison, image, image_text, table, chart, quote, stats "
    "(bullets '**42%** label'), timeline (bullets '**Step** text'), cards (bullets '**Title**: "
    "text'), agenda, code, closing. JSON uses the same keys: kind, title, subtitle, body "
    "(list, nested lists indent), left/right {heading, body, image}, image, caption, "
    "image_side, table (rows), chart, quote, author, stats [[value,label]], steps "
    "[[title,text]], cards [[title,text]], code, notes."
)


def _theme_name(deck) -> str | None:
    category = deck.core_properties.category or ""
    return category[len(_THEME_MARK) :] if category.startswith(_THEME_MARK) else None


def _resolve_theme(arguments: dict[str, Any], deck=None):
    name = optional_str(arguments, "theme") or (_theme_name(deck) if deck is not None else None)
    if name and name not in THEMES:
        name = None if deck is not None and not optional_str(arguments, "theme") else name
    return get_theme(
        name or "corporate",
        accent=optional_str(arguments, "accent") or None,
        font=optional_str(arguments, "font") or None,
    )


def _images(context: ToolContext, location, base: Path):
    tree = for_context(context, location)

    def resolve(src: str) -> Path:
        candidate = Path(src) if Path(src).is_absolute() else base / src
        return tree.resolve(candidate, must_exist=True)

    return resolve


async def render_deck(
    context: ToolContext,
    path: Path,
    selection: Any,
    arguments: dict[str, Any],
    payload: dict[str, Any],
    *,
    export: Path | None = None,
    max_side: int = 1280,
) -> dict[str, Any]:
    from code_ai.tools.office.converters import convert
    from code_ai.tools.office.render import page_count, render_pages

    with tempfile.TemporaryDirectory(prefix="code-ai-slides-") as scratch:
        pdf = Path(scratch, path.stem + ".pdf")
        try:
            result = await convert(
                path,
                "pdf",
                pdf,
                verify_ssl=bool(context.config.ssl_verification),
                install=bool(arguments.get("install_converter")),
            )
        except ToolExecutionError as exc:
            if export is not None:
                raise
            payload["render_note"] = f"Saved, but not rendered: {exc}"
            return payload
        payload["rendered_with"] = result.backend
        if export is not None:
            export.write_bytes(pdf.read_bytes())
            payload["pdf"] = display_path(context, export)
        if not selection:
            return payload
        total = page_count(pdf)
        try:
            pages = parse_page_spec(selection, total)
        except ToolArgumentError:
            pages = list(range(min(total, 3)))
        payload["rendered_slides"] = [page + 1 for page in pages[:12]]
        images = await asyncio.to_thread(render_pages, pdf, pages, max_side=max_side)
    return attach_images(payload, images)


class SlidesCreateTool:
    name = "slides_create"
    description = (
        "Create a professionally designed PowerPoint deck (.pptx) from Markdown or JSON. Each "
        "slide kind has its own layout on a consistent grid - cover, section divider, bullets, "
        "two columns, comparison cards, full-bleed image, image beside text, styled table, "
        "native editable charts, quote, big-number KPIs, timeline, feature cards, agenda, code "
        "panel, closing - in a theme (colours, fonts, accent) with slide numbers, footer and "
        "speaker notes. Text is measured and sized to fit its box; the result lists slides that "
        "still need shortening. Optionally start from a company template's masters. Use "
        "render_slides to look at the result before handing it over."
    )
    capabilities = frozenset(
        {ToolCapability.LOCAL_READ, ToolCapability.LOCAL_WRITE, ToolCapability.PROCESS}
    )
    input_schema = tool_schema(
        {
            "output": {"type": "string", "description": "Path of the .pptx to write."},
            "content": {"type": "string", "description": _SLIDES_DOC},
            "source": {
                "type": "string",
                "description": "A Markdown or JSON file with the slides, instead of content.",
            },
            "location": LOCATION_SCHEMA,
            "theme": _THEME_FIELD,
            "accent": _ACCENT_FIELD,
            "font": _FONT_FIELD,
            "aspect": {"type": "string", "description": "16:9 (default), 4:3 or 16:10."},
            "footer": {"type": "string", "description": "Footer text on content slides."},
            "slide_numbers": {
                "type": "boolean",
                "description": "Number content slides (default true).",
            },
            "template": {
                "type": "string",
                "description": "A .pptx/.potx whose masters to reuse; its slides are dropped.",
            },
            "title": {"type": "string", "description": "Presentation title property."},
            "author": {"type": "string", "description": "Author property."},
            "overwrite": {
                "type": "boolean",
                "description": "Replace output if it exists (default true).",
            },
            "render_slides": _RENDER_FIELD,
            "install_converter": _INSTALL_FIELD,
        },
        required=("output",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        location = arguments.get("location")
        output = resolve_output(
            context,
            require_str(arguments, "output", self.name),
            location=location,
            overwrite=arguments.get("overwrite") is not False,
            suffixes=(".pptx",),
        )
        content = arguments.get("content")
        base = output.parent
        if not (isinstance(content, str) and content.strip()) and not isinstance(
            content, list | dict
        ):
            source = optional_str(arguments, "source")
            if not source:
                raise ToolArgumentError("slides_create needs 'content' or 'source'.")
            path = resolve_input(context, source, location=location)
            content, base = path.read_text(encoding="utf-8", errors="replace"), path.parent
        slides = spec.parse(content)
        theme = _resolve_theme(arguments)
        template = optional_str(arguments, "template")
        template_path = (
            resolve_input(context, template, location=location, suffixes=_PPTX)
            if template
            else None
        )
        numbers = arguments.get("slide_numbers") is not False

        def build():
            deck = decks.new_deck(optional_str(arguments, "aspect", "16:9"), template_path)
            warnings: list[str] = []
            for number, slide in enumerate(slides, start=1):
                warnings += decks.add_slide(
                    deck,
                    slide,
                    theme,
                    resolve_image=_images(context, location, base),
                    number=number,
                    footer=optional_str(arguments, "footer"),
                    show_number=numbers,
                )
            core = deck.core_properties
            core.title = optional_str(arguments, "title") or next(
                (s.get("title", "") for s in slides), ""
            )
            core.author = optional_str(arguments, "author")
            core.last_modified_by = optional_str(arguments, "author")
            core.category = f"{_THEME_MARK}{theme.name}"
            decks.save_deck(deck, output)
            return warnings

        warnings = await asyncio.to_thread(build)
        payload: dict[str, Any] = {
            "path": display_path(context, output),
            "slides": len(slides),
            "kinds": [s["kind"] for s in slides],
            "theme": theme.name,
        }
        if warnings:
            payload["warnings"] = warnings
        if arguments.get("render_slides"):
            return await render_deck(
                context, output, arguments["render_slides"], arguments, payload
            )
        return payload


class SlidesInspectTool:
    name = "slides_inspect"
    description = (
        "Read a PowerPoint deck's slides and shapes and report its design problems. Covers slide "
        "size, fonts and layouts in use, and per slide its title, "
        "hidden state, speaker notes and every shape with id, name, kind, position and size in "
        "inches, text, font sizes, table header, chart type and data, picture alt text - the "
        "ids and names slides_edit uses. Reports design problems: text overflowing its box, "
        "text under 12pt, low-contrast colours, wordy or crowded slides, shapes off the slide, "
        "overlapping text, missing titles, pictures without alt text, empty placeholders, "
        "titles that move or change style between slides. Optionally renders slides as images."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ, ToolCapability.PROCESS})
    input_schema = tool_schema(
        {
            "path": {"type": "string", "description": "The .pptx to inspect."},
            "location": LOCATION_SCHEMA,
            "slides": {
                "type": "string",
                "description": "Slides to detail, e.g. '1-5' (default all).",
            },
            "text_chars": {
                "type": "integer",
                "description": "Characters of text shown per shape (default 200).",
            },
            "render_slides": _RENDER_FIELD,
            "install_converter": _INSTALL_FIELD,
        },
        required=("path",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_input(
            context,
            require_str(arguments, "path", self.name),
            location=arguments.get("location"),
            suffixes=_PPTX,
        )

        def work():
            deck = decks.open_deck(path)
            selection = parse_page_spec(arguments.get("slides"), len(deck.slides))
            return inspect_deck(
                deck,
                slides=selection,
                text_chars=clamp_int(arguments.get("text_chars"), default=200, low=20, high=4000),
            )

        report = await asyncio.to_thread(work)
        payload = {"path": display_path(context, path), **report}
        if arguments.get("render_slides"):
            return await render_deck(context, path, arguments["render_slides"], arguments, payload)
        return payload


_EDIT_DOC = (
    "JSON array of operations, applied in order and saved only if all succeed. Slides are "
    "1-based ('2', '3-5', 'last'); shapes are an id or name from slides_inspect; positions and "
    "sizes are inches. Ops: add {slide: Markdown or object as in slides_create, at?} (drawn in "
    "the deck's theme) | duplicate {slide, at?} | delete {slides} | move {slide, to} | hide "
    "{slides} | unhide {slides} | replace_text {find, replace, slides?, regex?, match_case?, "
    "include_notes?} (keeps formatting, also in tables) | set_text {slide, shape, text (lines "
    "and '-' bullets), size?} (keeps the shape's formatting) | add_text {slide, text, x, y, "
    "width, height, size?, color?, bold?, align?, bullets?} | add_image {slide, image, x, y, "
    "width, height, cover?, alt?} | move_shape {slide, shape, x?, y?, width?, height?} | "
    "delete_shape {slide, shape} | alt_text {slide, shape, text} | notes {slide, text} | "
    "background {slides, color | image} | table_cell {slide, shape?, row, column, text} | "
    'update_chart {slide, shape?, categories, series: {"Name": [values]}}. '
    'Example: [{"op":"replace_text","find":"2025","replace":"2026"},'
    '{"op":"add","at":3,"slide":"# Riscos\\n- Prazo\\n- Custo"}]'
)


class SlidesEditTool:
    name = "slides_edit"
    description = (
        "Edit an existing PowerPoint deck (.pptx) with a list of operations. Saved "
        "atomically: add new designed "
        "slides from Markdown in the deck's theme, duplicate (with pictures and charts), delete, "
        "reorder and hide slides; replace text across the deck keeping formatting; rewrite a "
        "shape's text; add text boxes and pictures; move, resize or delete shapes; alt text, "
        "speaker notes, backgrounds; edit table cells and chart data. Run slides_inspect first "
        "for shape ids, and render_slides to check the result."
    )
    capabilities = frozenset(
        {ToolCapability.LOCAL_READ, ToolCapability.LOCAL_WRITE, ToolCapability.PROCESS}
    )
    input_schema = tool_schema(
        {
            "path": {"type": "string", "description": "The .pptx to edit."},
            "location": LOCATION_SCHEMA,
            "operations": {"type": "string", "description": _EDIT_DOC},
            "output": {"type": "string", "description": "Write here instead of in place."},
            "theme": _THEME_FIELD,
            "accent": _ACCENT_FIELD,
            "footer": {"type": "string", "description": "Footer text for slides added here."},
            "render_slides": _RENDER_FIELD,
            "install_converter": _INSTALL_FIELD,
        },
        required=("path", "operations"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        location = arguments.get("location")
        source = resolve_input(
            context, require_str(arguments, "path", self.name), location=location, suffixes=_PPTX
        )
        specs = operations.normalize(parse_json_argument(arguments.get("operations"), "operations"))
        output_raw = optional_str(arguments, "output")
        target = (
            resolve_output(context, output_raw, location=location, suffixes=(".pptx",))
            if output_raw
            else source
        )

        def work():
            deck = decks.open_deck(source)
            state = operations.DeckState(
                deck=deck,
                theme=_resolve_theme(arguments, deck),
                resolve_image=_images(context, location, source.parent),
                footer=optional_str(arguments, "footer"),
            )
            done = operations.apply(state, specs)
            decks.save_deck(deck, target)
            return done, state.warnings, len(deck.slides)

        done, warnings, count = await asyncio.to_thread(work)
        payload: dict[str, Any] = {
            "path": display_path(context, target),
            "applied": done,
            "slides": count,
        }
        if warnings:
            payload["warnings"] = warnings
        if arguments.get("render_slides"):
            return await render_deck(
                context, target, arguments["render_slides"], arguments, payload
            )
        return payload


class SlidesFormatTool:
    name = "slides_format"
    description = (
        "Reformat a PowerPoint deck (.pptx) into a consistent, readable design. It "
        "applies a theme's fonts to "
        "titles, body and code; align titles that drift to the position most slides use; raise "
        "text below a minimum size; shrink text that overflows its box; recolour low-contrast "
        "text; remove empty placeholders; give pictures alt text; optionally set every "
        "background to the theme and add slide numbers. Reports what changed by count. Works on "
        "decks from anywhere, not only ones Code-AI created."
    )
    capabilities = frozenset(
        {ToolCapability.LOCAL_READ, ToolCapability.LOCAL_WRITE, ToolCapability.PROCESS}
    )
    input_schema = tool_schema(
        {
            "path": {"type": "string", "description": "The .pptx to format."},
            "location": LOCATION_SCHEMA,
            "output": {"type": "string", "description": "Write here instead of in place."},
            "theme": _THEME_FIELD,
            "accent": _ACCENT_FIELD,
            "font": _FONT_FIELD,
            "apply_fonts": {
                "type": "boolean",
                "description": "Set theme fonts on all text (default true).",
            },
            "apply_background": {
                "type": "boolean",
                "description": "Paint every background in the theme colour (default false).",
            },
            "fix_contrast": {
                "type": "boolean",
                "description": "Recolour hard-to-read text (default true).",
            },
            "min_font_size": {
                "type": "number",
                "description": "Smallest allowed text size in pt (default 12).",
            },
            "fix_overflow": {
                "type": "boolean",
                "description": "Shrink overflowing text (default true).",
            },
            "unify_titles": {
                "type": "boolean",
                "description": "Align titles to the common position (default true).",
            },
            "remove_empty_placeholders": {"type": "boolean", "description": "Default true."},
            "fill_alt_text": {"type": "boolean", "description": "Default true."},
            "slide_numbers": {
                "type": "boolean",
                "description": "Add missing slide numbers (default false).",
            },
            "render_slides": _RENDER_FIELD,
            "install_converter": _INSTALL_FIELD,
        },
        required=("path",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        location = arguments.get("location")
        source = resolve_input(
            context, require_str(arguments, "path", self.name), location=location, suffixes=_PPTX
        )
        output_raw = optional_str(arguments, "output")
        target = (
            resolve_output(context, output_raw, location=location, suffixes=(".pptx",))
            if output_raw
            else source
        )

        def flag(name: str, default: bool) -> bool:
            value = arguments.get(name)
            return default if value is None else bool(value)

        options = formatting.SlideFormatOptions(
            apply_fonts=flag("apply_fonts", True),
            apply_background=flag("apply_background", False),
            fix_contrast=flag("fix_contrast", True),
            min_font_size=clamp_float(arguments.get("min_font_size"), default=12, low=6, high=40),
            fix_overflow=flag("fix_overflow", True),
            unify_titles=flag("unify_titles", True),
            remove_empty_placeholders=flag("remove_empty_placeholders", True),
            fill_alt_text=flag("fill_alt_text", True),
            slide_numbers=flag("slide_numbers", False),
        )

        def work():
            deck = decks.open_deck(source)
            theme = _resolve_theme(arguments, deck)
            changes = formatting.format_deck(deck, theme, options)
            decks.save_deck(deck, target)
            return theme.name, changes

        theme_name, changes = await asyncio.to_thread(work)
        payload: dict[str, Any] = {
            "path": display_path(context, target),
            "theme": theme_name,
            "changes": changes,
        }
        if arguments.get("render_slides"):
            return await render_deck(
                context, target, arguments["render_slides"], arguments, payload
            )
        return payload


class SlidesRenderTool:
    name = "slides_render"
    description = (
        "Render PowerPoint slides as images to look at them, or export the deck to PDF. Renders "
        "a .pptx (or .ppt/.odp) through LibreOffice or PowerPoint and "
        "return the chosen slides as images, and optionally export the whole deck to PDF. Use "
        "it after creating or editing a deck to check layout, overflow and contrast with your "
        "own eyes."
    )
    capabilities = frozenset(
        {
            ToolCapability.LOCAL_READ,
            ToolCapability.LOCAL_WRITE,
            ToolCapability.PROCESS,
            ToolCapability.WEB,
        }
    )
    input_schema = tool_schema(
        {
            "path": {"type": "string", "description": "The deck to render."},
            "location": LOCATION_SCHEMA,
            "slides": {
                "type": "string",
                "description": "Slides to show, e.g. '1-4' (default the first 6).",
            },
            "export_pdf": {"type": "string", "description": "Also save the deck as this PDF."},
            "max_side": {
                "type": "integer",
                "description": "Longest image side in pixels (default 1280).",
            },
            "install_converter": _INSTALL_FIELD,
        },
        required=("path",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        location = arguments.get("location")
        path = resolve_input(
            context,
            require_str(arguments, "path", self.name),
            location=location,
            suffixes=(".pptx", ".ppt", ".odp", ".pptm", ".potx"),
        )
        export_raw = optional_str(arguments, "export_pdf")
        export = (
            resolve_output(context, export_raw, location=location, suffixes=(".pdf",))
            if export_raw
            else None
        )
        selection = arguments.get("slides") or "1-6"
        payload: dict[str, Any] = {"path": display_path(context, path)}
        max_side = clamp_int(arguments.get("max_side"), default=1280, low=320, high=2400)
        return await render_deck(
            context, path, selection, arguments, payload, export=export, max_side=max_side
        )
