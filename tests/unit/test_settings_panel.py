"""The settings the extension's panel reads and writes.

These are the fields the terminal edits through /doctor and /config. The panel
is a second front end onto the same configuration, so what matters here is that
a write lands in the live objects the agent is built from - not only on disk.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from code_ai.app.service import CodeAIApplication
from code_ai.config import loader as config_loader
from code_ai.config.models import AppConfig
from code_ai.core.state import AgentState


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"does {name}"


class _Registry:
    """The four methods the panel's tool switches depend on."""

    def __init__(self, names: list[str], disabled: set[str] | None = None) -> None:
        self._names = names
        self._disabled = set(disabled or ())

    def registered_names(self) -> list[str]:
        return list(self._names)

    def disabled_names(self) -> set[str]:
        return set(self._disabled)

    def tool(self, name: str) -> _Tool:
        return _Tool(name)

    def set_disabled(self, names) -> None:
        self._disabled = set(names)


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, payload: dict | None = None, *, source: str = "") -> None:
        self.events.append((event_type, payload or {}))


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CodeAIApplication:
    # Never touch the developer's real ~/.code-ai/config.json.
    monkeypatch.setattr(config_loader, "default_config_path", lambda: tmp_path / "config.json")
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    registry = _Registry(["read_file", "write_file", "execute_command"], {"execute_command"})
    return CodeAIApplication(
        session=SimpleNamespace(config=config, state=AgentState.READY),
        event_bus=_Bus(),
        orchestrator=SimpleNamespace(tool_registry=registry, usage=None),
        provider=None,
        compressor=None,
        conversation_store=None,
    )


def test_settings_carry_what_the_doctor_edits(app: CodeAIApplication) -> None:
    settings = app.get_settings()

    assert settings["disabled_tools"] == []
    assert settings["subagent_models"] == []
    assert settings["max_concurrent_subagents"] >= 1
    assert "temperature" in settings and "top_p" in settings and "top_k" in settings


def test_the_tool_catalog_comes_from_the_registry_not_the_config(app: CodeAIApplication) -> None:
    # The config says nothing is disabled; the registry says execute_command is.
    # The registry is what the agent is built from, so it wins.
    tools = {tool["name"]: tool for tool in app.get_settings()["supported"]["tools"]}

    assert set(tools) == {"read_file", "write_file", "execute_command"}
    assert tools["execute_command"]["enabled"] is False
    assert tools["read_file"]["enabled"] is True
    assert tools["read_file"]["description"]


def test_a_session_without_a_registry_offers_no_switches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config_loader, "default_config_path", lambda: tmp_path / "config.json")
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    app = CodeAIApplication(
        session=SimpleNamespace(config=config, state=AgentState.READY),
        event_bus=_Bus(),
        orchestrator=SimpleNamespace(),
        provider=None,
        compressor=None,
        conversation_store=None,
    )
    assert app.get_settings()["supported"]["tools"] == []


async def test_disabling_a_tool_reaches_the_live_registry(app: CodeAIApplication) -> None:
    result = await app.update_settings({"disabled_tools": ["write_file", " ", "write_file"]})

    assert "disabled_tools" in result["applied"]
    assert app.session.config.disabled_tools == ["write_file"]
    # Live, not just persisted: the next request is built from the registry.
    assert app.orchestrator.tool_registry.disabled_names() == {"write_file"}


async def test_subagent_models_keep_the_user_s_order(app: CodeAIApplication) -> None:
    await app.update_settings({"subagent_models": ["zeta", "alpha", "zeta", ""]})

    # The first entry is the default a sub-agent gets, so this is a ranking the
    # user builds by hand - sorting it would silently change which model runs.
    assert app.session.config.subagent_models == ["zeta", "alpha"]


async def test_subagent_concurrency_lands_in_the_budgets(app: CodeAIApplication) -> None:
    result = await app.update_settings({"max_concurrent_subagents": 4})

    assert "max_concurrent_subagents" in result["applied"]
    assert app.session.config.budgets.max_concurrent_subagents == 4


async def test_sampling_values_round_trip_and_clear(app: CodeAIApplication) -> None:
    await app.update_settings({"temperature": 0.2, "top_k": 40})
    assert app.session.config.sampling.temperature == pytest.approx(0.2)
    assert app.session.config.sampling.top_k == 40

    # Empty means "let the endpoint decide", which is not the same as zero.
    await app.update_settings({"temperature": "", "top_k": None})
    assert app.session.config.sampling.temperature is None
    assert app.session.config.sampling.top_k is None


async def test_an_unknown_setting_is_reported_not_swallowed(app: CodeAIApplication) -> None:
    result = await app.update_settings({"nao_existe": 1})

    assert result["errors"]["nao_existe"] == "unknown setting"
    assert result["applied"] == []


async def test_one_bad_value_does_not_abort_the_batch(app: CodeAIApplication) -> None:
    result = await app.update_settings(
        {"max_concurrent_subagents": "not a number", "language": "pt-BR"}
    )

    assert "language" in result["applied"]
    assert "max_concurrent_subagents" in result["errors"]
