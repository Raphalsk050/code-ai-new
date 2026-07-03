from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from code_ai.providers.models import ImageContent, Message, ToolCall

logger = logging.getLogger(__name__)

# Only these characters survive in a conversation id -> filename mapping, so an
# id from the client can never escape the store directory (path traversal).
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def _safe_id(conversation_id: str) -> str:
    cleaned = _SAFE_ID.sub("-", conversation_id.strip())
    return cleaned or "conversation"


def _message_to_record(message: Message) -> dict[str, Any]:
    """Round-trippable dict for one message (unlike ``Message.to_dict`` which
    emits the lossy OpenAI wire shape with arguments serialized to strings)."""
    record: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id:
        record["tool_call_id"] = message.tool_call_id
    if message.name:
        record["name"] = message.name
    if message.tool_calls:
        record["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
    if message.images:
        record["images"] = [image.to_dict() for image in message.images]
    return record


def _message_from_record(record: dict[str, Any]) -> Message:
    calls = [
        ToolCall(
            id=str(call.get("id") or ""),
            name=str(call.get("name") or ""),
            arguments=call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
        )
        for call in record.get("tool_calls") or []
    ]
    images = [
        ImageContent(
            data=str(image.get("data") or ""),
            media_type=str(image.get("media_type") or "image/png"),
        )
        for image in record.get("images") or []
        if image.get("data")
    ]
    return Message(
        role=record.get("role", "user"),
        content=str(record.get("content") or ""),
        tool_call_id=record.get("tool_call_id"),
        name=record.get("name"),
        tool_calls=calls,
        images=images,
    )


def _derive_title(messages: list[Message]) -> str:
    """First user line, trimmed to a readable card title (mirrors the webview)."""
    for message in messages:
        if message.role == "user" and message.content.strip():
            raw = re.sub(r"\s+", " ", message.content).strip()
            return raw[:60] + "…" if len(raw) > 60 else raw
    return "New conversation"


class ConversationStore:
    """Durable, per-workspace archive of chat conversations.

    Each conversation is a single JSON file keyed by the client-supplied id,
    holding the provider ``Message`` history (minus the system prompt, which is
    rebuilt fresh on load so rules/skills stay current). This is what makes the
    extension's history "real": conversations survive bridge restarts and the
    model can resume with full context.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def _path(self, conversation_id: str) -> Path:
        return self._dir / f"{_safe_id(conversation_id)}.json"

    def save(
        self,
        *,
        conversation_id: str,
        messages: list[Message],
        previous_response_id: str | None = None,
        title: str | None = None,
    ) -> None:
        """Persist the (non-system) messages of a conversation. Best-effort:
        callers should not let a storage failure abort a turn."""
        self._dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        existing = self.load(conversation_id)
        record = {
            "id": conversation_id,
            "title": title or _derive_title(messages),
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
            "previous_response_id": previous_response_id,
            "messages": [_message_to_record(m) for m in messages],
        }
        path = self._path(conversation_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # atomic swap so a crash never leaves a half-written file

    def load(self, conversation_id: str) -> dict[str, Any] | None:
        path = self._path(conversation_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read conversation %s: %s", conversation_id, exc)
            return None

    def load_messages(self, conversation_id: str) -> list[Message]:
        data = self.load(conversation_id)
        if not data:
            return []
        return [_message_from_record(r) for r in data.get("messages", [])]

    def list(self) -> list[dict[str, Any]]:
        """Conversation metadata, newest-first (id, title, timestamps, count)."""
        if not self._dir.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            messages = data.get("messages", [])
            if not messages:
                continue
            out.append(
                {
                    "id": data.get("id"),
                    "title": data.get("title", ""),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "message_count": len(messages),
                }
            )
        out.sort(key=lambda d: d.get("updated_at", 0), reverse=True)
        return out

    def delete(self, conversation_id: str) -> bool:
        path = self._path(conversation_id)
        if path.exists():
            path.unlink()
            return True
        return False
