from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from code_ai.config.models import AppConfig
from code_ai.context.compression import CompressionResult, ContextCompressor
from code_ai.context.conversation import ConversationState
from code_ai.context.usage import UsageLedger
from code_ai.core.approval import (
    ApprovalDecision,
    ApprovalGateway,
    ApprovalRequest,
    DenyAllGateway,
    call_signature,
)
from code_ai.core.errors import (
    CancellationError,
    CodeAIError,
    CommandTimeoutError,
    ProviderError,
    TransientProviderError,
)
from code_ai.core.planning import PlannerMode, PlannerService, PlanningPhase
from code_ai.core.planning.policy import PolicyDecision
from code_ai.core.state import AgentState
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import (
    FinishReason,
    Message,
    ModelRequest,
    ModelResponse,
    ProviderEvent,
    ToolCall,
    ToolResult,
)
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.output import bound_text
from code_ai.tools.registry import ToolRegistry

ToolContextFactory = Callable[[asyncio.Event | None], ToolContext]

_MODEL_STEP_MAX_RETRIES = 200
_TOOL_GUARD_POLL_SECONDS = 2.0
_TOOL_GUARD_GRACE_SECONDS = 10.0
_ALLOWED_POLICY = PolicyDecision(True, "allowed", set())

# Capabilities that mutate the workspace or run external processes. In "ask"
# mode these prompt for approval even when the policy already allows them; in
# "auto" mode they run freely and only a policy denial escalates to the user.
_APPROVAL_SENSITIVE_CAPABILITIES = frozenset(
    {
        ToolCapability.LOCAL_WRITE,
        ToolCapability.PROCESS,
        ToolCapability.INTERACTIVE_TERMINAL,
    }
)


@dataclass(slots=True)
class TurnResult:
    text: str
    response: ModelResponse | None
    cancelled: bool = False
    error: str | None = None


@dataclass(slots=True)
class _TurnState:
    cancel_event: asyncio.Event | None
    deadline: float
    tool_calls_executed: int = 0
    last_response: ModelResponse | None = None
    progress_signature: tuple[object, ...] = ()
    stall_rounds: int = 0
    stall_nudged: bool = False
    seen_call_fingerprints: set[str] = field(default_factory=set)


@dataclass(slots=True)
class _ToolOutcome:
    result: ToolResult
    payload: dict[str, object] | None
    denied: bool = False


@dataclass(slots=True)
class _Authorization:
    """Result of reconciling the tool policy with the permission mode."""

    allowed: bool
    # Reason text to surface when a call is rejected.
    reason: str = ""
    # True when the user explicitly approved a call the policy would have denied,
    # so the planner must not record it as a policy denial.
    overrode_policy: bool = False


class AgentOrchestrator:
    """Deterministic, fault-tolerant provider/tool loop for one user turn at a time."""

    def __init__(
        self,
        *,
        config: AppConfig,
        provider: ModelProvider,
        tool_registry: ToolRegistry,
        conversation: ConversationState,
        usage: UsageLedger,
        event_bus: AsyncEventBus,
        compressor: ContextCompressor,
        tool_context_factory: ToolContextFactory,
        planner: PlannerService | None = None,
        approval_gateway: ApprovalGateway | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.tool_registry = tool_registry
        self.conversation = conversation
        self.usage = usage
        self.event_bus = event_bus
        self.compressor = compressor
        self.tool_context_factory = tool_context_factory
        self.planner = planner
        # Interactive approver. Defaults to deny-all so non-interactive runs keep
        # the prior behaviour; the terminal UI swaps in a modal-backed gateway.
        self.approval_gateway: ApprovalGateway = approval_gateway or DenyAllGateway()
        # Signatures the user chose to "always allow" for this session.
        self._session_allowlist: set[str] = set()
        self.state = AgentState.STARTING

    @property
    def permission_mode(self) -> str:
        return self.config.permission_mode

    async def set_state(self, state: AgentState, *, phase: str | None = None) -> None:
        self.state = state
        await self.event_bus.emit(
            "status.changed", {"state": state.value}, source="core.orchestrator"
        )
        if phase:
            await self.event_bus.emit("phase.changed", {"phase": phase}, source="core.orchestrator")

    # ------------------------------------------------------------------ #
    # Turn entry point
    # ------------------------------------------------------------------ #
    async def run_turn(self, text: str, *, cancel_event: asyncio.Event | None = None) -> TurnResult:
        await self.set_state(AgentState.CALLING_MODEL, phase="accepted_user_message")
        await self.event_bus.emit("user.message", {"text": text}, source="core.orchestrator")
        self.conversation.add_user(text)

        state = _TurnState(
            cancel_event=cancel_event,
            deadline=time.monotonic() + self.config.budgets.turn_timeout(),
        )
        try:
            early = await self._begin_planner(text, state)
            if early is not None:
                return early
            state.progress_signature = self._progress_signature()
            return await self._run_model_loop(state)
        except CancellationError:
            await self.set_state(AgentState.READY, phase="waiting_user")
            await self.event_bus.emit("turn.cancelled", {}, source="core.orchestrator")
            return TurnResult(text="", response=state.last_response, cancelled=True)
        except ProviderError as exc:
            # Provider exhausted retries: degrade gracefully instead of crashing the turn.
            await self._emit_error(exc)
            await self.set_state(AgentState.FAILED, phase="failed")
            return TurnResult(
                text=self._best_effort_text(state),
                response=state.last_response,
                error=str(exc),
            )
        except Exception as exc:
            await self._emit_error(exc)
            await self.set_state(AgentState.FAILED, phase="failed")
            raise

    async def _begin_planner(self, text: str, state: _TurnState) -> TurnResult | None:
        if not (self.planner and self.planner.enabled):
            return None
        await self.planner.begin_turn(
            text, provider_supports_tools=self.provider.capabilities.tool_calling
        )
        if self.planner.should_auto_list_workspace() and self.tool_registry.has("list_files"):
            state.tool_calls_executed += 1
            await self._execute_host_tool(
                "host_list_files_initial",
                "list_files",
                {"path": ".", "max_depth": 2, "max_entries": 250},
                state,
            )
        if self.planner.mode == PlannerMode.PLAN:
            return await self._finish_turn(self._planner_summary_text(), None, state)
        return None

    # ------------------------------------------------------------------ #
    # Model loop
    # ------------------------------------------------------------------ #
    async def _run_model_loop(self, state: _TurnState) -> TurnResult:
        for step in range(self.config.budgets.max_model_steps):
            self._raise_if_cancelled(state.cancel_event)
            if time.monotonic() > state.deadline:
                return await self._wind_down(state, reason="turn_time_budget_exhausted")

            allowed = self._allowed_tool_names()
            tool_definitions = self.tool_registry.definitions(allowed)
            compression = await self.compressor.ensure_capacity(self.conversation, tool_definitions)
            await self._emit_pre_request_usage(compression)

            request = self._build_request(step, tool_definitions)
            await self.event_bus.emit(
                "model.request.started",
                {
                    "model": self.config.model,
                    "step": step,
                    "tools": len(tool_definitions),
                    "allowed_tools": sorted(allowed) if allowed is not None else None,
                },
                source="core.orchestrator",
            )

            response = await self._run_model_step(request, state)
            state.last_response = response
            self.usage.add(response.usage)
            if response.response_id:
                self.conversation.previous_response_id = response.response_id
            await self._emit_response_completed(response)

            if not response.tool_calls:
                outcome = await self._handle_no_tool_response(response, state)
                if outcome is not None:
                    return outcome
                continue

            self.conversation.add_assistant(response.text or "", response.tool_calls)
            outcome = await self._execute_tool_batch(response, state)
            if outcome is not None:
                return outcome
            outcome = await self._note_tool_round(response, state)
            if outcome is not None:
                return outcome
            await self.set_state(AgentState.CALLING_MODEL, phase="calling_model_after_tools")

        return await self._wind_down(state, reason="model_step_budget_exhausted")

    async def _handle_no_tool_response(
        self, response: ModelResponse, state: _TurnState
    ) -> TurnResult | None:
        if self._requires_tool_for_progress():
            if response.text:
                self.conversation.add_assistant(
                    bound_text(response.text, self.config.budgets.max_tool_output_chars), []
                )
            correction = await self.planner.note_no_tool_response(
                recommended_tool_names=self._recommended_tool_names()
            )
            self.conversation.add_user(correction)
            if self.planner.phase == PlanningPhase.BLOCKED:
                return await self._finish_turn(correction, response, state)
            await self.set_state(AgentState.CALLING_MODEL, phase="correcting_no_tool_response")
            return None

        if response.text:
            self.conversation.add_assistant(response.text, response.tool_calls)
        return await self._finish_turn(response.text, response, state)

    # ------------------------------------------------------------------ #
    # Convergence guard: stop tool-call loops that make no real progress
    # ------------------------------------------------------------------ #
    async def _note_tool_round(
        self, response: ModelResponse, state: _TurnState
    ) -> TurnResult | None:
        """Detect a model that keeps calling tools without advancing the task.

        A round counts as progress when the planner's semantic signature moves
        (new evidence, a phase/step transition) or, without a planner, when the
        batch contains a tool call we have not run before this turn. After a few
        unproductive rounds we nudge once, then wind down with a best-effort
        answer so the turn never spins to the hard step budget with no reply.
        """
        signature = self._progress_signature()
        if signature:
            productive = signature != state.progress_signature
            state.progress_signature = signature
        else:
            fingerprints = {self._call_fingerprint(call) for call in response.tool_calls}
            productive = bool(fingerprints - state.seen_call_fingerprints)
            state.seen_call_fingerprints |= fingerprints

        if productive:
            state.stall_rounds = 0
            state.stall_nudged = False
            return None

        state.stall_rounds += 1
        limit = max(2, self.config.budgets.max_stall_rounds)
        if state.stall_rounds >= limit:
            await self.event_bus.emit(
                "turn.stalled",
                {
                    "stall_rounds": state.stall_rounds,
                    "tool_calls_executed": state.tool_calls_executed,
                },
                source="core.orchestrator",
            )
            return await self._wind_down(state, reason="model_stalled")
        if state.stall_rounds >= limit // 2 and not state.stall_nudged:
            state.stall_nudged = True
            self.conversation.add_user(self._stall_nudge_text())
            await self.event_bus.emit(
                "agent.stall.nudged",
                {"stall_rounds": state.stall_rounds},
                source="core.orchestrator",
            )
        return None

    def _progress_signature(self) -> tuple[object, ...]:
        if self.planner and self.planner.enabled:
            return self.planner.progress_signature()
        return ()

    @staticmethod
    def _call_fingerprint(call: ToolCall) -> str:
        try:
            arguments = json.dumps(call.arguments, sort_keys=True, default=str)
        except (TypeError, ValueError):
            arguments = str(call.arguments)
        return f"{call.name}:{arguments}"

    @staticmethod
    def _stall_nudge_text() -> str:
        return (
            "Runtime check: recent tool calls have not advanced the task, and repeating "
            "the same observation will not help. If you already have enough information, "
            "reply to the user now with your final answer. If a workspace change still "
            "needs settling, make the single remaining change or call complete_task with "
            "evidence. Do not issue further redundant tool calls."
        )

    # ------------------------------------------------------------------ #
    # Model request with timeout + transient retry
    # ------------------------------------------------------------------ #
    async def _run_model_step(self, request: ModelRequest, state: _TurnState) -> ModelResponse:
        attempts = 0
        model_timeout = float(self.config.budgets.model_timeout())
        while True:
            streamed: list[str] = []
            try:
                return await asyncio.wait_for(
                    self._collect_model_response(request, state.cancel_event, streamed),
                    timeout=model_timeout,
                )
            except CancellationError:
                raise
            except (TransientProviderError, TimeoutError) as exc:
                if streamed:
                    # Output already reached the user; salvage rather than replay.
                    return ModelResponse(
                        text="".join(streamed), finish_reason=FinishReason.UNKNOWN
                    )
                if attempts >= _MODEL_STEP_MAX_RETRIES:
                    await self._emit_request_failed(exc)
                    raise ProviderError(f"Model step failed after retries: {exc}") from exc
                attempts += 1
                await self.event_bus.emit(
                    "model.request.retrying",
                    {"attempt": attempts, "reason": type(exc).__name__},
                    source="core.orchestrator",
                )
                await asyncio.sleep(min(2.0, 0.25 * (2**attempts)) + random.random() * 0.1)
            except ProviderError as exc:
                if streamed:
                    return ModelResponse(
                        text="".join(streamed), finish_reason=FinishReason.UNKNOWN
                    )
                await self._emit_request_failed(exc)
                raise

    async def _collect_model_response(
        self,
        request: ModelRequest,
        cancel_event: asyncio.Event | None,
        streamed_sink: list[str],
    ) -> ModelResponse:
        reasoning_parts: list[str] = []
        completed: ModelResponse | None = None
        async for event in self.provider.stream(request):
            self._raise_if_cancelled(cancel_event)
            await self._emit_provider_event(event)
            if event.kind == "text_delta":
                streamed_sink.append(event.text_delta)
            elif event.kind == "reasoning_delta":
                reasoning_parts.append(event.reasoning_delta)
            elif event.kind == "completed" and event.response:
                completed = event.response

        if completed is None:
            return ModelResponse(
                text="".join(streamed_sink),
                reasoning="".join(reasoning_parts),
                finish_reason=FinishReason.UNKNOWN,
            )
        if not completed.text and streamed_sink:
            completed.text = "".join(streamed_sink)
        if not completed.reasoning and reasoning_parts:
            completed.reasoning = "".join(reasoning_parts)
        return completed

    # ------------------------------------------------------------------ #
    # Tool batch execution
    # ------------------------------------------------------------------ #
    async def _execute_tool_batch(
        self, response: ModelResponse, state: _TurnState
    ) -> TurnResult | None:
        await self.set_state(AgentState.EXECUTING_TOOL, phase="executing_tools")
        calls = response.tool_calls

        if state.tool_calls_executed >= self.config.budgets.max_tool_calls:
            return await self._wind_down(state, reason="tool_call_budget_exhausted")
        state.tool_calls_executed += len(calls)

        # Evaluate policy for the whole batch against one consistent snapshot so a
        # planner transition triggered by an early call cannot retroactively deny a
        # later call in the same model response.
        decisions = {call.id: self._policy_decision_for(call.name) for call in calls}
        parallel = [
            call
            for call in calls
            if decisions[call.id].allowed and self._is_read_only(call.name)
        ]
        parallel_ids = {call.id for call in parallel}
        outcomes: dict[str, _ToolOutcome] = {}

        if len(parallel) > 1:
            gathered = await asyncio.gather(
                *(self._execute_call(call, decisions[call.id], state) for call in parallel)
            )
            for call, outcome in zip(parallel, gathered, strict=True):
                outcomes[call.id] = outcome
        elif parallel:
            call = parallel[0]
            outcomes[call.id] = await self._execute_call(call, decisions[call.id], state)

        for call in calls:
            if call.id in parallel_ids:
                continue
            self._raise_if_cancelled(state.cancel_event)
            outcomes[call.id] = await self._execute_call(call, decisions[call.id], state)

        # Record results and advance the planner sequentially, in model order, to keep
        # the evidence ledger deterministic even when reads ran concurrently.
        for call in calls:
            outcome = outcomes[call.id]
            self.conversation.add_tool_result(outcome.result)
            if (
                self.planner
                and self.planner.enabled
                and outcome.payload is not None
                and not outcome.result.is_error
            ):
                await self.planner.record_tool_result(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    payload=outcome.payload,
                    success=True,
                )
            if self.planner and self.planner.accepted_final_text is not None:
                return await self._finish_turn(self.planner.accepted_final_text, response, state)
        return None

    async def _execute_host_tool(
        self,
        call_id: str,
        name: str,
        arguments: dict[str, object],
        state: _TurnState,
    ) -> None:
        await self.set_state(AgentState.EXECUTING_TOOL, phase="executing_tools")
        call = ToolCall(id=call_id, name=name, arguments=arguments)
        outcome = await self._execute_call(call, None, state)
        if (
            self.planner
            and self.planner.enabled
            and outcome.payload is not None
            and not outcome.result.is_error
        ):
            await self.planner.record_tool_result(
                tool_call_id=call_id,
                tool_name=name,
                payload=outcome.payload,
                success=True,
            )

    async def _execute_call(
        self,
        call: ToolCall,
        decision: PolicyDecision | None,
        state: _TurnState,
    ) -> _ToolOutcome:
        await self.event_bus.emit(
            "tool.call.requested",
            {"tool_call_id": call.id, "name": call.name, "arguments": call.arguments},
            source="core.orchestrator",
        )
        authorization = await self._authorize_call(call, decision, state)
        if not authorization.allowed:
            policy_denied = decision is not None and not decision.allowed
            # A genuine policy gate that the user also declined is recorded so the
            # planner learns the gate held. A plain user refusal of an otherwise
            # allowed tool is not a policy denial and must not poison the ledger.
            if policy_denied and self.planner and self.planner.enabled:
                await self.planner.record_policy_denial(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    reason=decision.reason,
                    allowed_tool_names=decision.allowed_tool_names,
                )
            await self.event_bus.emit(
                "tool.call.failed",
                {
                    "tool_call_id": call.id,
                    "name": call.name,
                    "message": authorization.reason,
                    "type": "ToolPolicyDenied" if policy_denied else "ToolApprovalDenied",
                },
                source="core.orchestrator",
            )
            prefix = "Tool policy denied" if policy_denied else "Tool execution denied by user"
            return _ToolOutcome(
                result=ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=f"{prefix}: {authorization.reason}",
                    is_error=True,
                ),
                payload=None,
                denied=True,
            )

        await self.event_bus.emit(
            "tool.call.started",
            {"tool_call_id": call.id, "name": call.name},
            source="core.orchestrator",
        )
        try:
            payload = await self._guarded_execute(call.name, call.arguments, state)
            if self.planner and self.planner.enabled and call.name == "complete_task":
                rejection = await self._completion_rejection(call, payload)
                if rejection is not None:
                    return rejection
            content = bound_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str),
                self.config.budgets.max_tool_output_chars,
            )
            await self.event_bus.emit(
                "tool.call.completed",
                {"tool_call_id": call.id, "name": call.name, "result": payload},
                source="core.orchestrator",
            )
            return _ToolOutcome(
                result=ToolResult(tool_call_id=call.id, name=call.name, content=content),
                payload=payload,
            )
        except CancellationError:
            raise
        except CodeAIError as exc:
            await self.event_bus.emit(
                "tool.call.failed",
                {
                    "tool_call_id": call.id,
                    "name": call.name,
                    "message": str(exc),
                    "type": type(exc).__name__,
                },
                source="core.orchestrator",
            )
            return _ToolOutcome(
                result=ToolResult(
                    tool_call_id=call.id, name=call.name, content=str(exc), is_error=True
                ),
                payload=None,
            )

    async def _completion_rejection(
        self, call: ToolCall, payload: dict[str, object]
    ) -> _ToolOutcome | None:
        decision = await self.planner.evaluate_completion(payload)
        if decision.accepted:
            return None
        missing = {
            "status": "rejected",
            "missing_requirements": list(decision.missing_requirements),
        }
        content = bound_text(
            json.dumps(missing, indent=2, sort_keys=True, default=str),
            self.config.budgets.max_tool_output_chars,
        )
        await self.event_bus.emit(
            "tool.call.failed",
            {
                "tool_call_id": call.id,
                "name": call.name,
                "message": "completion rejected",
                "missing_requirements": list(decision.missing_requirements),
                "type": "CompletionRejected",
            },
            source="core.orchestrator",
        )
        return _ToolOutcome(
            result=ToolResult(
                tool_call_id=call.id, name=call.name, content=content, is_error=True
            ),
            payload=None,
        )

    async def _guarded_execute(
        self,
        name: str,
        arguments: dict[str, object],
        state: _TurnState,
    ) -> dict[str, object]:
        """Run a tool with a cooperative wall-clock backstop.

        Cancellation is signalled through a child event so the tool can clean up
        (e.g. terminate a subprocess group) instead of being hard-killed and
        leaking resources. A hard cancel is the last resort if the tool ignores
        the cooperative signal past a short grace period.
        """
        parent = state.cancel_event
        timeout = float(self.config.budgets.max_tool_wall_time_s)
        tool_cancel = asyncio.Event()
        exec_task = asyncio.ensure_future(
            self.tool_registry.execute(name, arguments, self.tool_context_factory(tool_cancel))
        )
        start = time.monotonic()
        timed_out = False
        try:
            while True:
                if parent is not None and parent.is_set():
                    tool_cancel.set()
                elapsed = time.monotonic() - start
                if not timed_out and elapsed > timeout:
                    timed_out = True
                    tool_cancel.set()
                done, _ = await asyncio.wait({exec_task}, timeout=_TOOL_GUARD_POLL_SECONDS)
                if exec_task in done:
                    break
                if timed_out and elapsed > timeout + _TOOL_GUARD_GRACE_SECONDS:
                    exec_task.cancel()
                    break
        except asyncio.CancelledError:
            exec_task.cancel()
            with contextlib.suppress(BaseException):
                await exec_task
            raise

        if exec_task.cancelled():
            if parent is not None and parent.is_set():
                raise CancellationError("Turn cancelled.")
            raise CommandTimeoutError(f"Tool '{name}' exceeded its time budget.")

        exc = exec_task.exception()
        if exc is not None:
            if isinstance(exc, CancellationError) and timed_out and not (
                parent is not None and parent.is_set()
            ):
                raise CommandTimeoutError(f"Tool '{name}' exceeded its time budget.") from exc
            raise exc
        return exec_task.result()

    # ------------------------------------------------------------------ #
    # Turn termination
    # ------------------------------------------------------------------ #
    async def _wind_down(self, state: _TurnState, *, reason: str) -> TurnResult:
        await self.event_bus.emit(
            "turn.budget_exhausted",
            {"reason": reason, "tool_calls_executed": state.tool_calls_executed},
            source="core.orchestrator",
        )
        text = self._best_effort_text(state) or (
            "I reached a runtime safety budget for this turn before fully completing the "
            "request. The work so far is preserved; re-run or narrow the request to continue."
        )
        return await self._finish_turn(text, state.last_response, state)

    async def _finish_turn(
        self,
        text: str,
        response: ModelResponse | None,
        state: _TurnState,
        *,
        error: str | None = None,
    ) -> TurnResult:
        await self.set_state(AgentState.READY, phase="waiting_user")
        await self.event_bus.emit(
            "turn.completed",
            {"text": text, "usage": self.usage.to_dict()},
            source="core.orchestrator",
        )
        return TurnResult(text=text, response=response, error=error)

    def _best_effort_text(self, state: _TurnState) -> str:
        if state.last_response and state.last_response.text:
            return state.last_response.text
        if self.planner and self.planner.enabled:
            summary = self.planner.best_effort_summary()
            if summary:
                return summary
        return ""

    # ------------------------------------------------------------------ #
    # Event helpers
    # ------------------------------------------------------------------ #
    async def _emit_pre_request_usage(self, compression: CompressionResult) -> None:
        await self.event_bus.emit(
            "usage.updated",
            {
                "active_context_tokens": compression.active_tokens,
                "active_context_estimated": compression.estimated,
                "cumulative": self.usage.to_dict(),
            },
            source="context",
        )

    async def _emit_response_completed(self, response: ModelResponse) -> None:
        await self.event_bus.emit(
            "model.response.completed",
            {
                "finish_reason": response.finish_reason.value,
                "tool_calls": [call.to_dict() for call in response.tool_calls],
                "usage": response.usage.to_dict() if response.usage else None,
            },
            source="core.orchestrator",
        )
        await self.event_bus.emit(
            "usage.updated",
            {"cumulative": self.usage.to_dict()},
            source="context",
        )

    async def _emit_provider_event(self, event: ProviderEvent) -> None:
        if event.kind == "text_delta":
            channel = "working" if self._requires_tool_for_progress() else "answer"
            await self.event_bus.emit(
                "model.stream.delta",
                {"text": event.text_delta, "channel": channel},
                source="provider",
            )
        elif event.kind == "reasoning_delta":
            await self.event_bus.emit(
                "model.thinking.delta",
                {"text": event.reasoning_delta},
                source="provider",
            )
        elif event.kind == "warning" and event.warning:
            await self.event_bus.emit("warning", {"message": event.warning}, source="provider")
        elif event.kind == "usage" and event.usage:
            await self.event_bus.emit(
                "usage.updated",
                {"usage": event.usage.to_dict()},
                source="provider",
            )

    async def _emit_request_failed(self, exc: Exception) -> None:
        await self.event_bus.emit(
            "model.request.failed",
            {"message": str(exc), "type": type(exc).__name__},
            source="core.orchestrator",
        )

    async def _emit_error(self, exc: Exception) -> None:
        await self.event_bus.emit(
            "error",
            {"message": str(exc), "type": type(exc).__name__},
            source="core.orchestrator",
        )

    # ------------------------------------------------------------------ #
    # Request construction + planner helpers
    # ------------------------------------------------------------------ #
    def _build_request(self, step: int, tool_definitions: list) -> ModelRequest:
        messages = self.conversation.snapshot()
        planner_context = self._planner_context()
        if planner_context:
            messages = [*messages, Message(role="user", content=planner_context)]
        return ModelRequest(
            model=self.config.model,
            messages=messages,
            tools=tool_definitions,
            max_output_tokens=self.config.output_token_reserve,
            previous_response_id=self.conversation.previous_response_id,
            use_remote_conversation_state=(
                self.config.use_remote_conversation_state
                and self.conversation.remote_state_supported
                and self.provider.capabilities.remote_conversation_state
            ),
            metadata={
                "step": step,
                "planning_phase": self.planner.phase.value
                if self.planner and self.planner.enabled
                else None,
            },
        )

    def _policy_decision_for(self, name: str) -> PolicyDecision:
        if not (self.planner and self.planner.enabled):
            return _ALLOWED_POLICY
        return self.planner.evaluate_tool(name, self.tool_registry)

    def _capabilities_for(self, name: str) -> frozenset[ToolCapability]:
        try:
            return self.tool_registry.capabilities(name)
        except Exception:
            return frozenset()

    def _requires_user_approval(self, name: str, *, policy_allowed: bool) -> bool:
        mode = self.permission_mode
        if mode == "bypass":
            return False
        if not policy_allowed:
            # Both "ask" and "auto" escalate a policy denial to the user instead
            # of failing the tool outright.
            return True
        if mode == "auto":
            return False
        # "ask": prompt before tools that mutate the workspace or run processes.
        return bool(self._capabilities_for(name) & _APPROVAL_SENSITIVE_CAPABILITIES)

    async def _authorize_call(
        self,
        call: ToolCall,
        decision: PolicyDecision | None,
        state: _TurnState,
    ) -> _Authorization:
        policy_allowed = decision is None or decision.allowed
        if self.permission_mode == "bypass":
            # Run everything, overriding any policy gate, and never prompt.
            return _Authorization(allowed=True, overrode_policy=not policy_allowed)
        if not self._requires_user_approval(call.name, policy_allowed=policy_allowed):
            reason = "" if policy_allowed or decision is None else decision.reason
            return _Authorization(allowed=policy_allowed, reason=reason)

        signature = call_signature(call.name, call.arguments)
        if signature in self._session_allowlist:
            return _Authorization(allowed=True, overrode_policy=not policy_allowed)

        request = ApprovalRequest(
            call_id=call.id,
            tool_name=call.name,
            arguments=call.arguments,
            signature=signature,
            reason=decision.reason if (decision and not policy_allowed) else "",
            capabilities=tuple(sorted(cap.value for cap in self._capabilities_for(call.name))),
            policy_denied=not policy_allowed,
        )
        await self.event_bus.emit(
            "tool.approval.requested", request.to_dict(), source="core.orchestrator"
        )
        try:
            user_decision = await self.approval_gateway.request_approval(request)
        except Exception as exc:  # pragma: no cover - defensive: never crash a turn on UI errors
            user_decision = ApprovalDecision.deny(f"Approval prompt failed: {exc}")
        await self.event_bus.emit(
            "tool.approval.resolved",
            {
                "call_id": call.id,
                "tool_name": call.name,
                "scope": user_decision.scope.value,
                "approved": user_decision.approved,
            },
            source="core.orchestrator",
        )
        if user_decision.approved and user_decision.remember:
            self._session_allowlist.add(signature)
        if not user_decision.approved:
            return _Authorization(
                allowed=False, reason=user_decision.reason or "Denied by user."
            )
        return _Authorization(allowed=True, overrode_policy=not policy_allowed)

    def _is_read_only(self, name: str) -> bool:
        try:
            caps = self.tool_registry.capabilities(name)
        except Exception:
            return False
        return bool(caps) and caps <= frozenset({ToolCapability.LOCAL_READ})

    def _allowed_tool_names(self) -> set[str] | None:
        if not (self.planner and self.planner.enabled):
            return None
        return self.planner.allowed_tool_names(self.tool_registry)

    def _recommended_tool_names(self) -> set[str]:
        if not (self.planner and self.planner.enabled):
            return set()
        return self.planner.recommended_tool_names(self.tool_registry)

    def _planner_context(self) -> str:
        if not (self.planner and self.planner.enabled):
            return ""
        return self.planner.task_context_block(
            recommended_tool_names=self.planner.recommended_tool_names(self.tool_registry)
        )

    def _requires_tool_for_progress(self) -> bool:
        return bool(self.planner and self.planner.requires_tool_for_progress())

    def _planner_summary_text(self) -> str:
        if not (self.planner and self.planner.plan):
            return "Plan mode is active, but no plan is available."
        snapshot = self.planner.plan_snapshot()
        steps = [
            f"{index + 1}. {step.title} [{step.kind.value}]"
            for index, step in enumerate(self.planner.plan.steps)
        ]
        return (
            "Plan mode is active. No workspace mutations were performed.\n"
            f"Objective: {self.planner.plan.objective}\n"
            f"Phase: {snapshot.get('phase')}\n"
            "Steps:\n"
            + "\n".join(steps)
        )

    @staticmethod
    def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
        if cancel_event and cancel_event.is_set():
            raise CancellationError("Turn cancelled.")
