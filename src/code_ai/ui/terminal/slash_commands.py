from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_ai.config.loader import persist_config_updates, redacted_config_json
from code_ai.config.models import (
    SUPPORTED_API_MODES,
    SUPPORTED_REASONING_EFFORTS,
    normalize_api_mode,
)
from code_ai.ui.terminal.widgets import (
    CODE_AI_BANNER_FONT_OPTIONS,
    CODE_AI_SPINNER_OPTIONS,
    normalize_banner_font,
    normalize_spinner,
)


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
    SlashCommand(
        "/mode <ask|auto|bypass>",
        "Set the tool permission mode (persisted).",
        "/mode ",
    ),
    SlashCommand("/deep-plan", "Show current bounded plan snapshot."),
    SlashCommand("/plan-status", "Show planner phase and current step."),
    SlashCommand("/replan", "Request a bounded replan on the next turn."),
    SlashCommand("/cancel", "Cancel the active turn."),
    SlashCommand(
        "/debug <on|off|status>",
        "Log raw model requests/responses for parser debugging.",
        "/debug ",
    ),
    SlashCommand("/clear", "Clear the conversation view."),
    SlashCommand("/quit", "Close Code-AI."),
    SlashCommand("/config help", "Browse and pick a /config command to run."),
    SlashCommand("/config show", "Show redacted active config."),
    SlashCommand(
        "/config models",
        "List models offered by your provider and pick one.",
    ),
    SlashCommand(
        "/config model <name>",
        "Persist and switch the model for future calls.",
        "/config model ",
    ),
    SlashCommand(
        "/config api-key <key>",
        "Persist the provider API key (redacted). Restart required.",
        "/config api-key ",
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
        "/config effort <none|minimal|low|medium|high|xhigh>",
        "Persist and switch reasoning_effort (OpenAI Responses API).",
        "/config effort ",
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
    SlashCommand(
        "/config spinner <name>",
        "Persist and switch the working-indicator animation.",
        "/config spinner ",
    ),
    SlashCommand(
        "/config max-context-window <tokens>",
        "Persist the max context window size in tokens. Restart required.",
        "/config max-context-window ",
    ),
    SlashCommand(
        "/config learn <on|off>",
        "Show/hide the model's explanation of why it's making each change.",
        "/config learn ",
    ),
]

API_MODE_SUGGESTIONS = ("responses", "completions", "ollama")
LANGUAGE_SUGGESTIONS = ("en", "pt", "pt-BR")
# Ordered low-to-high so the picker reads like a dial; gated to the values the
# SamplingConfig validator accepts.
REASONING_EFFORT_SUGGESTIONS = tuple(
    effort
    for effort in ("none", "minimal", "low", "medium", "high", "xhigh")
    if effort in SUPPORTED_REASONING_EFFORTS
)
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


def config_commands(*, include_help: bool = False) -> list[SlashCommand]:
    """The ``/config`` subcommands, in declaration order.

    Powers both the interactive ``/config help`` picker and its headless text
    fallback. ``/config help`` itself is omitted by default so the picker does
    not list a way back into itself.
    """
    return [
        item
        for item in SLASH_COMMANDS
        if item.command.startswith("/config")
        and (include_help or item.command != "/config help")
    ]


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
    if action == "help":
        lines = "\n".join(
            f"{item.command:<48} {item.description}" for item in config_commands()
        )
        return (
            "command> Config commands (run /config help in the terminal UI to "
            "pick one interactively):\n" + lines
        )
    if action == "show":
        return redacted_config_json(config)
    if action == "models":
        # The interactive picker lives in the terminal UI (it fetches the catalog
        # and opens a searchable list). Reaching here means there is no UI to host
        # the picker, so point the user at the direct form instead.
        return (
            "command> Run /config models inside the terminal UI to pick from your "
            "provider's models, or use /config model <name> to set one directly."
        )
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
    if action == "effort":
        if len(parts) != 3:
            return (
                "command> Usage: /config effort "
                "<none|minimal|low|medium|high|xhigh>"
            )
        effort = parts[2].strip().lower()
        if effort not in SUPPORTED_REASONING_EFFORTS:
            return (
                f"command> Unsupported reasoning effort: {parts[2]}. "
                f"Choose one of {list(REASONING_EFFORT_SUGGESTIONS)}."
            )
        # reasoning_effort lives under the nested ``sampling`` block, so persist
        # the whole block with the new value and apply it on the live config the
        # providers already hold (they read sampling fresh on every call).
        sampling = asdict(config.sampling)
        sampling["reasoning_effort"] = effort
        try:
            validated = persist_config_updates(
                config, {"sampling": sampling}, explicit_path=config_path
            )
        except Exception as exc:
            return f"command> Config not changed: {exc}"
        config.sampling = validated.sampling
        return f"command> Updated reasoning_effort={effort}. Applied now."
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
    if action == "spinner":
        if len(parts) != 3:
            return "command> Usage: /config spinner <name>"
        spinner = normalize_spinner(parts[2])
        if spinner != parts[2]:
            return f"command> Unsupported spinner: {parts[2]}"
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"terminal_spinner": spinner},
            live_fields={"terminal_spinner"},
            restart_required=False,
        )
    if action == "api-key":
        # Pull the key straight from the command text (not the shlex-split parts)
        # so it is stored exactly as typed and never echoed back to the log.
        key = command_text.split("api-key", 1)[1].strip().strip("'\"")
        if not key:
            return "command> Usage: /config api-key <key>"
        try:
            persist_config_updates(config, {"api_key": key}, explicit_path=config_path)
        except Exception as exc:
            return f"command> Config not changed: {exc}"
        config.api_key = key
        return (
            "command> Updated api_key=<redacted>. Restart Code-AI to apply this setting."
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
    if action == "max-context-window":
        if len(parts) != 3:
            return "command> Usage: /config max-context-window <tokens>"
        try:
            tokens = int(parts[2])
        except ValueError:
            return f"command> Invalid token count: {parts[2]}"
        # max_context_tokens lives under the nested ``budgets`` block. The
        # ContextCompressor reads it once at bootstrap, so this always
        # requires a restart to take effect (unlike model/language/effort).
        budgets = asdict(config.budgets)
        budgets["max_context_tokens"] = tokens
        try:
            validated = persist_config_updates(
                config, {"budgets": budgets}, explicit_path=config_path
            )
        except Exception as exc:
            return f"command> Config not changed: {exc}"
        config.budgets = validated.budgets
        return (
            f"command> Updated max_context_tokens={tokens}. "
            "Restart Code-AI to apply this setting."
        )
    if action == "learn":
        if len(parts) != 3 or parts[2].strip().lower() not in {"on", "off"}:
            return "command> Usage: /config learn <on|off>"
        enabled = parts[2].strip().lower() == "on"
        result = _apply_config_change(
            application,
            config_path=config_path,
            changes={"learn": enabled},
            live_fields={"learn"},
            restart_required=False,
        )
        if result.startswith("command> Config not changed"):
            return result
        if enabled:
            return (
                "command> Learn mode on. Approval prompts will show the model's "
                "explanation of why each change is needed."
            )
        return "command> Learn mode off. Approval prompts will no longer show explanations."
    return f"command> Unknown config action: {action}"


def handle_debug_command(
    application: Any, command_text: str, *, config_path: Path | None
) -> str:
    """Toggle raw model request/response logging for parser debugging.

    The flag lives on the active config object the providers already hold, so
    turning it on/off takes effect on the very next model call without a restart.
    It is also persisted so a debugging session survives a restart.
    """
    from code_ai.providers.debug import session_log_dir

    parts = command_text.split()
    action = parts[1].strip().lower() if len(parts) > 1 else "status"
    config = application.session.config

    if action == "status":
        state = "on" if config.debug else "off"
        return f"command> Debug logging is {state}. Session logs: {session_log_dir()}"

    if action in {"on", "off"}:
        enabled = action == "on"
        result = _apply_config_change(
            application,
            config_path=config_path,
            changes={"debug": enabled},
            live_fields={"debug"},
            restart_required=False,
        )
        if result.startswith("command> Config not changed"):
            return result
        if enabled:
            return (
                "command> Debug logging on. Raw model requests/responses will be "
                f"written to {session_log_dir()} (one numbered file per call)."
            )
        return "command> Debug logging off."

    return "command> Usage: /debug <on|off|status>"


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

    effort_prefix = "/config effort "
    if prefix.startswith(effort_prefix):
        value_prefix = prefix[len(effort_prefix) :].strip().lower()
        return [
            SlashCommand(
                f"/config effort {effort}",
                "Persist and switch reasoning_effort (OpenAI Responses API).",
            )
            for effort in REASONING_EFFORT_SUGGESTIONS
            if effort.startswith(value_prefix)
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

    spinner_prefix = "/config spinner "
    if prefix.startswith(spinner_prefix):
        value_prefix = prefix[len(spinner_prefix) :].strip()
        return [
            SlashCommand(
                f"/config spinner {spinner}",
                "Persist and switch the working-indicator animation.",
            )
            for spinner in CODE_AI_SPINNER_OPTIONS
            if spinner.startswith(value_prefix)
        ]

    learn_prefix = "/config learn "
    if prefix.startswith(learn_prefix):
        value_prefix = prefix[len(learn_prefix) :].strip().lower()
        return [
            SlashCommand(
                f"/config learn {value}",
                "Show/hide the model's explanation of why it's making each change.",
            )
            for value in ("on", "off")
            if value.startswith(value_prefix)
        ]
    return []
