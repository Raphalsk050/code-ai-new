from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from code_ai.core.errors import CancellationError, CommandTimeoutError
from code_ai.core.policies import Deadline

T = TypeVar("T")


async def wait_with_deadline(awaitable: Awaitable[T], deadline: Deadline) -> T:
    try:
        return await asyncio.wait_for(awaitable, timeout=deadline.remaining())
    except TimeoutError as exc:
        raise CommandTimeoutError("Operation exceeded its deadline.") from exc


def raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event and cancel_event.is_set():
        raise CancellationError("Operation cancelled.")
