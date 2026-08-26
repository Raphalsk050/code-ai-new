from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from code_ai.config.defaults import SANDBOX_DIR_ENV, default_sandbox_base_dir
from code_ai.config.models import AppConfig, SandboxConfig
from code_ai.core.errors import ConfigurationError


def test_sandbox_defaults_are_enabled_and_bounded() -> None:
    config = SandboxConfig()

    assert config.enabled is True
    assert config.cleanup_on_exit is True
    assert config.ttl_hours > 0
    assert config.max_artifact_bytes > 0


def test_default_base_dir_lives_in_the_system_temp_dir(monkeypatch) -> None:
    monkeypatch.delenv(SANDBOX_DIR_ENV, raising=False)

    base = default_sandbox_base_dir()

    assert base.parent == Path(tempfile.gettempdir())
    assert base.name == "python_agent_sandbox"


def test_env_override_relocates_the_base_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(SANDBOX_DIR_ENV, str(tmp_path / "elsewhere"))

    assert default_sandbox_base_dir() == tmp_path / "elsewhere"


def test_configured_base_dir_wins_over_the_default(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(SANDBOX_DIR_ENV, str(tmp_path / "env"))
    config = SandboxConfig(base_dir=str(tmp_path / "configured"))

    assert config.resolved_base_dir() == tmp_path / "configured"


def test_app_config_round_trips_the_sandbox_section(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "sandbox": {"enabled": False, "ttl_hours": 3, "cleanup_on_exit": False},
        }
    )

    assert config.sandbox.enabled is False
    assert config.sandbox.ttl_hours == 3
    assert config.sandbox.cleanup_on_exit is False
    # Unstated keys keep their defaults instead of collapsing to falsy values.
    assert config.sandbox.max_artifact_bytes > 0
    assert config.to_dict()["sandbox"]["ttl_hours"] == 3


@pytest.mark.parametrize(
    "overrides",
    [{"ttl_hours": 0}, {"max_artifact_bytes": 0}],
)
def test_non_positive_limits_are_rejected(tmp_path, overrides) -> None:
    with pytest.raises(ConfigurationError):
        AppConfig.from_mapping(
            {"api_mode": "ollama", "workspace": str(tmp_path), "sandbox": overrides}
        )
