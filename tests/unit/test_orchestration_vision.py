from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.errors import ProviderError
from code_ai.providers.models import (
    FinishReason,
    ImageContent,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)

_IMAGE = ImageContent(data="aGk=", media_type="image/png")
_ANALYSIS_TEXT = "[Image #1] A terminal showing a Python stack trace."


def _config(tmp_path, **overrides) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "main-model",
        "permission_mode": "bypass",
        "memories_dir": str(tmp_path / "memories"),
        "vision_model": "vision-model",
        # These tests exercise the vision hand-off, not planning; a disabled
        # planner keeps one-off planner model calls out of the recordings.
        "planner": {"enabled": False},
    }
    data.update(overrides)
    return AppConfig.from_mapping(data)


class VisionAwareProvider:
    """Records one-off complete() calls (vision) and stream() calls (turn)."""

    def __init__(self, *, vision_fails: bool = False) -> None:
        self.complete_requests: list[ModelRequest] = []
        self.stream_requests: list[ModelRequest] = []
        self.vision_fails = vision_fails

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.complete_requests.append(request)
        if self.vision_fails:
            raise ProviderError("vision model unavailable")
        return ModelResponse(text=_ANALYSIS_TEXT, finish_reason=FinishReason.STOP)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.stream_requests.append(request)
        yield ProviderEvent(kind="text_delta", text_delta="done")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def close(self) -> None:
        return None


async def test_vision_model_replaces_images_with_a_description(tmp_path) -> None:
    provider = VisionAwareProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("what broke? [Image #1]", images=[_IMAGE])
    await app.close()

    assert result.error is None
    # The vision model got the pixels, in a one-off call outside the turn.
    (vision_request,) = provider.complete_requests
    assert vision_request.model == "vision-model"
    assert vision_request.messages[-1].images == [_IMAGE]
    # The main model got the description as text and no image payloads at all.
    main_request = provider.stream_requests[0]
    assert all(not message.images for message in main_request.messages)
    analysis_messages = [
        message
        for message in main_request.messages
        if message.role == "user" and _ANALYSIS_TEXT in message.content
    ]
    assert analysis_messages, "vision description should reach the main model"
    assert "vision.analysis.started" in events
    assert "vision.analysis.completed" in events


async def test_vision_failure_falls_back_to_attaching_images(tmp_path) -> None:
    provider = VisionAwareProvider(vision_fails=True)
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("what broke? [Image #1]", images=[_IMAGE])
    await app.close()

    # The turn still runs; the images travel raw, exactly as without a
    # vision model (the provider may or may not be able to read them).
    assert result.error is None
    main_request = provider.stream_requests[0]
    user_messages = [m for m in main_request.messages if m.role == "user"]
    assert user_messages[-1].images == [_IMAGE]
    assert "vision.analysis.failed" in events


async def test_images_go_straight_to_a_multimodal_main_model(tmp_path) -> None:
    provider = VisionAwareProvider()
    app = build_application(config=_config(tmp_path, vision_model=""), provider=provider)

    await app.start()
    await app.submit_user_message("what broke? [Image #1]", images=[_IMAGE])
    await app.close()

    assert provider.complete_requests == []
    user_messages = [m for m in provider.stream_requests[0].messages if m.role == "user"]
    assert user_messages[-1].images == [_IMAGE]


async def test_vision_model_equal_to_main_model_is_a_no_op(tmp_path) -> None:
    # Pointing vision_model at the main model would just describe images to
    # the very model that can already see them; skip the extra call.
    provider = VisionAwareProvider()
    app = build_application(config=_config(tmp_path, vision_model="main-model"), provider=provider)

    await app.start()
    await app.submit_user_message("what broke? [Image #1]", images=[_IMAGE])
    await app.close()

    assert provider.complete_requests == []
    user_messages = [m for m in provider.stream_requests[0].messages if m.role == "user"]
    assert user_messages[-1].images == [_IMAGE]
