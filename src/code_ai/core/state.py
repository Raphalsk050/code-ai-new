from __future__ import annotations

from enum import StrEnum


class AgentState(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    CALLING_MODEL = "CALLING_MODEL"
    EXECUTING_TOOL = "EXECUTING_TOOL"
    COMPRESSING_CONTEXT = "COMPRESSING_CONTEXT"
    CANCELLING = "CANCELLING"
    FAILED = "FAILED"
    CLOSED = "CLOSED"
