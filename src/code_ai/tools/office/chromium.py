"""A throwaway headless Chromium for rendering HTML to screenshots and PDFs.

Kept apart from the agent's browser, whose persistent profile holds the user's
logins. The installers are shared, so one Chromium download serves both.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from code_ai.core.errors import ToolExecutionError
from code_ai.tools.browser.install import (
    FALLBACK_CHANNELS,
    MISSING_BROWSER,
    MISSING_DEPS,
    bundled_browser_present,
    describe_launch_failure,
    install_browser,
    install_system_deps,
    launch_failure_kind,
    package_missing_message,
    prepare_driver,
)

_LAUNCH_TIMEOUT_MS = 60_000


@asynccontextmanager
async def headless_page(
    *,
    verify_ssl: bool = False,
    width: int = 1280,
    height: int = 800,
    device_scale_factor: float = 1.0,
    is_mobile: bool = False,
    color_scheme: str | None = None,
    timeout_ms: int = 30_000,
) -> AsyncIterator[Any]:
    """A fresh page in a fresh headless browser, closed on exit."""

    try:
        module = importlib.import_module("playwright.async_api")
    except Exception as exc:  # noqa: BLE001 - any import failure means "not available"
        raise ToolExecutionError(package_missing_message()) from exc
    prepare_driver()
    async with module.async_playwright() as playwright:
        browser = await _launch(playwright.chromium, verify_ssl=verify_ssl)
        try:
            options: dict[str, Any] = {
                "viewport": {"width": width, "height": height},
                "device_scale_factor": device_scale_factor,
                "is_mobile": is_mobile,
                "has_touch": is_mobile,
                "ignore_https_errors": not verify_ssl,
            }
            if color_scheme in {"light", "dark"}:
                options["color_scheme"] = color_scheme
            context = await browser.new_context(**options)
            context.set_default_timeout(timeout_ms)
            try:
                yield await context.new_page()
            finally:
                await context.close()
        finally:
            await browser.close()


async def _launch(chromium: Any, *, verify_ssl: bool) -> Any:
    """Playwright's Chromium, installing it once if missing, else Chrome or Edge."""

    options = {"headless": True, "timeout": _LAUNCH_TIMEOUT_MS}
    install_output = ""
    if not bundled_browser_present(chromium):
        result = await install_browser(verify_ssl=verify_ssl)
        install_output = "" if result.ok else result.output
    try:
        return await chromium.launch(**options)
    except Exception as exc:  # noqa: BLE001
        failure: Exception = exc
    installers = {MISSING_BROWSER: install_browser, MISSING_DEPS: install_system_deps}
    tried: set[str] = set()
    for _ in range(2):
        kind = launch_failure_kind(failure)
        installer = installers.get(kind)
        if installer is None or kind in tried:
            break
        tried.add(kind)
        result = await installer(verify_ssl=verify_ssl)
        if not result.ok:
            install_output = result.output
            break
        try:
            return await chromium.launch(**options)
        except Exception as exc:  # noqa: BLE001
            failure = exc
    if launch_failure_kind(failure) == MISSING_BROWSER:
        for channel in FALLBACK_CHANNELS:
            try:
                return await chromium.launch(channel=channel, **options)
            except Exception:  # noqa: BLE001 - not on this machine either
                continue
    raise ToolExecutionError(
        describe_launch_failure(failure, install_output=install_output)
    ) from failure
