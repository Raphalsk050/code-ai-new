from __future__ import annotations

import email.utils
import functools
import inspect
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from code_ai.providers.models import (
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)

if TYPE_CHECKING:
    from code_ai.config.models import AppConfig


@asynccontextmanager
async def closing_stream(stream: Any) -> AsyncIterator[Any]:
    """Yield a provider stream and close it on the way out.

    An inference server generates until the client disconnects, so cancelling a
    turn has to *close* the HTTP response rather than merely stop reading it.
    Without this, pressing Ctrl+C stopped the UI while the model kept running,
    burning the GPU on tokens nobody would ever read - and holding the slot
    against the next request.

    The OpenAI SDK's stream closes via ``close()``, a bare async generator via
    ``aclose()``. Both are accepted, so this works against the SDK and any
    stand-in, and an object offering neither is simply left alone.
    """
    try:
        yield stream
    finally:
        closer = getattr(stream, "close", None) or getattr(stream, "aclose", None)
        if closer is not None:
            result = closer()
            if inspect.isawaitable(result):
                await result


# Statuses that say the server is busy or failing, not that the request is wrong.
TRANSIENT_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})
# Transport failures of httpx and the OpenAI SDK, named so neither is imported.
_TRANSPORT_FAILURES = frozenset({"TransportError", "APIConnectionError"})


def _causes(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    link: BaseException | None = exc
    while link is not None and all(link is not seen for seen in chain):
        chain.append(link)
        link = link.__cause__
    return chain


def failure_status(exc: BaseException) -> int | None:
    """The HTTP status behind ``exc`` or anything it was raised from, if any."""

    for link in _causes(exc):
        status = getattr(link, "status_code", None)
        if status is None:
            status = getattr(getattr(link, "response", None), "status_code", None)
        if isinstance(status, int):
            return status
    return None


def is_unreachable(exc: BaseException) -> bool:
    """Whether the model could not be reached, or its server failed.

    That is worth waiting out. Not so a request the server refused, which
    fails the same way however often it is sent, nor a read that went silent
    past max_model_call_s, a cap the user chose.
    """

    chain = [(link, {cls.__name__ for cls in type(link).__mro__}) for link in _causes(exc)]
    if any(isinstance(link, TimeoutError) or "ReadTimeout" in names for link, names in chain):
        return False
    lost = any(
        isinstance(link, ConnectionError) or names & _TRANSPORT_FAILURES for link, names in chain
    )
    if lost:
        return True
    return failure_status(exc) in TRANSIENT_STATUSES


def retry_after(exc: BaseException) -> float | None:
    """Seconds the server asked to be left alone for (Retry-After), if it said."""

    for link in _causes(exc):
        headers = getattr(getattr(link, "response", None), "headers", None)
        value = headers.get("retry-after") if headers is not None else None
        if value is None:
            continue
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    return None


# Connecting, and a single write, still fail the attempt when they hang: the
# orchestrator retries it. Reading is capped only by max_model_call_s, which is
# off by default - a model that thinks for minutes sends nothing meanwhile.
_CONNECT_TIMEOUT_S = 30.0
_WRITE_TIMEOUT_S = 120.0
# With no read cap, a peer that vanished (a dropped VPN, a NAT that forgot the
# connection) would leave the read waiting for ever. Keepalive probes an idle
# connection after 30 s and drops it after six unanswered probes 10 s apart.
_KEEPALIVE = (
    (("TCP_KEEPIDLE", "TCP_KEEPALIVE"), 30),
    (("TCP_KEEPINTVL",), 10),
    (("TCP_KEEPCNT",), 6),
)


def http_timeout(config: AppConfig) -> Any:
    """The httpx timeout for a model request: bounded connect and write, read per the config."""

    import httpx

    return httpx.Timeout(
        connect=_CONNECT_TIMEOUT_S,
        read=config.budgets.model_timeout(),
        write=_WRITE_TIMEOUT_S,
        pool=None,
    )


@functools.cache
def keepalive_socket_options() -> tuple[tuple[int, int, int], ...]:
    """TCP keepalive options this machine accepts.

    Each is tried on a scratch socket first: an option the OS refuses would
    otherwise fail every connection made with it (older Windows builds lack
    TCP_KEEPCNT; macOS names the idle time TCP_KEEPALIVE).
    """

    wanted = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    for names, value in _KEEPALIVE:
        option = next((getattr(socket, name) for name in names if hasattr(socket, name)), None)
        if option is not None:
            wanted.append((socket.IPPROTO_TCP, option, value))
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return ()
    usable: list[tuple[int, int, int]] = []
    with probe:
        for option in wanted:
            try:
                probe.setsockopt(*option)
            except OSError:
                continue
            usable.append(option)
    return tuple(usable)


def build_http_client(config: AppConfig) -> Any:
    """An httpx client for model traffic: see http_timeout and keepalive_socket_options."""

    import httpx

    return httpx.AsyncClient(
        verify=config.ssl_verification,
        timeout=http_timeout(config),
        transport=httpx.AsyncHTTPTransport(
            verify=config.ssl_verification,
            socket_options=keepalive_socket_options(),
        ),
    )


def build_openai_http_client(config: AppConfig) -> Any | None:
    """Build the httpx client the OpenAI SDK should use, honoring ssl_verification.

    SSL verification defaults to disabled (``ssl_verification=False``) so that
    self-signed or intercepting proxies in front of OpenAI-compatible endpoints
    work out of the box, mirroring the native Ollama provider. Returns ``None``
    when httpx is unavailable so the SDK falls back to its own default client.
    """

    try:
        import httpx  # noqa: F401 - only whether it is there
    except Exception:  # pragma: no cover - httpx ships with the openai package
        return None
    return build_http_client(config)


class ModelProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        raise NotImplementedError

    async def complete(self, request: ModelRequest) -> ModelResponse:
        text_parts: list[str] = []
        completed: ModelResponse | None = None
        async for event in self.stream(request):
            if event.kind == "text_delta":
                text_parts.append(event.text_delta)
            elif event.kind == "completed" and event.response:
                completed = event.response
        if completed is None:
            completed = ModelResponse(text="".join(text_parts))
        elif not completed.text and text_parts:
            completed.text = "".join(text_parts)
        return completed

    async def close(self) -> None:
        return None
