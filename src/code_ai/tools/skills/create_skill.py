from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema
from code_ai.tools.skills.common import (
    SKILL_ENTRYPOINT,
    render_skill_markdown,
    sanitize_skill_name,
    skills_root,
)
from code_ai.util.fileio import RetryPolicy, atomic_write_text


class CreateSkillTool:
    name = "create_skill"
    description = (
        "Author a reusable '.md' skill under ~/.code-ai/skills so it can be loaded "
        "later with use_skill. Provide a short name, a one-line description used for "
        "discovery, and the full instructions the skill should contain."
    )
    capabilities = frozenset({ToolCapability.LOCAL_WRITE})
    input_schema = tool_schema(
        {
            "name": {
                "type": "string",
                "description": (
                    "Short slug for the skill (lowercase letters, digits, '-' or '_'), "
                    "e.g. 'release-notes'."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "One-line summary of what the skill does and when to use it; "
                    "shown when listing skills."
                ),
            },
            "instructions": {
                "type": "string",
                "description": (
                    "The full markdown body of the skill: the steps, conventions, and "
                    "guidance to follow when the skill is used."
                ),
            },
            "overwrite": {
                "type": "boolean",
                "description": "Replace an existing skill of the same name. Defaults to false.",
            },
        },
        required=("name", "description", "instructions"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        name = sanitize_skill_name(arguments.get("name"))
        description = str(arguments.get("description") or "").strip()
        instructions = str(arguments.get("instructions") or "").strip()
        overwrite = bool(arguments.get("overwrite", False))
        if not description:
            raise ToolArgumentError("description is required.")
        if not instructions:
            raise ToolArgumentError("instructions is required.")

        root = skills_root()
        skill_dir = root / name
        entry = skill_dir / SKILL_ENTRYPOINT
        existed = entry.exists()
        if existed and not overwrite:
            raise ToolExecutionError(
                f"Skill already exists: {name}. Pass overwrite=true to replace it."
            )

        content = render_skill_markdown(
            name=name, description=description, instructions=instructions
        )
        skill_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            entry,
            content,
            policy=RetryPolicy.from_config(context.config.file_io),
            allow_non_atomic_fallback=context.config.file_io.allow_non_atomic_fallback,
        )

        await context.event_bus.emit(
            "skill.created",
            {"name": name, "path": str(entry), "overwritten": existed},
            source="tool.create_skill",
        )
        return {
            "name": name,
            "path": str(entry),
            "skills_dir": str(root),
            "overwritten": existed,
            "bytes_written": len(content.encode("utf-8")),
        }

