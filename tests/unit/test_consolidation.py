from __future__ import annotations

import json

from code_ai.config.models import MemoryConfig
from code_ai.core.memory import MemoryService, MemoryStore
from code_ai.core.reflection import ReflectionService


def _service(tmp_path, generator, *, min_new: int = 3) -> ReflectionService:
    memory = MemoryService(
        global_store=MemoryStore(tmp_path / "global"),
        project_store=MemoryStore(tmp_path / "project"),
    )
    config = MemoryConfig(consolidation_min_new=min_new)
    return ReflectionService(memory=memory, generator=generator, config=config)


def _project_store(service: ReflectionService) -> MemoryStore:
    stores = dict(service._memory.scoped_stores())
    return stores["project"]


async def test_consolidation_waits_for_the_threshold(tmp_path) -> None:
    calls = 0

    async def generator(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"drop": [], "rewrite": []}'

    service = _service(tmp_path, generator, min_new=3)
    service._memory.add(kind="project", content="fact 1")
    service._memory.add(kind="project", content="fact 2")

    assert await service.maybe_consolidate() is False
    assert calls == 0  # below threshold: no meta-call at all

    service._memory.add(kind="project", content="fact 3")
    assert await service.maybe_consolidate() is False  # clean store: no changes
    assert calls == 1

    # The baseline reset: the same store does not re-consolidate immediately.
    assert await service.maybe_consolidate() is False
    assert calls == 1


async def test_consolidation_merges_and_drops(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        # Entries are listed newest-first; map the listing back to ops.
        lines = [
            line
            for line in prompt.splitlines()
            if line and line[0].isdigit() and ". [" in line
        ]
        by_content = {}
        for line in lines:
            number = int(line.split(".", 1)[0])
            by_content[line.split("] ", 1)[1]] = number
        return json.dumps(
            {
                "drop": [by_content["Tests run with pytest."], by_content["Old fact."]],
                "rewrite": [
                    {
                        "n": by_content["Tests run with pytest -q."],
                        "content": "Tests run with pytest -q from the repo root.",
                    }
                ],
            }
        )

    service = _service(tmp_path, generator, min_new=3)
    service._memory.add(kind="project", content="Tests run with pytest.")
    service._memory.add(kind="project", content="Tests run with pytest -q.")
    service._memory.add(kind="project", content="Old fact.")

    assert await service.maybe_consolidate() is True

    contents = sorted(e.content for e in _project_store(service).all())
    assert contents == ["Tests run with pytest -q from the repo root."]


async def test_consolidation_never_drops_identity(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        return '{"drop": [1, 2, 3], "rewrite": []}'

    service = _service(tmp_path, generator, min_new=3)
    service._memory.add(kind="user", content="The user is named Rafael.")
    service._memory.add(kind="feedback", content="dup A")
    service._memory.add(kind="feedback", content="dup B")

    await service.maybe_consolidate()

    remaining = {e.content for e in dict(service._memory.scoped_stores())["global"].all()}
    # Identity survives a bulk drop; the non-identity dups are gone.
    assert remaining == {"The user is named Rafael."}


async def test_consolidation_generator_failure_leaves_store_and_retries(tmp_path) -> None:
    attempts = 0

    async def generator(prompt: str) -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("meta-call died")

    service = _service(tmp_path, generator, min_new=2)
    service._memory.add(kind="project", content="fact 1")
    service._memory.add(kind="project", content="fact 2")

    assert await service.maybe_consolidate() is False
    assert len(_project_store(service).all()) == 2
    # The marker was NOT written, so the next pass tries again.
    assert await service.maybe_consolidate() is False
    assert attempts == 2


async def test_consolidation_garbage_output_is_a_clean_no_op(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        return "not json"

    service = _service(tmp_path, generator, min_new=2)
    service._memory.add(kind="project", content="fact 1")
    service._memory.add(kind="project", content="fact 2")

    assert await service.maybe_consolidate() is False
    assert len(_project_store(service).all()) == 2


async def test_consolidation_ignores_out_of_range_and_caps_ops(tmp_path) -> None:
    async def generator(prompt: str) -> str:
        return json.dumps(
            {
                "drop": list(range(-3, 40)),  # junk indexes mixed with real ones
                "rewrite": [{"n": 999, "content": "should not apply"}],
            }
        )

    service = _service(tmp_path, generator, min_new=2)
    for i in range(15):
        service._memory.add(kind="project", content=f"fact {i}")

    await service.maybe_consolidate()

    remaining = _project_store(service).all()
    # At most the op cap (10) may be dropped in one pass, never the whole store.
    assert len(remaining) == 5
