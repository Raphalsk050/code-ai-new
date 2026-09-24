"""Model Context Protocol client: other programs' tools, resources and prompts,
offered to the agent as if they were its own."""

from code_ai.mcp.config import McpServerConfig, load_servers
from code_ai.mcp.manager import McpManager

__all__ = ["McpManager", "McpServerConfig", "load_servers"]
