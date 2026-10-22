"""Works a task board with sub-agents: plans tasks, runs their sub-tasks, checks the result.

Every agent is a sub-agent from the session's runtime, so they run side by side
under one limit (``max_concurrent_subagents``). A task's sub-tasks run as soon
as what they depend on is done; tasks run as soon as the tasks they depend on
are. A finished task is checked against its criteria and checklist by a
reviewer, gets one round of fixes when it fails, and lands in Done when it
passes.

The runner never holds a task across an await: the board is replaced on every
edit the user makes, so it looks tasks up by id inside ``mutate`` each time.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from code_ai.core.board import prompts
from code_ai.core.board.models import (
    CRITERION_FAILED,
    CRITERION_PASSED,
    ROLE_DOING,
    ROLE_DONE,
    SUB_BLOCKED,
    SUB_DONE,
    SUB_FAILED,
    SUB_PENDING,
    SUB_RUNNING,
    SUB_STOPPED,
    TASK_DONE,
    TASK_DRAFT,
    TASK_FAILED,
    TASK_PLANNING,
    TASK_QUEUED,
    TASK_READY,
    TASK_RUNNING,
    TASK_STOPPED,
    TASK_VERIFYING,
    Board,
    CheckItem,
    Criterion,
    SubTask,
    Task,
)
from code_ai.core.orchestration import TurnResult
from code_ai.core.subagents.coordinator import Pulse
from code_ai.core.subagents.naming import generate_agent_name
from code_ai.core.subagents.profiles import SubagentProfileRegistry, default_profile_registry
from code_ai.core.subagents.runtime import SubagentRuntime
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import ImageContent
from code_ai.tools.output import bound_text

logger = logging.getLogger(__name__)

# How long past its budget a silent agent gets before it is stopped.
_GRACE_S = 30.0

# How long a stopped agent gets to wind down by itself before it is cancelled.
_STOP_GRACE_S = 2.0

# Rounds of "fix what the check found" before a task is left failed.
FIX_ROUNDS = 1

# What an agent writes is passed on this often, as its last words, this long.
_SAY_EVERY_S = 1.0
_SAY_CHARS = 180

# Pictures attached to a task go with every request about it, up to these.
_MAX_IMAGES = 4
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

_TOOL_STATES = {
    "tool.call.started": "started",
    "tool.call.completed": "done",
    "tool.call.failed": "failed",
}

Mutate = Callable[..., Awaitable[None]]
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class Hired:
    """How one agent's turn ended."""

    status: str
    text: str = ""
    error: str = ""
    agent: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed"


class BoardRunner:
    """One run or one planning pass over a board."""

    def __init__(
        self,
        *,
        runtime: SubagentRuntime,
        mutate: Mutate,
        read: Callable[[], Board],
        emit: Emit,
        max_parallel: int,
        profiles: SubagentProfileRegistry | None = None,
        run_id: str = "",
    ) -> None:
        self._runtime = runtime
        self._mutate = mutate
        self._read = read
        self._emit = emit
        self._profiles = profiles or default_profile_registry()
        self._slots = asyncio.Semaphore(max(1, max_parallel))
        self._cancel = asyncio.Event()
        self._names: set[str] = set()
        self._finished: dict[str, asyncio.Event] = {}
        self._outcome: dict[str, str] = {}
        self._last_activity: dict[str, float] = {}
        self.run_id = run_id

    def stop(self) -> None:
        self._cancel.set()

    @property
    def stopped(self) -> bool:
        return self._cancel.is_set()

    # -- planning ---------------------------------------------------------

    async def plan(self, task_ids: list[str]) -> dict[str, str]:
        """Break each task into sub-tasks, all at once; the user confirms before anything runs."""

        await self._mutate(lambda board: _set_status(board, task_ids, TASK_PLANNING))
        results = await asyncio.gather(*(self._plan_one(task_id) for task_id in task_ids))
        return dict(zip(task_ids, results, strict=True))

    async def _plan_one(self, task_id: str) -> str:
        board = self._read()
        task = board.task(task_id)
        if task is None:
            return "gone"
        prompt = prompts.planner_prompt(board, task)
        hired = await self._hire("explorer", prompt, task_id, None, activity="planning")
        plan = prompts.parse_plan(hired.text) if hired.ok else None

        def land(board: Board) -> None:
            task = board.task(task_id)
            if task is None:
                return
            if plan is None:
                task.status = TASK_READY if task.subtasks else TASK_DRAFT
                task.error = (
                    hired.error
                    or "The planner did not answer with sub-tasks. Plan it again, or add them."
                )
                if hired.status == "cancelled":
                    task.error = ""
            else:
                _merge_plan(board, task, plan)
                task.status = TASK_READY
                task.error = ""
            task.activity = ""

        await self._mutate(land)
        return "planned" if plan is not None else hired.status

    # -- running ------------------------------------------------------------

    async def run(self, task_ids: list[str]) -> dict[str, str]:
        for task_id in task_ids:
            self._finished[task_id] = asyncio.Event()

        def queue(board: Board) -> None:
            for task_id in task_ids:
                task = board.task(task_id)
                if task is None:
                    continue
                task.status = TASK_QUEUED
                task.error = ""
                for sub in task.subtasks:
                    if sub.status != SUB_DONE:
                        sub.status = SUB_PENDING
                        sub.error = ""
                        sub.activity = ""
                        sub.started_at = sub.finished_at = 0.0

        await self._mutate(queue)
        await asyncio.gather(*(self._run_task(task_id) for task_id in task_ids))
        return dict(self._outcome)

    async def _run_task(self, task_id: str) -> None:
        try:
            outcome = await self._work_task(task_id)
        except asyncio.CancelledError:
            outcome = TASK_STOPPED
            raise
        except Exception as exc:  # one broken task never takes the run down
            logger.exception("Board task %s failed", task_id)
            outcome = TASK_FAILED
            await self._finish(task_id, TASK_FAILED, error=str(exc) or type(exc).__name__)
        finally:
            self._outcome[task_id] = outcome
            self._finished[task_id].set()

    async def _work_task(self, task_id: str) -> str:
        task = self._read().task(task_id)
        if task is None:
            return "gone"
        for dependency in list(task.depends_on):
            problem = await self._wait_for_task(dependency)
            if problem:
                await self._finish(task_id, TASK_FAILED, error=problem)
                return TASK_FAILED
        if self.stopped:
            await self._finish(task_id, TASK_STOPPED)
            return TASK_STOPPED

        task = self._read().task(task_id)
        if task is None:
            return "gone"
        if task.subtasks:
            worked = await self._run_subtasks(task_id)
        else:
            worked = await self._run_whole(task_id)
        if self.stopped:
            await self._finish(task_id, TASK_STOPPED)
            return TASK_STOPPED
        if not worked:
            await self._finish(task_id, TASK_FAILED, error=self._first_error(task_id))
            return TASK_FAILED
        return await self._verify(task_id)

    async def _wait_for_task(self, dependency: str) -> str:
        """Why the dependent cannot start, or "" once ``dependency`` is done."""

        event = self._finished.get(dependency)
        if event is not None:
            await event.wait()
            if self._outcome.get(dependency) == TASK_DONE:
                return ""
            return f"It waits for {dependency}, which did not finish."
        other = self._read().task(dependency)
        if other is None or other.status == TASK_DONE:
            return ""
        return f"It waits for {dependency}, which is not done and not in this run."

    async def _run_subtasks(self, task_id: str) -> bool:
        task = self._read().task(task_id)
        assert task is not None
        finished = {sub.id: asyncio.Event() for sub in task.subtasks}
        ok: dict[str, bool] = {}
        for sub in task.subtasks:
            if sub.status == SUB_DONE:
                ok[sub.id] = True
                finished[sub.id].set()

        async def run_one(sub_id: str, depends_on: list[str]) -> None:
            try:
                for dependency in depends_on:
                    if dependency not in finished:
                        continue
                    await finished[dependency].wait()
                    if not ok.get(dependency):
                        ok[sub_id] = False
                        await self._set_sub(
                            task_id, sub_id, SUB_BLOCKED, error=f"{dependency} did not finish."
                        )
                        return
                if self.stopped:
                    ok[sub_id] = False
                    return
                ok[sub_id] = await self._run_sub(task_id, sub_id)
            finally:
                finished[sub_id].set()

        await asyncio.gather(
            *(
                run_one(sub.id, list(sub.depends_on))
                for sub in task.subtasks
                if sub.status != SUB_DONE
            )
        )
        return all(ok.values())

    async def _run_sub(self, task_id: str, sub_id: str) -> bool:
        async with self._slots:
            if self.stopped:
                return False
            await self._start_task(task_id)
            board = self._read()
            task = board.task(task_id)
            sub = task.subtask(sub_id) if task else None
            if task is None or sub is None:
                return False
            prompt = prompts.worker_prompt(task, sub)
            agent = self._new_name()
            await self._set_sub(task_id, sub_id, SUB_RUNNING, agent=agent)
            hired = await self._hire("coder", prompt, task_id, sub_id, agent=agent)
        if hired.status == "cancelled":
            await self._set_sub(task_id, sub_id, SUB_STOPPED)
            return False
        if not hired.ok:
            await self._set_sub(task_id, sub_id, SUB_FAILED, error=hired.error, report=hired.text)
            return False
        await self._set_sub(task_id, sub_id, SUB_DONE, report=hired.text)
        return True

    async def _run_whole(self, task_id: str) -> bool:
        """A task nobody broke down is one agent's job."""

        async with self._slots:
            if self.stopped:
                return False
            await self._start_task(task_id)
            task = self._read().task(task_id)
            if task is None:
                return False
            hired = await self._hire("coder", prompts.worker_prompt(task, None), task_id, None)

        def land(board: Board) -> None:
            task = board.task(task_id)
            if task is not None:
                task.report = prompts.clip_report(hired.text)
                task.error = "" if hired.ok else hired.error

        await self._mutate(land)
        return hired.ok

    async def _verify(self, task_id: str) -> str:
        for round_number in range(FIX_ROUNDS + 1):
            task = self._read().task(task_id)
            if task is None:
                return "gone"
            if not prompts.needs_verification(task):
                break
            await self._set_task(task_id, TASK_VERIFYING)
            async with self._slots:
                if self.stopped:
                    break
                hired = await self._hire(
                    "reviewer", prompts.verifier_prompt(task), task_id, None, activity="checking"
                )
            if self.stopped or hired.status == "cancelled":
                await self._finish(task_id, TASK_STOPPED)
                return TASK_STOPPED
            verdict = (
                prompts.parse_verdict(hired.text)
                if hired.ok
                else prompts.Verdict({}, {}, unreadable=hired.error or "The check did not finish.")
            )
            await self._mutate(partial(_record_verdict, task_id=task_id, verdict=verdict))
            task = self._read().task(task_id)
            if task is None:
                return "gone"
            if verdict.passed(task):
                break
            if round_number == FIX_ROUNDS:
                await self._finish(task_id, TASK_FAILED, error=verdict.findings(task))
                return TASK_FAILED
            await self._set_task(task_id, TASK_RUNNING)
            async with self._slots:
                if self.stopped:
                    break
                fixed = await self._hire(
                    "coder", prompts.fix_prompt(task, verdict), task_id, None, activity="fixing"
                )
            if not fixed.ok:
                await self._finish(
                    task_id,
                    TASK_STOPPED if fixed.status == "cancelled" else TASK_FAILED,
                    error="" if fixed.status == "cancelled" else fixed.error,
                )
                return TASK_STOPPED if fixed.status == "cancelled" else TASK_FAILED
        if self.stopped:
            await self._finish(task_id, TASK_STOPPED)
            return TASK_STOPPED
        await self._finish(task_id, TASK_DONE)
        return TASK_DONE

    # -- agents -------------------------------------------------------------

    async def _hire(
        self,
        profile_name: str,
        prompt: str,
        task_id: str,
        sub_id: str | None,
        *,
        agent: str = "",
        activity: str = "",
    ) -> Hired:
        profile = self._profiles.get(profile_name) or default_profile_registry().get(profile_name)
        assert profile is not None, profile_name
        agent = agent or self._new_name()
        built = self._runtime.build(profile)
        pulse = Pulse()
        watch = built.event_bus.subscribe(pulse.beat)
        forward = built.event_bus.subscribe(self._forwarder(task_id, sub_id, agent))
        if activity:
            await self._activity(task_id, sub_id, agent, kind="phase", text=activity)
        child_cancel = asyncio.Event()
        images = await asyncio.to_thread(_attached_images, self._read().task(task_id))
        turn = asyncio.ensure_future(
            built.orchestrator.run_turn(prompt, cancel_event=child_cancel, images=images or None)
        )
        watching = asyncio.ensure_future(
            pulse.watch(turn, quiet_for=built.timeout_seconds + _GRACE_S)
        )
        stopping = asyncio.ensure_future(self._cancel.wait())
        try:
            await asyncio.wait({watching, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if not watching.done():
                # A turn looks at its cancel flag between model events, and a
                # model thinking in silence sends none: after a moment it is
                # cancelled outright, which also closes the connection.
                child_cancel.set()
                finished, _ = await asyncio.wait({turn}, timeout=_STOP_GRACE_S)
                if not finished:
                    turn.cancel()
                    await asyncio.wait({turn}, timeout=_GRACE_S)
                return Hired("cancelled", agent=agent)
            result: TurnResult = watching.result()
        except TimeoutError:
            child_cancel.set()
            turn.cancel()
            await asyncio.wait({turn}, timeout=_GRACE_S)
            return Hired("timeout", error=f"{agent} went silent and was stopped.", agent=agent)
        except Exception as exc:  # a broken agent fails its piece, not the run
            logger.exception("Board agent %s failed", agent)
            return Hired("failed", error=str(exc) or type(exc).__name__, agent=agent)
        finally:
            for pending in (turn, watching, stopping):
                if not pending.done():
                    pending.cancel()
            built.event_bus.unsubscribe(watch)
            built.event_bus.unsubscribe(forward)
            self._names.discard(agent)
        text = result.text or ""
        if result.cancelled or self.stopped:
            return Hired("cancelled", text=text, agent=agent)
        if result.error is not None:
            return Hired("failed", text=text, error=result.error, agent=agent)
        if result.wind_down_reason is not None:
            reason = result.wind_down_reason.replace("_", " ")
            return Hired(
                "failed", text=text, error=f"{agent} stopped early: {reason}.", agent=agent
            )
        return Hired("completed", text=text, agent=agent)

    def _new_name(self) -> str:
        name = generate_agent_name(exclude=self._names)
        self._names.add(name)
        return name

    def _forwarder(self, task_id: str, sub_id: str | None, agent: str):
        """Each step an agent takes, as the board shows it: the tool, what on, how it ended."""

        said: list[str] = []

        async def handler(envelope: EventEnvelope) -> None:
            payload = envelope.payload or {}
            kind = envelope.event_type
            if kind == "model.stream.delta":
                text = payload.get("text")
                if isinstance(text, str) and text:
                    said.append(text)
                    await self._say(task_id, sub_id, agent, said)
                return
            state = _TOOL_STATES.get(kind)
            if state is None:
                return
            if state == "started":
                # A new step: what it said before belongs to the step before.
                said.clear()
            await self._activity(
                task_id,
                sub_id,
                agent,
                kind="tool",
                state=state,
                tool=str(payload.get("name") or ""),
                target=bound_text(str(payload.get("target") or ""), 120),
                call=str(payload.get("tool_call_id") or ""),
            )

        return handler

    async def _say(self, task_id: str, sub_id: str | None, agent: str, said: list[str]) -> None:
        """What the agent is writing, in bursts: its last sentence, on one line."""

        now = time.monotonic()
        key = f"say:{task_id}/{sub_id or ''}"
        if now - self._last_activity.get(key, 0.0) < _SAY_EVERY_S:
            return
        self._last_activity[key] = now
        line = " ".join("".join(said).split())
        if not line:
            return
        await self._activity(task_id, sub_id, agent, kind="say", text=line[-_SAY_CHARS:])

    async def _activity(
        self,
        task_id: str,
        sub_id: str | None,
        agent: str,
        *,
        kind: str,
        state: str = "",
        tool: str = "",
        target: str = "",
        text: str = "",
        call: str = "",
    ) -> None:
        line = f"{tool} {target}".strip() if kind == "tool" else text
        card_line = bound_text(f"{agent}: {line}", 160)

        def note(board: Board) -> None:
            task = board.task(task_id)
            if task is None:
                return
            if kind != "say":
                task.activity = card_line
            sub = task.subtask(sub_id) if sub_id else None
            if sub is not None and kind != "say":
                sub.activity = bound_text(line, 160)

        await self._mutate(note, persist=False, quiet=True)
        await self._emit(
            "board.activity",
            {
                "run_id": self.run_id,
                "task": task_id,
                "subtask": sub_id or "",
                "agent": agent,
                "kind": kind,
                "state": state,
                "tool": tool,
                "target": target,
                "call": call,
                "text": card_line,
                "at": time.time(),
            },
        )

    # -- board changes ------------------------------------------------------

    async def _start_task(self, task_id: str) -> None:
        """The first agent on a task moves its card to In Progress."""

        def start(board: Board) -> None:
            task = board.task(task_id)
            if task is None:
                return
            if task.status in (TASK_QUEUED, TASK_STOPPED, TASK_FAILED, TASK_READY):
                task.status = TASK_RUNNING
            doing = board.lane_for(ROLE_DOING)
            if task.lane != doing.id:
                task.started_at = time.time()
                task.finished_at = 0.0
                _move(board, task, doing.id, to_top=False)

        await self._mutate(start)

    async def _set_task(self, task_id: str, status: str) -> None:
        def update(board: Board) -> None:
            task = board.task(task_id)
            if task is not None:
                task.status = status

        await self._mutate(update)

    async def _set_sub(
        self,
        task_id: str,
        sub_id: str,
        status: str,
        *,
        agent: str | None = None,
        report: str | None = None,
        error: str | None = None,
    ) -> None:
        def update(board: Board) -> None:
            task = board.task(task_id)
            sub = task.subtask(sub_id) if task else None
            if sub is None:
                return
            sub.status = status
            if status == SUB_RUNNING:
                sub.started_at = time.time()
                sub.finished_at = 0.0
            else:
                sub.finished_at = time.time()
                sub.activity = ""
            if agent is not None:
                sub.agent = agent
            if report is not None:
                sub.report = prompts.clip_report(report)
            if error is not None:
                sub.error = error

        await self._mutate(update)

    async def _finish(self, task_id: str, status: str, *, error: str = "") -> None:
        def finish(board: Board) -> None:
            task = board.task(task_id)
            if task is None:
                return
            task.activity = ""
            task.finished_at = time.time()
            if status == TASK_DONE:
                task.status = TASK_DONE
                task.error = ""
                _move(board, task, board.lane_for(ROLE_DONE).id, to_top=True)
                return
            if status == TASK_STOPPED and board.role_of(task) != ROLE_DOING:
                # Stopped before any agent picked it up: it waits again in To Do.
                task.status = TASK_READY if task.subtasks else TASK_DRAFT
                return
            task.status = status
            if error:
                task.error = error

        await self._mutate(finish)

    def _first_error(self, task_id: str) -> str:
        task = self._read().task(task_id)
        if task is None:
            return ""
        for sub in task.subtasks:
            if sub.status in (SUB_FAILED, SUB_BLOCKED) and sub.error:
                return f"{sub.id}: {sub.error}"
        return task.error or "A sub-task did not finish."


# -- helpers --------------------------------------------------------------------


def _set_status(board: Board, task_ids: list[str], status: str) -> None:
    for task_id in task_ids:
        task = board.task(task_id)
        if task is not None:
            task.status = status
            task.error = ""


def _move(board: Board, task: Task, lane_id: str, *, to_top: bool) -> None:
    board.tasks.remove(task)
    task.lane = lane_id
    positions = [index for index, other in enumerate(board.tasks) if other.lane == lane_id]
    if to_top and positions:
        board.tasks.insert(positions[0], task)
    elif positions:
        board.tasks.insert(positions[-1] + 1, task)
    else:
        board.tasks.append(task)


def _merge_plan(board: Board, task: Task, plan: prompts.Plan) -> None:
    """Add what the planner proposed, keeping everything the user already wrote."""

    by_title = {sub.title.strip().lower(): sub for sub in task.subtasks}
    placed: list[SubTask] = []
    for planned in plan.subtasks:
        existing = by_title.get(planned.title.strip().lower())
        if existing is None:
            existing = SubTask(
                id=task.new_subtask_id(), title=planned.title, description=planned.description
            )
            task.subtasks.append(existing)
            by_title[planned.title.strip().lower()] = existing
        elif planned.description and not existing.description:
            existing.description = planned.description
        _extend(board, existing.checklist, planned.checklist, "c")
        _extend(board, existing.criteria, planned.criteria, "v")
        placed.append(existing)
    for planned, sub in zip(plan.subtasks, placed, strict=True):
        for place in planned.depends_on:
            if 1 <= place <= len(placed):
                other = placed[place - 1]
                if other is not sub and other.id not in sub.depends_on:
                    sub.depends_on.append(other.id)
    _drop_cycles(task)
    _extend(board, task.checklist, plan.checklist, "c")
    _extend(board, task.criteria, plan.criteria, "v")


def _extend(board: Board, items: list[Any], texts: list[str], prefix: str) -> None:
    known = {item.text.strip().lower() for item in items}
    for text in texts:
        if text.strip().lower() in known:
            continue
        known.add(text.strip().lower())
        if prefix == "c":
            items.append(CheckItem(board.new_item_id("c"), text[:200]))
        else:
            items.append(Criterion(board.new_item_id("v"), text[:200]))


def _drop_cycles(task: Task) -> None:
    """A planner that wrote a loop loses the dependency that closes it."""

    order = {sub.id: index for index, sub in enumerate(task.subtasks)}
    for sub in task.subtasks:
        # Only earlier pieces may be waited on: that can never loop.
        sub.depends_on = [ref for ref in sub.depends_on if order.get(ref, 1 << 30) < order[sub.id]]


def _record_verdict(board: Board, *, task_id: str, verdict: prompts.Verdict) -> None:
    task = board.task(task_id)
    if task is None:
        return
    criteria = [*task.criteria, *(item for sub in task.subtasks for item in sub.criteria)]
    for item in criteria:
        judged = verdict.criteria.get(item.id)
        if judged is None:
            continue
        item.status = CRITERION_PASSED if judged.passed else CRITERION_FAILED
        item.note = judged.note[:400]
    checks = [*task.checklist, *(item for sub in task.subtasks for item in sub.checklist)]
    for item in checks:
        if verdict.checklist.get(item.id):
            item.done = True
    if verdict.summary:
        task.report = prompts.clip_report(verdict.summary)


def _attached_images(task: Task | None) -> list[ImageContent]:
    """The task's attached pictures, for the model to see, not only to read about."""

    if task is None:
        return []
    images: list[ImageContent] = []
    for item in task.attachments:
        if not item.is_image or item.size > _MAX_IMAGE_BYTES or len(images) >= _MAX_IMAGES:
            continue
        try:
            data = Path(item.path).read_bytes()
        except OSError:
            continue
        images.append(
            ImageContent(data=base64.b64encode(data).decode("ascii"), media_type=item.mime)
        )
    return images
