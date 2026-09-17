from __future__ import annotations

import asyncio
import datetime
import os
import re
import shutil
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.latex import compile as compiler
from code_ai.tools.latex import engines, install, markup, templates
from code_ai.tools.locations import LOCATION_SCHEMA, for_context
from code_ai.tools.office.common import (
    attach_images,
    clamp_int,
    display_path,
    optional_str,
    parse_page_spec,
    require_str,
    resolve_input,
)
from code_ai.tools.pdf.common import parse_json_argument
from code_ai.tools.schema import tool_schema

_BIB_ENTRY_FIELDS = (
    "author",
    "title",
    "journal",
    "booktitle",
    "year",
    "volume",
    "number",
    "pages",
    "publisher",
    "address",
    "doi",
    "url",
    "note",
    "editor",
    "edition",
    "school",
    "institution",
    "month",
    "urldate",
)


# latexmk's $pdf_mode for each engine.
PDF_MODES = {"pdflatex": 1, "lualatex": 4, "xelatex": 5}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, path)


def _authors(value: Any) -> list[templates.Author]:
    data = (
        parse_json_argument(value, "authors")
        if isinstance(value, str) and value.strip().startswith(("[", "{"))
        else value
    )
    if data in (None, "", []):
        return [templates.Author(name="Author")]
    if isinstance(data, str):
        return [
            templates.Author(name=name.strip())
            for name in re.split(r"[;\n]+", data)
            if name.strip()
        ]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ToolArgumentError("'authors' is a JSON list of {name, affiliation, email, orcid}.")
    authors = []
    for item in data:
        if isinstance(item, str):
            authors.append(templates.Author(name=item))
        elif isinstance(item, dict) and item.get("name"):
            authors.append(
                templates.Author(
                    name=str(item["name"]),
                    affiliation=str(item.get("affiliation", "")),
                    email=str(item.get("email", "")),
                    orcid=str(item.get("orcid", "")),
                )
            )
        else:
            raise ToolArgumentError("Each author needs at least a name.")
    return authors


def bibtex_from_entries(entries: Any) -> str:
    data = parse_json_argument(entries, "references")
    if not isinstance(data, list):
        raise ToolArgumentError(
            "'references' is a JSON list of {key, type, title, author, year, ...}."
        )
    out = []
    for number, entry in enumerate(data, start=1):
        if not isinstance(entry, dict) or not entry.get("key"):
            raise ToolArgumentError(f"reference {number} needs a 'key'.")
        kind = str(entry.get("type", "article")).lower().lstrip("@")
        fields = []
        for name in _BIB_ENTRY_FIELDS:
            value = entry.get(name)
            if value in (None, ""):
                continue
            if isinstance(value, list):
                value = " and ".join(str(v) for v in value)
            fields.append(f"  {name} = {{{value}}}")
        out.append(f"@{kind}{{{entry['key']},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(out) + "\n"


class LatexArticleTool:
    name = "latex_article"
    description = (
        "Create a LaTeX article project (IEEE, ACM, Springer, Elsevier, ABNT...) from Markdown. "
        "Writes main.tex, references.bib and a "
        "figures/ folder, in one of these templates - article (generic), report (chapters and "
        "table of contents), ieee (IEEEtran conference), acm (acmart sigconf), springer (LNCS), "
        "elsevier (elsarticle) or abnt (abnTeX2: cover, title page, resumo/abstract, sumário, "
        "ABNT citations). The body is Markdown: '#' headings become sections (chapters in "
        "report/abnt), with {#sec:label}; $inline$ and $$display$$ math, equation environments "
        "and any raw LaTeX pass through untouched; citations [@key] or [@a; @b]; cross "
        "references @fig:x @tab:x @sec:x @eq:x; figures '![Caption {#fig:x}](img.png){width=70%}' "
        "(images are copied in); tables with a 'Table: Caption {#tab:x}' line before them "
        "(booktabs); lists, quotes, code. Bibliography from BibTeX text or structured entries. "
        "Set compile true to build the PDF right away (same as latex_compile)."
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
            "output_dir": {
                "type": "string",
                "description": "Project directory to create (e.g. 'paper').",
            },
            "template": {
                "type": "string",
                "description": "article (default), report, ieee, acm, springer, elsevier or abnt.",
            },
            "title": {"type": "string", "description": "Article title."},
            "authors": {
                "type": "string",
                "description": (
                    'JSON list: [{"name":"Ana Silva","affiliation":"UFAM",'
                    '"email":"ana@ufam.edu.br","orcid":"0000-..."}].'
                ),
            },
            "abstract": {
                "type": "string",
                "description": "Abstract (Markdown/LaTeX allowed); for abnt, the resumo.",
            },
            "abstract_en": {"type": "string", "description": "abnt: English abstract."},
            "keywords": {"type": "array", "items": {"type": "string"}, "description": "Keywords."},
            "keywords_en": {
                "type": "array",
                "items": {"type": "string"},
                "description": "abnt: English keywords.",
            },
            "content": {
                "type": "string",
                "description": "The article body in Markdown (see description).",
            },
            "content_file": {
                "type": "string",
                "description": "A Markdown file with the body, instead of content.",
            },
            "bibtex": {"type": "string", "description": "BibTeX entries as text."},
            "references": {
                "type": "string",
                "description": (
                    'JSON list of entries: [{"key":"knuth84","type":"book",'
                    '"author":"Donald Knuth","title":"The TeXbook","year":1984,'
                    '"publisher":"Addison-Wesley"}].'
                ),
            },
            "acknowledgments": {"type": "string", "description": "Acknowledgments text."},
            "appendix": {"type": "string", "description": "Appendix in Markdown ('#' headings)."},
            "language": {
                "type": "string",
                "description": "english (default) or brazilian/pt-BR (abnt defaults to it).",
            },
            "engine": {"type": "string", "description": "pdflatex (default), xelatex or lualatex."},
            "abnt": {
                "type": "string",
                "description": (
                    'abnt: JSON {"institution":"Universidade...","type":"Dissertação (Mestrado)",'
                    '"advisor":"Prof. Dr. ...","city":"Manaus","year":"2026",'
                    '"preamble":"Dissertação apresentada ao Programa..."}.'
                ),
            },
            "date": {
                "type": "string",
                "description": "Date line (default today; empty string for none).",
            },
            "location": LOCATION_SCHEMA,
            "overwrite": {
                "type": "boolean",
                "description": "Replace existing main.tex/references.bib (default false).",
            },
            "compile": {
                "type": "boolean",
                "description": "Compile to PDF after writing (default false).",
            },
            "render_pages": {
                "type": "string",
                "description": "With compile: pages to return as images, e.g. '1-2'.",
            },
        },
        required=("output_dir", "title"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        location = arguments.get("location")
        tree = for_context(context, location)
        project = tree.resolve(require_str(arguments, "output_dir", self.name), must_exist=False)
        template = optional_str(arguments, "template", "article").lower()
        if template not in templates.TEMPLATES:
            raise ToolArgumentError(f"template is one of {', '.join(templates.TEMPLATES)}.")
        main = project / "main.tex"
        if main.exists() and not arguments.get("overwrite"):
            raise ToolExecutionError(
                f"{display_path(context, main)} exists; pass overwrite true to replace it."
            )

        content = arguments.get("content")
        base = project
        if not (isinstance(content, str) and content.strip()):
            content_file = optional_str(arguments, "content_file")
            if content_file:
                path = resolve_input(context, content_file, location=location)
                content, base = path.read_text(encoding="utf-8", errors="replace"), path.parent
            else:
                content = ""
        language = optional_str(arguments, "language") or (
            "brazilian" if template == "abnt" else "english"
        )
        engine = optional_str(arguments, "engine", "pdflatex").lower()
        if engine not in {"pdflatex", "xelatex", "lualatex"}:
            raise ToolArgumentError("engine is pdflatex, xelatex or lualatex.")
        abnt = parse_json_argument(arguments.get("abnt"), "abnt") or {}
        if not isinstance(abnt, dict):
            raise ToolArgumentError("'abnt' is a JSON object.")
        today = datetime.date.today()
        date = arguments.get("date")
        date = (
            (str(today.year) if template == "abnt" else today.strftime("%B %Y"))
            if date is None
            else str(date)
        )

        bibtex = optional_str(arguments, "bibtex")
        if arguments.get("references"):
            bibtex = (bibtex + "\n\n" if bibtex else "") + bibtex_from_entries(
                arguments["references"]
            )

        figures = project / "figures"
        copied: dict[str, str] = {}

        def copy_image(source: str) -> str:
            if source in copied:
                return copied[source]
            if source.startswith(("http://", "https://")):
                raise ToolArgumentError(
                    "remote images are not downloaded; save them into the workspace first"
                )
            candidate = Path(source) if Path(source).is_absolute() else base / source
            path = tree.resolve(candidate, must_exist=True)
            figures.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^\w.\-]+", "-", path.name)
            target = figures / name
            if path.resolve() != target.resolve():
                shutil.copyfile(path, target)
            copied[source] = f"figures/{name}"
            return copied[source]

        converter = markup.Converter(
            copy_image=copy_image,
            heading_commands=templates.heading_commands(template),
            language=language,
        )
        spec = templates.ArticleSpec(
            template=template,
            title=require_str(arguments, "title", self.name),
            authors=_authors(arguments.get("authors")),
            abstract=converter.convert(optional_str(arguments, "abstract")).strip()
            if arguments.get("abstract")
            else "",
            abstract_en=converter.convert(optional_str(arguments, "abstract_en")).strip()
            if arguments.get("abstract_en")
            else "",
            keywords=[str(k) for k in arguments.get("keywords") or []],
            keywords_en=[str(k) for k in arguments.get("keywords_en") or []],
            acknowledgments=converter.convert(optional_str(arguments, "acknowledgments")).strip()
            if arguments.get("acknowledgments")
            else "",
            language=language,
            engine=engine,
            has_bibliography=bool(bibtex.strip()),
            abnt=abnt,
            date=date,
        )
        body = converter.convert(content) if content.strip() else ""
        appendix = (
            converter.convert(optional_str(arguments, "appendix"))
            if arguments.get("appendix")
            else ""
        )
        tex = templates.render(spec, body, appendix, "references")

        missing_keys: list[str] = []
        if converter.citations:
            defined = set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", bibtex))
            missing_keys = sorted(converter.citations - defined)

        def write_all() -> None:
            _write(main, tex)
            if bibtex.strip():
                _write(project / "references.bib", bibtex.strip() + "\n")
            _write(
                project / "latexmkrc",
                f"$pdf_mode = {PDF_MODES[engine]};\n$out_dir = 'build';\n",
            )
            ignore = project / ".gitignore"
            if not ignore.exists():
                _write(
                    ignore,
                    "build/\n*.aux\n*.log\n*.out\n*.toc\n*.bbl\n*.blg\n*.synctex.gz\n*.fdb_latexmk\n*.fls\n",
                )

        await asyncio.to_thread(write_all)
        payload: dict[str, Any] = {
            "path": display_path(context, main),
            "template": template,
            "files": [display_path(context, main)]
            + ([display_path(context, project / "references.bib")] if bibtex.strip() else [])
            + [display_path(context, figures / Path(p).name) for p in copied.values()],
            "citations": sorted(converter.citations),
        }
        if missing_keys:
            payload["citations_without_bib_entry"] = missing_keys
        if converter.notes:
            payload["notes"] = converter.notes
        if arguments.get("compile"):
            compiled = await LatexCompileTool().execute(
                {
                    "path": display_path(context, main),
                    "location": location,
                    "render_pages": arguments.get("render_pages"),
                },
                context,
            )
            images = compiled.pop("_images", None)
            payload["compile"] = compiled
            if images:
                payload["_images"] = images
        return payload


class LatexCompileTool:
    name = "latex_compile"
    description = (
        "Compile a .tex file to PDF and explain what went wrong. Picks the engine (pdflatex, or "
        "xelatex/lualatex from a '% !TEX program' line or fontspec), runs BibTeX or biber when the "
        "document cites, and reruns until references settle. Aux files go to build/, the PDF "
        "next to the source. Errors come back with file, line, the source lines around it and a "
        "hint; also undefined citations and references, missing files, overfull boxes and "
        "warnings. With Code-AI's own TeX Live (latex_setup), missing packages are installed "
        "automatically and the build retried. Optionally renders pages as images to check the "
        "layout."
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
            "path": {"type": "string", "description": "The main .tex file."},
            "location": LOCATION_SCHEMA,
            "engine": {
                "type": "string",
                "description": "auto (default), pdflatex, xelatex, lualatex or tectonic.",
            },
            "output": {
                "type": "string",
                "description": "Where to put the PDF (default next to the .tex).",
            },
            "max_runs": {"type": "integer", "description": "Maximum engine runs (default 5)."},
            "install_missing": {
                "type": "boolean",
                "description": "Install missing packages with Code-AI's TeX Live (default true).",
            },
            "clean": {
                "type": "boolean",
                "description": "Delete build/ aux files after a successful build.",
            },
            "render_pages": {
                "type": "string",
                "description": "Pages to return as images, e.g. '1' or '1-3'.",
            },
        },
        required=("path",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        location = arguments.get("location")
        tex = resolve_input(
            context,
            require_str(arguments, "path", self.name),
            location=location,
            suffixes=(".tex", ".ltx"),
        )
        output_raw = optional_str(arguments, "output")
        output = (
            for_context(context, location).resolve(output_raw, must_exist=False)
            if output_raw
            else None
        )
        setup = await asyncio.to_thread(engines.discover)
        result = await compiler.compile_tex(
            tex,
            setup,
            engine=optional_str(arguments, "engine") or None,
            output=output,
            max_runs=clamp_int(arguments.get("max_runs"), default=5, low=1, high=10),
            install_missing=arguments.get("install_missing") is not False,
            verify_ssl=bool(context.config.ssl_verification),
            cancel_event=context.cancel_event,
        )
        payload: dict[str, Any] = {
            "success": result.pdf is not None,
            "engine": result.engine,
            "tex_distribution": setup.source,
            "runs": result.runs,
        }
        if result.pdf is not None:
            payload["path"] = display_path(context, result.pdf)
            from code_ai.tools.office.render import page_count

            payload["pages"] = page_count(result.pdf)
        if result.bibliography:
            payload["bibliography"] = result.bibliography
        if result.installed:
            payload["installed_packages"] = result.installed
        payload.update(result.report.to_dict())
        if result.notes:
            payload["notes"] = result.notes
        if result.pdf is not None and arguments.get("clean"):
            payload["cleaned_files"] = compiler.clean_build(tex)
        if result.pdf is not None and arguments.get("render_pages"):
            from code_ai.tools.office.render import page_count, render_pages

            total = page_count(result.pdf)
            try:
                pages = parse_page_spec(arguments["render_pages"], total)
            except ToolArgumentError:
                pages = list(range(min(total, 2)))
            payload["rendered_pages"] = [p + 1 for p in pages[:12]]
            attach_images(payload, await asyncio.to_thread(render_pages, result.pdf, pages))
        return payload


class LatexSetupTool:
    name = "latex_setup"
    description = (
        "Check or install the TeX distribution LaTeX tools use. action 'status' (default): which "
        "engines, BibTeX, biber, latexmk or tectonic exist and where (PATH, MiKTeX, TeX Live, or "
        "Code-AI's own). action 'install': install TeX Live for this user only, without admin "
        "rights, through the company proxy (downloads skip certificate checks unless "
        "ssl_verification is on; packages are still verified by checksum) - a base system plus "
        "what the article templates need, roughly 1 GB and 10-30 minutes; later documents get "
        "missing packages installed automatically. action 'packages': install named TeX Live "
        "packages into that installation."
    )
    capabilities = frozenset(
        {ToolCapability.PROCESS, ToolCapability.WEB, ToolCapability.LOCAL_WRITE}
    )
    input_schema = tool_schema(
        {
            "action": {"type": "string", "description": "status (default), install or packages."},
            "packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "packages: TeX Live package names, e.g. ['tikz-cd', 'siunitx'].",
            },
            "templates": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "install: templates to prepare (default all): ieee, acm, springer, "
                    "elsevier, abnt."
                ),
            },
            "repository": {
                "type": "string",
                "description": "A CTAN mirror's tlnet URL, when the default mirror is blocked.",
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        action = optional_str(arguments, "action", "status").lower()
        verify_ssl = bool(context.config.ssl_verification)
        repository = optional_str(arguments, "repository") or install.REPOSITORY
        if action == "status":
            setup = await asyncio.to_thread(engines.discover)
            payload = setup.describe()
            if not setup.usable:
                payload["next"] = "No TeX found. Call latex_setup with action 'install'."
            return payload
        if action == "install":
            chosen = [t.lower() for t in arguments.get("templates") or install.TEMPLATE_PACKAGES]
            unknown = sorted(set(chosen) - set(install.TEMPLATE_PACKAGES))
            if unknown:
                raise ToolArgumentError(f"Unknown template(s) {unknown}.")
            packages = install.BASE_PACKAGES + tuple(
                p for t in chosen for p in install.TEMPLATE_PACKAGES[t]
            )
            result = await install.install_texlive(
                verify_ssl=verify_ssl,
                repository=repository,
                extra_packages=tuple(dict.fromkeys(packages)),
            )
            setup = await asyncio.to_thread(engines.discover)
            return {**result, "setup": setup.describe()}
        if action == "packages":
            names = [str(p).strip() for p in arguments.get("packages") or [] if str(p).strip()]
            if not names:
                raise ToolArgumentError("packages action needs 'packages'.")
            return await install.install_packages(
                names, verify_ssl=verify_ssl, repository=repository
            )
        raise ToolArgumentError("action is status, install or packages.")
