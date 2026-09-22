"""The half of the graph runtime that touches providers, keys and humans."""

from __future__ import annotations

import asyncio

import pytest

from code_ai.app.graph_runner import (
    GraphNodeExecutor,
    UserChatGateway,
    _reads_as_yes,
)
from code_ai.config.models import AppConfig
from code_ai.core.graph.professions import graph_profile_registry
from code_ai.core.graph.runtime import ModelBinding
from code_ai.events.bus import AsyncEventBus


class StubProvider:
    def __init__(self, config: AppConfig) -> None:
        self.config = config


def executor(**kwargs) -> GraphNodeExecutor:
    config = kwargs.pop(
        "config",
        AppConfig.from_mapping(
            {
                "api_mode": "ollama",
                "base_url": "http://session.local/v1",
                "api_key": "session-key",
                "model": "session-model",
            }
        ),
    )
    built: list[AppConfig] = []

    def factory(candidate: AppConfig):
        built.append(candidate)
        return StubProvider(candidate)

    instance = GraphNodeExecutor(
        config=config,
        runtime=None,
        profiles=graph_profile_registry(),
        event_bus=AsyncEventBus(),
        judge_call=kwargs.pop("judge_call", None),
        gateway=kwargs.pop("gateway", UserChatGateway()),
        run_id="run-1",
        provider_factory=factory,
        **kwargs,
    )
    instance.built = built  # type: ignore[attr-defined]
    return instance


# -- endpoints -------------------------------------------------------------


def test_a_node_with_only_a_model_name_stays_on_the_session_provider() -> None:
    instance = executor()

    provider, overrides = instance._endpoint_for(ModelBinding(model="other-model"))

    assert provider is None
    assert overrides == {}
    assert instance.built == []  # type: ignore[attr-defined]


def test_no_model_node_at_all_stays_on_the_session_provider() -> None:
    instance = executor()

    provider, overrides = instance._endpoint_for(None)

    assert provider is None
    assert overrides == {}


def test_a_node_with_its_own_base_url_gets_its_own_provider() -> None:
    instance = executor()

    provider, overrides = instance._endpoint_for(
        ModelBinding(model="remote", base_url="http://other.local/v1", api_mode="completions")
    )

    assert provider is not None
    assert overrides["base_url"] == "http://other.local/v1"
    assert overrides["api_mode"] == "completions"
    # The session key travels with it: same user, different endpoint.
    assert overrides["api_key"] == "session-key"


def test_two_nodes_on_one_endpoint_share_a_provider() -> None:
    instance = executor()
    binding = ModelBinding(model="a", base_url="http://shared.local/v1")

    first, _ = instance._endpoint_for(binding)
    second, _ = instance._endpoint_for(ModelBinding(model="b", base_url="http://shared.local/v1"))

    assert first is second
    assert len(instance.built) == 1  # type: ignore[attr-defined]


def test_different_endpoints_get_different_providers() -> None:
    instance = executor()

    first, _ = instance._endpoint_for(ModelBinding(base_url="http://one.local/v1"))
    second, _ = instance._endpoint_for(ModelBinding(base_url="http://two.local/v1"))

    assert first is not second
    assert len(instance.built) == 2  # type: ignore[attr-defined]


def test_sampling_overrides_ride_along_without_a_new_provider() -> None:
    instance = executor()

    provider, overrides = instance._endpoint_for(ModelBinding(model="m", temperature=0.1, top_k=20))

    assert provider is None
    assert overrides["sampling"].temperature == pytest.approx(0.1)
    assert overrides["sampling"].top_k == 20


def test_an_env_key_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("CODE_AI_GRAPH_TEST_KEY", "from-env")
    instance = executor()

    _, overrides = instance._endpoint_for(
        ModelBinding(base_url="http://x.local/v1", api_key_ref="env:CODE_AI_GRAPH_TEST_KEY")
    )

    assert overrides["api_key"] == "from-env"


def test_a_missing_env_key_is_refused_with_the_variable_name(monkeypatch) -> None:
    monkeypatch.delenv("CODE_AI_GRAPH_TEST_KEY", raising=False)
    instance = executor()

    with pytest.raises(ValueError, match="CODE_AI_GRAPH_TEST_KEY"):
        instance._endpoint_for(
            ModelBinding(base_url="http://x.local/v1", api_key_ref="env:CODE_AI_GRAPH_TEST_KEY")
        )


def test_a_stored_secret_is_taken_from_the_run() -> None:
    instance = executor(secrets={"work": "shh"})

    _, overrides = instance._endpoint_for(
        ModelBinding(base_url="http://x.local/v1", api_key_ref="secret:work")
    )

    assert overrides["api_key"] == "shh"


def test_an_unsupplied_secret_is_refused() -> None:
    instance = executor()

    with pytest.raises(ValueError, match="No stored secret"):
        instance._endpoint_for(
            ModelBinding(base_url="http://x.local/v1", api_key_ref="secret:missing")
        )


def test_an_unsupported_key_reference_is_refused() -> None:
    instance = executor()

    with pytest.raises(ValueError, match="Unsupported API key reference"):
        instance._endpoint_for(
            ModelBinding(base_url="http://x.local/v1", api_key_ref="sk-literal")
        )


# -- the unknown profession ------------------------------------------------


async def test_an_unknown_profession_is_rejected_not_raised() -> None:
    instance = executor()

    outcome = await instance.run_agent(
        node_id="n1",
        profession="wizard",
        task="magic",
        extra_instructions="",
        model=None,
        max_steps=0,
        timeout_s=0,
        cancel=asyncio.Event(),
    )

    assert outcome.status == "rejected"
    assert "Unknown profession" in outcome.error


# -- the judge -------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("YES", True),
        ("yes", True),
        ("Yes.", True),
        ("**YES**", True),
        ("sim", True),
        ("NO", False),
        ("no, the tests still fail", False),
        ("", False),
        ("I think yes", False),
        ("maybe", False),
    ],
)
def test_a_verdict_is_read_from_the_first_word_only(answer: str, expected: bool) -> None:
    assert _reads_as_yes(answer) is expected


async def test_a_judge_that_fails_takes_the_false_branch() -> None:
    async def broken(system: str, user: str, model) -> str:
        raise RuntimeError("endpoint down")

    instance = executor(judge_call=broken)

    verdict = await instance.judge(
        node_id="n1", question="ok?", context="", model=None, cancel=asyncio.Event()
    )

    assert verdict is False


async def test_the_judge_is_given_the_question_and_the_context() -> None:
    seen: dict[str, str] = {}

    async def judge(system: str, user: str, model) -> str:
        seen["system"] = system
        seen["user"] = user
        return "yes"

    instance = executor(judge_call=judge)

    assert await instance.judge(
        node_id="n1",
        question="are the tests green?",
        context="the suite passed",
        model=None,
        cancel=asyncio.Event(),
    )
    assert "YES or NO" in seen["system"]
    assert "are the tests green?" in seen["user"]
    assert "the suite passed" in seen["user"]


# -- the user-chat gateway -------------------------------------------------


async def test_the_gateway_hands_the_answer_to_the_waiting_node() -> None:
    gateway = UserChatGateway()
    future = gateway.open("c1")

    assert gateway.pending == ("c1",)
    assert gateway.resolve("c1", "the importer") is True
    assert await future == "the importer"


async def test_answering_an_unknown_question_says_so() -> None:
    gateway = UserChatGateway()

    assert gateway.resolve("nope", "hi") is False


async def test_closing_the_gateway_unblocks_everything_waiting() -> None:
    gateway = UserChatGateway()
    first = gateway.open("c1")
    second = gateway.open("c2")

    gateway.close()

    assert await first == ""
    assert await second == ""
    assert gateway.pending == ()


async def test_a_second_question_with_the_same_id_releases_the_first() -> None:
    gateway = UserChatGateway()
    first = gateway.open("c1")
    second = gateway.open("c1")

    assert await first == ""
    assert gateway.resolve("c1", "answer") is True
    assert await second == "answer"
