from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from code_ai.config.defaults import DEFAULT_CONFIG_DIRNAME

if TYPE_CHECKING:
    from code_ai.config.models import AppConfig
    from code_ai.providers.models import ModelResponse

_WRITE_LOCK = threading.Lock()

# One short hash per process run groups every model exchange of a session under a
# single folder. Generated lazily so importing the module is side-effect free.
_SESSION_ID: str | None = None
_CALL_COUNTER = 0


def session_id() -> str:
    global _SESSION_ID
    with _WRITE_LOCK:
        if _SESSION_ID is None:
            _SESSION_ID = uuid.uuid4().hex[:12]
        return _SESSION_ID


def logs_root() -> Path:
    """Base ``logs`` directory holding one sub-folder per session.

    Overridable with ``CODE_AI_DEBUG_DIR`` so tests (and power users) can redirect
    the transcripts without touching the home config tree.
    """
    override = os.environ.get("CODE_AI_DEBUG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_CONFIG_DIRNAME / "logs"


def session_log_dir() -> Path:
    """Folder for the current session's raw request/response transcripts."""
    return logs_root() / session_id()


def _next_call_index() -> int:
    global _CALL_COUNTER
    with _WRITE_LOCK:
        _CALL_COUNTER += 1
        return _CALL_COUNTER


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-serializable view of arbitrary provider objects.

    Streamed SDK chunks are pydantic models (or other opaque objects); we try the
    common dump hooks before falling back to ``repr`` so logging can never raise
    and break the live request it is observing.
    """
    for attr in ("model_dump", "to_dict", "dict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return method()
            except Exception:  # pragma: no cover - defensive, never block the call
                pass
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    try:
        return repr(value)
    except Exception:  # pragma: no cover - extremely defensive
        return "<unrepresentable>"


class ModelDebugLogger:
    """Append-only raw transcript of a single model exchange.

    One file per model call captures, in order: the exact request payload we sent,
    every raw chunk/line the provider streamed back (before any parsing), and the
    final parsed :class:`ModelResponse`. This makes it possible to see exactly where
    the tool-call / reasoning parser diverged from what the model actually emitted.
    """

    __slots__ = ("_path", "_provider", "_chunk_index")

    def __init__(self, *, provider: str, path: Path) -> None:
        self._provider = provider
        self._path = path
        self._chunk_index = 0

    @classmethod
    def for_request(cls, config: AppConfig, *, provider: str) -> ModelDebugLogger | None:
        """Build a logger when debug is on, otherwise ``None`` (a cheap no-op).

        Each call gets its own numbered file inside the session folder so the
        request (entrada) and the raw streamed response (saída) for one exchange
        live together: ``logs/<session>/0001-<provider>.log``.
        """
        if not getattr(config, "debug", False):
            return None
        try:
            directory = session_log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            index = _next_call_index()
            path = directory / f"{index:04d}-{provider}.log"
            return cls(provider=provider, path=path)
        except Exception:  # pragma: no cover - never let logging break a request
            return None

    def _emit(self, label: str, payload: Any) -> None:
        try:
            body = json.dumps(_jsonable(payload), indent=2, ensure_ascii=False, default=str)
            stamp = datetime.now(UTC).isoformat()
            block = f"\n===== {label} @ {stamp} =====\n{body}\n"
            with _WRITE_LOCK:
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(block)
        except Exception:  # pragma: no cover - logging is strictly best-effort
            pass

    def log_request(self, payload: Any) -> None:
        self._emit(f"REQUEST ({self._provider})", payload)

    def log_raw_chunk(self, chunk: Any) -> None:
        self._emit(f"RAW CHUNK #{self._chunk_index}", chunk)
        self._chunk_index += 1

    def log_response(self, response: ModelResponse) -> None:
        self._emit(
            "PARSED RESPONSE",
            {
                "text": response.text,
                "reasoning": response.reasoning,
                "finish_reason": getattr(response.finish_reason, "value", response.finish_reason),
                "response_id": response.response_id,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ],
            },
        )

    def log_error(self, exc: BaseException) -> None:
        self._emit("ERROR", {"type": type(exc).__name__, "message": str(exc)})
