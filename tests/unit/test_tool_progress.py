from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.orchestration import _extract_partial_path
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)
from code_ai.ui.terminal.view_models import TerminalViewModel

# --- partial path extraction -------------------------------------------------


def test_extract_partial_path_from_incomplete_json() -> None:
    # Arguments still streaming: path is present but the object is not closed.
    assert _extract_partial_path('{"path": "src/foo.py", "content": "def ') == "src/foo.py"


def test_extract_partial_path_unescapes() -> None:
    assert _extract_partial_path('{"path": "a\\/b.py"') == "a/b.py"


def test_extract_partial_path_missing_returns_none() -> None:
    assert _extract_partial_path('{"content": "no path yet') is None


# --- view model live rendering ----------------------------------------------


def _event(payload: dict[str, object]) -> EventEnvelope:
    return EventEnvelope.create(
        event_type="tool.call.progress", session_id="test", sequence=0, payload=payload
    )


def test_view_model_updates_writing_line_in_place() -> None:
    vm = TerminalViewModel()
    vm.apply(_event({"name": "write_file", "path": "src/foo.py", "lines": 3, "chars": 40}))
    vm.apply(_event({"name": "write_file", "path": "src/foo.py", "lines": 20, "chars": 400}))

    writing = [line for line in vm.conversation if line.startswith("tool~ write_file:")]
    assert len(writing) == 1  # updated in place, not appended twice
    assert writing[0] == "tool~ write_file: writing src/foo.py (20 lines)"


def test_view_model_progress_without_path_shows_chars() -> None:
    vm = TerminalViewModel()
    vm.apply(_event({"name": "execute_command", "chars": 25}))
    assert vm.conversation[-1] == "tool~ execute_command: building call (25 chars)"


# --- end-to-end throttled emission through the orchestrator ------------------


class _WritingProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        base = '{"path": "a.py", "content": "'
        yield ProviderEvent(
            kind="tool_call_delta", tool_call_name="write_file", tool_call_arguments=base
        )
        # One extra char: below the throttle step, must not emit a second update.
        yield ProviderEvent(
            kind="tool_call_delta", tool_call_name="write_file", tool_call_arguments=base + "x"
        )
        # A big jump: crosses the step, so a fresh progress update fires.
        big = base + "line\\n" * 80 + '"'
        yield ProviderEvent(
            kind="tool_call_delta", tool_call_name="write_file", tool_call_arguments=big
        )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


def _config(tmp_path) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
        }
    )


async def test_orchestrator_emits_throttled_progress(tmp_path) -> None:
    app = build_application(config=_config(tmp_path), provider=_WritingProvider())
    envelopes: list[EventEnvelope] = []
    app.subscribe(envelopes.append)

    await app.start()
    await app.submit_user_message("write the file")
    await app.close()

    progress = [e for e in envelopes if e.event_type == "tool.call.progress"]
    # First fragment emits; the +1-char fragment is throttled; the big jump emits.
    assert len(progress) == 2
    assert progress[0].payload["path"] == "a.py"
    assert progress[0].payload["name"] == "write_file"
    assert progress[1].payload["lines"] > progress[0].payload["lines"]
