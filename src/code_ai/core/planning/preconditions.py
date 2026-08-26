from __future__ import annotations

from pathlib import Path

# Tools whose calls rewrite workspace content and therefore need their target's
# current state to be known before they run.
_MUTATION_TOOLS = frozenset({"write_file", "edit_code"})

# A single write_file above either bound is a monolith, not an increment. The
# thresholds are deliberately generous: a focused module or class skeleton fits
# comfortably, while "the whole project file in one shot" does not.
_INCREMENTAL_WRITE_MAX_LINES = 100
_INCREMENTAL_WRITE_MAX_CHARS = 5000


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
        self._delegation_nudged = False
        self._artifact_nudged = False
        self._oversized_nudged_paths: set[str] = set()

    def note_turn_started(self) -> None:
        """Reset the per-turn nudges when a fresh user request begins.

        The unrequested-artifact nudge is scoped to one request: each turn
        carries its own intent, so a nudge spent in the previous question must
        not silently wave through a file creation in the next one. Path and
        delegation nudges stay session-scoped on purpose - their evidence basis
        (what was read, what was explored) also spans the session.
        """
        self._artifact_nudged = False

    def unrequested_artifact_gap(
        self,
        tool_name: str,
        arguments: dict[str, object],
        *,
        task_requests_mutation: bool,
    ) -> str | None:
        """Reason to defer a file write the user's request never asked for.

        The classic failure at the *end* of a read-only task: told that prose
        does not complete tasks, the model manufactures a deliverable - an
        unrequested notes/summary/analysis document - just to have completion
        evidence. Fires exactly at the write attempt (before any approval
        prompt reaches the user), once per turn, then fails open so a
        misclassified genuine change costs one round-trip at most.
        """
        if task_requests_mutation or tool_name not in _MUTATION_TOOLS:
            return None
        if self._artifact_nudged:
            return None
        self._artifact_nudged = True
        path = str(arguments.get("path") or "").strip()
        target = f" on '{path}'" if path else ""
        return (
            "Precondition check: this task was classified as read-only (a "
            f"question or analysis), yet you are calling {tool_name}{target}. "
            "Answering a question never requires creating or editing files: put "
            "your findings directly in your chat answer instead of writing "
            "notes, summaries, or report documents the user never asked for. "
            "If the user's request genuinely requires this exact file change, "
            "retry the call and it will proceed."
        )

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

    def oversized_write_gap(
        self, tool_name: str, arguments: dict[str, object]
    ) -> str | None:
        """Reason to defer a single write that dumps a whole file at once.

        The classic failure on greenfield work: asked for a project, the model
        emits the complete class or module in one giant ``write_file``. That
        monolith skips the incremental loop that produces quality - write a
        skeleton, extend one behavior at a time, verify as you go - and it is
        also where truncated output and unreviewable changes come from. Nudges
        once per path, then fails open, so a file that genuinely must be
        written in one piece (fixtures, generated data) costs one retry at most.
        """
        if tool_name != "write_file":
            return None
        content = arguments.get("content")
        if not isinstance(content, str):
            return None
        lines = content.count("\n") + 1
        if lines <= _INCREMENTAL_WRITE_MAX_LINES and len(content) <= _INCREMENTAL_WRITE_MAX_CHARS:
            return None
        path = str(arguments.get("path") or "").strip()
        if path in self._oversized_nudged_paths:
            return None
        self._oversized_nudged_paths.add(path)
        target = f" to '{path}'" if path else ""
        return (
            f"Precondition check: this single write_file{target} carries "
            f"{lines} lines ({len(content)} characters) at once. Work "
            "incrementally instead: write a minimal skeleton first (imports, "
            "signatures, docstrings), then add one focused piece at a time with "
            "edit_code, verifying as you go. Small steps keep each change "
            "reviewable and thought through, and avoid truncated output. If "
            "this file genuinely must be written in one piece, retry the call "
            "and it will proceed."
        )

    def blind_delegation_gap(
        self,
        arguments: dict[str, object],
        *,
        has_local_grounding: bool,
        write_agent_types: frozenset[str],
    ) -> str | None:
        """Reason to defer delegating implementation before any reconnaissance.

        A write-capable sub-agent only knows what its prompt says - it cannot
        see the conversation or ask questions. Dispatching one before the
        orchestrator has read or searched anything means the prompt is built
        from assumptions, and the sub-agent will faithfully implement those
        assumptions. Read-only (explorer/reviewer) delegations are never gated:
        fanning out explorers *is* the reconnaissance. Nudges once per session,
        then fails open.
        """
        if self._delegation_nudged or has_local_grounding or not write_agent_types:
            return None
        blind_types = self._requested_write_agent_types(arguments, write_agent_types)
        if not blind_types:
            return None
        self._delegation_nudged = True
        return (
            "Precondition check: you are delegating implementation to "
            f"write-capable sub-agent(s) ({', '.join(blind_types)}) before "
            "gathering any evidence about this workspace. A sub-agent only "
            "knows what its prompt says, so a prompt written from assumptions "
            "produces work built on assumptions. First investigate - read or "
            "search the relevant code yourself, or fan out read-only explorer "
            "agents - then delegate with a prompt grounded in real paths and "
            "findings, stating the expected outcome. If the task genuinely "
            "needs no local context, simply retry and the dispatch will proceed."
        )

    @staticmethod
    def _requested_write_agent_types(
        arguments: dict[str, object], write_agent_types: frozenset[str]
    ) -> list[str]:
        tasks = arguments.get("tasks")
        if not isinstance(tasks, list):
            return []
        requested = {
            str(task.get("agent_type") or "").strip().lower()
            for task in tasks
            if isinstance(task, dict)
        }
        return sorted(requested & write_agent_types)

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
