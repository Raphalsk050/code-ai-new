"""A server asking the user something mid-call (``elicitation/create``).

The server sends a message and a flat JSON schema - strings, numbers,
booleans, enums - and waits. The question goes out as an event both front ends
render (a form card in the panel, ``/mcp answer`` in the terminal) and the tool
call waits for the answer, the way it waits for an approval.

A session nobody is watching (headless, no approver attached) declines at once
rather than leaving the server hanging until it times out.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from code_ai.events.bus import AsyncEventBus

_TRUE = {"true", "yes", "y", "1", "on", "sim", "s"}
_FALSE = {"false", "no", "n", "0", "off", "nao", "não"}


class ElicitationBroker:
    def __init__(
        self,
        *,
        event_bus: AsyncEventBus,
        interactive: Callable[[], bool],
        timeout_s: float = 600.0,
    ) -> None:
        self._bus = event_bus
        self._interactive = interactive
        self._timeout_s = timeout_s
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)

    async def __call__(self, server: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._interactive():
            return {"action": "decline"}
        schema = params.get("requestedSchema")
        schema = schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
        request_id = f"mcp-ask-{next(self._ids)}"
        request = {
            "id": request_id,
            "server": server,
            "message": str(params.get("message") or ""),
            "fields": fields_of(schema),
        }
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._requests[request_id] = request
        await self._bus.emit("mcp.elicitation.requested", request, source="mcp")
        try:
            answer = await asyncio.wait_for(future, self._timeout_s)
        except TimeoutError:
            answer = {"action": "cancel"}
        finally:
            self._pending.pop(request_id, None)
            self._requests.pop(request_id, None)
        await self._bus.emit(
            "mcp.elicitation.resolved",
            {"id": request_id, "server": server, "action": answer["action"]},
            source="mcp",
        )
        return answer

    def pending(self) -> list[dict[str, Any]]:
        return list(self._requests.values())

    def answer(self, request_id: str, action: str, content: Any = None) -> str:
        """Settle a question. Returns why it could not be, or "" when it was.

        An answer that does not fit the schema leaves the question open, so the
        user can correct it instead of starting over.
        """

        future = self._pending.get(request_id)
        request = self._requests.get(request_id)
        if future is None or request is None or future.done():
            return "No question is waiting under that id."
        if action == "accept":
            try:
                values = coerce(request["fields"], content if isinstance(content, dict) else {})
            except ValueError as exc:
                return str(exc)
            future.set_result({"action": "accept", "content": values})
        elif action in ("decline", "cancel"):
            future.set_result({"action": action})
        else:
            return f"Unknown action {action!r}: use accept, decline or cancel."
        return ""

    def close(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_result({"action": "cancel"})


def fields_of(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """The schema's properties as form fields, in their declared order."""

    required = {str(name) for name in schema.get("required") or []}
    fields: list[dict[str, Any]] = []
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for name, raw in properties.items():
        prop = raw if isinstance(raw, dict) else {}
        kind = str(prop.get("type") or "string")
        field: dict[str, Any] = {
            "name": str(name),
            "title": str(prop.get("title") or name),
            "description": str(prop.get("description") or ""),
            "type": kind if kind in ("string", "number", "integer", "boolean") else "string",
            "required": str(name) in required,
        }
        if isinstance(prop.get("enum"), list):
            field["enum"] = [str(value) for value in prop["enum"]]
            names = prop.get("enumNames")
            if isinstance(names, list) and len(names) == len(field["enum"]):
                field["enum_names"] = [str(value) for value in names]
        for key in ("format", "minimum", "maximum", "minLength", "maxLength", "default"):
            if key in prop:
                field[key] = prop[key]
        fields.append(field)
    return fields


def coerce(fields: list[dict[str, Any]], content: dict[str, Any]) -> dict[str, Any]:
    """The answer, typed the way the schema asks, or a ValueError naming what is wrong."""

    values: dict[str, Any] = {}
    for field in fields:
        name = field["name"]
        raw = content.get(name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if field["required"]:
                raise ValueError(f"{field['title']} is required.")
            continue
        kind = field["type"]
        try:
            if kind == "boolean":
                value: Any = _boolean(raw)
            elif kind == "integer":
                number = float(raw)
                if not number.is_integer():
                    raise ValueError
                value = int(number)
            elif kind == "number":
                value = float(raw)
            else:
                value = str(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{field['title']} must be a {kind}.") from None
        if "enum" in field and str(value) not in field["enum"]:
            raise ValueError(f"{field['title']} must be one of {', '.join(field['enum'])}.")
        if kind in ("number", "integer"):
            if "minimum" in field and value < field["minimum"]:
                raise ValueError(f"{field['title']} must be at least {field['minimum']}.")
            if "maximum" in field and value > field["maximum"]:
                raise ValueError(f"{field['title']} must be at most {field['maximum']}.")
        if kind == "string":
            if "minLength" in field and len(value) < field["minLength"]:
                raise ValueError(f"{field['title']} needs {field['minLength']} characters or more.")
            if "maxLength" in field and len(value) > field["maxLength"]:
                raise ValueError(f"{field['title']} takes {field['maxLength']} characters at most.")
        values[name] = value
    return values


def _boolean(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError
