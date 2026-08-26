"""Read and curate the agent's persistent memory from the command line.

Trusting an agent that teaches itself requires being able to see what it
learned without opening JSON files or starting a session: ``code-ai memories``
lists every stored memory and failure lesson (with the short ids needed to
curate them), and ``code-ai memories forget <id-prefix>`` deletes exactly one
entry. Reads the same stores the runtime uses; no provider is needed.
"""

from __future__ import annotations

import time
from argparse import Namespace
from dataclasses import dataclass

from code_ai.config.defaults import (
    default_memories_dir,
    global_knowledge_dir,
    project_memories_dir,
)
from code_ai.config.models import AppConfig
from code_ai.core.memory import FailureMemoryStore, MemoryStore


@dataclass(slots=True)
class _Row:
    """One deletable entry, however it is stored."""

    scope: str
    entry_id: str
    label: str  # "[kind]" or "x<count>" for lessons
    updated: float
    text: str
    delete: object  # zero-arg callable performing the deletion


def run_memories_command(config: AppConfig, args: Namespace) -> int:
    rows = _collect_rows(config)
    if getattr(args, "memories_command", None) == "forget":
        return _forget(rows, args.memory_id)
    return _list(rows)


def _collect_rows(config: AppConfig) -> list[_Row]:
    rows: list[_Row] = []
    stores = (
        ("global", MemoryStore(global_knowledge_dir())),
        (f"project {config.workspace}", MemoryStore(project_memories_dir(config.workspace))),
    )
    for scope, store in stores:
        for entry in store.all():
            rows.append(
                _Row(
                    scope=scope,
                    entry_id=entry.id,
                    label=f"[{entry.kind}]",
                    updated=entry.updated,
                    text=entry.content,
                    delete=lambda store=store, entry=entry: store.remove(entry.id),
                )
            )

    memories_dir = config.memories_dir or default_memories_dir()
    lessons = FailureMemoryStore(memories_dir)
    for entry in lessons.lessons():
        rows.append(
            _Row(
                scope="lessons",
                entry_id=entry.id,
                label=f"x{entry.count}",
                updated=entry.last_seen,
                text=entry.lesson,
                delete=lambda store=lessons, entry=entry: store.remove(entry.signature),
            )
        )
    return rows


def _list(rows: list[_Row]) -> int:
    if not rows:
        print("Nothing learned yet.")
        return 0
    for scope in dict.fromkeys(row.scope for row in rows):  # insertion order
        print(f"{scope}:")
        for row in (r for r in rows if r.scope == scope):
            print(
                f"  {row.entry_id[:8]}  {row.label:<11} {_age(row.updated):>6}  {row.text}"
            )
        print()
    print("Delete one entry with: code-ai memories forget <id-prefix>")
    return 0


def _forget(rows: list[_Row], prefix: str) -> int:
    prefix = prefix.strip()
    if not prefix:
        print("An id prefix is required.")
        return 2
    matches = [row for row in rows if row.entry_id.startswith(prefix)]
    if not matches:
        print(f"No memory or lesson matches id prefix {prefix!r}.")
        return 1
    if len(matches) > 1:
        print(f"Id prefix {prefix!r} is ambiguous between:")
        for row in matches:
            print(f"  {row.entry_id[:8]}  ({row.scope})  {row.text}")
        return 2
    row = matches[0]
    if not row.delete():
        print(f"Could not delete {row.entry_id[:8]} (already gone?).")
        return 1
    print(f"Forgot ({row.scope}): {row.text}")
    return 0


def _age(timestamp: float) -> str:
    delta = max(0.0, time.time() - timestamp)
    if delta < 120:
        return "now"
    if delta < 2 * 3600:
        return f"{int(delta // 60)}min"
    if delta < 2 * 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"
