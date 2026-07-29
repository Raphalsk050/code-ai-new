from __future__ import annotations

from typing import Any

from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.output import bound_text
from code_ai.tools.schema import tool_schema
from code_ai.tools.skills.common import (
    SkillSource,
    discover_skills_from,
    load_skill_from,
    native_skill_source,
)


def _sources(context: ToolContext) -> list[SkillSource]:
    """Skill directories to search, as wired for this session.

    The session list (Code-AI's own directory plus any third-party layout found
    in the workspace) is injected on the context. Without it - a directly
    constructed context - only Code-AI's own directory is searched, so the tool
    keeps working with no wiring at all.
    """

    configured = getattr(context, "skill_sources", None)
    return list(configured) if configured else [native_skill_source()]


class UseSkillTool:
    name = "use_skill"
    description = (
        "Discover and load reusable '.md' skills. Call with no name to list available "
        "skills; call with a name to load that skill's instructions, then follow them "
        "for the current task. Skills are read from ~/.code-ai/skills and from skill "
        "directories other agents keep in this workspace."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "name": {
                "type": "string",
                "description": (
                    "Name of the skill to load. Omit (null) to list every available "
                    "skill with its description."
                ),
            },
        },
        required=(),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        sources = _sources(context)
        requested = arguments.get("name")
        name = str(requested).strip() if requested is not None else ""

        if not name:
            skills = discover_skills_from(sources)
            return {
                "skills_dirs": [str(source.root) for source in sources],
                "count": len(skills),
                "skills": [record.to_summary() for record in skills],
            }

        record = load_skill_from(name, sources=sources)
        max_chars = context.config.budgets.max_tool_output_chars
        return {
            "name": record.name,
            "description": record.description,
            "path": str(record.path),
            "origin": record.origin,
            "instructions": bound_text(record.body, max_chars),
        }
