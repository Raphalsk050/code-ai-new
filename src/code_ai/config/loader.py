from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from code_ai.config.defaults import DEFAULT_CONFIG
from code_ai.config.models import AppConfig
from code_ai.core.errors import ConfigurationError


def default_config_path() -> Path:
    from code_ai.config.defaults import default_config_path as _default_config_path

    return _default_config_path()


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON configuration at {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigurationError("Configuration file must contain a JSON object.")
    return parsed


def load_config(
    *,
    explicit_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> AppConfig:
    path = explicit_path or default_config_path()
    data: dict[str, Any] = dict(DEFAULT_CONFIG)
    file_data = _read_config_file(path.expanduser())
    data.update(file_data)

    if "API_KEY" in os.environ:
        data["api_key"] = os.environ["API_KEY"]
    if "BASE_URL" in os.environ:
        data["base_url"] = os.environ["BASE_URL"]

    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is not None:
                data[key] = value

    return AppConfig.from_mapping(data)


def config_init(
    path: Path | None = None,
    *,
    force: bool = False,
    workspace: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> Path:
    target = (path or default_config_path()).expanduser()
    if target.exists() and not force:
        raise ConfigurationError(f"Configuration already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = dict(DEFAULT_CONFIG)
    data["api_key"] = ""
    data["workspace"] = str((workspace or Path.cwd()).resolve())
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def redacted_config_json(config: AppConfig) -> str:
    return json.dumps(config.to_dict(redacted=True), indent=2, sort_keys=True)


def persist_config_updates(
    config: AppConfig,
    changes: dict[str, Any],
    *,
    explicit_path: Path | None = None,
) -> AppConfig:
    target = (explicit_path or default_config_path()).expanduser()
    if target.exists():
        data: dict[str, Any] = dict(DEFAULT_CONFIG)
        data.update(_read_config_file(target))
    else:
        data = config.to_dict()
        data["api_key"] = ""

    data.update(changes)
    validated = AppConfig.from_mapping(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return validated
