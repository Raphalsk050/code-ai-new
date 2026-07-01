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
        self.submitted_context: list[str] = []
        self.reset_count = 0
        self.reset_ids: list[str | None] = []
        self.conversation_id: str | None = None
        self.loaded_id: str | None = None
        self.deleted_id: str | None = None
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

    async def submit_user_message(self, text: str, *, context: str = "") -> None:
        self.submitted.append(text)
        self.submitted_context.append(context)

    async def reset_conversation(self, conversation_id: str | None = None) -> None:
        self.reset_count += 1
        self.reset_ids.append(conversation_id)
        self.conversation_id = conversation_id or "generated"

    async def list_conversations(self) -> list:
        return [{"id": "c1", "title": "T", "updated_at": 1.0, "message_count": 2}]

    async def load_conversation(self, conversation_id: str) -> dict:
        self.loaded_id = conversation_id
        return {
            "id": conversation_id,
            "title": "T",
            "messages": [{"role": "user", "content": "hi"}],
        }

    async def delete_conversation(self, conversation_id: str) -> bool:
        self.deleted_id = conversation_id
        return True

    async def explain_code(self, *, code: str, path: str = "", language: str = "") -> str:
        self.explained = {"code": code, "path": path, "language": language}
        return f"explained:{code}"

    async def inline_complete(
        self, *, prefix: str, suffix: str = "", path: str = "", language: str = ""
    ) -> str:
        self.inline = {"prefix": prefix, "suffix": suffix, "path": path, "language": language}
        return f"done:{prefix}"

    async def analyze_refactor(self, *, code: str, path: str = "", language: str = "") -> list:
        self.analyzed = {"code": code, "path": path, "language": language}
        return [{"id": "x", "title": "T", "rationale": "R", "impact": "high"}]

    async def plan_refactor(self, *, code: str, path: str = "", language: str = "", improvements: list) -> str:
        self.planned = {"code": code, "improvements": improvements}
        return "# plan"

    def get_settings(self) -> dict:
        return {"model": "m", "api_key_set": False}

    async def update_settings(self, updates: dict) -> dict:
        self.updated_settings = updates
        return {"applied": list(updates), "restart_required": [], "errors": {}, "settings": {"model": "m"}}

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
    assert app.submitted_context == [""]
    (response,) = _messages(out)
    assert response["id"] == 7
    assert response["result"] == {"status": "accepted"}


async def test_submit_forwards_editor_context() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 8, "method": "submitUserMessage",
                    "params": {"text": "explain", "context": "[Editor context] foo.py"}})
    )
    await asyncio.gather(*server._turn_tasks)

    assert app.submitted == ["explain"]
    assert app.submitted_context == ["[Editor context] foo.py"]


async def test_new_conversation_resets_facade() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 9, "method": "newConversation",
                    "params": {"conversation_id": "c-42"}})
    )

    assert app.reset_count == 1
    assert app.reset_ids == ["c-42"]
    (response,) = _messages(out)
    assert response["id"] == 9
    assert response["result"] == {"status": "ok", "conversation_id": "c-42"}


async def test_list_conversations_returns_metadata() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 20, "method": "listConversations", "params": {}})
    )
    (response,) = _messages(out)
    assert response["result"]["conversations"][0]["id"] == "c1"


async def test_load_conversation_returns_messages() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 21, "method": "loadConversation",
                    "params": {"conversation_id": "c1"}})
    )
    assert app.loaded_id == "c1"
    (response,) = _messages(out)
    assert response["result"]["id"] == "c1"
    assert response["result"]["messages"][0]["content"] == "hi"


async def test_delete_conversation_forwards_id() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 22, "method": "deleteConversation",
                    "params": {"conversation_id": "c1"}})
    )
    assert app.deleted_id == "c1"
    (response,) = _messages(out)
    assert response["result"] == {"deleted": True}


async def test_get_settings_returns_snapshot() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 11, "method": "getSettings", "params": {}})
    )
    (response,) = _messages(out)
    assert response["id"] == 11
    assert response["result"] == {"model": "m", "api_key_set": False}


async def test_update_settings_forwards_updates() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 12, "method": "updateSettings",
                    "params": {"updates": {"model": "gpt"}}})
    )
    assert app.updated_settings == {"model": "gpt"}
    (response,) = _messages(out)
    assert response["id"] == 12
    assert response["result"]["applied"] == ["model"]


async def test_explain_code_returns_markdown() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 13, "method": "explainCode",
                    "params": {"code": "x = 1", "path": "a.py", "language": "python"}})
    )
    await asyncio.gather(*server._turn_tasks)  # AI handlers reply off the read loop
    assert app.explained == {"code": "x = 1", "path": "a.py", "language": "python"}
    (response,) = _messages(out)
    assert response["id"] == 13
    assert response["result"] == {"markdown": "explained:x = 1"}


async def test_inline_complete_returns_completion() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 30, "method": "inlineComplete",
                    "params": {"prefix": "def add(a, b):\n    return ", "suffix": "\n",
                               "path": "m.py", "language": "python"}})
    )
    await asyncio.gather(*server._turn_tasks)  # AI handlers reply off the read loop
    assert app.inline["prefix"] == "def add(a, b):\n    return "
    (response,) = _messages(out)
    assert response["id"] == 30
    assert response["result"] == {"completion": "done:def add(a, b):\n    return "}


async def test_analyze_refactor_returns_improvements() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 14, "method": "analyzeRefactor",
                    "params": {"code": "def f(): pass", "path": "a.py", "language": "python"}})
    )
    await asyncio.gather(*server._turn_tasks)
    (response,) = _messages(out)
    assert response["id"] == 14
    assert response["result"]["improvements"][0]["title"] == "T"


async def test_plan_refactor_returns_markdown() -> None:
    app = _StubApp()
    server, out = _server(app)
    await server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 15, "method": "planRefactor",
                    "params": {"code": "x", "improvements": [{"title": "T"}]}})
    )
    await asyncio.gather(*server._turn_tasks)
    assert app.planned["improvements"] == [{"title": "T"}]
    (response,) = _messages(out)
    assert response["result"] == {"markdown": "# plan"}


def test_parse_improvements_handles_fenced_and_prose() -> None:
    from code_ai.app.service import _parse_improvements

    text = (
        "Sure! Here are the improvements:\n```json\n"
        '[{"id": "a", "title": "Extract helper", "rationale": "why", "impact": "HIGH"},'
        ' {"title": ""}, {"id": "b", "title": "Rename", "impact": "weird"}]\n```'
    )
    out = _parse_improvements(text)
    assert [i["id"] for i in out] == ["a", "b"]
    assert out[0]["impact"] == "high"  # normalized lower-case
    assert out[1]["impact"] == "medium"  # invalid impact falls back


def test_parse_improvements_empty_on_garbage() -> None:
    from code_ai.app.service import _parse_improvements

    assert _parse_improvements("no json here") == []


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
