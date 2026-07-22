from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
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
    WorkspaceBoundaryError,
)
from code_ai.core.memory import FailureMemoryStore, MemoryService
from code_ai.core.planning import PlannerService
from code_ai.core.planning.policy import PolicyDecision
from code_ai.core.reflection import ReflectionService, TurnDigest
from code_ai.core.rules import RulesService
from code_ai.core.state import AgentState
from code_ai.events.bus import AsyncEventBus
from code_ai.prompts import VISION_ANALYSIS_PROMPT, build_system_prompt
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import (
    FinishReason,
    ImageContent,
    Message,
    ModelRequest,
    ModelResponse,
    ProviderEvent,
    ToolCall,
    ToolResult,
)
from code_ai.providers.reasoning import ReasoningTagFilter, split_reasoning_tags
from code_ai.providers.tool_recovery import (
    ToolCallStreamFilter,
    looks_like_attempted_tool_call,
    recover_tool_calls_from_text,
)
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.output import bound_text
from code_ai.tools.registry import ToolRegistry

ToolContextFactory = Callable[[asyncio.Event | None], ToolContext]

_MODEL_STEP_MAX_RETRIES = 200
# How many times to re-prompt a model that printed a tool call as text but in a
# shape we could not parse, before giving up and surfacing its best-effort text.
_MAX_TOOL_FORMAT_RETRIES = 2
# How many times to re-prompt a model that burned its entire output budget
# (typically inside the reasoning channel) without emitting a tool call, before
# giving up. Each retry hands back its own truncated thinking plus a nudge to
# commit to one concrete action.
_MAX_BUDGET_RETRIES = 2
_TOOL_GUARD_POLL_SECONDS = 2.0
_TOOL_GUARD_GRACE_SECONDS = 10.0

logger = logging.getLogger(__name__)
# Minimum growth in a streaming tool call's arguments before we emit another
# progress update, so a large write reports periodically rather than per-token.
_TOOL_PROGRESS_STEP_CHARS = 160
# Best-effort extraction of a "path" argument from partial (not-yet-valid) JSON,
# so progress feedback can name the file being written before the call closes.
_PARTIAL_PATH_RE = re.compile(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"')
_ALLOWED_POLICY = PolicyDecision(True, "allowed", set())

# Capabilities that mutate the workspace or run external processes. In "ask"
# mode these prompt for approval even when the policy already allows them; in
# "auto" mode they run freely and only a policy denial escalates to the user.
_APPROVAL_SENSITIVE_CAPABILITIES = frozenset(
    {
        ToolCapability.LOCAL_WRITE,
        ToolCapability.PROCESS,
        ToolCapability.INTERACTIVE_TERMINAL,
        ToolCapability.COMPUTER_CONTROL,
        # Delegating runs sub-agents that may themselves write and run
        # processes, so the user approves the delegation once at this boundary.
        ToolCapability.DELEGATE,
    }
)


@dataclass(slots=True)
class TurnResult:
    text: str
    response: ModelResponse | None
    cancelled: bool = False
    error: str | None = None
    # Set when the turn was wound down by a runtime safety budget instead of the
    # model finishing on its own (see WIND_DOWN_* constants). ``text`` then holds
    # a best-effort answer, so callers must not treat the turn as a clean success.
    wind_down_reason: str | None = None


# Wind-down reasons surfaced through ``TurnResult.wind_down_reason``.
WIND_DOWN_TIME_BUDGET = "turn_time_budget_exhausted"
WIND_DOWN_STEP_BUDGET = "model_step_budget_exhausted"
WIND_DOWN_TOOL_BUDGET = "tool_call_budget_exhausted"
WIND_DOWN_STALLED = "model_stalled"


@dataclass(slots=True)
class _TurnState:
    cancel_event: asyncio.Event | None
    deadline: float
    tool_calls_executed: int = 0
    last_response: ModelResponse | None = None
    progress_signature: tuple[object, ...] = ()
    stall_rounds: int = 0
    stall_nudged: bool = False
    tool_format_retries: int = 0
    budget_overflow_retries: int = 0
    no_tool_nudged: bool = False
    seen_call_fingerprints: set[str] = field(default_factory=set)
    # Whether the current model step streamed any visible text on the "answer"
    # channel. Reset per step. When a turn ends in prose that only ever streamed
    # on the "working" channel (tool-required phases), the finish path must
    # announce that prose as the final answer or the user never sees a message.
    step_streamed_answer: bool = False
    # Inputs for the post-turn reflection digest: what the user asked and one
    # compact line per executed tool call.
    user_text: str = ""
    actions: list[str] = field(default_factory=list)


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
        failure_memory: FailureMemoryStore | None = None,
        memory: MemoryService | None = None,
        rules: RulesService | None = None,
        skills_catalog: Callable[[], str] | None = None,
        system_prompt_builder: Callable[[], str] | None = None,
        reflection: ReflectionService | None = None,
    ) -> None:
        self.config = config
        # Optional override for how the leading system message is (re)built. The
        # main agent leaves this unset and uses ``build_system_prompt`` with its
        # learned lessons/memories/rules. A sub-agent injects its profile's own
        # role prompt here, so ``_refresh_system_prompt`` keeps the delegated
        # persona instead of overwriting it with the top-level agent prompt.
        self._system_prompt_builder = system_prompt_builder
        self.provider = provider
        self.tool_registry = tool_registry
        self.conversation = conversation
        self.usage = usage
        self.event_bus = event_bus
        self.compressor = compressor
        self.tool_context_factory = tool_context_factory
        self.planner = planner
        # Persistent cross-session memory of recurring failures; ``None`` keeps
        # the loop fully functional, just without learning.
        self.failure_memory = failure_memory
        # Durable memory of user-stated and proactively-saved facts; ``None``
        # simply means nothing is injected.
        self.memory = memory
        # Mandatory rules re-read on each prompt rebuild so a rule created
        # mid-session takes effect without a restart; ``None`` injects nothing.
        self.rules = rules
        # Renders the available-skills catalog, re-read on each prompt rebuild so
        # a skill created mid-session becomes visible next turn; ``None`` injects
        # nothing (kept optional so directly-constructed agents/tests stay clean).
        self._skills_catalog = skills_catalog
        # Interactive approver. Defaults to deny-all so non-interactive runs keep
        # the prior behaviour; the terminal UI swaps in a modal-backed gateway.
        self.approval_gateway: ApprovalGateway = approval_gateway or DenyAllGateway()
        # Signatures the user chose to "always allow" for this session.
        self._session_allowlist: set[str] = set()
        # Post-turn learning. ``None`` disables reflection; the background task
        # is tracked so shutdown can give it a bounded chance to finish.
        self.reflection = reflection
        self._learning_task: asyncio.Task[None] | None = None
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

    def _refresh_system_prompt(self) -> None:
        """Rebuild the system message so freshly-learned lessons and memories are
        actually seen by the model.

        The system prompt is otherwise built once at startup and frozen for the
        session, which means a lesson recorded mid-session (or a fact just saved
        via ``remember``) would never reach the model until a restart — the
        original cause of the agent repeating known mistakes. We rebuild in place
        at well-defined points (turn start, after recording a failure, after a
        ``remember`` call) rather than every step, to keep disk reads bounded.
        """

        if not self.conversation.messages:
            return
        if self._system_prompt_builder is not None:
            self.conversation.messages[0] = Message(
                role="system", content=self._system_prompt_builder()
            )
            return
        lessons = (
            self.failure_memory.render_for_prompt(
                limit=self.config.memory.lessons_render_limit
            )
            if self.failure_memory
            else ""
        )
        memories = (
            self.memory.render_for_prompt(
                limit_per_kind=self.config.memory.render_limit_per_kind
            )
            if self.memory
            else ""
        )
        rules = self.rules.render_for_prompt() if self.rules else ""
        skills = self._skills_catalog() if self._skills_catalog else ""
        self.conversation.messages[0] = Message(
            role="system",
            content=build_system_prompt(
                workspace=self.config.workspace,
                language=self.config.language,
                lessons=lessons,
                memories=memories,
                rules=rules,
                skills=skills,
            ),
        )

    # ------------------------------------------------------------------ #
    # Turn entry point
    # ------------------------------------------------------------------ #
    async def run_turn(
        self,
        text: str,
        *,
        cancel_event: asyncio.Event | None = None,
        context: str = "",
        resume_plan: bool = False,
        images: list[ImageContent] | None = None,
    ) -> TurnResult:
        # Pull in any lessons/memories learned since this session's system prompt
        # was built, so the model benefits from them on this turn.
        self._refresh_system_prompt()
        await self.set_state(AgentState.CALLING_MODEL, phase="accepted_user_message")
        await self.event_bus.emit("user.message", {"text": text}, source="core.orchestrator")
        # Editor context (open file / selection forwarded by an embedding client)
        # is added to the conversation so the model sees it, but it is *not*
        # echoed as a `user.message` event — the transcript stays clean and only
        # shows what the user actually typed.
        if context:
            self.conversation.add_user(context)
        if images and self._vision_model():
            analysis = await self._describe_images(text, images)
            if analysis is not None:
                # The vision model already turned the pixels into text; keep the
                # raw images out of the conversation so a non-multimodal main
                # model never receives payloads it cannot read.
                self.conversation.add_user(analysis)
                images = None
        self.conversation.add_user(text, images=images)

        state = _TurnState(
            cancel_event=cancel_event,
            deadline=time.monotonic() + self.config.budgets.turn_timeout(),
            user_text=text,
        )
        try:
            early = await self._begin_planner(text, state, resume=resume_plan)
            if early is not None:
                return early
            state.progress_signature = self._progress_signature()
            result = await self._run_model_loop(state)
            self._maybe_schedule_reflection(state, result)
            return result
        except CancellationError:
            await self._suspend_plan_sidebar()
            await self.set_state(AgentState.READY, phase="waiting_user")
            await self.event_bus.emit("turn.cancelled", {}, source="core.orchestrator")
            return TurnResult(text="", response=state.last_response, cancelled=True)
        except ProviderError as exc:
            # Provider exhausted retries: degrade gracefully instead of crashing the turn.
            await self._suspend_plan_sidebar()
            await self._emit_error(exc)
            await self.set_state(AgentState.FAILED, phase="failed")
            return TurnResult(
                text=self._best_effort_text(state),
                response=state.last_response,
                error=str(exc),
            )
        except Exception as exc:
            await self._suspend_plan_sidebar()
            await self._emit_error(exc)
            await self.set_state(AgentState.FAILED, phase="failed")
            raise

    def _vision_model(self) -> str:
        """The configured image-analysis model, or "" when images should go
        straight to the main model (multimodal setups, or same model anyway)."""
        model = self.config.vision_model.strip()
        return "" if model == self.config.model.strip() else model

    async def _describe_images(self, text: str, images: list[ImageContent]) -> str | None:
        """Turn attached images into text via the configured vision model.

        The main model may not be multimodal, so ``vision_model`` acts as its
        eyes: a one-off call outside the conversation (no tools, no history)
        produces a task-focused description that travels as plain text instead
        of pixels. Returns None on any failure so the caller can degrade to
        attaching the raw images, which is exactly the pre-vision behavior.
        """
        model = self._vision_model()
        await self.set_state(AgentState.CALLING_MODEL, phase="analyzing_images")
        await self.event_bus.emit(
            "vision.analysis.started",
            {"model": model, "images": len(images)},
            source="core.orchestrator",
        )
        request = ModelRequest(
            model=model,
            messages=[
                Message(role="system", content=VISION_ANALYSIS_PROMPT),
                Message(
                    role="user",
                    content=(
                        "Describe the attached images. The user's request they "
                        f"belong to, for context only:\n{text}"
                    ),
                    images=list(images),
                ),
            ],
            # Transcribing dense screenshots takes room, and reasoning models
            # spend output budget on hidden thinking first.
            max_output_tokens=8192,
        )
        try:
            response = await self.provider.complete(request)
        except Exception as exc:
            await self.event_bus.emit(
                "vision.analysis.failed",
                {"model": model, "error": str(exc)},
                source="core.orchestrator",
            )
            return None
        description = (response.text or "").strip()
        if not description:
            await self.event_bus.emit(
                "vision.analysis.failed",
                {"model": model, "error": "empty response"},
                source="core.orchestrator",
            )
            return None
        await self.event_bus.emit(
            "vision.analysis.completed",
            {"model": model, "images": len(images)},
            source="core.orchestrator",
        )
        return (
            f"[Image analysis by {model}] The next user message references "
            "attached images; this is what they contain, transcribed by a "
            "vision model:\n\n" + description
        )

    async def _begin_planner(
        self, text: str, state: _TurnState, *, resume: bool = False
    ) -> TurnResult | None:
        if not (self.planner and self.planner.enabled):
            return None
        await self.planner.begin_turn(
            text,
            provider_supports_tools=self.provider.capabilities.tool_calling,
            resume=resume,
        )
        if self.planner.should_auto_list_workspace() and self.tool_registry.has("list_files"):
            state.tool_calls_executed += 1
            await self._execute_host_tool(
                "host_list_files_initial",
                "list_files",
                {"path": ".", "max_depth": 2, "max_entries": 250},
                state,
            )
        # PLAN mode used to short-circuit here, returning a canned summary of the
        # internal skeleton without ever calling the model — so the user's actual
        # request was never read and the turn looked frozen. Instead, fall through
        # to the model loop: the model investigates and authors a real plan, while
        # the tool policy keeps write/process tools denied so nothing is mutated.
        return None

    # ------------------------------------------------------------------ #
    # Model loop
    # ------------------------------------------------------------------ #
    async def _run_model_loop(self, state: _TurnState) -> TurnResult:
        for step in range(self.config.budgets.max_model_steps):
            self._raise_if_cancelled(state.cancel_event)
            if time.monotonic() > state.deadline:
                return await self._wind_down(state, reason=WIND_DOWN_TIME_BUDGET)

            allowed = self._allowed_tool_names()
            tool_definitions = self.tool_registry.definitions(allowed)
            compression = await self.compressor.ensure_capacity(self.conversation, tool_definitions)
            await self.emit_context_usage(compression)

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

            state.step_streamed_answer = False
            response = await self._run_model_step(request, state)
            state.last_response = response
            self.usage.add(response.usage)
            if response.response_id:
                self.conversation.previous_response_id = response.response_id
            await self._recover_text_tool_calls(response, tool_definitions)
            await self._emit_response_completed(response)

            if not response.tool_calls:
                if await self._retry_malformed_tool_call(response, state):
                    continue
                if await self._retry_budget_overflow(response, request, state):
                    continue
                outcome = await self._handle_no_tool_response(response, state)
                if outcome is not None:
                    return outcome
                continue

            self.conversation.add_assistant(response.text or "", response.tool_calls)
            outcome = await self._execute_tool_batch(response, state)
            if outcome is not None:
                return outcome
            # A fact just saved via ``remember`` should inform the very next step.
            if any(call.name == "remember" for call in response.tool_calls):
                self._refresh_system_prompt()
            outcome = await self._note_tool_round(response, state)
            if outcome is not None:
                return outcome
            await self.set_state(AgentState.CALLING_MODEL, phase="calling_model_after_tools")

        return await self._wind_down(state, reason=WIND_DOWN_STEP_BUDGET)

    async def _handle_no_tool_response(
        self, response: ModelResponse, state: _TurnState
    ) -> TurnResult | None:
        # Fail-open. The surface classifier may still mislabel a request as a
        # mutation (keyword heuristics are inherently imperfect), so we nudge
        # the model toward tools at most once. If it still answers in
        # prose, we surface *its* answer rather than spiralling into repeated
        # corrections and ultimately handing the user a system message instead of
        # a reply. The only hard completion gate is the evidence-based
        # complete_task check, not this path.
        if self._requires_tool_for_progress() and not state.no_tool_nudged:
            state.no_tool_nudged = True
            if response.text:
                self.conversation.add_assistant(
                    bound_text(response.text, self.config.budgets.max_tool_output_chars), []
                )
            correction = await self.planner.note_no_tool_response(
                recommended_tool_names=self._recommended_tool_names()
            )
            self.conversation.add_user(correction)
            await self.set_state(AgentState.CALLING_MODEL, phase="correcting_no_tool_response")
            return None

        if response.text:
            self.conversation.add_assistant(response.text, response.tool_calls)
            if self.planner and self.planner.enabled:
                # The prose answer is the final checklist step's execution when
                # the model already declared that step done; settle the sidebar
                # instead of leaving its last step spinning after the turn.
                await self.planner.settle_agent_plan_on_final_answer()
        # Prose that streamed on the "working" channel (tool-required phases)
        # was rendered as dim trace only; announce it so the turn's actual
        # answer reaches the user as a message.
        return await self._finish_turn(
            response.text, response, state, announce_final=not state.step_streamed_answer
        )

    async def _recover_text_tool_calls(
        self, response: ModelResponse, tool_definitions: list
    ) -> None:
        """Promote tool calls that a weak model printed as text into real calls.

        When the model returns no structured tool calls but did offer text that
        encodes a call to a tool we actually exposed this step, rewrite the
        response so the runtime executes it instead of surfacing the raw markup
        as the final chat answer.

        Weak local models also mash the channels together: a call can land inside
        (or right after an unterminated) ``<think>`` block, so the markup ends up
        in the reasoning rather than the answer. We fall back to the reasoning
        channel only when the answer yielded nothing *and* the reasoning carries
        explicit ``<tool_call>``/``<function=`` markup. That keeps a real model's
        natural-language reasoning summary — which may merely mention a tool or
        quote a JSON blob — from being misread as an actual call.
        """
        if response.tool_calls:
            return
        known_names = {definition.name for definition in tool_definitions}
        if not known_names:
            # The step offered no tools (e.g. a turn misclassified as chat).
            # Fall back to the full registry so leaked call markup is still
            # recovered instead of surfacing raw in the chat. Recovery only
            # accepts markup naming a real tool, so prose stays untouched.
            known_names = set(self.tool_registry.names())
        if not known_names:
            return
        recovered, cleaned_text = recover_tool_calls_from_text(
            response.text, known_names
        )
        cleaned_reasoning = response.reasoning
        if not recovered and looks_like_attempted_tool_call(response.reasoning):
            recovered, cleaned_reasoning = recover_tool_calls_from_text(
                response.reasoning, known_names
            )
        if not recovered:
            return
        response.tool_calls = recovered
        response.text = cleaned_text
        response.reasoning = cleaned_reasoning
        response.finish_reason = FinishReason.TOOL_CALLS
        await self.event_bus.emit(
            "tool.calls.recovered",
            {
                "count": len(recovered),
                "names": [call.name for call in recovered],
                # The cleaned prose so the UI can replace the raw call text it
                # already streamed into the chat.
                "text": cleaned_text,
            },
            source="core.orchestrator",
        )

    async def _retry_malformed_tool_call(
        self, response: ModelResponse, state: _TurnState
    ) -> bool:
        """Re-prompt a model that emitted an unparseable tool call as text.

        Recovery already ran: if structured calls exist, or the text holds no
        tool-call markup, there is nothing to retry. Otherwise the model tried to
        call a tool but botched the format (wrong tool name, broken JSON, a
        truncated block). Rather than surface that markup as the final answer we
        drop it, nudge the model toward the proper format, and let the loop run
        another step. Bounded by ``_MAX_TOOL_FORMAT_RETRIES`` so a model that
        cannot comply still terminates with a best-effort reply.
        """
        if response.tool_calls:
            return False
        if not looks_like_attempted_tool_call(
            response.text
        ) and not looks_like_attempted_tool_call(response.reasoning):
            return False
        if state.tool_format_retries >= _MAX_TOOL_FORMAT_RETRIES:
            return False
        if state.tool_format_retries == 0:
            # Learn from the first botched format this turn, not every retry.
            await self._record_failure(
                trigger="malformed_tool_call",
                context=(
                    "The model tried to call a tool but emitted unparseable markup "
                    "as text instead of a structured function call. Offending "
                    f"output:\n{bound_text(response.text or response.reasoning, 600)}"
                ),
                fallback_lesson=(
                    "Invoke tools through the function-calling interface with valid "
                    "JSON arguments; never print the tool call as text or prose."
                ),
            )
        state.tool_format_retries += 1
        # Do not persist the malformed markup as the assistant turn; only keep
        # the correction so the next attempt is not anchored to broken output.
        self.conversation.add_user(self._tool_format_correction_text())
        await self.event_bus.emit(
            "tool.call.malformed",
            {
                "attempt": state.tool_format_retries,
                "max_attempts": _MAX_TOOL_FORMAT_RETRIES,
            },
            source="core.orchestrator",
        )
        await self.set_state(
            AgentState.CALLING_MODEL, phase="retrying_malformed_tool_call"
        )
        return True

    @staticmethod
    def _tool_format_correction_text() -> str:
        return (
            "Your previous message tried to call a tool but the call could not be "
            "parsed. Do not print the call as text. Invoke the tool through the "
            "function-calling interface, using the exact tool name and valid JSON "
            "arguments. If no tool is needed, answer the user directly instead."
        )

    # ------------------------------------------------------------------ #
    # Budget overflow: model burned its whole output budget without acting
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_truncated(response: ModelResponse, request: ModelRequest) -> bool:
        """True when the model hit the output-token ceiling mid-generation.

        ``finish_reason`` alone is unreliable: ollama/qwen report ``"stop"`` even
        when truncated by length, so we also treat "used the entire output
        budget" (provider-reported ``output_tokens`` reaching the requested cap)
        as truncation.
        """
        if response.finish_reason == FinishReason.LENGTH:
            return True
        cap = request.max_output_tokens
        usage = response.usage
        if cap and usage and usage.output_tokens >= cap:
            return True
        return False

    async def _retry_budget_overflow(
        self, response: ModelResponse, request: ModelRequest, state: _TurnState
    ) -> bool:
        """Recover a turn where the model spent its whole budget without acting.

        The classic failure: a thinking model pours the entire output budget into
        the reasoning channel, emits no tool call and no answer, and the stream
        ends — leaving the loop nothing to do, so the turn dies silently. Here we
        detect the truncation, record the lesson, and hand the model back its own
        truncated thinking plus a nudge to commit to one concrete action (and to
        slice large file writes). Bounded by ``_MAX_BUDGET_RETRIES``.
        """
        if response.text:
            # It produced a real answer; truncated or not, there is something to
            # surface — let the normal no-tool path handle it.
            return False
        if not self._is_truncated(response, request):
            return False

        await self._record_failure(
            trigger="token_budget_exceeded",
            context=(
                "The model used its entire output-token budget of "
                f"{request.max_output_tokens} tokens producing reasoning without "
                "emitting a tool call or a final answer. Truncated reasoning:\n"
                f"{(response.reasoning or '').strip()[:600]}"
            ),
            fallback_lesson=(
                "Do not spend the whole output budget thinking. Commit to one "
                "concrete tool call early, and write large files incrementally "
                "(create, then extend with edit_code) instead of emitting a huge "
                "file in a single call."
            ),
        )

        if state.budget_overflow_retries >= _MAX_BUDGET_RETRIES:
            return False
        state.budget_overflow_retries += 1
        self.conversation.add_user(
            self._budget_overflow_text(response.reasoning, request.max_output_tokens)
        )
        await self.event_bus.emit(
            "model.budget_overflow",
            {
                "attempt": state.budget_overflow_retries,
                "max_attempts": _MAX_BUDGET_RETRIES,
                "output_token_cap": request.max_output_tokens,
                "output_tokens": response.usage.output_tokens if response.usage else None,
            },
            source="core.orchestrator",
        )
        await self.set_state(AgentState.CALLING_MODEL, phase="retrying_budget_overflow")
        return True

    @staticmethod
    def _budget_overflow_text(reasoning: str, cap: int | None) -> str:
        limit = f"{cap} tokens" if cap else "the output-token limit"
        recap = (reasoning or "").strip()
        if recap:
            recap = bound_text(recap, 1500)
            thought = f"\n\nThis is what you were thinking when you ran out:\n{recap}"
        else:
            thought = ""
        return (
            f"You thought too much and ran out of output budget ({limit}) before "
            "doing anything — no tool call and no answer reached me, so nothing "
            "happened." + thought + "\n\nDo not re-think the whole problem. Pick the "
            "single next concrete action and take it now as one tool call. If it is "
            "a large file, do not emit it all at once: create or edit it in smaller "
            "chunks (e.g. write a skeleton, then extend it with edit_code) so each "
            "step fits the budget."
        )

    # ------------------------------------------------------------------ #
    # Post-turn reflection: distill durable memories in the background
    # ------------------------------------------------------------------ #
    def _maybe_schedule_reflection(self, state: _TurnState, result: TurnResult) -> None:
        """Kick off the background learning pass for a finished turn.

        Runs after the reply is already on its way to the user, so it costs the
        turn nothing. Skipped for cancelled/failed turns (nothing trustworthy
        to learn), for trivial turns (below the tool-call threshold), and while
        a previous pass is still running (learning must never queue up).
        """

        if self.reflection is None or result.cancelled or result.error is not None:
            return
        if not self.reflection.should_reflect(tool_calls_executed=state.tool_calls_executed):
            return
        if self._learning_task is not None and not self._learning_task.done():
            return
        digest = TurnDigest(
            user_text=state.user_text,
            final_text=result.text,
            actions=tuple(state.actions),
            evidence=self._turn_evidence_summary(),
            outcome=result.wind_down_reason or "success",
        )
        self._learning_task = asyncio.create_task(self._run_learning(digest))

    async def _run_learning(self, digest: TurnDigest) -> None:
        try:
            report = await self.reflection.reflect_on_turn(digest)
            # Same background lane, strictly after reflection: curate any store
            # that grew past its threshold, so cost stays one pass at a time.
            consolidated = await self.reflection.maybe_consolidate()
            if report.changed or consolidated:
                # Make what was just learned visible on the very next turn.
                self._refresh_system_prompt()
        except asyncio.CancelledError:  # shutdown drained us; nothing to salvage
            raise
        except Exception:  # noqa: BLE001 - learning must never surface as an error
            logger.debug("Post-turn learning pass failed.", exc_info=True)

    async def drain_learning(self, *, timeout: float = 30.0) -> None:
        """Give a pending background learning pass a bounded chance to finish.

        Called on shutdown before the provider closes; without it a headless
        run would exit before its only reflection completes. On timeout the
        pass is cancelled — learning is best-effort, shutdown is not.
        """

        task = self._learning_task
        if task is None or task.done():
            return
        done, _pending = await asyncio.wait({task}, timeout=timeout)
        if task not in done:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    def _turn_evidence_summary(self) -> str:
        """The planner's recent evidence ledger, serialized for the digest."""

        if not (self.planner and self.planner.enabled):
            return ""
        try:
            return json.dumps(self.planner.ledger.compact_recent(limit=12), default=str)
        except Exception:  # noqa: BLE001 - the digest is best-effort input
            return ""

    async def _record_failure(
        self, *, trigger: str, context: str, fallback_lesson: str, signature: str | None = None
    ) -> None:
        """Persist a lesson for a recurring failure, if memory is enabled.

        Never lets the learning path break the turn that triggered it: the store
        already swallows generator errors, and we guard the call itself too.
        """
        if self.failure_memory is None:
            return
        try:
            await self.failure_memory.record(
                trigger=trigger,
                context=context,
                fallback_lesson=fallback_lesson,
                signature=signature,
            )
            # Surface the just-learned lesson immediately so a failure that
            # recurs later in this same turn no longer slips past the model.
            self._refresh_system_prompt()
        except Exception as exc:  # pragma: no cover - defensive
            await self.event_bus.emit(
                "memory.record.failed",
                {"error": str(exc)},
                source="core.orchestrator",
            )

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
            await self._record_failure(
                trigger="stall",
                context=(
                    "The model stalled: it kept producing the same non-progressing "
                    f"output for {state.stall_rounds} rounds without advancing the "
                    "task toward completion."
                ),
                fallback_lesson=(
                    "When repeating yourself without progress, change approach: "
                    "re-read the relevant files or break the task into a smaller, "
                    "concrete next step instead of restating the plan."
                ),
            )
            return await self._wind_down(state, reason=WIND_DOWN_STALLED)
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
                    self._collect_model_response(request, state, streamed),
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
        state: _TurnState,
        streamed_sink: list[str],
    ) -> ModelResponse:
        cancel_event = state.cancel_event
        reasoning_parts: list[str] = []
        completed: ModelResponse | None = None
        # Two streaming guards on the visible text: peel inline <think> reasoning
        # off into the thinking channel, then hold any embedded tool-call markup
        # back from the chat. Both keep the full text flowing for recovery.
        reasoning_filter = ReasoningTagFilter()
        text_filter = ToolCallStreamFilter()

        async def _emit_visible_answer(answer: str) -> None:
            streamed_sink.append(answer)
            visible = text_filter.feed(answer)
            if visible:
                await self._emit_text_delta(visible, state)

        # Per-index high-water mark of already-announced argument length, so a
        # streaming tool call reports progress periodically instead of on every
        # tiny chunk (which would flood the UI).
        tool_progress_seen: dict[int, int] = {}

        async for event in self.provider.stream(request):
            self._raise_if_cancelled(cancel_event)
            if event.kind == "text_delta":
                answer, thought = reasoning_filter.feed(event.text_delta)
                if thought:
                    reasoning_parts.append(thought)
                    await self._emit_reasoning_delta(thought)
                if answer:
                    await _emit_visible_answer(answer)
                continue
            if event.kind == "tool_call_delta":
                await self._emit_tool_progress(event, tool_progress_seen)
                continue
            await self._emit_provider_event(event)
            if event.kind == "reasoning_delta":
                reasoning_parts.append(event.reasoning_delta)
            elif event.kind == "completed" and event.response:
                completed = event.response

        answer_tail, reasoning_tail = reasoning_filter.flush()
        if reasoning_tail:
            reasoning_parts.append(reasoning_tail)
            await self._emit_reasoning_delta(reasoning_tail)
        if answer_tail:
            # A short literal hold (e.g. a closing backtick) released at end of
            # stream still belongs to the visible answer; route it through the
            # same tool-call filter as every other answer fragment.
            await _emit_visible_answer(answer_tail)
        tail = text_filter.flush()
        if tail:
            await self._emit_text_delta(tail, state)

        if completed is None:
            return ModelResponse(
                text="".join(streamed_sink),
                reasoning="".join(reasoning_parts),
                finish_reason=FinishReason.UNKNOWN,
            )
        # Strip any <think> block the provider left inline so reasoning never
        # pollutes the answer, conversation history, or tool-call recovery.
        answer, inline_reasoning = split_reasoning_tags(completed.text)
        completed.text = answer
        if inline_reasoning and not completed.reasoning:
            completed.reasoning = inline_reasoning
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
            return await self._wind_down(state, reason=WIND_DOWN_TOOL_BUDGET)
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
            state.actions.append(
                self._action_line(call.name, call.arguments, outcome.result.is_error)
            )
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

        # A successful ask_user call blocks on the user by contract, so the turn
        # ends here with the question as the final answer. Feeding the "blocked"
        # tool result back into the model instead only burned extra steps on
        # "I'm waiting" prose that rendered as dim working trace, while the
        # question itself never reached the user as a real message.
        question = self._blocking_question(calls, outcomes)
        if question is not None:
            return await self._finish_turn(question, response, state, announce_final=True)
        return None

    @staticmethod
    def _blocking_question(
        calls: list[ToolCall], outcomes: dict[str, _ToolOutcome]
    ) -> str | None:
        """User-facing text of a successful ask_user call in the batch, or None.

        Choices are folded into the message as a numbered list so the user can
        answer by number in their next message.
        """
        for call in calls:
            outcome = outcomes.get(call.id)
            if call.name != "ask_user" or outcome is None or outcome.result.is_error:
                continue
            payload = outcome.payload or {}
            question = str(payload.get("question") or "").strip()
            if not question:
                continue
            raw_choices = payload.get("choices")
            choices = [
                choice.strip()
                for choice in (raw_choices if isinstance(raw_choices, list) else [])
                if isinstance(choice, str) and choice.strip()
            ]
            if choices:
                numbered = "\n".join(
                    f"{index}. {choice}" for index, choice in enumerate(choices, start=1)
                )
                question = f"{question}\n\n{numbered}"
            return question
        return None

    @staticmethod
    def _action_line(name: str, arguments: dict[str, object], failed: bool) -> str:
        """One compact ``tool(args) -> ok|error`` line for the reflection digest."""

        try:
            args = json.dumps(arguments, default=str, ensure_ascii=False)
        except Exception:  # noqa: BLE001 - digest input only
            args = "{}"
        return f"{name}({bound_text(args, 160)}) -> {'error' if failed else 'ok'}"

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
        state.actions.append(self._action_line(name, arguments, outcome.result.is_error))
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
        # Evidence preconditions run before the approval prompt: a call the
        # planner will defer for missing evidence should never cost the user an
        # approval decision. The gate is advisory (one nudge, then fail-open),
        # so this can delay a call by one round-trip but never trap the turn.
        precondition_gap = (
            self.planner.precondition_gap(call.name, call.arguments)
            if self.planner and self.planner.enabled
            else None
        )
        if precondition_gap:
            await self.event_bus.emit(
                "tool.call.failed",
                {
                    "tool_call_id": call.id,
                    "name": call.name,
                    "message": precondition_gap,
                    "type": "ToolPreconditionDeferred",
                },
                source="core.orchestrator",
            )
            return _ToolOutcome(
                result=ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=precondition_gap,
                    is_error=True,
                ),
                payload=None,
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
            if self.planner and self.planner.enabled and call.name == "complete_plan_step":
                # On the final step the later planner advance is a deliberate
                # no-op; the result must say so instead of echoing success.
                payload = self.planner.annotate_plan_step_payload(payload)
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
            content = str(exc)
            if isinstance(exc, WorkspaceBoundaryError):
                # The model tried to touch a file outside the workspace. Teach the
                # planner the task's real target is external (so completion stops
                # demanding workspace file evidence) and point the model at the
                # one channel that can do it, instead of leaving it to invent
                # workspace files that satisfy the evidence gate.
                if self.planner and self.planner.enabled:
                    await self.planner.note_workspace_boundary_rejection(
                        call.name, call.arguments
                    )
                content = (
                    f"{exc}\nFile tools only operate inside the workspace. To "
                    "change a file outside the workspace, use execute_command "
                    "(subject to user approval) and confirm the result with a "
                    "read-back command. Do not create or edit workspace files "
                    "just to satisfy completion evidence."
                )
            await self._record_failure(
                trigger="tool_error",
                signature=f"tool_error:{call.name}",
                context=(
                    f"The tool '{call.name}' failed with: {bound_text(str(exc), 400)}. "
                    f"Arguments: {bound_text(json.dumps(call.arguments, default=str), 400)}"
                ),
                fallback_lesson=(
                    f"Before calling '{call.name}', validate its arguments against "
                    "the workspace state (paths exist, JSON is well-formed) to avoid "
                    "the error seen previously."
                ),
            )
            return _ToolOutcome(
                result=ToolResult(
                    tool_call_id=call.id, name=call.name, content=content, is_error=True
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
        # A rejection for real evidence gaps is a recurring failure class worth
        # a lesson: the model claimed done without proof. The double-check
        # round-trip is pacing, not failure, so it never records one.
        if not self.planner.double_check_pending:
            await self._record_failure(
                trigger="completion_rejected",
                context=(
                    "The model called complete_task but the evidence gate "
                    "rejected the claim. Missing requirements: "
                    + bound_text(
                        "; ".join(decision.missing_requirements) or "(unspecified)",
                        600,
                    )
                ),
                fallback_lesson=(
                    "Do not claim completion before the evidence exists: run the "
                    "project's verification command and read back the changed "
                    "files, then call complete_task."
                ),
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
        return await self._finish_turn(
            text,
            state.last_response,
            state,
            wind_down_reason=reason,
            announce_final=not state.step_streamed_answer,
        )

    async def _suspend_plan_sidebar(self) -> None:
        """Stop the checklist's running step whenever a turn hands control back.

        Called on every turn exit path - clean finish, blocking question,
        wind-down, cancellation, provider failure, unexpected error - so a plan
        the turn did not settle can never keep a step spinning while the agent
        sits in ``waiting_user``. Best-effort by design: settling the sidebar
        must never mask the error that ended the turn.
        """
        if not (self.planner and self.planner.enabled):
            return
        try:
            await self.planner.suspend_agent_plan()
        except Exception:  # pragma: no cover - defensive: UI settling only
            logger.exception("Failed to suspend the plan sidebar at turn end.")

    async def _finish_turn(
        self,
        text: str,
        response: ModelResponse | None,
        state: _TurnState,
        *,
        error: str | None = None,
        wind_down_reason: str | None = None,
        announce_final: bool = False,
    ) -> TurnResult:
        # ``assistant.final`` is what UIs render as the agent's answer message.
        # The planner emits it when it accepts a completion claim, but turns can
        # also end with user-facing text that never streamed on the answer
        # channel (a blocking question, prose finishing a tool-required task, a
        # wind-down summary). Those callers pass ``announce_final`` so the text
        # reaches the user as a real message instead of dying in the dim trace.
        if announce_final and text.strip():
            await self.event_bus.emit(
                "assistant.final", {"text": text}, source="core.orchestrator"
            )
        await self._suspend_plan_sidebar()
        await self.set_state(AgentState.READY, phase="waiting_user")
        await self.event_bus.emit(
            "turn.completed",
            {"text": text, "usage": self.usage.to_dict()},
            source="core.orchestrator",
        )
        return TurnResult(
            text=text,
            response=response,
            error=error,
            wind_down_reason=wind_down_reason,
        )

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
    async def emit_context_usage(self, compression: CompressionResult) -> None:
        """Publish the context-meter payload after a compression pass.

        Called both before every model request and after a manual /compact, so
        the UI's context bar reflects the post-compaction token count right
        away instead of waiting for the next turn.
        """
        await self.event_bus.emit(
            "usage.updated",
            {
                "active_context_tokens": compression.active_tokens,
                "active_context_estimated": compression.estimated,
                "context_budget": self.compressor.budget,
                "context_threshold": self.compressor.threshold,
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

    async def _emit_text_delta(self, text: str, state: _TurnState | None = None) -> None:
        channel = "working" if self._requires_tool_for_progress() else "answer"
        if state is not None and text and channel == "answer":
            state.step_streamed_answer = True
        await self.event_bus.emit(
            "model.stream.delta",
            {"text": text, "channel": channel},
            source="provider",
        )

    async def _emit_reasoning_delta(self, text: str) -> None:
        await self.event_bus.emit(
            "model.thinking.delta",
            {"text": text},
            source="provider",
        )

    async def _emit_tool_progress(
        self, event: ProviderEvent, seen: dict[int, int]
    ) -> None:
        """Announce that a tool call's arguments are still streaming in.

        Emits a throttled ``tool.call.progress`` event so the UI can show live
        feedback (e.g. a file being written) while a large call accumulates,
        instead of appearing frozen until the whole call has arrived. The name
        may still be empty on the very first fragments; we wait until it is known
        so the feedback is meaningful.
        """

        name = event.tool_call_name
        if not name:
            return
        arguments = event.tool_call_arguments
        length = len(arguments)
        last = seen.get(event.tool_call_index)
        if last is not None and length - last < _TOOL_PROGRESS_STEP_CHARS:
            return
        seen[event.tool_call_index] = length

        payload: dict[str, object] = {
            "name": name,
            "index": event.tool_call_index,
            "chars": length,
        }
        path = _extract_partial_path(arguments)
        if path:
            payload["path"] = path
        if name in {"write_file", "edit_code"}:
            # Content newlines are JSON-escaped as the two characters "\n", so
            # counting them approximates how many lines have been written so far.
            payload["lines"] = arguments.count("\\n") + 1
        await self.event_bus.emit("tool.call.progress", payload, source="provider")

    async def _emit_provider_event(self, event: ProviderEvent) -> None:
        if event.kind == "text_delta":
            await self._emit_text_delta(event.text_delta)
        elif event.kind == "reasoning_delta":
            await self._emit_reasoning_delta(event.reasoning_delta)
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

    @staticmethod
    def _raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
        if cancel_event and cancel_event.is_set():
            raise CancellationError("Turn cancelled.")


def _extract_partial_path(arguments: str) -> str | None:
    """Pull the ``path`` value out of a partial tool-call arguments string.

    The arguments are still streaming, so the JSON is usually incomplete; but
    ``path`` normally appears near the front, so a lenient regex recovers it well
    before the call closes. Returns ``None`` when no complete path is present yet.
    """

    match = _PARTIAL_PATH_RE.search(arguments)
    if match is None:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return match.group(1)
