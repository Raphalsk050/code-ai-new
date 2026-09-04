from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_ai.config.loader import persist_config_updates, redacted_config_json
from code_ai.config.models import (
    SUPPORTED_API_MODES,
    SUPPORTED_REASONING_EFFORTS,
    normalize_api_mode,
)
from code_ai.core.workflows import WorkflowRecord
from code_ai.providers.factory import PROVIDER_BAKED_SETTINGS
from code_ai.tools.skills.common import SkillRecord
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
    SlashCommand("/doctor", "Guided setup: configure everything step by step."),
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
    SlashCommand(
        "/deep-plan <objetivo>",
        "Plan a task without changing anything (Cline-style plan mode).",
        "/deep-plan ",
    ),
    SlashCommand("/plan-status", "Show planner phase and current step."),
    SlashCommand("/replan", "Request a bounded replan on the next turn."),
    SlashCommand(
        "/goal <objetivo>",
        "Define a persistent goal; the agent iterates until it is verifiably met.",
        "/goal ",
    ),
    SlashCommand("/goal start", "Confirm the proposed criteria and start the goal loop."),
    SlashCommand("/goal status", "Show the goal, its criteria, and loop progress."),
    SlashCommand("/goal stop", "Stop the goal loop (also cancels the running turn)."),
    SlashCommand("/goal resume", "Resume a blocked goal and restart the loop."),
    SlashCommand("/cancel", "Cancel the active turn."),
    SlashCommand(
        "/term <texto>",
        "Type a line into the shared interactive terminal session.",
        "/term ",
    ),
    SlashCommand("/term start", "Open an interactive terminal in the workspace."),
    SlashCommand("/term status", "Show the terminal session and its screen."),
    SlashCommand(
        "/term ctrl <c|d|z>",
        "Send a control key (e.g. Ctrl+C) to the terminal.",
        "/term ctrl ",
    ),
    SlashCommand("/term kill", "Terminate the interactive terminal session."),
    SlashCommand(
        "/debug <on|off|status>",
        "Log raw model requests/responses for parser debugging.",
        "/debug ",
    ),
    SlashCommand("/workflows", "List the saved workflows you can run by name."),
    SlashCommand("/skills", "List the skills you can force by name."),
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
        "/config vision-model <name>",
        "Vision model that reads pasted images for a non-multimodal main model.",
        "/config vision-model ",
    ),
    SlashCommand(
        "/config api-key <key>",
        "Persist and switch the provider API key (redacted).",
        "/config api-key ",
    ),
    SlashCommand(
        "/config api-mode <responses|completions|ollama>",
        "Persist and switch the API mode.",
        "/config api-mode ",
    ),
    SlashCommand(
        "/config base-url <url>",
        "Persist and switch the provider base URL.",
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
        "Persist and apply the max context window size in tokens.",
        "/config max-context-window ",
    ),
    SlashCommand(
        "/config learn <on|off>",
        "Show/hide the model's explanation of why it's making each change.",
        "/config learn ",
    ),
    SlashCommand(
        "/config live-code <on|off>",
        "Show/hide the file being written, live, as the model streams it.",
        "/config live-code ",
    ),
]

# The /config actions consumed while the model client is constructed rather than
# read per request. Changing one is persisted and applied to the live config
# like any other, but the running client keeps the old value until it is
# rebuilt - so the caller has to follow up with reload_provider(). Named here
# rather than matched by prefix at the call site so there is one list to extend.
PROVIDER_ACTIONS = frozenset(name.replace("_", "-") for name in PROVIDER_BAKED_SETTINGS)


def rebuilds_the_provider(command_text: str) -> bool:
    """Whether running ``command_text`` leaves the model client out of date."""

    parts = command_text.split()
    return len(parts) >= 3 and parts[0] == "/config" and parts[1] in PROVIDER_ACTIONS


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


def _asset_command(name: str, description: str, fallback: str) -> SlashCommand:
    summary = " ".join(description.split()) or fallback
    if len(summary) > 80:
        summary = summary[:77].rstrip() + "..."
    return SlashCommand(f"/{name}", summary, f"/{name} ")


def workflow_commands(records: Sequence[WorkflowRecord]) -> list[SlashCommand]:
    """Expose each saved workflow as its own slash command.

    Workflows are user-authored, so they cannot be declared statically: they are
    discovered on disk (including in another agent's directory) and appear in the
    picker exactly like a built-in command, which is how the user expects to run
    them by name.
    """

    return [
        _asset_command(record.name, record.description, f"Run the {record.name} workflow.")
        for record in records
    ]


def skill_commands(records: Sequence[SkillRecord]) -> list[SlashCommand]:
    """Expose each skill as its own slash command.

    A skill normally loads on its own when the task matches its description.
    Naming it explicitly is the override: it forces that skill for the next turn
    instead of leaving the choice to the model.
    """

    return [
        _asset_command(record.name, record.description, f"Use the {record.name} skill.")
        for record in records
    ]


def command_suggestions(
    prefix: str, *, limit: int = 8, extra: Sequence[SlashCommand] = ()
) -> list[SlashCommand]:
    text = prefix.lstrip()
    if not text.startswith("/"):
        return []

    value_matches = _value_suggestions(text)
    if value_matches:
        return value_matches[:limit]

    # Built-ins first: a workflow can never shadow a command the app owns.
    available = [*SLASH_COMMANDS, *extra]
    command_prefix = text.rstrip()
    matches = [item for item in available if item.command.startswith(command_prefix)]
    if matches:
        return matches[:limit]
    return [item for item in SLASH_COMMANDS if item.command.startswith("/config")][:limit]


def render_suggestions(prefix: str, *, extra: Sequence[SlashCommand] = ()) -> str:
    suggestions = command_suggestions(prefix, extra=extra)
    if not suggestions:
        return ""
    return "\n".join(f"{item.command:<42} {item.description}" for item in suggestions)


# Which section each command belongs to, keyed by the command's first word so a
# section can never capture a command by accident: "/status" claims "/status",
# not a "/statusline" someone adds later.
_HELP_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Session", ("/help", "/doctor", "/status", "/compact", "/clear", "/quit", "/cancel")),
    ("Planning", ("/auto", "/plan", "/act", "/mode", "/deep-plan", "/plan-status", "/replan")),
    ("Goals", ("/goal",)),
    ("Terminal", ("/term",)),
    ("Workflows and skills", ("/workflows", "/skills")),
    ("Diagnostics", ("/debug",)),
    ("Configuration", ("/config",)),
)

# Where a command lands when no section claims it. It exists so that forgetting
# to file a new command costs it a good heading rather than costing it its only
# listing: /help is the answer to "what can I type", so a grouping that could
# silently drop an entry would be the exact bug this is here to prevent.
_UNFILED_SECTION = "Other"


def _section_for(command: str) -> str:
    head = command.split()[0]
    for title, members in _HELP_SECTIONS:
        if head in members:
            return title
    return _UNFILED_SECTION


def help_sections(
    *,
    workflows: Sequence[SlashCommand] = (),
    skills: Sequence[SlashCommand] = (),
) -> list[tuple[str, list[SlashCommand]]]:
    """Every command there is, grouped, in reading order.

    Workflows and skills come last and separately: they are discovered on disk
    and differ per project, so they are the part of the list the user is least
    likely to recognise and most likely to be looking for.
    """

    grouped: dict[str, list[SlashCommand]] = {title: [] for title, _ in _HELP_SECTIONS}
    grouped[_UNFILED_SECTION] = []
    for item in SLASH_COMMANDS:
        grouped[_section_for(item.command)].append(item)

    sections = [(title, grouped[title]) for title, _ in _HELP_SECTIONS if grouped[title]]
    if grouped[_UNFILED_SECTION]:
        sections.append((_UNFILED_SECTION, grouped[_UNFILED_SECTION]))
    if workflows:
        sections.append(("Saved workflows", list(workflows)))
    if skills:
        sections.append(("Skills", list(skills)))
    return sections


def render_help(
    *,
    workflows: Sequence[SlashCommand] = (),
    skills: Sequence[SlashCommand] = (),
) -> str:
    """The full command list, grouped and untruncated.

    Deliberately unlike :func:`render_suggestions`, which caps what it returns
    because it renders into a popup hovering over the conversation. ``/help`` is
    the opposite request - the user is asking what exists, not being offered a
    shortlist - so the cap that makes the popup usable is what made ``/help``
    wrong, and it has no business here.

    Each section is padded to its own widest command rather than to a shared
    column, so a long ``/config`` signature cannot push every other section's
    descriptions out to meet it.
    """

    blocks: list[str] = []
    for title, items in help_sections(workflows=workflows, skills=skills):
        width = max(len(item.command) for item in items)
        rows = [f"  {item.command:<{width}}  {item.description}" for item in items]
        blocks.append("\n".join([f"{title}:", *rows]))
    return "\n\n".join(blocks)


def command_completion(prefix: str, *, extra: Sequence[SlashCommand] = ()) -> str | None:
    suggestions = command_suggestions(prefix, limit=1, extra=extra)
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
    if action == "vision-model":
        if len(parts) < 3:
            return "command> Usage: /config vision-model <name|off>"
        value = " ".join(parts[2:])
        # "off"/"none" restores the default: images go to the main model.
        if value.strip().lower() in {"off", "none"}:
            value = ""
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"vision_model": value},
            live_fields={"vision_model"},
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
        # The caller rebuilds the client; see PROVIDER_ACTIONS.
        return "command> Updated api_key=<redacted>. Applied now."
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
            live_fields={"api_mode"},
            restart_required=False,
        )
    if action == "base-url":
        if len(parts) != 3:
            return "command> Usage: /config base-url <url>"
        return _apply_config_change(
            application,
            config_path=config_path,
            changes={"base_url": parts[2]},
            live_fields={"base_url"},
            restart_required=False,
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
        # max_context_tokens lives under the nested ``budgets`` block, so this
        # assigns the block wholesale rather than going through the helper.
        budgets = asdict(config.budgets)
        budgets["max_context_tokens"] = tokens
        try:
            validated = persist_config_updates(
                config, {"budgets": budgets}, explicit_path=config_path
            )
        except Exception as exc:
            return f"command> Config not changed: {exc}"
        config.budgets = validated.budgets
        # The compressor recomputes its budget from this on every check, so
        # re-pointing the number is the whole change.
        application.apply_context_window()
        return f"command> Updated max_context_tokens={tokens}. Applied now."
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
    if action == "live-code":
        if len(parts) != 3 or parts[2].strip().lower() not in {"on", "off"}:
            return "command> Usage: /config live-code <on|off>"
        enabled = parts[2].strip().lower() == "on"
        result = _apply_config_change(
            application,
            config_path=config_path,
            changes={"terminal_live_code": enabled},
            live_fields={"terminal_live_code"},
            restart_required=False,
        )
        if result.startswith("command> Config not changed"):
            return result
        if enabled:
            return (
                "command> Live code on. Files show up in a code window as the "
                "model writes them."
            )
        return (
            "command> Live code off. Writes report progress on one line only, "
            "and the finished code still shows in the approval dialog."
        )
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
                "Persist and switch the API mode.",
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

    live_code_prefix = "/config live-code "
    if prefix.startswith(live_code_prefix):
        value_prefix = prefix[len(live_code_prefix) :].strip().lower()
        return [
            SlashCommand(
                f"/config live-code {value}",
                "Show/hide the file being written, live, as the model streams it.",
            )
            for value in ("on", "off")
            if value.startswith(value_prefix)
        ]
    return []
