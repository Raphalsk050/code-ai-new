from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CommandKind(StrEnum):
    """What a verification command actually exercises, most-trusted first."""

    TEST = "test"
    BUILD = "build"
    TYPECHECK = "typecheck"
    LINT = "lint"


# Lower sorts first: a real test run is stronger evidence than a build, which is
# stronger than a type check, which is stronger than a lint.
KIND_PRIORITY: dict[CommandKind, int] = {
    CommandKind.TEST: 0,
    CommandKind.BUILD: 1,
    CommandKind.TYPECHECK: 2,
    CommandKind.LINT: 3,
}


@dataclass(frozen=True, slots=True)
class VerificationCommand:
    """A concrete command the project exposes to check that code works."""

    kind: CommandKind
    argv: tuple[str, ...]
    description: str
    source: str  # the manifest/file that revealed it, e.g. "pyproject.toml"

    @property
    def display(self) -> str:
        return " ".join(self.argv)


@dataclass(frozen=True, slots=True)
class ProjectVerification:
    """The verification capabilities detected for a workspace.

    ``commands`` is ordered by trust (test before build before typecheck before
    lint). ``ecosystems`` lists the toolchains that were recognised so the model
    can be told what the project actually uses.
    """

    commands: tuple[VerificationCommand, ...] = ()
    ecosystems: tuple[str, ...] = field(default=())

    @property
    def has_any(self) -> bool:
        return bool(self.commands)

    def primary(self) -> VerificationCommand | None:
        return self.commands[0] if self.commands else None

    def prompt_hint(self) -> str:
        """A short, human-readable description for the runtime task context."""
        if not self.commands:
            return (
                "No test/build system was detected in this project, so there is no "
                "automated check to run. Confirm the change is correct by reading it "
                "back, then complete with a clear summary noting verification was not "
                "available."
            )
        listed = "; ".join(
            f"`{cmd.display}` ({cmd.kind.value}, from {cmd.source})"
            for cmd in self.commands[:4]
        )
        return (
            "Verification commands detected for this project: "
            f"{listed}. Run the one that exercises your change (prefer the test "
            "command) before completing. A trivial command does not count."
        )
