"""Settings that used to need a restart, applied to the running application.

The model name reaches the provider as request data on every call, which is why
changing it always worked live. The API key, the base URL and the API mode are
read once, while the client is being constructed - so applying them means
building a new client and putting it behind the handle everything holds.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from code_ai.app.service import CodeAIApplication
from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.approval import DenyAllGateway
from code_ai.providers.models import (
    Message,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)
from code_ai.providers.ollama import NativeOllamaProvider
from code_ai.providers.openai_completions import OpenAIChatCompletionsProvider
from code_ai.providers.swappable import SwappableProvider


def build(tmp_path: Path, **overrides) -> CodeAIApplication:
    settings = {"api_mode": "ollama", "workspace": str(tmp_path), "model": "m"}
    settings.update(overrides)
    config = AppConfig.from_mapping(settings)
    return CodeAIApplication(
        session=SimpleNamespace(config=config),
        event_bus=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        provider=NativeOllamaProvider(config),
        compressor=SimpleNamespace(max_context_tokens=0),
    )


def test_the_provider_is_always_behind_a_swappable_handle(tmp_path) -> None:
    # Even built directly, rather than through bootstrap: a provider held
    # bare would be one nothing could replace.
    assert isinstance(build(tmp_path).provider, SwappableProvider)


async def test_a_new_base_url_reaches_a_new_client(tmp_path) -> None:
    app = build(tmp_path, base_url="http://localhost:11434")
    before = app.provider.current

    app.session.config.base_url = "http://elsewhere:9999"
    await app.reload_provider()

    after = app.provider.current
    assert after is not before
    assert "elsewhere:9999" in after._base_url


async def test_a_new_api_mode_reaches_a_different_kind_of_client(tmp_path) -> None:
    app = build(tmp_path)
    assert isinstance(app.provider.current, NativeOllamaProvider)

    app.session.config.api_mode = "completions"
    await app.reload_provider()

    assert isinstance(app.provider.current, OpenAIChatCompletionsProvider)


async def test_the_context_window_is_repointed_at_the_compressor(tmp_path) -> None:
    app = build(tmp_path)
    app.session.config.budgets.max_context_tokens = 128000

    app.apply_context_window()

    # The compressor recomputes its budget from this on every check, so the
    # next turn plans against the new window without anything else changing.
    assert app.compressor.max_context_tokens == 128000


# --------------------------------------------------------------------------- #
# Changing the project the session works on
# --------------------------------------------------------------------------- #
def project(root: Path, name: str) -> Path:
    made = root / name
    made.mkdir(parents=True, exist_ok=True)
    return made


async def test_the_session_moves_to_the_new_project(tmp_path) -> None:
    """Everything rooted at the old workspace has to follow, not just the config."""

    old = project(tmp_path, "old")
    new = project(tmp_path, "new")
    app = build_application(config=_config(old), provider=_StubProvider())
    before = app.orchestrator
    conversations_before = app.conversation_store._dir

    await app.retarget_workspace(new)

    assert app.session.config.workspace == new
    # The orchestrator is rebuilt, which is what re-roots the planner, the git
    # baseline and the tool contexts it hands out.
    assert app.orchestrator is not before
    assert app.orchestrator.git_baseline._workspace.root == new
    # Conversations are kept per project, under a directory derived from the
    # workspace, so the store has to land somewhere else than it started.
    assert app.conversation_store._dir != conversations_before
    assert app.conversation_store._dir.name == "conversations"
    assert "new" in app.conversation_store._dir.parent.name


async def test_the_tools_are_allowed_in_the_new_project_and_not_the_old(tmp_path) -> None:
    """The workspace policy is frozen, so a stale one would reject the new tree."""

    old = project(tmp_path, "old")
    new = project(tmp_path, "new")
    app = build_application(config=_config(old), provider=_StubProvider())

    await app.retarget_workspace(new)

    context = app.orchestrator.tool_context_factory(None)
    assert context.workspace.root == new


async def test_the_conversation_survives_the_move(tmp_path) -> None:
    old = project(tmp_path, "old")
    new = project(tmp_path, "new")
    app = build_application(config=_config(old), provider=_StubProvider())
    conversation = app.orchestrator.conversation
    conversation.messages.append(Message(role="user", content="lembre disto"))

    await app.retarget_workspace(new)

    assert app.orchestrator.conversation is conversation
    assert any(m.content == "lembre disto" for m in app.orchestrator.conversation.messages)


async def test_the_approval_state_survives_the_move(tmp_path) -> None:
    """The subtle one: both live on the orchestrator, which the move replaces.

    Losing the gateway would silently leave the session with no approver -
    every gated call denied - and losing the allowlist would re-ask for
    everything the user had already allowed for this session.
    """

    old = project(tmp_path, "old")
    new = project(tmp_path, "new")
    app = build_application(config=_config(old), provider=_StubProvider())
    gateway = DenyAllGateway()
    app.orchestrator.approval_gateway = gateway
    app.orchestrator._session_allowlist.add("execute_command:pytest")

    await app.retarget_workspace(new)

    assert app.orchestrator.approval_gateway is gateway
    assert "execute_command:pytest" in app.orchestrator._session_allowlist


async def test_the_sandbox_is_rebuilt_against_the_new_project(tmp_path) -> None:
    old = project(tmp_path, "old")
    new = project(tmp_path, "new")
    app = build_application(config=_config(old), provider=_StubProvider())
    previous = app.sandbox

    await app.retarget_workspace(new)

    if previous is not None:  # the sandbox is best-effort and may be off
        assert app.sandbox is not previous
        assert not previous.root.exists()  # the old scratch root is cleaned up
        assert app.sandbox is not None
        assert app.sandbox._workspace == new


async def test_the_event_bus_is_kept_so_subscribers_keep_receiving(tmp_path) -> None:
    old = project(tmp_path, "old")
    new = project(tmp_path, "new")
    app = build_application(config=_config(old), provider=_StubProvider())
    bus = app.event_bus
    seen: list[str] = []
    app.subscribe(lambda event: seen.append(event.event_type))

    await app.retarget_workspace(new)

    assert app.event_bus is bus
    assert "session.workspace.changed" in seen


def _config(workspace: Path) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(workspace),
            "model": "fake",
            "memories_dir": str(workspace.parent / "memories"),
        }
    )


class _StubProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest):
        yield ProviderEvent(kind="completed", response=ModelResponse(text=""))

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    async def close(self) -> None:
        return None
