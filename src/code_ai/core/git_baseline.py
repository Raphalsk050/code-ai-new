from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

# Git calls here are on the turn's critical path, so they are short-fused. A
# baseline that takes longer than this to capture is not worth the latency: the
# turn proceeds without one and the planner's ledger remains the only account of
# what changed.
_GIT_TIMEOUT_S = 10.0

# Keep git from flashing a console window when the host is a GUI process (the
# VSCode extension spawns the bridge as one). Zero elsewhere, where the flag
# does not exist.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GitBaseline:
    """Records the working tree as it was when a turn started.

    The planner's evidence ledger knows which paths the *tools* reported
    touching. Git knows which paths actually differ from the tree as it stood
    before the turn - including edits made through the shell, which no tool
    reported, and excluding writes that put back exactly what was already there.
    Where the two disagree, git is the fact and the ledger is a claim.

    Every operation fails open: outside a repository, without git installed, or
    on any error, the baseline is simply unavailable and callers fall back to
    what they had before.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._baseline: str | None = None
        # Files already untracked when the turn began. Without this they would
        # every one of them read as "changed by this turn", forever.
        self._untracked_at_baseline: frozenset[str] = frozenset()

    @property
    def available(self) -> bool:
        return self._baseline is not None

    async def capture(self) -> None:
        """Snapshot the current tree. Safe to call at the start of every turn."""

        self._baseline = None
        self._untracked_at_baseline = frozenset()
        # ``stash create`` builds a commit object for the working tree without
        # touching the stash, the index, or the tree itself. It returns nothing
        # when there is nothing uncommitted, in which case HEAD already is the
        # state we want to compare against.
        sha = await self._git("stash", "create")
        if sha is None:
            return
        baseline = sha.strip() or await self._git_line("rev-parse", "HEAD")
        if not baseline:
            return
        self._baseline = baseline
        self._untracked_at_baseline = await self._untracked()

    async def changed_paths(self) -> tuple[str, ...]:
        """Workspace-relative paths that differ from the baseline, sorted."""

        if self._baseline is None:
            return ()
        tracked = await self._git("diff", "--name-only", self._baseline)
        if tracked is None:
            return ()
        changed = {line.strip() for line in tracked.splitlines() if line.strip()}
        changed |= set(await self._untracked()) - self._untracked_at_baseline
        return tuple(sorted(changed))

    async def _untracked(self) -> frozenset[str]:
        out = await self._git("ls-files", "--others", "--exclude-standard")
        if out is None:
            return frozenset()
        return frozenset(line.strip() for line in out.splitlines() if line.strip())

    async def _git_line(self, *args: str) -> str:
        out = await self._git(*args)
        return "" if out is None else out.strip()

    def _git_blocking(self, args: tuple[str, ...]) -> str | None:
        """The actual git call, on a worker thread. ``None`` means "no answer"."""

        try:
            done = subprocess.run(
                ["git", "-C", str(self._workspace), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=_GIT_TIMEOUT_S,
                creationflags=_NO_WINDOW,
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # git missing, a workspace path the OS refuses, or a call that
            # overran its fuse - none is worth failing a turn over.
            return None
        if done.returncode != 0:
            return None
        return done.stdout.decode("utf-8", errors="replace")

    async def _git(self, *args: str) -> str | None:
        """Run a git command in the workspace, off the event loop.

        A thread rather than ``asyncio.create_subprocess_exec``: on Windows the
        proactor loop's ``communicate()`` did not complete for these calls
        inside the running app, so every turn paid the full ``_GIT_TIMEOUT_S``
        before the model was even asked - ten silent seconds, for a command that
        takes 0.1s from a shell. Measured on both a cold and a warm session.
        """

        return await asyncio.to_thread(self._git_blocking, args)
