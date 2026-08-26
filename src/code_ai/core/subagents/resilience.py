from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

from code_ai.core.errors import CodeAIError

T = TypeVar("T")

# A retry predicate decides whether a raised exception is worth another attempt.
ShouldRetry = Callable[[BaseException], bool]
# Optional async hook invoked before each backoff sleep (attempt index, error).
OnRetry = Callable[[int, BaseException], Awaitable[None]]
# Injected clock/sleep so tests drive time deterministically instead of waiting.
TimeSource = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class OpenCircuitError(CodeAIError):
    """A guarded operation was refused because its circuit breaker is open."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff with jitter for a single async operation.

    This is the reusable extraction of the retry shape already used inline by the
    model step loop (:meth:`AgentOrchestrator._run_model_step`): capped attempts,
    ``base_delay * 2**n`` backoff clamped to ``max_delay``, plus a little jitter so
    concurrent retries do not stampede. ``max_attempts`` counts *total* tries, so
    ``max_attempts=1`` disables retrying.
    """

    max_attempts: int = 2
    base_delay: float = 0.25
    max_delay: float = 2.0
    jitter: float = 0.1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        if self.base_delay < 0 or self.max_delay < 0 or self.jitter < 0:
            raise ValueError("delays and jitter must be non-negative.")

    def _delay_for(self, attempt: int, rng: Callable[[], float]) -> float:
        # ``attempt`` is 1-based (the count of failures observed so far).
        backoff = self.base_delay * (2 ** (attempt - 1))
        return min(self.max_delay, backoff) + rng() * self.jitter

    async def run(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        should_retry: ShouldRetry,
        on_retry: OnRetry | None = None,
        sleep: Sleeper = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> T:
        """Run ``operation`` until it succeeds or attempts/retryability run out.

        The last exception is re-raised unchanged so the caller sees the real
        failure, never a wrapper that hides its type.
        """
        attempt = 0
        while True:
            try:
                return await operation()
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                attempt += 1
                if attempt >= self.max_attempts or not should_retry(exc):
                    raise
                if on_retry is not None:
                    await on_retry(attempt, exc)
                await sleep(self._delay_for(attempt, rng))

    async def until(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        accept: Callable[[T], bool],
        on_retry: Callable[[int, T], Awaitable[None]] | None = None,
        sleep: Sleeper = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> T:
        """Run ``operation`` until ``accept(result)`` or attempts run out.

        Result-predicate counterpart of :meth:`run` for operations that degrade
        their failures into values instead of raising. The last (still
        unaccepted) result is returned unchanged when attempts are exhausted, so
        the caller always gets a real outcome to act on. Exceptions are not
        handled here - an operation that can raise should degrade or use
        :meth:`run`.
        """
        attempt = 0
        while True:
            result = await operation()
            attempt += 1
            if attempt >= self.max_attempts or accept(result):
                return result
            if on_retry is not None:
                await on_retry(attempt, result)
            await sleep(self._delay_for(attempt, rng))


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class _CircuitEntry:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    # True while a half-open probe is in flight, so only one trial call is let
    # through until it resolves.
    probing: bool = False


@dataclass(slots=True)
class CircuitBreaker:
    """Per-key circuit breaker to stop hammering a repeatedly failing dependency.

    Keyed so each sub-agent profile trips independently: an ``explorer`` outage
    must not block ``coder`` dispatches. After ``failure_threshold`` consecutive
    failures a key opens and refuses calls for ``reset_timeout`` seconds, then
    admits a single half-open probe. The probe closing the circuit resets it; the
    probe failing re-opens it for another cooldown.

    Single-event-loop use only: state mutation happens between ``await`` points,
    so no lock is required.
    """

    failure_threshold: int = 3
    reset_timeout: float = 30.0
    time_source: TimeSource = time.monotonic
    _entries: dict[str, _CircuitEntry] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1.")
        if self.reset_timeout <= 0:
            raise ValueError("reset_timeout must be positive.")

    def _entry(self, key: str) -> _CircuitEntry:
        return self._entries.setdefault(key, _CircuitEntry())

    def state(self, key: str) -> CircuitState:
        return self._entry(key).state

    def allows(self, key: str) -> bool:
        """Whether a call for ``key`` may proceed, advancing state as needed.

        A closed circuit always allows. An open circuit stays closed to traffic
        until the cooldown elapses, then flips to half-open and admits exactly one
        probe. While a probe is in flight, further calls are refused.
        """
        entry = self._entry(key)
        if entry.state is CircuitState.CLOSED:
            return True
        if entry.state is CircuitState.OPEN:
            if self.time_source() - entry.opened_at < self.reset_timeout:
                return False
            entry.state = CircuitState.HALF_OPEN
            entry.probing = True
            return True
        # HALF_OPEN: admit only if no probe is currently outstanding.
        if entry.probing:
            return False
        entry.probing = True
        return True

    def record_success(self, key: str) -> None:
        entry = self._entry(key)
        entry.state = CircuitState.CLOSED
        entry.consecutive_failures = 0
        entry.probing = False

    def record_failure(self, key: str) -> None:
        entry = self._entry(key)
        entry.probing = False
        if entry.state is CircuitState.HALF_OPEN:
            # A failed probe sends us straight back to a fresh cooldown.
            entry.state = CircuitState.OPEN
            entry.opened_at = self.time_source()
            return
        entry.consecutive_failures += 1
        if entry.consecutive_failures >= self.failure_threshold:
            entry.state = CircuitState.OPEN
            entry.opened_at = self.time_source()
