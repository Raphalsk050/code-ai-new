from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from code_ai.app.session import ApplicationSession
from code_ai.context.compression import CompressionResult, ContextCompressor
from code_ai.core.orchestration import AgentOrchestrator, TurnResult
from code_ai.core.planning import PlannerMode
from code_ai.core.state import AgentState
from code_ai.events.bus import AsyncEventBus, EventSubscriber
from code_ai.events.models import EventEnvelope
from code_ai.providers.base import ModelProvider
from code_ai.tools.terminal.manager import PersistentTerminalManager


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
    ) -> None:
        self.session = session
        self.event_bus = event_bus
        self.orchestrator = orchestrator
        self.provider = provider
        self.compressor = compressor
        self.terminal_manager = terminal_manager
        self._current_cancel: asyncio.Event | None = None
        self._current_task: asyncio.Task[TurnResult] | None = None

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

    async def submit_user_message(self, text: str, *, context: str = "") -> TurnResult:
        if self._current_task and not self._current_task.done():
            raise RuntimeError("A turn is already running.")
        self._current_cancel = asyncio.Event()
        self._current_task = asyncio.create_task(
            self.orchestrator.run_turn(
                text, cancel_event=self._current_cancel, context=context
            )
        )
        try:
            return await self._current_task
        finally:
            self._current_task = None
            self._current_cancel = None

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

    async def reset_conversation(self) -> None:
        """Start a fresh conversation, keeping the system prompt and tools.

        Powers the embedding "new conversation" action: any running turn is
        cancelled and the transcript is dropped so the next message starts a
        clean thread, while the leading system instructions are preserved.
        """
        if self._current_task and not self._current_task.done():
            await self.cancel_current_turn()
            try:
                await self._current_task
            except Exception:  # a cancelled turn may surface as an error; ignore
                pass
        messages = self.orchestrator.conversation.messages
        preserved = messages[:1] if messages and messages[0].role == "system" else []
        messages[:] = preserved
        self.orchestrator.conversation.reset_remote_state()
        await self.orchestrator.set_state(AgentState.READY, phase="waiting_user")
        await self.event_bus.emit("conversation.reset", {}, source="app")

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
    _LIVE_SETTINGS = {"model", "language", "learn", "permission_mode", "terminal_theme"}
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
            "max_context_tokens": config.budgets.max_context_tokens,
            "workspace": str(config.workspace),
            "supported": {
                "api_mode": sorted(SUPPORTED_API_MODES),
                "permission_mode": sorted(SUPPORTED_PERMISSION_MODES),
                "reasoning_effort": ["none", "minimal", "low", "medium", "high", "xhigh"],
            },
        }

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

    async def approve_or_start_plan_execution(self) -> None:
        await self.set_planner_mode(PlannerMode.ACT)

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

    async def submit_question_answer(self, answer: str) -> None:
        await self.event_bus.emit(
            "interaction.question.answered",
            {"answer": answer},
            source="app",
        )

    def subscribe(self, handler_or_sink: EventSubscriber) -> EventSubscriber:
        return self.event_bus.subscribe(handler_or_sink)

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        self.event_bus.unsubscribe(subscriber)

    async def close(self) -> None:
        if self._current_task and not self._current_task.done():
            await self.cancel_current_turn()
            await self._current_task
        if self.terminal_manager:
            self.terminal_manager.close_all()
        await self.provider.close()
        self.session.state = AgentState.CLOSED
        await self.event_bus.emit("session.closed", {}, source="app")


ApplicationEventHandler = Callable[[EventEnvelope], Awaitable[None] | None]


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
