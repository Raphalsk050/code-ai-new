from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# Who is sitting at this machine, worked out from what is already on it. The
# alternative - having the model go looking - is slower, unreliable, and reads
# far more of the user's filesystem than the question warrants. These sources
# are the ones a developer has already filled in on purpose.

_GIT_TIMEOUT_S = 5.0

# Account names are not names. Anything that looks like a login (no space, all
# lowercase, digits, dots) is rejected rather than greeted.
_LOOKS_LIKE_A_NAME = re.compile(r"^[^\W\d_][^\d_]*(?: [^\W\d_][^\d_]*)+$", re.UNICODE)

# Placeholders people leave in a global git config.
_PLACEHOLDERS = frozenset(
    {"your name", "user", "unknown", "admin", "administrator", "root", "none"}
)


def _plausible(candidate: str) -> str | None:
    """A candidate that reads as a person's name, cleaned up, else ``None``.

    Deliberately strict. Greeting someone by the wrong name is worse than not
    greeting them by name at all, and a login like ``rapha`` or ``dev01`` is a
    wrong name rather than a partial one.
    """

    cleaned = " ".join((candidate or "").split())
    if not cleaned or len(cleaned) > 60:
        return None
    if cleaned.casefold() in _PLACEHOLDERS:
        return None
    if not _LOOKS_LIKE_A_NAME.match(cleaned):
        return None
    return cleaned


def _git_user_name(workspace: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "config", "--get", "user.name"],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _plausible(result.stdout.decode("utf-8", errors="replace"))


def _full_name_from_os() -> str | None:
    """The account's display name, which on Windows is often the real name."""

    # ``FULLNAME``/``NAME`` are set by some shells and desktop environments; the
    # bare login names are checked through the same plausibility gate, so a
    # single-token account name is rejected rather than used as a first name.
    for variable in ("CODE_AI_USER_NAME", "FULLNAME", "NAME", "USERNAME", "USER"):
        candidate = _plausible(os.environ.get(variable, ""))
        if candidate:
            return candidate
    return None


def detect_user_name(workspace: Path) -> str | None:
    """The user's name if the machine already knows it, most reliable source first.

    ``git config user.name`` comes first because it is the one place a developer
    has deliberately typed their real name, and it is the name their commits
    already carry.
    """

    return _git_user_name(workspace) or _full_name_from_os()
