"""One MCP session: the handshake, requests with their answers, and what the
server may ask of us.

The client knows JSON-RPC and the MCP method names, and nothing about Code-AI:
what to answer when a server asks for roots, a model completion or the user's
input is handed in as callables, so the same client serves the chat session,
the tests and anything else that needs to talk to a server.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from code_ai.mcp import jsonrpc
from code_ai.mcp.jsonrpc import McpCancelled, McpConnectionError, McpError
from code_ai.mcp.transports import Transport

# A server that pages forever must not keep a listing going forever.
_MAX_PAGES = 50

ServerRequestHandler = Callable[[dict[str, Any]], Awaitable[Any]]
NotificationHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
ProgressHandler = Callable[[float, float | None, str], Awaitable[None]]


@dataclass(slots=True)
class ClientHandlers:
    """What this client does when the server asks something of it.

    A capability is only announced when its handler is set, so a server never
    asks for a model completion that nobody here can produce.
    """

    roots: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None
    sampling: ServerRequestHandler | None = None
    elicitation: ServerRequestHandler | None = None
    notification: NotificationHandler | None = None


@dataclass(slots=True)
class ServerIdentity:
    protocol_version: str = ""
    name: str = ""
    title: str = ""
    version: str = ""
    instructions: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)

    def supports(self, feature: str) -> bool:
        # A server that sends no capabilities at all is treated as offering the
        # basics; asking costs one "method not found" if it did not.
        if not self.capabilities:
            return feature == "tools"
        return feature in self.capabilities


class McpClient:
    def __init__(
        self,
        transport: Transport,
        *,
        client_name: str,
        client_version: str,
        handlers: ClientHandlers | None = None,
        request_timeout: float = 60.0,
    ) -> None:
        self._transport = transport
        self._client_name = client_name
        self._client_version = client_version
        self._handlers = handlers or ClientHandlers()
        self._request_timeout = request_timeout
        self._ids = itertools.count(1)
        self._pending: dict[int | str, asyncio.Future[Any]] = {}
        self._progress: dict[str, ProgressHandler] = {}
        self._inbound: set[asyncio.Task[None]] = set()
        self._notices: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._notice_worker: asyncio.Task[None] | None = None
        self._closed_reason = ""
        self._closed = asyncio.Event()
        self.identity = ServerIdentity()

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def closed_reason(self) -> str:
        return self._closed_reason

    async def wait_closed(self) -> str:
        await self._closed.wait()
        return self._closed_reason

    async def connect(self, *, timeout: float) -> ServerIdentity:
        """Start the transport and run the initialize handshake."""

        await self._transport.start(self._on_message, self._on_transport_closed)
        capabilities: dict[str, Any] = {}
        if self._handlers.roots is not None:
            capabilities["roots"] = {"listChanged": True}
        if self._handlers.sampling is not None:
            capabilities["sampling"] = {}
        if self._handlers.elicitation is not None:
            capabilities["elicitation"] = {}
        answer = await self.request(
            "initialize",
            {
                "protocolVersion": jsonrpc.PROTOCOL_VERSION,
                "capabilities": capabilities,
                "clientInfo": {"name": self._client_name, "version": self._client_version},
            },
            timeout=timeout,
        )
        if not isinstance(answer, dict):
            raise McpError(
                jsonrpc.INVALID_REQUEST, "The server answered initialize with no result."
            )
        version = str(answer.get("protocolVersion") or "")
        if version not in jsonrpc.SUPPORTED_VERSIONS:
            raise McpError(
                jsonrpc.INVALID_REQUEST,
                f"The server speaks MCP {version or '(unknown)'}, which this client does not.",
            )
        info = answer.get("serverInfo") if isinstance(answer.get("serverInfo"), dict) else {}
        self.identity = ServerIdentity(
            protocol_version=version,
            name=str(info.get("name") or ""),
            title=str(info.get("title") or ""),
            version=str(info.get("version") or ""),
            instructions=str(answer.get("instructions") or ""),
            capabilities=dict(answer.get("capabilities") or {}),
        )
        self._transport.set_protocol_version(version)
        await self.notify("notifications/initialized")
        self._transport.open_listener()
        return self.identity

    async def close(self) -> None:
        await self._transport.close()
        self._finish("the client closed the connection")
        if self._notice_worker is not None:
            self._notice_worker.cancel()

    # -- requests ---------------------------------------------------------

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        progress: ProgressHandler | None = None,
        cancel: asyncio.Event | None = None,
    ) -> Any:
        """Send one request and wait for its answer.

        Timing out or being cancelled tells the server to stop, as the spec
        asks, so a long tool call does not keep running for nobody.
        """

        if self.closed:
            raise McpConnectionError(self._closed_reason or "The connection is closed.")
        message_id = next(self._ids)
        token = ""
        if progress is not None:
            token = f"progress-{message_id}"
            params = dict(params or {})
            params["_meta"] = {**dict(params.get("_meta") or {}), "progressToken": token}
            self._progress[token] = progress
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await self._transport.send(jsonrpc.request(message_id, method, params))
            return await _race(future, timeout or self._request_timeout, cancel)
        except (TimeoutError, McpCancelled, asyncio.CancelledError) as exc:
            if method != "initialize" and not self.closed:
                reason = "timed out" if isinstance(exc, TimeoutError) else "cancelled"
                with contextlib.suppress(Exception):
                    await self.notify(
                        "notifications/cancelled", {"requestId": message_id, "reason": reason}
                    )
            if isinstance(exc, TimeoutError):
                raise McpError(
                    jsonrpc.INTERNAL_ERROR,
                    f"{method} got no answer in {timeout or self._request_timeout:.0f}s.",
                ) from None
            raise
        finally:
            self._pending.pop(message_id, None)
            if token:
                self._progress.pop(token, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._transport.send(jsonrpc.notification(method, params))

    async def ping(self, *, timeout: float = 10.0) -> None:
        await self.request("ping", timeout=timeout)

    # -- typed calls ------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        return await self._paged("tools/list", "tools")

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float,
        progress: ProgressHandler | None = None,
        cancel: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        answer = await self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
            progress=progress,
            cancel=cancel,
        )
        return answer if isinstance(answer, dict) else {"content": []}

    async def list_resources(self) -> list[dict[str, Any]]:
        return await self._paged("resources/list", "resources")

    async def list_resource_templates(self) -> list[dict[str, Any]]:
        return await self._paged("resources/templates/list", "resourceTemplates")

    async def read_resource(self, uri: str) -> list[dict[str, Any]]:
        answer = await self.request("resources/read", {"uri": uri})
        contents = answer.get("contents") if isinstance(answer, dict) else None
        return [item for item in contents or [] if isinstance(item, dict)]

    async def list_prompts(self) -> list[dict[str, Any]]:
        return await self._paged("prompts/list", "prompts")

    async def get_prompt(self, name: str, arguments: dict[str, str]) -> dict[str, Any]:
        answer = await self.request("prompts/get", {"name": name, "arguments": arguments})
        return answer if isinstance(answer, dict) else {"messages": []}

    async def set_log_level(self, level: str) -> None:
        await self.request("logging/setLevel", {"level": level})

    async def complete(
        self, ref: dict[str, Any], argument: dict[str, str], context: dict[str, Any] | None = None
    ) -> list[str]:
        params: dict[str, Any] = {"ref": ref, "argument": argument}
        if context:
            params["context"] = context
        answer = await self.request("completion/complete", params)
        completion = answer.get("completion") if isinstance(answer, dict) else None
        values = completion.get("values") if isinstance(completion, dict) else None
        return [str(value) for value in values or []]

    async def notify_roots_changed(self) -> None:
        await self.notify("notifications/roots/list_changed")

    async def _paged(self, method: str, key: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            answer = await self.request(method, {"cursor": cursor} if cursor else None)
            if not isinstance(answer, dict):
                break
            items.extend(item for item in answer.get(key) or [] if isinstance(item, dict))
            cursor = answer.get("nextCursor")
            if not cursor:
                break
        return items

    # -- inbound ----------------------------------------------------------

    async def _on_message(self, message: Any) -> None:
        if isinstance(message, list):
            for item in message:
                await self._on_message(item)
            return
        if not isinstance(message, dict):
            return
        if jsonrpc.is_response(message):
            future = self._pending.get(message.get("id"))
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(McpError.from_payload(message["error"]))
            else:
                future.set_result(message.get("result"))
        elif jsonrpc.is_request(message):
            # Never on the reader: a sampling request can wait on a person, and
            # the answer to our own request may arrive while it does.
            task = asyncio.create_task(self._answer(message))
            self._inbound.add(task)
            task.add_done_callback(self._inbound.discard)
        elif jsonrpc.is_notification(message):
            await self._on_notification(message)

    async def _on_notification(self, message: dict[str, Any]) -> None:
        method = str(message.get("method"))
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "notifications/progress":
            handler = self._progress.get(str(params.get("progressToken")))
            if handler is not None:
                total = params.get("total")
                with contextlib.suppress(Exception):
                    await handler(
                        float(params.get("progress") or 0),
                        float(total) if isinstance(total, int | float) else None,
                        str(params.get("message") or ""),
                    )
            return
        if method == "notifications/cancelled" or self._handlers.notification is None:
            return
        # In order, but off the reader: a handler that asks the server something
        # (tools/list after list_changed) needs the reader free to get the answer.
        self._notices.put_nowait((method, params))
        if self._notice_worker is None or self._notice_worker.done():
            self._notice_worker = asyncio.create_task(self._drain_notices())

    async def _drain_notices(self) -> None:
        handler = self._handlers.notification
        while handler is not None and not self._notices.empty():
            method, params = self._notices.get_nowait()
            with contextlib.suppress(Exception):
                await handler(method, params)

    async def _answer(self, message: dict[str, Any]) -> None:
        method = str(message.get("method"))
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        try:
            if method == "ping":
                value: Any = {}
            elif method == "roots/list" and self._handlers.roots is not None:
                value = {"roots": await self._handlers.roots()}
            elif method == "sampling/createMessage" and self._handlers.sampling is not None:
                value = await self._handlers.sampling(params)
            elif method == "elicitation/create" and self._handlers.elicitation is not None:
                value = await self._handlers.elicitation(params)
            else:
                raise McpError(jsonrpc.METHOD_NOT_FOUND, f"{method} is not supported.")
            reply = jsonrpc.result(message["id"], value)
        except McpError as exc:
            reply = jsonrpc.error(message["id"], exc)
        except Exception as exc:  # noqa: BLE001 - becomes the error the server reads
            reply = jsonrpc.error(message["id"], McpError(jsonrpc.INTERNAL_ERROR, str(exc)))
        with contextlib.suppress(Exception):
            await self._transport.send(reply)

    async def _on_transport_closed(self, reason: str) -> None:
        self._finish(reason)

    def _finish(self, reason: str) -> None:
        if self._closed.is_set():
            return
        self._closed_reason = reason
        self._closed.set()
        failure = McpConnectionError(f"The connection ended: {reason}.")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(failure)
        for task in list(self._inbound):
            task.cancel()


async def _race(future: asyncio.Future[Any], timeout: float, cancel: asyncio.Event | None) -> Any:
    """The answer, unless the timeout or a cancel request comes first."""

    if cancel is None:
        return await asyncio.wait_for(future, timeout=timeout)
    stop = asyncio.ensure_future(cancel.wait())
    try:
        done, _ = await asyncio.wait(
            {future, stop}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if future in done:
            return future.result()
        if stop in done:
            raise McpCancelled()
        raise TimeoutError()
    finally:
        stop.cancel()
