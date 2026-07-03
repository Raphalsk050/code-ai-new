from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalScope,
    DenyAllGateway,
    _StaticGateway,
    call_signature,
)
from code_ai.core.planning.policy import PolicyDecision
from code_ai.providers.models import (
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)


class _StubProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(kind="completed", response=ModelResponse(text=""))

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    async def close(self) -> None:
        return None


def _app(tmp_path, permission_mode: str, gateway=None):
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": permission_mode,
        }
    )
    app = build_application(config=config, provider=_StubProvider())
    if gateway is not None:
        app.orchestrator.approval_gateway = gateway
    return app


_DENIED = PolicyDecision(False, "blocked in this phase", set())
_ALLOWED = PolicyDecision(True, "allowed", set())


def _call(name: str = "execute_command", **arguments) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments or {"command": "pip install x"})


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_call_signature_keys_execute_command_on_program() -> None:
    assert (
        call_signature("execute_command", {"command": "pip install rich"})
        == "execute_command:pip"
    )
    assert (
        call_signature("execute_command", {"argv": ["mkdir", "-p", "a"]})
        == "execute_command:mkdir"
    )
    assert call_signature("write_file", {"path": "a.py"}) == "write_file"


def test_render_preview_highlights_by_language() -> None:
    from code_ai.ui.terminal.approval import _render_preview

    meta, body = _render_preview(
        ApprovalRequest(
            "c", "write_file", {"path": "demo.py", "content": "def x():\n    return 1\n"}, "s"
        )
    )
    assert "demo.py" in meta
    assert body.lexer.name == "Python"
    assert "return 1" in body.code


def test_render_preview_uses_language_from_path() -> None:
    from code_ai.ui.terminal.approval import _render_preview

    _, ts = _render_preview(
        ApprovalRequest("c", "edit_code", {"path": "app.ts", "new_text": "const x = 1"}, "s")
    )
    assert ts.lexer.name == "TypeScript"

    # A command renders as a terminal prompt (Text with a "$"), not as code.
    _, cmd = _render_preview(
        ApprovalRequest("c", "execute_command", {"command": "pip install rich"}, "s")
    )
    assert cmd.plain == "$ pip install rich"

    _, args = _render_preview(ApprovalRequest("c", "other_tool", {"foo": 1}, "s"))
    assert args.lexer.name == "JSON"
    assert '"foo"' in args.code


def test_render_preview_shows_diff_for_edit_code_with_old_text() -> None:
    from rich.text import Text

    from code_ai.ui.terminal.approval import _render_preview

    meta, body = _render_preview(
        ApprovalRequest(
            "c",
            "edit_code",
            {
                "path": "app.py",
                "old_text": "def x():\n    return 1\n",
                "new_text": "def x():\n    return 2\n",
            },
            "s",
        )
    )
    assert "app.py" in meta
    assert isinstance(body, Text)
    rendered = body.plain
    assert "-    return 1" in rendered
    assert "+    return 2" in rendered


def test_render_preview_falls_back_to_syntax_without_old_text() -> None:
    from code_ai.ui.terminal.approval import _render_preview

    _, body = _render_preview(
        ApprovalRequest("c", "edit_code", {"path": "app.py", "new_text": "x = 1"}, "s")
    )
    assert body.code == "x = 1"


def test_render_preview_falls_back_to_syntax_when_edit_is_a_noop() -> None:
    from code_ai.ui.terminal.approval import _render_preview

    _, body = _render_preview(
        ApprovalRequest(
            "c",
            "edit_code",
            {"path": "app.py", "old_text": "x = 1", "new_text": "x = 1"},
            "s",
        )
    )
    assert body.code == "x = 1"


def test_render_justification_reads_reason_argument() -> None:
    from code_ai.ui.terminal.approval import _render_justification

    request = ApprovalRequest(
        "c", "write_file", {"path": "a.py", "content": "x", "reason": "  Add the helper.  "}, "s"
    )
    assert _render_justification(request) == "Add the helper."


def test_render_justification_is_empty_when_reason_missing() -> None:
    from code_ai.ui.terminal.approval import _render_justification

    request = ApprovalRequest("c", "write_file", {"path": "a.py", "content": "x"}, "s")
    assert _render_justification(request) == ""


async def test_approval_modal_shows_justification_when_learn_enabled() -> None:
    from textual.app import App
    from textual.widgets import Static

    from code_ai.ui.terminal.approval import ApprovalModal

    request = ApprovalRequest(
        "c",
        "write_file",
        {"path": "a.py", "content": "x", "reason": "Add the helper."},
        "s",
    )

    class _HostApp(App):
        async def on_mount(self) -> None:
            await self.push_screen(ApprovalModal(request, learn_enabled=True))

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        justification = app.screen.query_one("#approval-justification", Static)
        assert "Add the helper." in str(justification.render())


async def test_approval_modal_hides_justification_when_learn_disabled() -> None:
    from textual.app import App

    from code_ai.ui.terminal.approval import ApprovalModal

    request = ApprovalRequest(
        "c",
        "write_file",
        {"path": "a.py", "content": "x", "reason": "Add the helper."},
        "s",
    )

    class _HostApp(App):
        async def on_mount(self) -> None:
            await self.push_screen(ApprovalModal(request, learn_enabled=False))

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.screen.query("#approval-justification")) == 0


def test_render_preview_survives_unknown_language() -> None:
    from code_ai.ui.terminal.approval import _render_preview

    # An extension Pygments has no lexer for must not raise.
    meta, body = _render_preview(
        ApprovalRequest("c", "write_file", {"path": "data.zzz", "content": "anything"}, "s")
    )
    assert body.code == "anything"


def test_approval_decision_flags() -> None:
    assert ApprovalDecision.allow_once().approved is True
    assert ApprovalDecision.allow_once().remember is False
    assert ApprovalDecision.allow_session().remember is True
    assert ApprovalDecision.deny().approved is False
    assert ApprovalDecision.deny().scope is ApprovalScope.DENY


# --------------------------------------------------------------------------- #
# Orchestrator authorization
# --------------------------------------------------------------------------- #
async def test_ask_mode_prompts_and_allow_once_overrides_policy_denial(tmp_path) -> None:
    gateway = _StaticGateway(ApprovalDecision.allow_once())
    app = _app(tmp_path, "ask", gateway)
    auth = await app.orchestrator._authorize_call(_call(), _DENIED, None)
    assert auth.allowed is True
    assert auth.overrode_policy is True
    assert len(gateway.requests) == 1
    # allow_once does not persist; the signature is not remembered.
    assert app.orchestrator._session_allowlist == set()


async def test_allow_session_is_remembered_and_skips_second_prompt(tmp_path) -> None:
    gateway = _StaticGateway(ApprovalDecision.allow_session())
    app = _app(tmp_path, "ask", gateway)
    first = await app.orchestrator._authorize_call(_call(), _DENIED, None)
    assert first.allowed is True
    assert "execute_command:pip" in app.orchestrator._session_allowlist
    second = await app.orchestrator._authorize_call(_call(), _DENIED, None)
    assert second.allowed is True
    # The gateway was only consulted once; the second call used the allowlist.
    assert len(gateway.requests) == 1


async def test_deny_keeps_tool_blocked(tmp_path) -> None:
    gateway = _StaticGateway(ApprovalDecision.deny("nope"))
    app = _app(tmp_path, "ask", gateway)
    auth = await app.orchestrator._authorize_call(_call(), _ALLOWED, None)
    assert auth.allowed is False
    assert "nope" in auth.reason


async def test_ask_mode_does_not_prompt_for_read_only_allowed_tool(tmp_path) -> None:
    gateway = _StaticGateway(ApprovalDecision.deny())
    app = _app(tmp_path, "ask", gateway)
    auth = await app.orchestrator._authorize_call(_call("list_files", path="."), _ALLOWED, None)
    assert auth.allowed is True
    assert gateway.requests == []


async def test_auto_mode_runs_allowed_sensitive_tool_without_prompt(tmp_path) -> None:
    gateway = _StaticGateway(ApprovalDecision.deny())
    app = _app(tmp_path, "auto", gateway)
    auth = await app.orchestrator._authorize_call(_call(), _ALLOWED, None)
    assert auth.allowed is True
    assert gateway.requests == []


async def test_auto_mode_escalates_policy_denial_to_user(tmp_path) -> None:
    gateway = _StaticGateway(ApprovalDecision.allow_once())
    app = _app(tmp_path, "auto", gateway)
    auth = await app.orchestrator._authorize_call(_call(), _DENIED, None)
    assert auth.allowed is True
    assert len(gateway.requests) == 1


async def test_bypass_mode_never_prompts(tmp_path) -> None:
    gateway = _StaticGateway(ApprovalDecision.deny())
    app = _app(tmp_path, "bypass", gateway)
    auth = await app.orchestrator._authorize_call(_call(), _DENIED, None)
    assert auth.allowed is True
    assert gateway.requests == []


async def test_deny_all_gateway_blocks_when_no_ui(tmp_path) -> None:
    app = _app(tmp_path, "ask", DenyAllGateway())
    auth = await app.orchestrator._authorize_call(_call(), _DENIED, None)
    assert auth.allowed is False
