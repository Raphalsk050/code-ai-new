"""A turn stopped while it waits for an approval does not keep waiting.

The stop only raised the turn's flag and the prompt never looked at it: the
turn sat on a question nobody would answer, its card and diff stayed on
screen, and the next message queued behind it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.approval import ApprovalDecision, ApprovalRequest
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)


class _NeverAnswers:
    """A prompt the user walks away from."""

    def __init__(self) -> None:
        self.asked = asyncio.Event()
        self.withdrawn = False

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        self.asked.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.withdrawn = True
            raise
        return ApprovalDecision.allow_once()


class _WritesAFile:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="w1", name="write_file", arguments={"path": "a.txt", "content": "x"}
                    )
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_stopping_a_turn_withdraws_the_question_it_was_waiting_on(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "ask",
            "memories_dir": str(tmp_path / "memories"),
            "planner": {"enabled": False},
            "memory": {"reflection_enabled": False},
        }
    )
    provider = _WritesAFile()
    app = build_application(config=config, provider=provider)
    gateway = _NeverAnswers()
    app.orchestrator.approval_gateway = gateway
    events: list[tuple[str, dict]] = []
    app.subscribe(lambda event: events.append((event.event_type, dict(event.payload))))

    await app.start()
    turn = asyncio.create_task(app.submit_user_message("write a.txt"))
    await asyncio.wait_for(gateway.asked.wait(), timeout=5)
    await app.cancel_current_turn()
    result = await asyncio.wait_for(turn, timeout=5)
    await app.close()

    assert result.cancelled
    assert gateway.withdrawn, "the prompt was left waiting"
    resolved = [payload for kind, payload in events if kind == "tool.approval.resolved"]
    assert resolved and resolved[0]["call_id"] == "w1" and resolved[0]["approved"] is False
    assert not (tmp_path / "a.txt").exists()
    assert provider.calls == 1, "the stopped turn asked the model again"
