from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.errors import ImageLimitError
from code_ai.providers.images import parse_image_limit
from code_ai.providers.models import (
    FinishReason,
    ImageContent,
    Message,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)
from code_ai.providers.openai_completions import OpenAIChatCompletionsProvider

_IMAGES = [ImageContent(data=f"img{index}", media_type="image/png") for index in range(4)]


def _config(tmp_path, **overrides) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "main-model",
        "permission_mode": "bypass",
        "memories_dir": str(tmp_path / "memories"),
        "vision_model": "",
        "max_images_per_request": 1,
        # These tests exercise the image path, not planning.
        "planner": {"enabled": False},
    }
    data.update(overrides)
    return AppConfig.from_mapping(data)


class RecordingProvider:
    """Refuses any request carrying more images than ``limit``, as the servers do."""

    def __init__(self, *, limit: int = 1) -> None:
        self.limit = limit
        self.complete_counts: list[int] = []
        self.stream_counts: list[int] = []
        self.refusals = 0
        self.capabilities = ProviderCapabilities(
            streaming=True, tool_calling=True, image_support=True
        )

    def _guard(self, request: ModelRequest) -> None:
        carried = sum(len(message.images) for message in request.messages)
        if carried > self.limit:
            self.refusals += 1
            raise ImageLimitError(
                f"Error code: 400 - {{'detail': 'At most {self.limit} image(s) may be "
                "provided in one prompt. (parameter=image)'}",
                limit=self.limit,
            )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.complete_counts.append(sum(len(m.images) for m in request.messages))
        self._guard(request)
        return ModelResponse(text="a description", finish_reason=FinishReason.STOP)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.stream_counts.append(sum(len(m.images) for m in request.messages))
        self._guard(request)
        yield ProviderEvent(kind="text_delta", text_delta="done")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# Parsing what the endpoint said
# --------------------------------------------------------------------------- #
def test_the_limit_is_read_from_the_refusal() -> None:
    assert (
        parse_image_limit(
            "Error code: 400 - {'detail': 'At most 1 image(s) may be provided in one "
            "prompt. (parameter=image)'}"
        )
        == 1
    )
    assert parse_image_limit("only 2 images are supported per request") == 2
    assert parse_image_limit("maximum of 4 images per prompt") == 4


def test_unrelated_failures_name_no_limit() -> None:
    assert parse_image_limit("Error code: 500 - internal server error") is None
    assert parse_image_limit("context length 4096 exceeded by 12 tokens") is None
    # Mentions images but names no cap: not a limit, just a failure.
    assert parse_image_limit("the image could not be decoded") is None


class _RefusingCompletions:
    async def create(self, **kwargs: object):
        raise RuntimeError(
            "Error code: 400 - {'detail': 'At most 1 image(s) may be provided in one "
            "prompt. (parameter=image)'}"
        )


class _RefusingChatClient:
    def __init__(self) -> None:
        self.chat = type("_Chat", (), {"completions": _RefusingCompletions()})()


async def test_the_chat_provider_reports_the_limit_it_was_told() -> None:
    """The 400 is a statement about the payload, so it is raised as one."""

    provider = object.__new__(OpenAIChatCompletionsProvider)
    provider._client = _RefusingChatClient()
    provider._config = AppConfig()
    provider._stream_options_supported = False
    provider._sampling_supported = False
    provider._capabilities = ProviderCapabilities(image_support=True)
    request = ModelRequest(
        model="m",
        messages=[Message(role="user", content="hi", images=list(_IMAGES))],
    )

    with pytest.raises(ImageLimitError) as caught:
        [event async for event in provider.stream(request)]

    assert caught.value.limit == 1
    # And it is remembered, so the next request is sized to fit rather than
    # rediscovering the same refusal.
    assert provider.capabilities.max_images_per_request == 1


# --------------------------------------------------------------------------- #
# Fitting the turn to the limit
# --------------------------------------------------------------------------- #
async def test_a_batch_of_images_is_split_into_accepted_requests(tmp_path) -> None:
    """Four pasted screenshots, one image per prompt: nothing is refused, nothing lost."""

    provider = RecordingProvider(limit=1)
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    result = await app.submit_user_message("what broke?", images=list(_IMAGES))
    await app.close()

    assert result.error is None
    assert provider.refusals == 0
    # Three transcribed one at a time; the newest rides along as pixels.
    assert provider.complete_counts == [1, 1, 1]
    assert all(count <= 1 for count in provider.stream_counts)


async def test_the_transcription_reaches_the_model(tmp_path) -> None:
    provider = RecordingProvider(limit=1)
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    await app.submit_user_message("what broke?", images=list(_IMAGES))
    await app.close()

    assert "vision.analysis.completed" in events


async def test_images_from_an_earlier_turn_do_not_stack_up(tmp_path) -> None:
    """One image per turn still put two in the same prompt: the history keeps them."""

    provider = RecordingProvider(limit=1)
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    first = await app.submit_user_message("first", images=[_IMAGES[0]])
    second = await app.submit_user_message("second", images=[_IMAGES[1]])
    await app.close()

    assert first.error is None and second.error is None
    assert provider.refusals == 0
    assert all(count <= 1 for count in provider.stream_counts)
    assert "images.trimmed" in events


async def test_a_refusal_teaches_the_limit_and_the_turn_survives(tmp_path) -> None:
    """Nothing configured: the cap is learned from the 400 and the turn retried."""

    provider = RecordingProvider(limit=1)
    app = build_application(config=_config(tmp_path, max_images_per_request=0), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("what broke?", images=list(_IMAGES))
    await app.close()

    assert result.error is None
    # Refused once, then fitted to the limit rather than stripped of every image.
    assert provider.refusals >= 1
    assert provider.stream_counts[-1] <= 1
    assert "images.trimmed" in events
    assert "images.dropped" not in events


async def test_no_limit_leaves_a_multimodal_endpoint_alone(tmp_path) -> None:
    provider = RecordingProvider(limit=99)
    app = build_application(config=_config(tmp_path, max_images_per_request=0), provider=provider)

    await app.start()
    await app.submit_user_message("what broke?", images=list(_IMAGES))
    await app.close()

    # All four travel together, in one request, exactly as before.
    assert provider.complete_counts == []
    assert provider.stream_counts[0] == 4
