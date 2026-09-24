"""A graph run, end to end through the real application.

No fakes below the provider: the graph is compiled by the real compiler, hires
real sub-agents through the real :class:`SubagentRuntime`, and the events come
off the real bus. Only the model is scripted, which is the one thing a test
cannot have.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)


class ScriptedProvider:
    """Answers every turn with prose, remembering what it was asked.

    ``replies`` is keyed by a substring of the task: the first key found in the
    prompt wins, so a test can give the architect and the writer different
    answers without knowing the order they run in.
    """

    def __init__(self, replies: dict[str, str] | None = None, default: str = "done") -> None:
        self.replies = replies or {}
        self.default = default
        self.requests: list[ModelRequest] = []
        self.models: list[str] = []
        self.base_urls: list[str] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    def _answer(self, request: ModelRequest) -> str:
        text = "\n".join(str(message.content or "") for message in request.messages)
        for needle, reply in self.replies.items():
            if needle in text:
                return reply
        return self.default

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        self.models.append(request.model)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=self._answer(request), finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        self.models.append(request.model)
        return ModelResponse(text=self._answer(request), finish_reason=FinishReason.STOP)

    async def close(self) -> None:
        return None


def app_for(tmp_path, provider, **settings):
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "main-model",
            "permission_mode": "bypass",
            "learn": False,
            **settings,
        }
    )
    return build_application(config=config, provider=provider)


def node(node_id: str, node_kind: str, **data: Any) -> dict[str, Any]:
    return {"id": node_id, "kind": node_kind, "pos": [0, 0], "data": data}


def wire(edge_id: str, source: tuple[str, str], target: tuple[str, str], kind: str = "exec"):
    return {"id": edge_id, "kind": kind, "from": list(source), "to": list(target)}


def graph(nodes: list[dict], edges: list[dict], **rest: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "name": "integration",
        "variables": [],
        "nodes": nodes,
        "edges": edges,
        "view": {"pan": [0, 0], "zoom": 1},
    }
    payload.update(rest)
    return payload


class Collector:
    """Every event the session emitted, in order."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, envelope) -> None:
        self.rows.append((envelope.event_type, envelope.payload))

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.rows if name == event_type]

    def graph_events(self) -> list[str]:
        return [name for name, _ in self.rows if name.startswith("graph.")]


async def run_graph(app, doc: dict[str, Any], **kwargs) -> dict[str, Any]:
    started = await app.start_graph_run(doc, **kwargs)
    assert started["ok"], started
    task = app._graph_task
    assert task is not None
    await task
    return started


# -- the catalog -----------------------------------------------------------


def test_catalog_carries_kinds_professions_and_run_limits(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    catalog = app.graph_catalog()

    kinds = {row["kind"] for row in catalog["node_kinds"]}
    assert {"start", "model", "profession", "loop", "decision", "user_chat"} <= kinds
    assert catalog["limits"]["run"]["max_loop_iterations"] == 50
    assert any(row["name"] == "architect" for row in catalog["professions"])


def test_validate_reports_issues_without_running(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    broken = graph([node("n1", "profession", profession="architect")], [])

    result = app.validate_graph(broken)

    assert result["ok"] is False
    messages = [item["message"] for item in result["issues"]]
    assert any("no Start node" in message for message in messages)
    assert app.graph_run_active() is False


# -- a real run ------------------------------------------------------------


async def test_a_graph_hires_a_real_agent_and_prints_its_report(tmp_path) -> None:
    provider = ScriptedProvider(replies={"design the importer": "three modules"})
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("agent", "profession", profession="architect", task="design the importer"),
            node("show", "print", label="design"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("show", "in")),
            wire("e3", ("agent", "report"), ("show", "value"), "data"),
            wire("e4", ("show", "out"), ("end", "in")),
        ],
    )
    await run_graph(app, doc)
    await app.close()

    assert collector.of("graph.run.started")
    completed = collector.of("graph.run.completed")[0]
    assert completed["status"] == "completed"
    assert collector.of("graph.print")[0]["text"] == "three modules"
    # The agent really ran: an isolated turn on the session model.
    started = collector.of("graph.agent.started")[0]
    assert started["profession"] == "architect"
    assert started["writes"] is False
    assert provider.models == ["main-model"]


async def test_node_progress_surfaces_the_agent_event_stream(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("agent", "profession", profession="teacher", task="explain the loop"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("end", "in")),
        ],
    )
    await run_graph(app, doc)
    await app.close()

    progress = collector.of("graph.node.progress")
    assert progress, collector.graph_events()
    assert progress[0]["node_id"] == "agent"
    assert progress[0]["event"] == "model.response.completed"


async def test_a_model_node_picks_the_model_the_agent_runs_on(tmp_path) -> None:
    provider = ScriptedProvider()
    app = app_for(tmp_path, provider)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("model", "model", model="graph-model"),
            node("agent", "profession", profession="explorer", task="find the entry point"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("model", "model"), ("agent", "model"), "data"),
            wire("e3", ("agent", "out"), ("end", "in")),
        ],
    )
    await run_graph(app, doc)
    await app.close()

    assert "graph-model" in provider.models


async def test_a_missing_env_key_fails_the_node_instead_of_falling_back(tmp_path) -> None:
    """A node pointing at another endpoint must never run on the session one."""

    provider = ScriptedProvider()
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node(
                "model",
                "model",
                model="remote-model",
                base_url="http://other.local:9999/v1",
                api_mode="completions",
                api_key_ref="env:CODE_AI_TEST_GRAPH_KEY_THAT_IS_UNSET",
            ),
            node("agent", "profession", profession="explorer", task="look"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("model", "model"), ("agent", "model"), "data"),
            wire("e3", ("agent", "out"), ("end", "in")),
        ],
    )
    await run_graph(app, doc)
    await app.close()

    completed = collector.of("graph.run.completed")[0]
    assert completed["status"] == "failed"
    assert "is not set" in completed["error"]
    # Nothing reached a model: the session client was never used as a fallback.
    assert provider.models == []


async def test_a_judge_decision_asks_the_model_and_takes_a_branch(tmp_path) -> None:
    provider = ScriptedProvider(
        replies={"Question: is it done?": "YES", "review it": "looks fine"}
    )
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("agent", "profession", profession="reviewer", task="review it"),
            node("judge", "decision", mode="judge", prompt="is it done?"),
            node("yes", "print", label="verdict", value="shipped"),
            node("no", "print", label="verdict", value="more work"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("judge", "in")),
            wire("e3", ("judge", "true"), ("yes", "in")),
            wire("e4", ("judge", "false"), ("no", "in")),
        ],
    )
    await run_graph(app, doc)
    await app.close()

    assert collector.of("graph.print")[0]["text"] == "shipped"
    assert collector.of("graph.decision")[0]["verdict"] is True


async def test_the_user_chat_node_parks_until_the_answer_arrives(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("ask", "user_chat", title="which one?", prompt="which module?"),
            node("show", "print", label="answer"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("ask", "in")),
            wire("e2", ("ask", "out"), ("show", "in")),
            wire("e3", ("ask", "answer"), ("show", "value"), "data"),
            wire("e4", ("show", "out"), ("end", "in")),
        ],
    )
    started = await app.start_graph_run(doc)
    assert started["ok"]

    # The run is parked: the question is on the wire and nothing has printed.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if collector.of("graph.user_chat.requested"):
            break
    question = collector.of("graph.user_chat.requested")[0]
    assert question["prompt"] == "which module?"
    assert app.graph_snapshot()["pending_questions"] == [question["call_id"]]
    assert collector.of("graph.print") == []

    answered = app.answer_graph_question(started["run_id"], question["call_id"], "the importer")
    assert answered["ok"]
    await app._graph_task
    await app.close()

    assert collector.of("graph.print")[0]["text"] == "the importer"


async def test_the_status_says_what_is_running_so_a_reopened_tab_can_rejoin(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    assert app.graph_snapshot() == {"running": False}

    doc = graph(
        [
            node("start", "start"),
            node("ask", "user_chat", prompt="esperando"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("ask", "in")),
            wire("e2", ("ask", "out"), ("end", "in")),
        ],
    )
    started = await app.start_graph_run(doc)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if collector.of("graph.user_chat.requested"):
            break

    snapshot = app.graph_snapshot()
    assert snapshot["running"] is True
    assert snapshot["run_id"] == started["run_id"]
    assert snapshot["pending_questions"] == [
        collector.of("graph.user_chat.requested")[0]["call_id"]
    ]

    await app.stop_graph_run()
    await app._graph_task
    await app.close()

    assert app.graph_snapshot()["running"] is False


async def test_stopping_a_run_ends_it_and_says_so(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("ask", "user_chat", prompt="waiting for you"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("ask", "in")),
            wire("e2", ("ask", "out"), ("end", "in")),
        ],
    )
    started = await app.start_graph_run(doc)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if collector.of("graph.user_chat.requested"):
            break

    stopped = await app.stop_graph_run(started["run_id"])
    assert stopped["ok"]
    await app._graph_task
    await app.close()

    assert collector.of("graph.run.completed")[0]["status"] == "stopped"


async def test_cancelling_the_turn_also_stops_the_graph(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("ask", "user_chat", prompt="waiting"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("ask", "in")),
            wire("e2", ("ask", "out"), ("end", "in")),
        ],
    )
    await app.start_graph_run(doc)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if collector.of("graph.user_chat.requested"):
            break

    await app.cancel_current_turn()
    await app._graph_task
    await app.close()

    assert collector.of("graph.run.completed")[0]["status"] == "stopped"


async def test_two_runs_at_once_are_refused(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("ask", "user_chat", prompt="waiting"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("ask", "in")),
            wire("e2", ("ask", "out"), ("end", "in")),
        ],
    )
    first = await app.start_graph_run(doc)
    second = await app.start_graph_run(doc)

    assert first["ok"] is True
    assert second["ok"] is False
    assert "already running" in second["message"]

    await app.stop_graph_run()
    await app._graph_task
    await app.close()


async def test_a_run_refuses_to_start_while_a_message_is_being_answered(tmp_path) -> None:
    """The graph and the chat share one turn slot, so one waits for the other."""

    release = asyncio.Event()

    class SlowProvider(ScriptedProvider):
        async def stream(self, request: ModelRequest):
            await release.wait()
            async for event in super().stream(request):
                yield event

    app = app_for(tmp_path, SlowProvider())
    await app.start()

    turn = asyncio.ensure_future(app.submit_user_message("faca algo"))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if app._current_task is not None and not app._current_task.done():
            break

    doc = graph([node("start", "start"), node("end", "end")],
                [wire("e1", ("start", "out"), ("end", "in"))])
    refused = await app.start_graph_run(doc)

    assert refused["ok"] is False
    assert "working on a message" in refused["message"]

    release.set()
    await turn
    await app.close()


async def test_a_graph_with_errors_never_starts(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("agent", "profession", profession="wizard", task="magic"),
        ],
        [wire("e1", ("start", "out"), ("agent", "in"))],
    )
    result = await app.start_graph_run(doc)
    await app.close()

    assert result["ok"] is False
    assert any("Unknown profession" in item["message"] for item in result["issues"])
    assert collector.of("graph.validation.failed")


async def test_a_loop_of_agents_runs_every_lap(tmp_path) -> None:
    provider = ScriptedProvider(replies={"write the next part": "wrote a part"})
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("loop", "loop", kind="count", count=3),
            node("writer", "profession", profession="code_writer", task="write the next part"),
            node("done", "print", label="done", value="finished"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("writer", "in")),
            wire("e3", ("loop", "completed"), ("done", "in")),
        ],
    )
    await run_graph(app, doc)
    await app.close()

    assert len(collector.of("graph.agent.started")) == 3
    assert collector.of("graph.print")[0]["text"] == "finished"
    assert collector.of("graph.run.completed")[0]["status"] == "completed"


async def test_variables_seeded_from_the_toolbar_reach_the_agent(tmp_path) -> None:
    provider = ScriptedProvider()
    app = app_for(tmp_path, provider)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("brief", "var_get", variable="brief"),
            node("agent", "profession", profession="documenter"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("brief", "value"), ("agent", "task"), "data"),
            wire("e3", ("agent", "out"), ("end", "in")),
        ],
        variables=[{"id": "v1", "name": "brief", "type": "string", "default": "document it"}],
    )
    await run_graph(app, doc, variables={"brief": "write the release notes"})
    await app.close()

    prompts = "\n".join(
        str(message.content or "")
        for request in provider.requests
        for message in request.messages
    )
    assert "write the release notes" in prompts


async def test_the_graph_run_is_stopped_when_the_app_closes(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("ask", "user_chat", prompt="waiting"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("ask", "in")),
            wire("e2", ("ask", "out"), ("end", "in")),
        ],
    )
    await app.start_graph_run(doc)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if collector.of("graph.user_chat.requested"):
            break

    await app.close()

    assert app.graph_run_active() is False


# -- what an agent may touch -----------------------------------------------


def _offered(provider: ScriptedProvider) -> set[str]:
    """The tool names the last request put in front of the model."""

    return {tool.name for tool in provider.requests[-1].tools or []}


async def _hire(tmp_path, agent: dict[str, Any], extra_nodes=(), extra_edges=()):
    provider = ScriptedProvider()
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()
    doc = graph(
        [node("start", "start"), agent, node("end", "end"), *extra_nodes],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("end", "in")),
            *extra_edges,
        ],
    )
    await run_graph(app, doc)
    await app.close()
    assert collector.of("graph.run.completed")[0]["status"] == "completed"
    return provider, collector.of("graph.agent.started")[0]


async def test_the_catalog_lists_only_tools_an_agent_can_be_given(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    names = {row["name"] for row in app.graph_catalog()["tools"]}

    assert {"read_file", "write_file", "execute_command", "web_search"} <= names
    assert not names & {"start_terminal", "ask_user", "remember", "complete_task", "load_tools"}


async def test_wired_tools_are_exactly_what_the_agent_gets(tmp_path) -> None:
    provider, started = await _hire(
        tmp_path,
        node("agent", "profession", profession="code_writer", task="t", name="Scout"),
        extra_nodes=[
            node("kit", "tools", tools=["read_file", "list_files"]),
            node("web", "tools", tools=["web_search", "start_terminal"]),
        ],
        extra_edges=[
            wire("e3", ("kit", "tools"), ("agent", "tools"), "data"),
            wire("e4", ("web", "tools"), ("agent", "tools"), "data"),
        ],
    )

    assert _offered(provider) == {"read_file", "list_files", "web_search"}
    assert set(started["tools"]) == {"read_file", "list_files", "web_search"}
    assert started["profession"] == "Scout"


async def test_switches_override_the_profession_and_blocked_tools_go(tmp_path) -> None:
    provider, started = await _hire(
        tmp_path,
        node(
            "agent",
            "profession",
            profession="architect",
            task="t",
            can_write=True,
            can_browse=False,
            blocked_tools=["edit_code"],
        ),
    )

    offered = _offered(provider)
    assert "write_file" in offered
    assert "edit_code" not in offered
    assert "web_search" not in offered
    assert started["writes"] is True


async def test_an_agent_with_no_profession_runs_on_its_own_instructions(tmp_path) -> None:
    provider, started = await _hire(
        tmp_path,
        node("agent", "profession", task="t", role_override="You are a strict auditor."),
    )

    system = provider.requests[0].messages[0].content
    assert system.startswith("You are a strict auditor.")
    assert started["profession"] == "custom"
    assert "write_file" in _offered(provider)


async def test_a_profession_with_no_switch_on_can_only_talk(tmp_path) -> None:
    provider, started = await _hire(
        tmp_path,
        node("agent", "profession", task="t"),
        extra_nodes=[
            node(
                "ana",
                "custom_profession",
                name="Ana",
                role="You chat.",
                can_read=False,
            )
        ],
        extra_edges=[wire("e3", ("ana", "profession"), ("agent", "profession"), "data")],
    )

    assert _offered(provider) == set()
    assert started["tool_count"] == 0


async def test_a_clean_agent_reaches_the_model_with_nothing_added(tmp_path) -> None:
    provider = ScriptedProvider(default="tudo bem, e voce?")
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()
    doc = graph(
        [
            node("start", "start"),
            node("ana", "clean_agent", task="Ola, tudo bem?"),
            node("bia", "clean_agent", task="Oi", system="Sempre termine com uma pergunta."),
            node("show", "print"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("ana", "in")),
            wire("e2", ("ana", "out"), ("bia", "in")),
            wire("e3", ("bia", "out"), ("show", "in")),
            wire("e4", ("ana", "report"), ("show", "value"), "data"),
            wire("e5", ("show", "out"), ("end", "in")),
        ],
    )
    await run_graph(app, doc)
    await app.close()

    assert collector.of("graph.run.completed")[0]["status"] == "completed"
    assert collector.of("graph.print")[0]["text"] == "tudo bem, e voce?"
    first, second = provider.requests[-2:]
    assert [(m.role, m.content) for m in first.messages] == [("user", "Ola, tudo bem?")]
    assert [(m.role, m.content) for m in second.messages] == [
        ("system", "Sempre termine com uma pergunta."),
        ("user", "Oi"),
    ]
    assert not first.tools and not second.tools
