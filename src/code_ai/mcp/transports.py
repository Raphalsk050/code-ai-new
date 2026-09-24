"""How MCP messages travel: a child process's pipes, or HTTP.

Every transport has the same shape: ``start`` with two callbacks (a message
arrived, the connection ended), ``send`` a JSON-RPC message, ``close``. The
client above them never knows which one it is talking through.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx

from code_ai.mcp import jsonrpc
from code_ai.mcp.jsonrpc import McpAuthRequired, McpConnectionError
from code_ai.mcp.sse import SseParser

OnMessage = Callable[[Any], Awaitable[None]]
OnClose = Callable[[str], Awaitable[None]]
# Returns the Authorization header value to send, or "" for none.
AuthHeader = Callable[[], Awaitable[str]]

# A tool result can carry a whole file; asyncio's default 64 KiB line limit
# would end the connection on the first large one.
_STDIO_LINE_LIMIT = 32 * 1024 * 1024
_STDERR_TAIL = 40

# What a server process inherits from ours, as the reference SDK does: enough
# to find its interpreter, its home and the network, nothing of our secrets.
_INHERITED_POSIX = ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER", "LANG", "TMPDIR")
_INHERITED_WINDOWS = (
    "APPDATA",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
)
_INHERITED_ANYWHERE = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "LC_ALL",
    "LC_CTYPE",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "NVM_DIR",
    "NPM_CONFIG_REGISTRY",
    "npm_config_registry",
    "PIP_INDEX_URL",
    "UV_INDEX_URL",
)


class Transport(Protocol):
    async def start(self, on_message: OnMessage, on_close: OnClose) -> None: ...

    async def send(self, message: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...

    def set_protocol_version(self, version: str) -> None: ...

    def open_listener(self) -> None: ...

    def diagnostics(self) -> str: ...


def child_environment(extra: dict[str, str], *, ssl_verification: bool) -> dict[str, str]:
    """The environment a stdio server starts with."""

    names = _INHERITED_WINDOWS if os.name == "nt" else _INHERITED_POSIX
    env: dict[str, str] = {}
    for key in (*names, *_INHERITED_ANYWHERE):
        value = os.environ.get(key)
        # A bash function export ("() { ...") is not a value a server can use.
        if value is not None and not value.startswith("()"):
            env[key] = value
    if not ssl_verification:
        # The network re-signs TLS, and the package managers a server starts
        # with (npx, uvx, pip) have to accept that the way this process does.
        env.setdefault("NODE_TLS_REJECT_UNAUTHORIZED", "0")
        env.setdefault("npm_config_strict_ssl", "false")
        env.setdefault("PIP_TRUSTED_HOST", "pypi.org files.pythonhosted.org")
    env.update(extra)
    return env


def resolve_command(command: str, env: dict[str, str]) -> str:
    """The program to run. ``npx`` on Windows is ``npx.cmd``, which only PATH search finds."""

    expanded = os.path.expanduser(command)
    if os.path.dirname(expanded):
        return expanded
    found = shutil.which(expanded, path=env.get("PATH") or env.get("Path"))
    if found is None:
        raise McpConnectionError(
            f"{command!r} was not found on PATH. Install it, or give the full path."
        )
    return found


class StdioTransport:
    """A server that is a child process, speaking one JSON message per line."""

    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        env: dict[str, str],
        cwd: Path | str,
    ) -> None:
        self._command = command
        self._args = list(args)
        self._env = env
        self._cwd = str(cwd)
        self._process: asyncio.subprocess.Process | None = None
        self._stderr: deque[str] = deque(maxlen=_STDERR_TAIL)
        self._tasks: list[asyncio.Task[None]] = []
        self._write_lock = asyncio.Lock()
        self._closing = False
        self._on_message: OnMessage | None = None
        self._on_close: OnClose | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    async def start(self, on_message: OnMessage, on_close: OnClose) -> None:
        self._on_message = on_message
        self._on_close = on_close
        executable = resolve_command(self._command, self._env)
        extra: dict[str, Any] = {}
        if os.name == "nt":
            # No console window flashing up for every server we start.
            extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            # Its own process group, so closing it takes its children too.
            extra["start_new_session"] = True
        try:
            self._process = await asyncio.create_subprocess_exec(
                executable,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=self._cwd,
                limit=_STDIO_LINE_LIMIT,
                **extra,
            )
        except OSError as exc:
            raise McpConnectionError(f"Could not start {self._command!r}: {exc}") from exc
        self._tasks = [
            asyncio.create_task(self._pump_stdout()),
            asyncio.create_task(self._pump_stderr()),
        ]

    async def send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise McpConnectionError("The server process is not running.")
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            try:
                process.stdin.write(line.encode("utf-8"))
                await process.stdin.drain()
            except (ConnectionError, RuntimeError) as exc:
                raise McpConnectionError(f"The server stopped reading: {exc}") from exc

    def set_protocol_version(self, version: str) -> None:
        return None

    def open_listener(self) -> None:
        return None

    def diagnostics(self) -> str:
        return "\n".join(self._stderr)

    async def close(self) -> None:
        self._closing = True
        process = self._process
        if process is not None and process.returncode is None:
            if process.stdin is not None:
                with contextlib.suppress(Exception):
                    process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                await _end_process_tree(process)
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(BaseException):
                await task

    async def _pump_stdout(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        reason = "the server closed its output"
        try:
            while True:
                try:
                    line = await process.stdout.readline()
                except ValueError:
                    reason = "the server sent a message larger than 32 MB"
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    # Some servers print a banner on stdout; keep it for the
                    # status line instead of failing the connection over it.
                    self._stderr.append(f"(stdout) {text[:300]}")
                    continue
                if self._on_message is not None:
                    await self._on_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported through on_close
            reason = f"reading from the server failed: {exc}"
        if self._closing or self._on_close is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=1.0)
        if process.returncode is not None:
            reason = f"the server exited with code {process.returncode}"
        await self._on_close(reason)

    async def _pump_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        with contextlib.suppress(Exception):
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._stderr.append(text[:500])


async def _end_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate, then kill, the server and whatever it started."""

    pid = process.pid
    if os.name == "nt":
        # npx.cmd runs node under cmd.exe; killing the cmd leaves node behind.
        await asyncio.to_thread(
            subprocess.run,
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    else:
        import signal

        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
            return
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=3.0)


class _HttpBase:
    """What both HTTP transports share: the client, headers and error reading."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        verify: bool,
        timeout: float,
        auth: AuthHeader | None = None,
    ) -> None:
        self._url = url
        self._headers = dict(headers)
        self._verify = verify
        self._timeout = timeout
        self._auth = auth
        self._client: httpx.AsyncClient | None = None
        self._protocol_version = ""
        self._on_message: OnMessage | None = None
        self._on_close: OnClose | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._notes: deque[str] = deque(maxlen=_STDERR_TAIL)

    def _open_client(self) -> httpx.AsyncClient:
        # No read timeout: an event stream is quiet for as long as it likes.
        return httpx.AsyncClient(
            verify=self._verify,
            timeout=httpx.Timeout(self._timeout, read=None),
            follow_redirects=True,
        )

    async def _headers_for(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept, **self._headers}
        if self._auth is not None:
            token = await self._auth()
            if token:
                headers["Authorization"] = token
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def set_protocol_version(self, version: str) -> None:
        self._protocol_version = version

    def diagnostics(self) -> str:
        return "\n".join(self._notes)

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _parse(self, raw: str) -> Any:
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except ValueError:
            self._notes.append(f"unreadable message: {raw[:200]}")
            return None

    async def _deliver(self, raw: str) -> None:
        message = self._parse(raw)
        if message is not None and self._on_message is not None:
            await self._on_message(message)

    async def _fail_for(self, response: httpx.Response) -> None:
        """Raise the error an HTTP status stands for; return when it is a success."""

        if response.status_code < 400:
            return
        body = ""
        with contextlib.suppress(Exception):
            body = (await response.aread()).decode("utf-8", errors="replace")[:300]
        if response.status_code == 401:
            challenge = response.headers.get("www-authenticate", "")
            raise McpAuthRequired(
                "The server needs you to sign in.",
                resource_metadata=_challenge_value(challenge, "resource_metadata"),
            )
        detail = f": {body.strip()}" if body.strip() else ""
        raise McpConnectionError(f"HTTP {response.status_code} from {self._url}{detail}")

    async def _close_client(self) -> None:
        self._closing = True
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):
                await task
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None


class StreamableHttpTransport(_HttpBase):
    """MCP over one HTTP endpoint (spec 2025-03-26 and later).

    Every message is a POST. A request's answer comes back as JSON, or as an
    event stream that may first carry the server's own requests and progress.
    The server names a session in ``Mcp-Session-Id``, and every later message
    carries it.
    """

    def __init__(self, url: str, **kwargs: Any) -> None:
        super().__init__(url, **kwargs)
        self._session_id = ""
        self._listening = False

    @property
    def session_id(self) -> str:
        return self._session_id

    async def start(self, on_message: OnMessage, on_close: OnClose) -> None:
        self._on_message = on_message
        self._on_close = on_close
        self._client = self._open_client()

    async def send(self, message: dict[str, Any]) -> None:
        if self._client is None:
            raise McpConnectionError("The transport is closed.")
        headers = await self._headers_for("application/json, text/event-stream")
        headers["Content-Type"] = "application/json"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = self._client.build_request("POST", self._url, json=message, headers=headers)
        try:
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise McpConnectionError(f"Could not reach {self._url}: {exc}") from exc
        streaming = False
        try:
            if response.status_code == 404 and self._session_id:
                self._session_id = ""
                raise McpConnectionError("The server ended the session.")
            await self._fail_for(response)
            session = response.headers.get("mcp-session-id")
            if session:
                self._session_id = session
            if response.status_code == 202 or not jsonrpc.is_request(message):
                return
            kind = response.headers.get("content-type", "").lower()
            if "text/event-stream" in kind:
                streaming = True
                self._spawn(self._consume(response, message.get("id")))
                return
            await self._deliver((await response.aread()).decode("utf-8", errors="replace"))
        finally:
            if not streaming:
                await response.aclose()

    def open_listener(self) -> None:
        """Open the GET stream a server uses to reach us unprompted, if it offers one."""

        if not self._listening and self._client is not None:
            self._listening = True
            self._spawn(self._listen())

    async def close(self) -> None:
        client = self._client
        if client is not None and self._session_id:
            with contextlib.suppress(Exception):
                headers = await self._headers_for("application/json")
                headers["Mcp-Session-Id"] = self._session_id
                await client.request("DELETE", self._url, headers=headers, timeout=3.0)
        await self._close_client()

    async def _consume(self, response: httpx.Response, request_id: Any) -> None:
        answered = False
        parser = SseParser()
        try:
            async for line in response.aiter_lines():
                event = parser.feed(line)
                if event is None or event.event != "message":
                    continue
                message = self._parse(event.data)
                if message is None or self._on_message is None:
                    continue
                if isinstance(message, dict) and jsonrpc.is_response(message):
                    answered = answered or message.get("id") == request_id
                await self._on_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced as the request's failure
            self._notes.append(f"stream broke: {exc}")
        finally:
            with contextlib.suppress(Exception):
                await response.aclose()
        if not answered and request_id is not None and not self._closing and self._on_message:
            # The stream ended without the answer: fail the request now rather
            # than let it sit until its timeout.
            failure = jsonrpc.McpError(
                jsonrpc.INTERNAL_ERROR, "The server closed the stream before answering."
            )
            await self._on_message(jsonrpc.error(request_id, failure))

    async def _listen(self) -> None:
        backoff = 1.0
        for _ in range(5):
            if self._client is None or self._closing:
                return
            headers = await self._headers_for("text/event-stream")
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
            try:
                async with self._client.stream("GET", self._url, headers=headers) as response:
                    if response.status_code in (400, 404, 405):
                        return  # the server does not offer the stream; that is allowed
                    await self._fail_for(response)
                    backoff = 1.0
                    parser = SseParser()
                    async for line in response.aiter_lines():
                        event = parser.feed(line)
                        if event is not None and event.event == "message":
                            await self._deliver(event.data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the stream is optional
                self._notes.append(f"listener: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


class SseTransport(_HttpBase):
    """The HTTP+SSE transport of spec 2024-11-05: a GET stream plus POSTs.

    The stream opens with an ``endpoint`` event naming where to POST; every
    answer then arrives on the stream.
    """

    def __init__(self, url: str, **kwargs: Any) -> None:
        super().__init__(url, **kwargs)
        self._post_url = ""
        self._ready = asyncio.Event()
        self._failure: BaseException | None = None

    async def start(self, on_message: OnMessage, on_close: OnClose) -> None:
        self._on_message = on_message
        self._on_close = on_close
        self._client = self._open_client()
        self._spawn(self._listen())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._timeout)
        except TimeoutError:
            raise McpConnectionError(
                "The server opened its event stream but never said where to send messages."
            ) from None
        if self._failure is not None:
            raise self._failure

    async def send(self, message: dict[str, Any]) -> None:
        if self._client is None or not self._post_url:
            raise McpConnectionError("The transport is not connected.")
        headers = await self._headers_for("application/json")
        try:
            response = await self._client.post(self._post_url, json=message, headers=headers)
        except httpx.HTTPError as exc:
            raise McpConnectionError(f"Could not reach {self._post_url}: {exc}") from exc
        await self._fail_for(response)

    def open_listener(self) -> None:
        return None

    async def close(self) -> None:
        await self._close_client()

    async def _listen(self) -> None:
        assert self._client is not None
        reason = "the server closed its event stream"
        try:
            headers = await self._headers_for("text/event-stream")
            async with self._client.stream("GET", self._url, headers=headers) as response:
                await self._fail_for(response)
                parser = SseParser()
                async for line in response.aiter_lines():
                    event = parser.feed(line)
                    if event is None:
                        continue
                    if event.event == "endpoint":
                        self._post_url = urljoin(self._url, event.data.strip())
                        self._ready.set()
                    elif event.event == "message":
                        await self._deliver(event.data)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - reported through start or on_close
            reason = str(exc) or type(exc).__name__
            if not self._ready.is_set():
                self._failure = (
                    exc if isinstance(exc, McpConnectionError) else McpConnectionError(reason)
                )
                self._ready.set()
                return
        if not self._closing and self._on_close is not None:
            await self._on_close(reason)


def _challenge_value(header: str, key: str) -> str:
    """One parameter out of a ``WWW-Authenticate`` challenge."""

    for part in header.replace("Bearer ", "", 1).split(","):
        name, _, value = part.strip().partition("=")
        if name.strip().lower() == key:
            return value.strip().strip('"')
    return ""
