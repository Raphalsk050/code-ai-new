from __future__ import annotations

import asyncio
import contextlib
import json
import logging
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
    ImageLimitError,
    ProviderError,
    TransientProviderError,
    WorkspaceBoundaryError,
)
from code_ai.core.git_baseline import GitBaseline
from code_ai.core.memory import FailureMemory, FailureMemoryStore, MemoryService
from code_ai.core.memory_recall import MemoryRecall
from code_ai.core.planning import PlannerService
from code_ai.core.planning.policy import PolicyDecision
from code_ai.core.reflection import ReflectionService, TurnDigest
from code_ai.core.reminders import ReminderEngine, ToolRound
from code_ai.core.rules import RulesService
from code_ai.core.state import AgentState
from code_ai.events.bus import AsyncEventBus
from code_ai.prompts import VISION_ANALYSIS_PROMPT, build_runtime_note, build_system_prompt
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
from code_ai.util.partial_json import PartialObjectDecoder

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
# How many times to re-issue a step whose tool call was cut off mid-stream (a
# model timeout, a provider failure, arguments the provider could not parse).
# Bounded so a stream that keeps breaking still terminates the turn instead of
# re-prompting forever.
_MAX_INTERRUPTED_CALL_RETRIES = 2
_TOOL_GUARD_POLL_SECONDS = 2.0
_TOOL_GUARD_GRACE_SECONDS = 10.0

logger = logging.getLogger(__name__)
# Minimum growth in a streaming tool call's arguments before we emit another
# progress update, so a large write reports periodically rather than per-token.
_TOOL_PROGRESS_STEP_CHARS = 160
# The same idea for the decoded source being written, but finer: this is what
# drives the live code view, and updating it only every 160 raw characters makes
# the file appear in visible jumps instead of flowing in.
_CODE_PROGRESS_STEP_CHARS = 48
# Arguments carrying the source a writing tool is about to commit, in the order
# they are preferred: write_file/create_rule use "content", edit_code the
# replacement text, create_skill the skill body. Only ever read from tools that
# declare LOCAL_WRITE, so a review tool passing code around is not mistaken for
# one writing it.
_CODE_ARGUMENT_KEYS = ("content", "new_text", "instructions")
_PATH_ARGUMENT_KEY = "path"
# The model's plain-language justification for the change. Declared ahead of the
# bulk arguments on the writing tools precisely so it arrives first and the UI
# can open with the reason already on screen.
_REASON_ARGUMENT_KEY = "reason"
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

# Capabilities whose user denial signals "I did not ask you to change anything":
# the planner drops its mutation demand for the rest of the turn. DELEGATE is
# deliberately excluded - denying a delegation means "do it yourself", not
# "stop changing things".
_MUTATION_DENIAL_CAPABILITIES = frozenset(
    {
        ToolCapability.LOCAL_WRITE,
        ToolCapability.PROCESS,
        ToolCapability.INTERACTIVE_TERMINAL,
        ToolCapability.COMPUTER_CONTROL,
    }
)


def _chunked(items: list[ImageContent], size: int) -> list[list[ImageContent]]:
    """Split ``items`` into groups of at most ``size`` (all of it when size < 1)."""

    if size < 1:
        return [list(items)]
    return [items[start : start + size] for start in range(0, len(items), size)]


@dataclass(slots=True)
class TurnResult:
    text: str
    response: ModelResponse | None
    cancelled: bool = False
    error: str | None = None
    # Set when the message was queued into a turn already running rather than
    # starting one of its own, so no answer belongs to this call - the running
    # turn will pick the message up at its next model step.
    queued: bool = False
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
    # Whether a tool call was still streaming in when the current model step
    # ended. Reset per step (and per retry within it). When this is set and the
    # step produced no calls, the call was *lost* rather than never made - a
    # response that looks like a plain prose answer but is really a truncated
    # one. See ``_retry_interrupted_tool_call``.
    tool_call_streaming: bool = False
    interrupted_call_retries: int = 0
    # Inputs for the post-turn reflection digest: what the user asked and one
    # compact line per executed tool call.
    user_text: str = ""
    actions: list[str] = field(default_factory=list)
    # Watches how this turn is using its tools and surfaces the occasional
    # reminder. Per-turn so cooldowns and the per-turn cap reset with the turn.
    reminders: ReminderEngine = field(default_factory=ReminderEngine)
    # Brings a stored memory back when the work reaches its subject. Per-turn so
    # a memory raised once is not raised again in the same turn.
    recall: MemoryRecall | None = None
    # Tools whose past-failure lesson has already been put in front of the model
    # this turn, so the warning lands once and keeps its weight.
    lessons_surfaced: set[str] = field(default_factory=set)
    # Whether any call in the round just executed came back an error. Set while
    # results are recorded and consumed by the round observer straight after.
    round_errored: bool = False
    # Paths git says this turn changed, refreshed after any round that mutated
    # the workspace. Empty when no baseline could be captured.
    git_changed_paths: tuple[str, ...] = ()


@dataclass(slots=True)
class _ToolCallStream:
    """Live decoding state for one tool call whose arguments are streaming in.

    Kept per call index for the duration of a model step so the arguments are
    decoded once, incrementally, instead of being re-parsed on every fragment.
    """

    name: str
    decoder: PartialObjectDecoder
    # Raw argument characters already reported, for throttling the progress line.
    announced_chars: int = 0
    # Decoded source characters already reported, so updates carry only the new
    # tail and the consumer can append instead of re-rendering.
    announced_code: int = 0
    # Which argument holds the source, once the model starts streaming it.
    code_key: str = ""
    # Whether this call may carry source at all (declared LOCAL_WRITE).
    writes: bool = False
    announced: bool = False
    # Whether the consumer has been told the source is final, so the live view
    # is never left showing a write that is still in progress.
    announced_complete: bool = False


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
        workflows_catalog: Callable[[], str] | None = None,
        system_prompt_builder: Callable[[], str] | None = None,
        reflection: ReflectionService | None = None,
        git_baseline: GitBaseline | None = None,
    ) -> None:
        self.config = config
        # Optional override for how the leading system message is (re)built. The
        # main agent leaves this unset and uses ``build_system_prompt`` with its
        # learned lessons/memories/rules. A sub-agent injects its profile's own
        # role prompt here, so ``_refresh_system_prompt`` keeps the delegated
        # persona instead of overwriting it with the top-level agent prompt.
        self._system_prompt_builder = system_prompt_builder
        # Git's account of what the current turn changed, independent of what the
        # tools reported. ``None`` (sub-agents, tests, non-repo workspaces) simply
        # means the ledger stays the only account.
        self.git_baseline = git_baseline
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
        # Renders the available-workflows catalog on each prompt rebuild, so a
        # workflow the user just authored (here or in another agent's directory)
        # is invocable by name without a restart.
        self._workflows_catalog = workflows_catalog
        # Interactive approver. Defaults to deny-all so non-interactive runs keep
        # the prior behaviour; the terminal UI swaps in a modal-backed gateway.
        self.approval_gateway: ApprovalGateway = approval_gateway or DenyAllGateway()
        # Signatures the user chose to "always allow" for this session.
        self._session_allowlist: set[str] = set()
        # Post-turn learning. ``None`` disables reflection; the background task
        # is tracked so shutdown can give it a bounded chance to finish.
        self.reflection = reflection
        self._learning_task: asyncio.Task[None] | None = None
        # Messages typed while a turn is running, waiting to join the
        # conversation at the next model step. See queue_user_message.
        self._queued_user_messages: list[str] = []
        self.state = AgentState.STARTING

    @property
    def permission_mode(self) -> str:
        return self.config.permission_mode

    # ------------------------------------------------------------------ #
    # Steering: messages that arrive mid-turn
    # ------------------------------------------------------------------ #
    def queue_user_message(self, text: str) -> None:
        """Hold a message typed mid-turn until the next model step reads it.

        Steering, not re-tasking: the message joins the conversation as an
        ordinary user turn and the model decides what to do with it. The plan,
        its checklist and the evidence ledger are left alone, because reopening
        those mid-turn would discard the work already done to satisfy them.
        """
        message = text.strip()
        if message:
            self._queued_user_messages.append(message)

    def has_queued_messages(self) -> bool:
        return bool(self._queued_user_messages)

    def take_queued_messages(self) -> list[str]:
        """Remove and return everything still queued."""
        queued, self._queued_user_messages = self._queued_user_messages, []
        return queued

    async def _deliver_queued_user_messages(self) -> None:
        """Move queued messages into the conversation, in the order they came."""
        for message in self.take_queued_messages():
            self.conversation.add_user(message)
            await self.event_bus.emit(
                "user.message.delivered", {"text": message}, source="core.orchestrator"
            )

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
            self._install_system_prompt(self._system_prompt_builder())
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
        workflows = self._workflows_catalog() if self._workflows_catalog else ""
        self._install_system_prompt(
            build_system_prompt(
                workspace=self.config.workspace,
                language=self.config.language,
                lessons=lessons,
                memories=memories,
                rules=rules,
                skills=skills,
                workflows=workflows,
            )
        )

    def _install_system_prompt(self, content: str) -> None:
        """Put the rebuilt prompt at index 0, replacing a system message there.

        Replacing whatever sits first would silently eat a real conversation
        turn if the history ever starts with something else, and prepending
        unconditionally would stack a second system message on every refresh —
        which chat templates reject outright. Only a system message is
        overwritten; anything else gets the prompt inserted ahead of it.
        """
        message = Message(role="system", content=content)
        if self.conversation.messages and self.conversation.messages[0].role == "system":
            self.conversation.messages[0] = message
            return
        self.conversation.messages.insert(0, message)

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
        # Post-turn learning must not compete with the user for the model. On a
        # local server a background reflection holds the whole thing: measured
        # against a 35B, a turn landing behind one waited 50-120s for its first
        # token and sometimes never got one, because the wait ate the request
        # timeout. Memory is the expendable half of that trade.
        await self._cancel_pending_learning()
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
        if images:
            images = await self._prepare_images(text, images)
        self.conversation.add_user(text, images=images)

        state = _TurnState(
            cancel_event=cancel_event,
            deadline=time.monotonic() + self.config.budgets.turn_timeout(),
            user_text=text,
        )
        # Snapshot the tree before the model touches it, so "what changed this
        # turn" is later answerable from git rather than from tool self-reports.
        if self.git_baseline is not None:
            await self.git_baseline.capture()
        if self.memory is not None:
            state.recall = MemoryRecall.from_contents(self.memory.recallable())
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

    def _image_request_limit(self) -> int:
        """Images this endpoint accepts in one request; 0 when it never said.

        Two sources, whichever is stricter: what the user configured, and what
        the endpoint told us when it refused a request for carrying too many.
        """

        limits = [
            value
            for value in (
                int(getattr(self.config, "max_images_per_request", 0) or 0),
                int(getattr(self.provider.capabilities, "max_images_per_request", 0) or 0),
            )
            if value > 0
        ]
        return min(limits) if limits else 0

    async def _prepare_images(
        self, text: str, images: list[ImageContent]
    ) -> list[ImageContent] | None:
        """Fit a batch of pasted images to what one request can actually carry.

        Two separate reasons an image cannot simply be attached: the main model
        may not read pixels at all (``vision_model`` covers that), and the
        endpoint may accept only so many per prompt - one, on the servers that
        prompted this. Six pasted screenshots used to travel as six payloads in
        a single request and were refused outright, which cost the user every
        one of them.

        So the ones that do not fit are transcribed instead of dropped: they
        travel as text, described in requests small enough to be accepted, and
        only the newest few ride along as actual pixels.
        """

        limit = self._image_request_limit()
        vision_model = self._vision_model()
        if vision_model:
            # A non-multimodal main model must never receive pixels: describe
            # everything and attach nothing, as before.
            describe, attach = list(images), []
            model = vision_model
        elif limit and len(images) > limit:
            describe, attach = images[:-limit], images[-limit:]
            model = self.config.model
        else:
            return list(images)

        analysis = await self._describe_images(text, describe, model=model, limit=limit)
        if analysis is None:
            # Transcription failed. Attach what the endpoint will take rather
            # than the whole batch it is certain to refuse; a limit of 0 means
            # nothing has told us otherwise, so send them all as before.
            return list(images[-limit:] if limit else images)
        self.conversation.add_user(analysis)
        return attach or None

    async def _describe_images(
        self,
        text: str,
        images: list[ImageContent],
        *,
        model: str,
        limit: int = 0,
    ) -> str | None:
        """Turn attached images into text, in requests the endpoint will accept.

        The main model may not be multimodal, so ``vision_model`` acts as its
        eyes: a one-off call outside the conversation (no tools, no history)
        produces a task-focused description that travels as plain text instead
        of pixels. When the endpoint caps images per prompt, the batch is split
        across that many calls, so the cap costs extra round-trips rather than
        the pictures themselves. Returns None when every batch fails, so the
        caller can degrade to attaching raw images as it did before.
        """
        if not images:
            return None
        batches = _chunked(images, limit) if limit else [list(images)]
        await self.set_state(AgentState.CALLING_MODEL, phase="analyzing_images")
        await self.event_bus.emit(
            "vision.analysis.started",
            {"model": model, "images": len(images), "requests": len(batches)},
            source="core.orchestrator",
        )
        described = 0
        sections: list[str] = []
        for index, batch in enumerate(batches):
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
                        images=list(batch),
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
                continue
            description = (response.text or "").strip()
            if not description:
                await self.event_bus.emit(
                    "vision.analysis.failed",
                    {"model": model, "error": "empty response"},
                    source="core.orchestrator",
                )
                continue
            described += len(batch)
            # Numbered only when split, so the single-image case reads exactly
            # as it did before.
            header = f"Image {index + 1}:\n" if len(batches) > 1 else ""
            sections.append(header + description)
        if not sections:
            return None
        await self.event_bus.emit(
            "vision.analysis.completed",
            {"model": model, "images": described, "requests": len(batches)},
            source="core.orchestrator",
        )
        return (
            f"[Image analysis by {model}] The next user message references "
            "attached images; this is what they contain, transcribed by a "
            "vision model:\n\n" + "\n\n".join(sections)
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
            # Steering: anything the user typed while the previous step ran joins
            # the conversation here, so it reaches the model on the very next
            # request instead of waiting for the turn to end. The step that was
            # already in flight finishes first - a call cannot be edited halfway
            # through - which is why this sits at the top of the loop rather than
            # inside the step.
            await self._deliver_queued_user_messages()

            allowed = self._allowed_tool_names()
            tool_definitions = self.tool_registry.definitions(allowed)
            compression = await self.compressor.ensure_capacity(self.conversation, tool_definitions)
            await self.emit_context_usage(compression)
            # Every request, not just the first: images accumulate in the
            # history, so a turn that attached nothing can still carry the
            # previous turn's payload past the cap.
            await self._enforce_image_limit()

            request = self._build_request(step, tool_definitions, state)
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
                if await self._retry_interrupted_tool_call(response, state):
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
        #
        # The verification checkpoint goes first and consumes the same budget:
        # when the model has actually changed the workspace, "you left this
        # unverified" is strictly better guidance than the generic "use the
        # tools", and the two must never cost two round-trips.
        if self.planner and self.planner.enabled:
            debt = await self.planner.note_final_answer_verification_debt()
            if debt:
                state.no_tool_nudged = True
                if response.text:
                    self.conversation.add_assistant(
                        bound_text(response.text, self.config.budgets.max_tool_output_chars),
                        [],
                    )
                self.conversation.add_user(build_runtime_note(debt))
                await self.set_state(
                    AgentState.CALLING_MODEL, phase="correcting_unverified_change"
                )
                return None

        if self._requires_tool_for_progress() and not state.no_tool_nudged:
            state.no_tool_nudged = True
            if response.text:
                self.conversation.add_assistant(
                    bound_text(response.text, self.config.budgets.max_tool_output_chars), []
                )
            correction = await self.planner.note_no_tool_response(
                recommended_tool_names=self._recommended_tool_names()
            )
            self.conversation.add_user(build_runtime_note(correction))
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
        # Not supplementary: re-issuing the call is the next step, not a detour
        # from the user's request.
        self.conversation.add_user(
            build_runtime_note(self._tool_format_correction_text(), supplementary=False)
        )
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

    async def _retry_interrupted_tool_call(
        self, response: ModelResponse, state: _TurnState
    ) -> bool:
        """Re-issue a step that was cut off while a tool call was streaming in.

        A step can end without the call it was in the middle of emitting: the
        model stream times out, the provider fails part-way, or it discards
        arguments it could not parse. What survives is whatever prose already
        reached the user - and because a model normally says what it is about to
        do *before* doing it, that prose is usually the announcement of the very
        change that then never happened ("I'll implement X now.").

        Nothing downstream can tell that apart from a model that simply chose to
        answer in prose, so the turn used to end there: the announcement was
        surfaced as the final answer and the agent settled into ``waiting_user``
        having written nothing. Re-issuing the step is the only honest reading -
        the model was mid-call, so let it finish the call.

        The announcement is kept as the assistant message it was, the model is
        told the call never landed, and the loop runs another step. Bounded by
        ``_MAX_INTERRUPTED_CALL_RETRIES`` so a stream that keeps breaking still
        terminates the turn with a best-effort reply instead of spinning.

        A response that reports ``STOP`` is left alone: it claims a clean prose
        ending, and taking the model at its word costs nothing here. Every way a
        call is really lost contradicts that claim - a salvaged or unfinished
        stream carries ``UNKNOWN``, a provider that discards arguments it could
        not parse still reports the ``TOOL_CALLS`` it no longer has, and a
        truncated one reports ``LENGTH``.
        """
        if response.tool_calls or not state.tool_call_streaming:
            return False
        if response.finish_reason == FinishReason.STOP:
            return False
        if state.interrupted_call_retries >= _MAX_INTERRUPTED_CALL_RETRIES:
            return False
        state.interrupted_call_retries += 1
        state.tool_call_streaming = False
        if response.text:
            self.conversation.add_assistant(
                bound_text(response.text, self.config.budgets.max_tool_output_chars), []
            )
        self.conversation.add_user(
            build_runtime_note(
                self._interrupted_call_correction_text(), supplementary=False
            )
        )
        await self.event_bus.emit(
            "tool.call.interrupted",
            {
                "attempt": state.interrupted_call_retries,
                "max_attempts": _MAX_INTERRUPTED_CALL_RETRIES,
            },
            source="core.orchestrator",
        )
        await self.set_state(
            AgentState.CALLING_MODEL, phase="retrying_interrupted_tool_call"
        )
        return True

    @staticmethod
    def _interrupted_call_correction_text() -> str:
        return (
            "Your previous tool call was cut off before it arrived, "
            "so it never ran and nothing in the workspace changed - whatever you "
            "just announced has not happened yet. Make the call again now. If it "
            "carried a large file, write it in smaller pieces so the call "
            "completes."
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
            build_runtime_note(
                self._budget_overflow_text(response.reasoning, request.max_output_tokens),
                supplementary=False,
            )
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

    async def _cancel_pending_learning(self) -> None:
        """Stop a background reflection so the turn ahead has the model to itself.

        Reflection is best-effort by design: what it would have distilled is
        still in the conversation, and the next quiet moment can distill it
        again. A turn blocked behind it is not recoverable in the same way, so
        the reflection is what gives way.
        """
        task = self._learning_task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        self._learning_task = None
        await self.event_bus.emit(
            "learning.cancelled",
            {"reason": "a new turn needs the model"},
            source="core.orchestrator",
        )

    def _turn_evidence_summary(self) -> str:
        """The planner's recent evidence ledger, serialized for the digest."""

        if not (self.planner and self.planner.enabled):
            return ""
        try:
            return json.dumps(self.planner.ledger.compact_recent(limit=12), default=str)
        except Exception:  # noqa: BLE001 - the digest is best-effort input
            return ""

    def _known_lesson(self, signature: str) -> FailureMemory | None:
        """A lesson already on file for ``signature``, or ``None``."""

        if self.failure_memory is None:
            return None
        try:
            return self.failure_memory.lesson_for(signature)
        except Exception:  # pragma: no cover - recall must never break a turn
            return None

    def _lesson_warning(self, names: tuple[str, ...], state: _TurnState) -> str | None:
        """Warn about a tool this session has already been burned by.

        Fired on the *first* use of that tool in the turn, before the same
        mistake has a chance to land again. Once per tool per turn: the point is
        to arrive ahead of the failure, not to narrate every call.
        """

        for name in names:
            if name in state.lessons_surfaced:
                continue
            lesson = self._known_lesson(f"tool_error:{name}")
            if lesson is None:
                continue
            state.lessons_surfaced.add(name)
            return (
                f"You have hit an error with '{name}' before ({lesson.count}x) and "
                f"recorded what to do about it:\n- {lesson.lesson}\n"
                "Check that this call already accounts for it."
            )
        return None

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
        await self._observe_tool_round(response, state)
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
            # Real progress buys back the clock, so the turn budget bounds time
            # spent *going nowhere* rather than time spent working. As a wall
            # clock it punished the wrong thing: on a local model a single step
            # costs minutes, so a turn doing everything right was cut off after
            # a handful of them with "I reached a runtime safety budget". The
            # guard against an agent that spins is stall detection right here,
            # which is semantic; max_model_steps and max_tool_calls remain the
            # absolute ceilings.
            state.deadline = time.monotonic() + self.config.budgets.turn_timeout()
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
            self.conversation.add_user(build_runtime_note(self._stall_nudge_text()))
            await self.event_bus.emit(
                "agent.stall.nudged",
                {"stall_rounds": state.stall_rounds},
                source="core.orchestrator",
            )
        return None

    async def _observe_tool_round(
        self, response: ModelResponse, state: _TurnState
    ) -> None:
        """Record what this round did, refresh the git view, and maybe say something.

        Kept off the convergence path on purpose: a reminder is advice, so it must
        never decide whether the turn continues. Anything that fails here is
        swallowed - the turn is doing real work and a nudge is not worth breaking it.
        """

        names = tuple(call.name for call in response.tool_calls)
        # Consumed here and reset, so it always describes the round just run.
        errored, state.round_errored = state.round_errored, False
        if not names:
            return
        capabilities = {name: self._capabilities_of(name) for name in names}
        round_ = ToolRound(
            names=names,
            read_only=all(
                caps and caps <= frozenset({ToolCapability.LOCAL_READ})
                for caps in capabilities.values()
            ),
            mutating=any(
                ToolCapability.LOCAL_WRITE in caps for caps in capabilities.values()
            ),
            ran_process=any(
                ToolCapability.PROCESS in caps for caps in capabilities.values()
            ),
            errored=errored,
        )
        state.reminders.observe(round_)

        if round_.mutating and self.git_baseline is not None:
            # Only after a write: `git diff` on every round would tax a large
            # repository for an answer that cannot have changed.
            try:
                state.git_changed_paths = await self.git_baseline.changed_paths()
            except Exception:  # pragma: no cover - defensive, git already fails open
                state.git_changed_paths = ()

        # Strict priority, one note per round so they never stack. A lesson from
        # a failure this session has already paid for outranks everything: it is
        # the difference between repeating a bug and not. A memory bearing on the
        # work comes next, and generic advice about how the turn is going last.
        note = (
            self._lesson_warning(names, state)
            or self._recalled_memory(response, state)
            or state.reminders.due(frozenset(self.tool_registry.names()))
        )
        if note:
            self.conversation.add_user(build_runtime_note(note))
            await self.event_bus.emit(
                "agent.reminder", {"rounds": state.reminders.activity.rounds},
                source="core.orchestrator",
            )

    @staticmethod
    def _recalled_memory(response: ModelResponse, state: _TurnState) -> str | None:
        """Any stored memory the calls in this round are about.

        The focus text is what the round is *touching* - the paths, queries, and
        commands in the arguments - because that is what says which part of the
        work is happening now, far more precisely than the prose around it.
        """

        if state.recall is None:
            return None
        focus = [state.user_text]
        for call in response.tool_calls:
            focus.append(call.name)
            arguments = call.arguments
            if isinstance(arguments, dict):
                focus.extend(str(value) for value in arguments.values())
        return state.recall.consider(" ".join(focus))

    def _capabilities_of(self, name: str) -> frozenset[ToolCapability]:
        try:
            return frozenset(self.tool_registry.capabilities(name))
        except Exception:
            return frozenset()

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
            "Recent tool calls have not advanced the task, and repeating "
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
            except ImageLimitError as exc:
                # The endpoint named its cap while refusing this request. Fit
                # the conversation to it and send again: the pictures over the
                # cap are worth losing, the turn is not.
                if streamed:
                    return ModelResponse(
                        text="".join(streamed), finish_reason=FinishReason.UNKNOWN
                    )
                if await self._enforce_image_limit(exc.limit) or (
                    await self._drop_unreadable_images()
                ):
                    attempts = 0
                    continue
                await self._emit_request_failed(exc)
                raise
            except ProviderError as exc:
                if streamed:
                    return ModelResponse(
                        text="".join(streamed), finish_reason=FinishReason.UNKNOWN
                    )
                if await self._drop_unreadable_images():
                    # The payload the endpoint refused is gone; the same turn is
                    # worth one more try as a text-only request.
                    attempts = 0
                    continue
                await self._emit_request_failed(exc)
                raise

    async def _enforce_image_limit(self, limit: int | None = None) -> bool:
        """Keep the conversation within the endpoint's images-per-request cap.

        Images stay in the history, so a second pasted screenshot puts two in
        the same prompt even though each turn only ever attached one - which is
        how a session that worked once starts failing on every later paste. The
        newest attachments are the ones the current turn is about, so those are
        the ones kept; the rest leave a note in their place, visible to the
        model, so it knows a picture was there rather than inventing one.

        Returns True when anything was trimmed.
        """

        cap = self._image_request_limit() if limit is None else max(0, limit)
        if cap <= 0:
            return False
        budget = cap
        trimmed = 0
        # Newest first: the current turn's attachments are the ones worth pixels.
        for message in reversed(self.conversation.messages):
            if not message.images:
                continue
            if budget >= len(message.images):
                budget -= len(message.images)
                continue
            keep = message.images[len(message.images) - budget :] if budget else []
            dropped = len(message.images) - len(keep)
            message.images = keep
            budget = 0
            trimmed += dropped
            message.content = (
                f"{message.content}\n\n[{dropped} earlier image(s) not sent: this "
                f"endpoint accepts at most {cap} image(s) per request. Ask the user "
                "to re-send one if you need to see it again.]"
            ).strip()
        if trimmed:
            self.conversation.reset_remote_state()
            await self.event_bus.emit(
                "images.trimmed",
                {"dropped": trimmed, "limit": cap},
                source="core.orchestrator",
            )
        return bool(trimmed)

    async def _drop_unreadable_images(self) -> bool:
        """Remove image payloads the endpoint just refused. True if any were there.

        Images are attached to the conversation before the request that carries
        them, so a server that rejects them leaves them sitting in the history:
        every later turn re-sends the same payload, is refused again, and pays
        the full retry schedule for it. One pasted screenshot is enough to make
        the rest of the session crawl - which reads as the agent getting slower
        rather than as an image that was never going to work.

        Dropping them costs the picture and keeps the session. The replacement
        text is deliberately visible to the model: it explains why it is being
        asked about something it cannot see, instead of leaving it to guess.
        """

        dropped = False
        for message in self.conversation.messages:
            if message.role != "user" or not message.images:
                continue
            count = len(message.images)
            message.images = []
            message.content = (
                f"{message.content}\n\n[{count} image(s) removed: this endpoint "
                "refused the image payload, so they were dropped to keep the "
                "conversation working. Answer from the text, and say plainly "
                "that you could not see them if it matters.]"
            ).strip()
            dropped = True
        if dropped:
            self.conversation.reset_remote_state()
            await self.event_bus.emit(
                "images.dropped",
                {"reason": "endpoint refused the image payload"},
                source="core.orchestrator",
            )
        return dropped

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

        # Per-index decoding/throttling state, so a streaming tool call reports
        # progress periodically instead of on every tiny chunk (which would
        # flood the UI) and its source is decoded once as it arrives.
        tool_streams: dict[int, _ToolCallStream] = {}
        # Reset per attempt: this stream is a fresh one, so whatever a previous
        # attempt was half-way through says nothing about what this one loses.
        state.tool_call_streaming = False

        # aclosing() so cancelling really disconnects. Raising out of the
        # loop leaves the generator suspended at its yield, and the HTTP
        # response under it open until the garbage collector gets to it -
        # meanwhile the server, which only stops when the client goes away,
        # keeps generating tokens nobody will ever read.
        async with contextlib.aclosing(self.provider.stream(request)) as provider_stream:
            async for event in provider_stream:
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
                    if event.tool_call_name:
                        # A call has begun arriving. Until the assembled response
                        # carries it, losing this stream means losing the call - not
                        # learning that the model chose to answer in prose. A
                        # nameless fragment proves nothing yet, and the providers
                        # drop those themselves, so it must not arm this.
                        state.tool_call_streaming = True
                    await self._emit_tool_progress(event, tool_streams)
                    continue
                await self._emit_provider_event(event)
                if event.kind == "reasoning_delta":
                    reasoning_parts.append(event.reasoning_delta)
                elif event.kind == "completed" and event.response:
                    completed = event.response

        await self._flush_tool_progress(tool_streams)

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
        if completed.tool_calls:
            # The call arrived intact, so nothing was lost with the stream. A
            # completed response *without* calls after one streamed is left
            # armed on purpose: the provider discards a call whose arguments it
            # cannot parse, and that loss looks exactly like a prose answer.
            state.tool_call_streaming = False
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
            if outcome.result.is_error:
                state.round_errored = True
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
                host_initiated=True,
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
            # A real person refusing a mutating/process action overrides the
            # surface classifier: the planner stops demanding workspace changes
            # for the rest of the turn. The DenyAllGateway is not a person -
            # its denial only means no approver is attached (headless run), so
            # it must not silently downgrade every gated task to prose.
            user_refused = not policy_denied and not isinstance(
                self.approval_gateway, DenyAllGateway
            )
            if (
                user_refused
                and self.planner
                and self.planner.enabled
                and self._capabilities_for(call.name) & _MUTATION_DENIAL_CAPABILITIES
            ):
                await self.planner.note_user_denial(call.name, authorization.reason)
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
            signature = f"tool_error:{call.name}"
            # What was already known about this exact failure, captured *before*
            # recording, so a lesson distilled from this very error is not read
            # back as if it had been learned earlier.
            prior = self._known_lesson(signature)
            await self._record_failure(
                trigger="tool_error",
                signature=signature,
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
            if prior is not None:
                # Carried on the error itself rather than as a separate note: the
                # model is looking at this result already, and a lesson it has to
                # go looking for in the prompt is a lesson it repeats.
                content = (
                    f"{content}\n\nThis is not the first time. You recorded this "
                    f"after it happened before (seen {prior.count}x):\n"
                    f"- {prior.lesson}\nApply it now instead of retrying the same way."
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
        self, event: ProviderEvent, streams: dict[int, _ToolCallStream]
    ) -> None:
        """Announce that a tool call's arguments are still streaming in.

        Emits a throttled ``tool.call.progress`` event so the UI can show live
        feedback while a large call accumulates, instead of appearing frozen
        until the whole call has arrived. For a tool that writes to the
        workspace the event also carries the *decoded* source produced since the
        last update, which is what lets the UI render the file as it is typed
        rather than only once it lands. The name may still be empty on the very
        first fragments; we wait until it is known so the feedback is meaningful.
        """

        name = event.tool_call_name
        if not name:
            return
        stream = streams.get(event.tool_call_index)
        if stream is None:
            if not self.tool_registry.has(name):
                # A name that is not a tool yet: either still arriving in pieces
                # ("write" before "write_file") or one the model invented.
                # Announcing it would fix the call's identity - and with it
                # whether the code window ever opens - on a fragment, so hold
                # off. Nothing is lost by waiting: the arguments are cumulative,
                # so a stream opened later still sees everything before it.
                return
            stream = self._tool_stream(name, event.tool_call_index, streams)
        stream.decoder.feed(event.tool_call_arguments)
        await self._publish_tool_progress(name, event.tool_call_index, stream, final=False)

    async def _flush_tool_progress(self, streams: dict[int, _ToolCallStream]) -> None:
        """Release the tail of every streamed call once the response ends.

        Throttling means the last fragments of a call are usually still
        unreported when the stream closes, which would leave the live view
        showing a file that stops a few lines short of what was actually
        written. This final, unthrottled update closes that gap and marks the
        source complete.
        """

        for index, stream in sorted(streams.items()):
            await self._publish_tool_progress(stream.name, index, stream, final=True)

    def _tool_stream(
        self, name: str, index: int, streams: dict[int, _ToolCallStream]
    ) -> _ToolCallStream:
        stream = streams.get(index)
        if stream is not None:
            return stream
        stream = _ToolCallStream(
            name=name,
            decoder=PartialObjectDecoder(
                (_PATH_ARGUMENT_KEY, _REASON_ARGUMENT_KEY, *_CODE_ARGUMENT_KEYS)
            ),
            writes=self._writes_to_workspace(name),
        )
        streams[index] = stream
        return stream

    def _writes_to_workspace(self, name: str) -> bool:
        """Whether ``name`` commits its arguments to disk, so they are source."""
        try:
            return ToolCapability.LOCAL_WRITE in self.tool_registry.capabilities(name)
        except CodeAIError:
            # A hallucinated tool name: no preview, and the call fails later on
            # its own terms. Progress feedback must never be the thing that
            # breaks the turn.
            return False

    async def _publish_tool_progress(
        self, name: str, index: int, stream: _ToolCallStream, *, final: bool
    ) -> None:
        decoder = stream.decoder
        code_key = stream.code_key or (
            decoder.first_started(_CODE_ARGUMENT_KEYS) if stream.writes else ""
        )
        stream.code_key = code_key
        code = decoder.value(code_key) if code_key else None
        pending = (len(code) - stream.announced_code) if code is not None else 0
        length = decoder.consumed

        if stream.announced:
            if final:
                # Throttling usually leaves a tail unreported, and the closing
                # quote itself adds no characters - so a call can be fully
                # streamed yet still look unfinished. Release either.
                #
                # Raw growth counts too, and for a call that writes nothing it is
                # the only thing that does. Backends differ in how they stream a
                # call: LM Studio sends the name in one chunk and the whole
                # arguments object in the next, so a small call never crosses the
                # throttle and its only ever published update is the opening one,
                # reporting zero characters. Every read tool then sat at
                # "building call (0 chars)" for the life of the call.
                grew_raw = length > stream.announced_chars
                if not pending and not grew_raw and (code is None or stream.announced_complete):
                    return
            else:
                grew_raw = length - stream.announced_chars >= _TOOL_PROGRESS_STEP_CHARS
                grew_code = pending >= _CODE_PROGRESS_STEP_CHARS
                if not grew_raw and not grew_code:
                    return

        payload: dict[str, object] = {
            "name": name,
            "index": index,
            "chars": length,
        }
        if not stream.announced:
            # The first word about this call. A consumer showing a window for
            # the write opens it here, before any source exists, rather than
            # waiting for the first line of code to know a write is happening.
            payload["call_started"] = True
        if stream.writes:
            payload["writes"] = True
            reason = decoder.value(_REASON_ARGUMENT_KEY)
            if reason is not None and reason.text:
                payload["reason"] = reason.text
        path = decoder.value(_PATH_ARGUMENT_KEY)
        if path is not None and path.closed and path.text:
            payload["path"] = path.text
        if code is not None:
            text = code.text
            payload["code_key"] = code_key
            payload["code_offset"] = stream.announced_code
            payload["code_delta"] = text[stream.announced_code :]
            payload["code_complete"] = code.closed or final
            payload["lines"] = text.count("\n") + 1 if text else 0
            stream.announced_code = len(text)
            stream.announced_complete = bool(payload["code_complete"])
        stream.announced_chars = length
        stream.announced = True
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
    def _build_request(
        self, step: int, tool_definitions: list, state: _TurnState
    ) -> ModelRequest:
        messages = self.conversation.snapshot()
        runtime_context = "\n\n".join(
            block
            for block in (self._planner_context(), self._git_context(state))
            if block
        )
        if runtime_context:
            messages = [*messages, Message(role="user", content=runtime_context)]
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

    @staticmethod
    def _git_context(state: _TurnState) -> str:
        """State what the workspace actually looks like relative to the turn's start.

        This is measured, not reported: it includes files changed through the
        shell and excludes writes that restored what was already there. When it
        disagrees with what the model believes it did, this is the side that is
        right - which is exactly why it is worth showing back to the model.
        """

        if not state.git_changed_paths:
            return ""
        listed = "\n".join(f"- {path}" for path in state.git_changed_paths)
        return (
            "Files that actually differ from the workspace as it was when this "
            f"turn started, according to git:\n{listed}\n"
            "This is measured from the working tree, not from what the tools "
            "reported. If it does not match what you believe you changed, trust "
            "this list and look again before claiming the work is done."
        )

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


