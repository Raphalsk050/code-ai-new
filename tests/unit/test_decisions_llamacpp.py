"""The decision client against a bare llama.cpp server: no /v1/systemone, so the
option letters are read off the model's first token here."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest

from code_ai.decisions import OpenJevClient, OpenJevError, probe_decision_engine
from code_ai.decisions.openjev import choice, noul


def _server(logprobs: dict[str, float], *, has_systemone: bool = False):
    """A llama.cpp-shaped server answering every readout with ``logprobs``."""
    requests: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/v1/systemone":
            if not has_systemone:
                return httpx.Response(404, json={"error": {"message": "File Not Found"}})
            return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.7}}})
        top = [{"token": token, "logprob": value} for token, value in logprobs.items()]
        return httpx.Response(
            200,
            json={"choices": [{"logprobs": {"content": [{"token": "A", "top_logprobs": top}]}}]},
        )

    return httpx.MockTransport(handler), requests


_ROUTE = choice("Which team?", {"billing": "invoices", "ops": "servers", "sales": ""})


async def test_a_server_without_the_route_is_read_letter_by_letter() -> None:
    transport, requests = _server({"B": -0.1, "A": -3.0, "C": -5.0, "Hello": -6.0})
    client = OpenJevClient(base_url="http://llama.test", transport=transport)

    answers = await client.system_one("the server is down", {"route": _ROUTE})

    assert answers["route"]["choice"] == "ops"
    probabilities = answers["route"]["probabilities"]
    assert set(probabilities) == {"billing", "ops", "sales"}
    assert math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-3)
    # Temperature 0.85 over the three letters, as the model's own helper does.
    weights = [math.exp(v / 0.85) for v in (-3.0, -0.1, -5.0)]
    assert math.isclose(probabilities["ops"], weights[1] / sum(weights), abs_tol=1e-3)

    path, body = requests[1]
    assert path == "/v1/chat/completions"
    assert body["max_tokens"] == 1 and body["logprobs"] is True
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    # The wording the model was tuned on.
    assert body["messages"][0]["content"] == (
        "State:\nthe server is down\n\nQuestion: Which team?\nOptions:\n"
        "[A] billing: invoices\n[B] ops: servers\n[C] sales: \n\n"
        "Answer with the letter of the best option only."
    )


async def test_the_missing_route_is_asked_about_only_once() -> None:
    transport, requests = _server({"A": -0.2, "B": -2.0})
    client = OpenJevClient(base_url="http://llama.test/v1/", transport=transport)

    await client.system_one("s", {"a": noul("?"), "b": noul("?")})
    await client.system_one("s", {"a": noul("?")})

    paths = [path for path, _ in requests]
    assert paths.count("/v1/systemone") == 1
    assert paths.count("/v1/chat/completions") == 3
    assert client.api == "llamacpp"
    # A 404 is an answer about the server, not a failure of it.
    assert client.available is True


async def test_yes_no_is_calibrated_like_the_helper() -> None:
    transport, _ = _server({"A": -0.05, "B": -3.0})
    client = OpenJevClient(base_url="http://llama.test", api="llamacpp", transport=transport)

    answers = await client.system_one("s", {"q": noul("Is it?")})

    raw = [math.exp(v / 0.85) for v in (-0.05, -3.0)]
    yes = raw[0] / sum(raw)
    expected = 1 / (1 + math.exp(-math.log(yes / (1 - yes)) / 1.829074))
    assert math.isclose(answers["q"]["noul"], expected, abs_tol=1e-3)
    # Calibration pulls a near-certain raw yes back toward the middle.
    assert 0.5 < answers["q"]["noul"] < yes


async def test_the_exact_letter_beats_its_spaced_lookalike() -> None:
    transport, _ = _server({" A": -0.1, "A": -4.0, "B": -0.5})
    client = OpenJevClient(base_url="http://llama.test", api="llamacpp", transport=transport)

    answers = await client.system_one("s", {"q": choice("?", {"x": "", "y": ""})})

    assert answers["q"]["choice"] == "y"


async def test_a_model_that_does_not_answer_in_letters_is_an_error() -> None:
    transport, _ = _server({"<think>": -0.01, "The": -4.0})
    client = OpenJevClient(base_url="http://llama.test", api="llamacpp", transport=transport)

    with pytest.raises(OpenJevError, match="No option letter"):
        await client.system_one("s", {"q": noul("?")})


async def test_the_helper_route_is_used_when_it_exists() -> None:
    transport, requests = _server({}, has_systemone=True)
    client = OpenJevClient(base_url="http://helper.test", transport=transport)

    answers = await client.system_one("s", {"q": noul("?")})

    assert answers["q"]["noul"] == 0.7
    assert [path for path, _ in requests] == ["/v1/systemone"]
    assert client.api == "systemone"


async def test_the_probe_says_which_kind_of_server_answered() -> None:
    transport, _ = _server({"A": -0.1, "B": -2.5})

    result = await probe_decision_engine("http://llama.test", transport=transport)

    assert result["ok"] is True
    assert result["api"] == "llamacpp"
