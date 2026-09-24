"""Every MCP server of one session: connecting them, their tools in the registry,
reconnecting when one drops, and what the settings panel shows about them.

Servers connect in the background as the session starts, so a slow ``npx``
download never holds up the first message; their tools join the registry the
moment each one is ready. A server with many tools waits in a deferred group
(``mcp:<name>``) that the model loads when it needs it, like the browser group.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from code_ai.core.errors import CancellationError, ToolExecutionError
from code_ai.mcp.client import ClientHandlers, McpClient, ServerIdentity
from code_ai.mcp.config import (
    McpServerConfig,
    expand,
    load_servers,
    missing_variables,
    parse_server,
    server_name,
)
from code_ai.mcp.jsonrpc import McpAuthRequired, McpCancelled, McpError
from code_ai.mcp.oauth import ClientFactory, OAuthError, OAuthFlow, OpenBrowser, TokenStore
from code_ai.mcp.tools import (
    PREFIX,
    GetMcpPromptTool,
    ListMcpResourcesTool,
    McpTool,
    ReadMcpResourceTool,
    qualified_name,
)
from code_ai.mcp.transports import (
    SseTransport,
    StdioTransport,
    StreamableHttpTransport,
    Transport,
    child_environment,
)
from code_ai.tools.groups import ToolGroup, register_group, unregister_group

if TYPE_CHECKING:
    from code_ai.config.models import AppConfig
    from code_ai.events.bus import AsyncEventBus
    from code_ai.tools.registry import ToolRegistry

STATUS_DISABLED = "disabled"
STATUS_NEEDS_APPROVAL = "needs_approval"
STATUS_CONNECTING = "connecting"
STATUS_CONNECTED = "connected"
STATUS_FAILED = "failed"
STATUS_NEEDS_AUTH = "needs_auth"
STATUS_INVALID = "invalid"

# "auto" puts a server's tools in a group once there are more than this.
_DEFER_ABOVE = 12
_RETRIES = 3
# Seconds before the next try, times the tries so far.
_BACKOFF_S = 2.0
# A connection that lasted this long resets the retry count when it drops.
_STABLE_S = 60.0
_LOG_TAIL = 30
_MAX_INSTRUCTIONS = 800
_SIGN_IN_HINT = "The server needs you to sign in."

Persist = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class ServerState:
    config: McpServerConfig
    status: str = STATUS_DISABLED
    error: str = ""
    client: McpClient | None = None
    identity: ServerIdentity | None = None
    tools: list[McpTool] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    # Set while the tools wait in a deferred group, instead of in every request.
    group_entry: ToolGroup | None = None
    task: asyncio.Task[None] | None = None
    stopping: bool = False
    missing_env: list[str] = field(default_factory=list)
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=_LOG_TAIL))
    # The last connection's stderr (stdio) or transport notes, kept after it is gone.
    diagnostics: str = ""
    # Where the server's 401 said its protected-resource metadata is.
    auth_hint: str = ""

    @property
    def group(self) -> str:
        return f"mcp:{self.config.name}".lower()

    @property
    def deferred(self) -> bool:
        return self.group_entry is not None


class McpManager:
    def __init__(
        self,
        *,
        config: AppConfig,
        registry: ToolRegistry,
        event_bus: AsyncEventBus,
        client_version: str,
        persist: Persist | None = None,
        auth_store: TokenStore | None = None,
        oauth_client: ClientFactory | None = None,
    ) -> None:
        self._config = config
        self._auth_store = auth_store or TokenStore()
        # The HTTP client sign-in uses; tests hand in one that never leaves the process.
        self._oauth_client = oauth_client
        self._registry = registry
        self._bus = event_bus
        self._client_version = client_version
        self._persist = persist
        self._servers: dict[str, ServerState] = {}
        self._meta: dict[str, Any] = {
            "list_mcp_resources": ListMcpResourcesTool(self),
            "read_mcp_resource": ReadMcpResourceTool(self),
            "get_mcp_prompt": GetMcpPromptTool(self),
        }
        self._closed = False
        # Handed in by the session once it can answer for a person or a model.
        self.sampling_handler: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None
        # An ElicitationBroker: called for a server's question, answered by the UI.
        self.elicitation: Any = None

    @property
    def workspace(self) -> Path:
        return Path(self._config.workspace)

    @property
    def servers(self) -> dict[str, ServerState]:
        return self._servers

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        """Read every declaration and start the servers that may run, in the background."""

        self._closed = False
        if not self._config.mcp_enabled:
            return
        for spec in load_servers(self._config.mcp_servers, self.workspace):
            previous = self._servers.get(spec.name)
            if previous is not None:
                await self._stop(previous)
            state = ServerState(config=spec)
            self._servers[spec.name] = state
            await self._schedule(state)

    async def close(self, *, reason: str = "") -> None:
        """Stop every server. With a ``reason`` (MCP switched off) they stay listed as off."""

        self._closed = True
        if self.elicitation is not None:
            self.elicitation.close()
        await asyncio.gather(
            *(self._stop(state) for state in self._servers.values()), return_exceptions=True
        )
        if reason:
            for state in self._servers.values():
                await self._set(state, STATUS_DISABLED, reason)

    async def wait_ready(self, timeout: float) -> None:
        """Wait until every server has settled one way or the other (tests, CLI)."""

        pending = [state for state in self._servers.values() if state.status == STATUS_CONNECTING]
        deadline = asyncio.get_running_loop().time() + timeout
        while pending and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
            pending = [s for s in pending if s.status == STATUS_CONNECTING]

    async def _schedule(self, state: ServerState) -> None:
        spec = state.config
        if spec.problem:
            await self._set(state, STATUS_INVALID, spec.problem)
        elif self._closed or not self._config.mcp_enabled:
            await self._set(state, STATUS_DISABLED, "MCP servers are switched off.")
        elif not spec.enabled:
            await self._set(state, STATUS_DISABLED)
        elif spec.from_workspace and not self._approved(spec):
            await self._set(
                state,
                STATUS_NEEDS_APPROVAL,
                f"Declared in {spec.source_path or 'the workspace'}; approve it to start it.",
            )
        else:
            state.stopping = False
            await self._set(state, STATUS_CONNECTING)
            state.task = asyncio.create_task(self._run(state))

    async def _run(self, state: ServerState) -> None:
        """Connect, keep it connected while the session lives, give up after a few tries."""

        loop = asyncio.get_running_loop()
        failures = 0
        while not self._closed and not state.stopping:
            try:
                await self._connect(state)
            except McpAuthRequired as exc:
                state.auth_hint = exc.resource_metadata or state.auth_hint
                await self._set(state, STATUS_NEEDS_AUTH, _SIGN_IN_HINT)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - becomes the server's status
                failures += 1
                await self._set(state, STATUS_FAILED, self._describe(state, exc))
                if failures >= _RETRIES:
                    return
                await asyncio.sleep(_BACKOFF_S * failures)
                if not self._closed and not state.stopping:
                    await self._set(state, STATUS_CONNECTING)
                continue
            connected_at = loop.time()
            client = state.client
            assert client is not None
            reason = await client.wait_closed()
            state.diagnostics = client.transport.diagnostics()
            with contextlib.suppress(Exception):
                await client.close()
            state.client = None
            self._unregister(state)
            if self._closed or state.stopping:
                return
            # A server that dies right after every handshake is failing, not flaky.
            if loop.time() - connected_at > _STABLE_S:
                failures = 0
            failures += 1
            await self._set(state, STATUS_FAILED, self._describe(state, reason))
            if failures >= _RETRIES:
                return
            await asyncio.sleep(_BACKOFF_S * failures)
            await self._set(state, STATUS_CONNECTING)

    async def _connect(self, state: ServerState) -> None:
        spec = state.config
        transport = self._transport_for(state)
        client = McpClient(
            transport,
            client_name="code-ai",
            client_version=self._client_version,
            handlers=ClientHandlers(
                roots=self._roots,
                sampling=self._bind(self.sampling_handler, spec.name),
                elicitation=self._bind(self.elicitation, spec.name),
                notification=lambda method, params: self._on_notification(state, method, params),
            ),
            request_timeout=spec.timeout_s,
        )
        state.client = client
        state.diagnostics = ""
        try:
            state.identity = await client.connect(timeout=spec.startup_timeout_s)
            await self._load_tools(state)
            await self._load_prompts(state)
        except BaseException:
            state.diagnostics = transport.diagnostics()
            with contextlib.suppress(Exception):
                await client.close()
            state.client = None
            self._unregister(state)
            raise
        await self._set(state, STATUS_CONNECTED)

    async def _stop(self, state: ServerState) -> None:
        state.stopping = True
        task = state.task
        state.task = None
        if state.client is not None:
            with contextlib.suppress(Exception):
                await state.client.close()
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        self._unregister(state)
        state.client = None

    def _transport_for(self, state: ServerState) -> Transport:
        spec = state.config
        workspace = self.workspace
        env = {key: expand(value, workspace=workspace) for key, value in spec.env.items()}
        state.missing_env = missing_variables(
            [spec.command, *spec.args, *spec.env.values(), spec.url, *spec.headers.values()]
        )
        if spec.transport == "stdio":
            return StdioTransport(
                command=expand(spec.command, workspace=workspace, env=env),
                args=[expand(arg, workspace=workspace, env=env) for arg in spec.args],
                env=child_environment(env, ssl_verification=self._config.ssl_verification),
                cwd=expand(spec.cwd, workspace=workspace, env=env) or workspace,
            )
        headers = {key: expand(value, workspace=workspace) for key, value in spec.headers.items()}
        url = expand(spec.url, workspace=workspace)
        options: dict[str, Any] = {
            "headers": headers,
            "verify": self._config.ssl_verification,
            "timeout": spec.timeout_s,
            # A server given its own Authorization header is not signed in to.
            "auth": None if _has_authorization(headers) else self._auth_header(state, url),
        }
        if spec.transport == "sse":
            return SseTransport(url, **options)
        return StreamableHttpTransport(url, **options)

    def _describe(self, state: ServerState, failure: BaseException | str) -> str:
        text = failure if isinstance(failure, str) else (str(failure) or type(failure).__name__)
        if state.missing_env:
            text += f" (not set: {', '.join(state.missing_env)})"
        tail = _diagnostics(state)
        if tail:
            text += "\n" + "\n".join(tail.splitlines()[-6:])
        return text

    # -- tools ----------------------------------------------------------------

    async def _load_tools(self, state: ServerState) -> None:
        client = state.client
        assert client is not None and state.identity is not None
        specs = await client.list_tools() if state.identity.supports("tools") else []
        self._unregister(state)
        tools: list[McpTool] = []
        for spec in specs:
            if not spec.get("name"):
                continue
            tool = McpTool(
                manager=self, server=state.config.name, spec=spec, trusted=state.config.trust
            )
            if self._registry.is_registered(tool.name):
                continue
            self._registry.register(tool)
            tools.append(tool)
        state.tools = tools
        defer = state.config.defer
        if tools and (defer == "always" or (defer == "auto" and len(tools) > _DEFER_ABOVE)):
            state.group_entry = register_group(
                ToolGroup(
                    name=state.group,
                    summary=self._group_summary(state),
                    tools=frozenset(tool.name for tool in tools),
                )
            )
        self._sync_meta_tools()

    async def _load_prompts(self, state: ServerState) -> None:
        client = state.client
        assert client is not None and state.identity is not None
        state.prompts = []
        if state.identity.supports("prompts"):
            with contextlib.suppress(McpError):
                state.prompts = await client.list_prompts()
        self._sync_meta_tools()

    def _unregister(self, state: ServerState) -> None:
        for tool in state.tools:
            if self._registry.tool(tool.name) is tool:
                self._registry.unregister(tool.name)
        state.tools = []
        if state.group_entry is not None:
            unregister_group(state.group, only=state.group_entry)
            state.group_entry = None
        self._sync_meta_tools()

    def _sync_meta_tools(self) -> None:
        """The resource and prompt tools exist only while some server has resources or prompts."""

        live = [
            state
            for state in self._servers.values()
            if state.client is not None and not state.client.closed and state.identity is not None
        ]
        resources = any(s.identity is not None and s.identity.supports("resources") for s in live)
        wanted = {
            "list_mcp_resources": resources,
            "read_mcp_resource": resources,
            "get_mcp_prompt": any(s.prompts for s in live),
        }
        for name, want in wanted.items():
            present = self._registry.tool(name) is self._meta[name]
            if want and not present and not self._registry.is_registered(name):
                self._registry.register(self._meta[name])
            elif not want and present:
                self._registry.unregister(name)

    def _group_summary(self, state: ServerState) -> str:
        identity = state.identity
        title = (identity.title or identity.name) if identity else ""
        first_line = (identity.instructions.strip().splitlines() or [""])[0] if identity else ""
        names = ", ".join(tool.remote_name for tool in state.tools[:6])
        more = f" and {len(state.tools) - 6} more" if len(state.tools) > 6 else ""
        lead = first_line[:160] or (title or state.config.name)
        return f"{lead} - tools: {names}{more}"

    # -- calls ----------------------------------------------------------------

    def _connected(self, server: str) -> ServerState:
        state = self._servers.get(server)
        if state is None:
            raise ToolExecutionError(f"There is no MCP server named {server!r}.")
        if state.client is None or state.client.closed:
            detail = f": {state.error.splitlines()[0]}" if state.error else ""
            raise ToolExecutionError(f"MCP server {server!r} is {state.status}{detail}")
        return state

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        cancel: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        state = self._connected(server)
        client = state.client
        assert client is not None

        async def progress(done: float, total: float | None, message: str) -> None:
            await self._bus.emit(
                "mcp.tool.progress",
                {
                    "name": qualified_name(server, tool),
                    "server": server,
                    "tool": tool,
                    "progress": done,
                    "total": total,
                    "message": message,
                },
                source="mcp",
            )

        try:
            return await client.call_tool(
                tool,
                arguments,
                timeout=state.config.tool_timeout_s,
                progress=progress,
                cancel=cancel,
            )
        except McpCancelled:
            raise CancellationError("The MCP call was cancelled.") from None
        except McpAuthRequired as exc:
            # The token expired and could not be renewed: the server is back to
            # waiting for a sign-in, and the model is told why the call failed.
            state.auth_hint = exc.resource_metadata or state.auth_hint
            await self._stop(state)
            await self._set(state, STATUS_NEEDS_AUTH, _SIGN_IN_HINT)
            raise ToolExecutionError(
                f"MCP server {server!r} needs the user to sign in again (Settings > MCP servers)."
            ) from exc
        except McpError as exc:
            raise ToolExecutionError(f"MCP {server}/{tool}: {exc.message}") from exc

    async def list_resources(self, server: str | None = None) -> list[dict[str, Any]]:
        listing: list[dict[str, Any]] = []
        for name, state in self._servers.items():
            if server and name != server:
                continue
            client = state.client
            if client is None or client.closed or state.identity is None:
                continue
            if not state.identity.supports("resources"):
                continue
            entry: dict[str, Any] = {"server": name}
            try:
                entry["resources"] = [
                    _trim_resource(item) for item in await client.list_resources()
                ]
                with contextlib.suppress(McpError):
                    entry["templates"] = [
                        _trim_resource(item) for item in await client.list_resource_templates()
                    ]
            except McpError as exc:
                entry["error"] = exc.message
            listing.append(entry)
        if server and not listing:
            self._connected(server)
            raise ToolExecutionError(f"MCP server {server!r} offers no resources.")
        return listing

    async def read_resource(self, server: str, uri: str) -> list[dict[str, Any]]:
        client = self._connected(server).client
        assert client is not None
        try:
            return await client.read_resource(uri)
        except McpError as exc:
            raise ToolExecutionError(f"MCP {server}: {exc.message}") from exc

    async def get_prompt(self, server: str, name: str, arguments: dict[str, str]) -> dict[str, Any]:
        client = self._connected(server).client
        assert client is not None
        try:
            return await client.get_prompt(name, arguments)
        except McpError as exc:
            raise ToolExecutionError(f"MCP {server}: {exc.message}") from exc

    # -- signing in ---------------------------------------------------------------

    def _flow(self, state: ServerState, url: str) -> OAuthFlow:
        oauth = state.config.oauth
        return OAuthFlow(
            server_url=url,
            store=self._auth_store,
            verify=self._config.ssl_verification,
            resource_metadata=state.auth_hint,
            client_id=oauth.get("client_id", ""),
            client_secret=oauth.get("client_secret", ""),
            scopes=tuple(oauth.get("scopes", "").split()),
            client_factory=self._oauth_client,
        )

    def _auth_header(self, state: ServerState, url: str) -> Callable[[], Awaitable[str]]:
        """The Authorization value a request carries: the stored token, renewed when due."""

        async def header() -> str:
            tokens = self._auth_store.tokens(url)
            if tokens is None:
                return ""
            if tokens.expired():
                try:
                    tokens = await self._flow(state, url).refresh(tokens)
                except OAuthError:
                    # A refusal comes back as a 401 and asks for a new sign-in.
                    self._auth_store.forget(url)
                    return ""
            return tokens.header()

        return header

    def _remote_url(self, state: ServerState) -> str:
        return expand(state.config.url, workspace=self.workspace)

    def _signed_in(self, state: ServerState) -> bool:
        if state.config.transport == "stdio":
            return False
        return self._auth_store.tokens(self._remote_url(state)) is not None

    async def sign_in(
        self, name: str, *, open_browser: OpenBrowser | None = None
    ) -> dict[str, Any]:
        """Run the OAuth sign-in for a remote server, then connect with the token.

        The page to visit goes out as ``mcp.auth.url`` whatever ``open_browser``
        does, so a front end that opens links its own way (VS Code) can.
        """

        state = self._require(name)
        if state.config.transport == "stdio":
            raise ValueError(f"{name} runs on this machine; there is nothing to sign in to.")
        url = self._remote_url(state)
        await self._set(state, STATUS_NEEDS_AUTH, "Finish signing in in the browser.")

        async def announce(page: str) -> None:
            await self._bus.emit("mcp.auth.url", {"server": name, "url": page}, source="mcp")
            if open_browser is not None:
                with contextlib.suppress(Exception):
                    await open_browser(page)

        try:
            await self._flow(state, url).authorize(announce)
        except OAuthError as exc:
            await self._set(state, STATUS_NEEDS_AUTH, str(exc))
            raise ValueError(str(exc)) from exc
        return await self.reconnect(name)

    async def sign_out(self, name: str) -> dict[str, Any]:
        state = self._require(name)
        if state.config.transport != "stdio":
            self._auth_store.forget(self._remote_url(state))
        return await self.reconnect(name)

    # -- what the server may ask of us ------------------------------------------

    async def _roots(self) -> list[dict[str, Any]]:
        return [{"uri": self.workspace.resolve().as_uri(), "name": self.workspace.name}]

    def _bind(
        self, handler: Callable[[str, dict[str, Any]], Awaitable[Any]] | None, server: str
    ) -> Callable[[dict[str, Any]], Awaitable[Any]] | None:
        if handler is None:
            return None

        async def bound(params: dict[str, Any]) -> Any:
            return await handler(server, params)

        return bound

    async def _on_notification(
        self, state: ServerState, method: str, params: dict[str, Any]
    ) -> None:
        if method == "notifications/tools/list_changed" and state.client is not None:
            with contextlib.suppress(Exception):
                await self._load_tools(state)
                await self._emit(state)
        elif method == "notifications/prompts/list_changed" and state.client is not None:
            with contextlib.suppress(Exception):
                await self._load_prompts(state)
                await self._emit(state)
        elif method == "notifications/message":
            level = str(params.get("level") or "info")
            data = params.get("data")
            text = data if isinstance(data, str) else str(data)
            state.logs.append(f"[{level}] {text[:400]}")
            await self._bus.emit(
                "mcp.server.log",
                {"server": state.config.name, "level": level, "message": text[:1000]},
                source="mcp",
            )

    # -- management -------------------------------------------------------------

    async def reconnect(self, name: str) -> dict[str, Any]:
        state = self._require(name)
        await self._stop(state)
        await self._schedule(state)
        return self.describe(state)

    async def approve(self, name: str) -> dict[str, Any]:
        """Let a server declared by the workspace start, now and in later sessions."""

        state = self._require(name)
        fingerprint = state.config.fingerprint(self.workspace)
        approved = [item for item in self._config.mcp_approved if item != fingerprint]
        approved.append(fingerprint)
        await self._save({"mcp_approved": approved})
        await self._stop(state)
        await self._schedule(state)
        return self.describe(state)

    async def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        state = self._require(name)
        spec = state.config
        if spec.from_workspace:
            # A workspace server is switched by its approval, not by editing the
            # repository's file.
            fingerprint = spec.fingerprint(self.workspace)
            approved = [item for item in self._config.mcp_approved if item != fingerprint]
            if enabled:
                approved.append(fingerprint)
            await self._save({"mcp_approved": approved})
        else:
            spec.enabled = enabled
            servers = dict(self._config.mcp_servers)
            servers[spec.name] = spec.to_config()
            await self._save({"mcp_servers": servers})
        await self._stop(state)
        await self._schedule(state)
        return self.describe(state)

    async def upsert(self, raw: dict[str, Any], *, previous: str = "") -> dict[str, Any]:
        """Add or edit a server in the user's config, then (re)start it."""

        name = server_name(str(raw.get("name") or ""))
        spec = parse_server(name, _declaration(raw))
        if spec.problem:
            raise ValueError(spec.problem)
        servers = dict(self._config.mcp_servers)
        if previous and previous != name:
            servers.pop(previous, None)
            old = self._servers.pop(previous, None)
            if old is not None:
                await self._stop(old)
        servers[name] = spec.to_config()
        await self._save({"mcp_servers": servers})
        existing = self._servers.get(name)
        if existing is not None:
            await self._stop(existing)
        state = ServerState(config=spec)
        self._servers[name] = state
        await self._schedule(state)
        return self.describe(state)

    async def remove(self, name: str) -> None:
        state = self._require(name)
        if state.config.from_workspace:
            raise ValueError(
                f"{name} is declared in {state.config.source_path}; remove it from that file."
            )
        servers = dict(self._config.mcp_servers)
        servers.pop(name, None)
        await self._save({"mcp_servers": servers})
        await self._stop(state)
        self._servers.pop(name, None)
        await self._bus.emit("mcp.server.removed", {"server": name}, source="mcp")

    def snapshot(self, *, secrets: bool = False) -> list[dict[str, Any]]:
        return [self.describe(state, secrets=secrets) for state in self._servers.values()]

    def describe(self, state: ServerState, *, secrets: bool = False) -> dict[str, Any]:
        """A server as the panel shows it. ``secrets`` keeps env and header values,
        which only the editor asks for; events go out masked."""

        identity = state.identity
        disabled = set(self._registry.disabled_names())
        spec = state.config.payload()
        if not secrets:
            for key in ("env", "headers", "oauth"):
                spec[key] = {name: "***" if value else "" for name, value in spec[key].items()}
        return {
            "name": state.config.name,
            "status": state.status,
            "error": state.error,
            "transport": state.config.transport,
            "source": state.config.source,
            "source_path": state.config.source_path,
            "deferred": state.deferred,
            "group": state.group if state.deferred else "",
            "server": (
                {
                    "name": identity.name,
                    "title": identity.title,
                    "version": identity.version,
                    "protocol": identity.protocol_version,
                    "instructions": identity.instructions[:_MAX_INSTRUCTIONS],
                }
                if identity
                else None
            ),
            "tools": [
                {
                    "name": tool.name,
                    "remote": tool.remote_name,
                    "title": tool.title,
                    "description": tool.description[:240],
                    "read_only": tool.read_only,
                    "enabled": tool.name not in disabled,
                }
                for tool in state.tools
            ],
            "prompts": [
                {
                    "name": str(prompt.get("name") or ""),
                    "description": str(prompt.get("description") or "")[:240],
                    "arguments": [
                        {
                            "name": str(argument.get("name") or ""),
                            "required": bool(argument.get("required")),
                        }
                        for argument in prompt.get("arguments") or []
                        if isinstance(argument, dict)
                    ],
                }
                for prompt in state.prompts
            ],
            "resources": bool(identity and identity.supports("resources")),
            "remote": state.config.transport != "stdio",
            "signed_in": self._signed_in(state),
            "missing_env": list(state.missing_env),
            "logs": list(state.logs)[-10:],
            "diagnostics": _diagnostics(state)[-2000:],
            "spec": spec,
        }

    def prompt_catalog(self) -> str:
        """What the system prompt says about the connected servers. Stable per connection."""

        lines: list[str] = []
        for state in self._servers.values():
            identity = state.identity
            if state.status != STATUS_CONNECTED or identity is None:
                continue
            name = state.config.name
            label = identity.title or identity.name or name
            if state.deferred:
                how = f"tools in the group {state.group}"
            elif state.tools:
                how = f"tools named {PREFIX}{name}__<tool>"
            else:
                how = "no tools"
            extras = [
                kind
                for kind, present in (
                    ("resources", identity.supports("resources")),
                    ("prompts", bool(state.prompts)),
                )
                if present
            ]
            line = f"- {name} ({label}): {how}" + (
                f", plus {' and '.join(extras)}" if extras else ""
            )
            instructions = " ".join(identity.instructions.split())[:_MAX_INSTRUCTIONS]
            if instructions:
                line += f". The server's notes: {instructions}"
            lines.append(line + ".")
        return "\n".join(lines)

    def _require(self, name: str) -> ServerState:
        state = self._servers.get(name)
        if state is None:
            raise ValueError(f"There is no MCP server named {name!r}.")
        return state

    def _approved(self, spec: McpServerConfig) -> bool:
        return spec.fingerprint(self.workspace) in set(self._config.mcp_approved)

    async def _save(self, changes: dict[str, Any]) -> None:
        if self._persist is not None:
            await self._persist(changes)
        for key, value in changes.items():
            setattr(self._config, key, value)

    async def _set(self, state: ServerState, status: str, error: str = "") -> None:
        state.status = status
        state.error = error
        await self._emit(state)

    async def _emit(self, state: ServerState) -> None:
        await self._bus.emit("mcp.server.status", self.describe(state), source="mcp")


def _declaration(raw: dict[str, Any]) -> dict[str, Any]:
    """The panel's form, as the declaration shape ``parse_server`` reads."""

    transport = str(raw.get("transport") or raw.get("type") or "").strip().lower()
    entry: dict[str, Any] = {key: raw[key] for key in raw if key not in ("name", "transport")}
    if transport:
        entry["type"] = transport
    return entry


def _diagnostics(state: ServerState) -> str:
    client = state.client
    return client.transport.diagnostics() if client is not None else state.diagnostics


def _trim_resource(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("uri", "uriTemplate", "name", "title", "description", "mimeType")
    return {key: item[key] for key in keys if item.get(key)}


def _has_authorization(headers: dict[str, str]) -> bool:
    return any(key.lower() == "authorization" and value for key, value in headers.items())


async def system_browser(url: str) -> None:
    """Open ``url`` in the machine's browser (the terminal's way; VS Code opens its own)."""

    import webbrowser

    await asyncio.to_thread(webbrowser.open, url)
