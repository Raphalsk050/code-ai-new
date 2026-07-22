from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.core.memory import VALID_KINDS
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class RememberTool:
    """Persist a durable fact so it survives across turns and sessions.

    The model calls this when the user states a lasting preference/instruction,
    or proactively when it discovers something about the project worth keeping.
    Facts are deduplicated by content and re-injected into the system prompt on
    later turns, so the agent actually acts on what it remembers.
    """

    name = "remember"
    description = (
        "Save a durable fact to long-term memory. Use it when the user states a "
        "lasting preference or instruction (e.g. 'always run tests with pytest -q', "
        "'my stack is FastAPI'), or proactively when you discover something about "
        "this project that will help in future turns or sessions. Be selective: do "
        "not store trivia or anything already evident from the code or git history. "
        "Choose 'user' for who the user is, 'feedback' for how you should work, "
        "'project' for facts about this codebase, 'reference' for external pointers "
        "(URLs, tickets). 'user' and 'feedback' are remembered everywhere; 'project' "
        "and 'reference' are scoped to this workspace. When a fact supersedes a "
        "memory you can see in your Memory section, pass that memory's exact text "
        "as 'replaces' so the outdated version is retired."
    )
    capabilities = frozenset({ToolCapability.MEMORY})
    input_schema = tool_schema(
        {
            "content": {
                "type": "string",
                "description": (
                    "The fact to remember, as one concise self-contained sentence. "
                    "Resolve relative dates to absolute ones."
                ),
            },
            "kind": {
                "type": "string",
                "description": (
                    "One of: user, feedback, project, reference. "
                    "user = who the user is; feedback = how you should work; "
                    "project = facts about this codebase; reference = external pointer."
                ),
            },
            "replaces": {
                "type": "string",
                "description": (
                    "Optional: the exact text of an existing memory this fact "
                    "supersedes (as shown in your Memory section). The outdated "
                    "memory is deleted so contradictory facts never coexist."
                ),
            },
        },
        required=("content", "kind"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        content = str(arguments.get("content", "")).strip()
        if not content:
            raise ToolArgumentError("content is required.")
        kind = str(arguments.get("kind", "")).strip()
        if kind not in VALID_KINDS:
            raise ToolArgumentError(
                f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}."
            )
        if context.memory is None:
            raise ToolArgumentError("Memory is not available in this session.")

        entry = context.memory.add(kind=kind, content=content, source="remember_tool")
        # Save first, then retire what it supersedes, so an exact re-statement
        # (replaces == content) can never delete the fact it just saved.
        replaces = str(arguments.get("replaces", "")).strip()
        replaced = False
        if replaces and replaces != entry.content:
            replaced = context.memory.remove_by_content(replaces)
        await context.event_bus.emit(
            "memory.saved",
            {"kind": entry.kind, "content": entry.content, "replaced": replaced},
            source="tools.remember",
        )
        result: dict[str, Any] = {"remembered": entry.content, "kind": entry.kind}
        if replaces:
            result["replaced_previous"] = replaced
        return result
