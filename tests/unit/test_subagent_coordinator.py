from __future__ import annotations

import asyncio

from code_ai.config.models import AppConfig
from code_ai.core.orchestration import TurnResult
from code_ai.core.subagents.coordinator import SubagentCoordinator, SubagentRequest
from code_ai.core.subagents.profiles import default_profile_registry
from code_ai.core.subagents.report import SubagentStatus
from code_ai.core.subagents.runtime import BuiltSubagent
from code_ai.events.bus import AsyncEventBus


def _config(tmp_path, **budgets) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "fake",
        "permission_mode": "bypass",
        "memories_dir": str(tmp_path / "memories"),
    }
    if budgets:
        data["budgets"] = budgets
    return AppConfig.from_mapping(data)


class _FakeOrchestrator:
    """Stand-in orchestrator whose run_turn behaviour is scripted per test."""

    def __init__(self, behaviour) -> None:
        self._behaviour = behaviour
        self.usage = _FakeUsage()

    async def run_turn(self, prompt, *, cancel_event=None, **_kwargs) -> TurnResult:
        return await self._behaviour(prompt, cancel_event)


class _FakeUsage:
    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


class _FakeRuntime:
    """Builds fake sub-agents, recording how many were built concurrently."""

    def __init__(self, behaviour, *, timeout_seconds: int = 5) -> None:
        self._behaviour = behaviour
        self._timeout = timeout_seconds
        self.built = 0
        self.live = 0
        self.peak = 0

    def build(self, profile) -> BuiltSubagent:
        self.built += 1
        return BuiltSubagent(
            orchestrator=_FakeOrchestrator(self._wrap()),
            event_bus=AsyncEventBus(),
            usage=_FakeUsage(),
            timeout_seconds=self._timeout,
        )

    def _wrap(self):
        async def behaviour(prompt, cancel_event):
            self.live += 1
            self.peak = max(self.peak, self.live)
            try:
                return await self._behaviour(prompt, cancel_event)
            finally:
                self.live -= 1

        return behaviour


def _coordinator(tmp_path, runtime, **kwargs) -> SubagentCoordinator:
    config = kwargs.pop("config", None) or _config(tmp_path)
    return SubagentCoordinator(
        runtime=runtime,
        profile_registry=default_profile_registry(),
        event_bus=kwargs.pop("event_bus", AsyncEventBus()),
        config=config,
        hard_grace_seconds=kwargs.pop("hard_grace_seconds", 5.0),
        **kwargs,
    )


async def test_completed_run_maps_to_report(tmp_path) -> None:
    async def behaviour(prompt, _cancel):
        return TurnResult(text=f"done: {prompt}", response=None)

    runtime = _FakeRuntime(behaviour)
    coord = _coordinator(tmp_path, runtime)
    reports = await coord.dispatch([SubagentRequest("explorer", "find X")])

    assert len(reports) == 1
    assert reports[0].status is SubagentStatus.COMPLETED
    assert reports[0].summary == "done: find X"
    assert reports[0].agent_type == "explorer"


async def test_agent_gets_a_name_used_in_report_and_events(tmp_path) -> None:
    import re

    async def behaviour(prompt, _cancel):
        return TurnResult(text="ok", response=None)

    runtime = _FakeRuntime(behaviour)
    bus = AsyncEventBus()
    seen: list[tuple[str, str]] = []
    bus.subscribe(
        lambda e: seen.append((e.event_type, str(e.payload.get("name"))))
        if e.event_type.startswith("subagent.")
        else None
    )
    coord = _coordinator(tmp_path, runtime, event_bus=bus)
    reports = await coord.dispatch([SubagentRequest("explorer", "find X")])

    name = reports[0].name
    assert re.match(r"^[a-z]+-[a-z]+-[a-z]+$", name), name
    # The same name is carried on the lifecycle events for this agent.
    started = next(n for et, n in seen if et == "subagent.started")
    completed = next(n for et, n in seen if et == "subagent.completed")
    assert started == name
    assert completed == name
    # It also survives serialization for the model.
    assert reports[0].to_dict()["name"] == name


async def test_parallel_fan_out_runs_concurrently(tmp_path) -> None:
    gate = asyncio.Event()
    started = 0

    async def behaviour(prompt, _cancel):
        nonlocal started
        started += 1
        if started >= 3:
            gate.set()
        await gate.wait()  # all three must be in-flight to pass
        return TurnResult(text=prompt, response=None)

    runtime = _FakeRuntime(behaviour)
    coord = _coordinator(tmp_path, runtime)
    requests = [
        SubagentRequest("explorer", "a"),
        SubagentRequest("explorer", "b"),
        SubagentRequest("explorer", "c"),
    ]
    reports = await asyncio.wait_for(coord.dispatch(requests), timeout=5)

    assert {r.summary for r in reports} == {"a", "b", "c"}
    assert runtime.peak == 3  # genuinely overlapped


async def test_concurrency_is_bounded_by_config(tmp_path) -> None:
    release = asyncio.Event()

    async def behaviour(prompt, _cancel):
        await release.wait()
        return TurnResult(text=prompt, response=None)

    runtime = _FakeRuntime(behaviour)
    config = _config(tmp_path, max_concurrent_subagents=2)
    coord = _coordinator(tmp_path, runtime, config=config)

    task = asyncio.ensure_future(
        coord.dispatch([SubagentRequest("explorer", str(i)) for i in range(5)])
    )
    await asyncio.sleep(0.05)
    assert runtime.peak <= 2  # never more than the configured ceiling in flight
    release.set()
    reports = await asyncio.wait_for(task, timeout=5)
    assert len(reports) == 5


async def test_unknown_type_is_rejected(tmp_path) -> None:
    runtime = _FakeRuntime(lambda p, c: None)
    coord = _coordinator(tmp_path, runtime)
    reports = await coord.dispatch([SubagentRequest("wizard", "cast a spell")])

    assert reports[0].status is SubagentStatus.REJECTED
    assert "Unknown sub-agent type" in reports[0].error
    assert runtime.built == 0  # never built


async def test_depth_limit_rejects_nested_delegation(tmp_path) -> None:
    async def behaviour(prompt, _cancel):
        return TurnResult(text="ok", response=None)

    runtime = _FakeRuntime(behaviour)
    coord = _coordinator(tmp_path, runtime)  # default max_subagent_depth == 1
    reports = await coord.dispatch([SubagentRequest("explorer", "x")], depth=1)

    assert reports[0].status is SubagentStatus.REJECTED
    assert "depth" in reports[0].error.lower()
    assert runtime.built == 0


async def test_per_turn_cap_refuses_overflow(tmp_path) -> None:
    async def behaviour(prompt, _cancel):
        return TurnResult(text=prompt, response=None)

    runtime = _FakeRuntime(behaviour)
    config = _config(tmp_path, max_subagents_per_turn=2)
    coord = _coordinator(tmp_path, runtime, config=config)
    reports = await coord.dispatch(
        [SubagentRequest("explorer", str(i)) for i in range(4)]
    )

    completed = [r for r in reports if r.status is SubagentStatus.COMPLETED]
    rejected = [r for r in reports if r.status is SubagentStatus.REJECTED]
    assert len(completed) == 2
    assert len(rejected) == 2
    assert all("limit" in r.error.lower() for r in rejected)


async def test_timeout_yields_timeout_report_without_crashing(tmp_path) -> None:
    async def behaviour(prompt, cancel_event):
        # Ignore the cooperative cancel and hang; the hard backstop must fire.
        await asyncio.sleep(30)
        return TurnResult(text="never", response=None)

    runtime = _FakeRuntime(behaviour, timeout_seconds=0)
    coord = _coordinator(tmp_path, runtime, hard_grace_seconds=0.2)
    reports = await asyncio.wait_for(
        coord.dispatch([SubagentRequest("coder", "slow task")]), timeout=5
    )

    assert reports[0].status is SubagentStatus.TIMEOUT
    assert "time budget" in reports[0].error


async def test_wound_down_time_budget_maps_to_timeout(tmp_path) -> None:
    from code_ai.core.orchestration import WIND_DOWN_TIME_BUDGET

    async def behaviour(prompt, _cancel):
        return TurnResult(
            text="partial findings", response=None, wind_down_reason=WIND_DOWN_TIME_BUDGET
        )

    coord = _coordinator(tmp_path, _FakeRuntime(behaviour))
    reports = await coord.dispatch([SubagentRequest("explorer", "x")])

    assert reports[0].status is SubagentStatus.TIMEOUT
    assert reports[0].summary == "partial findings"  # best-effort text preserved
    assert "time budget" in reports[0].error


async def test_wound_down_stall_maps_to_failed_not_completed(tmp_path) -> None:
    async def behaviour(prompt, _cancel):
        return TurnResult(text="kept repeating", response=None, wind_down_reason="model_stalled")

    coord = _coordinator(tmp_path, _FakeRuntime(behaviour))
    reports = await coord.dispatch([SubagentRequest("explorer", "x")])

    assert reports[0].status is SubagentStatus.FAILED
    assert "model stalled" in reports[0].error
    assert reports[0].summary == "kept repeating"


async def test_empty_final_answer_maps_to_failed(tmp_path) -> None:
    async def behaviour(prompt, _cancel):
        return TurnResult(text="  ", response=None)

    coord = _coordinator(tmp_path, _FakeRuntime(behaviour))
    reports = await coord.dispatch([SubagentRequest("explorer", "x")])

    assert reports[0].status is SubagentStatus.FAILED
    assert "final answer" in reports[0].error


async def test_failed_run_is_retried_with_a_fresh_subagent(tmp_path) -> None:
    from code_ai.core.subagents.resilience import RetryPolicy

    attempts = 0

    async def behaviour(prompt, _cancel):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return TurnResult(text="", response=None, error="derailed")
        return TurnResult(text="second try worked", response=None)

    runtime = _FakeRuntime(behaviour)
    events: list[str] = []
    bus = AsyncEventBus()
    bus.subscribe(lambda e: events.append(e.event_type))
    coord = _coordinator(
        tmp_path,
        runtime,
        event_bus=bus,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0),
    )
    reports = await coord.dispatch([SubagentRequest("explorer", "x")])

    assert reports[0].status is SubagentStatus.COMPLETED
    assert reports[0].summary == "second try worked"
    assert runtime.built == 2  # the retry got a freshly built sub-agent
    assert "subagent.retrying" in events


async def test_timeout_is_not_retried(tmp_path) -> None:
    from code_ai.core.subagents.resilience import RetryPolicy

    async def behaviour(prompt, cancel_event):
        await asyncio.sleep(30)
        return TurnResult(text="never", response=None)

    runtime = _FakeRuntime(behaviour, timeout_seconds=0)
    coord = _coordinator(
        tmp_path,
        runtime,
        hard_grace_seconds=0.2,
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.0, jitter=0.0),
    )
    reports = await asyncio.wait_for(
        coord.dispatch([SubagentRequest("coder", "slow")]), timeout=5
    )

    assert reports[0].status is SubagentStatus.TIMEOUT
    assert runtime.built == 1  # a full time budget is never spent twice


async def test_circuit_breaker_opens_after_repeated_failures(tmp_path) -> None:
    async def behaviour(prompt, _cancel):
        return TurnResult(text="", response=None, error="provider down")

    runtime = _FakeRuntime(behaviour)
    config = _config(tmp_path, subagent_circuit_failure_threshold=2)
    events: list[str] = []
    bus = AsyncEventBus()
    bus.subscribe(lambda e: events.append(e.event_type))
    coord = _coordinator(tmp_path, runtime, config=config, event_bus=bus)

    # Two failures trip the breaker for the coder profile.
    await coord.dispatch([SubagentRequest("coder", "1")])
    await coord.dispatch([SubagentRequest("coder", "2")])
    builds_before = runtime.built

    # Third dispatch is refused fast, without building another sub-agent.
    reports = await coord.dispatch([SubagentRequest("coder", "3")])
    assert reports[0].status is SubagentStatus.REJECTED
    assert "temporarily disabled" in reports[0].error
    assert runtime.built == builds_before
    assert "subagent.circuit.open" in events

    # A different profile is unaffected.
    ok = await coord.dispatch([SubagentRequest("explorer", "still works")])
    assert ok[0].status is SubagentStatus.FAILED  # fails too, but was NOT rejected


async def test_parent_cancellation_propagates_to_child(tmp_path) -> None:
    observed_cancel = asyncio.Event()

    async def behaviour(prompt, cancel_event):
        await cancel_event.wait()
        observed_cancel.set()
        from code_ai.core.errors import CancellationError

        raise CancellationError("stopped")

    runtime = _FakeRuntime(behaviour)
    coord = _coordinator(tmp_path, runtime)
    parent_cancel = asyncio.Event()

    task = asyncio.ensure_future(
        coord.dispatch([SubagentRequest("explorer", "x")], cancel_event=parent_cancel)
    )
    await asyncio.sleep(0.05)
    parent_cancel.set()
    reports = await asyncio.wait_for(task, timeout=5)

    assert observed_cancel.is_set()
    assert reports[0].status is SubagentStatus.CANCELLED


async def test_emits_lifecycle_events(tmp_path) -> None:
    async def behaviour(prompt, _cancel):
        return TurnResult(text="ok", response=None)

    runtime = _FakeRuntime(behaviour)
    bus = AsyncEventBus()
    events: list[str] = []
    bus.subscribe(lambda e: events.append(e.event_type))
    coord = _coordinator(tmp_path, runtime, event_bus=bus)

    await coord.dispatch([SubagentRequest("explorer", "x")])

    assert "subagent.dispatch.requested" in events
    assert "subagent.started" in events
    assert "subagent.completed" in events
