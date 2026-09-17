from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.design import exporters, pages, tokens
from code_ai.tools.locations import LOCATION_SCHEMA, for_context
from code_ai.tools.office.common import (
    attach_images,
    clamp_float,
    clamp_int,
    display_path,
    optional_str,
    resolve_input,
)
from code_ai.tools.office.deps import ensure
from code_ai.tools.schema import tool_schema

# Loaded when a tool runs: a missing library must not stop Code-AI from starting.
_DEPS = ("PIL",)


def _strings(value: Any, name: str) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ToolArgumentError(f"'{name}' is a list of strings.")


def _write(path: Path, text: str) -> None:
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, path)


class DesignSystemTool:
    name = "design_system"
    description = (
        "Generate accessible design tokens and colour palettes, check contrast, simulate colour "
        "blindness. mode 'generate' (default): from 1-3 brand "
        "colours builds perceptual OKLCH tonal scales (50-950), a brand-tinted neutral scale and "
        "success/warning/danger/info scales; light and dark theme roles (background, surface, "
        "text, muted, border, primary, on-primary, soft variants, link, focus ring, status "
        "colours) where every text pair is verified and auto-corrected to WCAG 2.2 AA and "
        "reported with APCA Lc; optional colour harmony; a modular fluid type scale with clamp(); "
        "spacing, radii, shadows, breakpoints, z-index and motion tokens. Writes CSS variables "
        "(light/dark, prefers-color-scheme, data-theme), Tailwind v3 config, Tailwind v4 @theme, "
        "SCSS, W3C design-token JSON and an accessible HTML style guide with components. mode "
        "'check': grade colour pairs (WCAG ratio, AA/AAA, APCA) and suggest the nearest passing "
        "colour. mode 'simulate': show colours as seen with protanopia, deuteranopia, "
        "tritanopia and achromatopsia and flag pairs that become indistinguishable."
    )
    capabilities = frozenset({ToolCapability.LOCAL_WRITE, ToolCapability.PROCESS})
    input_schema = tool_schema(
        {
            "mode": {"type": "string", "description": "generate (default), check or simulate."},
            "brand_colors": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "generate: 1-3 brand colours (#hex, rgb(), hsl(), oklch() or names); first is"
                    " primary."
                ),
            },
            "name": {"type": "string", "description": "generate: design system name."},
            "harmony": {
                "type": "string",
                "description": "generate: derive missing secondary/accent colours: complementary, "
                "analogous, "
                "triadic, split-complementary, tetradic or monochrome.",
            },
            "type_base": {
                "type": "number",
                "description": "generate: base font size in px (default 16).",
            },
            "type_ratio": {
                "type": "string",
                "description": (
                    "generate: minor-third, major-third (default), perfect-fourth, golden... or a"
                    " number."
                ),
            },
            "heading_font": {"type": "string", "description": "generate: heading font family."},
            "body_font": {"type": "string", "description": "generate: body font family."},
            "spacing_base": {
                "type": "integer",
                "description": "generate: 4 (default) or 8 px grid.",
            },
            "radius": {"type": "string", "description": "generate: sharp, default or round."},
            "neutral_tint": {
                "type": "number",
                "description": (
                    "generate: how much brand hue tints the greys, 0-0.03 (default 0.012)."
                ),
            },
            "output_dir": {
                "type": "string",
                "description": (
                    "generate: directory to write the files into. Omit to only return the tokens."
                ),
            },
            "formats": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "generate: any of css, tailwind3, tailwind4, scss, json, styleguide (default "
                    "all)."
                ),
            },
            "prefix": {
                "type": "string",
                "description": "generate: CSS variable prefix, e.g. 'ds-'.",
            },
            "location": LOCATION_SCHEMA,
            "pairs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "check: colour pairs like '#6b7280 on #ffffff'.",
            },
            "colors": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "simulate: colours that must stay distinguishable, e.g. chart series."
                ),
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        await ensure(*_DEPS, verify_ssl=bool(context.config.ssl_verification))
        mode = optional_str(arguments, "mode", "generate").lower()
        if mode == "check":
            pairs = _strings(arguments.get("pairs"), "pairs")
            if not pairs:
                raise ToolArgumentError("check mode needs 'pairs', e.g. ['#6b7280 on #ffffff'].")
            results = tokens.check_pairs(pairs)
            return {
                "mode": "check",
                "results": results,
                "failing": sum(1 for r in results if not r["aa_normal"]),
            }
        if mode == "simulate":
            colors = _strings(arguments.get("colors"), "colors")
            if len(colors) < 1:
                raise ToolArgumentError("simulate mode needs 'colors'.")
            return {"mode": "simulate", **tokens.simulate_colors(colors)}
        if mode != "generate":
            raise ToolArgumentError("mode is generate, check or simulate.")

        brand = _strings(arguments.get("brand_colors"), "brand_colors")
        system = tokens.build(
            brand,
            name=optional_str(arguments, "name", "Brand"),
            harmony=optional_str(arguments, "harmony") or None,
            neutral_tint=clamp_float(
                arguments.get("neutral_tint"), default=0.012, low=0.0, high=0.04
            ),
            type_base=clamp_float(arguments.get("type_base"), default=16, low=10, high=24),
            type_ratio=arguments.get("type_ratio") or "major-third",
            heading_font=optional_str(arguments, "heading_font") or None,
            body_font=optional_str(arguments, "body_font") or None,
            spacing_base=clamp_int(arguments.get("spacing_base"), default=4, low=2, high=8),
            radius=optional_str(arguments, "radius", "default"),
        )
        payload: dict[str, Any] = {
            "mode": "generate",
            "name": system.name,
            "colors": {
                group: {str(k): v for k, v in scale.items()}
                for group, scale in system.colors.items()
            },
            "brand_steps": system.base_steps,
            "themes": system.themes,
            "contrast": {
                theme: {"checked": len(rows), "failing": [r for r in rows if not r["pass"]]}
                for theme, rows in system.contrast.items()
            },
            "typography": {k: v["fluid"] for k, v in system.typography["sizes"].items()},
            "spacing": system.spacing,
            "radii": system.radii,
        }
        if system.harmony:
            payload["harmony"] = system.harmony
        if system.notes:
            payload["notes"] = system.notes

        output_dir = optional_str(arguments, "output_dir")
        if output_dir:
            formats = [f.lower() for f in _strings(arguments.get("formats"), "formats")] or list(
                exporters.FORMATS
            )
            unknown = sorted(set(formats) - set(exporters.FORMATS))
            if unknown:
                raise ToolArgumentError(
                    f"Unknown format(s) {unknown}. Use {', '.join(exporters.FORMATS)}."
                )
            directory = for_context(context, arguments.get("location")).resolve(
                output_dir, must_exist=False
            )
            prefix = optional_str(arguments, "prefix")

            def write_all() -> list[str]:
                directory.mkdir(parents=True, exist_ok=True)
                written = []
                for fmt in formats:
                    target = directory / exporters.FILENAMES[fmt]
                    _write(target, exporters.render(system, fmt, prefix))
                    written.append(display_path(context, target))
                return written

            payload["files"] = await asyncio.to_thread(write_all)
            payload["path"] = payload["files"][0]
            if "styleguide" in formats:
                payload["next"] = (
                    "Open the style guide with ui_preview to see it, and ui_audit to verify it."
                )
        return payload


_TARGET_FIELDS = {
    "url": {
        "type": "string",
        "description": "An http(s) URL, e.g. a local dev server http://localhost:5173.",
    },
    "path": {"type": "string", "description": "A local HTML file (relative assets load)."},
    "html": {"type": "string", "description": "HTML to render directly, e.g. a component mockup."},
    "base_dir": {
        "type": "string",
        "description": "For html: directory its relative assets resolve against.",
    },
    "location": LOCATION_SCHEMA,
    "viewports": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Any of mobile (390x844), tablet (820x1180), laptop (1280x800), desktop "
        "(1440x900), wide (1920x1080) or 'WIDTHxHEIGHT'. Default mobile and desktop.",
    },
    "wait_for": {"type": "string", "description": "CSS selector to wait for before capturing."},
    "wait_ms": {
        "type": "integer",
        "description": "Extra milliseconds to wait after load (animations).",
    },
    "max_side": {
        "type": "integer",
        "description": "Longest side of returned images in px (default 1400).",
    },
}


def _target(context: ToolContext, arguments: dict[str, Any]) -> pages.Target:
    location = arguments.get("location")
    url = optional_str(arguments, "url") or None
    path = optional_str(arguments, "path")
    file = (
        resolve_input(
            context, path, location=location, suffixes=(".html", ".htm", ".xhtml", ".svg")
        )
        if path
        else None
    )
    markup = (
        arguments.get("html")
        if isinstance(arguments.get("html"), str) and arguments["html"].strip()
        else None
    )
    base = optional_str(arguments, "base_dir")
    base_path = for_context(context, location).resolve(base, must_exist=True) if base else None
    if sum(1 for item in (url, file, markup) if item) != 1:
        raise ToolArgumentError("Give exactly one of url, path or html.")
    return pages.target_from(url, file, markup, base_path)


class UiPreviewTool:
    name = "ui_preview"
    description = (
        "Screenshot a web page, HTML file or HTML string at several viewports and colour schemes. "
        "Renders a URL (including a local dev server), an "
        "HTML file or an HTML string in a clean headless Chromium at one or more viewports "
        "(mobile, tablet, desktop or custom sizes), in light and/or dark colour scheme, and get "
        "the screenshots back as images - full page, above the fold, or one element by CSS "
        "selector. Also reports each page's title and document size, horizontal overflow, "
        "JavaScript console errors and failed requests, so a broken render explains itself. "
        "Optionally saves the PNGs. Use it to check a layout, a mockup or a redesign before and "
        "after changing it."
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
            **_TARGET_FIELDS,
            "color_scheme": {"type": "string", "description": "light (default), dark or both."},
            "full_page": {
                "type": "boolean",
                "description": "Capture the whole scrollable page (default false).",
            },
            "selector": {
                "type": "string",
                "description": "Capture only the first element matching this selector.",
            },
            "save_dir": {
                "type": "string",
                "description": "Also save full-resolution PNGs into this directory.",
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        await ensure(*_DEPS, verify_ssl=bool(context.config.ssl_verification))
        viewports = pages.parse_viewports(arguments.get("viewports"))
        color_schemes = pages.schemes(arguments.get("color_scheme"))
        if len(viewports) * len(color_schemes) > pages.MAX_IMAGES:
            raise ToolArgumentError(
                f"At most {pages.MAX_IMAGES} captures per call (viewports x schemes)."
            )
        target = _target(context, arguments)
        max_side = clamp_int(arguments.get("max_side"), default=1400, low=320, high=2400)
        try:
            reports, shots = await pages.preview(
                target,
                viewports,
                color_schemes,
                verify_ssl=bool(context.config.ssl_verification),
                full_page=bool(arguments.get("full_page")),
                selector=optional_str(arguments, "selector") or None,
                wait_for=optional_str(arguments, "wait_for") or None,
                wait_ms=clamp_int(arguments.get("wait_ms"), default=0, low=0, high=15_000),
                max_side=max_side,
            )
        finally:
            target.close()
        payload: dict[str, Any] = {
            "target": target.url if target.scratch is None else "html",
            "captures": reports,
        }
        save_dir = optional_str(arguments, "save_dir")
        if save_dir:
            directory = for_context(context, arguments.get("location")).resolve(
                save_dir, must_exist=False
            )
            directory.mkdir(parents=True, exist_ok=True)
            saved = []
            for label, png in shots:
                file = directory / f"{re.sub(r'[^a-z0-9-]+', '-', label.lower())}.png"
                file.write_bytes(png)
                saved.append(display_path(context, file))
            payload["saved"] = saved
            payload["path"] = saved[0] if saved else None
        return attach_images(payload, [png for _, png in shots])


class UiAuditTool:
    name = "ui_audit"
    description = (
        "Audit a web page's UX and accessibility like a reviewer would, at one or more viewports. "
        "Checks: text contrast with real computed colours (WCAG 1.4.3), missing alt text, form "
        "fields without labels or labelled only by placeholder, buttons and links without "
        "accessible names, heading structure, lang, title, viewport meta, main landmark, "
        "duplicate ids, touch targets under 24x24px (WCAG 2.5.8) and under 44px on mobile, "
        "controls covered by other elements, invisible keyboard focus (tabs through the page), "
        "positive tabindex, invalid ARIA roles, aria-hidden on focusable elements, links that "
        "only differ by colour, autoplaying sound, horizontal scrolling, clipped text, text "
        "under 12px, overlong lines, iOS input zoom, layout shift, oversized images, images "
        "without dimensions, page weight, console errors and failed requests. Returns scores "
        "per category and findings with severity, WCAG criterion, CSS selector, markup and a "
        "fix-oriented message; annotate draws numbered boxes on a screenshot of each viewport."
    )
    capabilities = frozenset(
        {ToolCapability.LOCAL_READ, ToolCapability.PROCESS, ToolCapability.WEB}
    )
    input_schema = tool_schema(
        {
            **_TARGET_FIELDS,
            "checks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Limit to: structure, contrast, typography, names, targets, aria, "
                "layout, "
                "performance, focus. Default all.",
            },
            "color_scheme": {"type": "string", "description": "light (default) or dark."},
            "annotate": {
                "type": "boolean",
                "description": "Return annotated screenshots (default true).",
            },
            "max_findings": {"type": "integer", "description": "Findings to return (default 60)."},
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        await ensure(*_DEPS, verify_ssl=bool(context.config.ssl_verification))
        viewports = pages.parse_viewports(arguments.get("viewports"))
        checks = [c.lower() for c in _strings(arguments.get("checks"), "checks")] or None
        known = {
            "structure",
            "contrast",
            "typography",
            "names",
            "targets",
            "aria",
            "layout",
            "performance",
            "focus",
        }
        if checks and set(checks) - known:
            raise ToolArgumentError(
                f"Unknown check(s) {sorted(set(checks) - known)}. Use {sorted(known)}."
            )
        scheme = pages.schemes(arguments.get("color_scheme"))[0]
        target = _target(context, arguments)
        try:
            report, shots = await pages.audit(
                target,
                viewports,
                verify_ssl=bool(context.config.ssl_verification),
                checks=checks,
                annotate=arguments.get("annotate") is not False,
                wait_for=optional_str(arguments, "wait_for") or None,
                wait_ms=clamp_int(arguments.get("wait_ms"), default=0, low=0, high=15_000),
                max_side=clamp_int(arguments.get("max_side"), default=1400, low=320, high=2400),
                color_scheme=scheme,
            )
        finally:
            target.close()
        limit = clamp_int(arguments.get("max_findings"), default=60, low=1, high=500)
        findings = report["findings"]
        payload: dict[str, Any] = {
            "target": target.url if target.scratch is None else "html",
            "pages": report["pages"],
            "summary": report["summary"],
            "findings": findings[:limit],
        }
        if len(findings) > limit:
            payload["omitted"] = len(findings) - limit
        return attach_images(payload, [png for _, png in shots])
