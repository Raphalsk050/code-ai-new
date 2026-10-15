"""A long task is not cut off by a clock.

Turns died about five minutes in: a model that went quiet for 180 s was
dropped and asked again from scratch, and twenty failed attempts, which an
endpoint restarting or a VPN reconnecting easily spends, ended the turn.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import openai

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig, BudgetConfig
from code_ai.core import orchestration
from code_ai.core.errors import CancellationError, ProviderError, TransientProviderError
from code_ai.providers.base import (
    http_timeout,
    is_unreachable,
    keepalive_socket_options,
    retry_after,
)
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.tools.base import ToolCapability
from code_ai.tools.registry import ToolRegistry

# Taken before the suite's fixture makes every retry immediate.
_REAL_RETRY_DELAY = orchestration._retry_delay


def _config(tmp_path, **overrides) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "fake",
        "permission_mode": "bypass",
        "memories_dir": str(tmp_path / "memories"),
        "memory": {"reflection_enabled": False},
        "planner": {"enabled": False},
    }
    data.update(overrides)
    return AppConfig.from_mapping(data)


class _Provider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


class UnreachableThenAnswers(_Provider):
    def __init__(self, failures: int, error) -> None:
        self.failures = failures
        self.error = error
        self.attempts = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.attempts += 1
        if self.failures < 0 or self.attempts <= self.failures:
            raise self.error()
        yield ProviderEvent(
            kind="completed", response=ModelResponse(text="back", finish_reason=FinishReason.STOP)
        )


def _retries(app) -> list[dict]:
    seen: list[dict] = []
    app.subscribe(
        lambda event: (
            seen.append(event.payload) if event.event_type == "model.request.retrying" else None
        )
    )
    return seen


async def _ask(app, text: str = "Hello"):
    await app.start()
    try:
        return await app.submit_user_message(text)
    finally:
        await app.close()


def test_a_model_may_stay_silent_by_default() -> None:
    assert BudgetConfig().model_timeout() is None
    timeout = http_timeout(AppConfig.from_mapping({"api_mode": "ollama"}))
    assert timeout.read is None
    # Reaching the server and sending to it still fail an attempt that hangs.
    assert timeout.connect == 30.0
    assert timeout.write == 120.0


def test_a_silence_cap_set_in_the_config_still_applies() -> None:
    budgets = BudgetConfig.from_mapping({"max_model_call_s": 300, "max_model_step_seconds": 0})
    assert budgets.model_timeout() == 300.0


async def test_an_unreachable_model_is_waited_out_past_any_retry_count(tmp_path) -> None:
    failures = orchestration._MAX_FAILED_ATTEMPTS + 10
    provider = UnreachableThenAnswers(failures, lambda: TransientProviderError("503"))
    app = build_application(config=_config(tmp_path), provider=provider)
    retries = _retries(app)

    result = await _ask(app)

    assert result.error is None
    assert result.text == "back"
    assert [r["attempt"] for r in retries] == list(range(1, failures + 1))
    # No count to show: the notice says which attempt, not how many are left.
    assert {r["max_attempts"] for r in retries} == {0}


async def test_a_connection_lost_under_the_provider_is_an_outage_too(tmp_path) -> None:
    failures = orchestration._MAX_FAILED_ATTEMPTS + 5
    provider = UnreachableThenAnswers(failures, lambda: httpx.ConnectError("refused"))
    app = build_application(config=_config(tmp_path), provider=provider)

    result = await _ask(app)

    assert result.text == "back"
    assert provider.attempts == failures + 1


async def test_max_model_outage_s_bounds_the_wait_when_it_is_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(orchestration, "_retry_delay", lambda attempt, exc=None: 0.2)
    provider = UnreachableThenAnswers(-1, lambda: TransientProviderError("503"))
    app = build_application(
        config=_config(tmp_path, budgets={"max_model_outage_s": 1}), provider=provider
    )

    result = await _ask(app)

    assert result.error is not None
    assert "max_model_outage_s" in result.error
    assert 4 <= provider.attempts <= 8


async def test_a_refused_request_keeps_its_bound(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(orchestration, "_MAX_FAILED_ATTEMPTS", 3)
    provider = UnreachableThenAnswers(-1, lambda: ProviderError("400 bad request"))
    app = build_application(config=_config(tmp_path), provider=provider)

    result = await _ask(app)

    assert result.error is not None
    assert provider.attempts == 4


class _Dispatch:
    name = "dispatch_agent"
    description = "Runs sub-agents."
    capabilities = frozenset({ToolCapability.DELEGATE})
    input_schema = {"type": "object", "properties": {}, "additionalProperties": True}

    def __init__(self) -> None:
        self.cancelled = False

    async def execute(self, arguments: dict, context) -> dict:
        for _ in range(30):
            if context.cancel_event is not None and context.cancel_event.is_set():
                self.cancelled = True
                raise CancellationError("stopped")
            await asyncio.sleep(0.05)
        return {"reports": []}


class DispatchThenAnswer(_Provider):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            response = ModelResponse(
                tool_calls=[ToolCall(id="d1", name="dispatch_agent", arguments={})],
                finish_reason=FinishReason.TOOL_CALLS,
            )
        else:
            response = ModelResponse(text="merged", finish_reason=FinishReason.STOP)
        yield ProviderEvent(kind="completed", response=response)


async def test_sub_agents_outlast_the_tool_wall_clock(tmp_path) -> None:
    dispatch = _Dispatch()
    app = build_application(
        config=_config(tmp_path, budgets={"max_tool_wall_time_s": 1}),
        provider=DispatchThenAnswer(),
    )
    registry = ToolRegistry()
    registry.register(dispatch)
    app.orchestrator.tool_registry = registry
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    result = await _ask(app, "fan out")

    assert dispatch.cancelled is False
    assert "tool.call.failed" not in events
    assert result.text == "merged"


def _status_error(status: int) -> Exception:
    error = Exception(f"Error code: {status}")
    error.status_code = status
    return error


def test_what_counts_as_unreachable() -> None:
    request = httpx.Request("POST", "http://model.local/v1/chat/completions")
    assert is_unreachable(httpx.ConnectError("refused"))
    assert is_unreachable(httpx.RemoteProtocolError("peer closed connection"))
    assert is_unreachable(ConnectionResetError())
    assert is_unreachable(openai.APIConnectionError(request=request))
    assert is_unreachable(_status_error(503))
    assert is_unreachable(_status_error(429))
    wrapped = ProviderError("Chat Completions request failed")
    wrapped.__cause__ = httpx.ConnectError("refused")
    assert is_unreachable(wrapped)

    assert not is_unreachable(_status_error(400))
    assert not is_unreachable(_status_error(401))
    assert not is_unreachable(ValueError("bad json"))
    # Silent past a cap the user set: bounded like a refusal, not waited out.
    assert not is_unreachable(TimeoutError())
    assert not is_unreachable(httpx.ReadTimeout("silent"))
    silent = openai.APITimeoutError(request=request)
    silent.__cause__ = httpx.ReadTimeout("silent")
    assert not is_unreachable(silent)
    # Not getting through at all is an outage, timed out or not.
    refused = openai.APITimeoutError(request=request)
    refused.__cause__ = httpx.ConnectTimeout("no route")
    assert is_unreachable(refused)


def test_the_server_s_retry_after_is_read() -> None:
    def failing(value: str) -> Exception:
        error = Exception("slow down")
        error.response = SimpleNamespace(headers={"retry-after": value})
        return error

    assert retry_after(failing("7")) == 7.0
    later = datetime.now(UTC) + timedelta(seconds=90)
    assert 80 <= retry_after(failing(format_datetime(later, usegmt=True))) <= 91
    assert retry_after(failing("soon")) is None
    assert retry_after(Exception("no response")) is None

    assert _REAL_RETRY_DELAY(1, failing("45")) >= 45
    assert _REAL_RETRY_DELAY(1, failing("100000")) == orchestration._MAX_RETRY_AFTER_S
    assert _REAL_RETRY_DELAY(30) <= 20.25


def test_a_dead_connection_is_found_by_keepalive() -> None:
    options = keepalive_socket_options()
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in options
    if hasattr(socket, "TCP_KEEPIDLE"):
        assert (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30) in options
