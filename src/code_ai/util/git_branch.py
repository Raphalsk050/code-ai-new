"""The git branch a workspace is on, read straight from .git: no git process.

Polled while a session runs, so it has to cost one small file read. A
workspace inside a repository finds it by walking up; a worktree's (or a
submodule's) .git is a file naming the real git directory.
"""

from __future__ import annotations

from pathlib import Path


def git_dir(workspace: Path) -> Path | None:
    folder = Path(workspace).resolve()
    for candidate in (folder, *folder.parents):
        dot_git = candidate / ".git"
        if dot_git.is_dir():
            return dot_git
        if dot_git.is_file():
            try:
                text = dot_git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            target = Path(text.removeprefix("gitdir:").strip())
            return target if target.is_absolute() else (candidate / target).resolve()
    return None


def current_branch(workspace: Path) -> str:
    """The branch name; "detached at <sha>" off any branch; "" outside a repository."""
    directory = git_dir(workspace)
    if directory is None:
        return ""
    try:
        head = (directory / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        return head.removeprefix("ref:").strip().removeprefix("refs/heads/")
    return f"detached at {head[:7]}" if head else ""
