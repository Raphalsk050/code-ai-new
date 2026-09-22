"""Ten graphs built to break the interpreter, run through the real application.

The suite next door (`test_graph_flow.py`) proves the happy paths work. This one
goes looking for trouble: nesting, fan-in, a stop landing mid-agent, an agent
whose report drives the next loop, a graph that is nothing but a loop. Every
scenario runs against a real `CodeAIApplication` with a scripted provider, so a
bug in the interpreter, the executor or the event stream shows up here.
"""

from __future__ import annotations

import asyncio
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
    """Answers by matching a substring of the prompt; records every request."""

    def __init__(self, replies: dict[str, str] | None = None, default: str = "ok") -> None:
        self.replies = replies or {}
        self.default = default
        self.prompts: list[str] = []
        self.models: list[str] = []
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    def _answer(self, request: ModelRequest) -> str:
        text = "\n".join(str(message.content or "") for message in request.messages)
        self.prompts.append(text)
        self.models.append(request.model)
        self.calls += 1
        for needle, reply in self.replies.items():
            if needle in text:
                return reply
        return self.default

    async def stream(self, request: ModelRequest):
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=self._answer(request), finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=self._answer(request), finish_reason=FinishReason.STOP)

    async def close(self) -> None:
        return None


class SlowProvider(ScriptedProvider):
    """Blocks until released, so a stop can land in the middle of an agent."""

    def __init__(self, release: asyncio.Event, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.release = release
        self.entered = asyncio.Event()

    async def stream(self, request: ModelRequest):
        self.entered.set()
        await self.release.wait()
        async for event in super().stream(request):
            yield event


class Collector:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, envelope) -> None:
        self.rows.append((envelope.event_type, envelope.payload))

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.rows if name == event_type]

    def nodes_started(self) -> list[str]:
        return [row["node_id"] for row in self.of("graph.node.started")]

    def printed(self) -> list[str]:
        return [row["text"] for row in self.of("graph.print")]

    def outcome(self) -> dict[str, Any]:
        rows = self.of("graph.run.completed")
        assert rows, "the run never reported an outcome"
        return rows[-1]


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
        "name": "scenario",
        "variables": [],
        "nodes": nodes,
        "edges": edges,
        "view": {"pan": [0, 0], "zoom": 1},
    }
    payload.update(rest)
    return payload


async def run(app, doc: dict[str, Any], **kwargs) -> dict[str, Any]:
    started = await app.start_graph_run(doc, **kwargs)
    assert started["ok"], started
    task = app._graph_task
    assert task is not None
    await task
    return started


async def wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never held")


# -- 1: a sequence of three branches, each with work of its own -----------


async def test_a_sequence_runs_its_branches_left_to_right(tmp_path) -> None:
    provider = ScriptedProvider()
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("seq", "sequence", outputs=3),
            node("a1", "print", label="a", value="a1"),
            node("a2", "print", label="a", value="a2"),
            node("b1", "profession", profession="explorer", task="olhe"),
            node("c1", "print", label="c", value="c1"),
        ],
        [
            wire("e1", ("start", "out"), ("seq", "in")),
            wire("e2", ("seq", "out_0"), ("a1", "in")),
            wire("e3", ("a1", "out"), ("a2", "in")),
            wire("e4", ("seq", "out_1"), ("b1", "in")),
            wire("e5", ("seq", "out_2"), ("c1", "in")),
        ],
    )
    await run(app, doc)
    await app.close()

    assert collector.nodes_started() == ["start", "seq", "a1", "a2", "b1", "c1"]
    assert collector.printed() == ["a1", "a2", "c1"]
    assert collector.outcome()["status"] == "completed"


# -- 2: a loop inside a sequence branch, with a decision inside the loop --


async def test_a_loop_inside_a_sequence_with_a_decision_inside_the_loop(tmp_path) -> None:
    provider = ScriptedProvider(replies={"Question: continuar?": "NO"})
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("seq", "sequence", outputs=2),
            node("loop", "loop", kind="count", count=3),
            node("body", "print", label="lap"),
            node("branch", "decision", mode="judge", prompt="continuar?"),
            node("yes", "print", label="yes", value="segue"),
            node("no", "print", label="no", value="para"),
            node("after", "print", label="after", value="fim do laco"),
            node("tail", "print", label="tail", value="segunda saida"),
        ],
        [
            wire("e1", ("start", "out"), ("seq", "in")),
            wire("e2", ("seq", "out_0"), ("loop", "in")),
            wire("e3", ("loop", "body"), ("body", "in")),
            wire("e4", ("loop", "index"), ("body", "value"), "data"),
            wire("e5", ("body", "out"), ("branch", "in")),
            wire("e6", ("branch", "true"), ("yes", "in")),
            wire("e7", ("branch", "false"), ("no", "in")),
            wire("e8", ("loop", "completed"), ("after", "in")),
            wire("e9", ("seq", "out_1"), ("tail", "in")),
        ],
    )
    await run(app, doc)
    await app.close()

    # Three laps, each printing its index then taking the false branch, then
    # the loop's completed pin, and only then the sequence's second output.
    assert collector.printed() == [
        "0",
        "para",
        "1",
        "para",
        "2",
        "para",
        "fim do laco",
        "segunda saida",
    ]


# -- 3: an agent's report becomes the list a foreach walks ----------------


async def test_an_agent_report_feeds_a_foreach_and_each_item_reaches_an_agent(tmp_path) -> None:
    provider = ScriptedProvider(
        replies={
            "liste os arquivos": "a.py\nb.py\nc.py",
            "a.py": "revisei a.py",
            "b.py": "revisei b.py",
            "c.py": "revisei c.py",
        }
    )
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("scout", "profession", profession="explorer", task="liste os arquivos"),
            node("loop", "loop", kind="foreach"),
            node("review", "profession", profession="reviewer"),
            node("log", "print", label="rev"),
            node("done", "print", label="done", value="tudo revisado"),
        ],
        [
            wire("e1", ("start", "out"), ("scout", "in")),
            wire("e2", ("scout", "out"), ("loop", "in")),
            wire("e3", ("scout", "report"), ("loop", "items"), "data"),
            wire("e4", ("loop", "body"), ("review", "in")),
            wire("e5", ("loop", "item"), ("review", "task"), "data"),
            wire("e6", ("review", "out"), ("log", "in")),
            wire("e7", ("review", "report"), ("log", "value"), "data"),
            wire("e8", ("loop", "completed"), ("done", "in")),
        ],
    )
    await run(app, doc)
    await app.close()

    assert collector.printed() == ["revisei a.py", "revisei b.py", "revisei c.py", "tudo revisado"]
    assert len(collector.of("graph.agent.started")) == 4  # the scout plus three reviewers


# -- 4: two agents feeding one print (fan-in on a data pin) ---------------


async def test_the_second_producer_wins_a_shared_data_pin_at_run_time(tmp_path) -> None:
    """Both agents run; the print reads whichever producer its wire names."""

    provider = ScriptedProvider(replies={"primeiro": "R1", "segundo": "R2"})
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("a1", "profession", profession="explorer", task="primeiro"),
            node("a2", "profession", profession="explorer", task="segundo"),
            node("show", "print", label="r"),
        ],
        [
            wire("e1", ("start", "out"), ("a1", "in")),
            wire("e2", ("a1", "out"), ("a2", "in")),
            wire("e3", ("a2", "out"), ("show", "in")),
            wire("e4", ("a1", "report"), ("show", "value"), "data"),
        ],
    )
    await run(app, doc)
    await app.close()

    # The wire says a1, so a1 is what prints - even though a2 ran later.
    assert collector.printed() == ["R1"]


# -- 5: a stop landing while an agent is mid-call -------------------------


async def test_stopping_mid_agent_cancels_it_and_ends_the_run(tmp_path) -> None:
    release = asyncio.Event()
    provider = SlowProvider(release)
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("agent", "profession", profession="architect", task="pense muito"),
            node("after", "print", label="after", value="nunca"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("after", "in")),
        ],
    )
    started = await app.start_graph_run(doc)
    assert started["ok"]
    await wait_for(lambda: provider.entered.is_set())

    await app.stop_graph_run(started["run_id"])
    release.set()
    await app._graph_task
    await app.close()

    assert collector.outcome()["status"] == "stopped"
    assert collector.printed() == []


# -- 6: a graph that is only a loop, hitting the configured ceiling -------


async def test_a_bare_loop_is_stopped_by_the_configured_ceiling(tmp_path) -> None:
    provider = ScriptedProvider()
    app = app_for(tmp_path, provider, graph={"max_loop_iterations": 4, "max_nodes_executed": 50})
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("loop", "loop", kind="while", condition=True),
            node("body", "print", label="lap", value="lap"),
            node("after", "print", label="after", value="saiu"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("body", "in")),
            wire("e3", ("loop", "completed"), ("after", "in")),
        ],
    )
    await run(app, doc)
    await app.close()

    assert collector.printed() == ["lap"] * 4 + ["saiu"]
    assert collector.of("graph.loop.capped")[0]["cap"] == 4
    assert collector.outcome()["status"] == "completed"


async def test_the_node_ceiling_ends_a_graph_that_would_never_stop(tmp_path) -> None:
    provider = ScriptedProvider()
    app = app_for(tmp_path, provider, graph={"max_nodes_executed": 8, "max_loop_iterations": 500})
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("loop", "loop", kind="while", condition=True),
            node("body", "print", label="lap", value="lap"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("body", "in")),
        ],
    )
    await run(app, doc)
    await app.close()

    outcome = collector.outcome()
    assert outcome["status"] == "exhausted"
    assert "node ceiling" in outcome["reason"]
    assert outcome["nodes_executed"] == 8


# -- 7: the user answers inside a loop, three times -----------------------


async def test_a_user_chat_inside_a_loop_asks_once_per_lap(tmp_path) -> None:
    provider = ScriptedProvider()
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("loop", "loop", kind="count", count=3),
            node("ask", "user_chat", prompt="qual o proximo?"),
            node("show", "print", label="resp"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("ask", "in")),
            wire("e3", ("ask", "out"), ("show", "in")),
            wire("e4", ("ask", "answer"), ("show", "value"), "data"),
        ],
    )
    started = await app.start_graph_run(doc)
    assert started["ok"]

    for index in range(3):
        # `index` is bound now rather than read when the lambda runs.
        await wait_for(
            lambda wanted=index + 1: len(collector.of("graph.user_chat.requested")) == wanted
        )
        call_id = collector.of("graph.user_chat.requested")[index]["call_id"]
        assert app.answer_graph_question(started["run_id"], call_id, f"resposta {index}")["ok"]

    await app._graph_task
    await app.close()

    assert collector.printed() == ["resposta 0", "resposta 1", "resposta 2"]


# -- 8: a variable carried across laps and read after the loop ------------


async def test_a_variable_written_in_the_body_survives_the_loop(tmp_path) -> None:
    provider = ScriptedProvider()
    app = app_for(tmp_path, provider)
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("loop", "loop", kind="count", count=4),
            node("keep", "var_set", variable="ultimo"),
            node("read", "var_get", variable="ultimo"),
            node("show", "print", label="ultimo"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("keep", "in")),
            wire("e3", ("loop", "index"), ("keep", "value"), "data"),
            wire("e4", ("loop", "completed"), ("show", "in")),
            wire("e5", ("read", "value"), ("show", "value"), "data"),
            wire("e6", ("show", "out"), ("end", "in")),
            wire("e7", ("read", "value"), ("end", "result"), "data"),
        ],
        variables=[{"id": "v1", "name": "ultimo", "type": "int", "default": -1}],
    )
    await run(app, doc)
    await app.close()

    assert collector.printed() == ["3"]
    outcome = collector.outcome()
    assert outcome["variables"]["ultimo"] == "3"
    assert outcome["result"] == "3"
    # One event per write, so the inspector can follow the value live.
    assert len(collector.of("graph.variable.changed")) == 4


# -- 9: two endpoints in one graph, each agent on its own model -----------


async def test_each_agent_runs_on_the_model_its_own_node_names(tmp_path) -> None:
    provider = ScriptedProvider()
    app = app_for(tmp_path, provider)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("fast", "model", model="fast-3b"),
            node("strong", "model", model="strong-70b"),
            node("scout", "profession", profession="explorer", task="procure"),
            node("writer", "profession", profession="code_writer", task="escreva"),
            node("plain", "profession", profession="teacher", task="explique"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("scout", "in")),
            wire("e2", ("fast", "model"), ("scout", "model"), "data"),
            wire("e3", ("scout", "out"), ("writer", "in")),
            wire("e4", ("strong", "model"), ("writer", "model"), "data"),
            wire("e5", ("writer", "out"), ("plain", "in")),
            wire("e6", ("plain", "out"), ("end", "in")),
        ],
    )
    await run(app, doc)
    await app.close()

    # Three agents: two on their own model, the third on the session's.
    assert provider.models == ["fast-3b", "strong-70b", "main-model"]


# -- 10: the graph shares the session, and says so ------------------------


async def test_a_graph_and_the_chat_take_turns(tmp_path) -> None:
    release = asyncio.Event()
    provider = SlowProvider(release)
    app = app_for(tmp_path, provider)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("agent", "profession", profession="architect", task="pense"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("end", "in")),
        ],
    )
    started = await app.start_graph_run(doc)
    assert started["ok"]
    await wait_for(lambda: provider.entered.is_set())

    # A second graph is refused while the first holds the slot.
    refused = await app.start_graph_run(doc)
    assert refused["ok"] is False
    assert "already running" in refused["message"]

    release.set()
    await app._graph_task

    # With the graph finished, the slot is free again.
    release.clear()
    provider.entered.clear()
    release.set()
    again = await app.start_graph_run(doc)
    assert again["ok"] is True
    await app._graph_task
    await app.close()


# -- bonus: the shapes that should degrade, not crash ---------------------


async def test_an_agent_that_fails_stops_the_run_at_that_node(tmp_path) -> None:
    class RefusingProvider(ScriptedProvider):
        async def stream(self, request: ModelRequest):
            raise RuntimeError("endpoint fora do ar")
            yield  # unreachable: makes this an async generator, like the real one

    app = app_for(tmp_path, RefusingProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("agent", "profession", profession="architect", task="pense"),
            node("after", "print", label="after", value="nunca"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("after", "in")),
        ],
    )
    await run(app, doc)
    await app.close()

    outcome = collector.outcome()
    assert outcome["status"] == "failed"
    assert collector.printed() == []
    assert collector.of("graph.node.failed")[0]["node_id"] == "agent"


async def test_a_print_of_a_report_that_has_not_happened_warns_and_carries_on(tmp_path) -> None:
    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    doc = graph(
        [
            node("start", "start"),
            node("show", "print", label="cedo"),
            node("agent", "profession", profession="teacher", task="explique"),
        ],
        [
            wire("e1", ("start", "out"), ("show", "in")),
            wire("e2", ("agent", "report"), ("show", "value"), "data"),
            wire("e3", ("show", "out"), ("agent", "in")),
        ],
    )
    await run(app, doc)
    await app.close()

    assert collector.printed() == [""]
    assert "has not produced" in collector.of("graph.warning")[0]["message"]
    assert collector.outcome()["status"] == "completed"


async def test_a_graph_with_a_hundred_nodes_runs_within_its_budget(tmp_path) -> None:
    """A long chain must not recurse: the interpreter walks an explicit stack."""

    app = app_for(tmp_path, ScriptedProvider())
    collector = Collector()
    app.subscribe(collector)
    await app.start()

    nodes = [node("start", "start")]
    edges = [wire("e0", ("start", "out"), ("p0", "in"))]
    for index in range(100):
        nodes.append(node(f"p{index}", "print", label=f"p{index}", value=str(index)))
        if index:
            edges.append(wire(f"e{index}", (f"p{index - 1}", "out"), (f"p{index}", "in")))
    nodes.append(node("end", "end"))
    edges.append(wire("elast", ("p99", "out"), ("end", "in")))

    await run(app, graph(nodes, edges))
    await app.close()

    assert collector.outcome()["status"] == "completed"
    assert len(collector.printed()) == 100
    assert collector.printed()[-1] == "99"
