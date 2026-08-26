from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from code_ai.tools.skills.common import SKILL_ENTRYPOINT, skills_root
from code_ai.util.fileio import RetryPolicy, atomic_write_text

# Seeding runs at startup, before any configuration is in hand, so it uses
# the built-in retry defaults rather than the user's file_io section.
_POLICY = RetryPolicy()

# Bumping this re-arms the one-time seed for installs that already ran an older
# bundle: a marker written by a previous version no longer matches, so any
# default skill the user has not created yet is added on the next startup.
BUNDLE_VERSION = "1"

# Records that the default skills were seeded, so later runs do not re-add a
# skill the user has since deleted. Lives beside the skills, hidden from listing.
SEED_MARKER_NAME = ".defaults-seeded"

_BUNDLE_PACKAGE = "code_ai.assets"
_BUNDLE_DIRNAME = "default_skills"


def _bundle_root() -> Traversable:
    return resources.files(_BUNDLE_PACKAGE) / _BUNDLE_DIRNAME


def bundled_default_skills() -> list[Traversable]:
    """Return the bundled skill directories shipped with the package.

    Each entry is a ``<name>/`` directory containing a ``SKILL.md``. Works both
    from a source checkout and an installed wheel via ``importlib.resources``.
    """

    root = _bundle_root()
    if not root.is_dir():
        return []
    return sorted(
        (child for child in root.iterdir() if child.is_dir()),
        key=lambda c: c.name.casefold(),
    )


def seed_default_skills(root: Path | None = None) -> list[str]:
    """Materialise the bundled default skills into the user's skills directory.

    Runs at most once per install (guarded by :data:`SEED_MARKER_NAME`): on first
    start every bundled skill the user does not already have is copied in. The
    marker means a skill the user later deletes is not silently recreated, and an
    existing skill of the same name is never overwritten. Best-effort: any error
    is swallowed so seeding can never block agent startup.
    """

    try:
        base = root or skills_root()
        marker = base / SEED_MARKER_NAME
        if marker.exists():
            return []

        base.mkdir(parents=True, exist_ok=True)
        seeded: list[str] = []
        for skill_dir in bundled_default_skills():
            name = skill_dir.name
            entry = skill_dir / SKILL_ENTRYPOINT
            if not entry.is_file():
                continue
            dest_dir = base / name
            dest = dest_dir / SKILL_ENTRYPOINT
            if dest.exists():
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(dest, entry.read_text(encoding="utf-8"), policy=_POLICY)
            seeded.append(name)

        atomic_write_text(marker, BUNDLE_VERSION + "\n", policy=_POLICY)
        return seeded
    except Exception:  # never let seeding break startup
        return []

