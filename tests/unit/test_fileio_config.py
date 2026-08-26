from __future__ import annotations

import pytest

from code_ai.config.models import AppConfig, FileIOConfig
from code_ai.core.errors import ConfigurationError


def test_retrying_is_on_by_default() -> None:
    config = FileIOConfig()

    assert config.retry_attempts > 1
    assert config.allow_non_atomic_fallback is True
    assert config.retry_initial_delay_ms <= config.retry_max_delay_ms


def test_the_section_round_trips(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "file_io": {"retry_attempts": 12, "allow_non_atomic_fallback": False},
        }
    )

    assert config.file_io.retry_attempts == 12
    assert config.file_io.allow_non_atomic_fallback is False
    # Unstated keys keep their defaults.
    assert config.file_io.retry_max_delay_ms > 0
    assert config.to_dict()["file_io"]["retry_attempts"] == 12


@pytest.mark.parametrize(
    "overrides",
    [
        {"retry_attempts": 0},
        {"retry_initial_delay_ms": -1},
        {"retry_initial_delay_ms": 500, "retry_max_delay_ms": 100},
    ],
)
def test_a_policy_that_cannot_work_is_rejected(tmp_path, overrides) -> None:
    with pytest.raises(ConfigurationError):
        AppConfig.from_mapping(
            {"api_mode": "ollama", "workspace": str(tmp_path), "file_io": overrides}
        )
