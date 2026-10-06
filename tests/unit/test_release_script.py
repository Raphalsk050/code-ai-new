"""Releases carry the right version and notes a reader takes in at a glance."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = _load("release")
bump_version = _load("bump_version")

_VERSION = {"major": 0, "minor": 27, "patch": 1, "build": 60, "note": "Retries, not failures"}


def test_only_user_facing_commits_become_changes() -> None:
    change = release.parse_subject("feat(tui): paint every theme in its own colours")

    assert change == release.Change("feat", "Terminal", "Paint every theme in its own colours")
    assert release.parse_subject("fix(vscode): keep working after the backend fails").area == (
        "VS Code"
    )
    assert release.parse_subject("feat(intellij): host the chat in a tool window").area == (
        "Android Studio"
    )
    assert release.parse_subject("refactor(extension): take the avatar out").area == "VS Code"
    for subject in ("chore: bump", "test(tui): cover it", "i18n(core): English", "Merge x"):
        assert release.parse_subject(subject) is None


def test_the_title_carries_the_build_and_the_notes_are_three_short_sections() -> None:
    subjects = [f"feat(mcp): capability {n}" for n in range(9)] + [
        "fix(bridge): read the pipe as UTF-8",
        "refactor: remove the old layer",
        "perf(intellij): read the project once",
        "chore: tidy",
        "feat(mcp): capability 1",
    ]

    assert release.release_title(_VERSION) == "Code-AI v0.27.1 - build 60"
    notes = release.build_notes(subjects)

    assert notes.startswith("## Features\n\n- **MCP:** Capability 0\n")
    assert "Capability 6" not in notes
    # A heading, a blank line, the list; and a blank line between sections.
    assert notes.endswith(
        "- **MCP:** Capability 5\n"
        "\n"
        "## Improvement\n\n- Remove the old layer\n- **Android Studio:** Read the project once\n"
        "\n"
        "## Fixes\n\n- **VS Code:** Read the pipe as UTF-8\n"
    )


def test_a_build_with_nothing_to_announce_says_so() -> None:
    assert release.build_notes(["chore: tidy", "test: more"]) == (
        "Maintenance build: no user-facing changes.\n"
    )


def test_the_extension_ships_under_the_product_version(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{\r\n  "name": "code-ai",\r\n  "version": "0.13.2",\r\n  "engines": {}\r\n}\r\n',
        encoding="utf-8",
        newline="",
    )
    lock = {
        "name": "code-ai",
        "version": "0.13.2",
        "packages": {
            "": {"name": "code-ai", "version": "0.13.2"},
            "node_modules/diff": {"version": "5.2.0"},
        },
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")

    bump_version.sync_extension_version("0.27.1", tmp_path)

    raw = (tmp_path / "package.json").read_bytes()
    assert b'"version": "0.27.1"' in raw
    assert b"\r\n" in raw  # line endings left as they were
    synced = json.loads((tmp_path / "package-lock.json").read_text(encoding="utf-8"))
    assert synced["version"] == "0.27.1"
    assert synced["packages"][""]["version"] == "0.27.1"
    assert synced["packages"]["node_modules/diff"]["version"] == "5.2.0"


def test_the_shipped_extension_version_matches_the_product() -> None:
    package = json.loads((_SCRIPTS.parent / "vscode-extension" / "package.json").read_text("utf-8"))
    version = release.read_version()

    assert package["version"] == release.release_version(version)


class _FakeGitHub:
    """Just enough of the releases API to follow what publish sends."""

    def __init__(self, existing: dict | None = None) -> None:
        self.existing = existing
        self.calls: list[tuple[str, str, bytes]] = []

    def serve(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return None

            def _reply(self, status: int, body: object = None) -> None:
                payload = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                fake.calls.append((self.command, self.path, body))
                base = f"http://127.0.0.1:{server.server_port}"
                release = {
                    "id": 7,
                    "html_url": f"{base}/releases/v0.27.1",
                    "upload_url": f"{base}/uploads/7/assets{{?name,label}}",
                    "assets": [{"id": 70, "name": "code-ai-win-v0.27.1.exe"}],
                }
                if self.command == "GET":
                    return self._reply(200, fake.existing) if fake.existing else self._reply(404)
                if self.command in ("POST", "PATCH") and "/uploads/" not in self.path:
                    return self._reply(200 if self.command == "PATCH" else 201, release)
                return self._reply(201 if self.command == "POST" else 204, {})

            do_GET = do_POST = do_PATCH = do_DELETE = _handle

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


def _publish(monkeypatch, tmp_path, existing: dict | None):
    fake = _FakeGitHub(existing)
    server = fake.serve()
    asset = tmp_path / "code-ai-win-v0.27.1.exe"
    asset.write_bytes(b"MZ binary")
    monkeypatch.setenv("GITHUB_API_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("GITHUB_REPOSITORY", "company/code-ai")
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    monkeypatch.setattr(release, "read_version", lambda path=None: _VERSION)
    monkeypatch.setattr(release, "commit_subjects", lambda since: ["feat(tui): a thing"])
    monkeypatch.setattr(release, "previous_tag", lambda current: "v0.27.0")
    try:
        release.publish([asset], prerelease=True)
    finally:
        server.shutdown()
    return fake.calls


def test_a_new_version_creates_its_release_and_uploads_the_assets(monkeypatch, tmp_path) -> None:
    calls = _publish(monkeypatch, tmp_path, existing=None)

    methods = [(method, path.split("?")[0]) for method, path, _ in calls]
    assert methods[0] == ("GET", "/repos/company/code-ai/releases/tags/v0.27.1")
    assert methods[1] == ("POST", "/repos/company/code-ai/releases")
    created = json.loads(calls[1][2])
    assert created["tag_name"] == "v0.27.1"
    assert created["target_commitish"] == "abc123"
    assert created["name"] == "Code-AI v0.27.1 - build 60"
    assert created["prerelease"] is True
    assert created["body"] == "## Features\n\n- **Terminal:** A thing\n"
    # The asset of the same name the release already had is replaced.
    assert ("DELETE", "/repos/company/code-ai/releases/assets/70") in methods
    upload = calls[-1]
    assert upload[0] == "POST"
    assert upload[1] == "/uploads/7/assets?name=code-ai-win-v0.27.1.exe"
    assert upload[2] == b"MZ binary"


def test_the_same_version_built_again_moves_its_tag(monkeypatch, tmp_path) -> None:
    calls = _publish(monkeypatch, tmp_path, existing={"id": 7})

    methods = [(method, path) for method, path, _ in calls]
    assert ("PATCH", "/repos/company/code-ai/git/refs/tags/v0.27.1") in methods
    assert ("PATCH", "/repos/company/code-ai/releases/7") in methods
    moved = next(json.loads(body) for method, path, body in calls if "git/refs" in path)
    assert moved == {"sha": "abc123", "force": True}
