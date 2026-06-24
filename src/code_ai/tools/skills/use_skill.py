from __future__ import annotations

from typing import Any

from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.output import bound_text
from code_ai.tools.schema import tool_schema
from code_ai.tools.skills.common import discover_skills, load_skill, skills_root


class UseSkillTool:
    name = "use_skill"
    description = (
        "Discover and load reusable '.md' skills from ~/.code-ai/skills. Call with no "
        "name to list available skills; call with a name to load that skill's "
        "instructions, then follow them for the current task."
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
        root = skills_root()
        requested = arguments.get("name")
        name = str(requested).strip() if requested is not None else ""

        if not name:
            skills = discover_skills(root)
            return {
                "skills_dir": str(root),
                "count": len(skills),
                "skills": [record.to_summary() for record in skills],
            }

        record = load_skill(name, root=root)
        max_chars = context.config.budgets.max_tool_output_chars
        return {
            "skills_dir": str(root),
            "name": record.name,
            "description": record.description,
            "path": str(record.path),
            "instructions": bound_text(record.body, max_chars),
        }
