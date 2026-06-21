from __future__ import annotations

import json

import pytest

from code_ai.cli.main import build_parser
from code_ai.config.defaults import PLACEHOLDER_API_KEY
from code_ai.config.loader import (
    config_init,
    default_config_path,
    load_config,
    persist_config_updates,
    redacted_config_json,
)
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


def test_terminal_banner_font_loads_from_config_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "terminal_banner_font": "future_1",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.terminal_banner_font == "future_1"


def test_terminal_spinner_loads_from_config_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "terminal_spinner": "braille-full",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.terminal_spinner == "braille-full"


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


def test_sampling_loads_from_config_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "completions",
                "base_url": "http://localhost:11434/v1",
                "workspace": str(tmp_path),
                "sampling": {
                    "temperature": 0.6,
                    "top_p": 0.95,
                    "presence_penalty": 0.0,
                    "top_k": 20,
                    "min_p": 0,
                    "extra_body": {"repetition_penalty": 1.05},
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    sampling = config.sampling
    assert sampling.temperature == 0.6
    assert sampling.top_k == 20

    chat = sampling.chat_completion_kwargs()
    assert chat["temperature"] == 0.6
    assert chat["top_p"] == 0.95
    assert chat["presence_penalty"] == 0.0
    # top_k/min_p are not OpenAI fields -> forwarded via extra_body alongside passthrough.
    assert chat["extra_body"] == {"repetition_penalty": 1.05, "top_k": 20, "min_p": 0.0}

    responses = sampling.responses_kwargs()
    assert "presence_penalty" not in responses
    assert responses["extra_body"]["top_k"] == 20


def test_sampling_omits_unset_fields(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "completions",
                "workspace": str(tmp_path),
                "sampling": {
                    "temperature": None,
                    "top_p": None,
                    "presence_penalty": None,
                    "top_k": None,
                    "min_p": None,
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.sampling.chat_completion_kwargs() == {}


def test_sampling_reasoning_controls_for_responses(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "responses",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test",
                "workspace": str(tmp_path),
                "sampling": {"reasoning_effort": "high", "reasoning_summary": "auto"},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.sampling.responses_kwargs()["reasoning"] == {
        "effort": "high",
        "summary": "auto",
    }


def test_sampling_rejects_invalid_reasoning_effort(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "completions",
                "workspace": str(tmp_path),
                "sampling": {"reasoning_effort": "turbo"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_config(explicit_path=config_path)


def test_config_init_writes_placeholder_api_key_treated_as_unset(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_init(config_path, workspace=tmp_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    # The file never carries a blank api_key — just a generic placeholder...
    assert saved["api_key"] == PLACEHOLDER_API_KEY
    # ...which is treated as "unset" once loaded, so it never reaches a provider.
    config = load_config(explicit_path=config_path)
    assert config.api_key == ""


def test_persisting_change_keeps_placeholder_when_key_unset(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_init(config_path, workspace=tmp_path)

    config = load_config(explicit_path=config_path)
    persist_config_updates(config, {"model": "other-model"}, explicit_path=config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model"] == "other-model"
    assert saved["api_key"] == PLACEHOLDER_API_KEY


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
