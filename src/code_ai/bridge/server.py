from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, TextIO

from code_ai.app.service import CodeAIApplication
from code_ai.bridge.approval import WireApprovalGateway
from code_ai.events.models import EventEnvelope

logger = logging.getLogger(__name__)

# JSON-RPC 2.0 error codes we use.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32000


class BridgeServer:
    """Drives a :class:`CodeAIApplication` over a line-delimited JSON-RPC stream.

    Events emitted on the bus are forwarded to the client as ``event``
    notifications; the client drives the agent by sending JSON-RPC requests that
    map onto the application facade. One JSON object per line, both directions.
    """

    def __init__(self, app: CodeAIApplication, *, stdin: TextIO, stdout: TextIO) -> None:
        self._app = app
        self._stdin = stdin
        self._stdout = stdout
        self._gateway = WireApprovalGateway()
        self._stop = asyncio.Event()
        self._turn_tasks: set[asyncio.Task[Any]] = set()

    async def run(self) -> int:
        # Gated tool calls block on the wire gateway instead of a terminal modal.
        self._app.orchestrator.approval_gateway = self._gateway
        self._app.subscribe(self._forward_event)
        await self._app.start()
        try:
            await self._read_loop()
        finally:
            self._gateway.close()
            for task in list(self._turn_tasks):
                task.cancel()
            await self._app.close()
        return 0

    # -- event egress ------------------------------------------------------

    async def _forward_event(self, event: EventEnvelope) -> None:
        self._send({"jsonrpc": "2.0", "method": "event", "params": event.to_dict()})

    def _send(self, message: dict[str, Any]) -> None:
        # asyncio is single-threaded and every payload is written as one full
        # line, so events and responses never interleave mid-line.
        self._stdout.write(json.dumps(message, default=str) + "\n")
        self._stdout.flush()

    # -- command ingress ---------------------------------------------------

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            line = await loop.run_in_executor(None, self._stdin.readline)
            if line == "":  # EOF: the client closed the pipe.
                break
            line = line.strip()
            if not line:
                continue
            await self._handle_line(line)

    async def _handle_line(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self._send_error(None, _PARSE_ERROR, "Invalid JSON.")
            return
        if not isinstance(message, dict) or "method" not in message:
            self._send_error(message.get("id") if isinstance(message, dict) else None,
                             _INVALID_REQUEST, "Not a JSON-RPC request.")
            return

        request_id = message.get("id")
        method = str(message["method"])
        params = message.get("params") or {}
        if not isinstance(params, dict):
            self._send_error(request_id, _INVALID_REQUEST, "params must be an object.")
            return

        handler = self._HANDLERS.get(method)
        if handler is None:
            self._send_error(request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")
            return
        try:
            result = await handler(self, params)
        except Exception as exc:  # surface handler failures as JSON-RPC errors
            logger.exception("Bridge handler %s failed", method)
            self._send_error(request_id, _INTERNAL_ERROR, str(exc) or type(exc).__name__)
            return
        if request_id is not None:
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _send_error(self, request_id: Any, code: int, message: str) -> None:
        # Notifications (no id) get no error response per JSON-RPC, but a parse
        # error has no id at all, so we still surface it with a null id.
        if request_id is None and code != _PARSE_ERROR:
            return
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    # -- handlers ----------------------------------------------------------

    async def _h_submit(self, params: dict[str, Any]) -> dict[str, Any]:
        text = str(params.get("text") or "")
        context = str(params.get("context") or "")
        # Turns stream their progress as events; accept and run in the
        # background so the read loop stays responsive (e.g. to cancel/approve).
        task = asyncio.create_task(self._app.submit_user_message(text, context=context))
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)
        return {"status": "accepted"}

    async def _h_new_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._app.reset_conversation()
        return {"status": "ok"}

    async def _h_get_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._app.get_settings()

    async def _h_update_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        updates = params.get("updates")
        if not isinstance(updates, dict):
            updates = {k: v for k, v in params.items() if k != "updates"}
        return await self._app.update_settings(updates)

    async def _h_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._app.cancel_current_turn()
        return {"status": "ok"}

    async def _h_compact(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._app.request_context_compression()
        return {
            "compressed": result.compressed,
            "previous_tokens": result.previous_tokens,
            "active_tokens": result.active_tokens,
        }

    async def _h_set_planner_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._app.set_planner_mode(str(params.get("mode") or ""))
        return {"status": "ok"}

    async def _h_set_permission_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._app.set_permission_mode(str(params.get("mode") or ""))
        return {"status": "ok"}

    async def _h_resolve_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        call_id = str(params.get("call_id") or "")
        scope = str(params.get("scope") or "")
        reason = str(params.get("reason") or "")
        resolved = self._gateway.resolve_scope(call_id, scope, reason)
        return {"resolved": resolved}

    async def _h_answer_question(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._app.submit_question_answer(str(params.get("answer") or ""))
        return {"status": "ok"}

    async def _h_shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        self._stop.set()
        return {"status": "closing"}

    _HANDLERS: dict[str, Any] = {
        "submitUserMessage": _h_submit,
        "newConversation": _h_new_conversation,
        "getSettings": _h_get_settings,
        "updateSettings": _h_update_settings,
        "cancel": _h_cancel,
        "compact": _h_compact,
        "setPlannerMode": _h_set_planner_mode,
        "setPermissionMode": _h_set_permission_mode,
        "resolveApproval": _h_resolve_approval,
        "answerQuestion": _h_answer_question,
        "shutdown": _h_shutdown,
    }


async def run_bridge(app: CodeAIApplication, *, stdin: TextIO, stdout: TextIO) -> int:
    """Serve a single application over stdio JSON-RPC until the client disconnects."""
    return await BridgeServer(app, stdin=stdin, stdout=stdout).run()
