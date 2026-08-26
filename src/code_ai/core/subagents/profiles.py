from __future__ import annotations

from dataclasses import dataclass

from code_ai.config.models import BudgetConfig
from code_ai.tools.base import ToolCapability
from code_ai.tools.review.prompts import REVIEW_CONFIDENCE_RUBRIC

# Capabilities a sub-agent may never hold, whichever profile it runs under.
# Interactive terminals and desktop control are backed by mutable singletons the
# main session owns, so handing them to an isolated sub-agent would share mutable
# state and let concurrent agents collide. Memory/interaction/plan-transition and
# completion tools belong to the top-level turn, not a delegated subtask. Removing
# these keeps sub-agents independent and their loop naturally terminating on a
# final text answer.
FORBIDDEN_SUBAGENT_CAPABILITIES = frozenset(
    {
        ToolCapability.INTERACTIVE_TERMINAL,
        ToolCapability.COMPUTER_CONTROL,
        ToolCapability.INTERACTION,
        ToolCapability.MEMORY,
        ToolCapability.INTERNAL_TRANSITION,
        ToolCapability.INTERNAL_COMPLETION,
    }
)


@dataclass(frozen=True, slots=True)
class SubagentProfile:
    """Strategy describing one kind of sub-agent.

    A profile is pure configuration: the tool capabilities it may use, its time
    and step budgets, and the role instructions injected as its system prompt.
    Adding a new kind of agent means adding a profile - the coordinator, runtime,
    and dispatch tool need no changes (open/closed).
    """

    name: str
    description: str
    allowed_capabilities: frozenset[ToolCapability]
    max_model_steps: int
    role_prompt: str
    # Which budget field bounds this profile's wall-clock time.
    timeout_field: str

    def __post_init__(self) -> None:
        leaked = self.allowed_capabilities & FORBIDDEN_SUBAGENT_CAPABILITIES
        if leaked:
            raise ValueError(
                f"Profile {self.name!r} requests forbidden sub-agent capabilities: "
                f"{sorted(cap.value for cap in leaked)}."
            )

    @property
    def writes(self) -> bool:
        return ToolCapability.LOCAL_WRITE in self.allowed_capabilities

    @property
    def runs_processes(self) -> bool:
        return ToolCapability.PROCESS in self.allowed_capabilities

    @property
    def read_only(self) -> bool:
        return not self.writes and not self.runs_processes

    def timeout_seconds(self, budgets: BudgetConfig) -> int:
        return int(getattr(budgets, self.timeout_field))


_EXPLORER = SubagentProfile(
    name="explorer",
    description=(
        "Read-only investigator. Searches and reads the workspace (and the web) to "
        "answer a focused question - locating code, tracing behavior, gathering "
        "facts. Cannot modify files or run processes. Use it to fan out research in "
        "parallel before you plan or change anything."
    ),
    allowed_capabilities=frozenset({ToolCapability.LOCAL_READ, ToolCapability.WEB}),
    max_model_steps=30,
    timeout_field="subagent_explorer_timeout_s",
    role_prompt=(
        "You are an exploration sub-agent. Your job is to investigate and report, "
        "not to act. Use the read and search tools to answer the specific question "
        "you were given. You cannot modify files or run commands.\n"
        "Finish by replying with a concise report: the direct answer first, then the "
        "concrete evidence (file paths with line numbers, key snippets, findings). "
        "Do not pad the report; the agent that dispatched you will act on it."
    ),
)

_CODER = SubagentProfile(
    name="coder",
    description=(
        "Implementation worker. Carries out ONE focused, self-contained change: "
        "reads what it needs, edits or creates files, and runs commands to verify. "
        "Give it a precise, bounded task with clear acceptance criteria. Not for "
        "open-ended planning or work that spans many unrelated areas."
    ),
    allowed_capabilities=frozenset(
        {
            ToolCapability.LOCAL_READ,
            ToolCapability.LOCAL_WRITE,
            ToolCapability.PROCESS,
            ToolCapability.WEB,
        }
    ),
    max_model_steps=60,
    timeout_field="subagent_worker_timeout_s",
    role_prompt=(
        "You are an implementation sub-agent focused on a single, well-scoped task. "
        "Make the change directly with the file and command tools - never write code "
        "into your reply as a substitute for editing the workspace. Read before you "
        "edit, follow the surrounding conventions, and work in small incremental "
        "steps: skeleton first, then one focused piece at a time with edit_code - "
        "never a whole file in one call. Verify your change by running the "
        "project's tests or build when one exists.\n"
        "Finish by replying with a concise report of exactly what you changed (paths), "
        "how you verified it, and anything you could not complete."
    ),
)

_REVIEWER = SubagentProfile(
    name="reviewer",
    description=(
        "Code reviewer. Reads code and diffs and runs the review/build/test tools to "
        "judge quality, correctness, and risk, then reports findings. Read-only with "
        "respect to source: it never edits files. Use it to get an independent "
        "assessment of a change or an area of the codebase."
    ),
    allowed_capabilities=frozenset(
        {
            ToolCapability.LOCAL_READ,
            ToolCapability.REVIEW,
            ToolCapability.PROCESS,
            ToolCapability.WEB,
        }
    ),
    max_model_steps=30,
    timeout_field="subagent_worker_timeout_s",
    role_prompt=(
        "You are a code-review sub-agent. Read the relevant code and run the review, "
        "build, and test tools to assess correctness, design, and risk. Do not edit "
        "any files - your job is to judge and report, not to fix.\n"
        + REVIEW_CONFIDENCE_RUBRIC
        + "Before you report a finding, argue the other side of it: look for the "
        "guard, conversion, or caller that would make it a non-issue, and drop it "
        "if you find one. The dispatcher acts on what you report, so a finding it "
        "cannot reproduce costs more than the one you left out.\n"
        "Finish by replying with a structured review: an overall verdict, then the "
        "surviving findings ordered by severity, each with a file:line reference, "
        "its confidence score, and why it matters. Call out what is solid as well "
        "as what is wrong."
    ),
)


class SubagentProfileRegistry:
    """Lookup for the available profiles (their Strategy instances)."""

    def __init__(self, profiles: list[SubagentProfile]) -> None:
        self._profiles = {profile.name: profile for profile in profiles}

    def get(self, name: str) -> SubagentProfile | None:
        return self._profiles.get(name.strip().lower())

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def all(self) -> list[SubagentProfile]:
        return [self._profiles[name] for name in self.names()]

    def describe(self) -> str:
        """One bullet per profile for the dispatch tool's schema/description."""
        return "\n".join(
            f"- {profile.name}: {profile.description}" for profile in self.all()
        )


def default_profile_registry() -> SubagentProfileRegistry:
    return SubagentProfileRegistry([_EXPLORER, _CODER, _REVIEWER])
