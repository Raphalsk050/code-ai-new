"""The settings panel end to end: the form the extension posts, over the wire.

The unit tests poke ``update_settings`` with one key at a time. The panel never
does that - it posts the whole form on every press of Salvar, in the order the
React draft holds it, with ``api_key`` appended last. That shape is the one
that broke: fields were validated one at a time against a rule that spans
three of them, and the panel then rewrote its form from the snapshot that came
back, so a refused field looked like a save that threw the edit away.

So these drive a real application through a real ``BridgeServer`` over real
JSON-RPC lines, and assert on what the panel would read back.
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from code_ai.bootstrap import build_application
from code_ai.bridge.server import BridgeServer
from code_ai.config import loader as config_loader
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)


class ScriptedProvider:
    """Enough of a provider to build an application around."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind="completed", response=ModelResponse(text="", finish_reason=FinishReason.STOP)
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    async def close(self) -> None:
        return None


class _Pipe(io.StringIO):
    """A stdout the test can read back without closing the server."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def write(self, data: str) -> int:  # type: ignore[override]
        if data.strip():
            self.lines.append(data.strip())
        return len(data)

    def flush(self) -> None:  # pragma: no cover - nothing to flush
        return None


@pytest.fixture
def panel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A bridge the test talks to the way the extension's webview does."""

    # Never touch the developer's real ~/.code-ai/config.json.
    monkeypatch.setattr(config_loader, "default_config_path", lambda: tmp_path / "config.json")
    workspace = tmp_path / "project"
    workspace.mkdir()
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "base_url": "http://localhost:11434/v1",
            "workspace": str(workspace),
            "model": "fake",
            "planner": {"enabled": False},
        }
    )
    app = build_application(config=config, provider=ScriptedProvider())
    out = _Pipe()
    server = BridgeServer(app, stdin=io.StringIO(), stdout=out)

    class _Panel:
        def __init__(self) -> None:
            self.app = app
            self.workspace = workspace
            self._id = 0

        async def call(self, method: str, params: dict[str, Any] | None = None) -> dict:
            self._id += 1
            out.lines.clear()
            await server._handle_line(
                json.dumps(
                    {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
                )
            )
            for line in out.lines:
                message = json.loads(line)
                if message.get("id") == self._id:
                    assert "error" not in message, message["error"]
                    return message["result"]
            raise AssertionError(f"no answer to {method}")

        async def form(self, **edits: Any) -> dict[str, Any]:
            """The exact payload SettingsScreen.submit() puts on the wire.

            Every field, in the draft's order, with the api key appended last -
            which is what made the endpoint validate against the key it was
            replacing rather than the one being saved.
            """
            settings = await self.call("getSettings")
            updates = {
                field: settings[field]
                for field in (
                    "model",
                    "api_mode",
                    "base_url",
                    "language",
                    "permission_mode",
                    "reasoning_effort",
                    "followup_reasoning_effort",
                    "learn",
                    "inline_hints_enabled",
                    "inline_model",
                    "max_context_tokens",
                    "workspace",
                    "disabled_tools",
                    "subagent_models",
                    "max_concurrent_subagents",
                    "temperature",
                    "top_p",
                    "top_k",
                )
            }
            api_key = edits.pop("api_key", None)
            updates.update(edits)
            if api_key:
                updates["api_key"] = api_key
            return updates

        async def save(self, **edits: Any) -> dict:
            return await self.call("updateSettings", {"updates": await self.form(**edits)})

    return _Panel()


async def test_the_whole_form_saves_without_a_refusal(panel) -> None:
    """Salvar with nothing edited must be a no-op, not a page of errors.

    The panel posts every field whether or not it was touched, so an untouched
    form is the commonest save there is.
    """
    result = await panel.save()

    assert result["errors"] == {}
    assert result["restart_required"] == []


async def test_every_edited_field_comes_back_edited(panel, tmp_path: Path) -> None:
    """One press of Salvar, one edit per section, nothing reverted.

    This is the regression: the panel rewrites its form from ``settings``, so
    any field that comes back unchanged is a field the user watched snap back.
    """
    moved = tmp_path / "other-project"
    moved.mkdir()
    edits = {
        "model": "qwen2.5-coder:14b",
        "language": "pt-BR",
        "reasoning_effort": "high",
        "followup_reasoning_effort": "low",
        "learn": True,
        "inline_hints_enabled": True,
        "inline_model": "qwen2.5-coder:1.5b",
        "max_context_tokens": 65536,
        "workspace": str(moved),
        "subagent_models": ["alpha", "beta"],
        "max_concurrent_subagents": 3,
        "temperature": 0.25,
        "top_p": 0.85,
        "top_k": 40,
    }

    result = await panel.save(**edits)

    assert result["errors"] == {}
    saved = result["settings"]
    for field, value in edits.items():
        if field == "workspace":
            assert Path(saved[field]) == moved.resolve()
        else:
            assert saved[field] == value, f"{field} came back as {saved[field]!r}"
    # And it is the live config, not only the snapshot.
    assert panel.app.session.config.model == "qwen2.5-coder:14b"


async def test_a_new_endpoint_saves_together_with_its_new_key(panel) -> None:
    """The panel appends api_key last; the endpoint must still validate.

    A non-local base_url needs a key. Persisted one field at a time, the new
    URL was checked against the key it was replacing, refused, and reverted -
    while the key it needed saved fine one field later.
    """
    result = await panel.save(
        api_mode="completions",
        base_url="https://llm.empresa.example/v1",
        api_key="sk-nova-chave",
    )

    assert result["errors"] == {}
    assert result["settings"]["base_url"] == "https://llm.empresa.example/v1"
    assert result["settings"]["api_mode"] == "completions"
    assert result["settings"]["api_key_set"] is True
    assert panel.app.session.config.base_url == "https://llm.empresa.example/v1"


async def test_the_workspace_moves_the_session(panel, tmp_path: Path) -> None:
    moved = tmp_path / "second-project"
    moved.mkdir()
    before = panel.app.orchestrator

    result = await panel.save(workspace=str(moved))

    assert result["errors"] == {}
    assert "workspace" in result["applied"]
    assert Path(result["settings"]["workspace"]) == moved.resolve()
    # Retargeting rebuilds the session rather than re-pointing it.
    assert panel.app.orchestrator is not before


async def test_an_untouched_workspace_does_not_rebuild_the_session(panel) -> None:
    """Saving anything else must not close the terminals.

    The form carries the workspace on every save, and retargeting tears the
    session down - so an unchanged path has to be recognised as unchanged.
    """
    before = panel.app.orchestrator
    terminals = panel.app.terminal_manager

    result = await panel.save(model="outro-modelo")

    assert result["errors"] == {}
    assert result["settings"]["model"] == "outro-modelo"
    assert panel.app.orchestrator is before
    assert panel.app.terminal_manager is terminals


async def test_a_refused_field_is_named_and_the_rest_still_saves(panel) -> None:
    """What the panel needs to tell "saved" from "reverted".

    A remote endpoint with no key is a real refusal. It has to arrive keyed by
    field name, and it must not take the rest of the form down with it.
    """
    result = await panel.save(
        api_mode="completions",
        base_url="https://llm.empresa.example/v1",
        model="modelo-novo",
    )

    assert "base_url" in result["errors"]
    assert "api_key" in result["errors"]["base_url"]
    # The snapshot carries the old endpoint - which is exactly why the panel
    # has to read `errors` instead of trusting it.
    assert result["settings"]["base_url"] == "http://localhost:11434/v1"
    # Everything independent of the refusal still landed.
    assert "model" in result["applied"]
    assert result["settings"]["model"] == "modelo-novo"


async def test_an_unknown_field_is_still_reported(panel) -> None:
    result = await panel.call("updateSettings", {"updates": {"nao_existe": 1}})

    assert result["errors"] == {"nao_existe": "unknown setting"}
    assert result["applied"] == []
