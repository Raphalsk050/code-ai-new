"""Loading a page at several viewports: previews with diagnostics, and the UX audit."""

from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.design.audit_script import ANNOTATE_JS, AUDIT_JS, FOCUS_PROBE_JS
from code_ai.tools.office.chromium import headless_page
from code_ai.tools.office.render import png_thumbnail

VIEWPORTS: dict[str, tuple[int, int, float, bool]] = {
    "mobile": (390, 844, 2.0, True),
    "tablet": (820, 1180, 2.0, True),
    "laptop": (1280, 800, 1.0, False),
    "desktop": (1440, 900, 1.0, False),
    "wide": (1920, 1080, 1.0, False),
}
SEVERITY_WEIGHT = {"serious": 12, "moderate": 6, "minor": 2, "info": 0}
SEVERITY_COLOR = {
    "serious": "#d7263d",
    "moderate": "#f18f01",
    "minor": "#2e86ab",
    "info": "#6c757d",
}
MAX_IMAGES = 8


@dataclass(frozen=True)
class Viewport:
    name: str
    width: int
    height: int
    scale: float
    mobile: bool


def parse_viewports(values: Any) -> list[Viewport]:
    if values in (None, "", []):
        values = ["mobile", "desktop"]
    if isinstance(values, str):
        values = [v for v in re.split(r"[,\s]+", values) if v]
    parsed = []
    for value in values:
        key = str(value).strip().lower()
        if key in VIEWPORTS:
            width, height, scale, mobile = VIEWPORTS[key]
            parsed.append(Viewport(key, width, height, scale, mobile))
            continue
        match = re.fullmatch(r"(\d{3,4})\s*x\s*(\d{3,4})", key)
        if not match:
            raise ToolArgumentError(
                f"Viewport {value!r}: use {', '.join(VIEWPORTS)} or WIDTHxHEIGHT."
            )
        width, height = int(match.group(1)), int(match.group(2))
        parsed.append(Viewport(key, width, height, 1.0, width < 600))
    return parsed[:6]


def schemes(value: Any) -> list[str | None]:
    key = str(value or "light").strip().lower()
    if key == "both":
        return ["light", "dark"]
    if key not in {"light", "dark"}:
        raise ToolArgumentError("color_scheme is light, dark or both.")
    return [key]


@dataclass
class Target:
    url: str
    scratch: tempfile.TemporaryDirectory | None = None

    def close(self) -> None:
        if self.scratch is not None:
            self.scratch.cleanup()


def target_from(
    url: str | None, file: Path | None, markup: str | None, base: Path | None
) -> Target:
    if url:
        if not re.match(r"^https?://", url):
            raise ToolArgumentError(
                "url must start with http:// or https:// (use path for local files)."
            )
        return Target(url)
    if file is not None:
        return Target(file.resolve().as_uri())
    if markup:
        scratch = tempfile.TemporaryDirectory(prefix="code-ai-ui-")
        if base is not None and "<base" not in markup.lower():
            tag = f'<base href="{base.resolve().as_uri()}/">'
            markup = (
                re.sub(r"(<head[^>]*>)", r"\1" + tag, markup, count=1, flags=re.I)
                if re.search(r"<head[^>]*>", markup, re.I)
                else tag + markup
            )
        page = Path(scratch.name, "page.html")
        page.write_text(markup, encoding="utf-8")
        return Target(page.as_uri(), scratch)
    raise ToolArgumentError("Give url, path or html.")


@dataclass
class PageDiagnostics:
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)

    def attach(self, page) -> None:
        page.on(
            "console", lambda msg: msg.type == "error" and self._add(self.console_errors, msg.text)
        )
        page.on("pageerror", lambda exc: self._add(self.console_errors, f"uncaught: {exc}"))
        page.on(
            "requestfailed",
            lambda req: self._add(self.failed_requests, f"{req.url} ({req.failure})"),
        )
        page.on(
            "response",
            lambda res: (
                res.status >= 400
                and self._add(self.failed_requests, f"{res.url} (HTTP {res.status})")
            ),
        )

    @staticmethod
    def _add(bucket: list[str], text: str) -> None:
        if len(bucket) < 20:
            bucket.append(str(text)[:300])


async def _load(page, url: str, *, wait_for: str | None, wait_ms: int, timeout_ms: int) -> None:
    try:
        await page.goto(url, wait_until="load", timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001 - playwright error types
        raise ToolExecutionError(f"Could not load {url}: {str(exc).splitlines()[0]}") from exc
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001 - long-polling pages never go idle; carry on
        pass
    if wait_for:
        try:
            await page.wait_for_selector(wait_for, timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise ToolExecutionError(
                f"{wait_for!r} never appeared: {str(exc).splitlines()[0]}"
            ) from exc
    if wait_ms:
        await asyncio.sleep(wait_ms / 1000)
    # Web fonts change text metrics; screenshots and measurements wait for them.
    try:
        await page.evaluate("document.fonts ? document.fonts.ready.then(() => true) : true")
    except Exception:  # noqa: BLE001
        pass


async def preview(
    target: Target,
    viewports: list[Viewport],
    color_schemes: list[str | None],
    *,
    verify_ssl: bool,
    full_page: bool,
    selector: str | None,
    wait_for: str | None,
    wait_ms: int,
    max_side: int,
    timeout_ms: int = 30_000,
) -> tuple[list[dict[str, Any]], list[tuple[str, bytes]]]:
    reports: list[dict[str, Any]] = []
    shots: list[tuple[str, bytes]] = []
    for viewport in viewports:
        for scheme in color_schemes:
            label = viewport.name + (f"-{scheme}" if len(color_schemes) > 1 else "")
            diagnostics = PageDiagnostics()
            async with headless_page(
                verify_ssl=verify_ssl,
                width=viewport.width,
                height=viewport.height,
                device_scale_factor=viewport.scale,
                is_mobile=viewport.mobile,
                color_scheme=scheme,
                timeout_ms=timeout_ms,
            ) as page:
                diagnostics.attach(page)
                await _load(
                    page, target.url, wait_for=wait_for, wait_ms=wait_ms, timeout_ms=timeout_ms
                )
                metrics = await page.evaluate(
                    "() => ({title: document.title, width: document.documentElement.scrollWidth,"
                    " height: document.documentElement.scrollHeight, viewport: innerWidth})"
                )
                if selector:
                    locator = page.locator(selector).first
                    if await locator.count() == 0:
                        raise ToolArgumentError(f"No element matches {selector!r}.")
                    png = await locator.screenshot()
                else:
                    png = await page.screenshot(full_page=full_page)
            report = {
                "viewport": label,
                "size": f"{viewport.width}x{viewport.height}",
                "title": metrics["title"],
                "document": f"{metrics['width']}x{metrics['height']}",
                "horizontal_overflow": metrics["width"] > metrics["viewport"] + 1,
            }
            if diagnostics.console_errors:
                report["console_errors"] = diagnostics.console_errors
            if diagnostics.failed_requests:
                report["failed_requests"] = diagnostics.failed_requests
            reports.append(report)
            shots.append((label, png))
    return reports, [(label, png_thumbnail(png, max_side)) for label, png in shots[:MAX_IMAGES]]


async def audit(
    target: Target,
    viewports: list[Viewport],
    *,
    verify_ssl: bool,
    checks: list[str] | None,
    annotate: bool,
    wait_for: str | None,
    wait_ms: int,
    max_side: int,
    color_scheme: str | None,
    timeout_ms: int = 30_000,
) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    merged: dict[tuple, dict[str, Any]] = {}
    shots: list[tuple[str, bytes]] = []
    pages: list[dict[str, Any]] = []
    for viewport in viewports:
        diagnostics = PageDiagnostics()
        async with headless_page(
            verify_ssl=verify_ssl,
            width=viewport.width,
            height=viewport.height,
            device_scale_factor=1.0,
            is_mobile=viewport.mobile,
            color_scheme=color_scheme,
            timeout_ms=timeout_ms,
        ) as page:
            diagnostics.attach(page)
            await _load(page, target.url, wait_for=wait_for, wait_ms=wait_ms, timeout_ms=timeout_ms)
            options = {
                "checks": checks,
                "focus_limit": 25,
                "report_manual": False,
                "viewport_width": viewport.width,
            }
            result = await page.evaluate(AUDIT_JS, options)
            findings = result["findings"]
            if checks is None or "focus" in checks:
                findings += await _focus_findings(page, result["focusables"])
            if diagnostics.console_errors:
                findings.append(
                    {
                        "rule": "console-errors",
                        "severity": "moderate",
                        "category": "quality",
                        "message": f"{len(diagnostics.console_errors)} JavaScript error(s) logged.",
                        "selector": None,
                        "snippet": diagnostics.console_errors[0],
                        "rect": None,
                    }
                )
            if diagnostics.failed_requests:
                findings.append(
                    {
                        "rule": "failed-requests",
                        "severity": "moderate",
                        "category": "quality",
                        "message": f"{len(diagnostics.failed_requests)} request(s) failed.",
                        "selector": None,
                        "snippet": diagnostics.failed_requests[0],
                        "rect": None,
                    }
                )
            if annotate:
                boxes = []
                for number, finding in enumerate(findings[:40], start=1):
                    finding["marker"] = number
                    if finding.get("rect") and finding["rect"][2] > 0:
                        x, y, w, h = finding["rect"]
                        boxes.append(
                            [number, x, y, w, h, SEVERITY_COLOR.get(finding["severity"], "#d7263d")]
                        )
                await page.evaluate(ANNOTATE_JS, boxes)
                png = await page.screenshot(full_page=True)
                shots.append(
                    (viewport.name, png_thumbnail(_crop_tall(png, viewport.width * 4), max_side))
                )
            pages.append(
                {
                    "viewport": viewport.name,
                    "size": f"{viewport.width}x{viewport.height}",
                    "title": result["title"],
                }
            )
        for finding in findings:
            key = (
                finding["rule"],
                finding.get("selector"),
                finding["message"] if not finding.get("selector") else "",
            )
            if key in merged:
                if viewport.name not in merged[key]["viewports"]:
                    merged[key]["viewports"].append(viewport.name)
            else:
                finding["viewports"] = [viewport.name]
                merged[key] = finding
    findings = sorted(merged.values(), key=lambda f: -SEVERITY_WEIGHT.get(f["severity"], 0))
    return {"pages": pages, "findings": findings, "summary": _summary(findings)}, shots


async def _focus_findings(page, count: int) -> list[dict[str, Any]]:
    if not count:
        return []
    seen: dict[int, dict[str, Any]] = {}
    for _ in range(count + 3):
        await page.keyboard.press("Tab")
        probe = await page.evaluate(FOCUS_PROBE_JS)
        if probe and probe["index"] not in seen:
            seen[probe["index"]] = probe
    return [
        {
            "rule": "focus-visible",
            "severity": "serious",
            "category": "accessibility",
            "message": "Keyboard focus is invisible here: no outline or style change on focus.",
            "selector": probe["selector"],
            "snippet": probe["snippet"],
            "rect": probe["rect"],
            "wcag": "2.4.7",
        }
        for probe in seen.values()
        if not probe["changed"]
    ]


def _crop_tall(png: bytes, max_height: int) -> bytes:
    """Very long pages become unreadable when shrunk whole; keep the top part."""

    import io

    from PIL import Image

    image = Image.open(io.BytesIO(png))
    if image.height <= max_height:
        return png
    buffer = io.BytesIO()
    image.crop((0, 0, image.width, max_height)).save(buffer, format="PNG")
    return buffer.getvalue()


def _summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, dict[str, int]] = {}
    for finding in findings:
        bucket = categories.setdefault(
            finding["category"], {"serious": 0, "moderate": 0, "minor": 0, "info": 0}
        )
        bucket[finding["severity"]] = bucket.get(finding["severity"], 0) + 1
    scores = {}
    for name in (
        "accessibility",
        "usability",
        "responsive",
        "readability",
        "performance",
        "quality",
    ):
        counts = categories.get(name, {})
        penalty = sum(SEVERITY_WEIGHT[s] * min(n, 6) for s, n in counts.items())
        scores[name] = max(0, 100 - penalty)
    return {
        "score": round(sum(scores.values()) / len(scores)),
        "category_scores": scores,
        "counts": {
            s: sum(1 for f in findings if f["severity"] == s)
            for s in ("serious", "moderate", "minor")
        },
    }
