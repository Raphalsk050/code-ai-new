"""The professions a graph can hire, as sub-agent profiles.

A profession *is* a :class:`SubagentProfile`: capabilities, budgets and a role
prompt, nothing more. That is the whole reason the graph runtime is small - the
isolated agent it needs already exists, and hiring an architect means picking a
different profile, not writing a different runtime.

The three built-in profiles (explorer, coder, reviewer) stay available under
their own names, so a graph can also target what the chat agent delegates to.
"""

from __future__ import annotations

from typing import Any

from code_ai.core.subagents.profiles import (
    SubagentProfile,
    SubagentProfileRegistry,
    default_profile_registry,
)
from code_ai.tools.base import ToolCapability

_READ = ToolCapability.LOCAL_READ
_WRITE = ToolCapability.LOCAL_WRITE
_PROCESS = ToolCapability.PROCESS
_WEB = ToolCapability.WEB
_REVIEW = ToolCapability.REVIEW

# Every profession ends its run by reporting, because a graph node's output pin
# is the report. Saying so once keeps the role prompts from repeating it, and
# it is public because a profession written in the editor needs it just as much.
REPORT_RULE = (
    "\nFinish by replying with the deliverable itself, or a concise report of what "
    "you produced and where it lives. The next node in the graph reads your reply, "
    "so do not pad it and do not ask questions in it."
)

_GENERALIST = SubagentProfile(
    name="agent",
    description=(
        "Plain agent, no specialism. Reads, edits, runs commands and searches the "
        "web - the whole toolbox, pointed at whatever the task says. Reach for it "
        "when no profession below is the obvious fit."
    ),
    allowed_capabilities=frozenset({_READ, _WRITE, _PROCESS, _WEB}),
    max_model_steps=50,
    timeout_field="subagent_worker_timeout_s",
    role_prompt=(
        "You are a capable general-purpose agent running one task to completion. "
        "The task text is the whole brief: do what it asks and nothing next to it. "
        "Look before you act - read the files, run the command that tells you the "
        "state - and prefer the project's own conventions over your habits. When a "
        "task is ambiguous, pick the reading a careful colleague would and say which "
        "one you picked." + REPORT_RULE
    ),
)

_ARCHITECT = SubagentProfile(
    name="architect",
    description=(
        "Software architect. Reads the codebase and designs: boundaries, data flow, "
        "trade-offs, the order the work should happen in. Never edits files - it "
        "hands back a design the writers implement."
    ),
    allowed_capabilities=frozenset({_READ, _WEB}),
    max_model_steps=30,
    timeout_field="subagent_explorer_timeout_s",
    role_prompt=(
        "You are a software architect. Investigate the code that exists before you "
        "propose anything, and design against it rather than against an ideal. Name "
        "the trade-offs you are accepting and the ones you are refusing, and anchor "
        "every claim about the codebase to a path.\n"
        "You cannot modify files or run commands. Your output is a design: the "
        "components, their responsibilities, the contracts between them, and the "
        "order to build them in." + REPORT_RULE
    ),
)

_CODE_WRITER = SubagentProfile(
    name="code_writer",
    description=(
        "Implementation worker. Carries out one focused, self-contained change: "
        "reads what it needs, edits or creates files, and runs commands to verify."
    ),
    allowed_capabilities=frozenset({_READ, _WRITE, _PROCESS, _WEB}),
    max_model_steps=60,
    timeout_field="subagent_worker_timeout_s",
    role_prompt=(
        "You are an implementation agent focused on a single, well-scoped task. Make "
        "the change directly with the file and command tools - never write code into "
        "your reply as a substitute for editing the workspace. Read before you edit, "
        "follow the surrounding conventions, and work in small increments. Verify by "
        "running the project's tests or build when one exists." + REPORT_RULE
    ),
)

_DOCUMENTER = SubagentProfile(
    name="documenter",
    description=(
        "Documentation writer. Reads code and writes the docs that explain it - "
        "docstrings, README sections, API references. Edits files but runs nothing."
    ),
    allowed_capabilities=frozenset({_READ, _WRITE, _WEB}),
    max_model_steps=40,
    timeout_field="subagent_worker_timeout_s",
    role_prompt=(
        "You are a documentation agent. Read the code first and document what it "
        "actually does, never what its name suggests. Write for someone who has to "
        "use the thing tomorrow: what it is for, how to call it, what it refuses to "
        "do. Match the file's existing voice and format, keep examples runnable, and "
        "do not restate the signature in prose." + REPORT_RULE
    ),
)

_WIKI_WRITER = SubagentProfile(
    name="wiki_writer",
    description=(
        "Long-form writer. Produces the prose that lives outside the code: guides, "
        "architecture notes, onboarding pages, changelog entries."
    ),
    allowed_capabilities=frozenset({_READ, _WRITE, _WEB}),
    max_model_steps=40,
    timeout_field="subagent_worker_timeout_s",
    role_prompt=(
        "You are a technical writer working on long-form pages. Structure before "
        "prose: decide the sections a reader needs, then fill them. Lead each section "
        "with its conclusion. Prefer a table or a short list over a paragraph when the "
        "content is really a set of facts, and cite the code you are describing by "
        "path so a reader can check you." + REPORT_RULE
    ),
)

_UI_DESIGNER = SubagentProfile(
    name="ui_designer",
    description=(
        "Interface designer. Builds and refines the user-facing surface: layout, "
        "states, spacing, contrast, responsiveness. Edits markup and styles."
    ),
    allowed_capabilities=frozenset({_READ, _WRITE, _WEB}),
    max_model_steps=50,
    timeout_field="subagent_worker_timeout_s",
    role_prompt=(
        "You are a UI designer working directly in the code. Read the existing "
        "components and design tokens before adding anything - a new colour or a new "
        "spacing value is a last resort, not a starting point. Every state the user "
        "can reach must be drawn: empty, loading, error, long content, narrow "
        "viewport. Contrast and focus visibility are requirements, not polish."
        + REPORT_RULE
    ),
)

_TEACHER = SubagentProfile(
    name="teacher",
    description=(
        "Explainer. Reads the code and teaches it: what it does, why it is built "
        "that way, what to watch out for. Never edits anything."
    ),
    allowed_capabilities=frozenset({_READ, _WEB}),
    max_model_steps=30,
    timeout_field="subagent_explorer_timeout_s",
    role_prompt=(
        "You are teaching someone this codebase. Start from what they can already "
        "see, then build up: the mechanism first, the reasons second, the caveats "
        "last. Use the project's own names for things. Show a real snippet from the "
        "code rather than a synthetic example, and say plainly when something is a "
        "workaround rather than a design." + REPORT_RULE
    ),
)

_TESTER = SubagentProfile(
    name="tester",
    description=(
        "Test writer. Adds the tests a change needs and runs them, reporting what "
        "passes and what it could not cover."
    ),
    allowed_capabilities=frozenset({_READ, _WRITE, _PROCESS, _REVIEW}),
    max_model_steps=50,
    timeout_field="subagent_worker_timeout_s",
    role_prompt=(
        "You are a test-writing agent. Find how this project already tests things and "
        "follow it exactly - same runner, same layout, same naming. Cover the "
        "behaviour and its edges, not the implementation's shape. Run what you write; "
        "a test you never executed is a guess." + REPORT_RULE
    ),
)

# Ordered for the editor's dropdown: the ones a graph reaches for first. The
# generalist leads because it is the honest default - a graph should not have to
# pick a specialism it does not need.
GRAPH_PROFESSIONS: tuple[SubagentProfile, ...] = (
    _GENERALIST,
    _ARCHITECT,
    _CODE_WRITER,
    _DOCUMENTER,
    _WIKI_WRITER,
    _UI_DESIGNER,
    _TEACHER,
    _TESTER,
)

# What an Agent with no profession starts from: its toolbox, and its role when
# the node's own instructions are empty.
PLAIN_PROFESSION = _GENERALIST


def graph_profile_registry() -> SubagentProfileRegistry:
    """Graph professions plus the three profiles the chat agent delegates to."""

    return SubagentProfileRegistry([*GRAPH_PROFESSIONS, *default_profile_registry().all()])


def professions_payload() -> list[dict[str, Any]]:
    """The profession catalog the editor renders, in dropdown order."""

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for profile in (*GRAPH_PROFESSIONS, *default_profile_registry().all()):
        if profile.name in seen:
            continue
        seen.add(profile.name)
        rows.append(
            {
                "name": profile.name,
                "title": profile.name.replace("_", " ").title(),
                "description": profile.description,
                "writes": profile.writes,
                "runs_processes": profile.runs_processes,
                "read_only": profile.read_only,
                "max_model_steps": profile.max_model_steps,
                "capabilities": sorted(str(item) for item in profile.allowed_capabilities),
                "plain": profile is PLAIN_PROFESSION,
            }
        )
    return rows
