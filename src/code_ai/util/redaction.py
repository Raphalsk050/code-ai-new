from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

SECRET_TEXT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"auth[_-]?token|authorization|password|secret)(['\"\s:=]+)([^,'\"\s}]+)"
)


def is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    exact = {
        "api_key",
        "apikey",
        "token",
        "authorization",
        "password",
        "secret",
        "credential",
        "credentials",
    }
    if normalized in exact:
        return True
    return normalized.endswith("_token") and not normalized.endswith("_tokens")


def redact_text(text: str) -> str:
    return SECRET_TEXT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)


def redact_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if is_secret_key(str(key)):
                redacted[str(key)] = "<redacted>" if item else ""
            else:
                redacted[str(key)] = redact_mapping(item)
        return redacted
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact_mapping(item) for item in value]
    return value


def sanitized_environment(env: Mapping[str, str]) -> dict[str, str]:
    allowed_prefixes = ("LC_",)
    allowed_names = {
        "PATH",
        "HOME",
        "LANG",
        "TERM",
        "SHELL",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "SYSTEMROOT",
        "WINDIR",
    }
    result: dict[str, str] = {}
    for key, value in env.items():
        if is_secret_key(key):
            continue
        if key in allowed_names or key.startswith(allowed_prefixes):
            result[key] = value
    return result
