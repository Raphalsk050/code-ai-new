"""What the board's sub-agents are told, and how their answers are read back.

Three kinds of agent work a board: a planner breaks a task into sub-tasks, a
worker does one sub-task, and a verifier judges a finished task against its
criteria and checklist. The planner and the verifier answer in JSON, read with
the same tolerance: the object may come fenced, or after a sentence of prose.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from code_ai.core.board.models import SUB_DONE, Board, CheckItem, Criterion, SubTask, Task

_REPORT_LIMIT = 4000

PLANNER_RULES = """\
Break the task below into sub-tasks for parallel sub-agents. Each sub-task is
given to its own agent, and agents run side by side in the same workspace, so:
- one sub-task is one focused change a single agent can finish and verify;
- two sub-tasks that touch the same files must not run at the same time: make
  one depend on the other;
- say in each description which files or areas it owns.
Read the code first, so the breakdown names real files and real conventions.
Give the task acceptance criteria the finished work can be checked against, and
a checklist of what must be true when it is done.

Reply with one JSON object and nothing after it:
{"subtasks": [{"title": "...", "description": "...", "depends_on": [1],
               "checklist": ["..."], "criteria": ["..."]}],
 "checklist": ["..."], "criteria": ["..."]}
depends_on lists earlier sub-tasks by their 1-based place in your list."""

VERIFIER_RULES = """\
Judge whether the work below is done. Read the code the workers changed, and run
the project's tests or build when there are any. Judge every criterion and every
checklist item by its id: pass only what you saw to be true.

Reply with one JSON object and nothing after it:
{"criteria": {"<id>": {"pass": true, "note": "what you checked"}},
 "checklist": {"<id>": true},
 "summary": "one or two sentences"}"""


def planner_prompt(board: Board, task: Task) -> str:
    parts = [PLANNER_RULES, "", _task_block(task)]
    if task.subtasks:
        kept = "\n".join(f"- {sub.title}: {sub.description}".rstrip(": ") for sub in task.subtasks)
        parts += [
            "",
            "The user already wrote these sub-tasks. Keep them and add what is missing:",
            kept,
        ]
    others = [other for other in board.tasks if other.id != task.id and other.status != "done"]
    if others:
        listing = "\n".join(f"- {other.id} {other.title}" for other in others[:30])
        parts += ["", "Other tasks on the board, planned apart (do not do their work):", listing]
    return "\n".join(parts)


def worker_prompt(task: Task, sub: SubTask | None) -> str:
    """One agent's assignment: its piece, the feature around it, and its neighbours."""

    lines = [f"You are doing one piece of the feature {task.id}: {task.title}."]
    if task.description:
        lines += ["", "The feature:", task.description]
    if task.attachments:
        lines += ["", *attachments_block(task)]
    if sub is None:
        lines += ["", "Do the whole feature."]
        lines += _checks("Make sure of", task.checklist)
        lines += _criteria("It will be judged against", task.criteria)
    else:
        lines += ["", f"Your piece, {sub.id}: {sub.title}"]
        if sub.description:
            lines.append(sub.description)
        lines += _checks("Make sure of", sub.checklist)
        lines += _criteria("It will be judged against", sub.criteria)
        done = [other for other in task.subtasks if other is not sub and other.status == SUB_DONE]
        if done:
            lines += ["", "Already done by other agents (build on it, do not redo it):"]
            lines += [f"- {other.id} {other.title}: {_clip(other.report, 600)}" for other in done]
        rest = [other for other in task.subtasks if other is not sub and other.status != SUB_DONE]
        if rest:
            lines += ["", "Other agents work on these at the same time; leave their part alone:"]
            lines += [f"- {other.id} {other.title}" for other in rest]
        lines += _checks("The feature as a whole must also", task.checklist)
    lines += [
        "",
        "Make the change in the workspace with the file and command tools, and verify it. "
        "Do only your piece. Finish with a short report: what you changed (paths), how you "
        "verified it, and anything you could not do.",
    ]
    return "\n".join(lines)


def fix_prompt(task: Task, verdict: Verdict) -> str:
    lines = [
        f"The feature {task.id}: {task.title} was checked and is not done yet.",
        "",
        "What the check found:",
        verdict.findings(task),
        "",
        _task_block(task),
        "",
        "Fix what failed, in the workspace, and verify it. Finish with a short report of "
        "what you changed.",
    ]
    return "\n".join(lines)


def verifier_prompt(task: Task) -> str:
    lines = [VERIFIER_RULES, "", _task_block(task, ids=True)]
    reports = [sub for sub in task.subtasks if sub.report]
    if reports:
        lines += ["", "What the workers reported:"]
        lines += [f"- {sub.id} {sub.title}: {_clip(sub.report, 1200)}" for sub in reports]
    elif task.report:
        lines += ["", "What the worker reported:", _clip(task.report, 2400)]
    return "\n".join(lines)


def needs_verification(task: Task) -> bool:
    return bool(_open_checks(task) or _all_criteria(task))


# -- reading answers ----------------------------------------------------------


@dataclass(slots=True)
class PlannedSubTask:
    title: str
    description: str = ""
    depends_on: list[int] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Plan:
    subtasks: list[PlannedSubTask]
    checklist: list[str]
    criteria: list[str]


def parse_plan(text: str) -> Plan | None:
    data = extract_json(text)
    if not isinstance(data, dict):
        return None
    raw_subs = data.get("subtasks")
    if not isinstance(raw_subs, list) or not raw_subs:
        return None
    subs: list[PlannedSubTask] = []
    for raw in raw_subs:
        if isinstance(raw, str):
            raw = {"title": raw}
        if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
            continue
        deps = [int(ref) for ref in _list(raw.get("depends_on")) if str(ref).strip().isdigit()]
        subs.append(
            PlannedSubTask(
                title=str(raw["title"]).strip(),
                description=str(raw.get("description") or "").strip(),
                depends_on=deps,
                checklist=_strings(raw.get("checklist")),
                criteria=_strings(raw.get("criteria")),
            )
        )
    if not subs:
        return None
    return Plan(subs, _strings(data.get("checklist")), _strings(data.get("criteria")))


@dataclass(slots=True)
class Judgement:
    passed: bool
    note: str = ""


@dataclass(slots=True)
class Verdict:
    criteria: dict[str, Judgement]
    checklist: dict[str, bool]
    summary: str = ""
    # The verifier's own words when it did not answer in JSON.
    unreadable: str = ""

    def passed(self, task: Task) -> bool:
        if self.unreadable:
            return False
        for item in _all_criteria(task):
            judged = self.criteria.get(item.id)
            if judged is None or not judged.passed:
                return False
        return all(self.checklist.get(item.id) for item in _open_checks(task))

    def findings(self, task: Task) -> str:
        if self.unreadable:
            return _clip(self.unreadable, 2000)
        lines: list[str] = []
        for item in _all_criteria(task):
            judged = self.criteria.get(item.id)
            if judged is None:
                lines.append(f"- {item.text}: not checked")
            elif not judged.passed:
                lines.append(f"- {item.text}: failed. {judged.note}".rstrip(". ") + ".")
        for item in _open_checks(task):
            if not self.checklist.get(item.id):
                lines.append(f"- {item.text}: not true yet")
        if self.summary:
            lines.append(self.summary)
        return "\n".join(lines) or "The check did not pass."


def parse_verdict(text: str) -> Verdict:
    data = extract_json(text)
    if not isinstance(data, dict):
        return Verdict({}, {}, unreadable=text.strip() or "The verifier gave no answer.")
    criteria: dict[str, Judgement] = {}
    raw_criteria = data.get("criteria")
    if isinstance(raw_criteria, dict):
        for key, value in raw_criteria.items():
            if isinstance(value, dict):
                criteria[str(key)] = Judgement(
                    _truthy(value.get("pass")), str(value.get("note") or "")
                )
            else:
                criteria[str(key)] = Judgement(_truthy(value))
    checklist: dict[str, bool] = {}
    raw_checks = data.get("checklist")
    if isinstance(raw_checks, dict):
        for key, value in raw_checks.items():
            checklist[str(key)] = _truthy(value.get("pass") if isinstance(value, dict) else value)
    return Verdict(criteria, checklist, str(data.get("summary") or "").strip())


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """The last JSON object in ``text``: fenced first, then any balanced ``{...}``."""

    text = text or ""
    for block in reversed(_FENCE.findall(text)):
        try:
            return json.loads(block)
        except ValueError:
            continue
    decoder = json.JSONDecoder()
    found: Any = None
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict):
            found = value
        index = text.find("{", end)
    return found


# -- helpers --------------------------------------------------------------------


def attachments_block(task: Task) -> list[str]:
    """The files the user attached and where an agent reads them; pictures also go as images."""

    if not task.attachments:
        return []
    lines = ["Files the user attached to this task (read them with read_file at these paths):"]
    for item in task.attachments:
        seen = ", also attached as an image" if item.is_image else ""
        lines.append(f"- {item.name} ({item.mime or 'file'}, {item.size} bytes{seen}): {item.path}")
    return lines


def _task_block(task: Task, *, ids: bool = False) -> str:
    lines = [f"Task {task.id}: {task.title}"]
    if task.description:
        lines.append(task.description)
    lines += attachments_block(task)
    for sub in task.subtasks:
        lines.append(f"- sub-task {sub.id}: {sub.title}")
        if sub.description:
            lines.append(f"  {sub.description}")
        lines += [f"  {_labelled('criterion', item, ids)}" for item in sub.criteria]
        lines += [f"  {_labelled('check', item, ids)}" for item in sub.checklist if not item.done]
    lines += [_labelled("criterion", item, ids) for item in task.criteria]
    lines += [_labelled("check", item, ids) for item in task.checklist if not item.done]
    return "\n".join(lines)


def _labelled(kind: str, item: CheckItem | Criterion, ids: bool) -> str:
    return f"{kind} {item.id}: {item.text}" if ids else f"{kind}: {item.text}"


def _checks(heading: str, items: list[CheckItem]) -> list[str]:
    open_items = [item for item in items if not item.done]
    if not open_items:
        return []
    return ["", f"{heading}:", *(f"- {item.text}" for item in open_items)]


def _criteria(heading: str, items: list[Criterion]) -> list[str]:
    if not items:
        return []
    return ["", f"{heading}:", *(f"- {item.text}" for item in items)]


def _all_criteria(task: Task) -> list[Criterion]:
    return [*task.criteria, *(item for sub in task.subtasks for item in sub.criteria)]


def _open_checks(task: Task) -> list[CheckItem]:
    items = [*task.checklist, *(item for sub in task.subtasks for item in sub.checklist)]
    return [item for item in items if not item.done]


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    return [value]


def _strings(value: Any) -> list[str]:
    items = []
    for raw in _list(value):
        text = raw.get("text") if isinstance(raw, dict) else raw
        text = " ".join(str(text or "").split())
        if text:
            items.append(text)
    return items


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "pass", "passed", "ok", "sim", "1")


def _clip(text: str, limit: int = _REPORT_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clip_report(text: str) -> str:
    return _clip(text, _REPORT_LIMIT)
