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


def test_sampling_gets_a_provider_built_with_it() -> None:
    # A provider reads sampling from its own config: on the session's client a
    # node's temperature would never reach the model.
    instance = executor()
    binding = ModelBinding(model="m", temperature=0.1, top_k=20)

    provider, overrides = instance._endpoint_for(binding)
    again, _ = instance._endpoint_for(ModelBinding(model="other", temperature=0.1, top_k=20))

    assert provider is not None and again is provider
    built = instance.built  # type: ignore[attr-defined]
    assert len(built) == 1
    assert built[0].sampling.temperature == pytest.approx(0.1)
    assert built[0].sampling.top_k == 20
    # Still the session endpoint: only the sampling is the node's own.
    assert built[0].base_url == "http://session.local/v1"
    assert "base_url" not in overrides
    assert overrides["sampling"].temperature == pytest.approx(0.1)


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


# -- the clean agent -------------------------------------------------------


class ChatStub:
    """A provider that answers once and remembers exactly what it was sent."""

    def __init__(self, reply: str = "oi") -> None:
        self.reply = reply
        self.requests: list = []

    async def complete(self, request):
        from code_ai.providers.models import ModelResponse, TokenUsage

        self.requests.append(request)
        return ModelResponse(
            text=self.reply,
            usage=TokenUsage(input_tokens=5, output_tokens=2, total_tokens=7),
        )


def clean_executor(chat: ChatStub) -> GraphNodeExecutor:
    from types import SimpleNamespace

    return GraphNodeExecutor(
        config=AppConfig.from_mapping({"api_mode": "ollama", "model": "session-model"}),
        runtime=SimpleNamespace(provider=chat),  # type: ignore[arg-type]
        profiles=graph_profile_registry(),
        event_bus=AsyncEventBus(),
        judge_call=None,  # type: ignore[arg-type]
        gateway=UserChatGateway(),
        run_id="run-1",
    )


async def test_a_clean_agent_sends_the_task_and_nothing_else() -> None:
    chat = ChatStub("tudo bem, e voce?")

    outcome = await clean_executor(chat).run_clean(
        node_id="n1",
        name="",
        system="",
        task="Ola, tudo bem?",
        model=None,
        timeout_s=0,
        cancel=asyncio.Event(),
    )

    assert outcome.ok and outcome.summary == "tudo bem, e voce?"
    assert outcome.usage == {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}
    request = chat.requests[0]
    assert [(message.role, message.content) for message in request.messages] == [
        ("user", "Ola, tudo bem?")
    ]
    assert request.tools == []
    assert request.model == "session-model"


async def test_a_clean_agent_sends_its_system_prompt_verbatim() -> None:
    chat = ChatStub()

    await clean_executor(chat).run_clean(
        node_id="n1",
        name="Goodall",
        system="Sempre termine com uma pergunta.",
        task="Oi",
        model=ModelBinding(model="gemma"),
        timeout_s=0,
        cancel=asyncio.Event(),
    )

    request = chat.requests[0]
    assert [(message.role, message.content) for message in request.messages] == [
        ("system", "Sempre termine com uma pergunta."),
        ("user", "Oi"),
    ]
    assert request.model == "gemma"


async def test_a_clean_agent_that_fails_reports_instead_of_raising() -> None:
    class Broken(ChatStub):
        async def complete(self, request):
            raise RuntimeError("endpoint down")

    outcome = await clean_executor(Broken()).run_clean(
        node_id="n1",
        name="",
        system="",
        task="Oi",
        model=None,
        timeout_s=0,
        cancel=asyncio.Event(),
    )

    assert outcome.status == "failed"
    assert "endpoint down" in outcome.error


def capture(executor: GraphNodeExecutor) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []

    async def keep(envelope) -> None:
        seen.append((envelope.event_type, envelope.payload))

    executor._bus.subscribe(keep)
    return seen


async def test_a_clean_agent_hands_its_answer_to_the_node_panel() -> None:
    executor = clean_executor(ChatStub("tudo certo"))
    seen = capture(executor)

    await executor.run_clean(
        node_id="n1", name="", system="", task="Oi", model=None, timeout_s=0, cancel=asyncio.Event()
    )

    outputs = [payload for kind, payload in seen if kind == "graph.node.output"]
    assert [(item["node_id"], item["channel"], item["text"]) for item in outputs] == [
        ("n1", "answer", "tudo certo")
    ]


async def test_an_agents_stream_reaches_the_node_in_bursts_and_in_order() -> None:
    from code_ai.app.graph_runner import _NodeStream

    executor = clean_executor(ChatStub())
    seen = capture(executor)
    stream = _NodeStream(executor._output_emitter("n1", "ada"))
    child = AsyncEventBus()
    child.subscribe(executor._forwarder("n1", "ada", "coder", stream))

    await child.emit("model.thinking.delta", {"text": "hmm "})
    await child.emit("model.thinking.delta", {"text": "ok"})
    await child.emit("model.stream.delta", {"text": "Vou ", "channel": "answer"})
    await child.emit("model.stream.delta", {"text": "ler.", "channel": "answer"})
    await child.emit("tool.call.started", {"name": "read_file"})
    await child.emit("model.stream.delta", {"text": "Pronto.", "channel": "answer"})
    await stream.close()

    shown = [
        (
            kind,
            payload.get("channel") or payload.get("event"),
            payload.get("text") or payload.get("detail"),
        )
        for kind, payload in seen
    ]
    assert shown == [
        ("graph.node.output", "thinking", "hmm ok"),
        ("graph.node.output", "answer", "Vou ler."),
        ("graph.node.progress", "tool.call.started", "read_file"),
        ("graph.node.output", "answer", "Pronto."),
    ]


async def test_a_stream_left_alone_flushes_on_its_own() -> None:
    from code_ai.app.graph_runner import _STREAM_FLUSH_S, _NodeStream

    sent: list[tuple[str, str]] = []

    async def emit(channel: str, text: str) -> None:
        sent.append((channel, text))

    stream = _NodeStream(emit)
    await stream.add("answer", "a")
    await stream.add("answer", "b")
    assert sent == []
    await asyncio.sleep(_STREAM_FLUSH_S * 3)
    assert sent == [("answer", "ab")]
    await stream.close()
    assert sent == [("answer", "ab")]


def test_a_model_node_sets_the_reasoning_effort_its_agents_think_at() -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "completions", "model": "qwen", "sampling": {"reasoning_effort": "xhigh"}}
    )
    instance = executor(config=config)

    provider, _ = instance._endpoint_for(ModelBinding(model="qwen", reasoning_effort="low"))
    off, _ = instance._endpoint_for(ModelBinding(model="qwen", reasoning_effort="none"))
    same, overrides = instance._endpoint_for(ModelBinding(model="qwen"))

    built = instance.built  # type: ignore[attr-defined]
    assert provider is not None and built[0].sampling.reasoning_effort == "low"
    # none is what the settings panel means by it: thinking off.
    assert off is not None and built[1].sampling.reasoning_effort == "none"
    assert same is None and overrides == {}
