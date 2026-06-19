from __future__ import annotations

from dataclasses import dataclass

from code_ai.config.models import AppConfig
from code_ai.core.state import AgentState


@dataclass(slots=True)
class ApplicationSession:
    session_id: str
    config: AppConfig
    state: AgentState = AgentState.STARTING
