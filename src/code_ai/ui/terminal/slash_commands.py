from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.config.loader import persist_config_updates, redacted_config_json
from code_ai.config.models import (
    SUPPORTED_API_MODES,
    normalize_api_mode,
)
from code_ai.ui.terminal.widgets import CODE_AI_BANNER_FONT_OPTIONS, normalize_banner_font


@dataclass(frozen=True, slots=True)
class SlashCommand:
    command: str
    description: str
    completion: str | None = None

    @property
    def completion_text(self) -> str:
        return self.completion or self.command


SLASH_COMMANDS = [
    SlashCommand("/help", "Show available commands."),
    SlashCommand("/status", "Show current session and provider state."),
    SlashCommand("/compact", "Request context compression."),
    SlashCommand("/auto", "Switch planner mode to auto."),
    SlashCommand("/plan", "Switch planner mode to plan."),
    SlashCommand("/act", "Switch planner mode to act."),
    SlashCommand("/deep-plan", "Show current bounded plan snapshot."),
    SlashCommand("/plan-status", "Show planner phase and current step."),
    SlashCommand("/replan", "Request a bounded replan on the next turn."),
    SlashCommand("/cancel", "Cancel the active turn."),
    SlashCommand("/clear", "Clear the conversation view."),
    SlashCommand("/quit", "Close Code-AI."),
    SlashCommand("/config show", "Show redacted active config."),
    SlashCommand(
        "/config model <name>",
        "Persist and switch the model for future calls.",
        "/config model ",
    ),
    SlashCommand(
        "/config api-mode <responses|completions|ollama>",
        "Persist API mode. Restart required.",
        "/config api-mode ",
    ),
    SlashCommand(
        "/config base-url <url>",
        "Persist provider base URL. Restart required.",
        "/config base-url ",
    ),
    SlashCommand(
        "/config workspace <path>",
        "Persist workspace path. Restart required.",
        "/config workspace ",
    ),
    SlashCommand(
        "/config language <code>",
        "Persist and switch response language for future calls.",
        "/config language ",
    ),
    SlashCommand(
        "/config theme <name>",
        "Persist and switch the terminal theme.",
        "/config theme ",
    ),
    SlashCommand(
        "/config banner-font <name>",
        "Persist and switch the banner art font.",
        "/config banner-font ",
    ),
]

API_MODE_SUGGESTIONS = ("responses", "completions", "ollama")
LANGUAGE_SUGGESTIONS = ("en", "pt", "pt-BR")
TERMINAL_THEME_SUGGESTIONS = (
    "textual-dark",
    "textual-light",
    "tokyo-night",
    "dracula",
    "monokai",
    "nord",
    "gruvbox",
    "catppuccin-mocha",
    "catppuccin-latte",
    "solarized-dark",
    "solarized-light",
)


def command_suggestions(prefix: str, *, limit: int = 8) -> list[SlashCommand]:
    text = prefix.lstrip()
    if not text.startswith("/"):
        return []

    value_matches = _value_suggestions(text)
    if value_matches:
        return value_matches[:limit]

    command_prefix = text.rstrip()
    matches = [item for item in SLASH_COMMANDS if item.command.startswith(command_prefix)]
    if matches:
        return matches[:limit]
    return [item for item in SLASH_COMMANDS if item.command.startswith("/config")][:limit]


def render_suggestions(prefix: str) -> str:
    suggestions = command_suggestions(prefix)
    if not suggestions:
        return ""
    return "\n".join(f"{item.command:<42} {item.description}" for item in suggestions)


def command_completion(prefix: str) -> str | None:
    suggestions = command_suggestions(prefix, limit=1)
    if not suggestions:
        return None
    completion = suggestions[0].completion_text
    return completion if len(completion) > len(prefix) else None


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
    if action == "theme":
        if len(parts) != 3:
            return "command> Usage: /config theme <name>"
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"terminal_theme": parts[2]},
            live_fields={"terminal_theme"},
            restart_required=False,
        )
    if action == "banner-font":
        if len(parts) != 3:
            return "command> Usage: /config banner-font <name>"
        font = normalize_banner_font(parts[2])
        if font != parts[2]:
            return f"command> Unsupported banner font: {parts[2]}"
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"terminal_banner_font": font},
            live_fields={"terminal_banner_font"},
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
    try:
        validated = persist_config_updates(config, changes, explicit_path=config_path)
    except Exception as exc:
        return f"command> Config not changed: {exc}"

    for field in live_fields:
        setattr(config, field, getattr(validated, field))

    changed = ", ".join(f"{key}={value}" for key, value in changes.items())
    suffix = " Restart Code-AI to apply this setting." if restart_required else " Applied now."
    return f"command> Updated {changed}.{suffix}"


def _value_suggestions(prefix: str) -> list[SlashCommand]:
    api_mode_prefix = "/config api-mode "
    if prefix.startswith(api_mode_prefix):
        value_prefix = prefix[len(api_mode_prefix) :].strip()
        return [
            SlashCommand(
                f"/config api-mode {mode}",
                "Persist API mode. Restart required.",
            )
            for mode in API_MODE_SUGGESTIONS
            if mode.startswith(value_prefix)
        ]

    language_prefix = "/config language "
    if prefix.startswith(language_prefix):
        value_prefix = prefix[len(language_prefix) :].strip()
        return [
            SlashCommand(
                f"/config language {language}",
                "Persist and switch response language for future calls.",
            )
            for language in LANGUAGE_SUGGESTIONS
            if language.lower().startswith(value_prefix.lower())
        ]

    theme_prefix = "/config theme "
    if prefix.startswith(theme_prefix):
        value_prefix = prefix[len(theme_prefix) :].strip()
        return [
            SlashCommand(
                f"/config theme {theme}",
                "Persist and switch the terminal theme.",
            )
            for theme in TERMINAL_THEME_SUGGESTIONS
            if theme.startswith(value_prefix)
        ]

    banner_font_prefix = "/config banner-font "
    if prefix.startswith(banner_font_prefix):
        value_prefix = prefix[len(banner_font_prefix) :].strip()
        return [
            SlashCommand(
                f"/config banner-font {font}",
                "Persist and switch the banner art font.",
            )
            for font in CODE_AI_BANNER_FONT_OPTIONS
            if font.startswith(value_prefix)
        ]
    return []
