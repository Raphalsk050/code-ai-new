"""How many images an endpoint accepts in one prompt.

Nothing advertises this. A server that caps images per request says so only by
refusing one - "At most 1 image(s) may be provided in one prompt" - so the cap
is learned from the refusal and remembered for the rest of the session.
"""

from __future__ import annotations

import re

# Each server phrases the same rule its own way; all of them put the number
# immediately before the word "image". Anchoring on that keeps the patterns
# short without matching unrelated counts elsewhere in an error body.
_LIMIT_PATTERNS = (
    re.compile(r"at most (\d+)\s+image", re.IGNORECASE),
    re.compile(r"only (\d+)\s+image", re.IGNORECASE),
    re.compile(r"max(?:imum)?(?: of)? (\d+)\s+image", re.IGNORECASE),
    re.compile(r"(\d+)\s+image\(?s?\)? (?:may|can) be", re.IGNORECASE),
    re.compile(r"image.{0,40}?limit(?: is)?[:= ]\s*(\d+)", re.IGNORECASE),
)


def parse_image_limit(text: str) -> int | None:
    """The per-request image cap named in an error body, or None.

    Returns None for anything that does not clearly name a cap, so an unrelated
    failure is never mistaken for one - the caller then handles it as the plain
    error it is.
    """

    if "image" not in text.lower():
        return None
    for pattern in _LIMIT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            limit = int(match.group(1))
        except (TypeError, ValueError):  # pragma: no cover - guarded by \d+
            continue
        if limit >= 0:
            return limit
    return None
