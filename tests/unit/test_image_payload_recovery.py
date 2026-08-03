from __future__ import annotations

from code_ai.providers.models import ImageContent, Message, ModelRequest
from code_ai.providers.openai_completions import _worth_retrying


class _Refused(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


def _request(*, with_image: bool) -> ModelRequest:
    image = [ImageContent(data="AAAA", media_type="image/png")] if with_image else []
    return ModelRequest(
        model="m", messages=[Message(role="user", content="hi", images=image)]
    )


def test_a_refused_image_payload_is_not_retried() -> None:
    """The payload does not change between attempts, so neither does the answer."""

    assert _worth_retrying(_Refused(500), _request(with_image=True)) is False


def test_a_plain_500_still_retries() -> None:
    assert _worth_retrying(_Refused(500), _request(with_image=False)) is True


def test_overload_retries_even_with_an_image() -> None:
    """429/503 are about the moment, not about what was sent."""

    for status in (408, 429, 503, 504):
        assert _worth_retrying(_Refused(status), _request(with_image=True)) is True


def test_a_real_error_is_never_retried() -> None:
    assert _worth_retrying(_Refused(400), _request(with_image=False)) is False
    assert _worth_retrying(ValueError("nope"), _request(with_image=False)) is False
