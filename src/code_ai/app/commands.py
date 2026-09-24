"""Session operations that are not a turn, in one UI-agnostic place.

The terminal grew these first (``/term``, ``/goal``, ``/index``, ``/replan``)
and kept them in its own controller, which meant the JSON-RPC bridge had two
options: duplicate them or do without. Both are how a second front end ends up
behaving differently from the first.

So the behaviour lives here, returning data, and each front end does its own
wording: the terminal prefixes its lines with ``term>``, the extension renders
the same result in a panel.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

from code_ai.app.service import CodeAIApplication
from code_ai.core.errors import (
    CodeAIError,
    ConfigurationError,
    GoalStateError,
    TerminalSessionError,
)
from code_ai.core.workflows import render_workflow_invocation
from code_ai.mcp.config import quick_declaration
from code_ai.mcp.manager import system_browser
from code_ai.mcp.tools import render_prompt
from code_ai.prompts import build_runtime_note
from code_ai.tools.skills.common import discover_skills_from, render_skill_invocation


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a command did, for a caller that will phrase it itself."""

    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, **self.data}


def _failed(message: str) -> CommandResult:
    return CommandResult(ok=False, message=message)


_MCP_STATUS = {
    "connected": "connected",
    "connecting": "connecting",
    "failed": "failed",
    "disabled": "off",
    "needs_approval": "waiting for approval (/mcp approve <name>)",
    "needs_auth": "needs sign-in",
    "invalid": "invalid declaration",
}


def prompt_arguments(declared: list[Any], text: str) -> dict[str, str]:
    """An MCP prompt's arguments out of what the user typed after its command.

    ``key=value`` pairs name them; otherwise the words fill them in order, and a
    prompt with a single argument takes the whole text.
    """

    names = [str(item.get("name") or "") for item in declared if isinstance(item, dict)]
    required = [
        str(item.get("name") or "")
        for item in declared
        if isinstance(item, dict) and item.get("required")
    ]
    text = text.strip()
    values: dict[str, str] = {}
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    if words and all("=" in word for word in words):
        for word in words:
            key, _, value = word.partition("=")
            values[key.strip()] = value
    elif len(names) == 1 and text:
        values[names[0]] = text
    else:
        for name, word in zip(names, words, strict=False):
            values[name] = word
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise ValueError(f"The prompt needs {', '.join(missing)}.")
    return values


class SessionCommands:
    """Plan, goal, terminal and index operations against one application."""

    def __init__(self, app: CodeAIApplication) -> None:
        self._app = app

    # -- planner -----------------------------------------------------------

    def plan_snapshot(self) -> CommandResult:
        snapshot = self._app.get_plan_snapshot()
        return CommandResult(ok=True, data={"plan": snapshot})

    async def replan(self, reason: str = "") -> CommandResult:
        message = await self._app.request_replan(reason=reason or None)
        return CommandResult(ok=True, message=message, data={"plan": self._app.get_plan_snapshot()})

    async def start_plan(self) -> CommandResult:
        started = await self._app.start_plan_execution()
        if not started:
            return _failed("No plan is waiting to be started.")
        return CommandResult(ok=True, message="Plan execution started.")

    # -- goal --------------------------------------------------------------

    async def goal_define(self, objective: str) -> CommandResult:
        text = objective.strip()
        if not text:
            return _failed("Give the goal an objective: /goal <objective>.")
        try:
            snapshot = await self._app.define_goal(text)
        except GoalStateError as exc:
            return _failed(str(exc))
        started = bool(snapshot.get("started"))
        message = (
            "Goal defined and loop started."
            if started
            else "Goal defined. Review the criteria, then start the loop."
        )
        return CommandResult(ok=True, message=message, data={"goal": snapshot})

    async def goal_start(self) -> CommandResult:
        try:
            snapshot = await self._app.start_goal()
        except GoalStateError as exc:
            return _failed(str(exc))
        return CommandResult(ok=True, message="Goal loop running.", data={"goal": snapshot})

    async def goal_stop(self) -> CommandResult:
        try:
            snapshot = await self._app.stop_goal()
        except GoalStateError as exc:
            return _failed(str(exc))
        return CommandResult(ok=True, message="Goal loop stopped.", data={"goal": snapshot})

    def goal_status(self) -> CommandResult:
        snapshot = self._app.goal_snapshot()
        if snapshot.get("status") == "none":
            return CommandResult(ok=True, message="No goal defined.", data={"goal": snapshot})
        return CommandResult(ok=True, data={"goal": snapshot})

    # -- interactive terminal ---------------------------------------------
    #
    # The PTY sessions the model drives through its terminal tools, reachable by
    # the human: both ends type into the same shell rather than the user being
    # locked out of it.

    @property
    def _terminal_manager(self) -> Any | None:
        return getattr(self._app, "terminal_manager", None)

    async def terminal_start(self, command: str = "") -> CommandResult:
        manager = self._terminal_manager
        if manager is None:
            return _failed("No terminal backend in this session.")
        try:
            session_id = manager.create(
                cwd=self._app.session.config.workspace, command=command or None
            )
        except TerminalSessionError as exc:
            return _failed(str(exc))
        screen = await self._emit_screen(manager, session_id)
        self._tell_model_about_terminal(session_id, command)
        return CommandResult(
            ok=True,
            message=f"Terminal {session_id[:8]} started.",
            data={"session_id": session_id, "screen": screen},
        )

    def _tell_model_about_terminal(self, session_id: str, command: str) -> None:
        """Put a user-opened terminal into the conversation.

        The session is created outside any turn, so the model has no way to know
        it exists: asked about the id on screen, it answered that it had no
        access to the editor and could not tell which shell that was. It is the
        same manager the terminal tools drive, so all it was missing is being
        told.
        """

        conversation = getattr(getattr(self._app, "orchestrator", None), "conversation", None)
        if conversation is None:
            # Nothing to tell, and the terminal is what the user asked for:
            # opening it must not fail over a note that could not be written.
            return

        started = f"`{command}`" if command else "the default shell"
        conversation.add_user(
            build_runtime_note(
                f"The user opened an interactive terminal in their editor, running "
                f"{started}. Its session_id is {session_id}. It is the same terminal "
                f"the terminal tools drive: pass that session_id to run commands in "
                f"it, and whatever you run there is visible on their screen. It stays "
                f"open until they close it, so state set in it (cd, activated "
                f"virtualenv, exported variables) persists between commands."
            )
        )

    async def terminal_write(self, text: str, *, enter: bool = True) -> CommandResult:
        def action(manager: Any, session_id: str) -> None:
            manager.send_text(session_id, text)
            if enter:
                manager.send_enter(session_id)

        return await self._terminal_action(action)

    async def terminal_control(self, key: str) -> CommandResult:
        return await self._terminal_action(
            lambda manager, session_id: manager.send_control(session_id, key)
        )

    async def terminal_kill(self) -> CommandResult:
        manager = self._terminal_manager
        session_id = manager.latest_session_id() if manager else None
        if manager is None or session_id is None:
            return _failed("No terminal session is running.")
        try:
            manager.terminate(session_id)
        except TerminalSessionError as exc:
            return _failed(str(exc))
        screen = manager.read_screen(session_id)
        screen["closed"] = True
        await self._app.event_bus.emit("terminal.screen.updated", screen, source="app.commands")
        return CommandResult(
            ok=True, message=f"Terminal {session_id[:8]} closed.", data={"screen": screen}
        )

    async def terminal_status(self) -> CommandResult:
        manager = self._terminal_manager
        session_id = manager.latest_session_id() if manager else None
        if manager is None or session_id is None:
            return _failed("No terminal session is running.")
        screen = await self._emit_screen(manager, session_id)
        return CommandResult(ok=True, data={"session_id": session_id, "screen": screen})

    async def _terminal_action(self, action: Any) -> CommandResult:
        manager = self._terminal_manager
        session_id = manager.latest_session_id() if manager else None
        if manager is None or session_id is None:
            return _failed("No terminal session is running. Start one first.")
        try:
            action(manager, session_id)
        except TerminalSessionError as exc:
            return _failed(str(exc))
        screen = await self._emit_screen(manager, session_id)
        return CommandResult(ok=True, data={"session_id": session_id, "screen": screen})

    async def _emit_screen(self, manager: Any, session_id: str) -> dict[str, Any]:
        """Publish the emulated screen so every attached UI repaints from one event."""
        screen = manager.read_screen(session_id)
        await self._app.event_bus.emit("terminal.screen.updated", screen, source="app.commands")
        return screen

    # -- code index --------------------------------------------------------

    async def index_refresh(self, *, full: bool = False, subtree: str = "") -> CommandResult:
        try:
            report = await self._app.refresh_code_index(full=full, subtree=subtree)
        except ConfigurationError as exc:
            return _failed(str(exc))
        kind = "Rebuilt" if full else "Refreshed"
        scope = f" ({subtree})" if subtree else ""
        return CommandResult(ok=True, message=f"{kind} the code index{scope}: {report.summary()}")

    def index_status(self) -> CommandResult:
        status = self._app.code_index_status()
        if status is None:
            return _failed("The code index is disabled (config: index.enabled).")
        return CommandResult(
            ok=True,
            data={
                "index": {
                    "files": status.files,
                    "chunks": status.chunks,
                    "embedded_chunks": status.embedded_chunks,
                    "embedding_model": status.embedding_model,
                    "lexical_engine": status.lexical_engine,
                    "last_refresh": status.last_refresh,
                    "refreshing": status.refreshing,
                    "pending_touches": status.pending_touches,
                    "last_error": status.last_error,
                    "empty": status.empty,
                }
            },
        )

    # -- workflows and skills ----------------------------------------------
    #
    # Both are authored by the user on disk, so they are discovered rather than
    # declared. The terminal turns each into a slash command; the extension
    # merges the same list into its own picker.

    def list_assets(self) -> CommandResult:
        workflows = [
            {
                "name": record.name,
                "command": record.command,
                "description": " ".join(record.description.split()),
                "origin": record.origin,
            }
            for record in self._workflow_records()
        ]
        skills = [
            {
                "name": record.name,
                "command": f"/{record.name}",
                "description": " ".join(record.description.split()),
                "origin": record.origin,
            }
            for record in self._skill_records()
        ]
        return CommandResult(ok=True, data={"workflows": workflows, "skills": skills})

    def resolve_asset(self, token: str, argument: str = "") -> CommandResult:
        """The prompt that running ``/<name>`` should submit, or a miss.

        Workflows are matched first: running a saved procedure is the more
        specific intent when a name exists as both. Returns the prompt rather
        than submitting it, because starting a turn is the caller's business -
        the bridge runs it in the background, the terminal spawns it.
        """
        name = token.strip().lstrip("/").strip()
        if not name:
            return _failed("No workflow or skill named.")

        service = getattr(self._app, "workflows", None)
        workflow = service.find(name) if service is not None else None
        if workflow is not None:
            return CommandResult(
                ok=True,
                message=f"Running workflow {workflow.name}.",
                data={
                    "kind": "workflow",
                    "name": workflow.name,
                    "prompt": render_workflow_invocation(workflow, argument),
                },
            )

        wanted = name.casefold()
        for record in self._skill_records():
            if record.name.casefold() != wanted:
                continue
            config = self._app.session.config
            max_chars = getattr(getattr(config, "budgets", None), "max_tool_output_chars", 20000)
            return CommandResult(
                ok=True,
                message=f"Using skill {record.name}.",
                data={
                    "kind": "skill",
                    "name": record.name,
                    "prompt": render_skill_invocation(record, argument, max_chars=max_chars),
                },
            )
        return _failed(f"No workflow or skill named {name!r}.")

    # -- MCP servers -------------------------------------------------------

    def mcp_status(self) -> CommandResult:
        mcp = getattr(self._app, "mcp", None)
        if mcp is None:
            return _failed("MCP is not available in this session.")
        servers = mcp.snapshot()
        if not self._app.session.config.mcp_enabled:
            return CommandResult(
                ok=True,
                message="MCP servers are switched off (mcp_enabled in the config).",
                data={"servers": servers},
            )
        if not servers:
            return CommandResult(
                ok=True,
                message=(
                    "No MCP server is configured. Add one in the settings panel, under "
                    "mcp_servers in config.json, or in the project's .mcp.json."
                ),
                data={"servers": servers},
            )
        lines = []
        for server in servers:
            status = _MCP_STATUS.get(server["status"], server["status"])
            line = f"{server['name']}: {status} ({server['transport']}"
            if server["tools"]:
                line += f", {len(server['tools'])} tools"
            line += ")"
            if server["error"]:
                line += f" - {server['error'].splitlines()[0]}"
            lines.append(line)
        lines.append("Manage them in /doctor mcp.")
        return CommandResult(ok=True, message="\n".join(lines), data={"servers": servers})

    async def mcp_add(self, text: str) -> CommandResult:
        """``/mcp add <name> <command line or url>``: a server in one line.

        Environment, headers and the rest are the Doctor's MCP tab (or the
        settings panel); this is the quick way in.
        """

        mcp = getattr(self._app, "mcp", None)
        if mcp is None:
            return _failed("MCP is not available in this session.")
        name, _, target = text.strip().partition(" ")
        if not name or not target.strip():
            return _failed("Use /mcp add <name> <command or url>.")
        try:
            described = await mcp.upsert({**quick_declaration(target), "name": name})
        except ValueError as exc:
            return _failed(str(exc))
        return CommandResult(
            ok=True,
            message=f"{described['name']}: added, starting now.",
            data={"server": described},
        )

    async def mcp_action(self, action: str, name: str) -> CommandResult:
        """``reconnect``, ``approve``, ``on``, ``off``, ``login``, ``logout`` or ``remove``."""

        mcp = getattr(self._app, "mcp", None)
        if mcp is None:
            return _failed("MCP is not available in this session.")
        name = name.strip()
        if not name:
            return _failed(f"Say which server: /mcp {action} <name>.")
        try:
            if action == "reconnect":
                described = await mcp.reconnect(name)
            elif action == "approve":
                described = await mcp.approve(name)
            elif action in ("on", "off"):
                described = await mcp.set_enabled(name, action == "on")
            elif action == "login":
                described = await mcp.sign_in(name, open_browser=system_browser)
            elif action == "logout":
                described = await mcp.sign_out(name)
            elif action == "remove":
                await mcp.remove(name)
                return CommandResult(ok=True, message=f"{name}: removed.")
            else:
                return _failed(
                    f"Unknown /mcp action {action!r}: use add, remove, reconnect, approve, "
                    "on, off, login or logout."
                )
        except ValueError as exc:
            return _failed(str(exc))
        status = _MCP_STATUS.get(described["status"], described["status"])
        return CommandResult(ok=True, message=f"{name}: {status}.", data={"server": described})

    def mcp_answer(self, text: str) -> CommandResult:
        """Answer the question an MCP server is waiting on: ``key=value`` pairs,
        the bare value of a one-field question, or ``decline``/``cancel``."""

        broker = getattr(getattr(self._app, "mcp", None), "elicitation", None)
        pending = broker.pending() if broker is not None else []
        if not pending:
            return _failed("No MCP server is waiting for an answer.")
        request = pending[0]
        text = text.strip()
        if text in ("decline", "cancel"):
            broker.answer(request["id"], text)
            done = "declined" if text == "decline" else "cancelled"
            return CommandResult(ok=True, message=f"{request['server']}: {done}.")
        fields = request["fields"]
        try:
            words = shlex.split(text)
        except ValueError:
            words = text.split()
        if words and all("=" in word for word in words):
            content = dict(word.split("=", 1) for word in words)
        elif len(fields) == 1:
            content = {fields[0]["name"]: text}
        elif not fields:
            content = {}
        else:
            names = " ".join(f"{field_['name']}=..." for field_ in fields)
            return _failed(f"Answer each field by name: /mcp answer {names}")
        problem = broker.answer(request["id"], "accept", content)
        if problem:
            return _failed(problem)
        return CommandResult(ok=True, message=f"Answer sent to {request['server']}.")

    def mcp_prompts(self) -> list[dict[str, Any]]:
        """Every prompt a connected server offers, as ``/mcp:<server>:<prompt>``."""

        mcp = getattr(self._app, "mcp", None)
        if mcp is None:
            return []
        entries: list[dict[str, Any]] = []
        for state in mcp.servers.values():
            for prompt in state.prompts:
                name = str(prompt.get("name") or "")
                if not name or state.client is None:
                    continue
                arguments = [
                    str(argument.get("name") or "")
                    for argument in prompt.get("arguments") or []
                    if isinstance(argument, dict)
                ]
                usage = " ".join(f"<{argument}>" for argument in arguments if argument)
                description = " ".join(str(prompt.get("description") or "").split())
                entries.append(
                    {
                        "name": f"mcp:{state.config.name}:{name}",
                        "command": f"/mcp:{state.config.name}:{name}",
                        "description": f"{description} {usage}".strip()
                        or f"MCP prompt from {state.config.name}.",
                    }
                )
        return entries

    async def resolve_mcp_prompt(self, token: str, argument: str = "") -> CommandResult:
        """The text ``/mcp:<server>:<prompt> [arguments]`` should submit, or why not."""

        mcp = getattr(self._app, "mcp", None)
        if mcp is None:
            return _failed("MCP is not available in this session.")
        server, _, name = token.strip().lstrip("/").removeprefix("mcp:").partition(":")
        state = mcp.servers.get(server)
        spec = next(
            (p for p in (state.prompts if state else []) if str(p.get("name")) == name), None
        )
        if spec is None:
            return _failed(f"No MCP prompt {name!r} on server {server!r}.")
        try:
            values = prompt_arguments(spec.get("arguments") or [], argument)
            prompt = await mcp.get_prompt(server, name, values)
        except (ValueError, CodeAIError) as exc:
            return _failed(str(exc))
        return CommandResult(
            ok=True,
            message=f"Running MCP prompt {server}/{name}.",
            data={"kind": "mcp", "name": f"{server}/{name}", "prompt": render_prompt(prompt)},
        )

    def _workflow_records(self) -> list[Any]:
        service = getattr(self._app, "workflows", None)
        if service is None:
            return []
        try:
            return list(service.load())
        except Exception:  # pragma: no cover - a broken file must not blank the list
            return []

    def _skill_records(self) -> list[Any]:
        sources = tuple(getattr(self._app, "skill_sources", ()) or ())
        if not sources:
            return []
        try:
            return list(discover_skills_from(sources))
        except Exception:  # pragma: no cover
            return []
