from __future__ import annotations

from pathlib import Path

# Tools whose calls rewrite workspace content and therefore need their target's
# current state to be known before they run.
_MUTATION_TOOLS = frozenset({"write_file", "edit_code"})


class PreconditionGate:
    """Advisory evidence checks that run before an action-taking tool call.

    The gate never blocks work outright - every check nudges exactly once and
    then fails open, mirroring the planner's advisory-policy philosophy: a wrong
    heuristic must cost one corrective round-trip at most, never trap the agent.
    What it buys is precision: the model is pushed to ground a mutation in the
    file's actual current content instead of acting on assumptions.
    """

    def __init__(self, *, workspace: Path | None) -> None:
        self._workspace = workspace
        self._nudged_paths: set[str] = set()

    def unread_mutation_gap(
        self,
        tool_name: str,
        arguments: dict[str, object],
        *,
        known_content_paths: set[str],
    ) -> str | None:
        """Reason to defer a mutation of an existing file that was never read.

        Overwriting or editing a file whose current content the agent has not
        observed (in this session, by itself or a sub-agent) is the classic
        blind action: the change may clobber logic the request never mentioned.
        Returns a corrective instruction the first time per path, ``None`` when
        the mutation is grounded (file was read/written before), targets a new
        file, or the path was already nudged (fail-open).
        """
        if tool_name not in _MUTATION_TOOLS or self._workspace is None:
            return None
        raw_path = str(arguments.get("path") or "").strip()
        if not raw_path:
            return None
        located = self._locate(raw_path)
        if located is None:
            # Outside the workspace or unresolvable: the workspace boundary in
            # the tool itself is the authority there, not this gate.
            return None
        relative, absolute = located
        if not absolute.is_file():
            return None  # creating a new file needs no prior read
        if relative in known_content_paths:
            return None
        if relative in self._nudged_paths:
            return None
        self._nudged_paths.add(relative)
        return (
            f"Precondition check: {tool_name} targets the existing file "
            f"'{relative}', but its current content was never read in this "
            "session. Modifying a file blind risks destroying content the "
            "request never asked to change. Call read_file on it first, ground "
            "your change in what is actually there, then retry the mutation. "
            "If you genuinely already know its content, simply retry and the "
            "call will proceed."
        )

    def _locate(self, path_value: str) -> tuple[str, Path] | None:
        """Resolve a tool path argument to (workspace-relative posix, absolute).

        Returns ``None`` for paths that leave the workspace or cannot resolve;
        those are the workspace boundary's concern, not an evidence question.
        """
        try:
            root = self._workspace.expanduser().resolve()
            candidate = Path(path_value).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve(strict=False)
            return resolved.relative_to(root).as_posix(), resolved
        except (OSError, ValueError):
            return None
