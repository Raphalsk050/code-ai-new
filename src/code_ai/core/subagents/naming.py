from __future__ import annotations

import random
from collections.abc import Iterable

# Claude-Code-style sub-agent names: a single name in the style of a historic
# genius of science, computing, or mathematics (e.g. "Turing", "Lovelace",
# "Euler"). The name is chosen when a sub-agent is created and used in every log
# and reference to it, so a fan-out reads as a roster of named agents rather than
# opaque ids.

_GENIUS_NAMES: tuple[str, ...] = (
    "Turing", "Pascal", "Newton", "Euler", "Gauss", "Darwin", "Tesla", "Edison",
    "Curie", "Bohr", "Planck", "Hawking", "Lovelace", "Hopper", "Knuth",
    "Dijkstra", "Ritchie", "Thompson", "Torvalds", "Babbage", "Fermi", "Feynman",
    "Galileo", "Kepler", "Copernicus", "Archimedes", "Pythagoras", "Euclid",
    "Fibonacci", "Ramanujan", "Noether", "Hilbert", "Riemann", "Cantor", "Godel",
    "Shannon", "Neumann", "Boole", "Leibniz", "Maxwell", "Faraday", "Kelvin",
    "Pasteur", "Mendel", "Hubble", "Sagan", "Franklin", "Goodall", "Hypatia",
    "Lorenz", "Poincare", "Chandrasekhar", "Bose", "Raman", "Banneker", "Germain",
    "Nash", "Erdos", "Lamarr", "Wing", "Liskov", "Karp", "Hamming", "Backus",
)


def generate_agent_name(
    rng: random.Random | None = None,
    *,
    exclude: Iterable[str] = (),
) -> str:
    """Return a single genius-style name for a sub-agent (e.g. ``"Turing"``).

    ``exclude`` lets a caller keep names distinct within one fan-out; when the
    whole pool is excluded (more agents than names), a numeric suffix keeps the
    name unique (e.g. ``"Turing-2"``). ``rng`` is injectable for deterministic
    tests; production uses the module's default random source.
    """
    source = rng or random
    taken = set(exclude)
    available = [name for name in _GENIUS_NAMES if name not in taken]
    if available:
        return source.choice(available)
    # Pool exhausted: fall back to a suffixed name that is still unique.
    base = source.choice(_GENIUS_NAMES)
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"
