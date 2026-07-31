from __future__ import annotations

import json
from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)
from code_ai.ui.terminal.view_models import TerminalViewModel

# --- view model live rendering ----------------------------------------------


def _event(payload: dict[str, object]) -> EventEnvelope:
    return EventEnvelope.create(
        event_type="tool.call.progress", session_id="test", sequence=0, payload=payload
    )


def test_view_model_updates_writing_line_in_place() -> None:
    vm = TerminalViewModel()
    vm.apply(_event({"name": "write_file", "path": "src/foo.py", "lines": 3, "chars": 40}))
    vm.apply(_event({"name": "write_file", "path": "src/foo.py", "lines": 20, "chars": 400}))

    writing = [line for line in vm.conversation if line.startswith("tool~ write_file:")]
    assert len(writing) == 1  # updated in place, not appended twice
    assert writing[0] == "tool~ write_file: writing src/foo.py (20 lines)"


def test_view_model_progress_without_path_shows_chars() -> None:
    vm = TerminalViewModel()
    vm.apply(_event({"name": "execute_command", "chars": 25}))
    assert vm.conversation[-1] == "tool~ execute_command: building call (25 chars)"


# --- live code window state --------------------------------------------------


def _opening_event(**extra: object) -> EventEnvelope:
    """The first word about a writing call, before any source exists."""
    payload: dict[str, object] = {
        "name": "write_file",
        "call_started": True,
        "writes": True,
        "chars": 20,
    }
    payload.update(extra)
    return _event(payload)


def _code_event(offset: int, delta: str, **extra: object) -> EventEnvelope:
    payload: dict[str, object] = {
        "name": "write_file",
        "writes": True,
        "path": "src/foo.py",
        "code_key": "content",
        "code_offset": offset,
        "code_delta": delta,
        "code_complete": False,
    }
    payload.update(extra)
    return _event(payload)


def test_the_window_opens_before_any_source_arrives() -> None:
    vm = TerminalViewModel()
    vm.apply(_opening_event(path="src/foo.py", reason="bound the cache"))

    assert vm.code_stream_visible is True
    assert vm.code_stream_tool == "write_file"
    assert vm.code_stream_path == "src/foo.py"
    assert vm.code_stream_reason == "bound the cache"
    assert vm.code_stream_code == ""


def test_the_reason_can_land_after_the_window_opened() -> None:
    vm = TerminalViewModel()
    vm.apply(_opening_event())
    vm.apply(_code_event(0, "a = 1\n", reason="  keep it small  "))

    assert vm.code_stream_reason == "keep it small"


def test_code_window_grows_from_append_only_deltas() -> None:
    vm = TerminalViewModel()
    vm.apply(_opening_event())
    vm.apply(_code_event(0, "def f():\n"))
    vm.apply(_code_event(9, "    return 1\n", code_complete=True))

    assert vm.code_stream_visible is True
    assert vm.code_stream_code == "def f():\n    return 1\n"
    assert vm.code_stream_path == "src/foo.py"
    assert vm.code_stream_key == "content"
    assert vm.code_stream_complete is True


def test_a_second_call_opens_a_fresh_window() -> None:
    vm = TerminalViewModel()
    vm.apply(_opening_event())
    vm.apply(_code_event(0, "old file\n", code_complete=True))
    vm.apply(_opening_event(reason="a different change"))
    vm.apply(_code_event(0, "new file\n", path="src/bar.py"))

    assert vm.code_stream_code == "new file\n"
    assert vm.code_stream_path == "src/bar.py"
    assert vm.code_stream_reason == "a different change"
    assert vm.code_stream_complete is False


def test_out_of_order_delta_is_ignored_rather_than_spliced() -> None:
    vm = TerminalViewModel()
    vm.apply(_opening_event())
    vm.apply(_code_event(0, "a = 1\n"))
    vm.apply(_code_event(999, "c = 3\n"))

    assert vm.code_stream_code == "a = 1\n"


def test_progress_without_source_leaves_the_window_closed() -> None:
    vm = TerminalViewModel()
    vm.apply(_event({"name": "execute_command", "call_started": True, "chars": 25}))

    assert vm.code_stream_visible is False


def test_a_completed_write_settles_the_window() -> None:
    vm = TerminalViewModel()
    vm.apply(_opening_event())
    vm.apply(_code_event(0, "a = 1\n"))
    vm.apply(
        EventEnvelope.create(
            event_type="tool.call.completed",
            session_id="test",
            sequence=1,
            payload={"name": "write_file", "result": {"path": "src/foo.py"}},
        )
    )

    assert vm.code_stream_complete is True
    assert vm.code_stream_visible is True


def test_an_interrupted_call_closes_the_window() -> None:
    # The call never arrived, so the file it was filling in is not being
    # written by anyone: the window must close rather than sit there spinning.
    vm = TerminalViewModel()
    vm.apply(_opening_event())
    vm.apply(_code_event(0, "a = 1\n"))
    vm.apply(
        EventEnvelope.create(
            event_type="tool.call.interrupted",
            session_id="test",
            sequence=1,
            payload={"attempt": 1, "max_attempts": 2},
        )
    )

    assert vm.code_stream_visible is False
    assert vm.code_stream_code == ""
    assert "cut off mid-stream" in vm.conversation[-1]


def test_a_new_user_turn_clears_the_window() -> None:
    vm = TerminalViewModel()
    vm.apply(_opening_event())
    vm.apply(_code_event(0, "a = 1\n", code_complete=True))
    vm.apply(
        EventEnvelope.create(
            event_type="user.message", session_id="test", sequence=1, payload={"text": "next"}
        )
    )

    assert vm.code_stream_visible is False
    assert vm.code_stream_code == ""


# --- end-to-end throttled emission through the orchestrator ------------------


class _WritingProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        base = '{"path": "a.py", "content": "'
        yield ProviderEvent(
            kind="tool_call_delta", tool_call_name="write_file", tool_call_arguments=base
        )
        # One extra char: below the throttle step, must not emit a second update.
        yield ProviderEvent(
            kind="tool_call_delta", tool_call_name="write_file", tool_call_arguments=base + "x"
        )
        # A big jump: crosses the step, so a fresh progress update fires.
        big = base + "line\\n" * 80 + '"'
        yield ProviderEvent(
            kind="tool_call_delta", tool_call_name="write_file", tool_call_arguments=big
        )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


def _config(tmp_path) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
        }
    )


async def test_orchestrator_emits_throttled_progress(tmp_path) -> None:
    app = build_application(config=_config(tmp_path), provider=_WritingProvider())
    envelopes: list[EventEnvelope] = []
    app.subscribe(envelopes.append)

    await app.start()
    await app.submit_user_message("write the file")
    await app.close()

    progress = [e for e in envelopes if e.event_type == "tool.call.progress"]
    # First fragment emits; the +1-char fragment is throttled; the big jump emits.
    assert len(progress) == 2
    assert progress[0].payload["path"] == "a.py"
    assert progress[0].payload["name"] == "write_file"
    assert progress[1].payload["lines"] > progress[0].payload["lines"]


# --- streamed source deltas --------------------------------------------------


class _FragmentProvider:
    """Streams one tool call's arguments a few characters at a time."""

    def __init__(self, arguments: str, name: str = "write_file", step: int = 7) -> None:
        self._arguments = arguments
        self._name = name
        self._step = step

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        for index in range(self._step, len(self._arguments) + self._step, self._step):
            yield ProviderEvent(
                kind="tool_call_delta",
                tool_call_name=self._name,
                tool_call_arguments=self._arguments[:index],
            )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def _progress_for(tmp_path, arguments: str, name: str = "write_file"):
    app = build_application(
        config=_config(tmp_path), provider=_FragmentProvider(arguments, name)
    )
    envelopes: list[EventEnvelope] = []
    app.subscribe(envelopes.append)
    await app.start()
    await app.submit_user_message("go")
    await app.close()
    return [e.payload for e in envelopes if e.event_type == "tool.call.progress"]


async def test_code_deltas_reassemble_into_the_written_file(tmp_path) -> None:
    content = "def main():\n    print('olá 🚀')\n    return 0\n" * 6
    arguments = json.dumps({"path": "src/app.py", "content": content}, ensure_ascii=True)

    payloads = await _progress_for(tmp_path, arguments)

    coded = [p for p in payloads if "code_delta" in p]
    assert coded, "a writing tool must stream its source"
    # Offsets are contiguous, so a consumer can append instead of re-rendering.
    offset = 0
    rebuilt = ""
    for payload in coded:
        assert payload["code_offset"] == offset
        assert payload["code_key"] == "content"
        rebuilt += payload["code_delta"]
        offset = len(rebuilt)
    assert rebuilt == content
    assert coded[-1]["code_complete"] is True
    assert coded[-1]["lines"] == content.count("\n") + 1
    assert not any(payload["code_complete"] for payload in coded[:-1])


async def test_final_flush_releases_the_throttled_tail(tmp_path) -> None:
    # A file short enough that every fragment after the first is throttled away:
    # without the end-of-stream flush the preview would stop at the first chunk.
    arguments = json.dumps({"path": "a.py", "content": "x = 1\ny = 2\n"})

    payloads = await _progress_for(tmp_path, arguments)

    coded = [p for p in payloads if "code_delta" in p]
    assert "".join(p["code_delta"] for p in coded) == "x = 1\ny = 2\n"
    assert coded[-1]["code_complete"] is True


async def test_edit_code_previews_the_replacement_text(tmp_path) -> None:
    arguments = json.dumps(
        {"path": "a.py", "old_text": "before\n", "new_text": "after\nmore\n"}
    )

    payloads = await _progress_for(tmp_path, arguments, name="edit_code")

    coded = [p for p in payloads if "code_delta" in p]
    assert {p["code_key"] for p in coded} == {"new_text"}
    assert "".join(p["code_delta"] for p in coded) == "after\nmore\n"


async def test_the_write_is_announced_before_its_source(tmp_path) -> None:
    # The arguments are declared path -> reason -> content, so this is the order
    # a model streams them in: the UI learns what is being written, and why,
    # while the file itself is still arriving.
    arguments = json.dumps(
        {
            "path": "src/app.py",
            "reason": "bound the cache so the budget holds",
            "content": "x = 1\n" * 40,
        }
    )

    payloads = await _progress_for(tmp_path, arguments)

    assert payloads[0]["call_started"] is True
    assert payloads[0]["writes"] is True
    reasoned = next(p for p in payloads if "reason" in p)
    first_code = next(p for p in payloads if p.get("code_delta"))
    assert reasoned["reason"] == "bound the cache so the budget holds"
    assert payloads.index(reasoned) <= payloads.index(first_code)
    assert not any(p.get("call_started") for p in payloads[1:])


async def test_a_non_writing_call_is_not_announced_as_a_write(tmp_path) -> None:
    payloads = await _progress_for(
        tmp_path, json.dumps({"command": "ls -la"}), name="execute_command"
    )

    assert payloads[0]["call_started"] is True
    assert not any(payload.get("writes") for payload in payloads)


async def test_non_writing_tool_streams_no_source(tmp_path) -> None:
    # code_review takes a "content" argument too, but it does not write it
    # anywhere - previewing it as a file being edited would be a lie.
    arguments = json.dumps({"content": "def f():\n    pass\n" * 20})

    payloads = await _progress_for(tmp_path, arguments, name="code_review")

    assert payloads, "progress is still reported for the call itself"
    assert not any("code_delta" in payload for payload in payloads)


async def test_unknown_tool_name_does_not_break_the_turn(tmp_path) -> None:
    payloads = await _progress_for(
        tmp_path, json.dumps({"content": "x" * 400}), name="not_a_real_tool"
    )

    assert not any("code_delta" in payload for payload in payloads)


class _SplitNameProvider:
    """Delivers the tool's name across two fragments, as some servers do."""

    def __init__(self, arguments: str, name: str = "write_file") -> None:
        self._arguments = arguments
        self._name = name

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        # First fragment names a tool that does not exist yet ("write"), which is
        # what the accumulated name looks like before its tail arrives.
        head = self._name[:5]
        yield ProviderEvent(
            kind="tool_call_delta", tool_call_name=head, tool_call_arguments=""
        )
        for index in range(9, len(self._arguments) + 9, 9):
            yield ProviderEvent(
                kind="tool_call_delta",
                tool_call_name=self._name,
                tool_call_arguments=self._arguments[:index],
            )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_a_name_arriving_in_pieces_still_opens_the_window(tmp_path) -> None:
    # The first fragment names no known tool, so the call is initially taken for
    # one that writes nothing. Once the name completes it must be re-read, or
    # the window stays shut for the whole write.
    content = "x = 1\ny = 2\nz = 3\n"
    arguments = json.dumps({"path": "a.py", "content": content})
    app = build_application(
        config=_config(tmp_path), provider=_SplitNameProvider(arguments)
    )
    envelopes: list[EventEnvelope] = []
    app.subscribe(envelopes.append)
    await app.start()
    await app.submit_user_message("go")
    await app.close()

    payloads = [e.payload for e in envelopes if e.event_type == "tool.call.progress"]
    opening = [p for p in payloads if p.get("call_started")]
    assert opening, "the window must be told where the call begins"
    # Exactly one opening, and it is the one that declares the write - a second
    # would reset the window and drop the source already shown in it.
    assert len(opening) == 1
    assert opening[-1].get("writes") is True

    vm = TerminalViewModel()
    for envelope in envelopes:
        vm.apply(envelope)
    assert vm.code_stream_visible is True
    assert vm.code_stream_code == content


async def test_an_invented_tool_name_is_not_announced(tmp_path) -> None:
    # A name no tool answers to says nothing worth showing, and announcing it
    # would decide whether the window opens from a fragment.
    arguments = json.dumps({"path": "a.py", "content": "x = 1\n"})
    app = build_application(
        config=_config(tmp_path), provider=_FragmentProvider(arguments, "frobnicate")
    )
    envelopes: list[EventEnvelope] = []
    app.subscribe(envelopes.append)
    await app.start()
    await app.submit_user_message("go")
    await app.close()

    assert not [e for e in envelopes if e.event_type == "tool.call.progress"]
