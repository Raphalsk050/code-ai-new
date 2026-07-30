"""Incremental decoding of a JSON object that is still being streamed.

A model streams a tool call's arguments as a growing raw string, so at any
moment the text is a *prefix* of a JSON document: quotes are unbalanced, an
escape sequence may be cut in half, and ``json.loads`` cannot touch it until the
very last fragment arrives. That is fine for executing the call, but it is
useless for showing the user what is being written while it is written.

:class:`PartialObjectDecoder` closes that gap. It walks the raw text with a tiny
resumable state machine and keeps the decoded value of each top-level string key
up to date. Two properties make it usable as the engine behind a live view:

* **Resumable.** ``feed`` is handed the *cumulative* buffer and only scans the
  part it has not seen yet, so decoding a whole call costs O(total length) no
  matter how many fragments it arrived in. A one-shot re-parse per fragment
  would be quadratic and would stall the terminal on a large file.
* **Monotonic.** The decoded text only ever grows: an incomplete escape at the
  end of the buffer is held back rather than guessed at, so what was already
  decoded never has to be rewritten. Consumers can therefore append deltas
  instead of re-rendering everything.

The decoder is deliberately lenient - it is a viewer, not a validator. Malformed
input yields whatever was readable rather than raising, because the authoritative
parse still happens on the completed arguments.
"""

from __future__ import annotations

from collections.abc import Iterable

# JSON's two-character escapes. Anything else after a backslash is passed
# through as-is: invalid JSON, but a preview should show it rather than die.
_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_REPLACEMENT = "�"
_HIGH_SURROGATE = range(0xD800, 0xDC00)
_LOW_SURROGATE = range(0xDC00, 0xE000)


class PartialValue:
    """One top-level string value, decoded as far as the stream allows.

    Text is accumulated in fragments and joined lazily: appending per character
    to a ``str`` would make decoding a large file quadratic.
    """

    __slots__ = ("_parts", "_joined", "closed")

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._joined: str | None = ""
        # True once the closing quote has arrived, i.e. the value is final.
        self.closed = False

    def append(self, text: str) -> None:
        self._parts.append(text)
        self._joined = None

    @property
    def text(self) -> str:
        if self._joined is None:
            self._joined = "".join(self._parts)
        return self._joined

    def __len__(self) -> int:
        return len(self.text)


class PartialObjectDecoder:
    """Resumable decoder for the top-level string values of a streaming object.

    Only depth-1 members are tracked: a tool call's arguments are a flat object,
    and ignoring nested containers keeps the state machine small enough to stay
    obviously correct. Nested text is skipped, not decoded.

    ``keys`` restricts what is retained; ``None`` keeps every top-level string.
    """

    __slots__ = (
        "_keys",
        "_values",
        "_consumed",
        "_depth",
        "_expect_value",
        "_key",
        "_in_string",
        "_mode",
        "_sink",
        "_key_parts",
        "_escape",
        "_unicode",
        "_high",
    )

    def __init__(self, keys: Iterable[str] | None = None) -> None:
        self._keys = None if keys is None else frozenset(keys)
        self._values: dict[str, PartialValue] = {}
        self._consumed = 0
        self._depth = 0
        # A ':' was seen at depth 1, so the next string is a value, not a key.
        self._expect_value = False
        self._key = ""
        self._in_string = False
        # What the string currently being read is: "key", "value" or "" (skip).
        self._mode = ""
        self._sink: PartialValue | None = None
        self._key_parts: list[str] = []
        self._escape = False
        # Hex digits collected so far inside a \uXXXX escape; None when outside.
        self._unicode: str | None = None
        # A pending high surrogate awaiting its low half.
        self._high: int | None = None

    # -- reading ---------------------------------------------------------- #

    @property
    def consumed(self) -> int:
        """How many raw characters have been scanned so far."""
        return self._consumed

    def value(self, key: str) -> PartialValue | None:
        """The accumulator for ``key``, or None if it has not started yet."""
        return self._values.get(key)

    def text(self, key: str) -> str:
        value = self._values.get(key)
        return value.text if value is not None else ""

    def started(self, key: str) -> bool:
        """True once ``key``'s value has begun streaming (possibly still empty)."""
        return key in self._values

    def first_started(self, keys: Iterable[str]) -> str:
        """The first of ``keys`` whose value has begun streaming, else ''."""
        for key in keys:
            if key in self._values:
                return key
        return ""

    # -- feeding ---------------------------------------------------------- #

    def feed(self, raw: str) -> None:
        """Scan the not-yet-consumed suffix of the cumulative arguments text.

        ``raw`` must be the whole buffer received so far (that is what providers
        hand out), and must only ever grow. A shorter buffer means the caller
        restarted the call on a fresh decoder's behalf, so it is ignored rather
        than re-scanned into corrupt state.
        """
        if len(raw) <= self._consumed:
            return
        chunk = raw[self._consumed :]
        self._consumed = len(raw)
        for char in chunk:
            if self._in_string:
                self._feed_string(char)
            else:
                self._feed_structure(char)

    def _feed_structure(self, char: str) -> None:
        if char == '"':
            self._open_string()
        elif char in "{[":
            self._depth += 1
        elif char in "}]":
            self._depth -= 1
        elif self._depth == 1 and char == ":":
            self._expect_value = True
        elif self._depth == 1 and char == ",":
            self._expect_value = False
            self._key = ""

    def _open_string(self) -> None:
        self._in_string = True
        self._escape = False
        self._unicode = None
        self._high = None
        if self._depth != 1:
            # Inside a nested container: consumed for structure, not decoded.
            self._mode = ""
            self._sink = None
            return
        if not self._expect_value:
            self._mode = "key"
            self._key_parts = []
            self._sink = None
            return
        self._mode = "value"
        self._sink = self._sink_for(self._key)

    def _sink_for(self, key: str) -> PartialValue | None:
        if not key or (self._keys is not None and key not in self._keys):
            return None
        value = self._values.get(key)
        if value is None:
            value = PartialValue()
            self._values[key] = value
        return value

    def _feed_string(self, char: str) -> None:
        if self._unicode is not None:
            self._unicode += char
            if len(self._unicode) == 4:
                digits, self._unicode = self._unicode, None
                self._emit_escaped_codepoint(digits)
            return
        if self._escape:
            self._escape = False
            if char == "u":
                self._unicode = ""
                return
            self._emit(_SIMPLE_ESCAPES.get(char, char))
            return
        if char == "\\":
            # Held back until the escape is complete, which is what keeps the
            # decoded text a stable prefix of the final value.
            self._escape = True
            return
        if char == '"':
            self._close_string()
            return
        self._emit(char)

    def _emit_escaped_codepoint(self, digits: str) -> None:
        try:
            code = int(digits, 16)
        except ValueError:
            # Not a valid \u escape; show it literally rather than dropping it.
            self._emit("\\u" + digits)
            return
        if code in _HIGH_SURROGATE:
            self._flush_surrogate()
            self._high = code
            return
        if code in _LOW_SURROGATE and self._high is not None:
            combined = 0x10000 + ((self._high - 0xD800) << 10) + (code - 0xDC00)
            self._high = None
            self._write(chr(combined))
            return
        self._flush_surrogate()
        self._write(chr(code))

    def _flush_surrogate(self) -> None:
        """Release an unpaired high surrogate, which cannot be rendered alone."""
        if self._high is not None:
            self._high = None
            self._write(_REPLACEMENT)

    def _emit(self, text: str) -> None:
        self._flush_surrogate()
        self._write(text)

    def _write(self, text: str) -> None:
        if self._mode == "key":
            self._key_parts.append(text)
        elif self._sink is not None:
            self._sink.append(text)

    def _close_string(self) -> None:
        self._flush_surrogate()
        self._in_string = False
        if self._mode == "key":
            self._key = "".join(self._key_parts)
        elif self._sink is not None:
            self._sink.closed = True
        self._mode = ""
        self._sink = None
