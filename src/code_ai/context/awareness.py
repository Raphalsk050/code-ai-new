"""What the agent is told about its own context window.

The runtime owns the hard decisions: it clears old tool output and compacts by
itself. The model is told only when the window crosses a mark, in one line at
the end of the newest tool result - never in the system prompt, which has to
stay the same from one request to the next for the server's prefix cache.
"""

from __future__ import annotations

from dataclasses import dataclass

# Marks, as shares of the point where compaction starts.
_GETTING_FULL = 0.7
_NEARLY_FULL = 0.92
# A mark is told again only once the window has emptied this far below it.
_REARM = 0.15

# A round of tool calls may take this share of the room left before compaction,
# split among its calls; no result is cut below the floor.
_ROUND_SHARE = 0.6
_RESULT_FLOOR_CHARS = 1500
# Code and JSON run nearer three characters a token than four.
_CHARS_PER_TOKEN = 3

_GETTING_FULL_NOTE = (
    "Context: {percent}% of the window is in use. The runtime clears old tool output "
    "by itself, so carry on with the task; from here on read files in slices and "
    "keep searches narrow."
)
_NEARLY_FULL_NOTE = (
    "Context: {percent}% of the window is in use and the conversation is about to be "
    "compacted. If something you found must survive that, say it plainly in your "
    "next message. Then carry on: this is not a reason to stop."
)
_CUT_NOTE = (
    "This result was cut to fit the context: {shown} of its {total} characters are "
    "above, the middle left out. Ask again for the part you need - a line range, a "
    "narrower search, fewer results."
)


def result_room(*, used: int, limit: int, ceiling: int, calls: int = 1) -> int:
    """How many characters one tool result may take right now.

    ``limit`` is the token count at which compaction starts, ``ceiling`` the
    configured cap, which this only ever lowers.
    """

    room = max(0, limit - used) * _ROUND_SHARE * _CHARS_PER_TOKEN / max(1, calls)
    return max(min(ceiling, _RESULT_FLOOR_CHARS), min(ceiling, int(room)))


def cut_note(shown: int, total: int) -> str:
    return _CUT_NOTE.format(shown=shown, total=total)


@dataclass(slots=True)
class ContextAwareness:
    """Which mark the agent was last told about."""

    told: int = 0

    def note(self, *, used: int, limit: int, window: int) -> str:
        """What to tell the model now that ``used`` tokens are in the window, or ""."""

        level = _level(used, limit)
        if level <= self.told:
            return ""
        self.told = level
        percent = min(99, round(100 * used / window)) if window > 0 else 0
        return (_NEARLY_FULL_NOTE if level == 2 else _GETTING_FULL_NOTE).format(percent=percent)

    def settle(self, *, used: int, limit: int) -> None:
        """The window as a request found it: one that emptied may be told about again."""

        share = used / limit if limit > 0 else 0.0
        if self.told == 2 and share < _NEARLY_FULL - _REARM:
            self.told = 1
        if self.told == 1 and share < _GETTING_FULL - _REARM:
            self.told = 0


def _level(used: int, limit: int) -> int:
    share = used / limit if limit > 0 else 0.0
    return 2 if share >= _NEARLY_FULL else 1 if share >= _GETTING_FULL else 0
