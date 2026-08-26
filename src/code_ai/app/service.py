from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from code_ai.app.conversation_store import ConversationStore
from code_ai.app.goal_runner import GoalRunner
from code_ai.app.session import ApplicationSession
from code_ai.context.compression import CompressionResult, ContextCompressor
from code_ai.core.errors import GoalStateError
from code_ai.core.goal import (
    AcceptanceCriterion,
    CriterionKind,
    GoalEvaluator,
    GoalService,
    GoalStatus,
)
from code_ai.core.orchestration import AgentOrchestrator, TurnResult
from code_ai.core.planning import PlannerMode
from code_ai.core.state import AgentState
from code_ai.core.workflows import WorkflowService
from code_ai.events.bus import AsyncEventBus, EventSubscriber
from code_ai.events.models import EventEnvelope
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import ImageContent
from code_ai.sandbox.session import SessionSandbox
from code_ai.tools.skills.common import SkillSource
from code_ai.tools.terminal.manager import PersistentTerminalManager
from code_ai.util.redaction import sanitized_environment

logger = logging.getLogger(__name__)

# Continuation submitted when the user approves a plan and switches to act mode.
# It is added to the conversation (and echoed in the transcript) so the model
# starts executing the already-authored checklist instead of waiting for the
# user to type something.
_EXECUTE_PLAN_INSTRUCTION = (
    "Plano aprovado. Execute agora o plano aprovado, passo a passo, usando as "
    "ferramentas para fazer as mudanças. Não replaneje a menos que a abordagem "
    "realmente precise mudar."
)


class CodeAIApplication:
    """Public facade for CLI, TUI, and embedding clients."""

    def __init__(
        self,
        *,
        session: ApplicationSession,
        event_bus: AsyncEventBus,
        orchestrator: AgentOrchestrator,
        provider: ModelProvider,
        compressor: ContextCompressor,
        terminal_manager: PersistentTerminalManager | None = None,
        conversation_store: ConversationStore | None = None,
        workflows: WorkflowService | None = None,
        skill_sources: Sequence[SkillSource] = (),
        sandbox: SessionSandbox | None = None,
    ) -> None:
        self.session = session
        self.event_bus = event_bus
        self.orchestrator = orchestrator
        self.provider = provider
        self.compressor = compressor
        self.terminal_manager = terminal_manager
        self.conversation_store = conversation_store
        # This session's isolated scratch root, owned here because the session
        # owns its lifetime: it is removed when the app closes unless the user
        # asked to keep it for inspection.
        self.sandbox = sandbox
        # Saved procedures discoverable this session. Exposed on the facade
        # because clients run them by name: the TUI turns each one into a slash
        # command, and the model reaches the same records through use_workflow.
        self.workflows = workflows
        # Skill directories searched this session, for clients that let the user
        # invoke a skill by name instead of waiting for the model to match it.
        self.skill_sources = tuple(skill_sources)
        # Id of the conversation currently loaded in the live session. Assigned
        # by the client via reset_conversation/load_conversation; a turn persists
        # under it so the user can resume the thread later.
        self._conversation_id: str | None = None
        self._current_cancel: asyncio.Event | None = None
        self._current_task: asyncio.Task[TurnResult] | None = None
        # Persistent-goal machinery (/goal). The service holds the one goal per
        # session; the runner/task pair exists only while the loop is running.
        self._goal_service: GoalService | None = None
        self._goal_runner: GoalRunner | None = None
        self._goal_task: asyncio.Task[Any] | None = None
        # Background drain of interactive terminal sessions (see
        # _poll_terminal_screens); exists only while the app is running.
        self._terminal_poll_task: asyncio.Task[None] | None = None

    @property
    def conversation_id(self) -> str | None:
        return self._conversation_id

    async def start(self) -> None:
        self.session.state = AgentState.READY
        await self.orchestrator.set_state(AgentState.READY, phase="waiting_user")
        await self.event_bus.emit(
            "session.started",
            {
                "model": self.session.config.model,
                "api_mode": self.session.config.api_mode,
                "workspace": str(self.session.config.workspace),
                "permission_mode": self.session.config.permission_mode,
            },
            source="app",
        )
        await self.event_bus.emit("session.ready", {}, source="app")
        if self.terminal_manager is not None and self._terminal_poll_task is None:
            self._terminal_poll_task = asyncio.create_task(self._poll_terminal_screens())

    async def _poll_terminal_screens(self) -> None:
        """Push live PTY output to the UI even when no tool call is running.

        Interactive sessions keep producing output on their own (dev servers,
        long builds), but the terminal tools only emit a screen snapshot when
        the model acts. This loop drains every session periodically and emits
        ``terminal.screen.updated`` whenever the rendered display changed, so
        the user watches the terminal live instead of only at tool boundaries.
        """
        while True:
            await asyncio.sleep(0.5)
            try:
                changed = self.terminal_manager.poll()
            except Exception:  # noqa: BLE001 - a dying session must not kill the poller
                continue
            for screen in changed:
                await self.event_bus.emit(
                    "terminal.screen.updated", screen, source="terminal.poller"
                )

    async def submit_user_message(
        self,
        text: str,
        *,
        context: str = "",
        resume_plan: bool = False,
        images: list[ImageContent] | None = None,
    ) -> TurnResult:
        """Run the message as a turn, or hand it to the turn already running.

        Typing while the agent works steers it: the message is queued and the
        running turn reads it at its next model step, rather than waiting for
        the whole turn to finish. The step already in flight completes first,
        since a request in progress cannot be edited.
        """
        if self._current_task and not self._current_task.done():
            message = text.strip()
            if not message:
                return TurnResult(text="", response=None, queued=True)
            self.orchestrator.queue_user_message(message)
            await self.event_bus.emit("user.message.queued", {"text": message}, source="app")
            return TurnResult(text="", response=None, queued=True)

        result = await self._run_turn(
            text, context=context, resume_plan=resume_plan, images=images
        )
        # A message queued so late that the loop never read it would otherwise be
        # lost with the turn that ended: give it a turn of its own, with the
        # whole conversation still behind it. Cancelling drops the queue instead
        # - Ctrl+C means stop, not "stop and then run this".
        if result.cancelled:
            self.orchestrator.take_queued_messages()
            return result
        while self.orchestrator.has_queued_messages():
            follow_up = "\n\n".join(self.orchestrator.take_queued_messages())
            result = await self._run_turn(follow_up, context="", resume_plan=False, images=None)
            if result.cancelled:
                self.orchestrator.take_queued_messages()
                break
        return result

    async def _run_turn(
        self,
        text: str,
        *,
        context: str,
        resume_plan: bool,
        images: list[ImageContent] | None,
    ) -> TurnResult:
        planner = self.orchestrator.planner
        if not resume_plan and planner is not None and planner.has_pending_question():
            # The previous turn ended on a blocking ask_user question and paused
            # its plan. This message is the user's reply: continue that plan
            # instead of reclassifying the reply as a brand-new task, which
            # wiped the plan and evidence ledger and made the question a dead
            # end in the terminal UI.
            resume_plan = True
        self._current_cancel = asyncio.Event()
        self._current_task = asyncio.create_task(
            self.orchestrator.run_turn(
                text,
                cancel_event=self._current_cancel,
                context=context,
                resume_plan=resume_plan,
                images=images,
            )
        )
        try:
            return await self._current_task
        finally:
            self._current_task = None
            self._current_cancel = None
            # Persist the thread so it can be resumed later, even after a bridge
            # restart. Best-effort: never let a storage hiccup surface as a turn
            # failure.
            self._persist_conversation()

    async def explain_code(self, *, code: str, path: str = "", language: str = "") -> str:
        """Return a Markdown explanation of a code snippet (one-off model call).

        Used by the extension's Explain mode to populate an editor hover. Runs
        outside the conversation so it never pollutes the agent's history.
        """
        from code_ai.providers.models import Message, ModelRequest

        if not code.strip():
            return ""
        language_directive = (
            f"Write the explanation in {self.session.config.language}."
            if self.session.config.language
            else ""
        )
        system = Message(role="system", content=_EXPLAIN_SYSTEM + " " + language_directive)
        location = f" from `{path}`" if path else ""
        user = Message(
            role="user",
            content=(
                f"Explain the following {language} snippet{location}.\n\n"
                f"```{language}\n{code}\n```"
            ),
        )
        request = ModelRequest(
            model=self.session.config.model,
            messages=[system, user],
            # Generous so reasoning models (which spend output budget on hidden
            # thinking first) still have room to emit the answer — a tight cap
            # makes them return empty text.
            max_output_tokens=8192,
        )
        response = await self.provider.complete(request)
        return response.text or ""

    async def inline_complete(
        self, *, prefix: str, suffix: str = "", path: str = "", language: str = ""
    ) -> str:
        """Return a short code completion to insert at the cursor (ghost text).

        Backs the extension's inline-hints provider. A one-off model call outside
        the conversation, using ``inline_model`` when set (so a small/fast model
        can drive hints) and falling back to the main ``model`` otherwise.
        """
        from code_ai.providers.models import Message, ModelRequest

        if not prefix.strip():
            return ""
        # Send a generous window around the cursor so the model has real context
        # (imports, nearby defs, the comment describing intent) instead of
        # guessing — the leading edge of the prefix and trailing edge of the
        # suffix are the parts furthest from the cursor, so they get trimmed.
        prefix = prefix[-_INLINE_PREFIX_CHARS:]
        suffix = suffix[:_INLINE_SUFFIX_CHARS]
        config = self.session.config
        location = f"File: {path}\n" if path else ""
        system = Message(role="system", content=_INLINE_SYSTEM)
        user = Message(
            role="user",
            content=(
                f"{location}"
                f"Language: {language or 'plain text'}\n\n"
                "Here is the file. The cursor is marked <CURSOR>. Output only the "
                "text to insert there so the code reads naturally.\n\n"
                "```\n"
                f"{prefix}<CURSOR>{suffix}\n"
                "```"
            ),
        )
        request = ModelRequest(
            model=config.inline_model.strip() or config.model,
            messages=[system, user],
            # Reasoning models spend output budget on hidden thinking first, so a
            # tight cap makes them return empty text. Give them ample room to
            # finish reasoning and still emit the completion.
            max_output_tokens=32768,
        )
        response = await self.provider.complete(request)
        return _clean_inline_completion(response.text or "")

    async def analyze_refactor(
        self, *, code: str, path: str = "", language: str = ""
    ) -> list[dict[str, Any]]:
        """Return a structured list of architectural improvements for a snippet.

        One-off model call that produces a JSON array; parsed defensively so a
        chatty model that wraps the array in prose or fences still works.
        """
        from code_ai.providers.models import Message, ModelRequest

        if not code.strip():
            return []
        location = f" from `{path}`" if path else ""
        system = Message(role="system", content=_REFACTOR_ANALYZE_SYSTEM)
        user = Message(
            role="user",
            content=(
                f"Analyze this {language} snippet{location} for architectural "
                f"improvements.\n\n```{language}\n{code}\n```"
            ),
        )
        request = ModelRequest(
            model=self.session.config.model,
            messages=[system, user],
            max_output_tokens=8192,  # leave room past the reasoning budget
        )
        response = await self.provider.complete(request)
        return _parse_improvements(response.text or "")

    async def plan_refactor(
        self,
        *,
        code: str,
        path: str = "",
        language: str = "",
        improvements: list[dict[str, Any]],
    ) -> str:
        """Return a detailed Markdown refactoring plan for the chosen improvements."""
        from code_ai.providers.models import Message, ModelRequest

        bullet_lines = "\n".join(
            f"- {imp.get('title', '')}: {imp.get('rationale', '')}" for imp in improvements
        )
        location = f" (`{path}`)" if path else ""
        system = Message(role="system", content=_REFACTOR_PLAN_SYSTEM)
        user = Message(
            role="user",
            content=(
                f"Produce a refactoring plan for this {language} snippet{location}.\n\n"
                f"Selected improvements:\n{bullet_lines or '- (all suggested improvements)'}\n\n"
                f"```{language}\n{code}\n```"
            ),
        )
        request = ModelRequest(
            model=self.session.config.model,
            messages=[system, user],
            max_output_tokens=16384,  # plan markdown + the reasoning budget
        )
        response = await self.provider.complete(request)
        return response.text or ""

    async def reset_conversation(self, conversation_id: str | None = None) -> None:
        """Start a fresh conversation, keeping the system prompt and tools.

        Powers the embedding "new conversation" action: any running turn is
        cancelled and the transcript is dropped so the next message starts a
        clean thread, while the leading system instructions are preserved. The
        optional ``conversation_id`` (from the client) tags the fresh thread so
        the turns it accrues persist under that id and can be resumed later.
        """
        await self._cancel_running_turn()
        messages = self.orchestrator.conversation.messages
        preserved = messages[:1] if messages and messages[0].role == "system" else []
        messages[:] = preserved
        self.orchestrator.conversation.reset_remote_state()
        self._conversation_id = conversation_id or uuid.uuid4().hex
        await self.orchestrator.set_state(AgentState.READY, phase="waiting_user")
        await self.event_bus.emit("conversation.reset", {}, source="app")

    async def list_conversations(self) -> list[dict[str, Any]]:
        """Metadata for every saved conversation in this workspace, newest-first."""
        if not self.conversation_store:
            return []
        return self.conversation_store.list()

    async def load_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Resume a saved conversation: reload its messages into the live session.

        The current system prompt is kept (so rules/skills stay current) and the
        saved history replaces the rest, giving the model full context to
        continue. Returns the stored messages so the UI can rebuild the
        transcript when its own cache is empty.
        """
        if not self.conversation_store:
            raise RuntimeError("Conversation persistence is not configured.")
        record = self.conversation_store.load(conversation_id)
        if record is None:
            raise ValueError(f"No saved conversation with id {conversation_id!r}.")
        await self._cancel_running_turn()
        loaded = self.conversation_store.load_messages(conversation_id)
        messages = self.orchestrator.conversation.messages
        preserved = messages[:1] if messages and messages[0].role == "system" else []
        messages[:] = preserved + loaded
        # Drop any remote-state pointer: it belongs to the old process/turn, so
        # the next request replays the full local history instead.
        self.orchestrator.conversation.reset_remote_state()
        self._conversation_id = conversation_id
        await self.orchestrator.set_state(AgentState.READY, phase="waiting_user")
        await self.event_bus.emit(
            "conversation.loaded",
            {"id": conversation_id, "message_count": len(loaded)},
            source="app",
        )
        return {
            "id": conversation_id,
            "title": record.get("title", ""),
            "messages": record.get("messages", []),
        }

    async def delete_conversation(self, conversation_id: str) -> bool:
        if not self.conversation_store:
            return False
        deleted = self.conversation_store.delete(conversation_id)
        if conversation_id == self._conversation_id:
            self._conversation_id = None
        return deleted

    async def _cancel_running_turn(self) -> None:
        if self._current_task and not self._current_task.done():
            await self.cancel_current_turn()
            try:
                await self._current_task
            except Exception:  # a cancelled turn may surface as an error; ignore
                pass

    def _persist_conversation(self) -> None:
        """Save the live (non-system) history under the current conversation id."""
        if not self.conversation_store or not self._conversation_id:
            return
        non_system = [
            m for m in self.orchestrator.conversation.messages if m.role != "system"
        ]
        if not non_system:
            return
        try:
            self.conversation_store.save(
                conversation_id=self._conversation_id,
                messages=non_system,
                previous_response_id=self.orchestrator.conversation.previous_response_id,
            )
        except Exception:  # pragma: no cover - persistence must never break a turn
            logger.warning(
                "Failed to persist conversation %s", self._conversation_id, exc_info=True
            )

    async def cancel_current_turn(self) -> None:
        if self._current_cancel is not None:
            await self.event_bus.emit(
                "status.changed", {"state": AgentState.CANCELLING.value}, source="app"
            )
            self._current_cancel.set()

    async def request_context_compression(self) -> CompressionResult:
        await self.orchestrator.set_state(
            AgentState.COMPRESSING_CONTEXT, phase="manual_compression"
        )
        # force=True: a manual /compact always runs right away, regardless of
        # whether the conversation is already under the auto-compress threshold.
        compression = await self.compressor.ensure_capacity(
            self.orchestrator.conversation,
            self.orchestrator.tool_registry.definitions(),
            force=True,
        )
        # Refresh the context-meter bar immediately; otherwise it would only
        # catch up to the post-compaction token count on the next turn.
        await self.orchestrator.emit_context_usage(compression)
        await self.orchestrator.set_state(AgentState.READY, phase="waiting_user")
        return compression

    async def set_planner_mode(self, mode: str | PlannerMode) -> None:
        if not self.orchestrator.planner:
            raise RuntimeError("Planner is not configured.")
        await self.orchestrator.planner.set_mode(PlannerMode(mode))

    async def set_permission_mode(self, mode: str) -> None:
        from code_ai.config.models import SUPPORTED_PERMISSION_MODES

        normalized = mode.strip().lower()
        if normalized not in SUPPORTED_PERMISSION_MODES:
            raise ValueError(
                f"Unsupported permission mode: {mode}. "
                f"Choose one of {sorted(SUPPORTED_PERMISSION_MODES)}."
            )
        # The orchestrator reads config.permission_mode live, so updating it in
        # place takes effect on the next tool call.
        self.session.config.permission_mode = normalized
        await self.event_bus.emit(
            "permission.mode.changed", {"mode": normalized}, source="app"
        )

    # -- settings (for the VSCode settings panel) --------------------------

    # Top-level config fields that take effect on the next call once written.
    _LIVE_SETTINGS = {
        "model",
        "language",
        "learn",
        "permission_mode",
        "terminal_theme",
        "inline_hints_enabled",
        "inline_model",
        "vision_model",
    }
    # Top-level fields the providers read once at bootstrap; need a restart.
    _RESTART_SETTINGS = {"api_mode", "base_url", "api_key", "workspace"}

    def get_settings(self) -> dict[str, Any]:
        """Snapshot of the user-editable settings for the extension panel.

        The API key is never returned; the panel only learns whether one is set.
        """
        from code_ai.config.models import (
            SUPPORTED_API_MODES,
            SUPPORTED_PERMISSION_MODES,
        )

        config = self.session.config
        return {
            "model": config.model,
            "api_mode": config.api_mode,
            "base_url": config.base_url,
            "api_key_set": bool(config.api_key),
            "language": config.language,
            "permission_mode": config.permission_mode,
            "reasoning_effort": config.sampling.reasoning_effort or "none",
            "learn": config.learn,
            "inline_hints_enabled": config.inline_hints_enabled,
            "inline_model": config.inline_model,
            "vision_model": config.vision_model,
            "max_context_tokens": config.budgets.max_context_tokens,
            "workspace": str(config.workspace),
            "supported": {
                "api_mode": sorted(SUPPORTED_API_MODES),
                "permission_mode": sorted(SUPPORTED_PERMISSION_MODES),
                "reasoning_effort": ["none", "minimal", "low", "medium", "high", "xhigh"],
            },
        }

    async def list_models(self) -> list[str]:
        """List the model identifiers the configured provider currently serves.

        Backs the settings panel's "list models" affordance so the user can pick
        a valid model instead of typing one. Queries the provider's catalog with
        the currently-loaded config (base_url/api_mode/api_key).
        """
        from code_ai.providers.model_listing import list_available_models

        return await list_available_models(self.session.config)

    async def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Persist and apply a batch of settings from the panel.

        Live fields take effect immediately; restart-only fields are written but
        reported back so the UI can flag a restart. Each field is validated by
        re-running the whole-config validator, so a bad value is rejected in
        isolation without aborting the rest of the batch.
        """
        from code_ai.config.loader import persist_config_updates

        config = self.session.config
        applied: list[str] = []
        restart: list[str] = []
        errors: dict[str, str] = {}

        for key, value in updates.items():
            try:
                if key in self._LIVE_SETTINGS or key in self._RESTART_SETTINGS:
                    if key == "workspace":
                        value = str(Path(str(value)).expanduser().resolve())
                    if key == "api_key" and not str(value).strip():
                        continue  # blank means "leave the stored key untouched"
                    validated = persist_config_updates(config, {key: value})
                    if key in self._LIVE_SETTINGS:
                        setattr(config, key, getattr(validated, key))
                        applied.append(key)
                    else:
                        if key == "api_key":
                            config.api_key = str(value)
                        restart.append(key)
                elif key == "reasoning_effort":
                    sampling = asdict(config.sampling)
                    sampling["reasoning_effort"] = None if value == "none" else value
                    validated = persist_config_updates(config, {"sampling": sampling})
                    config.sampling = validated.sampling
                    applied.append(key)
                elif key == "max_context_tokens":
                    budgets = asdict(config.budgets)
                    budgets["max_context_tokens"] = int(value)
                    persist_config_updates(config, {"budgets": budgets})
                    restart.append(key)
                else:
                    errors[key] = "unknown setting"
            except Exception as exc:
                errors[key] = str(exc) or type(exc).__name__

        if "permission_mode" in applied:
            await self.event_bus.emit(
                "permission.mode.changed", {"mode": config.permission_mode}, source="app"
            )
        return {
            "applied": applied,
            "restart_required": restart,
            "errors": errors,
            "settings": self.get_settings(),
        }

    def has_active_plan(self) -> bool:
        """True when the model authored a plan that is ready to execute."""
        planner = self.orchestrator.planner
        return bool(planner and planner.enabled and planner.agent_plan)

    async def start_plan_execution(self) -> bool:
        """Switch to act mode and immediately execute the approved plan.

        Returns True when an execution turn was started. When no plan has been
        authored yet there is nothing to run, so it only arms act mode and
        returns False, letting the caller fall back to plain mode switching.
        """
        await self.set_planner_mode(PlannerMode.ACT)
        if not self.has_active_plan():
            return False
        await self.submit_user_message(_EXECUTE_PLAN_INSTRUCTION, resume_plan=True)
        return True

    async def request_replan(self, reason: str | None = None) -> str:
        if not self.orchestrator.planner:
            raise RuntimeError("Planner is not configured.")
        await self.event_bus.emit(
            "planning.replan.started",
            {"reason": reason or "manual request"},
            source="app",
        )
        await self.event_bus.emit(
            "planning.replan.completed",
            self.orchestrator.planner.plan_snapshot(),
            source="app",
        )
        return "command> Replan requested. The next turn will classify the current objective again."

    def get_plan_snapshot(self) -> dict[str, object]:
        if not self.orchestrator.planner:
            return {"planner": "not configured"}
        return self.orchestrator.planner.plan_snapshot()

    # -- persistent goal (/goal) -------------------------------------------

    def _goal(self) -> GoalService:
        if self._goal_service is None:
            self._goal_service = GoalService(
                config=self.session.config.goal, event_bus=self.event_bus
            )
        return self._goal_service

    def goal_loop_running(self) -> bool:
        return bool(self._goal_task and not self._goal_task.done())

    def goal_snapshot(self) -> dict[str, Any]:
        if self._goal_service is None:
            return {"status": "none"}
        snapshot = self._goal_service.snapshot()
        snapshot["loop_running"] = self.goal_loop_running()
        return snapshot

    async def define_goal(self, objective: str) -> dict[str, Any]:
        """Define a persistent goal and derive its acceptance criteria.

        The criteria come from a one-off model call (outside the conversation)
        and are only *proposed* here; the loop starts on :meth:`start_goal`
        unless ``goal.confirm_criteria`` is disabled, in which case it starts
        immediately.
        """
        if self.goal_loop_running():
            raise GoalStateError(
                "A goal loop is already running. Stop it with /goal stop first."
            )
        service = self._goal()
        await service.define(objective)
        criteria = await self._derive_goal_criteria(objective)
        await service.propose_criteria(criteria)
        started = False
        if not self.session.config.goal.confirm_criteria:
            await self.start_goal()
            started = True
        snapshot = self.goal_snapshot()
        snapshot["started"] = started
        return snapshot

    async def start_goal(self) -> dict[str, Any]:
        """Activate the defined goal (or resume a blocked one) and run the loop."""
        service = self._goal()
        if self.goal_loop_running():
            raise GoalStateError("The goal loop is already running.")
        goal = service.goal
        if goal is None:
            raise GoalStateError("No goal is defined. Use /goal <objetivo> first.")
        if goal.status == GoalStatus.DRAFT:
            await service.activate()
        elif goal.status == GoalStatus.BLOCKED:
            await service.resume()
        elif goal.status != GoalStatus.ACTIVE:
            raise GoalStateError(
                f"The goal cannot start from status {goal.status.value}."
            )
        self._launch_goal_loop()
        return self.goal_snapshot()

    async def stop_goal(self) -> dict[str, Any]:
        """Stop the goal loop (and the in-flight iteration's turn)."""
        service = self._goal()
        if service.goal is None:
            raise GoalStateError("No goal is defined.")
        if self._goal_runner is not None:
            self._goal_runner.request_stop()
        await self.cancel_current_turn()
        if not self.goal_loop_running() and not service.goal.is_terminal:
            # No running loop will observe the stop flag (draft/blocked goal),
            # so settle the state machine directly.
            await service.stop("stopped by user")
        return self.goal_snapshot()

    def _launch_goal_loop(self) -> None:
        runner = GoalRunner(
            service=self._goal(),
            evaluator=self._build_goal_evaluator(),
            config=self.session.config.goal,
            run_iteration=self._run_goal_iteration,
            progress_marker=self._goal_progress_marker,
            evidence_summary=self._goal_evidence_summary,
        )
        self._goal_runner = runner
        task = asyncio.create_task(runner.run())
        self._goal_task = task
        task.add_done_callback(self._on_goal_loop_done)

    def _on_goal_loop_done(self, task: asyncio.Task[Any]) -> None:
        self._goal_runner = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Goal loop crashed", exc_info=exc)
            # Surface the crash in the transcript; fire-and-forget because this
            # callback is synchronous.
            asyncio.get_running_loop().create_task(
                self.event_bus.emit(
                    "error",
                    {"message": f"Goal loop crashed: {exc}", "type": type(exc).__name__},
                    source="app.goal",
                )
            )

    async def _run_goal_iteration(self, prompt: str, context: str) -> TurnResult:
        return await self.submit_user_message(prompt, context=context)

    def _build_goal_evaluator(self) -> GoalEvaluator:
        return GoalEvaluator(
            command_port=self._run_goal_command,
            judge_port=self._goal_judge,
            workspace=self.session.config.workspace,
            judge_enabled=self.session.config.goal.judge_enabled,
        )

    async def _run_goal_command(self, command: str) -> tuple[int | None, str]:
        """Run one acceptance-criterion command from the workspace root.

        Deliberately shell-based (criteria are user/model-authored one-liners
        like ``pytest -q``) and bounded by the build timeout so a hanging check
        cannot freeze the goal loop.
        """
        timeout = float(self.session.config.budgets.build_tool_timeout_s)
        env = sanitized_environment(os.environ)
        if self.sandbox is not None:
            # A criterion is normally the project's own test or build command,
            # so it runs from the workspace - but its caches and temp files
            # belong in the sandbox like any other run.
            env.update(self.sandbox.environment(os.environ))
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self.session.config.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return None, f"criterion command timed out after {timeout:.0f}s"
        output = (stdout or b"").decode("utf-8", errors="replace")
        # Keep the tail: with test/build output the verdict lives at the end.
        return process.returncode, output[-4000:]

    async def _goal_judge(self, system: str, user: str) -> str:
        from code_ai.providers.models import Message, ModelRequest

        request = ModelRequest(
            model=self.session.config.model,
            messages=[
                Message(role="system", content=system),
                Message(role="user", content=user),
            ],
            # Reasoning models spend output budget on hidden thinking first; a
            # tight cap would make them return empty text.
            max_output_tokens=8192,
        )
        response = await self.provider.complete(request)
        return response.text or ""

    def _goal_progress_marker(self) -> str:
        """Workspace half of the stagnation signature for the last iteration.

        Content hashes (not just paths) so re-editing a file to genuinely new
        content counts as progress while rewriting identical bytes does not.
        """
        planner = self.orchestrator.planner
        if planner is None:
            return ""
        ledger = planner.ledger
        return repr(
            (
                tuple(sorted(ledger.changed_hashes.items())),
                ledger.latest_verification_passed,
            )
        )

    def _goal_evidence_summary(self) -> str:
        planner = self.orchestrator.planner
        if planner is None:
            return ""
        return json.dumps(planner.ledger.compact_recent(limit=12), default=str)

    async def _derive_goal_criteria(self, objective: str) -> list[AcceptanceCriterion]:
        """Derive measurable acceptance criteria for the objective.

        One-off model call outside the conversation; parsed defensively. Any
        failure degrades to a single JUDGE criterion so /goal always works,
        just with a subjective gate instead of deterministic ones.
        """
        from code_ai.providers.models import Message, ModelRequest

        request = ModelRequest(
            model=self.session.config.model,
            messages=[
                Message(role="system", content=_GOAL_CRITERIA_SYSTEM),
                Message(
                    role="user",
                    content=(
                        "Objective the agent must fulfill inside the workspace "
                        f"{self.session.config.workspace}:\n{objective}"
                    ),
                ),
            ],
            max_output_tokens=8192,
        )
        try:
            response = await self.provider.complete(request)
            criteria = _parse_goal_criteria(response.text or "")
        except Exception:
            logger.warning("Goal criteria derivation failed", exc_info=True)
            criteria = []
        if criteria:
            return criteria
        return [
            AcceptanceCriterion(
                kind=CriterionKind.JUDGE,
                description=(
                    "O objetivo foi cumprido de forma completa e verificável: "
                    f"{objective}"
                ),
            )
        ]

    async def submit_question_answer(self, answer: str) -> TurnResult | None:
        """Deliver the user's reply to a blocking ask_user question.

        Emits the answered event for observing clients (e.g. so a question UI
        can close) and then runs the reply as a normal turn, which resumes the
        paused plan via the pending-question check in submit_user_message.
        Previously the event was emitted into the void - no consumer existed -
        so an answer sent through this API never reached the model.
        """
        await self.event_bus.emit(
            "interaction.question.answered",
            {"answer": answer},
            source="app",
        )
        if not answer.strip():
            return None
        return await self.submit_user_message(answer)

    def subscribe(self, handler_or_sink: EventSubscriber) -> EventSubscriber:
        return self.event_bus.subscribe(handler_or_sink)

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        self.event_bus.unsubscribe(subscriber)

    async def close(self) -> None:
        if self._goal_runner is not None:
            self._goal_runner.request_stop()
        if self._goal_task and not self._goal_task.done():
            self._goal_task.cancel()
            try:
                await self._goal_task
            except BaseException:  # noqa: BLE001 - shutdown must not propagate
                pass
        if self._current_task and not self._current_task.done():
            await self.cancel_current_turn()
            await self._current_task
        # Let a pending post-turn learning pass finish (bounded) while the
        # provider is still alive; after provider.close() it could not run.
        await self.orchestrator.drain_learning()
        if self._terminal_poll_task is not None:
            self._terminal_poll_task.cancel()
            try:
                await self._terminal_poll_task
            except BaseException:  # noqa: BLE001 - shutdown must not propagate
                pass
            self._terminal_poll_task = None
        if self.terminal_manager:
            self.terminal_manager.close_all()
        self._cleanup_sandbox()
        await self.provider.close()
        self.session.state = AgentState.CLOSED
        await self.event_bus.emit("session.closed", {}, source="app")

    def _cleanup_sandbox(self) -> None:
        """Discard this session's scratch root, unless it was asked to survive.

        Failure here is deliberately silent: a sandbox that outlives its session
        is reaped at the next startup by TTL, and shutdown is not the moment to
        raise over a temp directory.
        """

        if self.sandbox is None or not self.session.config.sandbox.cleanup_on_exit:
            return
        try:
            self.sandbox.cleanup()
        except OSError:
            pass


ApplicationEventHandler = Callable[[EventEnvelope], Awaitable[None] | None]


_GOAL_CRITERIA_SYSTEM = (
    "You define measurable acceptance criteria for a persistent goal an "
    "autonomous coding agent must fulfill inside the user's workspace. Return "
    "ONLY a JSON array of 2 to 5 objects, no prose and no code fences. Each "
    'object has: "kind" (one of "COMMAND", "FILE", "JUDGE"), "description" '
    "(one sentence, in the same language as the objective), and depending on "
    'kind: "command" (a non-interactive shell command that exits 0 exactly '
    "when the criterion is met, runnable from the workspace root) for "
    'COMMAND, or "path" (workspace-relative file path) plus optional '
    '"pattern" (plain-text substring the file must contain) for FILE. Prefer '
    "deterministic COMMAND/FILE criteria; use JUDGE only for genuinely "
    "subjective qualities. Never invent commands for tools the project may "
    "not have; when unsure of the project's tooling, prefer FILE or JUDGE."
)

_MAX_GOAL_CRITERIA = 6


def _parse_goal_criteria(text: str) -> list[AcceptanceCriterion]:
    """Extract validated criteria from a (possibly chatty) model reply."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    criteria: list[AcceptanceCriterion] = []
    for item in data[:_MAX_GOAL_CRITERIA]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().upper()
        if kind not in {member.value for member in CriterionKind}:
            continue
        try:
            criteria.append(
                AcceptanceCriterion(
                    kind=CriterionKind(kind),
                    description=str(item.get("description") or ""),
                    command=str(item.get("command") or ""),
                    path=str(item.get("path") or ""),
                    pattern=str(item.get("pattern") or ""),
                )
            )
        except Exception:
            continue  # one malformed criterion must not sink the batch
    return criteria


_REFACTOR_ANALYZE_SYSTEM = (
    "You are a staff engineer reviewing a code snippet for architectural "
    "improvements (separation of concerns, coupling, abstractions, error "
    "handling, testability, naming, performance). Return ONLY a JSON array of at "
    "most 5 objects, no prose and no code fences. Each object has: \"id\" (short "
    "kebab-case slug), \"title\" (a few words), \"rationale\" (1-2 sentences on "
    "why it matters), and \"impact\" (one of \"low\", \"medium\", \"high\"). If "
    "the snippet is already clean, return an empty array []."
)

_REFACTOR_PLAN_SYSTEM = (
    "You are a staff engineer writing a refactoring plan as GitHub-flavored "
    "Markdown for review before any code changes. Structure it with these "
    "sections: a top '# Refactoring plan' title; '## Why' (the motivation); "
    "'## Proposed changes' (concrete, ordered changes); '## Affected areas' "
    "(files/modules/functions touched); '## Architecture' (before/after, using a "
    "fenced diagram or bullet list); '## Risks & mitigations'; and '## Steps' (a "
    "checklist). Be thorough and specific to the snippet. Output only the Markdown."
)

_EXPLAIN_SYSTEM = (
    "You are a senior engineer explaining a code snippet inside an editor hover "
    "card. Be precise and concise. Respond in GitHub-flavored Markdown with this "
    "shape: a one-sentence summary in bold; a short '**What it does**' section "
    "(2-4 bullets) covering control flow, inputs/outputs and side effects; and a "
    "'**Suggestions**' section with 1-3 bullets on correctness, readability or "
    "performance (omit it if there is nothing useful to add). Keep it compact — "
    "no preamble, no repetition of the code, no headings beyond the bold labels."
)


_INLINE_SYSTEM = (
    "You are a precise code-completion engine embedded in an editor, like GitHub "
    "Copilot. You receive a source file with the caret marked as <CURSOR>. Predict "
    "the exact text the developer would type next at the caret, continuing the code "
    "and honoring any comment right above it that states the intent.\n"
    "Rules:\n"
    "- Output ONLY the raw text to insert at <CURSOR>. No prose, no explanations, "
    "no markdown fences, no <think> tags.\n"
    "- Continue naturally from the code before the caret and fit the code after it; "
    "never repeat text that already appears before or after the caret.\n"
    "- Match the file's language, style, indentation and naming.\n"
    "- If a comment or docstring just before the caret describes behavior, "
    "implement it.\n"
    "- Complete a coherent unit (finish the line, expression, or a small block) and "
    "stop at a natural boundary; do not rewrite the whole file.\n"
    "- If there is genuinely nothing useful to add, output nothing."
)

# Window of code sent around the cursor. Generous so the model sees imports,
# nearby definitions and the intent comment rather than guessing; the edges
# furthest from the cursor are trimmed first.
_INLINE_PREFIX_CHARS = 8000
_INLINE_SUFFIX_CHARS = 3000


def _clean_inline_completion(text: str) -> str:
    """Sanitize a raw model reply into insertable ghost text.

    Strips reasoning-model ``<think>`` blocks, markdown code fences and a leading
    "Here is..." style preamble, so only the code to insert remains.
    """
    import re

    cleaned = text
    # Drop any leaked reasoning block.
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip("\n")
    # Unwrap a single fenced block (```lang ... ```), keeping only its body.
    fenced = re.match(r"^```[a-zA-Z0-9_+-]*\n(.*?)\n?```\s*$", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    else:
        cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned


def _parse_improvements(text: str) -> list[dict[str, Any]]:
    """Extract a normalized improvements list from a (possibly chatty) reply."""
    import json
    import re

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        impact = str(item.get("impact") or "medium").strip().lower()
        out.append(
            {
                "id": str(item.get("id") or f"imp-{index + 1}").strip(),
                "title": title,
                "rationale": str(item.get("rationale") or "").strip(),
                "impact": impact if impact in {"low", "medium", "high"} else "medium",
            }
        )
    return out
