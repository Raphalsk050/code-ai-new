from __future__ import annotations

import random
import re

from code_ai.core.subagents.naming import generate_agent_name

# A single genius-style name: capitalized word, optionally with a numeric suffix
# when the pool is exhausted (e.g. "Turing" or "Turing-2").
_NAME_RE = re.compile(r"^[A-Z][a-zA-Z]+(?:-\d+)?$")


def test_name_is_a_single_genius_style_word() -> None:
    for _ in range(200):
        name = generate_agent_name()
        assert _NAME_RE.match(name), name
        # A single name, not the old adjective-adjective-noun triple.
        assert name.count("-") == 0


def test_name_is_deterministic_with_seeded_rng() -> None:
    a = generate_agent_name(random.Random(42))
    b = generate_agent_name(random.Random(42))
    assert a == b
    assert _NAME_RE.match(a)


def test_exclude_keeps_names_distinct() -> None:
    used: set[str] = set()
    for _ in range(20):
        name = generate_agent_name(exclude=used)
        assert name not in used
        used.add(name)
    assert len(used) == 20


def test_exhausted_pool_falls_back_to_suffixed_name() -> None:
    # Exclude every base name so the generator must suffix to stay unique.
    from code_ai.core.subagents.naming import _GENIUS_NAMES

    name = generate_agent_name(exclude=set(_GENIUS_NAMES))
    assert re.match(r"^[A-Z][a-zA-Z]+-\d+$", name), name
