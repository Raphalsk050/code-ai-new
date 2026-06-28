from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code_ai.providers import debug as debug_mod
from code_ai.providers.debug import ModelDebugLogger
from code_ai.providers.models import FinishReason, ModelResponse, ToolCall


@dataclass
class _Cfg:
    debug: bool = False


def _redirect_logs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODE_AI_DEBUG_DIR", str(tmp_path))


def test_for_request_is_noop_when_debug_off(tmp_path, monkeypatch) -> None:
    _redirect_logs(tmp_path, monkeypatch)
    assert ModelDebugLogger.for_request(_Cfg(debug=False), provider="ollama") is None
    assert list(tmp_path.iterdir()) == []


def test_for_request_writes_numbered_files_under_session(tmp_path, monkeypatch) -> None:
    _redirect_logs(tmp_path, monkeypatch)
    monkeypatch.setattr(debug_mod, "_SESSION_ID", "deadbeef")
    monkeypatch.setattr(debug_mod, "_CALL_COUNTER", 0)

    first = ModelDebugLogger.for_request(_Cfg(debug=True), provider="ollama")
    second = ModelDebugLogger.for_request(_Cfg(debug=True), provider="ollama")
    assert first is not None and second is not None

    session_dir = tmp_path / "deadbeef"
    first.log_request({"model": "gemma"})
    second.log_request({"model": "gemma"})

    names = sorted(p.name for p in session_dir.iterdir())
    assert names == ["0001-ollama.log", "0002-ollama.log"]


def test_full_exchange_records_request_chunks_and_parsed_response(tmp_path, monkeypatch) -> None:
    _redirect_logs(tmp_path, monkeypatch)
    monkeypatch.setattr(debug_mod, "_SESSION_ID", "session01")
    monkeypatch.setattr(debug_mod, "_CALL_COUNTER", 0)

    logger = ModelDebugLogger.for_request(_Cfg(debug=True), provider="ollama")
    assert logger is not None

    logger.log_request({"model": "gemma", "messages": [{"role": "user", "content": "hi"}]})
    logger.log_raw_chunk('{"message": {"content": "partial"}}')
    response = ModelResponse(
        text="done",
        reasoning="thinking",
        tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"})],
        finish_reason=FinishReason.TOOL_CALLS,
        response_id="resp-1",
    )
    logger.log_response(response)

    content = (tmp_path / "session01" / "0001-ollama.log").read_text(encoding="utf-8")
    assert "REQUEST (ollama)" in content
    assert "RAW CHUNK #0" in content
    assert "PARSED RESPONSE" in content
    # The parsed tool call must be visible so we can diff it against the raw chunk.
    assert "read_file" in content
    assert "a.txt" in content
    assert "tool_calls" in content


def test_jsonable_prefers_dump_hooks_and_falls_back_to_repr() -> None:
    class _Dumpable:
        def model_dump(self) -> dict[str, str]:
            return {"kind": "pydantic"}

    class _Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    assert debug_mod._jsonable(_Dumpable()) == {"kind": "pydantic"}
    assert debug_mod._jsonable(_Opaque()) == "<opaque>"
    assert debug_mod._jsonable({"a": [1, _Dumpable()]}) == {"a": [1, {"kind": "pydantic"}]}


def test_logging_never_raises_on_unserializable_payload(tmp_path, monkeypatch) -> None:
    _redirect_logs(tmp_path, monkeypatch)
    logger = ModelDebugLogger.for_request(_Cfg(debug=True), provider="ollama")
    assert logger is not None

    class _Boom:
        def __repr__(self) -> str:  # pragma: no cover - exercised indirectly
            raise RuntimeError("nope")

    # Must not propagate: logging is strictly best-effort around a live request.
    logger.log_raw_chunk(_Boom())
    logger.log_error(ValueError("bad parse"))
