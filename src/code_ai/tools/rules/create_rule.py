from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from code_ai.config.defaults import global_rules_dir, project_rules_dir
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema
from code_ai.tools.skills.common import render_skill_markdown, sanitize_skill_name

_VALID_SCOPES = ("project", "global")


class CreateRuleTool:
    name = "create_rule"
    description = (
        "Author a mandatory rule the agent must always follow. Rules are injected "
        "into the system prompt every session, unlike skills which load on demand. "
        "Use scope 'project' for rules committed with this workspace (the default) "
        "or 'global' for personal rules that apply in every project."
    )
    capabilities = frozenset({ToolCapability.LOCAL_WRITE})
    input_schema = tool_schema(
        {
            "name": {
                "type": "string",
                "description": (
                    "Short slug for the rule (lowercase letters, digits, '-' or '_'), "
                    "e.g. 'run-tests-before-done'."
                ),
            },
            "description": {
                "type": "string",
                "description": "One-line summary of what the rule enforces.",
            },
            "content": {
                "type": "string",
                "description": (
                    "The rule itself: a short, imperative instruction the agent must "
                    "always follow. Explain the why when it is not obvious."
                ),
            },
            "scope": {
                "type": "string",
                "description": (
                    "Either 'project' (default), which stores the rule in "
                    "<workspace>/.code-ai/rules so it is committed with the repo, or "
                    "'global', which stores it in ~/.code-ai/rules so it applies in "
                    "every project."
                ),
            },
            "overwrite": {
                "type": "boolean",
                "description": "Replace an existing rule of the same name. Defaults to false.",
            },
        },
        required=("name", "description", "content"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        name = sanitize_skill_name(arguments.get("name"))
        description = str(arguments.get("description") or "").strip()
        content = str(arguments.get("content") or "").strip()
        scope = str(arguments.get("scope") or "project").strip().lower()
        overwrite = bool(arguments.get("overwrite", False))
        if not description:
            raise ToolArgumentError("description is required.")
        if not content:
            raise ToolArgumentError("content is required.")
        if scope not in _VALID_SCOPES:
            raise ToolArgumentError(f"scope must be one of: {', '.join(_VALID_SCOPES)}.")

        directory = (
            global_rules_dir()
            if scope == "global"
            else project_rules_dir(context.config.workspace)
        )
        path = directory / f"{name}.md"
        existed = path.exists()
        if existed and not overwrite:
            raise ToolExecutionError(
                f"Rule already exists: {name} ({scope}). Pass overwrite=true to replace it."
            )

        rendered = render_skill_markdown(
            name=name, description=description, instructions=content
        )
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, rendered)

        await context.event_bus.emit(
            "rule.created",
            {"name": name, "scope": scope, "path": str(path), "overwritten": existed},
            source="tool.create_rule",
        )
        return {
            "name": name,
            "scope": scope,
            "path": str(path),
            "rules_dir": str(directory),
            "overwritten": existed,
            "bytes_written": len(rendered.encode("utf-8")),
        }


def _atomic_write(path: Path, content: str) -> None:
    data = content.encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
