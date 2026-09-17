from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.locations import LOCATION_SCHEMA, for_context
from code_ai.tools.office.common import (
    attach_images,
    clamp_int,
    display_path,
    optional_str,
    parse_page_spec,
    require_str,
    resolve_input,
    resolve_output,
)
from code_ai.tools.office.deps import LazyModule, ensure
from code_ai.tools.office.render import render_pages
from code_ai.tools.pdf import convert as conversions
from code_ai.tools.pdf.common import parse_json_argument, to_points
from code_ai.tools.schema import tool_schema

# Loaded when a tool runs: a missing library must not stop Code-AI from starting.
pypdf = LazyModule("pypdf")
operations = LazyModule("code_ai.tools.pdf.operations")
pdf_inspect = LazyModule("code_ai.tools.pdf.inspect")
_DEPS = ("pypdf", "pypdfium2", "cryptography", "reportlab", "PIL", "markdown_it")

_PDF = (".pdf",)

_RENDER_FIELD = {
    "type": "string",
    "description": (
        "Pages to render as images so you can see the result, e.g. '1' or '1-3,last' "
        "(at most 12). Omit to skip rendering."
    ),
}

_PASSWORD_FIELD = {"type": "string", "description": "Password of an encrypted input PDF."}


def _preview(context: ToolContext, pdf: Path, spec: Any, payload: dict[str, Any], password=None):
    if not spec:
        return payload
    from code_ai.tools.office.render import page_count

    total = page_count(pdf)
    try:
        pages = parse_page_spec(spec, total)
    except ToolArgumentError:
        # The file is already written; a bad preview range should not read as a failure.
        pages = list(range(min(total, 3)))
        payload["render_note"] = f"render_pages {spec!r} is out of range; showed the first pages."
    payload["rendered_pages"] = [page + 1 for page in pages[:12]]
    return attach_images(payload, render_pages(pdf, pages, password=password))


def _atomic_target(target: Path) -> Path:
    return target.with_name(f".{target.stem}.{os.getpid()}.tmp{target.suffix}")


class PdfInspectTool:
    name = "pdf_inspect"
    description = (
        "Inspect a PDF: structure, form fields, text, search and page images. Covers page count "
        "and sizes, metadata, encryption, bookmarks, form "
        "fields (names, types, current values, options - use these names with pdf_edit "
        "fill_form), annotations, fonts, images, attachments. Optionally extracts the text of "
        "chosen pages, searches for a phrase (each hit with page, context and its rect in "
        "points from the top-left, ready for pdf_edit redact/link/stamp), and renders pages as "
        "images so you can see layout, scans and signatures. Read-only."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "path": {"type": "string", "description": "The PDF to inspect."},
            "location": LOCATION_SCHEMA,
            "password": _PASSWORD_FIELD,
            "text_pages": {
                "type": "string",
                "description": "Pages whose text to extract, e.g. '1-5' or 'all'. Omit for none.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Budget for extracted text across pages (default 20000).",
            },
            "search": {
                "type": "string",
                "description": "Case-insensitive phrase to find in every page.",
            },
            "render_pages": _RENDER_FIELD,
        },
        required=("path",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        await ensure(*_DEPS, verify_ssl=bool(context.config.ssl_verification))
        raw = require_str(arguments, "path", self.name)
        path = resolve_input(context, raw, location=arguments.get("location"), suffixes=_PDF)
        password = optional_str(arguments, "password") or None
        max_chars = clamp_int(arguments.get("max_chars"), default=20_000, low=500, high=200_000)

        def work() -> dict[str, Any]:
            report = pdf_inspect.inspect_pdf(
                path,
                password=password,
                text_pages=arguments.get("text_pages"),
                max_chars=max_chars,
                search=optional_str(arguments, "search") or None,
            )
            payload = {"path": display_path(context, path), **report}
            return _preview(context, path, arguments.get("render_pages"), payload, password)

        return await asyncio.to_thread(work)


_OPERATIONS_DOC = (
    "JSON array of operations applied in order; the file is only written if all succeed. "
    "Pages are 1-based specs like '1-3,5', 'last', '-2' or 'all' (default all). Positions "
    "and rects are in points (or '2cm', '10mm', '1in') from the TOP-LEFT of the page as "
    "shown; rect is [left, top, right, bottom]. Ops: "
    "append {file, pages?, password?} | insert {file, at (page number or 'end'), pages?} | "
    "keep {pages} (extract/reorder/duplicate: '3,1-2,2') | delete {pages} | "
    "rotate {degrees, pages?} | crop {margins (length or {top,right,bottom,left}) or box, "
    "pages?} | resize {size: 'a4'|'letter'|[w,h], pages?} (content scaled to fit) | "
    "blank {at, count?, size?} | split {every | ranges: ['1-3','4-'], output: "
    "'parts/part-{n}.pdf'} (writes extra files) | "
    "watermark {text | image, opacity? 0.15, angle? 45, font_size?, color?, font?, "
    "under? false, pages?} | "
    "stamp {text | image, position? 'top-right' (center, top, bottom-left...) or x+y, "
    "width?/height? for images, font_size?, color?, align?, opacity?, margin?, pages?} | "
    "header_footer {header?, footer?, align? center, font_size? 9, color?, margin? "
    "'1.2cm', start_number?, skip_first?, pages?} | page_numbers {format? '{page} / "
    "{total}', position? 'bottom-center'} - text may use {page} {total} {title} {date} | "
    "fill_form {fields: {name: value or true/false}, flatten?} | "
    "metadata {title?, author?, subject?, keywords?, creator?} | "
    "bookmarks {items: [{title, page, level?}], replace? true} | "
    "link {page, rect, url | to_page} | "
    "encrypt {user_password, owner_password?, allow_print? true, allow_copy? true, "
    "allow_modify? false, allow_annotate? true} (AES-256) | "
    "compress {image_quality? 1-95, max_image_side? px} | "
    "redact {areas?: [{page, rect}], text?: ['phrase'], pages?, dpi? 200, color?} "
    "(secure: affected pages become images) | remove_annotations {subtypes?: ['Link']} | "
    "remove_javascript {}. Fonts: helvetica, times, courier (+ -bold), or font_file "
    "(a .ttf); non-Latin text picks a Unicode font automatically. "
    'Example: [{"op":"delete","pages":"2"},{"op":"watermark","text":"CONFIDENCIAL"},'
    '{"op":"page_numbers","format":"Página {page} de {total}"}]'
)


class PdfEditTool:
    name = "pdf_edit"
    description = (
        "Edit a PDF with a list of operations (merge, split, watermark, forms, redact, encrypt). "
        "Applied atomically: merge and insert other "
        "PDFs, extract, delete, reorder, duplicate, rotate, crop and resize pages, split into "
        "files, text or image watermarks and stamps, headers/footers and page numbers, fill "
        "and flatten forms, metadata, bookmarks, links, AES-256 encryption, compression, "
        "secure redaction of areas or found text, and removal of annotations or JavaScript. "
        "Writes to output (default: overwrite the input in place, safely). Use pdf_inspect "
        "first for field names and text positions, and render_pages to check the result."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ, ToolCapability.LOCAL_WRITE})
    input_schema = tool_schema(
        {
            "path": {"type": "string", "description": "The PDF to edit."},
            "location": LOCATION_SCHEMA,
            "operations": {"type": "string", "description": _OPERATIONS_DOC},
            "output": {
                "type": "string",
                "description": "Where to write the result. Omit to replace the input.",
            },
            "password": _PASSWORD_FIELD,
            "render_pages": _RENDER_FIELD,
        },
        required=("path", "operations"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        await ensure(*_DEPS, verify_ssl=bool(context.config.ssl_verification))
        raw = require_str(arguments, "path", self.name)
        location = arguments.get("location")
        source = resolve_input(context, raw, location=location, suffixes=_PDF)
        specs = operations.normalize(parse_json_argument(arguments.get("operations"), "operations"))
        output_raw = optional_str(arguments, "output")
        target = (
            resolve_output(context, output_raw, location=location, suffixes=_PDF)
            if output_raw
            else source
        )
        password = optional_str(arguments, "password") or None
        tree = for_context(context, location)

        def resolve_file(value: str) -> Path:
            return tree.resolve(value, must_exist=True)

        def resolve_extra(value: str) -> Path:
            return resolve_output(context, value, location=location, suffixes=_PDF)

        def work() -> dict[str, Any]:
            reader = pdf_inspect.open_reader(source, password)
            writer = pypdf.PdfWriter(clone_from=reader)
            state = operations.EditState(
                writer=writer,
                resolve_file=resolve_file,
                resolve_output=resolve_extra,
                display=lambda p: display_path(context, p),
                title=str((reader.metadata or {}).get("/Title") or ""),
            )
            done = operations.apply(state, specs)
            staging = _atomic_target(target)
            try:
                operations.save(state, staging)
                os.replace(staging, target)
            finally:
                staging.unlink(missing_ok=True)
            payload: dict[str, Any] = {
                "path": display_path(context, target),
                "pages": state.total,
                "bytes": target.stat().st_size,
                "applied": done,
            }
            if state.written:
                payload["files_written"] = state.written
            if state.notes:
                payload["notes"] = state.notes
            preview_password = state.encryption["user_password"] if state.encryption else None
            render = arguments.get("render_pages")
            return _preview(context, target, render, payload, preview_password)

        return await asyncio.to_thread(work)


class PdfConvertTool:
    name = "pdf_convert"
    description = (
        "Create PDFs and turn PDFs into other things; the direction comes from the source and "
        "output extensions. Into PDF: Markdown (.md, or content with content_format "
        "'markdown' - tables, code, images relative to the file, '\\newpage' breaks) and HTML "
        "files, URLs or strings, printed by headless Chromium with page size, margins, "
        "custom CSS and header/footer text ({page} {total} {title} {date}); images "
        "(sources list, one page each, fitted to page_size or 'fit' to the image); office "
        "files (.docx, .pptx, .xlsx, .odt...) through LibreOffice or Microsoft Office. Out of "
        "PDF: page images (.png/.jpg; output with {page}, e.g. 'pages/p-{page}.png', at dpi) "
        "or plain text (.txt). For editing an existing PDF use pdf_edit."
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
            "output": {
                "type": "string",
                "description": (
                    "File to write: .pdf, .txt, or an image pattern with {page} (.png/.jpg)."
                ),
            },
            "source": {
                "type": "string",
                "description": "Input file or http(s) URL. Omit when using content or sources.",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Several image files, in page order, to combine into one PDF.",
            },
            "content": {
                "type": "string",
                "description": "Markdown or HTML text to print, instead of a source file.",
            },
            "content_format": {
                "type": "string",
                "description": "'markdown' (default) or 'html', for content.",
            },
            "location": LOCATION_SCHEMA,
            "page_size": {
                "type": "string",
                "description": "a4 (default), letter, legal, a3, a5, '210mmx297mm', or 'fit' "
                "for images.",
            },
            "landscape": {"type": "boolean", "description": "Landscape pages."},
            "margin": {
                "type": "string",
                "description": "Page margin: '2cm', or 'top right bottom left' like CSS.",
            },
            "header": {"type": "string", "description": "Header text on every page."},
            "footer": {
                "type": "string",
                "description": "Footer text on every page, e.g. 'Página {page} de {total}'.",
            },
            "css": {"type": "string", "description": "Extra CSS for Markdown/HTML sources."},
            "title": {"type": "string", "description": "Document title for Markdown output."},
            "pages": {
                "type": "string",
                "description": "Pages to export when the source is a PDF (default all).",
            },
            "dpi": {
                "type": "integer",
                "description": "Resolution for PDF to image (default 150) or images to PDF.",
            },
            "password": _PASSWORD_FIELD,
            "install_converter": {
                "type": "boolean",
                "description": (
                    "For office files when no LibreOffice or Microsoft Office is present: "
                    "install LibreOffice for this user (~350 MB download)."
                ),
            },
            "render_pages": _RENDER_FIELD,
        },
        required=("output",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        await ensure(*_DEPS, verify_ssl=bool(context.config.ssl_verification))
        location = arguments.get("location")
        output_raw = require_str(arguments, "output", self.name)
        out_suffix = Path(output_raw.replace("{page}", "1")).suffix.lower()
        verify_ssl = bool(context.config.ssl_verification)
        source_raw = optional_str(arguments, "source")
        sources = arguments.get("sources") or []
        content = arguments.get("content")
        payload: dict[str, Any]

        if source_raw.lower().endswith(".pdf") and not source_raw.startswith(("http:", "https:")):
            return await self._from_pdf(arguments, context, source_raw, output_raw, out_suffix)

        if out_suffix != ".pdf":
            raise ToolArgumentError(
                "Only PDF sources convert to images or text; everything else converts to .pdf."
            )
        output = resolve_output(context, output_raw, location=location, suffixes=_PDF)
        staging = _atomic_target(output)
        options = conversions.print_options(arguments)
        try:
            if isinstance(content, str) and content.strip():
                fmt = optional_str(arguments, "content_format", "markdown").lower()
                markup = (
                    content
                    if fmt == "html"
                    else conversions.markdown_to_html(
                        content,
                        title=options.title,
                        css=options.css,
                        base=for_context(context, location).root,
                    )
                )
                pages = await conversions.html_to_pdf(
                    staging, options=options, verify_ssl=verify_ssl, markup=markup
                )
                kind = fmt
            elif sources or Path(source_raw).suffix.lower() in conversions.IMAGE_SUFFIXES:
                files = [str(s) for s in sources] if sources else [source_raw]
                image_suffixes = tuple(conversions.IMAGE_SUFFIXES)
                images = [
                    resolve_input(context, item, location=location, suffixes=image_suffixes)
                    for item in files
                ]
                size = optional_str(arguments, "page_size", "a4").lower()
                margin = to_points(arguments.get("margin") or 0, "margin") if size != "fit" else 0.0
                dpi = clamp_int(arguments.get("dpi"), default=150, low=36, high=1200)
                pages = await asyncio.to_thread(
                    conversions.images_to_pdf, images, staging, size=size, margin=margin, dpi=dpi
                )
                kind = "images"
            elif source_raw.startswith(("http://", "https://")):
                pages = await conversions.html_to_pdf(
                    staging, options=options, verify_ssl=verify_ssl, url=source_raw
                )
                kind = "url"
            elif source_raw:
                source = resolve_input(context, source_raw, location=location)
                suffix = source.suffix.lower()
                if suffix in conversions.MARKDOWN_SUFFIXES:
                    markup = conversions.markdown_to_html(
                        source.read_text(encoding="utf-8", errors="replace"),
                        title=options.title,
                        css=options.css,
                        base=source.parent,
                    )
                    pages = await conversions.html_to_pdf(
                        staging, options=options, verify_ssl=verify_ssl, markup=markup
                    )
                    kind = "markdown"
                elif suffix in conversions.HTML_SUFFIXES:
                    pages = await conversions.html_to_pdf(
                        staging, options=options, verify_ssl=verify_ssl, url=source.as_uri()
                    )
                    kind = "html"
                elif suffix in conversions.OFFICE_SUFFIXES:
                    from code_ai.tools.office.converters import convert
                    from code_ai.tools.office.render import page_count

                    result = await convert(
                        source,
                        "pdf",
                        staging,
                        verify_ssl=verify_ssl,
                        install=bool(arguments.get("install_converter")),
                    )
                    pages = page_count(staging)
                    kind = f"office via {result.backend}"
                else:
                    raise ToolArgumentError(f"Don't know how to turn {suffix} into a PDF.")
            else:
                raise ToolArgumentError("Give source, sources or content.")
            os.replace(staging, output)
        finally:
            staging.unlink(missing_ok=True)

        payload = {
            "path": display_path(context, output),
            "from": kind,
            "pages": pages,
            "bytes": output.stat().st_size,
        }
        if options.notes:
            payload["notes"] = options.notes
        return await asyncio.to_thread(
            _preview, context, output, arguments.get("render_pages"), payload
        )

    async def _from_pdf(
        self,
        arguments: dict[str, Any],
        context: ToolContext,
        source_raw: str,
        output_raw: str,
        out_suffix: str,
    ) -> dict[str, Any]:
        location = arguments.get("location")
        source = resolve_input(context, source_raw, location=location, suffixes=_PDF)
        password = optional_str(arguments, "password") or None
        reader = await asyncio.to_thread(pdf_inspect.open_reader, source, password)
        pages = parse_page_spec(arguments.get("pages"), len(reader.pages))
        if out_suffix == ".txt":
            output = resolve_output(context, output_raw, location=location)
            chars = await asyncio.to_thread(
                conversions.pdf_to_text, source, output, pages=pages, password=password
            )
            return {"path": display_path(context, output), "pages": len(pages), "chars": chars}
        if out_suffix not in {".png", ".jpg", ".jpeg"}:
            raise ToolArgumentError("A PDF converts to .png, .jpg (with {page}) or .txt.")
        if "{page}" not in output_raw and len(pages) > 1:
            stem, dot, ext = output_raw.rpartition(".")
            output_raw = f"{stem}-{{page}}{dot}{ext}"
        # Resolving the pattern checks the directory stays inside the workspace.
        pattern = for_context(context, location).resolve(output_raw, must_exist=False)
        dpi = clamp_int(arguments.get("dpi"), default=150, low=36, high=600)
        written = await asyncio.to_thread(
            conversions.pdf_to_images,
            source,
            pages,
            pattern=pattern,
            image_format="jpg" if out_suffix in {".jpg", ".jpeg"} else "png",
            dpi=dpi,
            password=password,
        )
        if not written:
            raise ToolExecutionError("No pages were exported.")
        listed = [display_path(context, path) for path in written]
        return {"path": listed[0], "files": listed[:200], "count": len(listed), "dpi": dpi}
