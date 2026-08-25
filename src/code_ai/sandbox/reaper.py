from __future__ import annotations

import contextlib
import os
import platform
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from code_ai.sandbox.session import read_marker, remove_sandbox


def _parse_created_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    with contextlib.suppress(ValueError):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _process_is_alive(pid: object, hostname: object) -> bool | None:
    """Whether the session that owns a sandbox is still running.

    ``None`` means "cannot tell", which is the answer whenever the sandbox was
    written by a different machine or on a platform where a liveness probe is
    not portable. An unknown answer never justifies deleting anything - the TTL
    decides those.
    """

    if not isinstance(pid, int) or pid <= 0:
        return None
    if hostname != platform.node() or os.name != "posix":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The pid exists, it just is not ours to signal.
        return True
    except OSError:
        return None
    return True


@dataclass(frozen=True, slots=True)
class SandboxReaper:
    """Removes sandboxes left behind by sessions that never cleaned up.

    A crash, a kill -9 or a machine losing power all end a session without its
    own cleanup running. Sweeping at startup keeps that from accumulating,
    while the ownership marker keeps the sweep from ever touching a directory
    this agent did not create.
    """

    base: Path
    ttl: timedelta

    def sweep(self, *, keep: Path | None = None, now: datetime | None = None) -> list[Path]:
        """Delete every expired sandbox under the base. Returns what was removed."""

        moment = now or datetime.now(UTC)
        kept = keep.resolve() if keep is not None else None
        removed: list[Path] = []
        try:
            entries = sorted(self.base.iterdir())
        except OSError:
            # No base directory yet, or one we cannot read: nothing to reap.
            return removed
        for entry in entries:
            try:
                if kept is not None and entry.resolve() == kept:
                    continue
                if not self._is_expired(entry, moment):
                    continue
                if remove_sandbox(entry):
                    removed.append(entry)
            except OSError:
                # One unreadable entry must not abort the sweep.
                continue
        return removed

    def _is_expired(self, entry: Path, now: datetime) -> bool:
        marker = read_marker(entry)
        if marker is None:
            return False
        if _process_is_alive(marker.get("pid"), marker.get("hostname")) is False:
            # The owning session is gone, so nothing will ever write here again.
            return True
        created_at = _parse_created_at(marker.get("created_at"))
        if created_at is None:
            created_at = datetime.fromtimestamp(entry.stat().st_mtime, UTC)
        return now - created_at > self.ttl
