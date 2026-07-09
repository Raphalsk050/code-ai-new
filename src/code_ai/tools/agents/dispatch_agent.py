from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.core.subagents.coordinator import Dispatcher, SubagentRequest
from code_ai.core.subagents.profiles import SubagentProfileRegistry
from code_ai.providers.models import ToolDefinition
from code_ai.tools.base import ToolCapability, ToolContext

# Rough serialized size of one report minus its summary (task preview, ids,
# status, usage, JSON envelope). Used to split the parent's tool-output budget
# across reports so the aggregate payload fits under the global bound instead
# of being middle-truncated into invalid JSON.
_REPORT_ENVELOPE_CHARS = 500
_MIN_SUMMARY_CHARS = 1000


class DispatchAgentTool:
    """Delegates focused subtasks to isolated sub-agents, optionally in parallel.

    The orchestrating model calls this to fan work out: each entry in ``tasks``
    launches one sub-agent of the chosen type with its own prompt. Sub-agents run
    concurrently, in isolation, and each returns a self-contained report. The
    tool never raises for a failed sub-agent - the per-agent status/error is in
    the result so the model can decide how to proceed.
    """

    name = "dispatch_agent"
    capabilities = frozenset({ToolCapability.DELEGATE})

    def __init__(self, profile_registry: SubagentProfileRegistry) -> None:
        self._profiles = profile_registry
        self.description = (
            "Delegate one or more focused subtasks to specialized sub-agents that "
            "run concurrently and in isolation, each returning its own report. Use "
            "this to parallelize independent work - e.g. fan out several read-only "
            "explorations at once, or hand a self-contained change to a worker while "
            "you continue. Give each sub-agent a precise, standalone prompt: it "
            "cannot see this conversation and cannot ask you questions, so ground "
            "the prompt in evidence you have actually gathered (real paths, real "
            "findings) and state the expected outcome. Each report includes an "
            "evidence digest of what the sub-agent really did (files read/changed, "
            "commands run with exit codes); reconcile reports against it instead of "
            "taking summaries at face value. Available agent types:\n"
            + self._profiles.describe()
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": (
                        "The subtasks to delegate. Each runs as one sub-agent. "
                        "Provide independent tasks so they can run in parallel."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_type": {
                                "type": "string",
                                "enum": self._profiles.names(),
                                "description": "Which kind of sub-agent to launch.",
                            },
                            "prompt": {
                                "type": "string",
                                "description": (
                                    "The complete, standalone instruction for this "
                                    "sub-agent, including all context it needs: the "
                                    "concrete file paths and findings you have already "
                                    "gathered, not assumptions."
                                ),
                            },
                            "expected_outcome": {
                                "type": "string",
                                "description": (
                                    "One or two sentences stating what a successful "
                                    "result looks like (what must exist, pass, or be "
                                    "answered). Appended to the sub-agent's brief and "
                                    "your yardstick for judging its report."
                                ),
                            },
                        },
                        "required": ["agent_type", "prompt"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["tasks"],
            "additionalProperties": False,
        }

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        coordinator: Dispatcher | None = context.subagent_coordinator
        if coordinator is None:
            raise ToolExecutionError("Sub-agent delegation is not available in this session.")

        requests = _parse_requests(arguments)
        reports = await coordinator.dispatch(
            requests,
            cancel_event=context.cancel_event,
            depth=context.subagent_depth,
        )
        # Give each summary an equal slice of the tool-output budget so a large
        # fan-out degrades into shorter per-agent summaries, never a mangled blob.
        budget = context.config.budgets.max_tool_output_chars
        per_summary = max(
            _MIN_SUMMARY_CHARS,
            budget // max(1, len(reports)) - _REPORT_ENVELOPE_CHARS,
        )
        return {
            "dispatched": len(reports),
            "reports": [
                report.to_dict(max_summary_chars=per_summary) for report in reports
            ],
        }


def _parse_requests(arguments: dict[str, Any]) -> list[SubagentRequest]:
    raw = arguments.get("tasks")
    if not isinstance(raw, list) or not raw:
        raise ToolArgumentError("tasks must be a non-empty array of {agent_type, prompt}.")
    requests: list[SubagentRequest] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ToolArgumentError(f"tasks[{index}] must be an object.")
        agent_type = str(item.get("agent_type") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not agent_type:
            raise ToolArgumentError(f"tasks[{index}].agent_type is required.")
        if not prompt:
            raise ToolArgumentError(f"tasks[{index}].prompt is required.")
        expected = str(item.get("expected_outcome") or "").strip()
        if expected:
            # The sub-agent cannot see the conversation, so its success bar
            # travels inside the brief; the dispatcher judges the report by it.
            prompt = (
                f"{prompt}\n\nExpected outcome (your report will be judged "
                f"against this): {expected}"
            )
        requests.append(SubagentRequest(agent_type=agent_type, prompt=prompt))
    return requests
