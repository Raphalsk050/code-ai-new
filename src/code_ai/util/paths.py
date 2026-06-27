from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code_ai.core.errors import WorkspaceBoundaryError


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    """Resolves file paths against one canonical workspace boundary."""

    root: Path

    @classmethod
    def from_path(cls, workspace: Path | str) -> WorkspacePolicy:
        root = Path(workspace).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise WorkspaceBoundaryError(f"Workspace is not a directory: {root}")
        return cls(root=root)

    def resolve(self, requested: str | Path, *, must_exist: bool = False) -> Path:
        raw = str(requested)
        if "\x00" in raw:
            raise WorkspaceBoundaryError("Path contains a NUL byte.")
        path = Path(raw).expanduser()
        candidate = path if path.is_absolute() else self.root / path

        if candidate.exists():
            resolved = candidate.resolve(strict=True)
            self._ensure_inside(resolved)
            return resolved

        if must_exist:
            raise WorkspaceBoundaryError(f"Path does not exist: {requested}")

        missing_parts: list[str] = []
        parent = candidate
        while not parent.exists():
            if parent.name in {"", ".", ".."}:
                raise WorkspaceBoundaryError(f"Unsafe path: {requested}")
            missing_parts.append(parent.name)
            parent = parent.parent

        resolved_parent = parent.resolve(strict=True)
        self._ensure_inside(resolved_parent)
        resolved = resolved_parent.joinpath(*reversed(missing_parts))
        normalized = resolved.resolve(strict=False)
        self._ensure_inside(normalized)
        return normalized

    def relative_workdir(self, requested: str | Path | None) -> Path:
        if requested is None:
            return self.root
        # Models routinely fill a nullable cwd with a stringified sentinel
        # ("None"/"null") instead of omitting it; treat those as "use the root"
        # rather than looking for a directory literally named that.
        if isinstance(requested, str) and requested.strip().lower() in {
            "",
            ".",
            "none",
            "null",
        }:
            return self.root
        path = self.resolve(requested, must_exist=True)
        if not path.is_dir():
            raise WorkspaceBoundaryError(f"Working directory is not a directory: {requested}")
        return path

    def _ensure_inside(self, path: Path) -> None:
        if path != self.root and not _is_relative_to(path, self.root):
            raise WorkspaceBoundaryError(f"Path escapes workspace: {path}")
