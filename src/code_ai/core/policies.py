from __future__ import annotations

import time
from dataclasses import dataclass

from code_ai.core.errors import CommandTimeoutError


@dataclass(slots=True)
class Deadline:
    expires_at: float

    @classmethod
    def after(cls, seconds: float) -> Deadline:
        return cls(time.monotonic() + max(0.0, seconds))

    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def ensure_time(self) -> None:
        if self.remaining() <= 0:
            raise CommandTimeoutError("Deadline exceeded.")

    def clip(self, requested: float | None) -> float:
        remaining = self.remaining()
        if requested is None:
            return remaining
        return max(0.0, min(float(requested), remaining))
