"""Non-ASCII text from the extension must reach the model intact, on Windows too."""

from __future__ import annotations

import asyncio
import io
import json
import sys

from code_ai.app.conversation_store import ConversationStore
from code_ai.util.unicode import scrub, scrub_json, use_utf8_stdio

SAMPLE = "ÍNDICE “grande” — ação 🔝\n"


def test_the_pipe_is_read_as_utf8_whatever_the_locale(monkeypatch) -> None:
    # What Windows hands a piped process: its ANSI code page.
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(SAMPLE.encode()), "cp1252"))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), "cp1252"))

    use_utf8_stdio()

    assert sys.stdin.readline() == SAMPLE
    assert sys.stdout.encoding == "utf-8"


def test_invalid_bytes_become_replacement_characters_not_surrogates(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"ok \x9d\n"), "cp1252"))

    use_utf8_stdio()

    assert sys.stdin.readline() == "ok �\n"


def test_lone_surrogates_are_replaced_and_everything_else_kept() -> None:
    assert scrub("a\udc9db😀") == "a�b😀"
    assert scrub_json({"text": ["\ud83d", 1, {"x": "ç"}]}) == {"text": ["�", 1, {"x": "ç"}]}


async def test_the_bridge_scrubs_what_it_receives() -> None:
    from tests.unit.test_session_commands import _App, _messages, _server

    server, out = _server(_App())
    # JSON can escape a lone surrogate; the handler must never see it.
    request = {"jsonrpc": "2.0", "id": 1, "method": "runAsset", "params": {"token": "/\ud83d"}}
    await server._handle_line(json.dumps(request))
    await asyncio.gather(*server._turn_tasks)

    (response,) = _messages(out)
    assert "�" in response["result"]["message"]


def test_a_conversation_saved_with_a_lone_surrogate_resumes(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    (tmp_path / "abc.json").write_text(
        json.dumps({"id": "abc", "messages": [{"role": "user", "content": "x\udc8dy"}]}),
        encoding="utf-8",
    )

    [message] = store.load_messages("abc")

    assert message.content == "x�y"
