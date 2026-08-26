from __future__ import annotations

import json
import os
import platform
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from code_ai.sandbox.artifacts import ArtifactRecorder
from code_ai.sandbox.layout import (
    MARKER_FILENAME,
    MARKER_KIND,
    PROJECT_LINK_NAME,
    SandboxLayout,
)
from code_ai.sandbox.runtimes import DEFAULT_RUNTIMES, LanguageRuntime, build_runtime_scratch
from code_ai.util.fileio import RetryPolicy, remove_tree
from code_ai.util.paths import WorkspacePolicy

# Owner-only: a sandbox holds whatever a build produced, which on a shared host
# is nobody else's business.
_DIRECTORY_MODE = 0o700

# Cleanup is best-effort and runs at shutdown, so it waits briefly for a
# lingering build process rather than either failing or delaying the exit.
_CLEANUP_POLICY = RetryPolicy(attempts=3, initial_delay_s=0.05, max_delay_s=0.2)


class SessionSandbox:
    """One session's isolated place to build, run and scribble.

    The sandbox exists so that working on a project never changes the project
    by accident. Everything a task produces incidentally - compiled output,
    generated scripts, temporary files, captured test logs - lands here, and
    the user's tree only ever changes through a deliberate source edit.

    Containment is enforced by the same path policy the workspace uses, rooted
    at the sandbox instead: a path that resolves outside is refused, including
    one that tries to leave through the ``project`` symlink. That link is there
    so a command running in the sandbox can still read the real sources, which
    is the read-only half of the arrangement.
    """

    def __init__(
        self,
        layout: SandboxLayout,
        *,
        workspace: Path,
        session_id: str,
        runtimes: Iterable[LanguageRuntime] = DEFAULT_RUNTIMES,
        max_artifact_bytes: int = 2_000_000,
    ) -> None:
        self._layout = layout
        self._workspace = workspace
        self._session_id = session_id
        self._runtimes = tuple(runtimes)
        self._policy = WorkspacePolicy.from_path(layout.root)
        self._artifacts = ArtifactRecorder(layout.artifacts, max_bytes=max_artifact_bytes)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        workspace: Path,
        base_dir: Path,
        runtimes: Iterable[LanguageRuntime] = DEFAULT_RUNTIMES,
        max_artifact_bytes: int = 2_000_000,
    ) -> SessionSandbox:
        """Materialise the sandbox on disk and return a handle to it."""

        base = Path(base_dir).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        # Resolve after creating: on macOS the temp dir is reached through a
        # symlink, and an unresolved root would never match the resolved paths
        # the path policy hands back, breaking every containment check.
        layout = SandboxLayout.under(base.resolve(), session_id)
        for directory in layout.directories():
            directory.mkdir(parents=True, exist_ok=True)
        try:
            layout.root.chmod(_DIRECTORY_MODE)
        except OSError:
            # Best effort: a filesystem without POSIX modes still isolates by path.
            pass

        resolved_workspace = Path(workspace).expanduser().resolve()
        sandbox = cls(
            layout,
            workspace=resolved_workspace,
            session_id=session_id,
            runtimes=runtimes,
            max_artifact_bytes=max_artifact_bytes,
        )
        sandbox._write_marker()
        sandbox._link_project()
        sandbox._prepare_runtime_directories()
        return sandbox

    @property
    def layout(self) -> SandboxLayout:
        return self._layout

    @property
    def root(self) -> Path:
        return self._layout.root

    @property
    def policy(self) -> WorkspacePolicy:
        """Path boundary for tools writing into the sandbox."""

        return self._policy

    @property
    def artifacts(self) -> ArtifactRecorder:
        return self._artifacts

    @property
    def workspace(self) -> Path:
        return self._workspace

    def workdir(self, requested: str | Path | None = None) -> Path:
        """Working directory for a command run inside the sandbox.

        Unqualified, that is ``work/`` rather than the sandbox root, so a
        command's own output never sits next to the marker and the captured
        run logs.
        """

        if requested is None or (isinstance(requested, str) and not requested.strip()):
            return self._layout.work
        return self._policy.relative_workdir(requested)

    def resolve(self, path: str | Path, *, must_exist: bool = False) -> Path:
        return self._policy.resolve(path, must_exist=must_exist)

    def relative(self, path: Path) -> str:
        """Sandbox-relative form of ``path``, for reporting it back to the agent."""

        return path.relative_to(self._layout.root).as_posix()

    def environment(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """Environment entries that keep a toolchain's own writes in the sandbox.

        Applied to every command the agent runs, including the ones working in
        the project: a test run belongs in the project directory, its cache
        does not.
        """

        return dict(build_runtime_scratch(self._layout, base or {}, self._runtimes).variables)

    def describe(self) -> dict[str, Any]:
        return {
            "session_id": self._session_id,
            "root": str(self._layout.root),
            "work": str(self._layout.work),
            "artifacts": str(self._layout.artifacts),
            "project_link": str(self._layout.project_link),
            "workspace": str(self._workspace),
        }

    def cleanup(self) -> bool:
        """Remove this sandbox. Returns whether anything was deleted.

        Refuses to touch a directory that does not carry our marker, so a
        misconfigured base directory pointed at something real is a no-op
        instead of a catastrophe.
        """

        return remove_sandbox(self._layout.root)

    def _write_marker(self) -> None:
        payload = {
            "kind": MARKER_KIND,
            "session_id": self._session_id,
            "workspace": str(self._workspace),
            "created_at": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
            "hostname": platform.node(),
        }
        self._layout.marker.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _link_project(self) -> None:
        """Point a symlink at the project so sandboxed commands can read it.

        Best effort by design: Windows without developer mode refuses to create
        one, and a sandbox without the link is still a working sandbox - the
        agent simply reaches project files through the workspace tools instead.
        """

        link = self._layout.project_link
        try:
            if link.is_symlink() or link.exists():
                return
            link.symlink_to(self._workspace, target_is_directory=True)
        except OSError:
            pass

    def _prepare_runtime_directories(self) -> None:
        scratch = build_runtime_scratch(self._layout, dict(os.environ), self._runtimes)
        for directory in scratch.directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError:
                # A toolchain that cannot use its redirected cache falls back to
                # its own default; that is a slower build, not a broken session.
                continue


def is_sandbox_root(path: Path) -> bool:
    """Whether ``path`` is a directory this agent created as a sandbox."""

    try:
        if not path.is_dir() or path.is_symlink():
            return False
        payload = json.loads((path / MARKER_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("kind") == MARKER_KIND


def read_marker(path: Path) -> dict[str, Any] | None:
    """Parsed ownership marker of a sandbox root, or ``None`` if it is not one."""

    if not is_sandbox_root(path):
        return None
    try:
        payload = json.loads((path / MARKER_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def remove_sandbox(path: Path) -> bool:
    """Delete a sandbox root, guarding against deleting anything else.

    The project is reachable from inside a sandbox through a symlink, so the
    link is unlinked explicitly before the tree is removed. ``rmtree`` would not
    follow it either, but this is the one operation in the feature that could
    destroy the user's work, and it should not rest on a library detail.

    Removal is retried: on Windows a build directory a compiler or dev server
    still holds cannot be deleted on the first ask, and read-only flags on
    build output stop it outright until they are cleared.
    """

    if not is_sandbox_root(path):
        return False
    link = path / PROJECT_LINK_NAME
    try:
        if link.is_symlink():
            link.unlink()
    except OSError:
        pass
    return remove_tree(path, policy=_CLEANUP_POLICY)
