from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import Message, ModelRequest, TokenUsage
from code_ai.tools.output import bound_text


@dataclass(slots=True)
class ReviewResult:
    summary: str
    findings: list[dict[str, str]]
    usage: TokenUsage | None

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "findings": self.findings,
            "usage": self.usage.to_dict() if self.usage else None,
        }


@dataclass(slots=True)
class GenerationResult:
    text: str
    usage: TokenUsage | None

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "usage": self.usage.to_dict() if self.usage else None,
        }


class ReviewService:
    """Runs one-shot review calls using the configured provider with tools disabled."""

    def __init__(
        self, *, provider: ModelProvider, config: AppConfig, event_bus: AsyncEventBus
    ) -> None:
        self._provider = provider
        self._config = config
        self._event_bus = event_bus

    async def review(self, *, prompt: str, content: str, source: str) -> ReviewResult:
        bounded = bound_text(content, self._config.budgets.max_tool_output_chars)
        request = ModelRequest(
            model=self._config.model,
            messages=[
                Message(
                    role="system",
                    content=prompt + "\nReturn concise JSON with summary and findings.",
                ),
                Message(role="user", content=bounded),
            ],
            tools=[],
            max_output_tokens=min(2048, self._config.output_token_reserve),
            use_remote_conversation_state=False,
        )
        await self._event_bus.emit(
            "model.request.started", {"review": source}, source=f"tool.{source}"
        )
        response = await asyncio.wait_for(
            self._provider.complete(request),
            timeout=min(
                self._config.budgets.subagent_worker_timeout_s,
                self._config.budgets.max_model_call_s,
            ),
        )
        await self._event_bus.emit(
            "usage.updated",
            {"usage": response.usage.to_dict() if response.usage else None, "review": source},
            source=f"tool.{source}",
        )
        return self._parse(response.text, response.usage)

    async def generate(self, *, prompt: str, content: str, source: str) -> GenerationResult:
        """Run a one-shot generation call that returns free-form text (no JSON contract)."""
        bounded = bound_text(content, self._config.budgets.max_tool_output_chars)
        request = ModelRequest(
            model=self._config.model,
            messages=[
                Message(role="system", content=prompt),
                Message(role="user", content=bounded),
            ],
            tools=[],
            max_output_tokens=min(2048, self._config.output_token_reserve),
            use_remote_conversation_state=False,
        )
        await self._event_bus.emit(
            "model.request.started", {"review": source}, source=f"tool.{source}"
        )
        response = await asyncio.wait_for(
            self._provider.complete(request),
            timeout=min(
                self._config.budgets.subagent_worker_timeout_s,
                self._config.budgets.max_model_call_s,
            ),
        )
        await self._event_bus.emit(
            "usage.updated",
            {"usage": response.usage.to_dict() if response.usage else None, "review": source},
            source=f"tool.{source}",
        )
        return GenerationResult(text=response.text.strip(), usage=response.usage)

    @staticmethod
    def _parse(text: str, usage: TokenUsage | None) -> ReviewResult:
        try:
            parsed = json.loads(text)
            summary = str(parsed.get("summary", "")).strip() or text.strip()
            findings_raw = parsed.get("findings") or []
            findings = [
                {str(key): str(value) for key, value in item.items()}
                for item in findings_raw
                if isinstance(item, dict)
            ]
            return ReviewResult(summary=summary, findings=findings, usage=usage)
        except Exception:
            return ReviewResult(
                summary=text.strip(),
                findings=[
                    {"severity": "unknown", "message": "Review response was not valid JSON."}
                ],
                usage=usage,
            )
