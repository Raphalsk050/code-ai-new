"""MCP tools as Code-AI tools, and the three tools that reach resources and prompts.

A server's tool keeps its own schema and description; its name becomes
``mcp__<server>__<tool>`` so two servers can both have a ``search``. Whatever a
server sends back is somebody else's text, so it reaches the model fenced as
data, the way a sub-agent's report does.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolCapability, ToolContext
from code_ai.tools.output import fence_untrusted
from code_ai.tools.schema import tool_schema

if TYPE_CHECKING:
    from code_ai.mcp.manager import McpManager

PREFIX = "mcp__"
# Function names on every provider we talk to: letters, digits, _ and -, <= 64.
_MAX_NAME = 64
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_DESCRIPTION = 1500
_FENCE = "mcp_output"


def qualified_name(server: str, tool: str) -> str:
    name = f"{PREFIX}{server}__{_UNSAFE.sub('_', tool)}"
    if len(name) <= _MAX_NAME:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[: _MAX_NAME - 9]}_{digest}"


def normalized_schema(raw: Any) -> dict[str, Any]:
    """The server's input schema, shaped the way every provider accepts it."""

    schema = dict(raw) if isinstance(raw, dict) else {}
    schema.pop("$schema", None)
    schema["type"] = "object"
    if not isinstance(schema.get("properties"), dict):
        schema["properties"] = {}
    return schema


class McpTool:
    """One tool of one server, callable like any other tool."""

    capabilities = frozenset({ToolCapability.MCP})
    # The schema is the server's; strict mode would refuse most of them.
    strict = False

    def __init__(
        self, *, manager: McpManager, server: str, spec: dict[str, Any], trusted: bool
    ) -> None:
        self._manager = manager
        self.server = server
        self.remote_name = str(spec.get("name") or "")
        self.name = qualified_name(server, self.remote_name)
        annotations = spec.get("annotations") if isinstance(spec.get("annotations"), dict) else {}
        self.title = str(spec.get("title") or annotations.get("title") or self.remote_name)
        self.read_only = annotations.get("readOnlyHint") is True
        # Hints are only believed from a server the user said to trust.
        self.approval_exempt = trusted and self.read_only
        text = " ".join(str(spec.get("description") or self.title).split())
        if len(text) > _MAX_DESCRIPTION:
            text = text[: _MAX_DESCRIPTION - 3].rstrip() + "..."
        self.description = f"[MCP {server}] {text}"
        self.input_schema = normalized_schema(spec.get("inputSchema"))

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        result = await self._manager.call_tool(
            self.server, self.remote_name, arguments, cancel=context.cancel_event
        )
        payload = result_payload(result)
        if result.get("isError") is True:
            raise ToolExecutionError(
                f"{self.name} failed: {payload.get('content') or 'the server reported an error.'}"
            )
        return payload


def result_payload(result: dict[str, Any]) -> dict[str, Any]:
    """A ``tools/call`` result, as a payload the orchestrator can hand the model."""

    texts: list[str] = []
    images: list[dict[str, str]] = []
    links: list[dict[str, Any]] = []
    notes: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            texts.append(str(item.get("text") or ""))
        elif kind == "image" and item.get("data"):
            images.append(
                {"data": str(item["data"]), "media_type": str(item.get("mimeType") or "image/png")}
            )
        elif kind == "resource_link":
            links.append(
                {
                    key: item[key]
                    for key in ("uri", "name", "title", "description", "mimeType")
                    if item.get(key)
                }
            )
        elif kind == "resource":
            texts.extend(_resource_texts([item.get("resource") or {}], images, notes))
        elif kind == "audio":
            notes.append(f"An audio clip ({item.get('mimeType') or 'audio'}) was returned.")
    payload: dict[str, Any] = {}
    text = "\n\n".join(part for part in texts if part.strip())
    if text:
        payload["content"] = fence_untrusted(text, tag=_FENCE)
    structured = result.get("structuredContent")
    if structured is not None:
        payload["structured"] = structured
    if links:
        payload["resource_links"] = links
    if notes:
        payload["notes"] = notes
    if images:
        payload[TOOL_IMAGES_KEY] = images
    if not payload:
        payload["content"] = "(the tool returned nothing)"
    return payload


def _resource_texts(
    contents: list[dict[str, Any]], images: list[dict[str, str]], notes: list[str]
) -> list[str]:
    texts: list[str] = []
    for resource in contents:
        if not isinstance(resource, dict):
            continue
        uri = str(resource.get("uri") or "")
        mime = str(resource.get("mimeType") or "")
        if "text" in resource:
            texts.append(f"{uri}:\n{resource.get('text') or ''}" if uri else str(resource["text"]))
        elif resource.get("blob") and mime.startswith("image/"):
            images.append({"data": str(resource["blob"]), "media_type": mime})
        elif resource.get("blob"):
            notes.append(f"{uri or 'A resource'} is binary ({mime or 'unknown type'}).")
    return texts


# -- resources and prompts, for the model --------------------------------------


class ListMcpResourcesTool:
    name = "list_mcp_resources"
    description = (
        "List the resources MCP servers offer - files, records, documents a server "
        "can hand over by URI - and the URI templates they accept. Read one with "
        "read_mcp_resource."
    )
    capabilities = frozenset({ToolCapability.MCP})
    approval_exempt = True
    read_only = True
    input_schema = tool_schema(
        {"server": {"type": "string", "description": "Only this server's resources."}}
    )

    def __init__(self, manager: McpManager) -> None:
        self._manager = manager

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        server = str(arguments.get("server") or "").strip() or None
        return {"servers": await self._manager.list_resources(server)}


class ReadMcpResourceTool:
    name = "read_mcp_resource"
    description = "Read one resource from an MCP server by its URI."
    capabilities = frozenset({ToolCapability.MCP})
    approval_exempt = True
    read_only = True
    input_schema = tool_schema(
        {
            "server": {"type": "string", "description": "The server that offers it."},
            "uri": {"type": "string", "description": "The resource's URI."},
        },
        required=("server", "uri"),
    )

    def __init__(self, manager: McpManager) -> None:
        self._manager = manager

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        server = str(arguments.get("server") or "").strip()
        uri = str(arguments.get("uri") or "").strip()
        if not server or not uri:
            raise ToolArgumentError("server and uri are required.")
        contents = await self._manager.read_resource(server, uri)
        images: list[dict[str, str]] = []
        notes: list[str] = []
        text = "\n\n".join(_resource_texts(contents, images, notes))
        payload: dict[str, Any] = {"uri": uri}
        if text:
            payload["content"] = fence_untrusted(text, tag=_FENCE)
        if notes:
            payload["notes"] = notes
        if images:
            payload[TOOL_IMAGES_KEY] = images
        return payload


class GetMcpPromptTool:
    name = "get_mcp_prompt"
    description = (
        "Fetch a prompt an MCP server offers - a ready-made instruction, filled with "
        "the arguments you give - and follow it."
    )
    capabilities = frozenset({ToolCapability.MCP})
    approval_exempt = True
    read_only = True
    input_schema = tool_schema(
        {
            "server": {"type": "string", "description": "The server that offers it."},
            "name": {"type": "string", "description": "The prompt's name."},
            "arguments": {
                "type": "object",
                "description": "Its arguments, name to value.",
                "additionalProperties": {"type": "string"},
            },
        },
        required=("server", "name"),
    )

    def __init__(self, manager: McpManager) -> None:
        self._manager = manager

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        server = str(arguments.get("server") or "").strip()
        name = str(arguments.get("name") or "").strip()
        if not server or not name:
            raise ToolArgumentError("server and name are required.")
        raw = arguments.get("arguments")
        values = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        prompt = await self._manager.get_prompt(server, name, values)
        return {"prompt": fence_untrusted(render_prompt(prompt), tag=_FENCE)}


def render_prompt(prompt: dict[str, Any]) -> str:
    """A ``prompts/get`` answer as plain text: each message under its role."""

    parts: list[str] = []
    description = str(prompt.get("description") or "").strip()
    if description:
        parts.append(description)
    for message in prompt.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        items = content if isinstance(content, list) else [content]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(f"[{message.get('role', 'user')}]\n{item.get('text') or ''}")
            elif item.get("type") == "resource":
                resource = item.get("resource") or {}
                if "text" in resource:
                    parts.append(f"[{resource.get('uri', 'resource')}]\n{resource['text']}")
    return "\n\n".join(parts) or json.dumps(prompt)[:2000]
