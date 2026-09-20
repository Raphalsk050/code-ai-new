"""A terminal the user opens must be one the agent knows about.

It is created outside any turn, so nothing in the conversation mentioned it:
asked about the session id shown in the panel, the agent answered that it had
no access to the editor and could not tell which shell it was, while holding
tools that drive that exact session.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from code_ai.app.commands import SessionCommands


class _Manager:
    def __init__(self) -> None:
        self.created: list[tuple] = []

    def create(self, *, cwd, command=None):
        self.created.append((cwd, command))
        return "sess-1234abcd"

    def read_screen(self, session_id):
        return {
            "session_id": session_id,
            "rows": 24,
            "columns": 80,
            "screen": "",
            "closed": False,
        }


class _Conversation:
    def __init__(self) -> None:
        self.notes: list[str] = []

    def add_user(self, text: str, **_kwargs) -> None:
        self.notes.append(text)


@pytest.fixture
def commands(tmp_path):
    conversation = _Conversation()
    app = SimpleNamespace(
        session=SimpleNamespace(config=SimpleNamespace(workspace=tmp_path)),
        orchestrator=SimpleNamespace(conversation=conversation),
        event_bus=SimpleNamespace(emit=_noop),
        terminal_manager=_Manager(),
    )
    return SessionCommands(app), conversation


async def _noop(*_args, **_kwargs):
    return None


async def test_opening_a_terminal_tells_the_model_its_session_id(commands) -> None:
    cmds, conversation = commands
    result = await cmds.terminal_start()

    assert result.ok
    assert len(conversation.notes) == 1
    note = conversation.notes[0]
    # The id is the part the agent needs: without it the tools cannot target
    # the session the user is looking at.
    assert "sess-1234abcd" in note
    assert "[runtime]" in note
    assert "session_id" in note


async def test_the_note_names_the_command_when_one_was_given(commands) -> None:
    cmds, conversation = commands
    await cmds.terminal_start("pwsh -NoLogo")
    assert "`pwsh -NoLogo`" in conversation.notes[0]


async def test_a_plain_shell_is_described_as_such(commands) -> None:
    cmds, conversation = commands
    await cmds.terminal_start()
    assert "the default shell" in conversation.notes[0]


async def test_the_note_says_the_session_persists(commands) -> None:
    # Without this the agent treats each command as a fresh shell and re-runs
    # the cd or the activate every time.
    cmds, conversation = commands
    await cmds.terminal_start()
    assert "persists" in conversation.notes[0]


async def test_no_backend_means_no_note(tmp_path) -> None:
    conversation = _Conversation()
    app = SimpleNamespace(
        session=SimpleNamespace(config=SimpleNamespace(workspace=tmp_path)),
        orchestrator=SimpleNamespace(conversation=conversation),
        event_bus=SimpleNamespace(emit=_noop),
        terminal_manager=None,
    )

    result = await SessionCommands(app).terminal_start()
    assert not result.ok
    assert conversation.notes == []
