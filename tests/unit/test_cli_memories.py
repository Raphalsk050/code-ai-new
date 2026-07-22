from __future__ import annotations

from argparse import Namespace

import pytest

from code_ai.cli import memories as memories_cli
from code_ai.cli.main import build_parser
from code_ai.config.models import AppConfig
from code_ai.core.memory import FailureMemoryStore, MemoryStore


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Point the CLI's store resolvers at throwaway directories."""

    knowledge = tmp_path / "knowledge"
    project = tmp_path / "project-memories"
    monkeypatch.setattr(memories_cli, "global_knowledge_dir", lambda: knowledge)
    monkeypatch.setattr(
        memories_cli, "project_memories_dir", lambda _workspace: project
    )
    return knowledge, project


def _config(tmp_path) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "memories_dir": str(tmp_path / "lessons"),
        }
    )


def _seed(tmp_path, isolated_dirs) -> FailureMemoryStore:
    knowledge, project = isolated_dirs
    MemoryStore(knowledge).add(kind="feedback", content="Always run pytest -q.")
    MemoryStore(project).add(kind="project", content="Build with make release.")
    return FailureMemoryStore(tmp_path / "lessons")


async def test_list_shows_all_scopes_and_lessons(tmp_path, isolated_dirs, capsys) -> None:
    store = _seed(tmp_path, isolated_dirs)
    await store.record(trigger="stall", context="c", fallback_lesson="Change approach.")

    exit_code = memories_cli.run_memories_command(
        _config(tmp_path), Namespace(memories_command=None)
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Always run pytest -q." in out
    assert "Build with make release." in out
    assert "Change approach." in out
    assert "[feedback]" in out
    assert "x1" in out  # lesson reinforcement count


def test_list_empty_store(tmp_path, isolated_dirs, capsys) -> None:
    exit_code = memories_cli.run_memories_command(
        _config(tmp_path), Namespace(memories_command=None)
    )
    assert exit_code == 0
    assert "Nothing learned yet." in capsys.readouterr().out


def test_forget_deletes_exactly_one_by_prefix(tmp_path, isolated_dirs, capsys) -> None:
    knowledge, _project = isolated_dirs
    entry = MemoryStore(knowledge).add(kind="feedback", content="Old directive.")

    exit_code = memories_cli.run_memories_command(
        _config(tmp_path),
        Namespace(memories_command="forget", memory_id=entry.id[:8]),
    )

    assert exit_code == 0
    assert "Forgot" in capsys.readouterr().out
    assert MemoryStore(knowledge).all() == []


def test_forget_unknown_prefix_fails_cleanly(tmp_path, isolated_dirs, capsys) -> None:
    exit_code = memories_cli.run_memories_command(
        _config(tmp_path),
        Namespace(memories_command="forget", memory_id="ffffffff"),
    )
    assert exit_code == 1
    assert "No memory or lesson matches" in capsys.readouterr().out


def test_forget_blank_prefix_is_rejected(tmp_path, isolated_dirs, capsys) -> None:
    knowledge, _project = isolated_dirs
    store = MemoryStore(knowledge)
    store.add(kind="feedback", content="Fact A.")

    exit_code = memories_cli.run_memories_command(
        _config(tmp_path), Namespace(memories_command="forget", memory_id="  ")
    )
    assert exit_code == 2
    assert len(store.all()) == 1


def test_forget_ambiguous_prefix_deletes_nothing(capsys) -> None:
    # Ids are content hashes, so a genuinely shared prefix cannot be seeded
    # through the stores; exercise the ambiguity guard on fabricated rows.
    deleted: list[str] = []
    rows = [
        memories_cli._Row(
            scope="global",
            entry_id=f"abc{i}",
            label="[feedback]",
            updated=0.0,
            text=f"Fact {i}.",
            delete=lambda i=i: deleted.append(str(i)) or True,
        )
        for i in range(2)
    ]

    exit_code = memories_cli._forget(rows, "abc")

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "ambiguous" in out
    assert deleted == []


def test_parser_wires_the_memories_subcommands() -> None:
    parser = build_parser()
    args = parser.parse_args(["memories"])
    assert args.command == "memories"
    assert args.memories_command is None

    args = parser.parse_args(["memories", "forget", "abc123"])
    assert args.memories_command == "forget"
    assert args.memory_id == "abc123"
