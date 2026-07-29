from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Sequence

from code_ai.config.models import AppConfig
from code_ai.context.compression import ContextCompressor
from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCounter
from code_ai.context.usage import UsageLedger
from code_ai.core.orchestration import AgentOrchestrator
from code_ai.core.subagents.profiles import SubagentProfile
from code_ai.events.bus import AsyncEventBus
from code_ai.prompts import build_subagent_system_prompt
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import Message
from code_ai.tools.base import ToolContext
from code_ai.tools.registry import ToolRegistry
from code_ai.util.paths import WorkspacePolicy

# Builds the review service a reviewer sub-agent needs, bound to that agent's own
# (isolated) event bus. Injected so this module stays decoupled from the concrete
# review implementation and so tests can pass a stub.
ReviewServiceFactory = Callable[[AsyncEventBus], object]


@dataclasses.dataclass(slots=True)
class BuiltSubagent:
    """An isolated orchestrator plus the handles needed to run and observe it."""

    orchestrator: AgentOrchestrator
    event_bus: AsyncEventBus
    usage: UsageLedger
    timeout_seconds: int


class SubagentRuntime:
    """Factory that assembles a fully isolated orchestrator for a profile.

    Each build produces its own conversation, usage ledger, event bus, and a tool
    registry restricted to the profile's capabilities. The only things shared with
    the parent are stateless collaborators (the provider, the read-only workspace
    policy, the immutable config) - never mutable session state - so sub-agents
    cannot interfere with each other or with the main turn.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        provider: ModelProvider,
        workspace: WorkspacePolicy,
        base_registry: ToolRegistry,
        rules_text: str = "",
        skills_text: str = "",
        skill_sources: Sequence[object] = (),
        workflows: object | None = None,
        review_service_factory: ReviewServiceFactory | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._workspace = workspace
        self._base_registry = base_registry
        self._rules_text = rules_text
        self._skills_text = skills_text
        # The same skill directories the parent searches, so a sub-agent that acts
        # on the injected catalog can actually load what it lists.
        self._skill_sources = tuple(skill_sources)
        # Read-only service, safe to share: a sub-agent asked to follow a named
        # procedure resolves it from the same directories as the parent.
        self._workflows = workflows
        self._review_service_factory = review_service_factory

    def build(self, profile: SubagentProfile) -> BuiltSubagent:
        event_bus = AsyncEventBus()
        child_config = self._child_config(profile)
        registry = self._base_registry.select(profile.allowed_capabilities)

        def system_prompt() -> str:
            return build_subagent_system_prompt(
                role_prompt=profile.role_prompt,
                workspace=child_config.workspace,
                language=child_config.language,
                rules=self._rules_text,
                skills=self._skills_text,
            )

        conversation = ConversationState(
            messages=[Message(role="system", content=system_prompt())]
        )
        usage = UsageLedger()
        compressor = ContextCompressor(
            counter=TokenCounter(model=child_config.model),
            max_context_tokens=child_config.budgets.max_context_tokens,
            threshold=child_config.context_compression_threshold,
            target=child_config.context_compression_target,
            output_reserve=child_config.output_token_reserve,
            event_bus=event_bus,
            provider=self._provider,
            model=child_config.model,
        )
        review_service = (
            self._review_service_factory(event_bus)
            if self._review_service_factory is not None
            else None
        )

        def tool_context(cancel_event: asyncio.Event | None) -> ToolContext:
            # No terminal_manager, desktop_controller, or subagent coordinator:
            # a sub-agent shares no mutable singletons and cannot delegate further.
            return ToolContext(
                config=child_config,
                workspace=self._workspace,
                event_bus=event_bus,
                cancel_event=cancel_event,
                review_service=review_service,
                skill_sources=self._skill_sources or None,
                workflows=self._workflows,
            )

        orchestrator = AgentOrchestrator(
            config=child_config,
            provider=self._provider,
            tool_registry=registry,
            conversation=conversation,
            usage=usage,
            event_bus=event_bus,
            compressor=compressor,
            tool_context_factory=tool_context,
            # No planner (sub-agents run a focused loop), no failure/durable
            # memory, no rules service (rules are baked into the system prompt).
            planner=None,
            system_prompt_builder=system_prompt,
        )
        return BuiltSubagent(
            orchestrator=orchestrator,
            event_bus=event_bus,
            usage=usage,
            timeout_seconds=profile.timeout_seconds(child_config.budgets),
        )

    def _child_config(self, profile: SubagentProfile) -> AppConfig:
        """Clone the config with sub-agent-scoped budgets and no approval gate.

        The sub-agent runs in ``bypass`` internally: approval already happened
        once, at the parent, when the model called the dispatch tool. Re-prompting
        per action would deadlock parallel sub-agents. Workspace boundaries still
        apply - those live in the tools, not the permission mode. The turn budget
        is set to the profile timeout so the inner loop winds down gracefully
        before the coordinator's hard wall-clock cutoff trips.
        """
        timeout = profile.timeout_seconds(self._config.budgets)
        budgets = dataclasses.replace(
            self._config.budgets,
            max_model_steps=profile.max_model_steps,
            max_turn_seconds=timeout,
            max_turn_wall_time_s=timeout,
        )
        return dataclasses.replace(
            self._config,
            permission_mode="bypass",
            budgets=budgets,
        )
