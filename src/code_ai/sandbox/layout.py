from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# Written at the root of every sandbox we create. Two jobs: it records what the
# directory belongs to so a stale one can be reaped safely later, and it is the
# proof of ownership the reaper demands before deleting anything - a directory
# without it is somebody else's and is left alone.
MARKER_FILENAME = "sandbox.json"
MARKER_KIND = "python_agent_sandbox"

# Name of the symlink pointing back at the project. The sandbox is a working
# area, not a copy: commands running inside it reach the real sources through
# this link instead of a snapshot that would drift within a single turn.
PROJECT_LINK_NAME = "project"

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_ID_LENGTH = 64


def safe_session_id(session_id: str) -> str:
    """Turn a session id into a single, safe path segment.

    Session ids come from the event bus and are normally UUIDs, but nothing
    guarantees it: an embedding client is free to pass a label with slashes or
    non-ASCII in it. Anything outside a conservative alphabet is folded away,
    and an id that survives as empty falls back to a digest of the original so
    two different ids never collide on one directory.
    """

    cleaned = _UNSAFE_ID.sub("-", session_id.strip()).strip("-.")[:_MAX_ID_LENGTH]
    if cleaned:
        return cleaned
    return "session-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class SandboxLayout:
    """The fixed directory shape of one session's sandbox.

    Separating the areas is what makes the sandbox reportable: ``work`` is what
    the agent produced on purpose, ``artifacts`` is the record of what it ran,
    and ``tmp``/``cache`` are churn nobody needs to look at. Cleanup and TTL
    reaping treat the whole root as one unit regardless.
    """

    root: Path
    work: Path
    tmp: Path
    cache: Path
    artifacts: Path

    @classmethod
    def under(cls, base: Path, session_id: str) -> SandboxLayout:
        root = Path(base).expanduser() / safe_session_id(session_id)
        return cls(
            root=root,
            work=root / "work",
            tmp=root / "tmp",
            cache=root / "cache",
            artifacts=root / "artifacts",
        )

    def directories(self) -> tuple[Path, ...]:
        """Every directory that must exist for the sandbox to be usable."""

        return (self.root, self.work, self.tmp, self.cache, self.artifacts)

    @property
    def marker(self) -> Path:
        return self.root / MARKER_FILENAME

    @property
    def project_link(self) -> Path:
        return self.root / PROJECT_LINK_NAME
