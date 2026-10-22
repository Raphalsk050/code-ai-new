"""The workspace's task board as the front ends and the agent reach it.

The panel, the terminal and the agent's board tools all call this one object,
so they see one board and the same rules. A change is saved and announced as
``board.changed`` with the whole board: it is small, and a front end that only
ever replaces its copy cannot drift from it.

Nothing is implemented until the user presses Play. Planning, by the board's
own planners or by the agent, ends with the tasks waiting in To Do for that.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from code_ai.app.board_runner import BoardRunner
from code_ai.core.board import BoardError, apply_operations, load_board, save_board
from code_ai.core.board.models import (
    BUSY_STATUSES,
    RESUMABLE_STATUSES,
    ROLE_DOING,
    ROLE_DONE,
    ROLE_TODO,
    TASK_DRAFT,
    TASK_READY,
    Board,
)
from code_ai.events.bus import AsyncEventBus

logger = logging.getLogger(__name__)


class BoardService:
    def __init__(
        self,
        *,
        path: Path | None,
        event_bus: AsyncEventBus,
        runtime: Callable[[], Any],
        max_parallel: Callable[[], int],
        profiles: Callable[[], Any] = lambda: None,
    ) -> None:
        self._path = path
        self._bus = event_bus
        self._runtime = runtime
        self._max_parallel = max_parallel
        self._profiles = profiles
        self._board = load_board(path)
        self._runner: BoardRunner | None = None
        self._run: asyncio.Task[Any] | None = None
        self._planners: set[asyncio.Task[Any]] = set()
        self._plan_runners: set[BoardRunner] = set()

    # -- reading ----------------------------------------------------------

    @property
    def board(self) -> Board:
        return self._board

    @property
    def running(self) -> bool:
        return bool(self._run and not self._run.done())

    @property
    def planning(self) -> bool:
        return any(not task.done() for task in self._planners)

    def snapshot(self) -> dict[str, Any]:
        payload = self._board.to_dict()
        payload["running"] = self.running
        payload["planning"] = self.planning
        payload["run_id"] = self._runner.run_id if self._runner and self.running else ""
        return payload

    # -- editing ----------------------------------------------------------

    async def apply(
        self, operations: list[dict[str, Any]], *, source: str = "user"
    ) -> dict[str, Any]:
        """Apply a batch of edits; a refused one leaves the board as it was."""

        try:
            board, applied = apply_operations(self._board, operations)
        except BoardError as exc:
            return {"ok": False, "message": str(exc), "board": self.snapshot()}
        self._board = board
        self._save()
        await self._announce(moved=applied.moved, source=source)
        return {"ok": True, "created": applied.created, "board": self.snapshot()}

    async def mutate(
        self, change: Callable[[Board], None], *, persist: bool = True, quiet: bool = False
    ) -> None:
        """A change the runner makes in place; ``quiet`` ones are announced by their own event."""

        before = {task.id: task.lane for task in self._board.tasks}
        change(self._board)
        if persist:
            self._save()
        if quiet:
            return
        moved = [
            {"task": task.id, "from": before[task.id], "to": task.lane}
            for task in self._board.tasks
            if task.id in before and before[task.id] != task.lane
        ]
        await self._announce(moved=moved, source="run")

    # -- planning ---------------------------------------------------------

    async def plan(self, task_ids: list[str] | None = None) -> dict[str, Any]:
        """Break tasks into sub-tasks with planner agents running side by side.

        Without ids it plans every draft in To Do. The tasks come back ready,
        waiting for the user to look them over and press Play.
        """

        runtime = self._runtime()
        if runtime is None:
            return {"ok": False, "message": "This session has no sub-agents to plan with."}
        if task_ids:
            targets, problem = self._pick(
                task_ids, allow={TASK_DRAFT, TASK_READY, *RESUMABLE_STATUSES}
            )
            if problem:
                return {"ok": False, "message": problem}
        else:
            todo = self._board.lane_for(ROLE_TODO).id
            targets = [
                task.id
                for task in self._board.tasks
                if task.lane == todo and task.status == TASK_DRAFT
            ]
        if not targets:
            return {"ok": False, "message": "There is no draft in To Do to plan."}
        runner = self._new_runner(runtime)
        self._plan_runners.add(runner)
        job = asyncio.create_task(self._plan_job(runner, targets))
        self._planners.add(job)
        job.add_done_callback(self._planners.discard)
        return {"ok": True, "tasks": targets, "message": f"Planning {len(targets)} task(s)."}

    async def _plan_job(self, runner: BoardRunner, targets: list[str]) -> None:
        await self._emit("board.plan.started", {"tasks": targets})
        try:
            results = await runner.plan(targets)
        finally:
            self._plan_runners.discard(runner)
        planned = [task_id for task_id, result in results.items() if result == "planned"]
        await self._emit("board.plan.completed", {"tasks": targets, "planned": planned})
        if planned:
            await self.request_review(planned, source="planner")
        else:
            await self._announce(moved=[], source="planner")

    async def request_review(
        self, task_ids: list[str] | None = None, *, source: str = "agent", message: str = ""
    ) -> dict[str, Any]:
        """Ask the user to confirm the planned tasks; only their Play starts the work."""

        todo = self._board.lane_for(ROLE_TODO).id
        if task_ids:
            ready = [task_id for task_id in task_ids if self._board.task(task_id) is not None]
        else:
            ready = [
                task.id
                for task in self._board.tasks
                if task.lane == todo and task.status == TASK_READY
            ]
        payload = {"tasks": ready, "source": source, "message": message}
        await self._emit("board.review.requested", payload)
        await self._announce(moved=[], source=source)
        return {"ok": True, **payload}

    # -- running ----------------------------------------------------------

    async def play(self, task_ids: list[str] | None = None) -> dict[str, Any]:
        """The user's go: implement the ready tasks, each sub-task by its own agent.

        Without ids it takes every task waiting in To Do plus the ones a run
        left failed or stopped in In Progress. A draft among them is planned
        instead: nothing runs before the user has seen its sub-tasks.
        """

        if self.running:
            return {"ok": False, "message": "The board is already running. Stop it first."}
        runtime = self._runtime()
        if runtime is None:
            return {"ok": False, "message": "This session has no sub-agents to run the board with."}
        if task_ids:
            targets, problem = self._pick(
                task_ids, allow={TASK_DRAFT, TASK_READY, *RESUMABLE_STATUSES}
            )
            if problem:
                return {"ok": False, "message": problem}
        else:
            todo = self._board.lane_for(ROLE_TODO).id
            doing = self._board.lane_for(ROLE_DOING).id
            targets = [
                task.id
                for task in self._board.tasks
                if (task.lane == todo and task.status in (TASK_DRAFT, TASK_READY))
                or (task.lane in (todo, doing) and task.status in RESUMABLE_STATUSES)
            ]
        drafts = [task_id for task_id in targets if self._board.task(task_id).status == TASK_DRAFT]
        runnable = [task_id for task_id in targets if task_id not in drafts]
        if drafts:
            await self.plan(drafts)
        if not runnable:
            if drafts:
                return {
                    "ok": True,
                    "tasks": [],
                    "planning": drafts,
                    "message": (
                        f"{len(drafts)} task(s) had no sub-tasks yet; they are being planned. "
                        "Look them over and press Play again."
                    ),
                }
            return {"ok": False, "message": "There is nothing in To Do to run."}
        runner = self._new_runner(runtime)
        self._runner = runner
        self._run = asyncio.create_task(self._run_job(runner, runnable))
        return {
            "ok": True,
            "run_id": runner.run_id,
            "tasks": runnable,
            "planning": drafts,
            "message": f"Running {len(runnable)} task(s).",
        }

    async def _run_job(self, runner: BoardRunner, targets: list[str]) -> None:
        await self._emit("board.run.started", {"run_id": runner.run_id, "tasks": targets})
        status = "completed"
        outcome: dict[str, str] = {}
        try:
            outcome = await runner.run(targets)
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception:
            logger.exception("The board run failed")
            status = "failed"
        finally:
            if runner.stopped and status == "completed":
                status = "stopped"
            done = [task_id for task_id, result in outcome.items() if result == "done"]
            failed = [task_id for task_id, result in outcome.items() if result == "failed"]
            await self._emit(
                "board.run.completed",
                {"run_id": runner.run_id, "status": status, "done": done, "failed": failed},
            )
            await self._announce(moved=[], source="run")

    async def stop(self) -> dict[str, Any]:
        stopped = False
        if self._runner is not None and self.running:
            self._runner.stop()
            stopped = True
        for runner in list(self._plan_runners):
            runner.stop()
            stopped = True
        if not stopped:
            return {"ok": False, "message": "Nothing on the board is running."}
        return {"ok": True, "message": "Stopping the board's agents."}

    async def close(self) -> None:
        await self.stop()
        jobs = [job for job in (self._run, *self._planners) if job and not job.done()]
        for job in jobs:
            job.cancel()
        for job in jobs:
            try:
                await job
            except BaseException:  # noqa: BLE001 - shutting down
                pass

    # -- helpers ----------------------------------------------------------

    def _pick(self, task_ids: list[str], *, allow: set[str]) -> tuple[list[str], str]:
        picked: list[str] = []
        for ref in task_ids:
            task = self._board.task(str(ref))
            if task is None:
                return [], f"No task {ref!r}."
            if task.status in BUSY_STATUSES:
                return [], f"{task.id} is already being worked on."
            if task.status not in allow:
                return [], f"{task.id} is {task.status}."
            if self._board.role_of(task) == ROLE_DONE:
                return [], f"{task.id} is done. Move it out of Done to work on it again."
            if task.id not in picked:
                picked.append(task.id)
        return picked, ""

    def _new_runner(self, runtime: Any) -> BoardRunner:
        return BoardRunner(
            runtime=runtime,
            mutate=self.mutate,
            read=lambda: self._board,
            emit=self._emit,
            max_parallel=self._max_parallel(),
            profiles=self._profiles(),
            run_id=uuid4().hex[:12],
        )

    def _save(self) -> None:
        try:
            save_board(self._path, self._board)
        except OSError:
            # The board stays right in memory; the next change tries again.
            logger.exception("The board could not be saved to %s", self._path)

    async def _announce(self, *, moved: list[dict[str, str]], source: str) -> None:
        await self._emit(
            "board.changed", {"board": self.snapshot(), "moved": moved, "source": source}
        )

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        await self._bus.emit(event_type, payload, source="board")
