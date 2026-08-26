from __future__ import annotations

from code_ai.config.models import SamplingConfig
from code_ai.providers.openai_completions import _reasoning_delta


def test_reasoning_effort_reaches_chat_completions() -> None:
    """Several local servers enable thinking on this field and nothing else."""

    sampling = SamplingConfig.from_mapping({"reasoning_effort": "medium"})
    assert sampling.chat_completion_kwargs()["reasoning_effort"] == "medium"


def test_unset_reasoning_effort_is_omitted() -> None:
    kwargs = SamplingConfig.from_mapping({}).chat_completion_kwargs()
    assert "reasoning_effort" not in kwargs


def test_reasoning_effort_still_reaches_the_responses_api() -> None:
    sampling = SamplingConfig.from_mapping({"reasoning_effort": "high"})
    assert sampling.responses_kwargs()["reasoning"] == {"effort": "high"}


def test_reasoning_summary_stays_out_of_chat_completions() -> None:
    """It is a Responses-only field; sending it would trip a strict server."""

    sampling = SamplingConfig.from_mapping(
        {"reasoning_effort": "low", "reasoning_summary": "detailed"}
    )
    assert "reasoning_summary" not in sampling.chat_completion_kwargs()


def test_thinking_field_is_read_as_reasoning() -> None:
    assert _reasoning_delta({"thinking": "step one"}) == "step one"


def test_reasoning_content_wins_when_several_are_present() -> None:
    delta = {"reasoning_content": "primary", "reasoning": "secondary"}
    assert _reasoning_delta(delta) == "primary"


def test_missing_reasoning_yields_empty_string() -> None:
    assert _reasoning_delta({"content": "hello"}) == ""
