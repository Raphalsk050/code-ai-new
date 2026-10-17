from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError, WorkspaceBoundaryError
from code_ai.tools.base import ToolContext
from code_ai.util.paths import WorkspacePolicy


class ToolLocation(StrEnum):
    """Which tree a file or command tool is acting on."""

    PROJECT = "project"
    SANDBOX = "sandbox"


# Values a model plausibly sends for "no location given". Mirrors the sentinels
# WorkspacePolicy.relative_workdir already tolerates for cwd.
_UNSET = {"", ".", "none", "null", "default"}

# Declared as a plain string rather than an enum: the tool schemas here stay
# atomic because weak local models handle enums badly, and the accepted values
# are enforced by resolve_location with a message that names them.
LOCATION_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": (
        "Which tree to act on, either 'project' or 'sandbox'. 'project' is the user's "
        "workspace and is where real source changes belong. 'sandbox' is this session's "
        "isolated scratch area for anything the project should not keep: generated "
        "scripts, throwaway experiments, build output, captured logs. Defaults to "
        "'project'."
    ),
}


def reaches_outside(root: Path, path: object) -> bool:
    """Whether ``path`` is an absolute one (or under ~) that lands outside ``root``."""
    if not isinstance(path, (str, Path)) or not str(path).strip() or "\x00" in str(path):
        return False
    candidate = Path(str(path)).expanduser()
    if not candidate.is_absolute():
        return False
    try:
        resolved = candidate.resolve(strict=False)
        base = root.expanduser().resolve(strict=False)
    except (OSError, ValueError):
        return False
    return resolved != base and not resolved.is_relative_to(base)


def resolve_location(
    value: object, *, default: ToolLocation = ToolLocation.PROJECT
) -> ToolLocation:
    """Read a ``location`` argument, tolerating the ways a model says "unset"."""

    if value is None:
        return default
    if isinstance(value, ToolLocation):
        return value
    if not isinstance(value, str):
        raise ToolArgumentError("location must be 'project' or 'sandbox'.")
    normalized = value.strip().lower()
    if normalized in _UNSET:
        return default
    try:
        return ToolLocation(normalized)
    except ValueError as exc:
        raise ToolArgumentError(
            f"Unsupported location: {value!r}. Use 'project' or 'sandbox'."
        ) from exc


@dataclass(frozen=True, slots=True)
class ResolvedLocation:
    """One tree a tool may act on, with its own boundary and default workdir.

    Both trees answer the same three questions - resolve a path, name it back
    relative to its root, pick a working directory - so a tool takes the
    location it was given and stops caring which one it is.
    """

    location: ToolLocation
    root: Path
    policy: WorkspacePolicy
    default_workdir: Path

    @property
    def is_sandbox(self) -> bool:
        return self.location is ToolLocation.SANDBOX

    def resolve(self, path: str | Path, *, must_exist: bool = False) -> Path:
        return self.policy.resolve(path, must_exist=must_exist)

    def resolve_reaching_out(self, path: str | Path, *, must_exist: bool = False) -> Path:
        """``resolve``, except that an absolute path outside the project is taken as given.

        How the file tools reach the rest of the machine: the user approves each
        call there (the orchestrator's outside-the-project gate), where before
        the model wrote such files through a shell. A relative path stays
        inside, so ".." or a symlink still cannot carry one out by accident,
        and the sandbox never reaches out at all.
        """
        if self.is_sandbox or not reaches_outside(self.root, path):
            return self.resolve(path, must_exist=must_exist)
        target = Path(str(path)).expanduser().resolve(strict=False)
        if must_exist and not target.exists():
            raise WorkspaceBoundaryError(f"Path does not exist: {path}")
        return target

    def relative(self, path: Path) -> str:
        """The path as the user reads it: from the root inside, whole outside."""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def workdir(self, requested: str | Path | None) -> Path:
        if requested is None:
            return self.default_workdir
        if isinstance(requested, str) and requested.strip().lower() in _UNSET:
            return self.default_workdir
        return self.policy.relative_workdir(requested)


def for_context(
    context: ToolContext,
    value: object = None,
    *,
    default: ToolLocation = ToolLocation.PROJECT,
) -> ResolvedLocation:
    """Pick the tree a tool call is addressing.

    Asking for the sandbox when the session has none is an explicit failure
    rather than a silent fallback to the project: the caller asked for
    isolation, and quietly writing into the user's tree instead is the exact
    accident the sandbox exists to prevent.
    """

    location = resolve_location(value, default=default)
    if location is ToolLocation.SANDBOX:
        sandbox = getattr(context, "sandbox", None)
        if sandbox is None:
            raise ToolExecutionError(
                "No sandbox is available in this session (it is disabled in the "
                "configuration). Use location 'project', or enable the sandbox."
            )
        return ResolvedLocation(
            location=location,
            root=sandbox.root,
            policy=sandbox.policy,
            default_workdir=sandbox.workdir(),
        )
    return ResolvedLocation(
        location=location,
        root=context.workspace.root,
        policy=context.workspace,
        default_workdir=context.workspace.root,
    )
