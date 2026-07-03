from __future__ import annotations

import random
import re

from code_ai.core.subagents.naming import generate_agent_name

_NAME_RE = re.compile(r"^[a-z]+-[a-z]+-[a-z]+$")


def test_name_has_adjective_adjective_noun_shape() -> None:
    for _ in range(200):
        name = generate_agent_name()
        assert _NAME_RE.match(name), name
        first, second, _noun = name.split("-")
        # The two adjectives are always distinct.
        assert first != second


def test_name_is_deterministic_with_seeded_rng() -> None:
    a = generate_agent_name(random.Random(42))
    b = generate_agent_name(random.Random(42))
    assert a == b
    assert _NAME_RE.match(a)


def test_names_vary() -> None:
    names = {generate_agent_name() for _ in range(50)}
    # Overwhelmingly likely to produce many distinct names from the large space.
    assert len(names) > 40
