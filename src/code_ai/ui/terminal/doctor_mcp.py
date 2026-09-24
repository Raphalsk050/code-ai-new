"""The Doctor's MCP tab: what the settings panel does for MCP servers, in the terminal.

Every server with its state and the buttons that act on it (approve, sign in,
reconnect, switch, edit, remove), its tools as switches, a one-line form to add
a server and a box to paste the JSON a server's README gives. The list follows
the servers live: it is redrawn when one of them changes, and only then, so a
half-typed form is never wiped.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, Static, TextArea

from code_ai.config.loader import persist_config_updates
from code_ai.mcp.config import parse_import, quick_declaration
from code_ai.mcp.manager import system_browser
from code_ai.ui.terminal.clipboard import paste_from_system_clipboard
from code_ai.ui.terminal.palette import active_palette

_STATUS_TEXT = {
    "connected": "connected",
    "connecting": "connecting…",
    "failed": "failed",
    "disabled": "off",
    "needs_approval": "waiting for approval",
    "needs_auth": "needs sign-in",
    "invalid": "invalid",
}
_FAILING = {"failed", "invalid", "needs_auth"}


class McpDoctorPane(VerticalScroll):
    def __init__(
        self,
        application: Any,
        *,
        config_path: Path | None,
        on_change: Callable[[], None] | None,
        status: Callable[[str], None],
    ) -> None:
        super().__init__(id="doctor-mcp")
        self._application = application
        self._config_path = config_path
        self._on_change = on_change
        self._status = status
        self._signature = ""
        # A remove waits for a second press on the same button.
        self._confirm_remove = ""
        # The server the form is editing, "" while it adds a new one.
        self._editing = ""

    @property
    def _mcp(self) -> Any:
        return getattr(self._application, "mcp", None)

    @property
    def _config(self) -> Any:
        return self._application.session.config

    @property
    def _registry(self) -> Any:
        return getattr(getattr(self._application, "orchestrator", None), "tool_registry", None)

    def _disabled(self) -> set[str]:
        names = getattr(self._registry, "disabled_names", None)
        return set(names()) if callable(names) else set(self._config.disabled_tools)

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        if self._mcp is None:
            yield Static("MCP is not available in this session.", classes="doctor-intro")
            return
        yield Static(
            "Tools from other programs: GitHub, databases, a browser. Servers a "
            "project declares (.mcp.json, .vscode/mcp.json, .cursor/mcp.json) start "
            "only after you approve them. Every change is saved and applied at once.",
            classes="doctor-intro",
        )
        yield Checkbox(
            "Use MCP servers (off stops every server and takes their tools away)",
            bool(self._config.mcp_enabled),
            id="doctor-mcp-enabled",
            compact=True,
        )
        yield Vertical(*self._server_rows(), id="doctor-mcp-servers")

        yield Static("Add a server", classes="doctor-tools-section", id="doctor-mcp-form-title")
        yield Static(
            "A command line (npx -y @modelcontextprotocol/server-github) or a URL "
            "(https://mcp.example.com/mcp). Env for a command, headers for a URL, "
            "as KEY=value; KEY2=value. ${NAME} reads your environment.",
            classes="doctor-intro",
        )
        for field, label, placeholder in (
            ("name", "Name", "github"),
            ("target", "Command or URL", "npx -y @modelcontextprotocol/server-github"),
            ("extras", "Env / headers", "GITHUB_PERSONAL_ACCESS_TOKEN=${GITHUB_TOKEN}"),
        ):
            yield Horizontal(
                Static(label, classes="doctor-knob-label"),
                Input(
                    placeholder=placeholder,
                    id=f"doctor-mcp-{field}",
                    classes="doctor-mcp-input",
                    compact=True,
                ),
                classes="doctor-knob",
            )
        yield Checkbox(
            "Legacy SSE (servers from before 2025)", False, id="doctor-mcp-sse", compact=True
        )
        yield Checkbox(
            "Run the tools it marks read-only without asking",
            False,
            id="doctor-mcp-trust",
            compact=True,
        )
        yield Horizontal(
            Button("Add", id="doctor-mcp-add", variant="success", compact=True),
            Button("Clear", id="doctor-mcp-clear", compact=True),
            classes="doctor-tools-actions",
        )
        yield Static("Paste a README's JSON", classes="doctor-tools-section")
        yield TextArea(id="doctor-mcp-json", classes="doctor-mcp-json", soft_wrap=True)
        yield Horizontal(
            Button("Import", id="doctor-mcp-import", variant="success", compact=True),
            Button("Paste clipboard", id="doctor-mcp-paste", compact=True),
            classes="doctor-tools-actions",
        )

    def on_mount(self) -> None:
        if self._mcp is not None:
            self._signature = self._snapshot_signature()
            self.set_interval(1.0, self.refresh_servers)

    def _snapshot_signature(self) -> str:
        servers = self._mcp.snapshot() if self._mcp is not None else []
        essentials = [
            (
                s["name"],
                s["status"],
                s["error"],
                s.get("signed_in"),
                [t["name"] for t in s["tools"]],
                s["spec"]["enabled"],
            )
            for s in servers
        ]
        return json.dumps([essentials, sorted(self._disabled()), self._confirm_remove])

    async def refresh_servers(self, *, force: bool = False) -> None:
        """Redraw the server list when a server changed (or when told to)."""

        if self._mcp is None:
            return
        signature = self._snapshot_signature()
        if not force and signature == self._signature:
            return
        self._signature = signature
        container = self.query_one("#doctor-mcp-servers", Vertical)
        await container.remove_children()
        await container.mount(*self._server_rows())

    def _server_rows(self) -> list[Widget]:
        servers = self._mcp.snapshot()
        if not servers:
            return [
                Static(
                    "No MCP server yet. Add one below, or paste the JSON a server's "
                    "README gives you.",
                    classes="doctor-knob-hint",
                )
            ]
        palette = active_palette()
        disabled = self._disabled()
        rows: list[Widget] = []
        for server in servers:
            name = server["name"]
            status = server["status"]
            colour = {
                "connected": palette.success_bright,
                "failed": palette.error,
                "invalid": palette.error,
                "needs_approval": palette.amber,
                "needs_auth": palette.amber,
            }.get(status, palette.muted)
            head = Text()
            head.append("● ", style=colour)
            head.append(name, style="bold")
            # An invalid declaration was never read far enough to have a transport.
            kind = "" if status == "invalid" else f"{server['transport']} · "
            head.append(f"  {kind}{_STATUS_TEXT.get(status, status)}")
            if status == "connected":
                count = len(server["tools"])
                head.append(f" · {count} tool{'' if count == 1 else 's'}")
            rows.append(Static(head, classes="doctor-mcp-head"))
            for line in self._detail_lines(server):
                rows.append(Static(line, classes="doctor-mcp-detail"))
            rows.append(Horizontal(*self._buttons(server), classes="doctor-mcp-actions"))
            for tool in server["tools"]:
                rows.append(
                    Checkbox(
                        tool["remote"] + ("  (read-only)" if tool.get("read_only") else ""),
                        tool["name"] not in disabled,
                        id=f"doctor-mcp-tool-{tool['name']}",
                        classes="doctor-mcp-tool",
                        tooltip=tool.get("description") or None,
                        compact=True,
                    )
                )
            if server["prompts"]:
                commands = "  ".join(f"/mcp:{name}:{p['name']}" for p in server["prompts"])
                rows.append(Static(Text(f"Prompts: {commands}"), classes="doctor-mcp-detail"))
        return rows

    @staticmethod
    def _detail_lines(server: dict[str, Any]) -> list[Text]:
        error_style = active_palette().error
        lines: list[Text] = []
        if server["source"] != "config":
            where = server.get("source_label") or server["source_path"]
            lines.append(Text(f"Declared by the project in {where}"))
        if server["missing_env"]:
            lines.append(Text(f"Not set in the environment: {', '.join(server['missing_env'])}"))
        if server["error"] and (server["status"] in _FAILING or server["status"] == "disabled"):
            first = server["error"].splitlines()[0]
            style = error_style if server["status"] in _FAILING else ""
            lines.append(Text(first, style=style))
        return lines

    def _buttons(self, server: dict[str, Any]) -> list[Button]:
        name = server["name"]
        status = server["status"]
        own = server["source"] == "config"
        on = server["spec"]["enabled"] if own else status != "needs_approval"
        buttons: list[Button] = []
        if status == "needs_approval":
            buttons.append(
                Button("Approve", id=f"doctor-mcp-approve-{name}", variant="success", compact=True)
            )
        if status == "needs_auth":
            buttons.append(
                Button("Sign in", id=f"doctor-mcp-signin-{name}", variant="primary", compact=True)
            )
        if on and status != "invalid":
            buttons.append(Button("Reconnect", id=f"doctor-mcp-reconnect-{name}", compact=True))
        if status != "invalid":
            buttons.append(
                Button(
                    "Turn off" if on else "Turn on", id=f"doctor-mcp-toggle-{name}", compact=True
                )
            )
        if server.get("signed_in"):
            buttons.append(Button("Sign out", id=f"doctor-mcp-signout-{name}", compact=True))
        if own:
            buttons.append(Button("Edit", id=f"doctor-mcp-edit-{name}", compact=True))
            confirming = self._confirm_remove == name
            buttons.append(
                Button(
                    "Confirm remove" if confirming else "Remove",
                    id=f"doctor-mcp-remove-{name}",
                    variant="error",
                    compact=True,
                )
            )
        return buttons

    # -- events ---------------------------------------------------------------

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("doctor-mcp-"):
            return
        event.stop()
        action, _, name = button_id[len("doctor-mcp-") :].partition("-")
        if action == "add":
            self.run_worker(self._save_form(), exclusive=False)
        elif action == "clear":
            self._clear_form()
        elif action == "import":
            self.run_worker(self._import(), exclusive=False)
        elif action == "paste":
            text = paste_from_system_clipboard()
            if text is None:
                self._status("✗ Clipboard is unavailable on this system.")
            else:
                self.query_one("#doctor-mcp-json", TextArea).text = text
        elif action == "edit":
            self._fill_form(name)
        elif action == "remove" and self._confirm_remove != name:
            self._confirm_remove = name
            self._status(f"Press Confirm remove to take {name} out of your config.")
            await self.refresh_servers(force=True)
        else:
            self._confirm_remove = ""
            self.run_worker(self._act(action, name), exclusive=False)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        checkbox_id = event.checkbox.id or ""
        if checkbox_id == "doctor-mcp-enabled":
            event.stop()
            self._apply_enabled(event.value)
        elif checkbox_id.startswith("doctor-mcp-tool-"):
            event.stop()
            self._apply_tool(checkbox_id[len("doctor-mcp-tool-") :], event.value)

    # -- actions ----------------------------------------------------------------

    async def _act(self, action: str, name: str) -> None:
        mcp = self._mcp
        try:
            if action == "approve":
                await mcp.approve(name)
            elif action == "signin":
                self._status(f"Opening the browser to sign in to {name}…")
                await mcp.sign_in(name, open_browser=system_browser)
            elif action == "signout":
                await mcp.sign_out(name)
            elif action == "reconnect":
                await mcp.reconnect(name)
            elif action == "toggle":
                server = next(s for s in mcp.snapshot() if s["name"] == name)
                own = server["source"] == "config"
                on = server["spec"]["enabled"] if own else server["status"] != "needs_approval"
                await mcp.set_enabled(name, not on)
            elif action == "remove":
                await mcp.remove(name)
            else:
                return
        except (ValueError, StopIteration) as exc:
            self._status(f"✗ {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - reported, never raised into the UI
            self._status(f"✗ {name}: {exc}")
            return
        self._status(f"✓ {name}: {action} done")
        self._changed()
        await self.refresh_servers(force=True)

    def _form(self, field: str) -> Input:
        return self.query_one(f"#doctor-mcp-{field}", Input)

    def _fill_form(self, name: str) -> None:
        server = next((s for s in self._mcp.snapshot(secrets=True) if s["name"] == name), None)
        if server is None:
            return
        spec = server["spec"]
        if spec["transport"] == "stdio":
            target = _command_line([spec["command"], *spec["args"]])
            pairs = spec["env"]
        else:
            target = spec["url"]
            pairs = spec["headers"]
        self._form("name").value = name
        self._form("target").value = target
        self._form("extras").value = "; ".join(f"{key}={value}" for key, value in pairs.items())
        self.query_one("#doctor-mcp-sse", Checkbox).value = spec["transport"] == "sse"
        self.query_one("#doctor-mcp-trust", Checkbox).value = bool(spec["trust"])
        self._editing = name
        self.query_one("#doctor-mcp-add", Button).label = "Save"
        self.query_one("#doctor-mcp-form-title", Static).update(f"Edit {name}")
        self._status(f"Editing {name}: change the fields and press Save.")

    def _clear_form(self) -> None:
        for field in ("name", "target", "extras"):
            self._form(field).value = ""
        self.query_one("#doctor-mcp-sse", Checkbox).value = False
        self.query_one("#doctor-mcp-trust", Checkbox).value = False
        self._editing = ""
        self.query_one("#doctor-mcp-add", Button).label = "Add"
        self.query_one("#doctor-mcp-form-title", Static).update("Add a server")

    async def _save_form(self) -> None:
        name = self._form("name").value.strip()
        target = self._form("target").value.strip()
        if not name or not target:
            self._status("✗ Give the server a name and a command or URL.")
            return
        declaration = quick_declaration(
            target,
            self._form("extras").value,
            legacy_sse=self.query_one("#doctor-mcp-sse", Checkbox).value,
        )
        declaration["name"] = name
        declaration["trust"] = self.query_one("#doctor-mcp-trust", Checkbox).value
        try:
            await self._mcp.upsert(declaration, previous=self._editing)
        except ValueError as exc:
            self._status(f"✗ {exc}")
            return
        self._status(f"✓ Saved {name}; it is starting now.")
        self._clear_form()
        self._changed()
        await self.refresh_servers(force=True)

    async def _import(self) -> None:
        servers, problem = parse_import(self.query_one("#doctor-mcp-json", TextArea).text)
        if problem:
            self._status(f"✗ {problem}")
            return
        added: list[str] = []
        for name, declaration in servers:
            try:
                await self._mcp.upsert({**declaration, "name": name})
            except ValueError as exc:
                self._status(f"✗ {name}: {exc}")
                return
            added.append(name)
        self.query_one("#doctor-mcp-json", TextArea).text = ""
        self._status(f"✓ Added {', '.join(added)}; starting now.")
        self._changed()
        await self.refresh_servers(force=True)

    def _apply_enabled(self, value: bool) -> None:
        config = self._config
        if config.mcp_enabled == value:
            return
        try:
            validated = persist_config_updates(
                config, {"mcp_enabled": value}, explicit_path=self._config_path
            )
        except Exception as exc:  # noqa: BLE001
            self._status(f"✗ Not saved: {exc}")
            return
        config.mcp_enabled = validated.mcp_enabled
        if value:
            self.run_worker(self._mcp.start(), exclusive=False)
        else:
            self.run_worker(
                self._mcp.close(reason="MCP servers are switched off."), exclusive=False
            )
        self._changed()
        self._status(f"✓ MCP servers {'on' if value else 'off'} (applied now)")

    def _apply_tool(self, name: str, enabled: bool) -> None:
        disabled = self._disabled()
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        config = self._config
        try:
            validated = persist_config_updates(
                config, {"disabled_tools": sorted(disabled)}, explicit_path=self._config_path
            )
        except Exception as exc:  # noqa: BLE001
            self._status(f"✗ Not saved: {exc}")
            return
        config.disabled_tools = validated.disabled_tools
        set_disabled = getattr(self._registry, "set_disabled", None)
        if callable(set_disabled):
            set_disabled(config.disabled_tools)
        self._changed()
        self._status(f"✓ {name} {'on' if enabled else 'off'} (applied from the next model call)")

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()


def _command_line(words: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(words)
    import shlex

    return shlex.join(words)
