"""The environment the agent's commands run in, and what the agent is told about it."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.prompts import build_subagent_system_prompt, build_system_prompt
from code_ai.tools.base import ToolContext
from code_ai.tools.process.command_runner import CommandRunner
from code_ai.tools.process.execute_command import ExecuteCommandTool
from code_ai.tools.system.system_information import SystemInformationTool
from code_ai.util import environment as environment_module
from code_ai.util.environment import (
    CommandEnvironment,
    command_environment,
    describe_environment,
    is_secret_name,
    resolve_argv,
)
from code_ai.util.paths import WorkspacePolicy

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows command resolution")


def _context(tmp_path: Path, **settings: Any) -> ToolContext:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "m", **settings}
    )
    return ToolContext(
        config=config, workspace=WorkspacePolicy.from_path(tmp_path), event_bus=AsyncEventBus()
    )


def test_secret_names_are_told_apart_by_their_parts() -> None:
    for name in ("OPENAI_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "TOKEN"):
        assert is_secret_name(name), name
    for name in ("SSH_AUTH_SOCK", "KEYBOARD_LAYOUT", "PATH", "PWD", "MAX_TOKENS", "JAVA_KEYSTORE"):
        assert not is_secret_name(name), name


def test_commands_get_the_whole_environment_but_no_secrets() -> None:
    base = {
        "PATH": "/usr/bin",
        "TEMP": "/tmp",
        "USERPROFILE": "/home/r",
        "JAVA_HOME": "/opt/jdk",
        "HTTPS_PROXY": "http://proxy:8080",
        "OPENAI_API_KEY": "sk-1",
        "GITHUB_TOKEN": "ghp-1",
    }

    held = command_environment(base=base)
    passed = command_environment(base=base, pass_secrets=True)

    assert held.variables == {k: v for k, v in base.items() if "KEY" not in k and "TOKEN" not in k}
    assert held.withheld == ("GITHUB_TOKEN", "OPENAI_API_KEY")
    assert passed.variables == base
    assert passed.withheld == ()


def test_the_packaged_app_gives_commands_the_users_libraries(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    base = {
        "LD_LIBRARY_PATH": "/tmp/_MEI123",
        "LD_LIBRARY_PATH_ORIG": "/usr/lib/custom",
        "DYLD_LIBRARY_PATH": "/tmp/_MEI123",
    }

    env = command_environment(base=base).variables

    assert env == {"LD_LIBRARY_PATH": "/usr/lib/custom"}


def test_the_registry_adds_what_the_session_never_saw() -> None:
    env = {
        "PATH": r"C:\venv\Scripts;C:\Windows",
        "JAVA_HOME": r"C:\jdk-17",
        "USERPROFILE": r"C:\Users\r",
    }
    registry = (
        {"JAVA_HOME": r"C:\jdk-21", "ANDROID_HOME": r"%USERPROFILE%\sdk"},
        r"C:\Windows;C:\Tools\new\bin",
        r"%USERPROFILE%\bin",
    )

    merged = environment_module._with_registry(env, registry)

    # The session's own values and PATH order win: an activated venv stays first.
    assert merged["JAVA_HOME"] == r"C:\jdk-17"
    assert merged["PATH"].split(";") == [
        r"C:\venv\Scripts",
        r"C:\Windows",
        r"C:\Tools\new\bin",
        r"C:\Users\r\bin",
    ]
    # What was created since the session started is picked up, expanded.
    assert merged["ANDROID_HOME"] == r"C:\Users\r\sdk"


def test_argv_is_left_alone_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(environment_module.os, "name", "posix")

    assert resolve_argv(["npm", "test"], {"PATH": "/usr/bin"}) == ["npm", "test"]


@windows_only
def test_windows_finds_cmd_tools_and_runs_builtins_through_cmd(tmp_path) -> None:
    (tmp_path / "fakecli.cmd").write_text("@echo off\r\necho hi\r\n", encoding="utf-8")
    env = {"PATH": str(tmp_path), "COMSPEC": r"C:\Windows\System32\cmd.exe"}

    assert resolve_argv(["fakecli", "x"], env)[0].lower() == str(tmp_path / "fakecli.cmd").lower()
    assert resolve_argv(["dir", "/b"], env) == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
        "dir /b",
    ]
    assert resolve_argv(["mkdir", "-p", "src/app/ui"], env)[-1] == r"mkdir src\app\ui"
    assert resolve_argv(["dir", "/b", "src/app"], env)[-1] == r"dir /b src\app"
    assert resolve_argv(["echo", "a/b"], env)[-1] == "echo a/b"
    assert resolve_argv(["start", "", "notepad"], env)[-1] == 'start "" notepad'
    assert resolve_argv([r"C:\x\tool.exe"], env) == [r"C:\x\tool.exe"]
    assert resolve_argv(["nothing-by-this-name"], env) == ["nothing-by-this-name"]


async def _run(argv: list[str], tmp_path: Path, **kwargs: Any):
    return await CommandRunner().run(
        argv=argv, cwd=tmp_path, timeout=30, event_bus=AsyncEventBus(), cancel_event=None, **kwargs
    )


async def test_a_command_reads_end_of_file_instead_of_waiting(tmp_path) -> None:
    result = await _run(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], tmp_path
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "''"


async def test_a_command_sees_the_users_variables_but_not_their_secrets(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CODE_AI_PROBE_HOME", "/opt/probe")
    monkeypatch.setenv("CODE_AI_PROBE_API_KEY", "s3cret")
    script = (
        "import os; print(os.environ.get('CODE_AI_PROBE_HOME'), "
        "os.environ.get('CODE_AI_PROBE_API_KEY'))"
    )

    result = await _run([sys.executable, "-c", script], tmp_path)

    assert result.stdout.split() == ["/opt/probe", "None"]


@windows_only
async def test_windows_runs_builtins_and_cmd_scripts(tmp_path) -> None:
    (tmp_path / "hello.cmd").write_text("@echo off\r\necho hello from cmd\r\n", encoding="utf-8")
    env = {**os.environ, "PATH": f"{tmp_path};{os.environ['PATH']}"}

    listed = await _run(["dir", "/b"], tmp_path, base_env=env)
    greeted = await _run(["hello"], tmp_path, base_env=env)

    assert listed.exit_code == 0 and "hello.cmd" in listed.stdout
    assert greeted.stdout.strip() == "hello from cmd"


async def test_a_timeout_points_at_the_terminal(tmp_path) -> None:
    command = f'"{sys.executable}" -c "import time; time.sleep(10)"'

    with pytest.raises(ToolExecutionError, match="start_terminal"):
        await ExecuteCommandTool().execute({"command": command, "timeout": 0.5}, _context(tmp_path))


def test_the_description_says_where_commands_run() -> None:
    environment = CommandEnvironment(
        variables={
            "PATH": os.environ.get("PATH", ""),
            "JAVA_HOME": "/opt/jdk",
            "XDG_CONFIG_HOME": "/home/r/.config",
            "HTTPS_PROXY": "http://proxy:8080",
            "COMSPEC": r"C:\Windows\System32\cmd.exe",
            "SHELL": "/bin/bash",
            "DISPLAY": ":0",
        },
        withheld=("GITHUB_TOKEN",),
    )

    text = describe_environment(environment)

    assert text.startswith("Machine: ")
    assert "opens it on their screen" in text
    assert "start_terminal" in text
    assert "JAVA_HOME=/opt/jdk" in text
    assert "XDG_CONFIG_HOME" not in text
    assert "HTTPS_PROXY" in text and "proxy:8080" not in text
    assert "GITHUB_TOKEN" in text
    if os.name == "nt":
        assert r"Interactive terminal shell: C:\Windows\System32\cmd.exe" in text
        assert "cmd built-ins" in text
    else:
        assert "Interactive terminal shell: /bin/bash" in text


def test_both_prompts_carry_the_environment(tmp_path) -> None:
    note = "Machine: Test OS. Commands run directly."

    main = build_system_prompt(workspace=tmp_path, language="en", environment=note)
    sub = build_subagent_system_prompt(
        role_prompt="Explore.", workspace=tmp_path, language="en", environment=note
    )

    assert note in main
    assert note in sub


async def test_system_information_reports_the_users_machine(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODE_AI_PROBE_HOME", "/opt/probe")
    monkeypatch.setenv("CODE_AI_PROBE_TOKEN", "s3cret")

    info = await SystemInformationTool().execute(
        {
            "commands": ["python", "surely-not-installed-anywhere"],
            "variables": ["CODE_AI_PROBE_HOME", "CODE_AI_PROBE_TOKEN"],
        },
        _context(tmp_path),
    )
    shown = await SystemInformationTool().execute(
        {"variables": ["CODE_AI_PROBE_TOKEN"]}, _context(tmp_path, commands_see_secrets=True)
    )

    assert info["commands"]["surely-not-installed-anywhere"] is None
    assert info["variables"]["CODE_AI_PROBE_HOME"] == "/opt/probe"
    assert info["variables"]["CODE_AI_PROBE_TOKEN"] is None
    assert "CODE_AI_PROBE_TOKEN" in info["withheld_secrets"]
    # Passed to commands, yet never shown to the model.
    assert shown["variables"]["CODE_AI_PROBE_TOKEN"] == "<set, secret>"


async def test_the_setting_is_live_and_the_prompt_follows_it(tmp_path, monkeypatch) -> None:
    from code_ai.bootstrap import build_application
    from code_ai.config import loader as config_loader
    from code_ai.providers.models import ModelResponse, ProviderCapabilities, ProviderEvent

    class _Provider:
        @property
        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(streaming=True, tool_calling=True)

        async def stream(self, request):  # noqa: ANN001
            yield ProviderEvent(kind="completed", response=ModelResponse(text=""))

        async def complete(self, request):  # noqa: ANN001
            return ModelResponse(text="")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(config_loader, "default_config_path", lambda: tmp_path / "config.json")
    monkeypatch.setenv("CODE_AI_PROBE_TOKEN", "s3cret")
    app = build_application(
        config=AppConfig.from_mapping(
            {"api_mode": "ollama", "workspace": str(tmp_path), "model": "m"}
        ),
        provider=_Provider(),
    )
    assert "CODE_AI_PROBE_TOKEN" in app.orchestrator.environment_note

    result = await app.update_settings({"commands_see_secrets": True})

    assert result["errors"] == {}
    assert result["settings"]["commands_see_secrets"] is True
    assert "CODE_AI_PROBE_TOKEN" not in app.orchestrator.environment_note
    assert app.subagent_runtime.environment_text == app.orchestrator.environment_note


async def test_the_doctor_switches_it_in_the_terminal(tmp_path) -> None:
    import json

    from textual.widgets import Checkbox, TextArea

    from code_ai.tools.registry import ToolRegistry
    from code_ai.ui.terminal.app import create_terminal_app
    from tests.unit.test_terminal_ui import FakeTerminalApplication

    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.orchestrator.tool_registry = ToolRegistry()
    refreshed: list[bool] = []
    fake_app.refresh_environment_note = lambda: refreshed.append(True)
    cfg_path = tmp_path / "config.json"
    terminal_app = create_terminal_app(fake_app, config_path=cfg_path)

    async with terminal_app.run_test(size=(120, 80)) as pilot:
        terminal_app.query_one("#input", TextArea).value = "/doctor tools"
        await pilot.press("enter")
        await pilot.pause(0.3)
        terminal_app.screen.query_one("#doctor-commands-secrets", Checkbox).value = True
        await pilot.pause(0.2)

    assert fake_app.session.config.commands_see_secrets is True
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["commands_see_secrets"] is True
    assert refreshed == [True]
