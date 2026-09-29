#!/usr/bin/env python
"""Publish a build as a GitHub release: short notes, versioned assets.

    python scripts/release.py version            # 0.26.1
    python scripts/release.py notes              # the release notes, as Markdown
    python scripts/release.py publish out/*      # create or update the release

``publish`` reads GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_SHA and
GITHUB_API_URL from the Actions environment. Standard library only: CI runs it
in a bare python container.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION_FILE = REPO / "src" / "code_ai" / "version.json"

# Each section, and how many lines it gets before the rest is summed up in one.
_SECTIONS = {"feat": ("What's new", 6), "perf": ("Faster", 3), "fix": ("Fixed", 5)}
_AREAS = {
    "tui": "Terminal",
    "vscode": "VS Code",
    "bridge": "VS Code",
    "settings": "Settings",
    "graph": "Graph editor",
    "mcp": "MCP",
    "interop": "Setup",
    "agent": "Agent",
    "prompt": "Agent",
    "core": "Agent",
    "orchestrator": "Agent",
    "providers": "Models",
    "tools": "Tools",
    "build": "Build",
}
_SUBJECT = re.compile(r"^(?P<kind>[a-z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<text>.+)$")


@dataclass(frozen=True, slots=True)
class Change:
    kind: str
    area: str
    text: str


def read_version(path: Path = VERSION_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def release_version(data: dict) -> str:
    """``X.Y.Z``: the tag, the asset names and the extension's version."""
    return f"{data['major']}.{data['minor']}.{data['patch']}"


def parse_subject(subject: str) -> Change | None:
    """A user-facing change from a conventional commit subject, or None."""
    match = _SUBJECT.match(subject.strip())
    if not match or match["kind"] not in _SECTIONS:
        return None
    scope = (match["scope"] or "").split(",")[0].strip()
    area = _AREAS.get(scope, scope.capitalize())
    text = match["text"].strip().rstrip(".")
    return Change(match["kind"], area, text[:1].upper() + text[1:])


def build_notes(version: dict, subjects: list[str]) -> str:
    """Release notes a reader takes in at a glance: what changed, by area."""
    changes: list[Change] = []
    seen: set[str] = set()
    for subject in subjects:
        change = parse_subject(subject)
        if change and change.text.lower() not in seen:
            seen.add(change.text.lower())
            changes.append(change)

    lines = [f"**Code-AI v{release_version(version)}** · build {version.get('build', 0)}"]
    if version.get("note"):
        lines += ["", str(version["note"]).strip()]
    left_out = 0
    for kind, (title, limit) in _SECTIONS.items():
        picked = [change for change in changes if change.kind == kind]
        left_out += max(0, len(picked) - limit)
        if picked:
            lines += ["", f"**{title}**"]
            lines += [
                f"- {change.area}: {change.text}" if change.area else f"- {change.text}"
                for change in picked[:limit]
            ]
    if not changes:
        lines += ["", "Maintenance build: no user-facing changes."]
    if left_out:
        lines += ["", f"Plus {left_out} smaller change{'s' if left_out != 1 else ''}."]
    lines += [
        "",
        "**Downloads**: `code-ai-win` (Windows), `code-ai-linux` (Linux), "
        "`code-ai-vscode` (VS Code extension, the agent included), "
        "`code-ai-mac` (macOS: the command and the VS Code extension in one).",
        "",
        "**macOS**: `code-ai-mac-arm64` for Apple chips (M1 and later), `code-ai-mac-x64` "
        "for Intel. Install from the Terminal with "
        f"`sh ~/Downloads/code-ai-mac-arm64-v{release_version(version)}.sh`.",
    ]
    return "\n".join(lines) + "\n"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def previous_tag(current: str) -> str:
    """The newest release tag before this build, "" when there is none."""
    try:
        tags = _git("tag", "--merged", "HEAD", "--list", "v*", "--sort=-v:refname").split()
    except subprocess.CalledProcessError:
        return ""
    return next((tag for tag in tags if tag != f"v{current}"), "")


def commit_subjects(since: str) -> list[str]:
    span = f"{since}..HEAD" if since else "HEAD"
    return _git("log", "--no-merges", "--format=%s", "-n", "200", span).splitlines()


# --- GitHub ---------------------------------------------------------------


class GitHub:
    def __init__(self, api: str, repository: str, token: str) -> None:
        self.api = api.rstrip("/")
        self.repository = repository
        self.token = token
        # The company network re-signs TLS with its own certificate, which a
        # bare container does not trust.
        self.context = ssl._create_unverified_context()

    def call(
        self,
        method: str,
        url: str,
        body: object = None,
        *,
        raw: bytes | None = None,
        content_type: str = "application/json",
    ) -> dict | list | None:
        if not url.startswith("http"):
            url = f"{self.api}/repos/{self.repository}{url}"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")
        if data is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=600) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            detail = exc.read().decode(errors="replace")[:500]
            raise SystemExit(f"{method} {url} failed: {exc.code} {detail}") from exc
        return json.loads(payload) if payload else {}


def publish(assets: list[Path], *, prerelease: bool) -> str:
    version = read_version()
    release = release_version(version)
    tag = f"v{release}"
    sha = os.environ["GITHUB_SHA"]
    github = GitHub(
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_TOKEN"],
    )
    notes = build_notes(version, commit_subjects(previous_tag(release)))
    fields = {"name": f"Code-AI v{release}", "body": notes, "prerelease": prerelease}

    existing = github.call("GET", f"/releases/tags/{tag}")
    if isinstance(existing, dict) and existing.get("id"):
        # The same version built again: the release follows the newest build.
        github.call("PATCH", f"/git/refs/tags/{tag}", {"sha": sha, "force": True})
        current = github.call("PATCH", f"/releases/{existing['id']}", fields)
    else:
        current = github.call(
            "POST", "/releases", {"tag_name": tag, "target_commitish": sha, **fields}
        )
    assert isinstance(current, dict)

    names = {asset.name for asset in assets}
    for old in current.get("assets") or []:
        if old.get("name") in names:
            github.call("DELETE", f"/releases/assets/{old['id']}")
    upload = str(current["upload_url"]).split("{", 1)[0]
    for asset in assets:
        kind = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        github.call(
            "POST",
            f"{upload}?name={urllib.parse.quote(asset.name)}",
            raw=asset.read_bytes(),
            content_type=kind,
        )
        print(f"uploaded {asset.name}")
    return str(current.get("html_url") or tag)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="Print X.Y.Z.")
    commands.add_parser("notes", help="Print the release notes.")
    push = commands.add_parser("publish", help="Create or update the release.")
    push.add_argument("assets", nargs="+", type=Path)
    push.add_argument("--prerelease", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    version = read_version()
    if args.command == "version":
        print(release_version(version))
    elif args.command == "notes":
        release = release_version(version)
        print(build_notes(version, commit_subjects(previous_tag(release))), end="")
    else:
        missing = [str(asset) for asset in args.assets if not asset.is_file()]
        if missing:
            print(f"missing assets: {', '.join(missing)}", file=sys.stderr)
            return 2
        print(publish(args.assets, prerelease=args.prerelease))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
