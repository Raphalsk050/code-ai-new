from __future__ import annotations

import json

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.review.service import ReviewResult, ReviewService

TWO_FINDINGS = [
    {"severity": "high", "message": "null deref at a.py:1"},
    {"severity": "high", "message": "off-by-one at b.py:2"},
]


def _first_pass(findings: list[dict[str, str]]) -> str:
    return json.dumps({"summary": "reviewed", "findings": findings})


class _ScriptedProvider:
    """Replies with each queued response in order, recording what it was asked."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def complete(self, request):  # noqa: ANN001 - test double
        self.prompts.append(request.messages[-1].content)
        if not self._replies:
            raise RuntimeError("provider is down")
        text = self._replies.pop(0)

        class _Response:
            usage = None

        response = _Response()
        response.text = text
        return response


def _service(provider, tmp_path) -> ReviewService:
    return ReviewService(
        provider=provider,
        config=AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)}),
        event_bus=AsyncEventBus(session_id="session"),
    )


async def _review(provider, tmp_path, *, refute: bool = True, content: str = "code"):
    return await _service(provider, tmp_path).review(
        prompt="p", content=content, source="code_review", refute=refute
    )


async def test_refuted_findings_are_dropped(tmp_path) -> None:
    provider = _ScriptedProvider(
        [_first_pass(TWO_FINDINGS), json.dumps({"survived": [1], "refuted": [{"index": 0}]})]
    )
    result = await _review(provider, tmp_path)
    assert [f["message"] for f in result.findings] == ["off-by-one at b.py:2"]
    assert "dropped" in result.summary


async def test_everything_refuted_is_honoured(tmp_path) -> None:
    """An explicitly empty survivor list is a verdict, not a missing answer."""

    provider = _ScriptedProvider([_first_pass(TWO_FINDINGS), json.dumps({"survived": []})])
    assert (await _review(provider, tmp_path)).findings == []


async def test_unparseable_refutation_keeps_every_finding(tmp_path) -> None:
    provider = _ScriptedProvider([_first_pass(TWO_FINDINGS), "I could not decide."])
    assert len((await _review(provider, tmp_path)).findings) == 2


async def test_refutation_reply_without_the_key_keeps_findings(tmp_path) -> None:
    provider = _ScriptedProvider([_first_pass(TWO_FINDINGS), json.dumps({"notes": "x"})])
    assert len((await _review(provider, tmp_path)).findings) == 2


async def test_out_of_range_indices_do_not_silently_empty_the_review(tmp_path) -> None:
    provider = _ScriptedProvider([_first_pass(TWO_FINDINGS), json.dumps({"survived": [7, 9]})])
    assert len((await _review(provider, tmp_path)).findings) == 2


async def test_partially_valid_indices_are_honoured(tmp_path) -> None:
    provider = _ScriptedProvider([_first_pass(TWO_FINDINGS), json.dumps({"survived": [1, 42]})])
    result = await _review(provider, tmp_path)
    assert [f["message"] for f in result.findings] == ["off-by-one at b.py:2"]


async def test_review_without_findings_skips_the_second_call(tmp_path) -> None:
    provider = _ScriptedProvider([_first_pass([])])
    result = await _review(provider, tmp_path)
    assert result.findings == []
    assert len(provider.prompts) == 1


async def test_refutation_is_opt_in(tmp_path) -> None:
    provider = _ScriptedProvider([_first_pass(TWO_FINDINGS)])
    result = await _review(provider, tmp_path, refute=False)
    assert len(result.findings) == 2
    assert len(provider.prompts) == 1


async def test_refutation_sees_the_code_and_the_candidates(tmp_path) -> None:
    provider = _ScriptedProvider([_first_pass(TWO_FINDINGS), json.dumps({"survived": [0, 1]})])
    await _review(provider, tmp_path, content="the code under test")
    refute_prompt = provider.prompts[1]
    assert "the code under test" in refute_prompt
    assert "null deref at a.py:1" in refute_prompt
    assert "survived" in refute_prompt


async def test_failing_refutation_call_leaves_the_review_intact(tmp_path) -> None:
    """A flaky second pass must never cost a real finding."""

    provider = _ScriptedProvider([_first_pass(TWO_FINDINGS)])
    result = await _service(provider, tmp_path).review(
        prompt="p", content="code", source="code_review", refute=True
    )
    assert len(result.findings) == 2


def test_result_serialization_is_unchanged() -> None:
    result = ReviewResult(summary="s", findings=[{"a": "b"}], usage=None)
    assert result.to_dict() == {"summary": "s", "findings": [{"a": "b"}], "usage": None}
