"""Signing in to a remote MCP server: discovery, registration, PKCE, tokens."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.mcp import manager as manager_module
from code_ai.mcp.manager import McpManager
from code_ai.mcp.oauth import OAuthError, OAuthFlow, OAuthTokens, TokenStore
from code_ai.mcp.transports import StreamableHttpTransport
from code_ai.tools.registry import ToolRegistry

MCP_URL = "https://mcp.example.com/mcp"
METADATA = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"


class _World:
    """An MCP server that wants a token, and the authorization server that issues it."""

    def __init__(self, *, legacy: bool = False, registration: bool = True) -> None:
        self.legacy = legacy
        self.registration = registration
        self.registrations: list[dict[str, Any]] = []
        self.token_forms: list[dict[str, str]] = []
        self.challenge = ""
        self.issued = "token-1"

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        if request.url.host == "mcp.example.com" and path == "/mcp":
            return self._mcp(request)
        if path.startswith("/.well-known/oauth-protected-resource"):
            if self.legacy:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "resource": MCP_URL,
                    "authorization_servers": ["https://auth.example.com"],
                    "scopes_supported": ["repo", "read:org"],
                },
            )
        if path == "/.well-known/oauth-authorization-server":
            if self.legacy:
                return httpx.Response(404)
            body = {
                "issuer": "https://auth.example.com",
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
            }
            if self.registration:
                body["registration_endpoint"] = "https://auth.example.com/register"
            return httpx.Response(200, json=body)
        if path.endswith("/register"):
            self.registrations.append(json.loads(request.content))
            return httpx.Response(201, json={"client_id": f"client-{len(self.registrations)}"})
        if path.endswith("/token"):
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            self.token_forms.append(form)
            if form["grant_type"] == "authorization_code":
                digest = hashlib.sha256(form["code_verifier"].encode()).digest()
                expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                if expected != self.challenge or form["code"] != "the-code":
                    return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": self.issued,
                    "token_type": "bearer",
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(404, text=f"no route for {url}")

    def _mcp(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.issued}":
            return httpx.Response(
                401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{METADATA}"'}
            )
        if request.method == "GET":
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "remote", "version": "1"},
            }
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]}
        else:
            result = {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


class _Browser:
    """Stands in for the user: reads the authorization page and follows the redirect."""

    def __init__(self, world: _World, *, state: str | None = None, error: str = "") -> None:
        self.world = world
        self.state = state
        self.error = error
        self.visited: list[str] = []

    async def __call__(self, url: str) -> None:
        self.visited.append(url)
        query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
        self.world.challenge = query["code_challenge"]
        answer = {"state": self.state if self.state is not None else query["state"]}
        if self.error:
            answer["error"] = self.error
        else:
            answer["code"] = "the-code"

        # A real request to the loopback server, after the flow starts waiting.
        async def follow() -> None:
            async with httpx.AsyncClient(trust_env=False) as http:
                await http.get(f"{query['redirect_uri']}?{urlencode(answer)}")

        asyncio.get_running_loop().create_task(follow())

    def query(self) -> dict[str, str]:
        return {
            key: values[0] for key, values in parse_qs(urlparse(self.visited[-1]).query).items()
        }


def _flow(world: _World, store: TokenStore, **kwargs: Any) -> OAuthFlow:
    return OAuthFlow(
        server_url=MCP_URL, store=store, verify=False, client_factory=world.client, **kwargs
    )


async def test_discovery_follows_the_resource_to_its_authorization_server(tmp_path) -> None:
    server = await _flow(_World(), TokenStore(tmp_path / "auth.json")).discover()

    assert server.issuer == "https://auth.example.com"
    assert server.authorization_endpoint == "https://auth.example.com/authorize"
    assert server.registration_endpoint == "https://auth.example.com/register"


async def test_a_server_without_metadata_gets_the_default_endpoints(tmp_path) -> None:
    server = await _flow(_World(legacy=True), TokenStore(tmp_path / "auth.json")).discover()

    assert server.issuer == "https://mcp.example.com"
    assert server.authorization_endpoint == "https://mcp.example.com/authorize"
    assert server.token_endpoint == "https://mcp.example.com/token"


async def test_signing_in_registers_uses_pkce_and_keeps_the_tokens(tmp_path) -> None:
    world = _World()
    store = TokenStore(tmp_path / "auth.json")
    browser = _Browser(world)

    tokens = await _flow(world, store, resource_metadata=METADATA).authorize(browser)

    assert tokens.access_token == "token-1"
    assert store.tokens(MCP_URL).access_token == "token-1"
    registration = world.registrations[0]
    assert registration["redirect_uris"][0].startswith("http://127.0.0.1:")
    assert registration["token_endpoint_auth_method"] == "none"
    sent = browser.query()
    assert sent["code_challenge_method"] == "S256"
    assert sent["resource"] == MCP_URL
    assert sent["scope"] == "repo read:org"
    assert sent["client_id"] == "client-1"
    exchange = world.token_forms[0]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["resource"] == MCP_URL

    await _flow(world, store).authorize(_Browser(world))
    assert len(world.registrations) == 1, "the registration is reused"


async def test_a_configured_client_id_skips_registration(tmp_path) -> None:
    world = _World(registration=False)
    store = TokenStore(tmp_path / "auth.json")

    with pytest.raises(OAuthError, match="client id"):
        await _flow(world, store).authorize(_Browser(world))
    tokens = await _flow(world, store, client_id="mine", scopes=("repo",)).authorize(
        browser := _Browser(world)
    )

    assert tokens.access_token == "token-1"
    assert world.registrations == []
    assert browser.query()["client_id"] == "mine"
    assert browser.query()["scope"] == "repo"


async def test_a_forged_or_refused_answer_is_not_accepted(tmp_path) -> None:
    world = _World()
    store = TokenStore(tmp_path / "auth.json")

    with pytest.raises(OAuthError, match="did not match"):
        await _flow(world, store).authorize(_Browser(world, state="forged"))
    with pytest.raises(OAuthError, match="access_denied"):
        await _flow(world, store).authorize(_Browser(world, error="access_denied"))
    assert store.tokens(MCP_URL) is None


async def test_an_expired_token_is_refreshed_and_kept(tmp_path) -> None:
    world = _World()
    world.issued = "token-2"
    store = TokenStore(tmp_path / "auth.json")
    old = OAuthTokens(access_token="token-1", refresh_token="refresh-1", expires_at=time.time() - 5)

    fresh = await _flow(world, store).refresh(old)

    assert old.expired()
    assert fresh.access_token == "token-2"
    assert fresh.refresh_token == "refresh-1"
    assert store.tokens(MCP_URL).access_token == "token-2"
    assert world.token_forms[-1]["grant_type"] == "refresh_token"
    with pytest.raises(OAuthError, match="sign in again"):
        await _flow(world, store).refresh(OAuthTokens(access_token="x"))


def test_the_token_file_is_private_and_forgetting_keeps_the_client(tmp_path) -> None:
    store = TokenStore(tmp_path / "auth.json")
    store.save(MCP_URL, tokens=OAuthTokens(access_token="a"))

    if os.name != "nt":
        assert (tmp_path / "auth.json").stat().st_mode & 0o077 == 0
    store.forget("HTTPS://MCP.EXAMPLE.COM/mcp")
    assert store.tokens(MCP_URL) is None


# -- through the manager -----------------------------------------------------------


async def test_a_remote_server_waits_for_sign_in_then_connects(tmp_path, monkeypatch) -> None:
    world = _World()
    monkeypatch.setattr(manager_module, "_BACKOFF_S", 0.05)
    monkeypatch.setattr(StreamableHttpTransport, "_open_client", lambda self: world.client())
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "mcp_servers": {"remote": {"type": "http", "url": MCP_URL}},
        }
    )
    bus = AsyncEventBus()
    urls: list[str] = []
    bus.subscribe(
        lambda event: (
            urls.append(event.payload["url"]) if event.event_type == "mcp.auth.url" else None
        )
    )
    manager = McpManager(
        config=config,
        registry=ToolRegistry(),
        event_bus=bus,
        client_version="test",
        auth_store=TokenStore(tmp_path / "auth.json"),
        oauth_client=world.client,
    )
    await manager.start()
    await manager.wait_ready(10)
    try:
        assert manager.servers["remote"].status == "needs_auth"
        assert manager.servers["remote"].auth_hint == METADATA

        described = await manager.sign_in("remote", open_browser=_Browser(world))
        await manager.wait_ready(10)

        assert manager.servers["remote"].status == "connected"
        assert described["signed_in"] is True
        assert urls and urls[0].startswith("https://auth.example.com/authorize?")
        assert manager.servers["remote"].tools[0].name == "mcp__remote__lookup"

        await manager.sign_out("remote")
        await manager.wait_ready(10)
        assert manager.servers["remote"].status == "needs_auth"
    finally:
        await manager.close()


async def test_a_server_with_its_own_token_header_is_never_signed_in_to(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "mcp_servers": {
                "gh": {"type": "http", "url": MCP_URL, "headers": {"Authorization": "Bearer pat"}},
                "local": {"command": "npx"},
            },
        }
    )
    manager = McpManager(
        config=config,
        registry=ToolRegistry(),
        event_bus=AsyncEventBus(),
        client_version="test",
        auth_store=TokenStore(tmp_path / "auth.json"),
    )
    await manager.start()
    try:
        transport = manager._transport_for(manager.servers["gh"])
        assert transport._auth is None
        with pytest.raises(ValueError, match="nothing to sign in"):
            await manager.sign_in("local")
    finally:
        await manager.close()


def test_a_server_config_carries_its_oauth_client(tmp_path) -> None:
    from code_ai.mcp.config import parse_server

    spec = parse_server(
        "api",
        {"url": MCP_URL, "oauth": {"clientId": "abc", "scopes": ["repo", "user"], "x": 1}},
    )

    assert spec.oauth == {"client_id": "abc", "scopes": "repo user"}
    assert parse_server("api", spec.to_config()).oauth == spec.oauth


def test_the_auth_file_lives_in_the_config_dir() -> None:
    from code_ai.mcp.oauth import auth_store_path

    assert auth_store_path().parent == Path.home() / ".code-ai"
