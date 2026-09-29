"""Updates come from GitHub releases: the newest one, the right file, put in place."""

from __future__ import annotations

import base64
import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_ai import updates
from code_ai.bridge.server import BridgeServer

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build-binaries.yml"


def _release(tag: str, *, prerelease: bool = False, draft: bool = False, assets=()) -> dict:
    return {
        "tag_name": tag,
        "name": f"Code-AI {tag}",
        "body": f"notes for {tag}",
        "html_url": f"https://example/releases/{tag}",
        "prerelease": prerelease,
        "draft": draft,
        "assets": [
            {"name": name, "browser_download_url": f"/dl/{name}", "url": f"/api/{name}", "size": 9}
            for name in assets
        ],
    }


def test_the_newest_published_release_wins() -> None:
    listing = [
        _release("v0.9.0"),
        _release("v0.30.0", draft=True),
        _release("v0.29.1", prerelease=True),
        _release("v0.28.0"),
        _release("not-a-version"),
    ]

    assert updates.pick_newest(listing, include_prereleases=True).version == "0.29.1"
    assert updates.pick_newest(listing, include_prereleases=False).version == "0.28.0"
    assert updates.pick_newest([], include_prereleases=True) is None


def test_only_a_higher_version_is_an_update() -> None:
    release = updates.pick_newest([_release("v0.28.1")], include_prereleases=True)

    assert updates.is_newer(release, "0.28.0")
    assert not updates.is_newer(release, "0.28.1")
    assert not updates.is_newer(release, "0.29.0")
    assert not updates.is_newer(None, "0.1.0")


def test_asset_names_are_the_ones_ci_publishes() -> None:
    # The workflow names the files; the updater has to ask for the same names.
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    assert "code-ai-win-v$CODE_AI_VERSION.exe" in workflow
    assert 'code-ai-linux-v$CODE_AI_VERSION"' in workflow
    assert "code-ai-vscode-v$CODE_AI_VERSION-$target" in workflow

    assert updates.asset_name("binary", "1.2.3", "win") == "code-ai-win-v1.2.3.exe"
    assert updates.asset_name("binary", "1.2.3", "linux") == "code-ai-linux-v1.2.3"
    vsix = updates.asset_name("vsix", "1.2.3", "win32-x64")
    assert vsix == "code-ai-vscode-v1.2.3-win32-x64.vsix"
    assert updates.platform_key("win32") == "win"
    assert updates.platform_key("linux") == "linux"
    assert updates.platform_key("darwin") == ""


class _FakeGitHub:
    def __init__(self, listing: list[dict], *, private: bool = False, bad_tokens=()) -> None:
        self.listing = listing
        # A private repository looks missing to anyone not signed in.
        self.private = private
        self.bad_tokens = set(bad_tokens)
        self.requests: list[tuple[str, dict[str, str]]] = []

    def __enter__(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return None

            def do_GET(self) -> None:  # noqa: N802 - http.server's naming
                fake.requests.append((self.path, dict(self.headers)))
                signed = self.headers.get("Authorization")
                status = 200
                if signed in fake.bad_tokens:
                    status, body = 401, b'{"message": "Bad credentials"}'
                elif fake.private and not signed:
                    status, body = 404, b'{"message": "Not Found"}'
                elif "/releases" in self.path:
                    body = json.dumps(fake.listing).encode()
                elif self.path.startswith(("/dl/", "/api/")):
                    body = b"new build " + self.path.encode()
                else:
                    status, body = 404, b""
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_port}"
        # Asset links are relative in the fixture; give them this server's origin.
        for item in self.listing:
            for asset in item["assets"]:
                asset["browser_download_url"] = base + asset["browser_download_url"]
                asset["url"] = base + asset["url"]
        return base

    def __exit__(self, *exc) -> None:
        self.server.shutdown()


@pytest.fixture
def github(monkeypatch):
    def serve(listing: list[dict], **options):
        fake = _FakeGitHub(listing, **options)
        base = fake.__enter__()
        monkeypatch.setattr(updates, "RELEASES_API", base)
        monkeypatch.setattr(updates, "RELEASES_REPOSITORY", "team/code-ai")
        # conftest keeps every test off GitHub; these talk to the fake one.
        monkeypatch.setattr(updates, "newest_release", _REAL_NEWEST)
        monkeypatch.setattr(updates, "current_version", lambda: "0.28.0")
        return fake

    fakes: list[_FakeGitHub] = []

    def start(listing: list[dict], **options):
        fake = serve(listing, **options)
        fakes.append(fake)
        return fake

    yield start
    for fake in fakes:
        fake.__exit__()


_REAL_NEWEST = updates.newest_release


async def test_a_newer_release_is_found_and_its_file_downloaded(github, tmp_path) -> None:
    fake = github([_release("v0.29.0", assets=["code-ai-win-v0.29.0.exe"])])

    release = await updates.check(verify_ssl=False)

    assert release is not None and release.version == "0.29.0"
    assert fake.requests[0][0] == "/repos/team/code-ai/releases?per_page=30"
    asset = updates.asset_for(release, "binary", "win")
    path = await updates.download(asset, tmp_path, verify_ssl=False)
    assert path.name == "code-ai-win-v0.29.0.exe"
    assert path.read_bytes() == b"new build /dl/code-ai-win-v0.29.0.exe"
    assert not list(tmp_path.glob("*.part"))


async def test_a_private_repository_is_read_with_the_token(github, tmp_path, monkeypatch) -> None:
    fake = github([_release("v0.29.0", assets=["code-ai-linux-v0.29.0"])])
    monkeypatch.setenv(updates.RELEASES_TOKEN_ENV, "s3cret")

    release = await updates.check(verify_ssl=False)
    asset = updates.asset_for(release, "binary", "linux")
    await updates.download(asset, tmp_path, verify_ssl=False)

    listing_headers = fake.requests[0][1]
    assert listing_headers.get("Authorization") == "Bearer s3cret"
    path, headers = fake.requests[1]
    # Assets of a private repository only come through the API.
    assert path == "/api/code-ai-linux-v0.29.0"
    assert headers.get("Accept") == "application/octet-stream"


async def test_nothing_newer_is_no_update(github) -> None:
    github([_release("v0.28.0"), _release("v0.27.9")])

    assert await updates.check(verify_ssl=False) is None


def _tools(monkeypatch, *, gh: str = "", git: str = "") -> list[tuple[list[str], str]]:
    """Stand-ins for gh and git, answering as this machine's logins would."""
    calls: list[tuple[list[str], str]] = []

    def run(argv: list[str], stdin: str = "") -> str:
        calls.append((argv, stdin))
        return {"gh": gh, "git": git}[argv[0]]

    monkeypatch.setattr(updates, "_run_tool", run)
    return calls


def test_a_login_is_kept_under_the_web_host(monkeypatch) -> None:
    assert updates.login_host() == "github.com"
    monkeypatch.setattr(updates, "RELEASES_API", "https://github.sec.samsung.net/api/v3")
    assert updates.login_host() == "github.sec.samsung.net"


def test_git_s_credential_manager_is_asked_without_a_prompt(monkeypatch) -> None:
    monkeypatch.setattr(updates, "RELEASES_API", "https://github.sec.samsung.net/api/v3")
    calls = _tools(
        monkeypatch,
        git="protocol=https\nhost=github.sec.samsung.net\nusername=rafael\npassword=gho_git\n",
    )

    assert updates.find_credential() == updates.Credential("gho_git", username="rafael")
    assert calls[0][0] == ["gh", "auth", "token", "--hostname", "github.sec.samsung.net"]
    argv, stdin = calls[1]
    assert "credential.interactive=never" in argv and argv[-2:] == ["credential", "fill"]
    assert stdin == "protocol=https\nhost=github.sec.samsung.net\n\n"


def test_the_configured_token_then_the_cli_then_git(monkeypatch) -> None:
    monkeypatch.setattr(updates, "RELEASES_API", "https://github.sec.samsung.net/api/v3")
    _tools(monkeypatch, gh="gho_gh\n", git="username=rafael\npassword=gho_git\n")
    assert updates.find_credential() == updates.Credential("gho_gh")

    # github.com's variable is no login for an Enterprise host.
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_public")
    assert updates.find_credential() == updates.Credential("gho_gh")

    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "ghe_env")
    assert updates.find_credential() == updates.Credential("ghe_env")

    monkeypatch.setenv(updates.RELEASES_TOKEN_ENV, "s3cret")
    assert updates.find_credential() == updates.Credential("s3cret", explicit=True)


async def test_the_machine_s_login_opens_a_private_repository(github, monkeypatch) -> None:
    fake = github([_release("v0.29.0")], private=True)
    _tools(monkeypatch, git="username=rafael\npassword=gho_git\n")

    release = await updates.check(verify_ssl=False)

    assert release is not None and release.version == "0.29.0"
    basic = "Basic " + base64.b64encode(b"rafael:gho_git").decode()
    assert fake.requests[0][1].get("Authorization") == basic


async def test_a_stale_login_gives_way_to_an_anonymous_ask(github, monkeypatch) -> None:
    fake = github([_release("v0.29.0")], bad_tokens={"Bearer gho_stale"})
    _tools(monkeypatch, gh="gho_stale\n")

    release = await updates.check(verify_ssl=False)

    assert release is not None
    sent = [headers.get("Authorization") for _, headers in fake.requests]
    assert sent == ["Bearer gho_stale", None]
    # Not offered again this session.
    assert updates.find_credential() is None


async def test_a_private_repository_without_a_login_says_how_to_get_one(github) -> None:
    github([_release("v0.29.0")], private=True)

    with pytest.raises(updates.UpdateError, match="sign in to 127.0.0.1 with git or gh"):
        await updates.check(verify_ssl=False)


async def test_the_login_never_leaves_the_api_host(github, monkeypatch, tmp_path) -> None:
    fake = github([_release("v0.29.0", assets=["code-ai-linux-v0.29.0"])])
    _tools(monkeypatch, gh="gho_gh\n")
    release = await updates.check(verify_ssl=False)
    asset = updates.asset_for(release, "binary", "linux")
    elsewhere = dataclasses.replace(asset, api_url=asset.api_url.replace("127.0.0.1", "localhost"))

    await updates.download(elsewhere, tmp_path, verify_ssl=False)

    path, headers = fake.requests[-1]
    assert path == "/dl/code-ai-linux-v0.29.0"
    assert "Authorization" not in headers


def test_the_running_binary_steps_aside_for_the_new_one(tmp_path) -> None:
    current = tmp_path / "code-ai.exe"
    current.write_bytes(b"old build")
    new = tmp_path / "download" / "code-ai-win-v0.29.0.exe"
    new.parent.mkdir()
    new.write_bytes(b"new build")

    updates.replace_binary(new, current)

    assert current.read_bytes() == b"new build"
    assert (tmp_path / "code-ai.exe.old").read_bytes() == b"old build"
    updates.remove_replaced(current)
    assert not (tmp_path / "code-ai.exe.old").exists()


def test_a_backend_bundled_with_the_extension_is_left_to_the_extension() -> None:
    extension = "C:/Users/ana/.vscode/extensions/code-ai.code-ai-0.28.1-win32-x64"
    bundled = Path(extension) / "bin" / "code-ai.exe"
    assert updates.bundled_with_extension(bundled)
    assert not updates.bundled_with_extension(Path("C:/tools/code-ai.exe"))
    # Running from source there is nothing to replace.
    assert updates.running_binary() is None


def _bridge() -> SimpleNamespace:
    config = SimpleNamespace(ssl_verification=False)
    return SimpleNamespace(_app=SimpleNamespace(session=SimpleNamespace(config=config)))


async def test_the_bridge_reports_and_fetches_the_release(github, tmp_path, monkeypatch) -> None:
    github([_release("v0.29.0", assets=["code-ai-vscode-v0.29.0-win32-x64.vsix"])])
    monkeypatch.setattr(updates.tempfile, "gettempdir", lambda: str(tmp_path))

    info = await BridgeServer._h_check_for_update(_bridge(), {})
    fetched = await BridgeServer._h_download_update(_bridge(), {"target": "win32-x64"})

    assert info["version"] == "0.29.0"
    assert info["current"] == "0.28.0"
    assert info["notes"] == "notes for v0.29.0"
    assert fetched["version"] == "0.29.0"
    assert Path(fetched["path"]).name == "code-ai-vscode-v0.29.0-win32-x64.vsix"
    with pytest.raises(ValueError, match="no VS Code package for linux-x64"):
        await BridgeServer._h_download_update(_bridge(), {"target": "linux-x64"})


async def test_the_bridge_says_nothing_when_up_to_date(monkeypatch) -> None:
    monkeypatch.setattr(updates, "RELEASES_REPOSITORY", "team/code-ai")
    # conftest's stub: GitHub has nothing to offer.
    assert await BridgeServer._h_check_for_update(_bridge(), {}) == {}


async def test_the_placeholder_repository_is_never_asked(github, monkeypatch) -> None:
    fake = github([_release("v0.29.0", assets=["code-ai-vscode-v0.29.0-win32-x64.vsix"])])
    monkeypatch.setattr(updates, "RELEASES_REPOSITORY", "owner/repository")

    assert not updates.configured()
    assert await updates.check(verify_ssl=False) is None
    info = await BridgeServer._h_check_for_update(_bridge(), {})
    assert info == {"configured": False, "message": updates.UNSET_MESSAGE}
    with pytest.raises(ValueError, match="RELEASES_REPOSITORY"):
        await BridgeServer._h_download_update(_bridge(), {"target": "win32-x64"})
    assert fake.requests == []
