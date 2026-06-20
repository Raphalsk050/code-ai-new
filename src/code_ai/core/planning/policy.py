from __future__ import annotations

from dataclasses import dataclass

from code_ai.core.planning.models import (
    PlannerMode,
    PlanningPhase,
    PlanStep,
    PlanStepKind,
    TaskIntent,
    TaskProfile,
)
from code_ai.tools.base import ToolCapability
from code_ai.tools.registry import ToolRegistry

DEFAULT_CAPABILITIES_BY_NAME: dict[str, frozenset[ToolCapability]] = {
    "read_file": frozenset({ToolCapability.LOCAL_READ}),
    "list_files": frozenset({ToolCapability.LOCAL_READ}),
    "search_code": frozenset({ToolCapability.LOCAL_READ}),
    "system_information": frozenset({ToolCapability.LOCAL_READ}),
    "write_file": frozenset({ToolCapability.LOCAL_WRITE}),
    "edit_code": frozenset({ToolCapability.LOCAL_WRITE}),
    "execute_command": frozenset({ToolCapability.PROCESS}),
    "build_review": frozenset({ToolCapability.PROCESS, ToolCapability.REVIEW}),
    "architecture_review": frozenset({ToolCapability.REVIEW}),
    "code_review": frozenset({ToolCapability.REVIEW}),
    "control_terminal": frozenset({ToolCapability.INTERACTIVE_TERMINAL}),
    "read_screen": frozenset(
        {ToolCapability.INTERACTIVE_TERMINAL, ToolCapability.LOCAL_READ}
    ),
    "web_search": frozenset({ToolCapability.WEB}),
    "ask_user": frozenset({ToolCapability.INTERACTION}),
    "finish_discovery": frozenset({ToolCapability.INTERNAL_TRANSITION}),
    "complete_task": frozenset({ToolCapability.INTERNAL_COMPLETION}),
}


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str
    allowed_tool_names: set[str]


class PlannerToolPolicy:
    """Derives model-visible and execution-time tool permissions from planner state."""

    def allowed_tool_names(
        self,
        *,
        registry: ToolRegistry,
        profile: TaskProfile | None,
        mode: PlannerMode,
        phase: PlanningPhase,
        current_step: PlanStep | None,
        approved_external_gap: bool = False,
        strict: bool = True,
    ) -> set[str]:
        names = set(registry.names())
        if not strict or profile is None:
            return names

        if not profile.requires_workspace_mutation:
            return self._allowed_for_read_only(
                names=names,
                registry=registry,
                profile=profile,
                phase=phase,
                approved_external_gap=approved_external_gap,
            )

        if mode == PlannerMode.PLAN:
            return self._by_capabilities(
                names,
                registry,
                {
                    ToolCapability.LOCAL_READ,
                    ToolCapability.INTERACTION,
                    ToolCapability.INTERNAL_TRANSITION,
                },
            )

        if phase in {PlanningPhase.UNDERSTAND, PlanningPhase.DISCOVER_LOCAL}:
            allowed = self._by_capabilities(
                names,
                registry,
                {
                    ToolCapability.LOCAL_READ,
                    ToolCapability.INTERACTION,
                    ToolCapability.INTERNAL_TRANSITION,
                },
            )
            if approved_external_gap:
                allowed.update(self._by_capabilities(names, registry, {ToolCapability.WEB}))
            return allowed

        if phase == PlanningPhase.CREATE_PLAN:
            return set()
        if phase == PlanningPhase.WAITING_FOR_ACT:
            return set()
        if phase == PlanningPhase.EXECUTE:
            allowed = self._by_capabilities(
                names,
                registry,
                {
                    ToolCapability.LOCAL_READ,
                    ToolCapability.LOCAL_WRITE,
                    ToolCapability.INTERACTION,
                    ToolCapability.INTERNAL_COMPLETION,
                },
            )
            if current_step and current_step.kind == PlanStepKind.EXECUTE_COMMAND:
                allowed.update(self._by_capabilities(names, registry, {ToolCapability.PROCESS}))
            if current_step and current_step.kind == PlanStepKind.RESEARCH_WEB:
                allowed.update(self._by_capabilities(names, registry, {ToolCapability.WEB}))
            return allowed
        if phase == PlanningPhase.VERIFY:
            return self._by_capabilities(
                names,
                registry,
                {
                    ToolCapability.LOCAL_READ,
                    ToolCapability.PROCESS,
                    ToolCapability.REVIEW,
                    ToolCapability.INTERACTIVE_TERMINAL,
                    ToolCapability.INTERNAL_COMPLETION,
                },
            )
        if phase == PlanningPhase.REPAIR:
            return self._by_capabilities(
                names,
                registry,
                {
                    ToolCapability.LOCAL_READ,
                    ToolCapability.LOCAL_WRITE,
                    ToolCapability.PROCESS,
                    ToolCapability.INTERACTION,
                    ToolCapability.INTERNAL_COMPLETION,
                },
            )
        if phase in {PlanningPhase.COMPLETE, PlanningPhase.BLOCKED}:
            return self._by_capabilities(
                names,
                registry,
                {ToolCapability.INTERACTION, ToolCapability.INTERNAL_COMPLETION},
            )
        return set()

    def evaluate(
        self,
        *,
        tool_name: str,
        registry: ToolRegistry,
        profile: TaskProfile | None,
        mode: PlannerMode,
        phase: PlanningPhase,
        current_step: PlanStep | None,
        approved_external_gap: bool = False,
        strict: bool = True,
    ) -> PolicyDecision:
        allowed = self.allowed_tool_names(
            registry=registry,
            profile=profile,
            mode=mode,
            phase=phase,
            current_step=current_step,
            approved_external_gap=approved_external_gap,
            strict=strict,
        )
        if tool_name in allowed:
            return PolicyDecision(True, "allowed", allowed)
        if profile and profile.requires_local_context and tool_name == "web_search":
            return PolicyDecision(
                False,
                "web_search requires a validated external gap for local workspace tasks.",
                allowed,
            )
        if mode == PlannerMode.PLAN and self._has_capability(
            registry, tool_name, ToolCapability.LOCAL_WRITE
        ):
            return PolicyDecision(False, "PLAN mode does not allow workspace mutation.", allowed)
        if mode == PlannerMode.PLAN and self._has_capability(
            registry, tool_name, ToolCapability.PROCESS
        ):
            return PolicyDecision(False, "PLAN mode does not allow command execution.", allowed)
        return PolicyDecision(
            False,
            f"{tool_name} is not allowed during phase {phase.value}.",
            allowed,
        )

    def _allowed_for_read_only(
        self,
        *,
        names: set[str],
        registry: ToolRegistry,
        profile: TaskProfile,
        phase: PlanningPhase,
        approved_external_gap: bool,
    ) -> set[str]:
        if profile.requires_external_information and profile.allows_web_first:
            return self._by_capabilities(names, registry, {ToolCapability.WEB})
        if profile.requires_local_context:
            allowed = self._by_capabilities(
                names,
                registry,
                {
                    ToolCapability.LOCAL_READ,
                    ToolCapability.INTERACTION,
                    ToolCapability.INTERNAL_TRANSITION,
                },
            )
            if approved_external_gap:
                allowed.update(self._by_capabilities(names, registry, {ToolCapability.WEB}))
            return allowed
        if profile.intent == TaskIntent.CONVERSATION:
            return set()
        if phase == PlanningPhase.COMPLETE:
            return names
        return names

    def _by_capabilities(
        self,
        names: set[str],
        registry: ToolRegistry,
        capabilities: set[ToolCapability],
    ) -> set[str]:
        return {
            name
            for name in names
            if self._capabilities(registry, name).intersection(capabilities)
        }

    def _has_capability(
        self, registry: ToolRegistry, name: str, capability: ToolCapability
    ) -> bool:
        return capability in self._capabilities(registry, name)

    @staticmethod
    def _capabilities(registry: ToolRegistry, name: str) -> frozenset[ToolCapability]:
        try:
            explicit = registry.capabilities(name)
        except Exception:
            explicit = frozenset()
        return explicit or DEFAULT_CAPABILITIES_BY_NAME.get(name, frozenset())
