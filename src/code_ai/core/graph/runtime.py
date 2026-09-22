"""The graph interpreter: walks exec tokens, pulls data, bounds everything.

Shape of the thing, because it is easier to read the code knowing it:

- Exec flow is a LIFO stack of small step descriptors, never recursion. That is
  what lets a Sequence finish one branch entirely before starting the next
  (push the continuation first, then the branch) without a call stack that a
  200-node graph could exhaust.
- Data flow is pull-based and memoised per producer: a node about to run asks
  for its inputs, and a pure node (Model, Get variable) is evaluated on demand.
- Everything an agent actually does happens behind :class:`NodeExecutor`, which
  the application implements. This module therefore needs no provider, no
  sub-agent runtime and no event bus - which is exactly why it can be tested
  with a twenty-line fake.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from code_ai.core.graph.models import NodeKind, PinType
from code_ai.core.graph.program import GraphProgram, NodeProgram

# How the interpreter talks to the outside world: one async callable per event.
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

# Printed values and agent reports are bounded before they travel - a graph that
# prints a whole file must not wedge the editor.
MAX_VALUE_CHARS = 4000
MAX_PREVIEW_CHARS = 280


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class ModelBinding:
    """A Model node, resolved to what a provider needs to be built.

    ``api_key_ref`` is a reference, never a key: the file this came from is
    committed to git. Resolving it is the executor's business.
    """

    model: str = ""
    base_url: str = ""
    api_mode: str = ""
    api_key_ref: str = "session"
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None

    @property
    def empty(self) -> bool:
        return not any((self.model, self.base_url, self.api_mode))

    def label(self) -> str:
        return self.model or self.base_url or "session model"


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    """What one hired agent came back with."""

    status: str  # completed | failed | timeout | cancelled | rejected
    summary: str = ""
    error: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    agent_name: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed"


class NodeExecutor(Protocol):
    """Everything the interpreter cannot do by itself."""

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
    ) -> AgentOutcome: ...

    async def judge(
        self,
        *,
        node_id: str,
        question: str,
        context: str,
        model: ModelBinding | None,
        cancel: asyncio.Event,
    ) -> bool: ...

    async def ask_user(self, *, node_id: str, prompt: str, cancel: asyncio.Event) -> str: ...


@dataclass(slots=True)
class GraphLimits:
    """Hard ceilings. Applied whatever the graph asks for."""

    max_nodes_executed: int = 200
    max_seconds: int = 3600
    max_loop_iterations: int = 50


@dataclass(slots=True)
class GraphRunOutcome:
    run_id: str
    status: RunStatus
    result: Any = None
    error: str = ""
    nodes_executed: int = 0
    seconds: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    printed: list[str] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": str(self.status),
            "result": _preview(self.result),
            "error": self.error,
            "reason": self.reason,
            "nodes_executed": self.nodes_executed,
            "seconds": round(self.seconds, 2),
            "usage": dict(self.usage),
            "variables": {key: _preview(value) for key, value in self.variables.items()},
        }


# -- exec steps -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Visit:
    node_id: str


@dataclass(frozen=True, slots=True)
class _SequenceStep:
    node_id: str
    index: int


@dataclass(frozen=True, slots=True)
class _LoopHead:
    node_id: str
    index: int


_Step = _Visit | _SequenceStep | _LoopHead


class _Stop(Exception):
    """Raised inside the walk to unwind it: End, cancel, or a budget."""

    def __init__(self, status: RunStatus, reason: str = "") -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class GraphInterpreter:
    """One run of one graph."""

    def __init__(
        self,
        *,
        program: GraphProgram,
        executor: NodeExecutor,
        emit: Emit,
        run_id: str,
        limits: GraphLimits | None = None,
        cancel: asyncio.Event | None = None,
        variables: dict[str, Any] | None = None,
    ) -> None:
        self._program = program
        self._executor = executor
        self._emit = emit
        self._run_id = run_id
        self._limits = limits or GraphLimits()
        self._cancel = cancel or asyncio.Event()
        self._vars: dict[str, Any] = program.initial_variables()
        for name, value in (variables or {}).items():
            if name in self._vars:
                self._vars[name] = self._coerce_variable(name, value)
        # Values produced by nodes that have run, keyed (node id, pin).
        self._outputs: dict[tuple[str, str], Any] = {}
        # Current lap of each loop, so a body node pulling `index` sees its own.
        self._loops: dict[str, tuple[int, str]] = {}
        self._printed: list[str] = []
        self._usage: dict[str, int] = {}
        self._executed = 0
        self._deadline = 0.0
        self._result: Any = None
        self._last_report = ""

    @property
    def cancel_event(self) -> asyncio.Event:
        return self._cancel

    def request_stop(self) -> None:
        self._cancel.set()

    async def run(self) -> GraphRunOutcome:
        started = time.monotonic()
        self._deadline = started + self._limits.max_seconds
        await self._emit(
            "graph.run.started",
            {
                "run_id": self._run_id,
                "name": self._program.name,
                "node_count": len(self._program.nodes),
                "variables": {k: _preview(v) for k, v in self._vars.items()},
            },
        )
        status = RunStatus.COMPLETED
        reason = ""
        error = ""
        try:
            await self._walk()
        except _Stop as stop:
            status = stop.status
            reason = stop.reason
            if status is RunStatus.FAILED:
                error = stop.reason
        except asyncio.CancelledError:
            status = RunStatus.STOPPED
            reason = "cancelled"
            raise
        except Exception as exc:  # degrade: a broken run reports, never crashes the bridge
            status = RunStatus.FAILED
            error = str(exc) or type(exc).__name__
            reason = error
        finally:
            outcome = GraphRunOutcome(
                run_id=self._run_id,
                status=status,
                result=self._result,
                error=error,
                reason=reason,
                nodes_executed=self._executed,
                seconds=time.monotonic() - started,
                usage=dict(self._usage),
                printed=list(self._printed),
                variables=dict(self._vars),
            )
            await self._emit("graph.run.completed", outcome.payload())
        return outcome

    # -- the walk ---------------------------------------------------------

    async def _walk(self) -> None:
        start = self._program.node(self._program.start_id)
        if start is None:
            raise _Stop(RunStatus.FAILED, "The graph has no Start node.")
        # Start is walked into like any other node, so the editor lights it up
        # and the node counter agrees with what the user sees.
        stack: list[_Step] = [_Visit(start.id)]

        while stack:
            self._checkpoint()
            step = stack.pop()
            match step:
                case _Visit(node_id=node_id):
                    node = self._program.node(node_id)
                    if node is None:
                        continue
                    await self._execute(node, stack)
                case _SequenceStep(node_id=node_id, index=index):
                    await self._sequence_step(node_id, index, stack)
                case _LoopHead(node_id=node_id, index=index):
                    await self._loop_head(node_id, index, stack)

    def _checkpoint(self) -> None:
        if self._cancel.is_set():
            raise _Stop(RunStatus.STOPPED, "stopped by user")
        if self._executed >= self._limits.max_nodes_executed:
            raise _Stop(
                RunStatus.EXHAUSTED,
                f"reached the node ceiling ({self._limits.max_nodes_executed})",
            )
        if time.monotonic() > self._deadline:
            raise _Stop(
                RunStatus.EXHAUSTED, f"reached the time ceiling ({self._limits.max_seconds}s)"
            )

    async def _enter(self, node: NodeProgram, pin: str, stack: list[_Step]) -> None:
        """Follow one exec output, lighting its wire on the way."""

        wired = node.exec_out.get(pin)
        if wired is None:
            return
        target, edge_id = wired
        await self._emit(
            "graph.edge.active",
            {
                "run_id": self._run_id,
                "edge_id": edge_id,
                "kind": "exec",
                "from_node": node.id,
                "from_pin": pin,
                "to_node": target,
            },
        )
        stack.append(_Visit(target))

    async def _execute(self, node: NodeProgram, stack: list[_Step]) -> None:
        self._executed += 1
        started = time.monotonic()
        await self._emit(
            "graph.node.started",
            {
                "run_id": self._run_id,
                "node_id": node.id,
                "kind": node.kind,
                "label": node.label,
                "index": self._executed,
            },
        )
        try:
            summary = await self._dispatch(node, stack)
        except _Stop as stop:
            # End is a normal completion, so the node still reports as done
            # before the walk unwinds - the editor must not leave it spinning.
            if stop.status is RunStatus.COMPLETED:
                await self._emit(
                    "graph.node.completed",
                    {
                        "run_id": self._run_id,
                        "node_id": node.id,
                        "kind": node.kind,
                        "status": "completed",
                        "summary": _preview(self._result),
                        "seconds": round(time.monotonic() - started, 2),
                    },
                )
            else:
                await self._emit(
                    "graph.node.failed",
                    {"run_id": self._run_id, "node_id": node.id, "error": stop.reason},
                )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            await self._emit(
                "graph.node.failed",
                {"run_id": self._run_id, "node_id": node.id, "error": message},
            )
            raise _Stop(RunStatus.FAILED, f"{node.label}: {message}") from exc

        await self._emit(
            "graph.node.completed",
            {
                "run_id": self._run_id,
                "node_id": node.id,
                "kind": node.kind,
                "status": "completed",
                "summary": _preview(summary),
                "seconds": round(time.monotonic() - started, 2),
            },
        )

    async def _dispatch(self, node: NodeProgram, stack: list[_Step]) -> str:
        match node.kind:
            case NodeKind.START:
                await self._enter(node, "out", stack)
                return ""
            case NodeKind.END:
                self._result = await self._pull(node, "result")
                raise _Stop(RunStatus.COMPLETED, "reached End")
            case NodeKind.PROFESSION:
                return await self._run_profession(node, stack)
            case NodeKind.PRINT:
                return await self._run_print(node, stack)
            case NodeKind.SEQUENCE:
                stack.append(_SequenceStep(node.id, 0))
                return f"{len(node.ordered_exec)} branches"
            case NodeKind.DECISION:
                return await self._run_decision(node, stack)
            case NodeKind.LOOP:
                stack.append(_LoopHead(node.id, 0))
                return str(node.data.get("kind") or "count")
            case NodeKind.USER_CHAT:
                return await self._run_user_chat(node, stack)
            case NodeKind.VAR_SET:
                return await self._run_var_set(node, stack)
            case _:
                # A pure node reached by an exec wire cannot happen (the
                # compiler refuses the wire), so this is a genuine bug.
                raise RuntimeError(f"{node.kind} is not executable")

    # -- node kinds -------------------------------------------------------

    async def _run_profession(self, node: NodeProgram, stack: list[_Step]) -> str:
        task = _as_text(await self._pull(node, "task"))
        model = await self._pull(node, "model")
        outcome = await self._race(
            self._executor.run_agent(
                node_id=node.id,
                profession=str(node.data.get("profession") or ""),
                task=task,
                extra_instructions=str(node.data.get("role_override") or ""),
                model=model if isinstance(model, ModelBinding) else None,
                max_steps=_as_int(node.data.get("max_steps"), 0),
                timeout_s=_as_int(node.data.get("timeout_s"), 0),
                cancel=self._cancel,
            )
        )
        for key, value in (outcome.usage or {}).items():
            self._usage[key] = self._usage.get(key, 0) + int(value)
        if outcome.status == "cancelled":
            # An agent that unwound because the run was stopped is not a
            # failure: the user asked for this.
            raise _Stop(RunStatus.STOPPED, "stopped by user")
        if not outcome.ok:
            detail = outcome.error or outcome.status
            raise _Stop(RunStatus.FAILED, f"{node.label} ({outcome.status}): {detail}")
        report = _bound(outcome.summary, MAX_VALUE_CHARS)
        self._outputs[(node.id, "report")] = report
        self._last_report = report
        await self._enter(node, "out", stack)
        return report

    async def _run_print(self, node: NodeProgram, stack: list[_Step]) -> str:
        value = await self._pull(node, "value")
        label = str(node.data.get("label") or "").strip()
        text = _bound(_as_text(value), MAX_VALUE_CHARS)
        self._printed.append(f"{label}: {text}" if label else text)
        await self._emit(
            "graph.print",
            {"run_id": self._run_id, "node_id": node.id, "label": label, "text": text},
        )
        await self._enter(node, "out", stack)
        return text

    async def _run_decision(self, node: NodeProgram, stack: list[_Step]) -> str:
        mode = str(node.data.get("mode") or "bool")
        if mode == "judge":
            question = _as_text(await self._pull(node, "prompt"))
            model = await self._pull(node, "model")
            verdict = await self._race(
                self._executor.judge(
                    node_id=node.id,
                    question=question,
                    context=self._judge_context(),
                    model=model if isinstance(model, ModelBinding) else None,
                    cancel=self._cancel,
                )
            )
        else:
            verdict = _as_bool(await self._pull(node, "condition"))
        await self._emit(
            "graph.decision",
            {"run_id": self._run_id, "node_id": node.id, "mode": mode, "verdict": verdict},
        )
        await self._enter(node, "true" if verdict else "false", stack)
        return "true" if verdict else "false"

    async def _run_user_chat(self, node: NodeProgram, stack: list[_Step]) -> str:
        prompt = _as_text(await self._pull(node, "prompt"))
        answer = await self._race(
            self._executor.ask_user(node_id=node.id, prompt=prompt, cancel=self._cancel)
        )
        text = _bound(str(answer or ""), MAX_VALUE_CHARS)
        self._outputs[(node.id, "answer")] = text
        await self._enter(node, "out", stack)
        return text

    async def _run_var_set(self, node: NodeProgram, stack: list[_Step]) -> str:
        name = str(node.data.get("variable") or "").strip()
        value = self._coerce_variable(name, await self._pull(node, "value"))
        self._vars[name] = value
        await self._emit(
            "graph.variable.changed",
            {"run_id": self._run_id, "node_id": node.id, "name": name, "value": _preview(value)},
        )
        await self._enter(node, "out", stack)
        return f"{name} = {_preview(value)}"

    async def _sequence_step(self, node_id: str, index: int, stack: list[_Step]) -> None:
        node = self._program.node(node_id)
        if node is None or index >= len(node.ordered_exec):
            return
        # The continuation goes on first: LIFO then guarantees this branch runs
        # to its end before the next one starts.
        stack.append(_SequenceStep(node_id, index + 1))
        await self._enter(node, node.ordered_exec[index], stack)

    async def _loop_head(self, node_id: str, index: int, stack: list[_Step]) -> None:
        node = self._program.node(node_id)
        if node is None:
            return
        kind = str(node.data.get("kind") or "count")
        cap = _as_int(node.data.get("max_iterations"), 0) or self._limits.max_loop_iterations
        cap = min(cap, self._limits.max_loop_iterations)

        item = ""
        if index >= cap:
            await self._emit(
                "graph.loop.capped",
                {"run_id": self._run_id, "node_id": node_id, "cap": cap},
            )
            keep_going = False
        elif kind == "count":
            total = _as_int(await self._pull(node, "count"), 0)
            keep_going = index < total
        elif kind == "while":
            keep_going = _as_bool(await self._pull(node, "condition"))
        else:
            items = _lines(_as_text(await self._pull(node, "items")))
            keep_going = index < len(items)
            if keep_going:
                item = items[index]

        if not keep_going:
            self._loops.pop(node_id, None)
            await self._enter(node, "completed", stack)
            return

        self._loops[node_id] = (index, item)
        self._outputs[(node_id, "index")] = index
        self._outputs[(node_id, "item")] = item
        await self._emit(
            "graph.loop.iteration",
            {"run_id": self._run_id, "node_id": node_id, "index": index, "item": _preview(item)},
        )
        stack.append(_LoopHead(node_id, index + 1))
        await self._enter(node, "body", stack)

    # -- data -------------------------------------------------------------

    async def _pull(self, node: NodeProgram, pin: str) -> Any:
        """The value on one data input: the wire if there is one, else the literal."""

        wired = node.data_in.get(pin)
        if wired is None:
            return self._coerce_pin(node, pin, node.literal(pin))
        source_id, source_pin, edge_id = wired
        await self._emit(
            "graph.edge.active",
            {
                "run_id": self._run_id,
                "edge_id": edge_id,
                "kind": "data",
                "from_node": source_id,
                "from_pin": source_pin,
                "to_node": node.id,
            },
        )
        value = await self._value_of(source_id, source_pin)
        return self._coerce_pin(node, pin, value)

    async def _value_of(self, node_id: str, pin: str) -> Any:
        cached = self._outputs.get((node_id, pin))
        if cached is not None:
            return cached
        node = self._program.node(node_id)
        if node is None:
            return None
        if node.kind == NodeKind.MODEL:
            binding = _binding_from(node)
            self._outputs[(node_id, pin)] = binding
            return binding
        if node.kind == NodeKind.VAR_GET:
            name = str(node.data.get("variable") or "").strip()
            return self._vars.get(name)
        if node.kind == NodeKind.LOOP:
            index, item = self._loops.get(node_id, (0, ""))
            return index if pin == "index" else item
        # An impure producer that has not run yet. Legal to author (a report
        # read before its agent ran), so it degrades to empty and says so.
        await self._emit(
            "graph.warning",
            {
                "run_id": self._run_id,
                "node_id": node_id,
                "message": f"{node.label} has not produced {pin!r} yet; reading it as empty.",
            },
        )
        return None

    def _coerce_pin(self, node: NodeProgram, pin: str, value: Any) -> Any:
        spec = node.spec.pin(pin, "in")
        if spec is None:
            return value
        return _coerce(value, str(spec.type))

    def _coerce_variable(self, name: str, value: Any) -> Any:
        declared = self._program.variables.get(name)
        return _coerce(value, str(declared.type)) if declared is not None else value

    def _judge_context(self) -> str:
        """What a judge decision is allowed to look at: the last report and the vars."""

        pieces: list[str] = []
        if self._last_report:
            pieces.append(f"Last agent report:\n{_bound(self._last_report, 2000)}")
        if self._vars:
            rendered = "\n".join(
                f"- {key} = {_preview(value)}" for key, value in self._vars.items()
            )
            pieces.append(f"Variables:\n{rendered}")
        if self._printed:
            pieces.append("Printed:\n" + "\n".join(self._printed[-5:]))
        return "\n\n".join(pieces)

    async def _race(self, awaitable: Awaitable[Any]) -> Any:
        """Await something, but never past a stop request.

        The executor also receives the cancel event and is expected to unwind
        cooperatively; this is the backstop for the parts that cannot, such as a
        node parked on a human answer.
        """

        task = asyncio.ensure_future(awaitable)
        stop = asyncio.ensure_future(self._cancel.wait())
        try:
            done, _ = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
            # The stop wins a tie. Both settle at once when a cancelled agent
            # returns in the same tick the flag was set, and reading that as the
            # agent's own answer would report a stop as whatever it came back
            # with.
            if self._cancel.is_set():
                raise _Stop(RunStatus.STOPPED, "stopped by user")
            if task in done:
                return task.result()
            raise _Stop(RunStatus.STOPPED, "stopped by user")
        finally:
            for pending in (task, stop):
                if not pending.done():
                    pending.cancel()
            # Let a cancelled agent settle so its own cleanup runs.
            if task.cancelled() or not task.done():
                await asyncio.gather(task, return_exceptions=True)
            stop.cancel()


# -- value helpers --------------------------------------------------------


def _binding_from(node: NodeProgram) -> ModelBinding:
    return ModelBinding(
        model=str(node.data.get("model") or "").strip(),
        base_url=str(node.data.get("base_url") or "").strip(),
        api_mode=str(node.data.get("api_mode") or "").strip(),
        api_key_ref=str(node.data.get("api_key_ref") or "session").strip(),
        temperature=_as_opt_float(node.data.get("temperature")),
        top_p=_as_opt_float(node.data.get("top_p")),
        top_k=_as_opt_int(node.data.get("top_k")),
    )


def _coerce(value: Any, pin_type: str) -> Any:
    if value is None:
        return None
    match pin_type:
        case PinType.STRING:
            return _as_text(value)
        case PinType.INT:
            return _as_int(value, 0)
        case PinType.FLOAT:
            return _as_opt_float(value) or 0.0
        case PinType.BOOL:
            return _as_bool(value)
        case _:
            return value


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, ModelBinding):
        return value.label()
    if isinstance(value, str):
        return value
    return str(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        return value != 0
    text = str(value).strip().lower()
    return text in ("true", "1", "yes", "y", "sim", "on")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _bound(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _preview(value: Any) -> str:
    return _bound(_as_text(value), MAX_PREVIEW_CHARS)
