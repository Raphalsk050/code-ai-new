"""The Sub-agents tab: limits, the tool-call protocol, and the model roster.

Driven through the real Textual screen rather than the methods behind it, so a
button that is never wired to its handler fails here instead of in the user's
session.
"""

from __future__ import annotations

import json

from textual.widgets import Button, Input, Static, TextArea

from code_ai.config.models import AppConfig
from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.doctor import DoctorModal
from tests.unit.test_terminal_ui import FakeTerminalApplication


class FakeApplication(FakeTerminalApplication):
    """The shared terminal fake, with the settings this tab reads."""

    def __init__(self, tmp_path, **settings) -> None:
        super().__init__(tmp_path)
        self.session.config = AppConfig.from_mapping(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "model": "main-model",
                **settings,
            }
        )


async def open_subagents(tmp_path, cfg_path, **settings):
    """Open the Doctor straight on the Sub-agents tab, the way the user does."""

    fake_app = FakeApplication(tmp_path, **settings)
    terminal_app = create_terminal_app(fake_app, config_path=cfg_path)
    return fake_app, terminal_app


async def drive(terminal_app):
    input_widget = terminal_app.query_one("#input", TextArea)
    input_widget.value = "/doctor subagents"
    return input_widget


def saved(cfg_path):
    return json.loads(cfg_path.read_text(encoding="utf-8"))


async def test_the_tab_opens_with_the_limits_the_roster_and_the_protocol(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    _, terminal_app = await open_subagents(tmp_path, cfg_path)

    async with terminal_app.run_test(size=(120, 140)) as pilot:
        await drive(terminal_app)
        await pilot.press("enter")
        await pilot.pause(0.2)

        modal = terminal_app.screen
        assert isinstance(modal, DoctorModal)
        # Every limit has a field and both steppers.
        for field in ("max_concurrent_subagents", "max_subagents_per_turn"):
            modal.query_one(f"#doctor-limit-{field}", Input)
            modal.query_one(f"#doctor-limit-inc-{field}", Button)
            modal.query_one(f"#doctor-limit-dec-{field}", Button)
        # The protocol switch that makes a parser-less endpoint usable.
        for choice in ("auto", "native", "text"):
            modal.query_one(f"#doctor-toolcalling-{choice}", Button)
        # The roster, its green + and its catalog button.
        modal.query_one("#doctor-subagent-model-input", Input)
        assert modal.query_one("#doctor-subagent-add", Button).variant == "success"
        modal.query_one("#doctor-subagent-list", Button)


async def test_the_plus_button_adds_a_model_and_saves_it(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app, terminal_app = await open_subagents(tmp_path, cfg_path)

    async with terminal_app.run_test(size=(120, 140)) as pilot:
        await drive(terminal_app)
        await pilot.press("enter")
        await pilot.pause(0.2)
        modal = terminal_app.screen

        modal.query_one("#doctor-subagent-model-input", Input).value = "small-model"
        await pilot.click("#doctor-subagent-add")
        await pilot.pause(0.3)

        status = str(modal.query_one("#doctor-status", Static).render())
        assert fake_app.session.config.subagent_models == ["small-model"], status
        assert saved(cfg_path)["subagent_models"] == ["small-model"]
        # The box is cleared so the next one can be typed straight away.
        assert modal.query_one("#doctor-subagent-model-input", Input).value == ""
        # And the model now has a row with its own delete button.
        modal.query_one("#doctor-subagent-del-0", Button)


async def test_the_delete_button_removes_that_model(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app, terminal_app = await open_subagents(
        tmp_path, cfg_path, subagent_models=["small-model", "tiny-model"]
    )

    async with terminal_app.run_test(size=(120, 140)) as pilot:
        await drive(terminal_app)
        await pilot.press("enter")
        await pilot.pause(0.2)

        await pilot.click("#doctor-subagent-del-0")
        await pilot.pause(0.2)

        assert fake_app.session.config.subagent_models == ["tiny-model"]
        assert saved(cfg_path)["subagent_models"] == ["tiny-model"]


async def test_the_session_model_is_refused_as_a_duplicate(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app, terminal_app = await open_subagents(tmp_path, cfg_path)

    async with terminal_app.run_test(size=(120, 140)) as pilot:
        await drive(terminal_app)
        await pilot.press("enter")
        await pilot.pause(0.2)
        modal = terminal_app.screen

        modal.query_one("#doctor-subagent-model-input", Input).value = "main-model"
        await pilot.click("#doctor-subagent-add")
        await pilot.pause(0.2)

        assert fake_app.session.config.subagent_models == []
        status = str(modal.query_one("#doctor-status", Static).render())
        assert "session model" in status


async def test_the_same_model_is_not_added_twice(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app, terminal_app = await open_subagents(
        tmp_path, cfg_path, subagent_models=["small-model"]
    )

    async with terminal_app.run_test(size=(120, 140)) as pilot:
        await drive(terminal_app)
        await pilot.press("enter")
        await pilot.pause(0.2)
        modal = terminal_app.screen

        modal.query_one("#doctor-subagent-model-input", Input).value = "small-model"
        await pilot.click("#doctor-subagent-add")
        await pilot.pause(0.2)

        assert fake_app.session.config.subagent_models == ["small-model"]
        assert "already" in str(modal.query_one("#doctor-status", Static).render())


async def test_a_limit_steps_and_saves(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app, terminal_app = await open_subagents(tmp_path, cfg_path)
    before = fake_app.session.config.budgets.max_concurrent_subagents

    async with terminal_app.run_test(size=(120, 140)) as pilot:
        await drive(terminal_app)
        await pilot.press("enter")
        await pilot.pause(0.2)

        await pilot.click("#doctor-limit-inc-max_concurrent_subagents")
        await pilot.pause(0.2)

        assert fake_app.session.config.budgets.max_concurrent_subagents == before + 1
        assert saved(cfg_path)["budgets"]["max_concurrent_subagents"] == before + 1


async def test_a_limit_never_steps_below_one(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app, terminal_app = await open_subagents(
        tmp_path, cfg_path, budgets={"max_subagent_depth": 1}
    )

    async with terminal_app.run_test(size=(120, 140)) as pilot:
        await drive(terminal_app)
        await pilot.press("enter")
        await pilot.pause(0.2)

        await pilot.click("#doctor-limit-dec-max_subagent_depth")
        await pilot.pause(0.2)

        # Zero is not a value BudgetConfig accepts, so the button stops at one.
        assert fake_app.session.config.budgets.max_subagent_depth == 1


async def test_the_tool_calling_protocol_is_switched_and_marked(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app, terminal_app = await open_subagents(tmp_path, cfg_path)

    async with terminal_app.run_test(size=(120, 140)) as pilot:
        await drive(terminal_app)
        await pilot.press("enter")
        await pilot.pause(0.2)
        modal = terminal_app.screen

        await pilot.click("#doctor-toolcalling-text")
        await pilot.pause(0.2)

        assert fake_app.session.config.tool_calling == "text"
        assert saved(cfg_path)["tool_calling"] == "text"
        assert modal.query_one("#doctor-toolcalling-text", Button).variant == "success"
        assert modal.query_one("#doctor-toolcalling-auto", Button).variant == "default"
