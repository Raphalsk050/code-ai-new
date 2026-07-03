from __future__ import annotations

import random

# Claude-Code-style sub-agent names: two distinct adjectives and a scientist's
# surname, joined by hyphens (e.g. "magical-dazzling-pascal"). The name is
# generated once when a sub-agent is created and used in every log and reference
# to it, so a fan-out reads as a roster of named agents rather than opaque ids.

_ADJECTIVES: tuple[str, ...] = (
    "magical", "dazzling", "witty", "sparkling", "brave", "cosmic", "gentle",
    "radiant", "clever", "bold", "serene", "vivid", "lucid", "nimble", "jolly",
    "mellow", "quirky", "plucky", "dapper", "breezy", "sunny", "feisty", "snappy",
    "zesty", "chipper", "spry", "peppy", "swift", "keen", "wily", "merry",
    "cheery", "stellar", "lunar", "solar", "azure", "crimson", "amber", "emerald",
    "golden", "silver", "velvet", "silky", "frosty", "misty", "dreamy", "humble",
    "mighty", "noble", "regal", "curious", "eager", "fabled", "glowing", "hearty",
    "jaunty", "lively", "rustic", "wandering",
)

# Surnames of scientists, mathematicians, and computing pioneers, lowercased.
_NOUNS: tuple[str, ...] = (
    "turing", "pascal", "newton", "euler", "gauss", "darwin", "tesla", "edison",
    "curie", "bohr", "planck", "hawking", "lovelace", "hopper", "knuth",
    "dijkstra", "ritchie", "thompson", "torvalds", "babbage", "fermi", "feynman",
    "galileo", "kepler", "copernicus", "archimedes", "pythagoras", "euclid",
    "fibonacci", "ramanujan", "noether", "hilbert", "riemann", "cantor", "godel",
    "shannon", "neumann", "boole", "leibniz", "maxwell", "faraday", "kelvin",
    "pasteur", "mendel", "hubble", "sagan", "franklin", "goodall", "hypatia",
    "lorenz", "poincare", "chandrasekhar", "bose", "raman", "banneker", "germain",
)


def generate_agent_name(rng: random.Random | None = None) -> str:
    """Return a fresh ``adjective-adjective-noun`` name for a sub-agent.

    The two adjectives are always distinct so the name never doubles a word
    (e.g. never ``brave-brave-turing``). ``rng`` is injectable for deterministic
    tests; production uses the module's default random source.
    """
    source = rng or random
    first, second = source.sample(_ADJECTIVES, 2)
    noun = source.choice(_NOUNS)
    return f"{first}-{second}-{noun}"
