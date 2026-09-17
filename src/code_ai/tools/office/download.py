"""Downloads that get through the company proxy.

Certificate checks follow ``ssl_verification``, off by default like every
other connection Code-AI makes: the proxy re-signs TLS with a certificate no
bundled CA list knows. Proxy variables in the environment are honoured.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import httpx

from code_ai.core.errors import ToolExecutionError

_CHUNK = 1 << 16
_ATTEMPTS = 3
# Connect fast or not at all; a large file on a slow link can take many
# minutes, so the read timeout is per chunk, not per file.
_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=60.0, pool=30.0)
_USER_AGENT = "code-ai"


def client(*, verify_ssl: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        verify=verify_ssl,
        follow_redirects=True,
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )


async def fetch_text(url: str, *, verify_ssl: bool) -> str:
    last: Exception | None = None
    for attempt in range(_ATTEMPTS):
        try:
            async with client(verify_ssl=verify_ssl) as http:
                response = await http.get(url)
                response.raise_for_status()
                return response.text
        except httpx.HTTPError as exc:
            last = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    raise ToolExecutionError(f"Could not fetch {url}: {_describe(last)}")


async def download(
    url: str,
    destination: Path,
    *,
    verify_ssl: bool,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Stream ``url`` to ``destination``, atomically, retrying a dropped connection.

    A ``.part`` file is only renamed once complete, so an interrupted download
    never passes for a finished one on the next attempt.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    last: Exception | None = None
    for attempt in range(_ATTEMPTS):
        try:
            async with client(verify_ssl=verify_ssl) as http:
                async with http.stream("GET", url) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length") or 0) or None
                    received = 0
                    with partial.open("wb") as stream:
                        async for chunk in response.aiter_bytes(_CHUNK):
                            stream.write(chunk)
                            received += len(chunk)
                            if progress is not None:
                                progress(received, total)
                    if total is not None and received < total:
                        raise httpx.ReadError(f"connection closed at {received} of {total} bytes")
            os.replace(partial, destination)
            return destination
        except (httpx.HTTPError, OSError) as exc:
            last = exc
            partial.unlink(missing_ok=True)
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                break
            await asyncio.sleep(2.0 * (attempt + 1))
    raise ToolExecutionError(f"Could not download {url}: {_describe(last)}")


def _describe(exc: Exception | None) -> str:
    if exc is None:
        return "unknown error"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    text = str(exc) or type(exc).__name__
    if "CERTIFICATE_VERIFY_FAILED" in text:
        text += " (set ssl_verification to false in the config on a network that re-signs TLS)"
    return text
