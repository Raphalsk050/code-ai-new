from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

from code_ai.config.defaults import DEFAULT_CONFIG, PLACEHOLDER_API_KEY
from code_ai.config.models import AppConfig
from code_ai.core.errors import ConfigurationError
from code_ai.util.fileio import FileOperationError, RetryPolicy, atomic_write_text, read_bytes

logger = logging.getLogger(__name__)

# The configuration file is read before the user's own file_io settings can be
# known, and written on the same host those settings exist for. The built-in
# defaults are what stands in: a locked-down machine must not be unable to read
# its own config just because the retry policy is still inside it.
_BOOTSTRAP_POLICY = RetryPolicy()


# A config.json says which version it is from 2 on. Before, every save wrote
# the whole configuration back, so a file held each default of its day as if
# the user had chosen it, and a default changed later never reached them.
CONFIG_VERSION = 2

# Values each budget defaulted to before version 2. In an older file they are
# what a save froze, not a choice, so they go and today's default holds.
_FROZEN_BUDGETS: dict[str, frozenset[int]] = {
    "default_tool_timeout_s": frozenset({60}),
    "max_model_call_s": frozenset({180}),
    "max_model_step_seconds": frozenset({180}),
    "max_model_steps": frozenset({80}),
    "max_stall_rounds": frozenset({4}),
    "max_tool_call_seconds": frozenset({900}),
    "max_tool_calls": frozenset({250}),
    "max_tool_wall_time_s": frozenset({900}),
    "max_turn_seconds": frozenset({900}),
    "max_turn_wall_time_s": frozenset({900}),
    "subagent_explorer_timeout_s": frozenset({120}),
    "subagent_worker_timeout_s": frozenset({300}),
}

# Written even when they match the default: what someone opening the file
# looks for, and what a changed default must not move under a working setup.
_ALWAYS_WRITTEN = frozenset({"api_key", "base_url", "model", "workspace"})
_UNSET = object()


def default_config_path() -> Path:
    from code_ai.config.defaults import default_config_path as _default_config_path

    return _default_config_path()


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(read_bytes(path, policy=_BOOTSTRAP_POLICY).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON configuration at {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigurationError("Configuration file must contain a JSON object.")
    return parsed


def _sparse(data: dict[str, Any]) -> dict[str, Any]:
    """``data`` without what only repeats a default, so a default changed later reaches it."""

    kept: dict[str, Any] = {}
    for key, value in data.items():
        default = DEFAULT_CONFIG.get(key, _UNSET)
        if key in _ALWAYS_WRITTEN or default is _UNSET:
            kept[key] = value
        elif isinstance(value, dict) and isinstance(default, dict):
            section = {k: v for k, v in value.items() if k not in default or v != default[k]}
            if section:
                kept[key] = section
        elif value != default:
            kept[key] = value
    return kept


def _migrate(data: dict[str, Any]) -> dict[str, Any] | None:
    """``data`` brought up to CONFIG_VERSION, or None when it already is."""

    version = data.get("config_version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= CONFIG_VERSION:
        return None
    migrated = copy.deepcopy(data)
    # Chat Completions is the default now. A file that says Responses got it
    # from the old default far more often than from a choice.
    if str(migrated.get("api_mode", "")).strip().lower() == "responses":
        migrated["api_mode"] = "completions"
    budgets = migrated.get("budgets")
    if isinstance(budgets, dict):
        for key, frozen in _FROZEN_BUDGETS.items():
            if key in budgets and budgets[key] in frozen:
                del budgets[key]
    return _versioned(migrated)


def _versioned(data: dict[str, Any]) -> dict[str, Any]:
    sparse = _sparse(data)
    sparse["config_version"] = CONFIG_VERSION
    return sparse


def _read_current(path: Path, *, write_back: bool) -> dict[str, Any]:
    """The file's settings, migrated if it predates CONFIG_VERSION.

    With ``write_back`` the migrated file replaces the old one, so the
    migration runs once and a setting chosen after it is kept. A file that
    cannot be written is migrated in memory, and again until it can be.
    """

    file_data = _read_config_file(path)
    migrated = _migrate(file_data) if file_data else None
    if migrated is None:
        return file_data
    if write_back:
        try:
            _write(path, migrated, policy=_BOOTSTRAP_POLICY, allow_non_atomic_fallback=True)
        except (OSError, FileOperationError) as exc:
            logger.warning("Could not save the migrated configuration at %s: %s", path, exc)
    return migrated


def _write(
    path: Path, data: dict[str, Any], *, policy: RetryPolicy, allow_non_atomic_fallback: bool
) -> None:
    atomic_write_text(
        path,
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        policy=policy,
        allow_non_atomic_fallback=allow_non_atomic_fallback,
    )


def load_config(
    *,
    explicit_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> AppConfig:
    path = explicit_path or default_config_path()
    data: dict[str, Any] = dict(DEFAULT_CONFIG)
    data.update(_read_current(path.expanduser(), write_back=True))

    if "OPENAI_API_KEY" in os.environ:
        data["api_key"] = os.environ["OPENAI_API_KEY"]
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
    # The fields a new user fills in; everything else follows the defaults
    # until it is set (see _sparse).
    data: dict[str, Any] = {key: DEFAULT_CONFIG[key] for key in ("base_url", "model")}
    data["api_key"] = PLACEHOLDER_API_KEY
    data["workspace"] = str((workspace or Path.cwd()).resolve())
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
    _ensure_api_key_placeholder(data)
    _write(target, _versioned(data), policy=_BOOTSTRAP_POLICY, allow_non_atomic_fallback=True)
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
        data = _read_current(target, write_back=False)
    else:
        # What the session runs against, and nothing else: not a key that
        # came from the environment, and no defaults to freeze.
        data = {
            "api_mode": config.api_mode,
            "base_url": config.base_url,
            "model": config.model,
            "workspace": str(config.workspace),
        }

    data.update(changes)
    validated = AppConfig.from_mapping({**DEFAULT_CONFIG, **data})
    _ensure_api_key_placeholder(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Through the atomic write, not ``write_text``: the plain one truncates the
    # file before it writes, so a scanner or encryption agent blocking it
    # halfway leaves the user with no configuration at all rather than the one
    # they had. Every /config command lands here. Only what differs from the
    # defaults goes in: the panel saves every field, changed or not.
    _write(
        target,
        _versioned(data),
        policy=RetryPolicy.from_config(config.file_io),
        allow_non_atomic_fallback=config.file_io.allow_non_atomic_fallback,
    )
    return validated


def _ensure_api_key_placeholder(data: dict[str, Any]) -> None:
    """Never persist a blank api_key — fall back to the generic placeholder."""
    if not str(data.get("api_key", "")).strip():
        data["api_key"] = PLACEHOLDER_API_KEY
