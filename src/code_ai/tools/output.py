from __future__ import annotations


def bound_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars < 64:
        return text[:max_chars]
    head_len = max_chars // 2
    tail_len = max_chars - head_len - 40
    return text[:head_len] + "\n...[truncated]...\n" + text[-tail_len:]
