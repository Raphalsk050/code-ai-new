from __future__ import annotations

import asyncio
from pathlib import Path

# Git calls here are on the turn's critical path, so they are short-fused. A
# baseline that takes longer than this to capture is not worth the latency: the
# turn proceeds without one and the planner's ledger remains the only account of
# what changed.
_GIT_TIMEOUT_S = 10.0


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

    async def _git(self, *args: str) -> str | None:
        """Run a git command in the workspace. ``None`` means "no answer"."""

        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self._workspace),
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (OSError, ValueError):
            # git missing, or a workspace path the OS refuses - neither is worth
            # failing a turn over.
            return None
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=_GIT_TIMEOUT_S
            )
        except TimeoutError:
            process.kill()
            # Reap the killed child so it does not linger as a zombie.
            await process.wait()
            return None
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            return None
        return stdout.decode("utf-8", errors="replace")
