from __future__ import annotations

import asyncio
import inspect
import io
import json
from types import SimpleNamespace

from code_ai.bridge.approval import WireApprovalGateway
from code_ai.bridge.server import BridgeServer
from code_ai.context.compression import CompressionResult
from code_ai.core.approval import ApprovalRequest, ApprovalScope
from code_ai.events.models import EventEnvelope


def _request(call_id: str = "c1") -> ApprovalRequest:
    return ApprovalRequest(
        call_id=call_id, tool_name="execute_command", arguments={}, signature="execute_command"
    )


# -- WireApprovalGateway ---------------------------------------------------


async def test_gateway_resolve_releases_with_scope() -> None:
    gateway = WireApprovalGateway()
    pending = asyncio.create_task(gateway.request_approval(_request()))
    await asyncio.sleep(0)  # let request_approval park its future

    assert gateway.resolve_scope("c1", ApprovalScope.SESSION) is True
    decision = await pending
    assert decision.approved is True
    assert decision.remember is True


async def test_gateway_resolve_unknown_call_returns_false() -> None:
    gateway = WireApprovalGateway()
    assert gateway.resolve_scope("nope", ApprovalScope.ONCE) is False


async def test_gateway_close_denies_pending() -> None:
    gateway = WireApprovalGateway()
    pending = asyncio.create_task(gateway.request_approval(_request()))
    await asyncio.sleep(0)

    gateway.close()
    decision = await pending
    assert decision.approved is False
    assert "bridge closed" in decision.reason


# -- BridgeServer dispatch -------------------------------------------------


class _StubApp:
    def __init__(self) -> None:
        self._subs: list = []
        self.orchestrator = SimpleNamespace(approval_gateway=None)
        self.started = False
        self.closed = False
        self.submitted: list[str] = []
        self.planner_mode: str | None = None
        self.permission_mode: str | None = None

    def subscribe(self, fn):
        self._subs.append(fn)
        return fn

    async def emit(self, event: EventEnvelope) -> None:
        for fn in self._subs:
            result = fn(event)
            if inspect.isawaitable(result):
                await result

    async def start(self) -> None:
        self.started = True

    async def submit_user_message(self, text: str) -> None:
        self.submitted.append(text)

    async def request_context_compression(self) -> CompressionResult:
        return CompressionResult(
            compressed=True, active_tokens=10, estimated=False, previous_tokens=30
        )

    async def set_planner_mode(self, mode: str) -> None:
        self.planner_mode = mode

    async def set_permission_mode(self, mode: str) -> None:
        self.permission_mode = mode

    async def close(self) -> None:
        self.closed = True


def _server(app: _StubApp) -> tuple[BridgeServer, io.StringIO]:
    out = io.StringIO()
    server = BridgeServer(app, stdin=io.StringIO(), stdout=out)
    return server, out


def _messages(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line]


async def test_forward_event_wraps_envelope_as_notification() -> None:
    server, out = _server(_StubApp())
    event = EventEnvelope.create(event_type="user.message", session_id="s", sequence=1,
                                 payload={"text": "hi"})
    await server._forward_event(event)

    (msg,) = _messages(out)
    assert msg["method"] == "event"
    assert msg["params"]["event_type"] == "user.message"
    assert msg["params"]["payload"]["text"] == "hi"


async def test_submit_dispatches_to_facade_and_acks() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "submitUserMessage",
                    "params": {"text": "liste os arquivos"}})
    )
    # The turn runs as a background task; let it start.
    await asyncio.gather(*server._turn_tasks)

    assert app.submitted == ["liste os arquivos"]
    (response,) = _messages(out)
    assert response["id"] == 7
    assert response["result"] == {"status": "accepted"}


async def test_resolve_approval_releases_pending() -> None:
    app = _StubApp()
    server, out = _server(app)
    gateway = server._gateway
    pending = asyncio.create_task(gateway.request_approval(_request("call-9")))
    await asyncio.sleep(0)

    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resolveApproval",
                    "params": {"call_id": "call-9", "scope": "once"}})
    )
    decision = await pending
    assert decision.approved is True
    assert _messages(out)[0]["result"] == {"resolved": True}


async def test_compact_returns_token_counts() -> None:
    server, out = _server(_StubApp())
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "compact", "params": {}})
    )
    result = _messages(out)[0]["result"]
    assert result == {"compressed": True, "previous_tokens": 30, "active_tokens": 10}


async def test_unknown_method_returns_error() -> None:
    server, out = _server(_StubApp())
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "doesNotExist", "params": {}})
    )
    error = _messages(out)[0]["error"]
    assert error["code"] == -32601


async def test_invalid_json_reports_parse_error() -> None:
    server, out = _server(_StubApp())
    await server._handle_line("{not json")
    error = _messages(out)[0]["error"]
    assert error["code"] == -32700


async def test_notification_without_id_gets_no_response() -> None:
    app = _StubApp()
    server, out = _server(app)
    # No "id" -> a notification; cancel still runs but nothing is written back.
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "method": "setPlannerMode", "params": {"mode": "act"}})
    )
    assert app.planner_mode == "act"
    assert out.getvalue() == ""


async def test_run_starts_installs_gateway_and_closes_on_eof() -> None:
    app = _StubApp()
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "method": "setPermissionMode", "params": {"mode": "auto"}})
        + "\n"
    )
    server = BridgeServer(app, stdin=stdin, stdout=io.StringIO())
    code = await server.run()

    assert code == 0
    assert app.started is True
    assert app.closed is True
    assert app.permission_mode == "auto"
    assert isinstance(app.orchestrator.approval_gateway, WireApprovalGateway)
