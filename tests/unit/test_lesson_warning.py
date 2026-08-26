from __future__ import annotations

import asyncio

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.memory import FailureMemoryStore
from code_ai.providers.models import ProviderCapabilities


def _config(tmp_path) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
            "memory": {"reflection_enabled": False},
        }
    )


class _IdleProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=True, image_support=False)

    async def stream(self, request):  # noqa: ANN001 - unused in these tests
        raise AssertionError("no model call expected")

    async def complete(self, request):  # noqa: ANN001
        raise AssertionError("no model call expected")


def _orchestrator(tmp_path):
    store = FailureMemoryStore(tmp_path / "memories")
    app = build_application(
        config=_config(tmp_path), provider=_IdleProvider(), failure_memory=store
    )
    return app.orchestrator, store


def _state(orchestrator):
    from code_ai.core.orchestration import _TurnState

    return _TurnState(cancel_event=None, deadline=0.0)


async def _record(store: FailureMemoryStore, tool: str, lesson: str) -> None:
    await store.record(
        trigger="tool_error",
        signature=f"tool_error:{tool}",
        context="ctx",
        fallback_lesson=lesson,
    )


def test_a_tool_with_a_recorded_lesson_is_warned_about(tmp_path) -> None:
    """The whole point: the lesson arrives before the mistake, not after it."""

    orchestrator, store = _orchestrator(tmp_path)
    asyncio.run(_record(store, "edit_code", "Read the file before editing it."))

    warning = orchestrator._lesson_warning(("edit_code",), _state(orchestrator))

    assert warning is not None
    assert "Read the file before editing it." in warning
    assert "edit_code" in warning


def test_a_tool_that_never_failed_produces_no_warning(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path)
    asyncio.run(_record(store, "edit_code", "Read the file first."))

    assert orchestrator._lesson_warning(("read_file",), _state(orchestrator)) is None


def test_the_warning_is_given_once_per_tool_per_turn(tmp_path) -> None:
    """Repeating it every call is how a warning stops being read."""

    orchestrator, store = _orchestrator(tmp_path)
    asyncio.run(_record(store, "edit_code", "Read the file first."))
    state = _state(orchestrator)

    assert orchestrator._lesson_warning(("edit_code",), state) is not None
    assert orchestrator._lesson_warning(("edit_code",), state) is None


def test_each_tool_gets_its_own_warning(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path)
    asyncio.run(_record(store, "edit_code", "Read the file first."))
    asyncio.run(_record(store, "execute_command", "Do not use shell syntax."))
    state = _state(orchestrator)

    first = orchestrator._lesson_warning(("edit_code",), state)
    second = orchestrator._lesson_warning(("execute_command",), state)

    assert "Read the file first." in first
    assert "Do not use shell syntax." in second


def test_the_warning_says_how_often_it_has_already_happened(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path)
    for _ in range(4):
        asyncio.run(_record(store, "edit_code", "Read the file first."))

    warning = orchestrator._lesson_warning(("edit_code",), _state(orchestrator))

    assert "4x" in warning


def test_an_empty_store_warns_about_nothing(tmp_path) -> None:
    orchestrator, _ = _orchestrator(tmp_path)
    assert orchestrator._lesson_warning(("edit_code",), _state(orchestrator)) is None
