from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static

from code_ai.config.loader import persist_config_updates
from code_ai.config.models import AppConfig, normalize_api_mode
from code_ai.providers.model_listing import list_available_models
from code_ai.ui.terminal.clipboard import paste_from_system_clipboard

# The setup topics offered on the main menu, in the order a first-time user would
# naturally work through them: how to reach the provider, then which model, then
# workspace and preferences. Each id maps to a step builder below.
_STEPS: tuple[tuple[str, str, str], ...] = (
    ("api_mode", "API mode", "How Code-AI talks to the provider"),
    ("base_url", "Base URL", "Where the provider lives (validate reachability)"),
    ("api_key", "API key", "Provider credential (paste from clipboard)"),
    ("model", "Model", "Pick from the catalog and test it live"),
    ("vision_model", "Vision model", "Reads pasted images for a non-multimodal main model"),
    ("workspace", "Workspace", "The project directory the agent works in"),
    ("language", "Language", "Language the agent replies in"),
    ("permission", "Permission mode", "When the agent must ask before acting"),
    ("effort", "Reasoning effort", "Thinking budget (OpenAI Responses API)"),
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
_LANGUAGE_HINTS = ("en", "pt-BR", "pt", "es", "fr")


class DoctorModal(ModalScreen[None]):
    """Step-by-step first-run setup, so nothing has to be edited by hand on disk.

    A single modal with a main menu of setup topics; picking one opens a focused
    sub-step with a "‹ Menu" button back to the top. Values can be pasted from the
    system clipboard, the base URL can be checked for reachability, and a chosen
    model can be tested with a live call - all without leaving the dialog. Each
    save is persisted to the config file immediately.
    """

    BINDINGS = [("escape", "close", "Close")]

    def __init__(
        self,
        application: Any,
        *,
        config_path: Path | None = None,
        on_change: Any = None,
    ) -> None:
        super().__init__()
        self._application = application
        self._config_path = config_path
        # Called after any successful save so the host app can refresh its
        # status line / logo to reflect the new configuration.
        self._on_change = on_change
        self._step = "menu"

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def compose(self) -> ComposeResult:
        with Vertical(id="doctor-dialog"):
            with Horizontal(id="doctor-header"):
                yield Button("‹ Menu", id="doctor-back", classes="doctor-hidden")
                yield Static("Code-AI setup", id="doctor-title")
                yield Button("✕", id="doctor-close")
            yield VerticalScroll(id="doctor-body")
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
        self.query_one("#doctor-title", Static).update(title)
        self.query_one("#doctor-back", Button).set_class(step == "menu", "doctor-hidden")
        self._status("")

    def _build_step(self, step: str) -> tuple[str, list[Any]]:
        if step == "menu":
            return "Code-AI setup", self._menu_widgets()
        if step == "api_mode":
            return "API mode", self._choice_widgets(
                "api_mode",
                _API_MODE_CHOICES,
                self._config.api_mode,
                "How Code-AI reaches the model. Restart to apply to the running agent.",
            )
        if step == "permission":
            return "Permission mode", self._choice_widgets(
                "permission_mode",
                _PERMISSION_CHOICES,
                self._config.permission_mode,
                "ask prompts before write/run tools · auto runs freely · bypass never asks.",
            )
        if step == "effort":
            return "Reasoning effort", self._choice_widgets(
                "reasoning_effort",
                _EFFORT_CHOICES,
                self._config.sampling.reasoning_effort or "none",
                "Thinking budget for OpenAI Responses reasoning models.",
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
                "config file - no need to edit anything by hand.",
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
        if step_id == "workspace":
            return str(config.workspace)
        if step_id == "language":
            return config.language
        if step_id == "permission":
            return config.permission_mode
        if step_id == "effort":
            return config.sampling.reasoning_effort or "none"
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
            OptionList(id=f"doctor-model-list-{field}", classes="doctor-hidden"),
        ]
        return widgets

    # ------------------------------------------------------------------ #
    # Button handling
    # ------------------------------------------------------------------ #
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id in {"doctor-close", "doctor-close-2"}:
            self.dismiss(None)
        elif button_id == "doctor-back":
            await self._set_step("menu")
        elif button_id.startswith("doctor-menu-"):
            await self._set_step(button_id[len("doctor-menu-") :])
        elif button_id.startswith("doctor-choice-"):
            await self._on_choice(button_id[len("doctor-choice-") :])
        elif button_id.startswith("doctor-paste-"):
            self._paste_into(button_id[len("doctor-paste-") :])
        elif button_id.startswith("doctor-save-"):
            self._save_text(button_id[len("doctor-save-") :])
        elif button_id.startswith("doctor-validate-"):
            await self._validate_base_url(button_id[len("doctor-validate-") :])
        elif button_id.startswith("doctor-list-"):
            await self._list_models(button_id[len("doctor-list-") :])
        elif button_id.startswith("doctor-test-"):
            await self._test_model(button_id[len("doctor-test-") :])

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
        if field == "reasoning_effort":
            self._status(self._apply_effort(value))
        elif field == "api_mode":
            self._status(
                self._apply({"api_mode": normalize_api_mode(value)}, restart=True)
            )
        else:  # permission_mode
            self._status(self._apply({field: value}, restart=False))
        await self._set_step(self._step)  # repaint to move the ✓ marker

    def _save_text(self, field: str) -> None:
        value = self.query_one(f"#doctor-input-{field}", Input).value.strip()
        if field == "api_key":
            self._status(self._apply({"api_key": value}, restart=True, secret=True))
            return
        if field == "workspace":
            value = str(Path(value).expanduser().resolve())
        restart = field in {"base_url", "workspace"}
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
            # which AppConfig would reject as a main model).
            models = await list_available_models(self._candidate_config())
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
        # new value; restart-only fields still need a restart for the running
        # provider, which is noted below.
        for key in changes:
            if hasattr(config, key):
                setattr(config, key, getattr(validated, key))
        if self._on_change is not None:
            self._on_change()
        if secret:
            shown = "api_key=<redacted>"
        else:
            shown = ", ".join(f"{key}={value}" for key, value in changes.items())
        suffix = " (restart to apply to the running agent)" if restart else " (applied now)"
        return f"✓ Saved {shown}{suffix}"

    def _apply_effort(self, value: str) -> str:
        config = self._config
        sampling = asdict(config.sampling)
        sampling["reasoning_effort"] = None if value == "none" else value
        try:
            validated = persist_config_updates(
                config, {"sampling": sampling}, explicit_path=self._config_path
            )
        except Exception as exc:  # noqa: BLE001
            return f"✗ Not saved: {exc}"
        config.sampling = validated.sampling
        if self._on_change is not None:
            self._on_change()
        return f"✓ Saved reasoning_effort={value} (applied now)"

    def _status(self, message: str) -> None:
        self.query_one("#doctor-status", Static).update(message)

    def action_close(self) -> None:
        self.dismiss(None)
