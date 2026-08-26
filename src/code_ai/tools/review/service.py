from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.base import ModelProvider
from code_ai.providers.models import Message, ModelRequest, TokenUsage
from code_ai.tools.output import bound_text
from code_ai.tools.review.prompts import build_refutation_prompt


def _combined_usage(first: TokenUsage | None, second: TokenUsage | None) -> TokenUsage | None:
    """Bill both passes together so a refuted review reports what it really cost."""

    if first is None or second is None:
        return first or second
    return TokenUsage.from_counts(
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        exact=first.exact and second.exact,
        source=first.source,
    )


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

    async def review(
        self, *, prompt: str, content: str, source: str, refute: bool = False
    ) -> ReviewResult:
        bounded = bound_text(content, self._config.budgets.max_tool_output_chars)
        result = await self._review_once(prompt=prompt, content=bounded, source=source)
        if not refute or not result.findings:
            return result
        return await self._keep_what_survives(result, content=bounded, source=source)

    async def _review_once(
        self, *, prompt: str, content: str, source: str
    ) -> ReviewResult:
        request = ModelRequest(
            model=self._config.model,
            messages=[
                Message(
                    role="system",
                    content=prompt + "\nReturn concise JSON with summary and findings.",
                ),
                Message(role="user", content=content),
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

    async def _keep_what_survives(
        self, result: ReviewResult, *, content: str, source: str
    ) -> ReviewResult:
        """Drop the findings a second, adversarial pass manages to refute.

        Fails open at every step: a refutation call that errors, returns
        unparseable text, or names no indices at all leaves the review exactly as
        it was. Losing a real finding to a flaky second call is far worse than
        showing one that a stricter pass would have removed. An explicitly empty
        ``survived`` list is the one case where "nothing" is the answer, and it
        is honoured.
        """

        request = ModelRequest(
            model=self._config.model,
            messages=[
                Message(
                    role="system",
                    content=(
                        "You adversarially check the findings of a code review. "
                        "You have only the code you are shown; do not assume "
                        "anything about code you cannot see. A finding survives "
                        "unless you can make a concrete case against it."
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        f"{build_refutation_prompt(result.findings)}\n\n"
                        f"The code under review:\n{content}"
                    ),
                ),
            ],
            tools=[],
            max_output_tokens=min(2048, self._config.output_token_reserve),
            use_remote_conversation_state=False,
        )
        await self._event_bus.emit(
            "model.request.started", {"review": f"{source}.refute"}, source=f"tool.{source}"
        )
        try:
            response = await asyncio.wait_for(
                self._provider.complete(request),
                timeout=min(
                    self._config.budgets.subagent_worker_timeout_s,
                    self._config.budgets.max_model_call_s,
                ),
            )
        except Exception:
            return result
        await self._event_bus.emit(
            "usage.updated",
            {
                "usage": response.usage.to_dict() if response.usage else None,
                "review": f"{source}.refute",
            },
            source=f"tool.{source}",
        )
        survived = self._parse_survivors(response.text, total=len(result.findings))
        if survived is None:
            return result
        kept = [result.findings[index] for index in survived]
        dropped = len(result.findings) - len(kept)
        summary = result.summary
        if dropped:
            summary = (
                f"{summary}\n\n({dropped} candidate finding(s) dropped: a second "
                "pass refuted them.)"
            ).strip()
        return ReviewResult(
            summary=summary,
            findings=kept,
            usage=_combined_usage(result.usage, response.usage),
        )

    @staticmethod
    def _parse_survivors(text: str, *, total: int) -> list[int] | None:
        """Indices that survived, or ``None`` when the reply says nothing usable."""

        try:
            parsed = json.loads(text)
        except Exception:
            return None
        if not isinstance(parsed, dict) or "survived" not in parsed:
            return None
        raw = parsed.get("survived")
        if not isinstance(raw, list):
            return None
        # Out-of-range or non-integer entries are dropped rather than treated as
        # a broken reply: a model that miscounts one index still judged the rest.
        survived = sorted(
            {index for index in raw if isinstance(index, int) and 0 <= index < total}
        )
        if not survived and raw:
            # Indices were offered but none were usable - that is a malformed
            # reply, not a verdict of "everything is refuted".
            return None
        return survived

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
