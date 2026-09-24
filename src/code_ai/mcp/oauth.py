"""Signing in to a remote MCP server: OAuth 2.1 with PKCE, as the MCP spec has it.

The server answers 401 and names its protected-resource metadata (RFC 9728),
which names the authorization server; that one's metadata (RFC 8414) gives the
endpoints. Code-AI registers itself there when the server allows it (RFC 7591)
- or uses the client id the user configured - opens the browser on the
authorization page, and takes the code on a loopback port (RFC 8252). Tokens
are kept per server URL in ``~/.code-ai/mcp-auth.json`` and refreshed when they
expire, so signing in is a once-per-server thing.

Every request follows ``ssl_verification``: the company network re-signs TLS.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from code_ai.config.defaults import DEFAULT_CONFIG_DIRNAME

# A token this close to expiring is refreshed before it is sent.
_EXPIRY_MARGIN_S = 60.0
_CALLBACK_TIMEOUT_S = 300.0
_CLIENT_NAME = "Code-AI"


class OAuthError(Exception):
    """Signing in could not go on; the message says what the user can do."""


@dataclass(slots=True)
class OAuthTokens:
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str = ""
    expires_at: float = 0.0
    scope: str = ""

    def expired(self, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now or time.time()) >= self.expires_at - _EXPIRY_MARGIN_S

    def header(self) -> str:
        kind = self.token_type if self.token_type.lower() != "bearer" else "Bearer"
        return f"{kind} {self.access_token}"


@dataclass(slots=True)
class AuthServer:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str = ""
    scopes_supported: list[str] = field(default_factory=list)
    token_auth_methods: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ClientRegistration:
    client_id: str
    client_secret: str = ""
    redirect_uri: str = ""
    auth_method: str = "none"


def auth_store_path() -> Path:
    return Path.home() / DEFAULT_CONFIG_DIRNAME / "mcp-auth.json"


def resource_uri(url: str) -> str:
    """The server URL as the ``resource`` parameter wants it (RFC 8707)."""

    parts = urlparse(url)
    return urlunparse((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", parts.query, ""))


class TokenStore:
    """Tokens and client registrations, per server URL, in one JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or auth_store_path()

    def tokens(self, server_url: str) -> OAuthTokens | None:
        entry = self._read().get(resource_uri(server_url), {}).get("tokens")
        if not isinstance(entry, dict) or not entry.get("access_token"):
            return None
        return OAuthTokens(**{k: entry[k] for k in entry if k in OAuthTokens.__dataclass_fields__})

    def client(self, server_url: str, issuer: str) -> ClientRegistration | None:
        entry = self._read().get(resource_uri(server_url), {})
        client = entry.get("client")
        if not isinstance(client, dict) or entry.get("issuer") != issuer:
            return None
        fields = ClientRegistration.__dataclass_fields__
        return ClientRegistration(**{k: client[k] for k in client if k in fields})

    def save(
        self,
        server_url: str,
        *,
        tokens: OAuthTokens | None = None,
        client: ClientRegistration | None = None,
        issuer: str = "",
    ) -> None:
        data = self._read()
        entry = data.setdefault(resource_uri(server_url), {})
        if tokens is not None:
            entry["tokens"] = asdict(tokens)
        if client is not None:
            entry["client"] = asdict(client)
            entry["issuer"] = issuer
        self._write(data)

    def forget(self, server_url: str) -> None:
        data = self._read()
        entry = data.get(resource_uri(server_url))
        if entry is not None:
            entry.pop("tokens", None)
            self._write(data)

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, self._path)


ClientFactory = Callable[[], httpx.AsyncClient]
OpenBrowser = Callable[[str], Awaitable[None]]


class OAuthFlow:
    """One server's sign-in: discovery, registration, the browser, the tokens."""

    def __init__(
        self,
        *,
        server_url: str,
        store: TokenStore,
        verify: bool,
        resource_metadata: str = "",
        client_id: str = "",
        client_secret: str = "",
        scopes: tuple[str, ...] = (),
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._server_url = server_url
        self._store = store
        self._resource_metadata = resource_metadata
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(verify=verify, timeout=30.0, follow_redirects=True)
        )
        self._server: AuthServer | None = None
        self._resource_scopes: list[str] = []

    # -- discovery ------------------------------------------------------------

    async def discover(self) -> AuthServer:
        if self._server is not None:
            return self._server
        async with self._client_factory() as http:
            issuer = await self._issuer(http)
            self._server = await self._metadata(http, issuer)
        return self._server

    async def _issuer(self, http: httpx.AsyncClient) -> str:
        """The authorization server, from the resource's metadata; the server itself if none."""

        parts = urlparse(self._server_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        candidates = [self._resource_metadata] if self._resource_metadata else []
        path = parts.path.rstrip("/")
        if path:
            candidates.append(f"{origin}/.well-known/oauth-protected-resource{path}")
        candidates.append(f"{origin}/.well-known/oauth-protected-resource")
        for url in candidates:
            document = await _json(http, url)
            servers = document.get("authorization_servers") if document else None
            if isinstance(servers, list) and servers:
                scopes = document.get("scopes_supported")
                self._resource_scopes = [str(s) for s in scopes] if isinstance(scopes, list) else []
                return str(servers[0]).rstrip("/")
        # Servers of the 2025-03-26 spec are their own authorization server.
        return origin

    async def _metadata(self, http: httpx.AsyncClient, issuer: str) -> AuthServer:
        parts = urlparse(issuer)
        origin = f"{parts.scheme}://{parts.netloc}"
        path = parts.path.rstrip("/")
        candidates = (
            [
                f"{origin}/.well-known/oauth-authorization-server{path}",
                f"{origin}/.well-known/openid-configuration{path}",
                f"{origin}{path}/.well-known/openid-configuration",
            ]
            if path
            else [
                f"{origin}/.well-known/oauth-authorization-server",
                f"{origin}/.well-known/openid-configuration",
            ]
        )
        for url in candidates:
            document = await _json(http, url)
            if (
                document
                and document.get("authorization_endpoint")
                and document.get("token_endpoint")
            ):
                return AuthServer(
                    issuer=str(document.get("issuer") or issuer),
                    authorization_endpoint=str(document["authorization_endpoint"]),
                    token_endpoint=str(document["token_endpoint"]),
                    registration_endpoint=str(document.get("registration_endpoint") or ""),
                    scopes_supported=[str(s) for s in document.get("scopes_supported") or []],
                    token_auth_methods=[
                        str(m) for m in document.get("token_endpoint_auth_methods_supported") or []
                    ],
                )
        # No metadata at all: the spec's fallback paths on the server's origin.
        return AuthServer(
            issuer=issuer,
            authorization_endpoint=f"{origin}/authorize",
            token_endpoint=f"{origin}/token",
            registration_endpoint=f"{origin}/register",
        )

    # -- registration -----------------------------------------------------------

    async def _client(self, server: AuthServer, redirect_uri: str) -> ClientRegistration:
        if self._client_id:
            return ClientRegistration(
                client_id=self._client_id,
                client_secret=self._client_secret,
                redirect_uri=redirect_uri,
                auth_method="client_secret_post" if self._client_secret else "none",
            )
        known = self._store.client(self._server_url, server.issuer)
        if known is not None and known.redirect_uri == redirect_uri:
            return known
        if not server.registration_endpoint:
            raise OAuthError(
                "The server does not let clients register themselves. Add its client id "
                'to the server\'s settings: "oauth": {"client_id": "..."}.'
            )
        body = {
            "client_name": _CLIENT_NAME,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        async with self._client_factory() as http:
            try:
                response = await http.post(server.registration_endpoint, json=body)
            except httpx.HTTPError as exc:
                raise OAuthError(f"Could not register with {server.issuer}: {exc}") from exc
        if response.status_code >= 400:
            raise OAuthError(
                f"Registration refused ({response.status_code}): {response.text[:300]}"
            )
        answer = response.json()
        client = ClientRegistration(
            client_id=str(answer.get("client_id") or ""),
            client_secret=str(answer.get("client_secret") or ""),
            redirect_uri=redirect_uri,
            auth_method=str(answer.get("token_endpoint_auth_method") or "none"),
        )
        if not client.client_id:
            raise OAuthError("The registration answer carried no client_id.")
        self._store.save(self._server_url, client=client, issuer=server.issuer)
        return client

    # -- the interactive part ----------------------------------------------------

    async def authorize(self, open_browser: OpenBrowser) -> OAuthTokens:
        """Run the whole sign-in; ``open_browser`` gets the page the user must visit."""

        server = await self.discover()
        if urlparse(server.authorization_endpoint).scheme not in ("https", "http"):
            raise OAuthError("The server's sign-in page is not a web address.")
        known = self._store.client(self._server_url, server.issuer)
        port = urlparse(known.redirect_uri).port if known and known.redirect_uri else None
        async with _Callback(port or 0) as callback:
            redirect_uri = f"http://127.0.0.1:{callback.port}/callback"
            client = await self._client(server, redirect_uri)
            verifier = secrets.token_urlsafe(64)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
                .rstrip(b"=")
                .decode("ascii")
            )
            state = secrets.token_urlsafe(24)
            query: dict[str, str] = {
                "response_type": "code",
                "client_id": client.client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "resource": resource_uri(self._server_url),
            }
            scopes = self._scopes or tuple(self._resource_scopes)
            if scopes:
                query["scope"] = " ".join(scopes)
            separator = "&" if "?" in server.authorization_endpoint else "?"
            await open_browser(f"{server.authorization_endpoint}{separator}{urlencode(query)}")
            params = await callback.wait(_CALLBACK_TIMEOUT_S)
        if params.get("state") != state:
            raise OAuthError("The sign-in answer did not match the request; try again.")
        if params.get("error"):
            detail = params.get("error_description") or params["error"]
            raise OAuthError(f"The server refused the sign-in: {detail}")
        code = params.get("code")
        if not code:
            raise OAuthError("The sign-in came back without a code.")
        tokens = await self._token_request(
            server,
            client,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        self._store.save(self._server_url, tokens=tokens)
        return tokens

    async def refresh(self, tokens: OAuthTokens) -> OAuthTokens:
        if not tokens.refresh_token:
            raise OAuthError("The session expired and cannot be renewed; sign in again.")
        server = await self.discover()
        client = self._store.client(self._server_url, server.issuer) or ClientRegistration(
            client_id=self._client_id, client_secret=self._client_secret
        )
        fresh = await self._token_request(
            server,
            client,
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
        )
        if not fresh.refresh_token:
            fresh.refresh_token = tokens.refresh_token
        self._store.save(self._server_url, tokens=fresh)
        return fresh

    async def _token_request(
        self, server: AuthServer, client: ClientRegistration, form: dict[str, str]
    ) -> OAuthTokens:
        form = {**form, "client_id": client.client_id, "resource": resource_uri(self._server_url)}
        auth: tuple[str, str] | None = None
        if client.client_secret:
            if client.auth_method == "client_secret_basic":
                auth = (client.client_id, client.client_secret)
            else:
                form["client_secret"] = client.client_secret
        async with self._client_factory() as http:
            try:
                response = await http.post(
                    server.token_endpoint,
                    data=form,
                    auth=auth,
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise OAuthError(f"Could not reach {server.token_endpoint}: {exc}") from exc
        try:
            answer = response.json()
        except ValueError:
            # Some servers (GitHub) answer form-encoded whatever Accept says.
            answer = {key: values[0] for key, values in parse_qs(response.text).items()}
        if response.status_code >= 400 or not answer.get("access_token"):
            detail = answer.get("error_description") or answer.get("error") or response.text[:200]
            raise OAuthError(f"The token request was refused: {detail}")
        expires_in = answer.get("expires_in")
        return OAuthTokens(
            access_token=str(answer["access_token"]),
            token_type=str(answer.get("token_type") or "Bearer"),
            refresh_token=str(answer.get("refresh_token") or ""),
            expires_at=time.time() + float(expires_in) if expires_in else 0.0,
            scope=str(answer.get("scope") or ""),
        )


class _Callback:
    """The loopback page the browser is sent back to, holding the code."""

    def __init__(self, port: int) -> None:
        self._wanted = port
        self._server: asyncio.AbstractServer | None = None
        self._result: asyncio.Future[dict[str, str]] | None = None
        self.port = 0

    async def __aenter__(self) -> _Callback:
        self._result = asyncio.get_running_loop().create_future()
        try:
            self._server = await asyncio.start_server(self._serve, "127.0.0.1", self._wanted)
        except OSError:
            # The port an earlier registration used is taken: any port will do,
            # at the price of registering again.
            self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def wait(self, timeout: float) -> dict[str, str]:
        assert self._result is not None
        try:
            return await asyncio.wait_for(self._result, timeout)
        except TimeoutError:
            raise OAuthError("Nobody finished the sign-in in the browser in time.") from None

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
            while (await asyncio.wait_for(reader.readline(), 10)).strip():
                pass
            target = line.split(" ")[1] if line.count(" ") >= 2 else "/"
            parts = urlparse(target)
            if parts.path != "/callback":
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                return
            params = {key: values[0] for key, values in parse_qs(parts.query).items()}
            ok = "code" in params and "error" not in params
            page = (
                "<h2>Signed in.</h2><p>You can close this tab and go back to Code-AI.</p>"
                if ok
                else "<h2>Sign-in failed.</h2><p>Code-AI shows what the server said.</p>"
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                + f"Content-Length: {len(page)}\r\nConnection: close\r\n\r\n".encode()
                + page
            )
            if self._result is not None and not self._result.done():
                self._result.set_result(params)
        except Exception:  # noqa: BLE001 - a stray connection must not end the sign-in
            return
        finally:
            with contextlib.suppress(Exception):
                await writer.drain()
                writer.close()


async def _json(http: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    try:
        response = await http.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
