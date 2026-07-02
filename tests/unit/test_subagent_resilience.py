from __future__ import annotations

import pytest

from code_ai.core.errors import TransientProviderError
from code_ai.core.subagents.resilience import (
    CircuitBreaker,
    CircuitState,
    RetryPolicy,
)


class _Clock:
    """Manually advanced monotonic clock for deterministic breaker tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _no_sleep(_delay: float) -> None:
    return None


# --------------------------------------------------------------------------- #
# RetryPolicy
# --------------------------------------------------------------------------- #


async def test_retry_returns_first_success_without_sleeping() -> None:
    policy = RetryPolicy(max_attempts=3)
    calls = 0
    slept: list[float] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await policy.run(
        operation,
        should_retry=lambda _exc: True,
        sleep=lambda delay: slept.append(delay) or _no_sleep(delay),
    )

    assert result == "ok"
    assert calls == 1
    assert slept == []


async def test_retry_retries_transient_then_succeeds() -> None:
    policy = RetryPolicy(max_attempts=3, base_delay=0.1, jitter=0.0)
    calls = 0
    delays: list[float] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TransientProviderError("blip")
        return "recovered"

    async def sleep(delay: float) -> None:
        delays.append(delay)

    result = await policy.run(
        operation,
        should_retry=lambda exc: isinstance(exc, TransientProviderError),
        sleep=sleep,
        rng=lambda: 0.0,
    )

    assert result == "recovered"
    assert calls == 3
    # Exponential: base*2^0, base*2^1.
    assert delays == [pytest.approx(0.1), pytest.approx(0.2)]


async def test_retry_stops_on_non_retryable_and_reraises_original() -> None:
    policy = RetryPolicy(max_attempts=5)
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("fatal")

    with pytest.raises(ValueError, match="fatal"):
        await policy.run(
            operation,
            should_retry=lambda exc: isinstance(exc, TransientProviderError),
            sleep=_no_sleep,
        )
    assert calls == 1


async def test_retry_exhausts_attempts_and_reraises_last() -> None:
    policy = RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0)
    calls = 0
    on_retry_calls: list[int] = []

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise TransientProviderError(f"attempt {calls}")

    async def on_retry(attempt: int, _exc: BaseException) -> None:
        on_retry_calls.append(attempt)

    with pytest.raises(TransientProviderError, match="attempt 2"):
        await policy.run(
            operation,
            should_retry=lambda _exc: True,
            on_retry=on_retry,
            sleep=_no_sleep,
        )
    assert calls == 2
    assert on_retry_calls == [1]


def test_retry_policy_validates_arguments() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=-1)


# --------------------------------------------------------------------------- #
# CircuitBreaker
# --------------------------------------------------------------------------- #


def test_breaker_opens_after_threshold_failures() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=30.0, time_source=clock)

    for _ in range(2):
        assert breaker.allows("coder") is True
        breaker.record_failure("coder")
    assert breaker.state("coder") is CircuitState.CLOSED

    assert breaker.allows("coder") is True
    breaker.record_failure("coder")
    assert breaker.state("coder") is CircuitState.OPEN
    # Open circuit refuses further calls during the cooldown window.
    assert breaker.allows("coder") is False


def test_breaker_keys_are_independent() -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=30.0, time_source=_Clock())
    breaker.allows("explorer")
    breaker.record_failure("explorer")

    assert breaker.state("explorer") is CircuitState.OPEN
    # A different profile is untouched.
    assert breaker.allows("coder") is True
    assert breaker.state("coder") is CircuitState.CLOSED


def test_breaker_half_open_probe_closes_on_success() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, time_source=clock)
    breaker.allows("coder")
    breaker.record_failure("coder")
    assert breaker.state("coder") is CircuitState.OPEN

    clock.advance(11.0)
    # First call after cooldown becomes the single half-open probe.
    assert breaker.allows("coder") is True
    assert breaker.state("coder") is CircuitState.HALF_OPEN
    # A concurrent second call is refused while the probe is outstanding.
    assert breaker.allows("coder") is False

    breaker.record_success("coder")
    assert breaker.state("coder") is CircuitState.CLOSED
    assert breaker.allows("coder") is True


def test_breaker_half_open_probe_reopens_on_failure() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, time_source=clock)
    breaker.allows("coder")
    breaker.record_failure("coder")

    clock.advance(11.0)
    assert breaker.allows("coder") is True  # probe admitted
    breaker.record_failure("coder")
    # Failed probe restarts the cooldown from now.
    assert breaker.state("coder") is CircuitState.OPEN
    assert breaker.allows("coder") is False
    clock.advance(11.0)
    assert breaker.allows("coder") is True


def test_breaker_validates_arguments() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(reset_timeout=0)
