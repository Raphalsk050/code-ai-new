from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from code_ai.bootstrap import build_application
from code_ai.config.loader import default_config_path, load_config
from code_ai.config.models import AppConfig

pytestmark = pytest.mark.live_model

LIVE_ENV = "CODE_AI_RUN_LIVE_MODEL_TESTS"
LIVE_CONFIG_ENV = "CODE_AI_LIVE_CONFIG"


def _live_config_path() -> Path:
    if os.environ.get(LIVE_ENV) != "1":
        pytest.skip(f"Set {LIVE_ENV}=1 to run live configured-model tests.")
    config_path = Path(os.environ.get(LIVE_CONFIG_ENV, default_config_path())).expanduser()
    if not config_path.exists():
        pytest.skip(f"Live config file does not exist: {config_path}")
    return config_path


def _with_live_budgets(config: AppConfig) -> AppConfig:
    data = config.to_dict()
    budgets = dict(data["budgets"])
    budgets.update(
        {
            "default_tool_timeout_s": 30,
            "max_model_call_s": 240,
            "max_model_step_seconds": 240,
            "max_model_steps": 80,
            "max_tool_calls": 120,
            "max_turn_seconds": 600,
            "max_turn_wall_time_s": 600,
        }
    )
    data["budgets"] = budgets
    data["show_ui"] = False
    return AppConfig.from_mapping(data)


def _live_config(tmp_path: Path) -> AppConfig:
    config = load_config(
        explicit_path=_live_config_path(),
        cli_overrides={"workspace": str(tmp_path), "show_ui": False},
    )
    return _with_live_budgets(config)


def _saved_workspace_live_config() -> AppConfig:
    config = load_config(
        explicit_path=_live_config_path(),
        cli_overrides={"show_ui": False},
    )
    return _with_live_budgets(config)


async def _run_live_task(config: AppConfig, task: str) -> tuple[str, list[Any]]:
    app = build_application(config=config)
    events: list[Any] = []
    app.subscribe(lambda event: events.append(event))
    try:
        await app.start()
        result = await app.submit_user_message(task)
        return result.text, events
    finally:
        await app.close()


def _tool_names(events: list[Any], event_type: str = "tool.call.requested") -> list[str]:
    return [
        str(event.payload.get("name"))
        for event in events
        if event.event_type == event_type and event.payload.get("name")
    ]


def _assert_tool_called(events: list[Any], tool_name: str) -> None:
    names = _tool_names(events)
    assert tool_name in names, f"Expected {tool_name} to be called. Called tools: {names}"


async def test_live_model_reads_file_and_answers_expected_token(tmp_path) -> None:
    config = _live_config(tmp_path)
    (tmp_path / "facts.txt").write_text(
        "EXPECTED_ANSWER: BLUE-17\nDo not infer this value without reading the file.\n",
        encoding="utf-8",
    )

    answer, events = await _run_live_task(
        config,
        (
            "Leia o arquivo facts.txt usando a ferramenta read_file. "
            "Responda somente o valor depois de EXPECTED_ANSWER."
        ),
    )

    names = _tool_names(events)
    assert "list_files" in names
    _assert_tool_called(events, "read_file")
    assert names.index("list_files") < names.index("read_file")
    assert "BLUE-17" in answer


async def test_live_model_searches_code_for_expected_path(tmp_path) -> None:
    config = _live_config(tmp_path)
    source = tmp_path / "src" / "live_lookup.py"
    source.parent.mkdir(parents=True)
    source.write_text('LIVE_SEARCH_TOKEN = "SEARCH-PASS-91"\n', encoding="utf-8")

    answer, events = await _run_live_task(
        config,
        (
            "Use search_code para procurar LIVE_SEARCH_TOKEN no workspace. "
            "Responda com o caminho do arquivo e o valor encontrado."
        ),
    )

    _assert_tool_called(events, "search_code")
    assert "src/live_lookup.py" in answer
    assert "SEARCH-PASS-91" in answer


async def test_live_model_creates_verifies_and_completes_file_task(tmp_path) -> None:
    config = _live_config(tmp_path)

    answer, events = await _run_live_task(
        config,
        (
            "Crie src/live_created.py com uma funcao live_value() que retorna "
            "'LIVE_OK_73'. Verifique executando um comando Python real que importe "
            "a funcao e confirme o retorno. Use este interpretador exato no comando "
            f"de verificacao: {sys.executable}. Finalize somente depois da verificacao."
        ),
    )

    path = tmp_path / "src" / "live_created.py"
    assert path.exists()
    assert "LIVE_OK_73" in path.read_text(encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.live_created import live_value; assert live_value() == 'LIVE_OK_73'",
        ],
        cwd=tmp_path,
        check=True,
    )

    names = _tool_names(events)
    assert "list_files" in names
    assert "write_file" in names or "edit_code" in names
    assert "execute_command" in names
    assert "complete_task" in names
    if "write_file" in names:
        assert names.index("list_files") < names.index("write_file")
    if "edit_code" in names:
        assert names.index("list_files") < names.index("edit_code")
    assert "assistant.final" in {event.event_type for event in events}
    assert "src/live_created.py" in answer


async def test_live_model_uses_saved_workspace_for_project_today_without_web() -> None:
    config = _saved_workspace_live_config()

    greeting, greeting_events = await _run_live_task(config, "Olá")
    assert greeting.strip()
    assert _tool_names(greeting_events) == []

    answer, project_events = await _run_live_task(
        config,
        "O que temos no projeto hoje? Inspecione o workspace local salvo na configuracao.",
    )

    names = _tool_names(project_events)
    assert "list_files" in names
    assert "web_search" not in names
    assert answer.strip()
