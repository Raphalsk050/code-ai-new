from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.config.loader import default_config_path, redacted_config_json
from code_ai.config.models import (
    SUPPORTED_API_MODES,
    AppConfig,
    normalize_api_mode,
)


@dataclass(frozen=True, slots=True)
class SlashCommand:
    command: str
    description: str


SLASH_COMMANDS = [
    SlashCommand("/help", "Show available commands."),
    SlashCommand("/status", "Show current session and provider state."),
    SlashCommand("/compact", "Request context compression."),
    SlashCommand("/cancel", "Cancel the active turn."),
    SlashCommand("/clear", "Clear the conversation view."),
    SlashCommand("/quit", "Close Code-AI."),
    SlashCommand("/config show", "Show redacted active config."),
    SlashCommand("/config model <name>", "Persist and switch the model for future calls."),
    SlashCommand(
        "/config api-mode <responses|completions|ollama>",
        "Persist API mode. Restart required.",
    ),
    SlashCommand("/config base-url <url>", "Persist provider base URL. Restart required."),
    SlashCommand("/config workspace <path>", "Persist workspace path. Restart required."),
    SlashCommand(
        "/config language <code>",
        "Persist and switch response language for future calls.",
    ),
]


def command_suggestions(prefix: str, *, limit: int = 8) -> list[SlashCommand]:
    text = prefix.strip()
    if not text.startswith("/"):
        return []
    matches = [item for item in SLASH_COMMANDS if item.command.startswith(text)]
    if matches:
        return matches[:limit]
    return [item for item in SLASH_COMMANDS if item.command.startswith("/config")][:limit]


def render_suggestions(prefix: str) -> str:
    suggestions = command_suggestions(prefix)
    if not suggestions:
        return ""
    return "\n".join(f"{item.command:<42} {item.description}" for item in suggestions)


def handle_config_command(application: Any, command_text: str, *, config_path: Path | None) -> str:
    parts = shlex.split(command_text)
    if len(parts) < 2 or parts[0] != "/config":
        return "command> Invalid config command."

    action = parts[1]
    config = application.session.config
    if action == "show":
        return redacted_config_json(config)
    if action == "model":
        if len(parts) < 3:
            return "command> Usage: /config model <name>"
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"model": " ".join(parts[2:])},
            live_fields={"model"},
            restart_required=False,
        )
    if action == "language":
        if len(parts) != 3:
            return "command> Usage: /config language <code>"
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"language": parts[2]},
            live_fields={"language"},
            restart_required=False,
        )
    if action == "api-mode":
        if len(parts) != 3:
            return "command> Usage: /config api-mode <responses|completions|ollama>"
        mode = normalize_api_mode(parts[2])
        if mode not in SUPPORTED_API_MODES:
            return f"command> Unsupported api mode: {parts[2]}"
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"api_mode": mode},
            live_fields=set(),
            restart_required=True,
        )
    if action == "base-url":
        if len(parts) != 3:
            return "command> Usage: /config base-url <url>"
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"base_url": parts[2]},
            live_fields=set(),
            restart_required=True,
        )
    if action == "workspace":
        if len(parts) != 3:
            return "command> Usage: /config workspace <path>"
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"workspace": str(Path(parts[2]).expanduser().resolve())},
            live_fields=set(),
            restart_required=True,
        )
    return f"command> Unknown config action: {action}"


def _apply_config_change(
    application: Any,
    *,
    config_path: Path | None,
    changes: dict[str, Any],
    live_fields: set[str],
    restart_required: bool,
) -> str:
    config = application.session.config
    target = (config_path or default_config_path()).expanduser()
    data = _load_config_data(target, config)
    data.update(changes)
    try:
        validated = AppConfig.from_mapping(data)
    except Exception as exc:
        return f"command> Config not changed: {exc}"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for field in live_fields:
        setattr(config, field, getattr(validated, field))

    changed = ", ".join(f"{key}={value}" for key, value in changes.items())
    suffix = " Restart Code-AI to apply this setting." if restart_required else " Applied now."
    return f"command> Updated {changed}.{suffix}"


def _load_config_data(target: Path, config: AppConfig) -> dict[str, Any]:
    if target.exists():
        parsed = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return parsed
    data = config.to_dict()
    data["api_key"] = ""
    return data
