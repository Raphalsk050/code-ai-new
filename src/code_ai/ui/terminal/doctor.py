from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, NamedTuple

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, Input, OptionList, Static, TabbedContent, TabPane

from code_ai.config.defaults import DEFAULT_SAMPLING
from code_ai.config.loader import persist_config_updates
from code_ai.config.models import AppConfig, normalize_api_mode
from code_ai.index import build_embedding_client
from code_ai.providers.factory import PROVIDER_BAKED_SETTINGS
from code_ai.providers.model_listing import list_available_models
from code_ai.ui.terminal.clipboard import paste_from_system_clipboard

# The setup topics offered on the main menu, in the order a first-time user would
# naturally work through them: how to reach the provider, then which model, then
# workspace and preferences. Each id maps to a step builder below. How the model
# writes (sampling, reasoning) is not a setup step: it has the Model tab.
_STEPS: tuple[tuple[str, str, str], ...] = (
    ("api_mode", "API mode", "How Code-AI talks to the provider"),
    ("base_url", "Base URL", "Where the provider lives (validate reachability)"),
    ("api_key", "API key", "Provider credential (paste from clipboard)"),
    ("model", "Model", "Pick from the catalog and test it live"),
    ("vision_model", "Vision model", "Reads pasted images for a non-multimodal main model"),
    ("embedding_model", "Embedding model", "Semantic search over the code index"),
    ("workspace", "Workspace", "The project directory the agent works in"),
    ("language", "Language", "Language the agent replies in"),
    ("permission", "Permission mode", "When the agent must ask before acting"),
)

# A 1x1 PNG attached to the vision-model live test, so the test exercises the
# image path instead of only proving the model answers text.
_TEST_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ"
    "/pLvAAAAAElFTkSuQmCC"
)

_API_MODE_CHOICES = ("responses", "completions", "ollama")
_PERMISSION_CHOICES = ("ask", "auto", "bypass")
_EFFORT_CHOICES = ("none", "minimal", "low", "medium", "high", "xhigh")
_SUMMARY_CHOICES = ("none", "auto", "concise", "detailed")
_LANGUAGE_HINTS = ("en", "pt-BR", "pt", "es", "fr")


class _Knob(NamedTuple):
    """One numeric sampling control on the Model tab."""

    field: str
    label: str
    integer: bool
    low: float
    high: float | None
    step: float
    hint: str


# The numeric controls on the Model tab, most reached-for first. The bounds
# mirror SamplingConfig.validate() and only clamp the − / + buttons: a typed
# value outside them is refused by the validator, with its own message.
_KNOBS: tuple[_Knob, ...] = (
    _Knob(
        "temperature",
        "Temperature",
        False,
        0.0,
        2.0,
        0.1,
        "Randomness: low is focused and repeatable, high is varied. 0 to 2",
    ),
    _Knob(
        "top_p",
        "Top P",
        False,
        0.05,
        1.0,
        0.05,
        "Samples from the likeliest tokens covering this much probability. 0 to 1",
    ),
    _Knob(
        "top_k",
        "Top K",
        True,
        0,
        None,
        5,
        "Samples from the K likeliest tokens only. 0 turns it off",
    ),
    _Knob(
        "min_p",
        "Min P",
        False,
        0.0,
        1.0,
        0.01,
        "Drops tokens less likely than this share of the top one. 0 to 1",
    ),
    _Knob(
        "presence_penalty",
        "Presence penalty",
        False,
        -2.0,
        2.0,
        0.1,
        "Above 0 steers towards new topics and away from loops. -2 to 2",
    ),
    _Knob(
        "frequency_penalty",
        "Frequency penalty",
        False,
        -2.0,
        2.0,
        0.1,
        "Above 0 discourages repeating the same words. -2 to 2",
    ),
)
_KNOB_BY_FIELD = {knob.field: knob for knob in _KNOBS}

# The pick-one controls on the same tab. "none" stands for "not sent".
_CHOICE_KNOBS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "reasoning_effort",
        "Reasoning effort",
        _EFFORT_CHOICES,
        "How hard the model thinks before answering. On many local servers it "
        "is also the switch that turns thinking on.",
    ),
    (
        "reasoning_summary",
        "Reasoning summary",
        _SUMMARY_CHOICES,
        "Summary of the thinking. Responses API only.",
    ),
)

# How long typing has to pause before a Model-tab value is saved.
_APPLY_DELAY_S = 0.6


def _format_knob(value: Any) -> str:
    """A sampling value as its field shows it; empty means "not sent"."""

    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value) if value else ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _parse_knob(field: str, raw: str) -> Any:
    """The typed text as the value the config stores, or ValueError saying why not."""

    if field == "extra_body":
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON ({exc.msg})") from None
        if not isinstance(value, dict):
            raise ValueError('must be a JSON object, like {"repetition_penalty": 1.05}')
        return value
    if not raw:
        return None
    knob = _KNOB_BY_FIELD[field]
    try:
        return int(raw) if knob.integer else float(raw)
    except ValueError:
        kind = "a whole number" if knob.integer else "a number"
        raise ValueError(f"{raw!r} is not {kind}") from None


class DoctorModal(ModalScreen[None]):
    """Step-by-step first-run setup, so nothing has to be edited by hand on disk.

    Two tabs. Setup is a main menu of setup topics; picking one opens a focused
    sub-step with a "‹ Menu" button back to the top. Values can be pasted from
    the system clipboard, the base URL can be checked for reachability, and a
    chosen model can be tested with a live call - all without leaving the
    dialog. Model holds the sampling controls on one page, each saved and put
    in force the moment it changes. Every save is persisted to the config file
    immediately.
    """

    BINDINGS = [("escape", "close", "Close")]

    def __init__(
        self,
        application: Any,
        *,
        config_path: Path | None = None,
        on_change: Any = None,
        tab: str = "setup",
    ) -> None:
        super().__init__()
        self._application = application
        self._config_path = config_path
        # Called after any successful save so the host app can refresh its
        # status line / logo to reflect the new configuration.
        self._on_change = on_change
        self._step = "menu"
        self._step_title = "Code-AI setup"
        self._initial_tab = f"doctor-tab-{tab}"
        # Model-tab edits waiting for typing to pause, by field.
        self._pending_knobs: dict[str, Timer] = {}

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def compose(self) -> ComposeResult:
        with Vertical(id="doctor-dialog"):
            with Horizontal(id="doctor-header"):
                yield Button("‹ Menu", id="doctor-back", classes="doctor-hidden")
                yield Static("Code-AI setup", id="doctor-title")
                yield Button("✕", id="doctor-close")
            with TabbedContent(initial=self._initial_tab, id="doctor-tabs"):
                with TabPane("Setup", id="doctor-tab-setup"):
                    yield VerticalScroll(id="doctor-body")
                with TabPane("Model", id="doctor-tab-model"):
                    yield VerticalScroll(*self._sampling_widgets(), id="doctor-sampling")
            yield Static("", id="doctor-status")

    async def on_mount(self) -> None:
        await self._set_step("menu")

    @property
    def _config(self) -> AppConfig:
        return self._application.session.config

    # ------------------------------------------------------------------ #
    # Step routing
    # ------------------------------------------------------------------ #
    async def _set_step(self, step: str) -> None:
        self._step = step
        body = self.query_one("#doctor-body", VerticalScroll)
        await body.remove_children()
        title, widgets = self._build_step(step)
        await body.mount(*widgets)
        self._step_title = title
        self._sync_header()
        self._status("")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._sync_header()

    def _sync_header(self) -> None:
        """Title and back button for whichever tab is showing.

        The back button walks the Setup menu, so it has nothing to do on the
        Model tab, which is a single page.
        """

        on_setup = self.query_one("#doctor-tabs", TabbedContent).active == "doctor-tab-setup"
        title = self._step_title if on_setup else "Model behavior"
        self.query_one("#doctor-title", Static).update(title)
        hide_back = not on_setup or self._step == "menu"
        self.query_one("#doctor-back", Button).set_class(hide_back, "doctor-hidden")

    def _build_step(self, step: str) -> tuple[str, list[Any]]:
        if step == "menu":
            return "Code-AI setup", self._menu_widgets()
        if step == "api_mode":
            return "API mode", self._choice_widgets(
                "api_mode",
                _API_MODE_CHOICES,
                self._config.api_mode,
                "How Code-AI reaches the model. Applied to the running agent on save.",
            )
        if step == "permission":
            return "Permission mode", self._choice_widgets(
                "permission_mode",
                _PERMISSION_CHOICES,
                self._config.permission_mode,
                "ask prompts before write/run tools · auto runs freely · bypass never asks.",
            )
        if step == "base_url":
            return "Base URL", self._text_widgets(
                "base_url",
                self._config.base_url,
                note="The provider endpoint. Validate to check it is reachable.",
                validate=True,
            )
        if step == "api_key":
            return "API key", self._text_widgets(
                "api_key",
                "",
                note="Stored redacted. Leave blank for local endpoints that need no key.",
                password=True,
            )
        if step == "model":
            return "Model", self._model_widgets("model")
        if step == "vision_model":
            return "Vision model", self._model_widgets("vision_model")
        if step == "embedding_model":
            return "Embedding model", self._model_widgets("embedding_model")
        if step == "workspace":
            return "Workspace", self._text_widgets(
                "workspace",
                str(self._config.workspace),
                note="Absolute path to the project the agent operates in.",
            )
        if step == "language":
            return "Language", self._text_widgets(
                "language",
                self._config.language,
                note=f"Language code the agent replies in (e.g. {', '.join(_LANGUAGE_HINTS)}).",
            )
        return "Code-AI setup", self._menu_widgets()

    # ------------------------------------------------------------------ #
    # Menu
    # ------------------------------------------------------------------ #
    def _menu_widgets(self) -> list[Any]:
        widgets: list[Any] = [
            Static(
                "Choose what to configure. Everything is saved straight to your "
                "config file - no need to edit anything by hand. Temperature, "
                "top_k and the other sampling settings are on the Model tab.",
                classes="doctor-intro",
            )
        ]
        for step_id, label, hint in _STEPS:
            current = self._current_summary(step_id)
            button = Button(f"{label}   ·   {current}", id=f"doctor-menu-{step_id}")
            button.tooltip = hint
            widgets.append(button)
        widgets.append(Button("Done", variant="success", id="doctor-close-2"))
        return widgets

    def _current_summary(self, step_id: str) -> str:
        config = self._config
        if step_id == "api_mode":
            return config.api_mode
        if step_id == "base_url":
            return config.base_url
        if step_id == "api_key":
            return "configured" if config.api_key else "not set"
        if step_id == "model":
            return config.model
        if step_id == "vision_model":
            return config.vision_model or "not set"
        if step_id == "embedding_model":
            return config.index.embedding_model or "not set (lexical only)"
        if step_id == "workspace":
            return str(config.workspace)
        if step_id == "language":
            return config.language
        if step_id == "permission":
            return config.permission_mode
        return ""

    # ------------------------------------------------------------------ #
    # Choice steps (pick one of a fixed set)
    # ------------------------------------------------------------------ #
    def _choice_widgets(
        self, field: str, choices: tuple[str, ...], current: str, note: str
    ) -> list[Any]:
        widgets: list[Any] = [Static(note, classes="doctor-note")]
        for value in choices:
            selected = value == current
            label = f"{value}  ✓" if selected else value
            widgets.append(
                Button(
                    label,
                    id=f"doctor-choice-{field}-{value}",
                    variant="success" if selected else "default",
                )
            )
        return widgets

    # ------------------------------------------------------------------ #
    # Text steps (type or paste a value)
    # ------------------------------------------------------------------ #
    def _text_widgets(
        self,
        field: str,
        current: str,
        *,
        note: str,
        password: bool = False,
        validate: bool = False,
    ) -> list[Any]:
        widgets: list[Any] = [Static(note, classes="doctor-note")]
        widgets.append(
            Input(
                value=current,
                password=password,
                id=f"doctor-input-{field}",
                classes="doctor-input",
            )
        )
        row: list[Any] = [
            Button("Paste", id=f"doctor-paste-{field}"),
            Button("Save", variant="primary", id=f"doctor-save-{field}"),
        ]
        if validate:
            row.insert(1, Button("Validate", id=f"doctor-validate-{field}"))
        widgets.append(Horizontal(*row, classes="doctor-actions"))
        return widgets

    def _model_widgets(self, field: str) -> list[Any]:
        if field == "model":
            note = (
                "Type or paste a model name, or list your provider's catalog and "
                "pick one. Test runs a quick live call to confirm it responds."
            )
            value = self._config.model
        elif field == "embedding_model":
            note = (
                "Turns on semantic search over the code index: search_index then "
                "finds code by meaning, not only by the words in it. Pick an "
                "embedding model your provider serves (e.g. nomic-embed-text, "
                "mxbai-embed-large, text-embedding-3-small). Test asks it for a "
                "vector; saving applies right away and embeds the indexed chunks "
                "in the background. Leave empty and save for lexical search only."
            )
            value = self._config.index.embedding_model
        else:  # vision_model
            note = (
                "Vision sidekick that reads pasted images when the main model is "
                "not multimodal. Leave empty and save to send images to the main "
                "model instead. Test sends a tiny image to confirm the model "
                "accepts one."
            )
            value = self._config.vision_model
        widgets: list[Any] = [
            Static(note, classes="doctor-note"),
            Input(
                value=value,
                id=f"doctor-input-{field}",
                classes="doctor-input",
            ),
            Horizontal(
                Button("Paste", id=f"doctor-paste-{field}"),
                Button("List models", id=f"doctor-list-{field}"),
                Button("Test model", id=f"doctor-test-{field}"),
                Button("Save", variant="primary", id=f"doctor-save-{field}"),
                classes="doctor-actions",
            ),
            OptionList(
                id=f"doctor-model-list-{field}",
                classes="doctor-model-list doctor-hidden",
            ),
        ]
        return widgets

    # ------------------------------------------------------------------ #
    # Model tab (sampling, applied live)
    # ------------------------------------------------------------------ #
    def _sampling_widgets(self) -> list[Any]:
        sampling = self._config.sampling
        widgets: list[Any] = [
            Static(
                "How the model writes. Every change is saved and used from the "
                "next model call on - no restart, no Save button. Leave a field "
                "empty to let the server decide; ↺ puts back Code-AI's default "
                "(tuned for Qwen3.8-27B in thinking mode).",
                classes="doctor-intro",
            )
        ]
        for knob in _KNOBS:
            default = Button(
                "↺", id=f"doctor-default-{knob.field}", classes="doctor-knob-step", compact=True
            )
            shown = _format_knob(DEFAULT_SAMPLING.get(knob.field)) or "not sent"
            default.tooltip = f"Code-AI default: {shown}"
            widgets.append(
                Horizontal(
                    Static(knob.label, classes="doctor-knob-label"),
                    Button(
                        "−", id=f"doctor-dec-{knob.field}", classes="doctor-knob-step", compact=True
                    ),
                    Input(
                        value=_format_knob(getattr(sampling, knob.field)),
                        placeholder="server default",
                        type="integer" if knob.integer else "number",
                        valid_empty=True,
                        id=f"doctor-knob-{knob.field}",
                        classes="doctor-knob-input",
                        compact=True,
                    ),
                    Button(
                        "+", id=f"doctor-inc-{knob.field}", classes="doctor-knob-step", compact=True
                    ),
                    default,
                    classes="doctor-knob",
                )
            )
            widgets.append(Static(knob.hint, classes="doctor-knob-hint"))
        for field, label, choices, hint in _CHOICE_KNOBS:
            current = getattr(sampling, field) or "none"
            buttons = [
                Button(
                    f"{value} ✓" if value == current else value,
                    id=f"doctor-pick-{field}-{value}",
                    classes=f"doctor-pick-{field}",
                    variant="success" if value == current else "default",
                    compact=True,
                )
                for value in choices
            ]
            widgets.append(
                Horizontal(
                    Static(label, classes="doctor-knob-label"), *buttons, classes="doctor-knob"
                )
            )
            widgets.append(Static(hint, classes="doctor-knob-hint"))
        widgets.append(
            Horizontal(
                Static("Extra body", classes="doctor-knob-label"),
                Input(
                    value=_format_knob(sampling.extra_body),
                    placeholder='{"repetition_penalty": 1.05}',
                    id="doctor-knob-extra_body",
                    classes="doctor-knob-json",
                    compact=True,
                ),
                classes="doctor-knob",
            )
        )
        widgets.append(
            Static(
                "Any other server setting, as a JSON object. Sent in completions "
                "and responses mode; native ollama mode does not read it.",
                classes="doctor-knob-hint",
            )
        )
        widgets.append(Button("Reset all to Code-AI defaults", id="doctor-sampling-reset"))
        return widgets

    def on_input_changed(self, event: Input.Changed) -> None:
        field = self._knob_field(event.input)
        if field is None:
            return
        # Typing "0.75" passes through "0" and "0." on the way, and saving each
        # would put values nobody chose in force. Waiting for typing to pause
        # saves the one the user settled on; Enter skips the wait.
        self._cancel_pending(field)
        self._pending_knobs[field] = self.set_timer(
            _APPLY_DELAY_S, lambda: self._apply_knob(field)
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        field = self._knob_field(event.input)
        if field is not None:
            self._apply_knob(field)

    @staticmethod
    def _knob_field(widget: Input) -> str | None:
        widget_id = widget.id or ""
        if not widget_id.startswith("doctor-knob-"):
            return None
        return widget_id[len("doctor-knob-") :]

    def _cancel_pending(self, field: str) -> None:
        pending = self._pending_knobs.pop(field, None)
        if pending is not None:
            pending.stop()

    def _apply_knob(self, field: str) -> None:
        """Save what the field holds, if it is a value and a new one."""

        self._cancel_pending(field)
        raw = self.query_one(f"#doctor-knob-{field}", Input).value.strip()
        try:
            value = _parse_knob(field, raw)
        except ValueError as exc:
            self._status(f"✗ {field}: {exc}")
            return
        if value == getattr(self._config.sampling, field):
            return
        self._status(self._apply_sampling({field: value}))

    def _step_knob(self, field: str, direction: int) -> None:
        knob = _KNOB_BY_FIELD[field]
        widget = self.query_one(f"#doctor-knob-{field}", Input)
        try:
            current = _parse_knob(field, widget.value.strip())
        except ValueError:
            current = None
        if current is None:
            # Nothing typed: step from the default rather than from zero,
            # which for top_p is not even a legal value.
            current = DEFAULT_SAMPLING.get(field)
        base = float(current) if current is not None else max(knob.low, 0.0)
        stepped = round(base + direction * knob.step, 4)
        if knob.high is not None:
            stepped = min(knob.high, stepped)
        stepped = max(knob.low, stepped)
        value: Any = int(stepped) if knob.integer else stepped
        self._cancel_pending(field)
        widget.value = _format_knob(value)
        self._status(self._apply_sampling({field: value}))

    def _default_knob(self, field: str) -> None:
        value = DEFAULT_SAMPLING.get(field)
        self._cancel_pending(field)
        self.query_one(f"#doctor-knob-{field}", Input).value = _format_knob(value)
        self._status(self._apply_sampling({field: value}))

    def _pick(self, spec: str) -> None:
        field, _, value = spec.partition("-")
        self._status(self._apply_sampling({field: None if value == "none" else value}))
        self._mark_choice(field)

    def _mark_choice(self, field: str) -> None:
        current = getattr(self._config.sampling, field) or "none"
        for button in self.query(f".doctor-pick-{field}").results(Button):
            value = (button.id or "").rpartition("-")[2]
            button.label = f"{value} ✓" if value == current else value
            button.variant = "success" if value == current else "default"

    def _reset_sampling(self) -> None:
        for field in list(self._pending_knobs):
            self._cancel_pending(field)
        changes: dict[str, Any] = {knob.field: DEFAULT_SAMPLING.get(knob.field) for knob in _KNOBS}
        for field, *_ in _CHOICE_KNOBS:
            changes[field] = DEFAULT_SAMPLING.get(field)
        changes["extra_body"] = dict(DEFAULT_SAMPLING.get("extra_body") or {})
        self._status(self._apply_sampling(changes, summary="the Code-AI defaults"))
        sampling = self._config.sampling
        for knob in _KNOBS:
            widget = self.query_one(f"#doctor-knob-{knob.field}", Input)
            widget.value = _format_knob(getattr(sampling, knob.field))
        self.query_one("#doctor-knob-extra_body", Input).value = _format_knob(sampling.extra_body)
        for field, *_ in _CHOICE_KNOBS:
            self._mark_choice(field)

    def _apply_sampling(self, changes: dict[str, Any], *, summary: str | None = None) -> str:
        """Persist sampling changes and put them in force for the next model call."""

        config = self._config
        sampling = asdict(config.sampling)
        sampling.update(changes)
        try:
            validated = persist_config_updates(
                config, {"sampling": sampling}, explicit_path=self._config_path
            )
        except Exception as exc:  # noqa: BLE001
            return f"✗ Not saved: {exc}"
        # Nothing to rebuild: every provider reads config.sampling on each
        # request, so the next call already goes out with these values.
        config.sampling = validated.sampling
        self._retry_sampling()
        if self._on_change is not None:
            self._on_change()
        shown = summary or ", ".join(
            f"{key}={_format_knob(value) or 'not sent'}" for key, value in changes.items()
        )
        return f"✓ Saved {shown} (applied from the next model call)"

    def _retry_sampling(self) -> None:
        """Let a provider that gave up on sampling controls send them again.

        An endpoint that refused them once has the provider stop sending any
        for the rest of the session, which would quietly drop the values the
        user just chose. If it refuses again, it falls back the same way.
        """

        provider = getattr(self._application, "provider", None)
        retry = getattr(provider, "retry_sampling", None)
        if retry is not None:
            retry()

    # ------------------------------------------------------------------ #
    # Button handling
    # ------------------------------------------------------------------ #
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id in {"doctor-close", "doctor-close-2"}:
            self._close()
        elif button_id == "doctor-back":
            await self._set_step("menu")
        elif button_id.startswith("doctor-menu-"):
            await self._set_step(button_id[len("doctor-menu-") :])
        elif button_id.startswith("doctor-choice-"):
            await self._on_choice(button_id[len("doctor-choice-") :])
        elif button_id.startswith("doctor-paste-"):
            self._paste_into(button_id[len("doctor-paste-") :])
        elif button_id.startswith("doctor-save-"):
            await self._save_text(button_id[len("doctor-save-") :])
        elif button_id.startswith("doctor-validate-"):
            await self._validate_base_url(button_id[len("doctor-validate-") :])
        elif button_id.startswith("doctor-list-"):
            await self._list_models(button_id[len("doctor-list-") :])
        elif button_id.startswith("doctor-test-"):
            await self._test_model(button_id[len("doctor-test-") :])
        elif button_id.startswith("doctor-dec-"):
            self._step_knob(button_id[len("doctor-dec-") :], -1)
        elif button_id.startswith("doctor-inc-"):
            self._step_knob(button_id[len("doctor-inc-") :], 1)
        elif button_id.startswith("doctor-default-"):
            self._default_knob(button_id[len("doctor-default-") :])
        elif button_id.startswith("doctor-pick-"):
            self._pick(button_id[len("doctor-pick-") :])
        elif button_id == "doctor-sampling-reset":
            self._reset_sampling()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Picking a listed model drops its name into the step's input field
        # (the option list id carries which model field this step edits).
        list_id = event.option_list.id or ""
        field = list_id[len("doctor-model-list-") :] or "model"
        self.query_one(f"#doctor-input-{field}", Input).value = str(event.option.prompt)

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _paste_into(self, field: str) -> None:
        text = paste_from_system_clipboard()
        if text is None:
            self._status("✗ Clipboard is unavailable on this system.")
            return
        self.query_one(f"#doctor-input-{field}", Input).value = text.strip()
        self._status("Pasted from clipboard.")

    async def _on_choice(self, spec: str) -> None:
        field, _, value = spec.partition("-")
        if field == "api_mode":
            self._status(
                self._apply({"api_mode": normalize_api_mode(value)}, restart=False)
            )
        else:  # permission_mode
            self._status(self._apply({field: value}, restart=False))
        await self._set_step(self._step)  # repaint to move the ✓ marker

    async def _save_text(self, field: str) -> None:
        value = self.query_one(f"#doctor-input-{field}", Input).value.strip()
        if field == "embedding_model":
            self._status(await self._apply_embedding_model(value))
            return
        if field == "api_key":
            self._status(self._apply({"api_key": value}, restart=False, secret=True))
            return
        if field == "workspace":
            value = str(Path(value).expanduser().resolve())
        restart = field == "workspace"
        self._status(self._apply({field: value}, restart=restart))

    async def _validate_base_url(self, field: str) -> None:
        value = self.query_one(f"#doctor-input-{field}", Input).value.strip()
        self._status("Validating base URL…")
        try:
            candidate = self._candidate_config(base_url=value)
            models = await list_available_models(candidate)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            self._status(f"✗ {exc}")
            return
        self._status(f"✓ Reachable — {len(models)} model(s) available.")

    async def _list_models(self, field: str) -> None:
        self._status("Fetching models…")
        try:
            # Listing only needs the endpoint; the live config already carries a
            # valid main model, so no override (the vision field may be empty,
            # which AppConfig would reject as a main model). Embeddings may be
            # served somewhere else entirely, so that step asks their endpoint.
            catalog = (
                self._embedding_catalog_config()
                if field == "embedding_model"
                else self._candidate_config()
            )
            models = await list_available_models(catalog)
        except Exception as exc:  # noqa: BLE001
            self._status(f"✗ {exc}")
            return
        option_list = self.query_one(f"#doctor-model-list-{field}", OptionList)
        option_list.clear_options()
        option_list.add_options(models)
        option_list.set_class(False, "doctor-hidden")
        self._status(f"{len(models)} model(s) — pick one to fill the field.")

    async def _test_model(self, field: str) -> None:
        from code_ai.providers.factory import create_provider
        from code_ai.providers.models import ImageContent, Message, ModelRequest

        if field == "embedding_model":
            await self._test_embedding_model()
            return
        fallback = self._config.model if field == "model" else self._config.vision_model
        model = self.query_one(f"#doctor-input-{field}", Input).value.strip() or fallback
        if not model:
            self._status("✗ Type a model name to test.")
            return
        self._status(f"Testing {model}…")
        # The vision test attaches a tiny image so it exercises the image path;
        # a text-only probe would pass for models that cannot see at all.
        images = [ImageContent(data=_TEST_IMAGE_B64)] if field == "vision_model" else []
        try:
            candidate = self._candidate_config(model=model)
            provider = create_provider(candidate)
            try:
                request = ModelRequest(
                    model=candidate.model,
                    messages=[
                        Message(
                            role="user",
                            content="Reply with the single word OK.",
                            images=images,
                        )
                    ],
                    max_output_tokens=32,
                    use_remote_conversation_state=False,
                )
                response = await asyncio.wait_for(
                    provider.complete(request),
                    timeout=min(30.0, candidate.budgets.model_timeout()),
                )
            finally:
                await provider.close()
        except Exception as exc:  # noqa: BLE001
            self._status(f"✗ {exc}")
            return
        reply = (response.text or "").strip().replace("\n", " ")
        self._status(f"✓ {model} responded: {reply[:60] or '[empty reply]'}")

    def _embedding_catalog_config(self) -> AppConfig:
        """A config pointed at wherever embeddings are served, for listing models.

        Embeddings default to the chat provider's endpoint, so usually this is
        the live config. When ``index.embedding_base_url`` / ``embedding_api_mode``
        send them elsewhere, the catalog has to come from there instead - the
        chat provider may not serve a single embedding model.
        """

        index = self._config.index
        overrides: dict[str, Any] = {}
        if index.embedding_base_url:
            overrides["base_url"] = index.embedding_base_url
        if index.embedding_api_mode:
            overrides["api_mode"] = (
                "ollama" if index.embedding_api_mode == "ollama" else "completions"
            )
        return self._candidate_config(**overrides)

    async def _test_embedding_model(self) -> None:
        """Ask the model for one vector, which is the only proof that matters.

        A model that answers chat may still have no embeddings endpoint, and one
        that does may not be served under the name that was typed. The reply is
        reported with its dimension, because that is what tells the user they
        got an embedding model rather than something that merely responded.
        """

        model = self.query_one("#doctor-input-embedding_model", Input).value.strip()
        if not model:
            self._status("✗ Type an embedding model name to test.")
            return
        self._status(f"Testing {model}…")
        try:
            candidate = self._candidate_config(index=self._index_settings(model))
            client = build_embedding_client(candidate)
            if client is None:  # pragma: no cover - guarded by the empty check above
                self._status("✗ No embedding model to test.")
                return
            try:
                vectors = await asyncio.wait_for(
                    client.embed(["def parse_tool_call(payload): return payload"]),
                    timeout=min(30.0, candidate.budgets.model_timeout()),
                )
            finally:
                await client.close()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            self._status(f"✗ {exc}")
            return
        self._status(f"✓ {model} returned a {len(vectors[0])}-dimension vector.")

    def _index_settings(self, model: str) -> dict[str, Any]:
        """The index config as a full mapping, with the embedding model swapped.

        Persisting has to carry every index key: the saved file replaces the
        whole ``index`` object, so a partial mapping would silently reset the
        other index settings to their defaults.
        """

        settings = asdict(self._config.index)
        settings["embedding_model"] = model
        return settings

    async def _apply_embedding_model(self, value: str) -> str:
        config = self._config
        try:
            validated = persist_config_updates(
                config, {"index": self._index_settings(value)}, explicit_path=self._config_path
            )
        except Exception as exc:  # noqa: BLE001
            return f"✗ Not saved: {exc}"
        config.index = validated.index
        if self._on_change is not None:
            self._on_change()
        # Applied live: the running index swaps its embedding client, so
        # search_index answers semantically without restarting the session.
        apply = getattr(self._application, "set_embedding_model", None)
        if apply is None:
            return f"✓ Saved embedding_model={value or 'none'} (restart to apply)"
        try:
            pending = await apply(value)
        except Exception as exc:  # noqa: BLE001
            return f"✓ Saved embedding_model={value or 'none'} — not applied: {exc}"
        if not value:
            return "✓ Saved: semantic search off, search_index stays lexical (applied now)"
        if pending <= 0:
            return f"✓ Saved embedding_model={value} — already embedded (applied now)"
        if not self._application.start_embedding_backfill():
            return (
                f"✓ Saved embedding_model={value} (applied now) — "
                f"{pending} chunk(s) get vectors on the next /index"
            )
        return (
            f"✓ Saved embedding_model={value} (applied now) — embedding "
            f"{pending} chunk(s) in the background; see /index status"
        )

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #
    def _candidate_config(self, **overrides: Any) -> AppConfig:
        """An AppConfig reflecting the live config plus in-progress overrides.

        Used to test a base URL / model with the value the user just typed,
        before it is saved, without mutating the running configuration.
        """
        data = self._config.to_dict()
        data.update(overrides)
        return AppConfig.from_mapping(data)

    def _apply(
        self, changes: dict[str, Any], *, restart: bool, secret: bool = False
    ) -> str:
        config = self._config
        try:
            validated = persist_config_updates(
                config, changes, explicit_path=self._config_path
            )
        except Exception as exc:  # noqa: BLE001
            return f"✗ Not saved: {exc}"
        # Apply live so the doctor's own tests (and the next model call) use the
        # new value.
        for key in changes:
            if hasattr(config, key):
                setattr(config, key, getattr(validated, key))
        # Some of those are read while the model client is constructed, so the
        # live config would otherwise disagree with the client still in use -
        # the dialog would report a new base URL while the agent kept calling
        # the old one. Rebuilding is async and this is not, so it goes through
        # a worker rather than being dropped.
        if set(changes) & PROVIDER_BAKED_SETTINGS:
            self.run_worker(self._application.reload_provider(), exclusive=False)
        if self._on_change is not None:
            self._on_change()
        if secret:
            shown = "api_key=<redacted>"
        else:
            shown = ", ".join(f"{key}={value}" for key, value in changes.items())
        suffix = " (restart to apply to the running agent)" if restart else " (applied now)"
        return f"✓ Saved {shown}{suffix}"

    def _status(self, message: str) -> None:
        self.query_one("#doctor-status", Static).update(message)

    def _close(self) -> None:
        # A value typed just before closing is still waiting for typing to
        # pause. It was meant, so it is saved rather than dropped with the
        # dialog.
        for field in list(self._pending_knobs):
            self._apply_knob(field)
        self.dismiss(None)

    def action_close(self) -> None:
        self._close()
