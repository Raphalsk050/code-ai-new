"""The project's version, and the only place allowed to change it.

``version.json`` next to this module is the single source of truth; every other
place that wants a version reads it from here. It is shaped ``X.Y.Z-W``:

* ``X`` (major) moves **only when the user says so**. Nothing in this module
  bumps it on its own - ``bump("major", ...)`` refuses without ``allow_major``.
* ``Y`` (minor) moves when a release adds a capability; ``Z`` goes back to 0.
* ``Z`` (patch) moves for a fix, a correction, a cleanup. This is the default:
  reach for ``Z`` first and for ``Y`` only when something genuinely new landed.
* ``W`` (build) counts finished pieces of work. It moves on **every** bump,
  whatever kind, and never resets, so it orders builds on its own.

``X.Y.Z`` is what packaging sees (PEP 440 has no room for the ``-W`` suffix);
``X.Y.Z-W`` is what a human is shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

__all__ = [
    "VersionInfo",
    "__version__",
    "bump",
    "full_version",
    "read_version",
    "write_version",
]

VERSION_FILE = Path(__file__).with_name("version.json")

_KINDS = ("major", "minor", "patch")


@dataclass(frozen=True, slots=True)
class VersionInfo:
    major: int
    minor: int
    patch: int
    build: int
    updated: str = ""
    note: str = ""

    @property
    def release(self) -> str:
        """``X.Y.Z`` - what packaging metadata accepts."""

        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def full(self) -> str:
        """``X.Y.Z-W`` - what the user is shown."""

        return f"{self.release}-{self.build}"

    def __str__(self) -> str:
        return self.full

    def to_dict(self) -> dict[str, object]:
        return {
            "major": self.major,
            "minor": self.minor,
            "patch": self.patch,
            "build": self.build,
            "updated": self.updated,
            "note": self.note,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, object]) -> VersionInfo:
        def number(key: str) -> int:
            value = data.get(key, 0)
            try:
                number = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"version.json: {key} must be a whole number") from exc
            if number < 0:
                raise ValueError(f"version.json: {key} cannot be negative")
            return number

        return cls(
            major=number("major"),
            minor=number("minor"),
            patch=number("patch"),
            build=number("build"),
            updated=str(data.get("updated") or ""),
            note=str(data.get("note") or ""),
        )


def read_version(path: Path | None = None) -> VersionInfo:
    """The current version.

    A missing or unreadable file is not worth taking the program down for -
    a frozen build that lost its data file should still start - so it degrades
    to 0.0.0-0 rather than raising. A file that is present but malformed does
    raise: that is a mistake in the repo, and hiding it would ship a wrong
    version number.
    """

    target = path or VERSION_FILE
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return VersionInfo(0, 0, 0, 0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{target} must hold a JSON object")
    return VersionInfo.from_mapping(data)


def write_version(info: VersionInfo, path: Path | None = None) -> None:
    target = path or VERSION_FILE
    target.write_text(
        json.dumps(info.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def bump(
    kind: str,
    note: str,
    *,
    path: Path | None = None,
    allow_major: bool = False,
    today: date | None = None,
) -> VersionInfo:
    """Raise the version by one ``kind`` and write it back.

    ``note`` is one line saying what this build is for; it is what the file is
    worth reading for later. ``allow_major`` is the gate on ``X``: it exists so
    a major bump cannot happen by habit or by a typo in the kind, only because
    the user asked for one in so many words.
    """

    if kind not in _KINDS:
        raise ValueError(f"Unknown bump kind: {kind!r}. Use one of {', '.join(_KINDS)}.")
    if not note.strip():
        raise ValueError("A bump needs a one-line note saying what it is for.")
    if kind == "major" and not allow_major:
        raise ValueError(
            "A major bump needs allow_major=True: X only moves when the user asks for it."
        )

    current = read_version(path)
    if kind == "major":
        updated = replace(current, major=current.major + 1, minor=0, patch=0)
    elif kind == "minor":
        updated = replace(current, minor=current.minor + 1, patch=0)
    else:
        updated = replace(current, patch=current.patch + 1)
    updated = replace(
        updated,
        build=current.build + 1,
        updated=(today or date.today()).isoformat(),
        note=note.strip(),
    )
    write_version(updated, path)
    return updated


def full_version() -> str:
    """``X.Y.Z-W``, for anything shown to a person."""

    return read_version().full


__version__ = read_version().release
