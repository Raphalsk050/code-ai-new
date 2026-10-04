from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from code_ai.bootstrap import build_application
from code_ai.config import loader as config_loader
from code_ai.config.models import AppConfig, PlannerConfig
from code_ai.core.errors import ConfigurationError
from code_ai.core.planning import PlannerService
from code_ai.core.planning.models import (
    FinalAnswerQuery,
    FinalAnswerVerdict,
    TaskDecision,
    TaskSignals,
)
from code_ai.decisions import (
    OpenJevClient,
    OpenJevError,
    OpenJevFinalAnswerJudge,
    OpenJevTaskClassifier,
)
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)


def _client(
    nouls: dict[str, float] | None = None,
    *,
    choice: str = "conversation",
    p: float = 0.9,
    api_key: str = "",
):
    """A client whose server answers yes/no with ``nouls`` and every choice with ``choice``."""
    seen: list[dict[str, Any]] = []
    headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        headers.append(request.headers.get("authorization", ""))
        answers: dict[str, Any] = {}
        for question_id, question in body["questions"].items():
            if question["type"] == "noul":
                answers[question_id] = {
                    "type": "noul",
                    "noul": (nouls or {}).get(question_id, 0.0),
                }
            else:
                answers[question_id] = {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": {choice: p},
                    "confidence": p,
                }
        return httpx.Response(200, json={"answers": answers})

    client = OpenJevClient(
        base_url="http://jev.test", api_key=api_key, transport=httpx.MockTransport(handler)
    )
    client.sent_headers = headers  # type: ignore[attr-defined]
    return client, seen


def _planner(**kwargs: Any) -> PlannerService:
    return PlannerService(
        config=PlannerConfig(),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
        **kwargs,
    )


# -- client -------------------------------------------------------------------


async def test_client_sends_every_question_in_one_request() -> None:
    client, seen = _client({"a": 0.9})

    answers = await client.system_one(
        "state", {"a": {"type": "noul", "instructions": "?"}, "b": {"type": "noul"}}
    )

    assert answers["a"]["noul"] == 0.9
    assert len(seen) == 1
    assert seen[0]["state"] == "state"
    assert seen[0]["model"] == "openjev"
    assert set(seen[0]["questions"]) == {"a", "b"}
    assert client.sent_headers == [""]


async def test_client_sends_the_token_only_when_there_is_one() -> None:
    client, _ = _client(api_key="s3cret")

    await client.system_one("state", {"a": {"type": "noul", "instructions": "?"}})

    assert client.sent_headers == ["Bearer s3cret"]


async def test_a_failed_request_takes_the_engine_out_for_a_while() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused")

    client = OpenJevClient(
        base_url="http://jev.test", transport=httpx.MockTransport(handler), retry_after_s=60
    )

    with pytest.raises(OpenJevError):
        await client.system_one("state", {})
    assert client.available is False
    # Both adapters check availability first, so a server that is down costs one
    # failed request, not one per decision.
    assert await OpenJevTaskClassifier(client)("crie x.py") is None
    assert calls == 1


# -- task classifier ----------------------------------------------------------


async def test_the_message_is_read_in_one_question() -> None:
    client, seen = _client()

    await OpenJevTaskClassifier(client)("oi")

    # A request costs about the same whatever it asks, so it asks once.
    assert list(seen[0]["questions"]) == ["intent"]
    assert seen[0]["questions"]["intent"]["type"] == "choice"


async def test_confident_engine_overrides_the_keywords() -> None:
    # "bota um endpoint de health" has no mutation keyword: keywords read chat.
    client, _ = _client(choice="implementation")

    decision = await OpenJevTaskClassifier(client)("bota um endpoint de health no servidor")

    assert decision.signals.mutation is True
    assert decision.signals.local_reference is True
    assert decision.signals.bug_fix is False
    assert decision.continuation is False


async def test_confident_engine_clears_a_keyword_false_positive() -> None:
    client, _ = _client(choice="inspect_project")

    decision = await OpenJevTaskClassifier(client)("fix: por que isso acontece")

    assert TaskSignals.from_keywords("fix: por que isso acontece").mutation is True
    assert decision.signals.mutation is False
    assert decision.signals.local_reference is True


async def test_every_signal_comes_from_the_one_intent() -> None:
    async def read(intent: str) -> TaskSignals:
        client, _ = _client(choice=intent)
        return (await OpenJevTaskClassifier(client)("mensagem")).signals

    assert await read("bug_fix") == TaskSignals(
        mutation=True, local_reference=True, bug_fix=True
    )
    assert await read("run_command") == TaskSignals(command=True, local_reference=True)
    assert await read("web_research") == TaskSignals(
        explicit_web=True, external=True, current_info=True
    )
    assert await read("conversation") == TaskSignals()


async def test_unsure_engine_leaves_the_keyword_reading() -> None:
    client, _ = _client(choice="conversation", p=0.4)

    decision = await OpenJevTaskClassifier(client)("fix the login bug in auth.py")

    assert decision.signals == TaskSignals.from_keywords("fix the login bug in auth.py")
    assert decision.signals.mutation is True


async def test_engine_recognises_a_continuation_the_vocabulary_misses() -> None:
    client, _ = _client(choice="continue")
    assert (
        await OpenJevTaskClassifier(client)("termina o que voce estava fazendo")
    ).continuation is True

    # Not sure enough: a new request must never be swallowed as a "continue".
    client, _ = _client(choice="continue", p=0.5)
    assert (await OpenJevTaskClassifier(client)("segue e cria o teste")).continuation is False


async def test_planner_builds_the_profile_from_the_engine() -> None:
    async def classifier(text: str) -> TaskDecision:
        return TaskDecision(signals=TaskSignals(mutation=True, bug_fix=True))

    events: list[dict[str, Any]] = []
    service = _planner(task_classifier=classifier)
    service.event_bus.subscribe(
        lambda event: events.append(event.payload)
        if event.event_type == "planning.task.classified"
        else None
    )

    await service.begin_turn("o login quebrou de novo", provider_supports_tools=True)

    assert service.profile.intent == "bug_fix"
    assert service.profile.requires_workspace_mutation is True
    assert events[0]["decided_by"] == "engine"


async def test_planner_falls_back_to_keywords_when_the_engine_fails() -> None:
    async def classifier(text: str) -> TaskDecision:
        raise OpenJevError("down")

    service = _planner(task_classifier=classifier)

    await service.begin_turn("Fix the authentication bug", provider_supports_tools=True)

    assert service.profile.intent == "bug_fix"


async def test_engine_continuation_keeps_the_live_task() -> None:
    decisions = iter(
        [
            TaskDecision(signals=TaskSignals(mutation=True)),
            TaskDecision(signals=TaskSignals(), continuation=True),
        ]
    )

    async def classifier(text: str) -> TaskDecision:
        return next(decisions)

    service = _planner(task_classifier=classifier)
    await service.begin_turn("implemente o health check", provider_supports_tools=True)
    plan_id = service.plan.plan_id

    await service.begin_turn("termina isso ai", provider_supports_tools=True)

    assert service.plan.plan_id == plan_id
    assert service.profile.requires_workspace_mutation is True


# -- final-answer judge -------------------------------------------------------


def _query(answer: str = "Vou criar o arquivo agora.", *, changed: bool = False):
    return FinalAnswerQuery(
        objective="crie hello.py", actions="", answer=answer, changed_files=changed
    )


@pytest.mark.parametrize(
    ("choice", "p", "expected"),
    [
        ("done", 0.9, FinalAnswerVerdict.DONE),
        ("announced", 0.9, FinalAnswerVerdict.ANNOUNCED),
        ("unapplied", 0.85, FinalAnswerVerdict.UNAPPLIED),
        ("incomplete", 0.8, FinalAnswerVerdict.INCOMPLETE),
        # Not sure enough to cost a round trip: the answer stands.
        ("announced", 0.6, FinalAnswerVerdict.DONE),
        ("incomplete", 0.79, FinalAnswerVerdict.DONE),
        # A question to the user is theirs to answer.
        ("asks_user", 0.5, FinalAnswerVerdict.ASKS_USER),
    ],
)
async def test_judge_verdicts(choice: str, p: float, expected: FinalAnswerVerdict) -> None:
    client, seen = _client(choice=choice, p=p)

    assert await OpenJevFinalAnswerJudge(client)(_query()) == expected
    # One question, one pass.
    assert list(seen[0]["questions"]) == ["verdict"]


async def test_judge_does_not_offer_unapplied_once_files_changed() -> None:
    client, seen = _client(choice="done")

    await OpenJevFinalAnswerJudge(client)(_query(changed=True))
    await OpenJevFinalAnswerJudge(client)(_query(changed=False))

    assert "unapplied" not in seen[0]["questions"]["verdict"]["criteria"]
    assert "unapplied" in seen[1]["questions"]["verdict"]["criteria"]
    assert "Files changed: yes" in seen[0]["state"]


async def test_planner_nudges_an_unfinished_answer_once_per_turn() -> None:
    queries: list[FinalAnswerQuery] = []

    async def judge(query: FinalAnswerQuery) -> FinalAnswerVerdict:
        queries.append(query)
        return FinalAnswerVerdict.ANNOUNCED

    service = _planner(final_answer_judge=judge)
    await service.begin_turn("crie hello.py que imprime ola", provider_supports_tools=True)

    first = await service.note_final_answer_gap("Vou criar o arquivo.", tool_calls_executed=0)
    second = await service.note_final_answer_gap("Vou criar o arquivo.", tool_calls_executed=0)

    assert "before you did it" in first
    assert "crie hello.py que imprime ola" in first
    assert second is None
    assert len(queries) == 1
    assert queries[0].changed_files is False

    # The budget is per turn.
    await service.begin_turn("crie outro.py", provider_supports_tools=True)
    assert await service.note_final_answer_gap("Vou criar.", tool_calls_executed=0)


async def test_planner_lets_the_answer_stand_when_it_should() -> None:
    calls = 0

    async def judge(query: FinalAnswerQuery) -> FinalAnswerVerdict:
        nonlocal calls
        calls += 1
        return FinalAnswerVerdict.DONE

    service = _planner(final_answer_judge=judge)

    # Small talk is never judged.
    await service.begin_turn("Olá", provider_supports_tools=True)
    assert await service.note_final_answer_gap("Olá!", tool_calls_executed=0) is None
    assert calls == 0

    await service.begin_turn("crie hello.py", provider_supports_tools=True)
    assert await service.note_final_answer_gap("Criado.", tool_calls_executed=1) is None
    assert calls == 1


async def test_planner_survives_a_judge_that_fails() -> None:
    async def judge(query: FinalAnswerQuery) -> FinalAnswerVerdict:
        raise OpenJevError("down")

    service = _planner(final_answer_judge=judge)
    await service.begin_turn("crie hello.py", provider_supports_tools=True)

    assert await service.note_final_answer_gap("Vou criar.", tool_calls_executed=0) is None


# -- the whole turn -----------------------------------------------------------


class AnnouncesThenWritesProvider:
    """Says it will write the file and stops; writes it once told to."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            response = ModelResponse(
                text="Vou criar o notes.md agora.", finish_reason=FinishReason.STOP
            )
        elif self.calls == 2:
            response = ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="w1",
                        name="write_file",
                        arguments={"path": "notes.md", "content": "ola\n"},
                    )
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            )
        else:
            response = ModelResponse(text="Criei notes.md.", finish_reason=FinishReason.STOP)
        yield ProviderEvent(kind="completed", response=response)


async def test_a_turn_that_only_announces_is_sent_back_to_do_the_work(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
            "memory": {"reflection_enabled": False},
        }
    )
    provider = AnnouncesThenWritesProvider()
    app = build_application(config=config, provider=provider)

    async def judge(query: FinalAnswerQuery) -> FinalAnswerVerdict:
        return (
            FinalAnswerVerdict.DONE if query.changed_files else FinalAnswerVerdict.ANNOUNCED
        )

    app.orchestrator.planner._final_answer_judge = judge

    await app.start()
    result = await app.submit_user_message("crie notes.md com ola")
    await app.close()

    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "ola\n"
    assert provider.calls == 3
    assert result.text == "Criei notes.md."


# -- config -------------------------------------------------------------------


def test_engine_is_off_by_default_and_validated(tmp_path) -> None:
    base = {"workspace": str(tmp_path)}

    assert AppConfig.from_mapping(base).decisions.enabled is False
    assert AppConfig.from_mapping(
        {**base, "decisions": {"engine": "OpenJev"}}
    ).decisions.enabled
    with pytest.raises(ConfigurationError):
        AppConfig.from_mapping({**base, "decisions": {"engine": "other"}})
    with pytest.raises(ConfigurationError):
        AppConfig.from_mapping({**base, "decisions": {"confidence": 0.3}})


# -- settings -----------------------------------------------------------------


async def test_settings_turn_the_engine_on_and_off_live(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config_loader, "default_config_path", lambda: tmp_path / "config.json")
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "memories_dir": str(tmp_path / "memories"),
        }
    )
    app = build_application(config=config, provider=AnnouncesThenWritesProvider())
    planner = app.orchestrator.planner
    assert app.get_settings()["jev_enabled"] is False
    assert planner._task_classifier is None

    result = await app.update_settings(
        {"jev_enabled": True, "jev_base_url": "http://10.0.0.5:8771"}
    )

    assert result["errors"] == {}
    assert result["settings"]["jev_enabled"] is True
    assert result["settings"]["jev_base_url"] == "http://10.0.0.5:8771"
    assert result["settings"]["jev_api_key_set"] is False
    assert planner._task_classifier is not None
    assert planner._final_answer_judge is not None

    result = await app.update_settings({"jev_enabled": False})

    assert planner._task_classifier is None
    assert result["settings"]["jev_base_url"] == "http://10.0.0.5:8771"

    refused = await app.update_settings({"jev_enabled": True, "jev_base_url": "nope"})
    assert "jev_base_url" in refused["errors"]

    # The token is write-only, and a blank one keeps what is stored.
    result = await app.update_settings({"jev_api_key": "s3cret"})
    assert result["settings"]["jev_api_key_set"] is True
    assert "s3cret" not in json.dumps(result["settings"])
    await app.update_settings({"jev_api_key": ""})
    assert app.session.config.decisions.api_key == "s3cret"


async def test_probe_reports_latency_or_the_error() -> None:
    from code_ai.decisions import probe_decision_engine

    def answers(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answers": {"probe": {"type": "noul", "noul": 0.97}}})

    def refuses(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    ok = await probe_decision_engine("http://jev.test", transport=httpx.MockTransport(answers))
    down = await probe_decision_engine("http://jev.test", transport=httpx.MockTransport(refuses))

    assert ok["ok"] is True and ok["probability"] == 0.97
    assert down["ok"] is False and "refused" in down["error"]


async def test_unfinished_verdicts_share_one_threshold() -> None:
    # Measured on the real model: "did part, will do the rest" splits between
    # announced and incomplete. Neither clears the bar alone; together they do.
    def handler(request: httpx.Request) -> httpx.Response:
        verdict = {
            "type": "choice",
            "choice": "announced",
            "probabilities": {"announced": 0.6, "incomplete": 0.39, "done": 0.01},
        }
        return httpx.Response(200, json={"answers": {"verdict": verdict}})

    client = OpenJevClient(base_url="http://jev.test", transport=httpx.MockTransport(handler))

    assert await OpenJevFinalAnswerJudge(client)(_query()) == FinalAnswerVerdict.ANNOUNCED
