"""JSON-RPC 2.0 the way MCP speaks it: constants, errors and message shapes."""

from __future__ import annotations

from typing import Any

# What initialize asks for, and every version this client can talk. A server
# may answer with any of them; one it answers with outside this set ends the
# connection, as the spec asks.
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"})

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpError(Exception):
    """An error a server answered with, or one this client raises in its place."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error

    @classmethod
    def from_payload(cls, error: Any) -> McpError:
        if not isinstance(error, dict):
            return cls(INTERNAL_ERROR, str(error))
        return cls(
            int(error.get("code", INTERNAL_ERROR)),
            str(error.get("message") or "unknown error"),
            error.get("data"),
        )


class McpCancelled(McpError):
    """We stopped waiting because the user stopped the turn."""

    def __init__(self) -> None:
        super().__init__(INTERNAL_ERROR, "Cancelled.")


class McpConnectionError(McpError):
    """The transport is gone: the process exited, the socket closed, the server 404'd."""

    def __init__(self, message: str) -> None:
        super().__init__(INTERNAL_ERROR, message)


class McpAuthRequired(McpConnectionError):
    """The server answered 401: it wants credentials before it talks."""

    def __init__(self, message: str, *, resource_metadata: str = "") -> None:
        super().__init__(message)
        self.resource_metadata = resource_metadata


def request(message_id: int | str, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def result(message_id: int | str, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": value}


def error(message_id: int | str | None, failure: McpError) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": failure.payload()}


def is_request(message: dict[str, Any]) -> bool:
    return "method" in message and "id" in message


def is_notification(message: dict[str, Any]) -> bool:
    return "method" in message and "id" not in message


def is_response(message: dict[str, Any]) -> bool:
    return "method" not in message and "id" in message
