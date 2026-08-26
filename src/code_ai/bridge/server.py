from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Coroutine
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
        # AI analyses (explain/refactor) drive a model call that can take up to
        # the model timeout (180s). Run them off the read loop so the bridge
        # stays responsive — otherwise a slow analysis would stall every other
        # request (a second selection, a cancel) behind it.
        if method in self._CONCURRENT_METHODS:
            task = asyncio.create_task(self._run_and_reply(handler, params, request_id, method))
            self._turn_tasks.add(task)
            task.add_done_callback(self._turn_tasks.discard)
            return
        try:
            result = await handler(self, params)
        except Exception as exc:  # surface handler failures as JSON-RPC errors
            logger.exception("Bridge handler %s failed", method)
            self._send_error(request_id, _INTERNAL_ERROR, str(exc) or type(exc).__name__)
            return
        if request_id is not None:
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _run_and_reply(
        self, handler: Any, params: dict[str, Any], request_id: Any, method: str
    ) -> None:
        try:
            result = await handler(self, params)
        except Exception as exc:
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

    def _start_background_turn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a turn-producing coroutine off the read loop.

        Turns stream their progress as events; acknowledging the RPC right away
        keeps the read loop responsive (e.g. to cancel/approve) while the turn
        runs. A turn awaited inline could even deadlock: an approval prompt
        inside it blocks on the very RPC the frozen loop would never read.
        """
        task = asyncio.create_task(coro)
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)
        # Heartbeat: a reasoning model can go silent for tens of seconds with no
        # events, leaving the UI unable to tell "still working" from "died". A
        # ticking heartbeat from this process is the liveness proof. It is tied to
        # the turn's lifetime (cancelled the moment the turn ends) and kept out of
        # _turn_tasks so it never blocks shutdown or the turn's own completion.
        hb = asyncio.create_task(self._heartbeat(task))
        task.add_done_callback(lambda _: hb.cancel())

    async def _h_submit(self, params: dict[str, Any]) -> dict[str, Any]:
        text = str(params.get("text") or "")
        context = str(params.get("context") or "")
        self._start_background_turn(self._app.submit_user_message(text, context=context))
        return {"status": "accepted"}

    async def _heartbeat(self, turn_task: asyncio.Task[Any]) -> None:
        start = time.monotonic()
        bus = getattr(self._app, "event_bus", None)
        if bus is None:
            return
        try:
            while not turn_task.done() and not self._stop.is_set():
                await asyncio.sleep(2.0)
                if turn_task.done() or self._stop.is_set():
                    break
                await bus.emit(
                    "turn.heartbeat",
                    {"elapsed_s": round(time.monotonic() - start, 1)},
                    source="bridge",
                )
        except asyncio.CancelledError:
            pass

    async def _h_new_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation_id = params.get("conversation_id") or params.get("id")
        await self._app.reset_conversation(
            conversation_id=str(conversation_id) if conversation_id else None
        )
        return {"status": "ok", "conversation_id": self._app.conversation_id}

    async def _h_list_conversations(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"conversations": await self._app.list_conversations()}

    async def _h_load_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(params.get("conversation_id") or params.get("id") or "")
        return await self._app.load_conversation(conversation_id)

    async def _h_delete_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        deleted = await self._app.delete_conversation(
            str(params.get("conversation_id") or params.get("id") or "")
        )
        return {"deleted": deleted}

    async def _h_explain_code(self, params: dict[str, Any]) -> dict[str, Any]:
        markdown = await self._app.explain_code(
            code=str(params.get("code") or ""),
            path=str(params.get("path") or ""),
            language=str(params.get("language") or ""),
        )
        return {"markdown": markdown}

    async def _h_inline_complete(self, params: dict[str, Any]) -> dict[str, Any]:
        completion = await self._app.inline_complete(
            prefix=str(params.get("prefix") or ""),
            suffix=str(params.get("suffix") or ""),
            path=str(params.get("path") or ""),
            language=str(params.get("language") or ""),
        )
        return {"completion": completion}

    async def _h_analyze_refactor(self, params: dict[str, Any]) -> dict[str, Any]:
        improvements = await self._app.analyze_refactor(
            code=str(params.get("code") or ""),
            path=str(params.get("path") or ""),
            language=str(params.get("language") or ""),
        )
        return {"improvements": improvements}

    async def _h_plan_refactor(self, params: dict[str, Any]) -> dict[str, Any]:
        improvements = params.get("improvements")
        markdown = await self._app.plan_refactor(
            code=str(params.get("code") or ""),
            path=str(params.get("path") or ""),
            language=str(params.get("language") or ""),
            improvements=improvements if isinstance(improvements, list) else [],
        )
        return {"markdown": markdown}

    async def _h_get_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._app.get_settings()

    async def _h_list_models(self, params: dict[str, Any]) -> dict[str, Any]:
        models = await self._app.list_models()
        return {"models": models}

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
        # The answer resumes the paused turn, so it runs in the background
        # exactly like a submitted message.
        self._start_background_turn(
            self._app.submit_question_answer(str(params.get("answer") or ""))
        )
        return {"status": "accepted"}

    async def _h_shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        self._stop.set()
        return {"status": "closing"}

    _HANDLERS: dict[str, Any] = {
        "submitUserMessage": _h_submit,
        "newConversation": _h_new_conversation,
        "listConversations": _h_list_conversations,
        "loadConversation": _h_load_conversation,
        "deleteConversation": _h_delete_conversation,
        "getSettings": _h_get_settings,
        "listModels": _h_list_models,
        "updateSettings": _h_update_settings,
        "explainCode": _h_explain_code,
        "inlineComplete": _h_inline_complete,
        "analyzeRefactor": _h_analyze_refactor,
        "planRefactor": _h_plan_refactor,
        "cancel": _h_cancel,
        "compact": _h_compact,
        "setPlannerMode": _h_set_planner_mode,
        "setPermissionMode": _h_set_permission_mode,
        "resolveApproval": _h_resolve_approval,
        "answerQuestion": _h_answer_question,
        "shutdown": _h_shutdown,
    }

    # Methods whose model call may run long; dispatched off the read loop.
    _CONCURRENT_METHODS = frozenset(
        {"explainCode", "inlineComplete", "analyzeRefactor", "planRefactor", "listModels"}
    )


async def run_bridge(app: CodeAIApplication, *, stdin: TextIO, stdout: TextIO) -> int:
    """Serve a single application over stdio JSON-RPC until the client disconnects."""
    return await BridgeServer(app, stdin=stdin, stdout=stdout).run()
