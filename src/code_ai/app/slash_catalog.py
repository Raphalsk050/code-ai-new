"""The slash-command catalog, shared by every front end.

The terminal owns how a command is typed and rendered; what the commands *are*
is not a terminal concern. Keeping the list here is what stops the TUI and the
VS Code extension from drifting into two different notions of ``/goal`` - the
extension asks the bridge for this catalog rather than hard-coding its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SlashCommand:
    command: str
    description: str
    completion: str | None = None

    @property
    def completion_text(self) -> str:
        return self.completion or self.command


SLASH_COMMANDS = [
    SlashCommand("/help", "Show available commands."),
    SlashCommand("/doctor", "Guided setup: configure everything step by step."),
    SlashCommand("/doctor model", "Tune temperature, top_p, top_k and more; applied live."),
    SlashCommand("/doctor tools", "Switch the agent's tools on and off; applied live."),
    SlashCommand(
        "/doctor mcp", "MCP servers: add, approve, sign in, switch their tools; applied live."
    ),
    SlashCommand(
        "/doctor subagents",
        "Sub-agent limits, tool-call protocol, and the models they may use.",
    ),
    SlashCommand("/status", "Show current session and provider state."),
    SlashCommand("/compact", "Request context compression."),
    SlashCommand("/forget", "Forget your last message and its answer."),
    SlashCommand("/auto", "Switch planner mode to auto."),
    SlashCommand("/plan", "Switch planner mode to plan."),
    SlashCommand("/act", "Switch planner mode to act."),
    SlashCommand(
        "/mode <ask|auto|bypass>",
        "Set the tool permission mode (persisted).",
        "/mode ",
    ),
    SlashCommand(
        "/deep-plan <objective>",
        "Plan a task without changing anything (Cline-style plan mode).",
        "/deep-plan ",
    ),
    SlashCommand("/plan-status", "Show planner phase and current step."),
    SlashCommand("/replan", "Request a bounded replan on the next turn."),
    SlashCommand(
        "/goal <objective>",
        "Define a persistent goal; the agent iterates until it is verifiably met.",
        "/goal ",
    ),
    SlashCommand("/goal start", "Confirm the proposed criteria and start the goal loop."),
    SlashCommand("/goal status", "Show the goal, its criteria, and loop progress."),
    SlashCommand("/goal stop", "Stop the goal loop (also cancels the running turn)."),
    SlashCommand("/goal resume", "Resume a blocked goal and restart the loop."),
    SlashCommand("/cancel", "Cancel the active turn."),
    SlashCommand("/index", "Refresh the code index (incremental) for fast search_index."),
    SlashCommand("/index full", "Rebuild the code index from scratch."),
    SlashCommand("/index status", "Show what the code index holds."),
    SlashCommand(
        "/index <dir>",
        "Refresh the code index for one workspace subtree.",
        "/index ",
    ),
    SlashCommand(
        "/term <text>",
        "Type a line into the shared interactive terminal session.",
        "/term ",
    ),
    SlashCommand("/term start", "Open an interactive terminal in the workspace."),
    SlashCommand("/term status", "Show the terminal session and its screen."),
    SlashCommand(
        "/term ctrl <c|d|z>",
        "Send a control key (e.g. Ctrl+C) to the terminal.",
        "/term ctrl ",
    ),
    SlashCommand("/term kill", "Terminate the interactive terminal session."),
    SlashCommand(
        "/debug <on|off|status>",
        "Log raw model requests/responses for parser debugging.",
        "/debug ",
    ),
    SlashCommand("/workflows", "List the saved workflows you can run by name."),
    SlashCommand("/skills", "List the skills you can force by name."),
    SlashCommand(
        "/rules", "List the rules and instruction files in effect, and where each came from."
    ),
    SlashCommand("/agents", "List the sub-agent types the agent can delegate to."),
    SlashCommand("/mcp", "Show the MCP servers, their state and their tools."),
    SlashCommand(
        "/mcp add <name> <command or url>",
        "Add an MCP server in one line; the rest is in /doctor mcp.",
        "/mcp add ",
    ),
    SlashCommand("/mcp remove <name>", "Take an MCP server out of your config.", "/mcp remove "),
    SlashCommand(
        "/mcp reconnect <name>", "Restart one MCP server.", "/mcp reconnect "
    ),
    SlashCommand(
        "/mcp approve <name>",
        "Let a server the project declares start, now and next time.",
        "/mcp approve ",
    ),
    SlashCommand("/mcp on <name>", "Switch an MCP server on.", "/mcp on "),
    SlashCommand("/mcp off <name>", "Switch an MCP server off.", "/mcp off "),
    SlashCommand(
        "/mcp answer <values>",
        "Answer the question an MCP server is waiting on (name=value ...).",
        "/mcp answer ",
    ),
    SlashCommand("/mcp decline", "Decline the question an MCP server is waiting on."),
    SlashCommand(
        "/mcp login <name>", "Sign in to a remote MCP server in the browser.", "/mcp login "
    ),
    SlashCommand(
        "/mcp logout <name>", "Forget the sign-in of a remote MCP server.", "/mcp logout "
    ),
    SlashCommand("/clear", "Clear the conversation view."),
    SlashCommand("/update", "Install the newest Code-AI release, when there is one."),
    SlashCommand("/quit", "Close Code-AI."),
    SlashCommand("/config help", "Browse and pick a /config command to run."),
    SlashCommand("/config show", "Show redacted active config."),
    SlashCommand(
        "/config models",
        "List models offered by your provider and pick one.",
    ),
    SlashCommand(
        "/config model <name>",
        "Persist and switch the model for future calls.",
        "/config model ",
    ),
    SlashCommand(
        "/config vision-model <name>",
        "Vision model that reads pasted images for a non-multimodal main model.",
        "/config vision-model ",
    ),
    SlashCommand(
        "/config api-key <key>",
        "Persist and switch the provider API key (redacted).",
        "/config api-key ",
    ),
    SlashCommand(
        "/config api-mode <responses|completions|ollama>",
        "Persist and switch the API mode.",
        "/config api-mode ",
    ),
    SlashCommand(
        "/config base-url <url>",
        "Persist and switch the provider base URL.",
        "/config base-url ",
    ),
    SlashCommand(
        "/config workspace <path>",
        "Persist and switch the project directory.",
        "/config workspace ",
    ),
    SlashCommand(
        "/config language <code>",
        "Persist and switch response language for future calls.",
        "/config language ",
    ),
    SlashCommand(
        "/config effort <none|minimal|low|medium|high|xhigh>",
        "Persist and switch reasoning_effort (OpenAI Responses API).",
        "/config effort ",
    ),
    SlashCommand(
        "/config theme <name>",
        "Recolour the whole terminal (monokai is Code-AI's own palette).",
        "/config theme ",
    ),
    SlashCommand(
        "/config banner-font <name>",
        "Persist and switch the banner art font.",
        "/config banner-font ",
    ),
    SlashCommand(
        "/config spinner <name>",
        "Persist and switch the working-indicator animation.",
        "/config spinner ",
    ),
    SlashCommand(
        "/config max-context-window <tokens>",
        "Persist and apply the max context window size in tokens.",
        "/config max-context-window ",
    ),
    SlashCommand(
        "/config learn <on|off>",
        "Show/hide the model's explanation of why it's making each change.",
        "/config learn ",
    ),
    SlashCommand(
        "/config live-code <on|off>",
        "Show/hide the file being written, live, as the model streams it.",
        "/config live-code ",
    ),
]


# Commands the JSON-RPC bridge can actually run today. A front end that is not
# the terminal greys out the rest rather than pretending they work: the bridge
# has no shell to run ``/quit`` in and no config screen behind ``/doctor``.
BRIDGE_SUPPORTED_PREFIXES = (
    "/act",
    "/agents",
    "/auto",
    "/cancel",
    "/clear",
    "/compact",
    "/goal",
    "/index",
    "/mode",
    "/plan",
    "/replan",
    "/rules",
    "/skills",
    "/term",
    "/update",
    "/workflows",
)


def bridge_supports(command: str) -> bool:
    """Whether ``command`` maps onto a bridge method.

    Matched on the command word, not the whole string: the catalog spells
    arguments out (``/goal <objective>``) and those are the same command.
    """

    name = command.split(" <")[0].strip().lower()
    return any(
        name == prefix or name.startswith(prefix + " ") for prefix in BRIDGE_SUPPORTED_PREFIXES
    )


def catalog_payload(
    *,
    workflows: Sequence[Mapping[str, object]] = (),
    skills: Sequence[Mapping[str, object]] = (),
    mcp_prompts: Sequence[Mapping[str, object]] = (),
) -> list[dict[str, object]]:
    """The catalog as the wire carries it, one entry per command.

    Workflows and skills are appended as commands of their own, the way the
    terminal's picker shows them: they are user-authored and discovered on
    disk, so they cannot be declared in the static list above. Workflows come
    first - running a saved procedure is the more specific intent when a name
    exists as both.
    """

    entries: list[dict[str, object]] = [
        {
            "command": entry.command,
            "description": entry.description,
            "completion": entry.completion_text,
            "available": bridge_supports(entry.command),
            "kind": "command",
        }
        for entry in SLASH_COMMANDS
    ]
    for kind, records in (("workflow", workflows), ("skill", skills), ("mcp", mcp_prompts)):
        for record in records:
            command = str(record.get("command") or f"/{record.get('name') or ''}").strip()
            if not command or command == "/":
                continue
            entries.append(
                {
                    "command": command,
                    "description": str(record.get("description") or ""),
                    "completion": command + " ",
                    "available": True,
                    "kind": kind,
                }
            )
    return entries
