"""The Doctor's decision-layer step: its own address, a token, and the switch."""

from __future__ import annotations

import json

from textual.widgets import Button, Input, TextArea

from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.doctor import DoctorModal
from tests.unit.test_doctor_subagents import FakeApplication


async def test_the_step_saves_the_address_the_token_and_the_switch(tmp_path) -> None:
    cfg_path = tmp_path / "config.json"
    fake_app = FakeApplication(tmp_path)
    reloads: list[bool] = []
    fake_app.reload_decision_engine = lambda: reloads.append(
        fake_app.session.config.decisions.enabled
    )
    terminal_app = create_terminal_app(fake_app, config_path=cfg_path)

    async with terminal_app.run_test(size=(120, 140)) as pilot:
        terminal_app.query_one("#input", TextArea).value = "/doctor"
        await pilot.press("enter")
        await pilot.pause(0.2)
        modal = terminal_app.screen
        assert isinstance(modal, DoctorModal)

        menu = modal.query_one("#doctor-menu-jev", Button)
        assert "off" in str(menu.label)
        menu.press()
        await pilot.pause(0.2)

        modal.query_one("#doctor-input-jev_base_url", Input).value = "http://gpu-box:3000"
        modal.query_one("#doctor-save-jev_base_url", Button).press()
        await pilot.pause(0.1)
        modal.query_one("#doctor-input-jev_api_key", Input).value = "s3cret"
        modal.query_one("#doctor-save-jev_api_key", Button).press()
        await pilot.pause(0.1)
        modal.query_one("#doctor-jev-toggle", Button).press()
        await pilot.pause(0.2)

        # The switch repaints as its opposite, and the model's address is untouched.
        assert str(modal.query_one("#doctor-jev-toggle", Button).label) == "Turn off"

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["decisions"]["engine"] == "openjev"
    assert saved["decisions"]["base_url"] == "http://gpu-box:3000"
    assert saved["decisions"]["api_key"] == "s3cret"
    assert saved["base_url"] == fake_app.session.config.base_url
    # Each save hands the planner a fresh engine; the last one is switched on.
    assert reloads == [False, False, True]
