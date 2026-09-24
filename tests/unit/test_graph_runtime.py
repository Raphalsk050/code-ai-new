from __future__ import annotations

import asyncio
from typing import Any

from code_ai.core.graph.compile import compile_graph
from code_ai.core.graph.professions import professions_payload
from code_ai.core.graph.runtime import (
    AgentOutcome,
    GraphInterpreter,
    GraphLimits,
    ModelBinding,
    ProfessionBinding,
    RunStatus,
    ToolAccess,
    ToolsetBinding,
)

PROFESSIONS = tuple(row["name"] for row in professions_payload())


class FakeExecutor:
    """A NodeExecutor that never calls a model.

    ``reports`` maps a profession (or a node id) to what its agent hands back;
    anything unlisted completes with an echo. Everything it was asked to do is
    recorded, in order, so a test can assert on the orchestration itself.
    """

    def __init__(
        self,
        *,
        reports: dict[str, Any] | None = None,
        verdicts: list[bool] | None = None,
        answers: list[str] | None = None,
        answer_delay: float = 0.0,
    ) -> None:
        self.reports = reports or {}
        self.verdicts = list(verdicts or [])
        self.answers = list(answers or [])
        self.answer_delay = answer_delay
        self.agent_calls: list[dict[str, Any]] = []
        self.clean_calls: list[dict[str, Any]] = []
        self.judge_calls: list[dict[str, Any]] = []
        self.ask_calls: list[dict[str, Any]] = []

    async def run_agent(
        self,
        *,
        node_id: str,
        profession: str,
        task: str,
        extra_instructions: str,
        model: ModelBinding | None,
        max_steps: int,
        timeout_s: int,
        cancel: asyncio.Event,
        custom: ProfessionBinding | None = None,
        access: ToolAccess | None = None,
        name: str = "",
    ) -> AgentOutcome:
        self.agent_calls.append(
            {
                "node_id": node_id,
                "profession": profession,
                "custom": custom,
                "task": task,
                "extra": extra_instructions,
                "model": model,
                "max_steps": max_steps,
                "timeout_s": timeout_s,
                "access": access,
                "name": name,
            }
        )
        scripted = self.reports.get(node_id, self.reports.get(profession))
        if callable(scripted):
            scripted = scripted(task)
        if isinstance(scripted, AgentOutcome):
            return scripted
        if isinstance(scripted, str):
            return AgentOutcome(status="completed", summary=scripted, usage={"total_tokens": 10})
        return AgentOutcome(
            status="completed", summary=f"{profession} did: {task}", usage={"total_tokens": 10}
        )

    async def run_clean(
        self,
        *,
        node_id: str,
        name: str,
        system: str,
        task: str,
        model: ModelBinding | None,
        timeout_s: int,
        cancel: asyncio.Event,
    ) -> AgentOutcome:
        self.clean_calls.append(
            {"node_id": node_id, "name": name, "system": system, "task": task, "model": model}
        )
        scripted = self.reports.get(node_id)
        if callable(scripted):
            scripted = scripted(task)
        return AgentOutcome(status="completed", summary=scripted or f"clean: {task}")

    async def judge(
        self,
        *,
        node_id: str,
        question: str,
        context: str,
        model: ModelBinding | None,
        cancel: asyncio.Event,
    ) -> bool:
        self.judge_calls.append({"node_id": node_id, "question": question, "context": context})
        return self.verdicts.pop(0) if self.verdicts else False

    async def ask_user(self, *, node_id: str, prompt: str, cancel: asyncio.Event) -> str:
        self.ask_calls.append({"node_id": node_id, "prompt": prompt})
        if self.answer_delay:
            await asyncio.sleep(self.answer_delay)
        return self.answers.pop(0) if self.answers else ""


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))

    def types(self) -> list[str]:
        return [name for name, _ in self.events]

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.events if name == event_type]

    def node_order(self) -> list[str]:
        return [payload["node_id"] for payload in self.of("graph.node.started")]

    def printed(self) -> list[str]:
        return [payload["text"] for payload in self.of("graph.print")]


def build(doc: dict[str, Any], **kwargs: Any):
    program, issues = compile_graph(doc, professions=PROFESSIONS)
    assert program is not None, [
        item.message for item in issues if item.severity == "error"
    ]
    return program


async def run(
    doc: dict[str, Any],
    executor: FakeExecutor | None = None,
    *,
    limits: GraphLimits | None = None,
    variables: dict[str, Any] | None = None,
    cancel: asyncio.Event | None = None,
):
    program = build(doc)
    recorder = Recorder()
    interpreter = GraphInterpreter(
        program=program,
        executor=executor or FakeExecutor(),
        emit=recorder,
        run_id="run-1",
        limits=limits,
        cancel=cancel,
        variables=variables,
    )
    outcome = await interpreter.run()
    return outcome, recorder, interpreter


def doc(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], **rest: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "name": "t",
        "variables": [],
        "nodes": nodes,
        "edges": edges,
        "view": {"pan": [0, 0], "zoom": 1},
    }
    payload.update(rest)
    return payload


def node(node_id: str, node_kind: str, **data: Any) -> dict[str, Any]:
    return {"id": node_id, "kind": node_kind, "pos": [0, 0], "data": data}


def wire(edge_id: str, source: tuple[str, str], target: tuple[str, str], kind: str = "exec"):
    return {"id": edge_id, "kind": kind, "from": list(source), "to": list(target)}


# -- scenario 1: the straight line ---------------------------------------


async def test_linear_chain_runs_in_order_and_carries_the_report() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("model", "model", model="local-7b", base_url="http://localhost:11434/v1"),
            node("agent", "profession", profession="architect", task="design the importer"),
            node("show", "print", label="design"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("model", "model"), ("agent", "model"), "data"),
            wire("e3", ("agent", "out"), ("show", "in")),
            wire("e4", ("agent", "report"), ("show", "value"), "data"),
            wire("e5", ("show", "out"), ("end", "in")),
        ],
    )
    executor = FakeExecutor(reports={"agent": "here is the design"})

    outcome, recorder, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    assert recorder.node_order() == ["start", "agent", "show", "end"]
    assert recorder.printed() == ["here is the design"]
    assert executor.agent_calls[0]["task"] == "design the importer"
    assert executor.agent_calls[0]["model"].model == "local-7b"
    assert outcome.usage == {"total_tokens": 10}


async def test_run_lifecycle_events_are_emitted_in_order() -> None:
    graph = doc(
        [node("start", "start"), node("end", "end")],
        [wire("e1", ("start", "out"), ("end", "in"))],
    )
    outcome, recorder, _ = await run(graph)

    assert recorder.types()[0] == "graph.run.started"
    assert recorder.types()[-1] == "graph.run.completed"
    assert outcome.nodes_executed == 2
    completed = recorder.of("graph.node.completed")
    assert [item["node_id"] for item in completed] == ["start", "end"]


async def test_exec_edges_light_up_as_the_flow_walks_them() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("a", "print", label="a", value="1"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("a", "in")),
            wire("e2", ("a", "out"), ("end", "in")),
        ],
    )
    _, recorder, _ = await run(graph)

    lit = [item["edge_id"] for item in recorder.of("graph.edge.active") if item["kind"] == "exec"]
    assert lit == ["e1", "e2"]


async def test_data_edges_light_up_when_pulled() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("model", "model", model="m"),
            node("agent", "profession", profession="teacher", task="explain"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("model", "model"), ("agent", "model"), "data"),
            wire("e3", ("agent", "out"), ("end", "in")),
        ],
    )
    _, recorder, _ = await run(graph)

    data_edges = [
        item["edge_id"] for item in recorder.of("graph.edge.active") if item["kind"] == "data"
    ]
    assert data_edges == ["e2"]


# -- scenario 2: sequence ------------------------------------------------


async def test_sequence_finishes_one_branch_before_starting_the_next() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("seq", "sequence", outputs=3),
            node("a1", "print", label="a1", value="a1"),
            node("a2", "print", label="a2", value="a2"),
            node("b1", "print", label="b1", value="b1"),
            node("c1", "print", label="c1", value="c1"),
        ],
        [
            wire("e1", ("start", "out"), ("seq", "in")),
            wire("e2", ("seq", "out_0"), ("a1", "in")),
            wire("e3", ("a1", "out"), ("a2", "in")),
            wire("e4", ("seq", "out_1"), ("b1", "in")),
            wire("e5", ("seq", "out_2"), ("c1", "in")),
        ],
    )
    _, recorder, _ = await run(graph)

    assert recorder.node_order() == ["start", "seq", "a1", "a2", "b1", "c1"]


async def test_sequence_with_an_empty_branch_continues() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("seq", "sequence", outputs=3),
            node("only", "print", label="only", value="x"),
        ],
        [
            wire("e1", ("start", "out"), ("seq", "in")),
            wire("e2", ("seq", "out_2"), ("only", "in")),
        ],
    )
    outcome, recorder, _ = await run(graph)

    assert recorder.node_order() == ["start", "seq", "only"]
    assert outcome.status is RunStatus.COMPLETED


# -- scenario 3: decisions -----------------------------------------------


async def test_bool_decision_takes_the_true_branch() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("branch", "decision", mode="bool", condition=True),
            node("yes", "print", label="yes", value="yes"),
            node("no", "print", label="no", value="no"),
        ],
        [
            wire("e1", ("start", "out"), ("branch", "in")),
            wire("e2", ("branch", "true"), ("yes", "in")),
            wire("e3", ("branch", "false"), ("no", "in")),
        ],
    )
    _, recorder, _ = await run(graph)

    assert recorder.printed() == ["yes"]


async def test_bool_decision_reads_a_variable_through_a_wire() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("flag", "var_get", variable="done"),
            node("branch", "decision", mode="bool"),
            node("yes", "print", label="yes", value="yes"),
            node("no", "print", label="no", value="no"),
        ],
        [
            wire("e1", ("start", "out"), ("branch", "in")),
            wire("e2", ("flag", "value"), ("branch", "condition"), "data"),
            wire("e3", ("branch", "true"), ("yes", "in")),
            wire("e4", ("branch", "false"), ("no", "in")),
        ],
        variables=[{"id": "v1", "name": "done", "type": "bool", "default": False}],
    )
    _, recorder, _ = await run(graph, variables={"done": True})

    assert recorder.printed() == ["yes"]


async def test_judge_decision_sees_the_last_report_as_context() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("agent", "profession", profession="reviewer", task="review"),
            node("branch", "decision", mode="judge", prompt="is it good?"),
            node("yes", "print", label="yes", value="ship"),
            node("no", "print", label="no", value="fix"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("branch", "in")),
            wire("e3", ("branch", "true"), ("yes", "in")),
            wire("e4", ("branch", "false"), ("no", "in")),
        ],
    )
    executor = FakeExecutor(reports={"agent": "looks solid"}, verdicts=[True])

    _, recorder, _ = await run(graph, executor)

    assert recorder.printed() == ["ship"]
    assert executor.judge_calls[0]["question"] == "is it good?"
    assert "looks solid" in executor.judge_calls[0]["context"]


# -- scenario 4: loops ---------------------------------------------------


async def test_count_loop_runs_its_body_and_then_completes() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("loop", "loop", kind="count", count=3),
            node("body", "print", label="lap"),
            node("after", "print", label="after", value="done"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("body", "in")),
            wire("e3", ("loop", "index"), ("body", "value"), "data"),
            wire("e4", ("loop", "completed"), ("after", "in")),
        ],
    )
    _, recorder, _ = await run(graph)

    assert recorder.printed() == ["0", "1", "2", "done"]
    assert len(recorder.of("graph.loop.iteration")) == 3


async def test_foreach_loop_walks_one_line_per_lap() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("loop", "loop", kind="foreach", items="alpha\n\nbeta\ngamma"),
            node("body", "print", label="file"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("body", "in")),
            wire("e3", ("loop", "item"), ("body", "value"), "data"),
        ],
    )
    _, recorder, _ = await run(graph)

    assert recorder.printed() == ["alpha", "beta", "gamma"]


async def test_while_loop_exits_when_its_condition_flips() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("get", "var_get", variable="keep"),
            node("loop", "loop", kind="while"),
            node("body", "print", label="lap", value="lap"),
            node("stop", "var_set", variable="keep", value=False),
            node("after", "print", label="after", value="out"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("get", "value"), ("loop", "condition"), "data"),
            wire("e3", ("loop", "body"), ("body", "in")),
            wire("e4", ("body", "out"), ("stop", "in")),
            wire("e5", ("loop", "completed"), ("after", "in")),
        ],
        variables=[{"id": "v1", "name": "keep", "type": "bool", "default": True}],
    )
    _, recorder, _ = await run(graph)

    assert recorder.printed() == ["lap", "out"]


async def test_loop_is_capped_even_when_the_condition_never_flips() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("loop", "loop", kind="while", condition=True),
            node("body", "print", label="lap", value="lap"),
            node("after", "print", label="after", value="out"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("body", "in")),
            wire("e3", ("loop", "completed"), ("after", "in")),
        ],
    )
    _, recorder, _ = await run(graph, limits=GraphLimits(max_loop_iterations=4))

    assert recorder.printed() == ["lap"] * 4 + ["out"]
    assert recorder.of("graph.loop.capped")[0]["cap"] == 4


async def test_nested_loops_keep_their_own_index() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("outer", "loop", kind="count", count=2),
            node("inner", "loop", kind="count", count=2),
            node("body", "print", label="pair"),
            node("oi", "var_set", variable="pair"),
        ],
        [
            wire("e1", ("start", "out"), ("outer", "in")),
            wire("e2", ("outer", "body"), ("inner", "in")),
            wire("e3", ("inner", "body"), ("body", "in")),
            wire("e4", ("inner", "index"), ("body", "value"), "data"),
            wire("e5", ("body", "out"), ("oi", "in")),
            wire("e6", ("outer", "index"), ("oi", "value"), "data"),
        ],
        variables=[{"id": "v1", "name": "pair", "type": "int", "default": -1}],
    )
    outcome, recorder, _ = await run(graph)

    assert recorder.printed() == ["0", "1", "0", "1"]
    assert outcome.variables["pair"] == 1


# -- scenario 5: variables -----------------------------------------------


async def test_variables_round_trip_through_set_and_get() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("set", "var_set", variable="ticket", value="ABC-1"),
            node("get", "var_get", variable="ticket"),
            node("show", "print", label="ticket"),
        ],
        [
            wire("e1", ("start", "out"), ("set", "in")),
            wire("e2", ("set", "out"), ("show", "in")),
            wire("e3", ("get", "value"), ("show", "value"), "data"),
        ],
        variables=[{"id": "v1", "name": "ticket", "type": "string", "default": ""}],
    )
    outcome, recorder, _ = await run(graph)

    assert recorder.printed() == ["ABC-1"]
    assert outcome.variables["ticket"] == "ABC-1"
    assert recorder.of("graph.variable.changed")[0]["name"] == "ticket"


async def test_variables_are_coerced_to_their_declared_type() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("set", "var_set", variable="rounds", value="7"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("set", "in")),
            wire("e2", ("set", "out"), ("end", "in")),
        ],
        variables=[{"id": "v1", "name": "rounds", "type": "int", "default": 0}],
    )
    outcome, _, _ = await run(graph)

    assert outcome.variables["rounds"] == 7


async def test_run_overrides_seed_the_declared_variables() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("get", "var_get", variable="brief"),
            node("show", "print", label="brief"),
        ],
        [
            wire("e1", ("start", "out"), ("show", "in")),
            wire("e2", ("get", "value"), ("show", "value"), "data"),
        ],
        variables=[{"id": "v1", "name": "brief", "type": "string", "default": "nothing"}],
    )
    _, recorder, _ = await run(graph, variables={"brief": "from the toolbar"})

    assert recorder.printed() == ["from the toolbar"]


async def test_an_agent_task_can_come_from_a_variable() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("get", "var_get", variable="brief"),
            node("agent", "profession", profession="code_writer"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("get", "value"), ("agent", "task"), "data"),
            wire("e3", ("agent", "out"), ("end", "in")),
        ],
        variables=[{"id": "v1", "name": "brief", "type": "string", "default": "write the parser"}],
    )
    executor = FakeExecutor()

    await run(graph, executor)

    assert executor.agent_calls[0]["task"] == "write the parser"


# -- scenario 6: the user in the loop ------------------------------------


async def test_user_chat_answer_reaches_the_next_node() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("ask", "user_chat", title="Which module?", prompt="which module?"),
            node("agent", "profession", profession="code_writer"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("ask", "in")),
            wire("e2", ("ask", "out"), ("agent", "in")),
            wire("e3", ("ask", "answer"), ("agent", "task"), "data"),
            wire("e4", ("agent", "out"), ("end", "in")),
        ],
    )
    executor = FakeExecutor(answers=["the importer"])

    outcome, _, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    assert executor.ask_calls[0]["prompt"] == "which module?"
    assert executor.agent_calls[0]["task"] == "the importer"


async def test_stopping_while_parked_on_the_user_ends_the_run() -> None:
    graph = doc(
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
    executor = FakeExecutor(answer_delay=30.0)
    cancel = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        cancel.set()

    stopper = asyncio.ensure_future(stop_soon())
    outcome, recorder, _ = await run(graph, executor, cancel=cancel)
    await stopper

    assert outcome.status is RunStatus.STOPPED
    assert recorder.of("graph.run.completed")[0]["status"] == "stopped"


# -- scenario 7: failure and budgets -------------------------------------


async def test_a_failed_agent_stops_the_run_and_names_the_node() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("agent", "profession", profession="code_writer", task="do it"),
            node("after", "print", label="after", value="never"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("after", "in")),
        ],
    )
    executor = FakeExecutor(
        reports={"agent": AgentOutcome(status="failed", error="the model refused")}
    )

    outcome, recorder, _ = await run(graph, executor)

    assert outcome.status is RunStatus.FAILED
    assert "the model refused" in outcome.error
    assert recorder.printed() == []
    assert recorder.of("graph.node.failed")[0]["node_id"] == "agent"


async def test_a_timed_out_agent_fails_the_run() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("agent", "profession", profession="architect", task="think"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("end", "in")),
        ],
    )
    executor = FakeExecutor(
        reports={"agent": AgentOutcome(status="timeout", error="exceeded its budget")}
    )

    outcome, _, _ = await run(graph, executor)

    assert outcome.status is RunStatus.FAILED
    assert "timeout" in outcome.reason


async def test_the_node_ceiling_exhausts_a_runaway_graph() -> None:
    graph = doc(
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
    outcome, _, _ = await run(
        graph, limits=GraphLimits(max_nodes_executed=5, max_loop_iterations=50)
    )

    assert outcome.status is RunStatus.EXHAUSTED
    assert "node ceiling" in outcome.reason
    assert outcome.nodes_executed == 5


async def test_the_time_ceiling_exhausts_a_slow_graph() -> None:
    graph = doc(
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
    outcome, _, _ = await run(graph, limits=GraphLimits(max_seconds=0, max_loop_iterations=50))

    assert outcome.status is RunStatus.EXHAUSTED
    assert "time ceiling" in outcome.reason


async def test_stopping_mid_run_reports_stopped() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("loop", "loop", kind="count", count=40),
            node("body", "print", label="lap", value="lap"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("body", "in")),
        ],
    )
    cancel = asyncio.Event()
    cancel.set()

    outcome, recorder, _ = await run(graph, cancel=cancel)

    assert outcome.status is RunStatus.STOPPED
    assert recorder.printed() == []


# -- scenario 8: reading a value before its producer ran -----------------


async def test_reading_a_report_before_its_agent_ran_warns_and_degrades() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("show", "print", label="early"),
            node("agent", "profession", profession="teacher", task="explain"),
        ],
        [
            wire("e1", ("start", "out"), ("show", "in")),
            wire("e2", ("agent", "report"), ("show", "value"), "data"),
            wire("e3", ("show", "out"), ("agent", "in")),
        ],
    )
    outcome, recorder, _ = await run(graph)

    assert outcome.status is RunStatus.COMPLETED
    assert recorder.printed() == [""]
    assert "has not produced" in recorder.of("graph.warning")[0]["message"]


# -- scenario 9: End carries the result ----------------------------------


async def test_end_publishes_whatever_reaches_its_result_pin() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("agent", "profession", profession="wiki_writer", task="write the page"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("end", "in")),
            wire("e3", ("agent", "report"), ("end", "result"), "data"),
        ],
    )
    executor = FakeExecutor(reports={"agent": "the page is written"})

    outcome, _, _ = await run(graph, executor)

    assert outcome.result == "the page is written"
    assert outcome.status is RunStatus.COMPLETED


async def test_a_graph_with_no_end_still_completes_when_the_flow_runs_out() -> None:
    graph = doc(
        [node("start", "start"), node("show", "print", label="x", value="x")],
        [wire("e1", ("start", "out"), ("show", "in"))],
    )
    outcome, _, _ = await run(graph)

    assert outcome.status is RunStatus.COMPLETED
    assert outcome.result is None


# -- scenario 10: the full pipeline --------------------------------------


async def test_review_loop_pipeline_runs_until_the_judge_is_happy() -> None:
    """Architect -> loop(write -> review -> judge) -> document, with a shared model."""

    graph = doc(
        [
            node("start", "start"),
            node("model", "model", model="local-30b", base_url="http://localhost:11434/v1"),
            node("architect", "profession", profession="architect", task="design the feature"),
            node("loop", "loop", kind="count", count=3),
            node("writer", "profession", profession="code_writer"),
            node("reviewer", "profession", profession="reviewer", task="review the change"),
            node("judge", "decision", mode="judge", prompt="is the change acceptable?"),
            node("mark", "var_set", variable="approved", value=True),
            node("docs", "profession", profession="documenter", task="document it"),
            node("log", "print", label="done"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("architect", "in")),
            wire("e2", ("model", "model"), ("architect", "model"), "data"),
            wire("e3", ("architect", "out"), ("loop", "in")),
            wire("e4", ("loop", "body"), ("writer", "in")),
            wire("e5", ("architect", "report"), ("writer", "task"), "data"),
            wire("e6", ("model", "model"), ("writer", "model"), "data"),
            wire("e7", ("writer", "out"), ("reviewer", "in")),
            wire("e8", ("reviewer", "out"), ("judge", "in")),
            wire("e9", ("judge", "true"), ("mark", "in")),
            wire("e10", ("loop", "completed"), ("docs", "in")),
            wire("e11", ("docs", "out"), ("log", "in")),
            wire("e12", ("docs", "report"), ("log", "value"), "data"),
            wire("e13", ("log", "out"), ("end", "in")),
        ],
        variables=[{"id": "v1", "name": "approved", "type": "bool", "default": False}],
    )
    executor = FakeExecutor(
        reports={
            "architect": "build it in three parts",
            "writer": "wrote part one",
            "reviewer": "one nit left",
            "docs": "documented",
        },
        verdicts=[False, True, True],
    )

    outcome, recorder, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    assert outcome.variables["approved"] is True
    # The writer takes the architect's report as its task, every lap.
    assert [call["task"] for call in executor.agent_calls if call["profession"] == "code_writer"] \
        == ["build it in three parts"] * 3
    assert len(executor.judge_calls) == 3
    assert recorder.printed() == ["documented"]
    assert recorder.node_order().count("writer") == 3
    assert recorder.node_order()[-2:] == ["log", "end"]


async def test_per_node_models_reach_the_right_agents() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("small", "model", model="fast-3b", api_mode="ollama"),
            node("big", "model", model="strong-70b", base_url="https://api.example.com/v1"),
            node("scout", "profession", profession="explorer", task="find it"),
            node("writer", "profession", profession="code_writer", task="change it"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("scout", "in")),
            wire("e2", ("small", "model"), ("scout", "model"), "data"),
            wire("e3", ("scout", "out"), ("writer", "in")),
            wire("e4", ("big", "model"), ("writer", "model"), "data"),
            wire("e5", ("writer", "out"), ("end", "in")),
        ],
    )
    executor = FakeExecutor()

    await run(graph, executor)

    by_profession = {call["profession"]: call["model"] for call in executor.agent_calls}
    assert by_profession["explorer"].model == "fast-3b"
    assert by_profession["explorer"].api_mode == "ollama"
    assert by_profession["code_writer"].model == "strong-70b"
    assert by_profession["code_writer"].base_url == "https://api.example.com/v1"


async def test_node_budgets_travel_to_the_executor() -> None:
    graph = doc(
        [
            node("start", "start"),
            node(
                "agent",
                "profession",
                profession="tester",
                task="write tests",
                max_steps=12,
                timeout_s=90,
                role_override="use pytest only",
            ),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("end", "in")),
        ],
    )
    executor = FakeExecutor()

    await run(graph, executor)

    call = executor.agent_calls[0]
    assert call["max_steps"] == 12
    assert call["timeout_s"] == 90
    assert call["extra"] == "use pytest only"


async def test_two_custom_agents_hold_a_conversation_across_a_loop() -> None:
    """The case the editor exists for: two professions the user wrote, talking.

    Each lap appends both replies to one transcript variable, and each agent is
    handed that transcript as its task - so the second lap proves they are
    reading each other rather than monologuing in parallel.
    """

    graph = doc(
        [
            node("start", "start"),
            node("ana_role", "custom_profession", name="Ana", role="You are Ana.", can_read=True),
            node(
                "bruno_role",
                "custom_profession",
                name="Bruno",
                role="You are Bruno.",
                can_read=True,
            ),
            node("loop", "loop", kind="count", count=2),
            node("read_a", "var_get", variable="transcript"),
            node("ana", "profession"),
            node("say_a", "var_set", variable="transcript", mode="append", prefix="Ana:"),
            node("read_b", "var_get", variable="transcript"),
            node("bruno", "profession"),
            node("say_b", "var_set", variable="transcript", mode="append", prefix="Bruno:"),
            node("read_end", "var_get", variable="transcript"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("ana", "in")),
            wire("e3", ("ana_role", "profession"), ("ana", "profession"), "data"),
            wire("e4", ("read_a", "value"), ("ana", "task"), "data"),
            wire("e5", ("ana", "out"), ("say_a", "in")),
            wire("e6", ("ana", "report"), ("say_a", "value"), "data"),
            wire("e7", ("say_a", "out"), ("bruno", "in")),
            wire("e8", ("bruno_role", "profession"), ("bruno", "profession"), "data"),
            wire("e9", ("read_b", "value"), ("bruno", "task"), "data"),
            wire("e10", ("bruno", "out"), ("say_b", "in")),
            wire("e11", ("bruno", "report"), ("say_b", "value"), "data"),
            wire("e12", ("loop", "completed"), ("end", "in")),
            wire("e13", ("read_end", "value"), ("end", "result"), "data"),
        ],
        variables=[{"id": "v1", "name": "transcript", "type": "string", "default": ""}],
    )
    # Each reply quotes how much it was told, which is the evidence of reading.
    executor = FakeExecutor(
        reports={
            "ana": lambda heard: f"ana saw {len(heard.splitlines())} lines",
            "bruno": lambda heard: f"bruno saw {len(heard.splitlines())} lines",
        }
    )

    outcome, _, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    assert outcome.result.splitlines() == [
        "Ana: ana saw 0 lines",
        "",
        "Bruno: bruno saw 1 lines",
        "",
        "Ana: ana saw 3 lines",
        "",
        "Bruno: bruno saw 5 lines",
    ]
    # Ana opened on an empty transcript and re-entered holding Bruno's turn.
    ana_tasks = [call["task"] for call in executor.agent_calls if call["node_id"] == "ana"]
    assert ana_tasks[0] == ""
    assert "Bruno: bruno saw 1 lines" in ana_tasks[1]


async def test_a_profession_written_in_the_editor_reaches_the_executor_verbatim() -> None:
    graph = doc(
        [
            node("start", "start"),
            node(
                "role",
                "custom_profession",
                name="Ana",
                role="You are Ana. Speak in one sentence.",
                can_read=True,
                can_write=True,
                max_steps=7,
            ),
            node("ana", "profession", task="say hello"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("ana", "in")),
            wire("e2", ("role", "profession"), ("ana", "profession"), "data"),
            wire("e3", ("ana", "out"), ("end", "in")),
        ],
    )

    executor = FakeExecutor()

    outcome, _, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    hired = executor.agent_calls[0]["custom"]
    assert hired is not None
    # Verbatim: no report rule, no framing, nothing bolted on.
    assert hired.role == "You are Ana. Speak in one sentence."
    assert hired.name == "Ana"
    assert (hired.can_read, hired.can_write, hired.can_run) == (True, True, False)
    assert hired.max_steps == 7
    assert hired.framing is False


# -- what an agent may touch ------------------------------------------------


async def test_an_agent_hands_over_only_the_switches_it_set() -> None:
    graph = doc(
        [
            node("start", "start"),
            node(
                "agent",
                "profession",
                profession="architect",
                task="t",
                can_write=True,
                can_run=False,
                can_read=None,
                blocked_tools=["web_search", "web_search", " "],
                name="Planner",
            ),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("end", "in")),
        ],
    )
    executor = FakeExecutor()

    await run(graph, executor)

    access = executor.agent_calls[0]["access"]
    assert access.switches == {"can_write": True, "can_run": False}
    assert access.toolsets == ()
    assert access.blocked == ("web_search",)
    assert executor.agent_calls[0]["name"] == "Planner"


async def test_wired_tools_nodes_reach_the_agent_in_order_and_light_their_wires() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("git", "tools", name="git", tools=["git_review", "read_file"]),
            node("web", "tools", tools=["web_search", "read_file"]),
            node("agent", "profession", task="t"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("git", "tools"), ("agent", "tools"), "data"),
            wire("e3", ("web", "tools"), ("agent", "tools"), "data"),
            wire("e4", ("agent", "out"), ("end", "in")),
        ],
    )
    executor = FakeExecutor()

    outcome, recorder, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    access = executor.agent_calls[0]["access"]
    assert access.toolsets == (
        ToolsetBinding(name="git", tools=("git_review", "read_file")),
        ToolsetBinding(name="", tools=("web_search", "read_file")),
    )
    assert access.tool_names() == ("git_review", "read_file", "web_search")
    lit = {payload["edge_id"] for payload in recorder.of("graph.edge.active")}
    assert {"e2", "e3"} <= lit


async def test_an_agent_with_no_profession_passes_its_instructions_through() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("agent", "profession", task="t", role_override="You are an auditor."),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("agent", "in")),
            wire("e2", ("agent", "out"), ("end", "in")),
        ],
    )
    executor = FakeExecutor()

    outcome, _, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    call = executor.agent_calls[0]
    assert call["profession"] == ""
    assert call["extra"] == "You are an auditor."


async def test_two_clean_agents_talk_through_a_variable() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("loop", "loop", kind="count", count=2),
            node("read", "var_get", variable="said"),
            node("ana", "clean_agent", name="Ana", system="Pergunte algo."),
            node("bia", "clean_agent", name="Bia"),
            node("keep", "var_set", variable="said"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("ana", "in")),
            wire("e3", ("read", "value"), ("ana", "task"), "data"),
            wire("e4", ("ana", "out"), ("bia", "in")),
            wire("e5", ("ana", "report"), ("bia", "task"), "data"),
            wire("e6", ("bia", "out"), ("keep", "in")),
            wire("e7", ("bia", "report"), ("keep", "value"), "data"),
            wire("e8", ("loop", "completed"), ("end", "in")),
        ],
        variables=[{"id": "v1", "name": "said", "type": "string", "default": "Oi"}],
    )
    executor = FakeExecutor(
        reports={"ana": lambda task: f"Ana ouviu: {task}", "bia": lambda task: f"Bia: {task}"}
    )

    outcome, _, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    calls = executor.clean_calls
    assert [call["name"] for call in calls] == ["Ana", "Bia", "Ana", "Bia"]
    assert calls[0] == {
        "node_id": "ana",
        "name": "Ana",
        "system": "Pergunte algo.",
        "task": "Oi",
        "model": None,
    }
    assert calls[2]["task"] == "Bia: Ana ouviu: Oi"
    assert outcome.variables["said"] == "Bia: Ana ouviu: Bia: Ana ouviu: Oi"


# -- function nodes ----------------------------------------------------------


async def test_a_counter_grows_across_a_loop_and_formats_the_answer() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("loop", "loop", kind="count", count=3),
            node("read", "var_get", variable="n"),
            node("plus", "math", op="add", type="int", b=1),
            node("keep", "var_set", variable="n"),
            node("again", "var_get", variable="n"),
            node("say", "format", template="contei {0} vezes", inputs=1),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("loop", "in")),
            wire("e2", ("loop", "body"), ("keep", "in")),
            wire("e3", ("read", "value"), ("plus", "a"), "data"),
            wire("e4", ("plus", "result"), ("keep", "value"), "data"),
            wire("e5", ("loop", "completed"), ("end", "in")),
            wire("e6", ("again", "value"), ("say", "arg_0"), "data"),
            wire("e7", ("say", "result"), ("end", "result"), "data"),
        ],
        variables=[{"id": "v1", "name": "n", "type": "int", "default": 0}],
    )

    outcome, _, _ = await run(graph)

    assert outcome.status is RunStatus.COMPLETED
    assert outcome.variables["n"] == 3
    assert outcome.result == "contei 3 vezes"


async def test_a_text_check_steers_a_decision_on_an_agent_report() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("review", "profession", profession="architect", task="review it"),
            node("check", "text_check", op="contains", part="approved"),
            node("gate", "decision", mode="bool"),
            node("yes", "print", label="ok"),
            node("no", "print", label="again"),
        ],
        [
            wire("e1", ("start", "out"), ("review", "in")),
            wire("e2", ("review", "out"), ("gate", "in")),
            wire("e3", ("review", "report"), ("check", "text"), "data"),
            wire("e4", ("check", "result"), ("gate", "condition"), "data"),
            wire("e5", ("gate", "true"), ("yes", "in")),
            wire("e6", ("gate", "false"), ("no", "in")),
        ],
    )
    executor = FakeExecutor(reports={"review": "Verdict: APPROVED"})

    outcome, recorder, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    assert [payload["label"] for payload in recorder.of("graph.print")] == ["ok"]


async def test_append_reads_its_parts_and_a_bad_division_only_warns() -> None:
    graph = doc(
        [
            node("start", "start"),
            node("join", "append", separator=" | ", inputs=3, in_0="a", in_2="c"),
            node("split", "math", op="divide", a=1, b=0),
            node("both", "append", inputs=2),
            node("show", "print"),
            node("end", "end"),
        ],
        [
            wire("e1", ("start", "out"), ("show", "in")),
            wire("e2", ("join", "result"), ("both", "in_0"), "data"),
            wire("e3", ("split", "result"), ("both", "in_1"), "data"),
            wire("e4", ("both", "result"), ("show", "value"), "data"),
            wire("e5", ("show", "out"), ("end", "in")),
        ],
    )

    outcome, recorder, _ = await run(graph)

    assert outcome.status is RunStatus.COMPLETED
    assert recorder.printed() == ["a |  | c0"]
    assert any("division by zero" in item["message"] for item in recorder.of("graph.warning"))
