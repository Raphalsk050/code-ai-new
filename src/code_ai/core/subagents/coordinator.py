from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from code_ai.config.models import AppConfig
from code_ai.core.errors import CancellationError
from code_ai.core.orchestration import WIND_DOWN_TIME_BUDGET, TurnResult
from code_ai.core.subagents.profiles import SubagentProfile, SubagentProfileRegistry
from code_ai.core.subagents.report import SubagentReport, SubagentStatus
from code_ai.core.subagents.resilience import CircuitBreaker, RetryPolicy
from code_ai.core.subagents.runtime import BuiltSubagent
from code_ai.events.bus import AsyncEventBus
from code_ai.events.models import EventEnvelope
from code_ai.tools.output import bound_text

# Child events surfaced to the parent bus as ``subagent.progress``. Streaming
# deltas are intentionally excluded: forwarding token-level output from several
# concurrent sub-agents would scramble the parent transcript. Callers see the
# milestones (which tools each sub-agent ran) without the noise.
_FORWARDED_EVENTS = frozenset(
    {
        "tool.call.started",
        "tool.call.completed",
        "tool.call.failed",
        "model.response.completed",
    }
)

_SUMMARY_PREVIEW_CHARS = 500


class SubagentRuntimeLike(Protocol):
    """The narrow slice of the runtime the coordinator depends on (DIP)."""

    def build(self, profile: SubagentProfile) -> BuiltSubagent: ...


@dataclass(frozen=True, slots=True)
class SubagentRequest:
    """One delegation the orchestrating model asked for."""

    agent_type: str
    prompt: str


class Dispatcher(Protocol):
    """Narrow interface the dispatch tool depends on, not the whole coordinator."""

    async def dispatch(
        self,
        requests: list[SubagentRequest],
        *,
        cancel_event: asyncio.Event | None = None,
        depth: int = 0,
    ) -> list[SubagentReport]: ...

    def available_types_description(self) -> str: ...


class SubagentCoordinator:
    """Facade owning sub-agent lifecycle: dispatch, concurrency, and resilience.

    A single dispatch call may carry several requests; they run concurrently up
    to ``max_concurrent_subagents`` and each is guarded by a per-profile circuit
    breaker and a bounded retry. Failures never propagate as exceptions - every
    request resolves to a :class:`SubagentReport`, so a failed delegation degrades
    into structured feedback the model can react to rather than crashing the turn.
    """

    def __init__(
        self,
        *,
        runtime: SubagentRuntimeLike,
        profile_registry: SubagentProfileRegistry,
        event_bus: AsyncEventBus,
        config: AppConfig,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        hard_grace_seconds: float = 30.0,
    ) -> None:
        self._runtime = runtime
        self._profiles = profile_registry
        self._bus = event_bus
        self._config = config
        budgets = config.budgets
        self._retry = retry_policy or RetryPolicy(
            max_attempts=budgets.subagent_retry_max_attempts
        )
        self._breaker = circuit_breaker or CircuitBreaker(
            failure_threshold=budgets.subagent_circuit_failure_threshold,
            reset_timeout=float(budgets.subagent_circuit_reset_s),
        )
        self._hard_grace_seconds = hard_grace_seconds

    def available_types_description(self) -> str:
        return self._profiles.describe()

    async def dispatch(
        self,
        requests: list[SubagentRequest],
        *,
        cancel_event: asyncio.Event | None = None,
        depth: int = 0,
    ) -> list[SubagentReport]:
        if not requests:
            return []
        # Depth guard: a sub-agent (depth >= limit) cannot delegate further.
        if depth >= self._config.budgets.max_subagent_depth:
            return [
                self._rejected(req, "Maximum sub-agent delegation depth reached.")
                for req in requests
            ]
        # Per-turn cap: run the first N, refuse the overflow with a clear reason.
        cap = self._config.budgets.max_subagents_per_turn
        accepted, overflow = requests[:cap], requests[cap:]

        await self._bus.emit(
            "subagent.dispatch.requested",
            {"count": len(accepted), "types": [req.agent_type for req in accepted]},
            source="subagent",
        )

        semaphore = asyncio.Semaphore(self._config.budgets.max_concurrent_subagents)

        async def _guarded(request: SubagentRequest) -> SubagentReport:
            async with semaphore:
                return await self._dispatch_one(request, cancel_event, depth)

        reports = list(await asyncio.gather(*(_guarded(req) for req in accepted)))
        reports.extend(
            self._rejected(req, "Per-turn sub-agent limit reached; not dispatched.")
            for req in overflow
        )
        return reports

    async def _dispatch_one(
        self,
        request: SubagentRequest,
        parent_cancel: asyncio.Event | None,
        depth: int,
    ) -> SubagentReport:
        agent_id = uuid4().hex[:8]
        profile = self._profiles.get(request.agent_type)
        if profile is None:
            reason = (
                f"Unknown sub-agent type {request.agent_type!r}. "
                f"Available types: {', '.join(self._profiles.names())}."
            )
            await self._emit_rejected(agent_id, request.agent_type, reason)
            return self._rejected(request, reason, agent_id=agent_id)

        if not self._breaker.allows(profile.name):
            reason = (
                f"The {profile.name} sub-agent is temporarily disabled after repeated "
                "failures. Handle this task directly instead of delegating."
            )
            await self._bus.emit(
                "subagent.circuit.open",
                {"agent_type": profile.name},
                source="subagent",
            )
            await self._emit_rejected(agent_id, profile.name, reason)
            return self._rejected(request, reason, agent_id=agent_id)

        async def _attempt() -> SubagentReport:
            try:
                return await self._run(profile, request, agent_id, parent_cancel)
            except Exception as exc:  # noqa: BLE001 - degrade, never crash the parent turn
                return SubagentReport(
                    agent_id=agent_id,
                    agent_type=profile.name,
                    task=request.prompt,
                    status=SubagentStatus.FAILED,
                    error=str(exc) or type(exc).__name__,
                )

        async def _note_retry(attempt: int, failed: SubagentReport) -> None:
            await self._bus.emit(
                "subagent.retrying",
                {
                    "agent_id": agent_id,
                    "agent_type": profile.name,
                    "attempt": attempt,
                    "error": failed.error,
                },
                source="subagent",
            )

        # Retry only genuine FAILED outcomes, each attempt with a freshly built
        # sub-agent: a clean context is the best medicine when a weak model
        # derails mid-task. Timeouts are excluded - they already consumed a full
        # time budget, so a rerun would double the wall-clock cost for the same
        # likely outcome.
        report = await self._retry.until(
            _attempt,
            accept=lambda r: r.status is not SubagentStatus.FAILED,
            on_retry=_note_retry,
        )

        if report.status is SubagentStatus.COMPLETED:
            self._breaker.record_success(profile.name)
        else:
            self._breaker.record_failure(profile.name)

        await self._emit_terminal(report)
        return report

    async def _run(
        self,
        profile: SubagentProfile,
        request: SubagentRequest,
        agent_id: str,
        parent_cancel: asyncio.Event | None,
    ) -> SubagentReport:
        built = self._runtime.build(profile)
        forwarder = self._make_forwarder(agent_id, profile.name)
        built.event_bus.subscribe(forwarder)

        await self._bus.emit(
            "subagent.started",
            {
                "agent_id": agent_id,
                "agent_type": profile.name,
                "task": bound_text(request.prompt, _SUMMARY_PREVIEW_CHARS),
            },
            source="subagent",
        )

        child_cancel = asyncio.Event()
        mirror = asyncio.ensure_future(self._mirror_cancel(parent_cancel, child_cancel))
        hard_timeout = built.timeout_seconds + self._hard_grace_seconds
        try:
            result = await asyncio.wait_for(
                built.orchestrator.run_turn(request.prompt, cancel_event=child_cancel),
                timeout=hard_timeout,
            )
        except TimeoutError:
            # Cooperative first: let the loop observe the cancel and unwind.
            child_cancel.set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(mirror, timeout=0)
            return SubagentReport(
                agent_id=agent_id,
                agent_type=profile.name,
                task=request.prompt,
                status=SubagentStatus.TIMEOUT,
                error=f"Sub-agent exceeded its {built.timeout_seconds}s time budget.",
                usage=built.usage.to_dict(),
            )
        except CancellationError:
            return SubagentReport(
                agent_id=agent_id,
                agent_type=profile.name,
                task=request.prompt,
                status=SubagentStatus.CANCELLED,
                usage=built.usage.to_dict(),
            )
        finally:
            mirror.cancel()
            with contextlib.suppress(BaseException):
                await mirror
            built.event_bus.unsubscribe(forwarder)

        return self._report_from_turn(result, profile, request, agent_id, built)

    def _report_from_turn(
        self,
        result: TurnResult,
        profile: SubagentProfile,
        request: SubagentRequest,
        agent_id: str,
        built: BuiltSubagent,
    ) -> SubagentReport:
        usage = built.usage.to_dict()
        if result.cancelled:
            return SubagentReport(
                agent_id=agent_id,
                agent_type=profile.name,
                task=request.prompt,
                status=SubagentStatus.CANCELLED,
                usage=usage,
            )
        if result.error is not None:
            return SubagentReport(
                agent_id=agent_id,
                agent_type=profile.name,
                task=request.prompt,
                status=SubagentStatus.FAILED,
                summary=result.text,
                error=result.error,
                usage=usage,
            )
        # A wound-down turn produced only a best-effort answer; reporting it as
        # COMPLETED would let the parent model take the summary at face value.
        if result.wind_down_reason == WIND_DOWN_TIME_BUDGET:
            return SubagentReport(
                agent_id=agent_id,
                agent_type=profile.name,
                task=request.prompt,
                status=SubagentStatus.TIMEOUT,
                summary=result.text,
                error=f"Sub-agent exceeded its {built.timeout_seconds}s time budget.",
                usage=usage,
            )
        if result.wind_down_reason is not None:
            reason = result.wind_down_reason.replace("_", " ")
            return SubagentReport(
                agent_id=agent_id,
                agent_type=profile.name,
                task=request.prompt,
                status=SubagentStatus.FAILED,
                summary=result.text,
                error=f"Sub-agent stopped before finishing: {reason}.",
                usage=usage,
            )
        if not result.text.strip():
            return SubagentReport(
                agent_id=agent_id,
                agent_type=profile.name,
                task=request.prompt,
                status=SubagentStatus.FAILED,
                error="Sub-agent finished without producing a final answer.",
                usage=usage,
            )
        return SubagentReport(
            agent_id=agent_id,
            agent_type=profile.name,
            task=request.prompt,
            status=SubagentStatus.COMPLETED,
            summary=result.text,
            usage=usage,
        )

    @staticmethod
    async def _mirror_cancel(
        parent: asyncio.Event | None, child: asyncio.Event
    ) -> None:
        """Propagate a parent-turn cancellation down into the sub-agent."""
        if parent is None:
            return
        await parent.wait()
        child.set()

    def _make_forwarder(self, agent_id: str, agent_type: str):
        async def handler(envelope: EventEnvelope) -> None:
            if envelope.event_type not in _FORWARDED_EVENTS:
                return
            await self._bus.emit(
                "subagent.progress",
                {
                    "agent_id": agent_id,
                    "agent_type": agent_type,
                    "event": envelope.event_type,
                    "name": envelope.payload.get("name"),
                },
                source="subagent",
            )

        return handler

    async def _emit_rejected(self, agent_id: str, agent_type: str, reason: str) -> None:
        await self._bus.emit(
            "subagent.rejected",
            {"agent_id": agent_id, "agent_type": agent_type, "reason": reason},
            source="subagent",
        )

    async def _emit_terminal(self, report: SubagentReport) -> None:
        event = "subagent.completed" if report.ok else "subagent.failed"
        await self._bus.emit(
            event,
            {
                "agent_id": report.agent_id,
                "agent_type": report.agent_type,
                "status": report.status.value,
                "summary": bound_text(report.summary, _SUMMARY_PREVIEW_CHARS),
                "error": report.error,
                "usage": report.usage,
            },
            source="subagent",
        )

    @staticmethod
    def _rejected(
        request: SubagentRequest, reason: str, *, agent_id: str = ""
    ) -> SubagentReport:
        return SubagentReport(
            agent_id=agent_id or uuid4().hex[:8],
            agent_type=request.agent_type,
            task=request.prompt,
            status=SubagentStatus.REJECTED,
            error=reason,
        )
