from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)
from code_ai.sandbox.layout import MARKER_FILENAME, MARKER_KIND


class _SilentProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="", finish_reason=FinishReason.STOP),
        )

    async def close(self) -> None:
        return None


def make_config(tmp_path, **sandbox_overrides) -> AppConfig:
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    sandbox = {"base_dir": str(tmp_path / "sandboxes")}
    sandbox.update(sandbox_overrides)
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(workspace),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
            "memory": {"reflection_enabled": False},
            "sandbox": sandbox,
        }
    )


def build(tmp_path, **sandbox_overrides):
    return build_application(
        config=make_config(tmp_path, **sandbox_overrides), provider=_SilentProvider()
    )


def test_a_session_gets_its_own_sandbox(tmp_path) -> None:
    app = build(tmp_path)

    assert app.sandbox is not None
    assert app.sandbox.root.is_dir()
    assert app.sandbox.root.parent == (tmp_path / "sandboxes").resolve()
    assert app.sandbox.root.name == app.session.session_id


def test_the_tools_receive_the_session_sandbox(tmp_path) -> None:
    app = build(tmp_path)

    context = app.orchestrator.tool_context_factory(None)

    assert context.sandbox is app.sandbox


def test_the_system_prompt_names_the_sandbox(tmp_path) -> None:
    app = build(tmp_path)

    prompt = app.orchestrator.conversation.messages[0].content

    assert str(app.sandbox.root) in prompt


def test_a_disabled_sandbox_leaves_the_agent_working_against_the_project(tmp_path) -> None:
    app = build(tmp_path, enabled=False)

    assert app.sandbox is None
    assert app.orchestrator.tool_context_factory(None).sandbox is None
    prompt = app.orchestrator.conversation.messages[0].content
    assert "There are two places to work in" not in prompt


async def test_closing_the_session_removes_its_sandbox(tmp_path) -> None:
    app = build(tmp_path)
    root = app.sandbox.root

    await app.start()
    await app.close()

    assert not root.exists()


async def test_a_sandbox_can_be_kept_for_inspection(tmp_path) -> None:
    app = build(tmp_path, cleanup_on_exit=False)
    root = app.sandbox.root

    await app.start()
    await app.close()

    assert root.is_dir()


def test_startup_reaps_a_sandbox_a_dead_session_left_behind(tmp_path) -> None:
    base = tmp_path / "sandboxes"
    abandoned = base / "long-gone-session"
    (abandoned / "work").mkdir(parents=True)
    (abandoned / MARKER_FILENAME).write_text(
        json.dumps(
            {
                "kind": MARKER_KIND,
                "session_id": "long-gone-session",
                "workspace": str(tmp_path),
                "created_at": (datetime.now(UTC) - timedelta(days=7)).isoformat(),
                "pid": 1,
                "hostname": "another-host",
            }
        ),
        encoding="utf-8",
    )

    app = build(tmp_path, ttl_hours=1)

    assert not abandoned.exists()
    assert app.sandbox.root.is_dir()


def test_startup_never_reaps_a_directory_that_is_not_ours(tmp_path) -> None:
    base = tmp_path / "sandboxes"
    stranger = base / "someone-elses-data"
    stranger.mkdir(parents=True)
    (stranger / "precious.txt").write_text("data", encoding="utf-8")

    build(tmp_path, ttl_hours=1)

    assert (stranger / "precious.txt").exists()


def test_a_host_that_cannot_hold_a_sandbox_still_starts(tmp_path, monkeypatch) -> None:
    config = make_config(tmp_path, base_dir=str(tmp_path / "unwritable"))
    original = Path.mkdir

    def refuse_below_the_base(self, *args, **kwargs):
        if str(self).startswith(str(tmp_path / "unwritable")):
            raise OSError("read-only file system")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse_below_the_base)

    app = build_application(config=config, provider=_SilentProvider())

    # An agent that cannot isolate falls back to working against the project,
    # which is what it did before the sandbox existed - it does not refuse to run.
    assert app.sandbox is None
