from __future__ import annotations

import json

import pytest

from code_ai.cli.main import build_parser
from code_ai.config.loader import default_config_path, load_config, redacted_config_json
from code_ai.core.errors import ConfigurationError


def test_default_config_path_is_under_home() -> None:
    path = default_config_path()
    assert path.name == "config.json"
    assert path.parent.name == ".code-ai"


def test_file_environment_and_cli_precedence(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "base_url": "http://file.example/v1",
                "model": "file-model",
                "workspace": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BASE_URL", "http://env.example/v1")
    config = load_config(
        explicit_path=config_path,
        cli_overrides={"model": "cli-model"},
    )
    assert config.base_url == "http://env.example/v1"
    assert config.model == "cli-model"


def test_terminal_theme_loads_from_config_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "terminal_theme": "tokyo-night",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.terminal_theme == "tokyo-night"


def test_invalid_budget_is_rejected(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "budgets": {"max_context_tokens": 1},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_config(explicit_path=config_path)


def test_redacted_config_output_hides_api_key(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "responses",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-real-secret",
                "workspace": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    rendered = redacted_config_json(config)
    assert "sk-real-secret" not in rendered
    assert "<redacted>" in rendered
    assert '"max_context_tokens": 256000' in rendered


def test_config_init_accepts_overrides_after_subcommand(tmp_path) -> None:
    args = build_parser().parse_args(
        [
            "--config",
            str(tmp_path / "config.json"),
            "config",
            "init",
            "--workspace",
            str(tmp_path),
            "--api-mode",
            "ollama",
            "--model",
            "local-model",
        ]
    )
    assert args.init_workspace == tmp_path
    assert args.init_api_mode == "ollama"
    assert args.init_model == "local-model"
