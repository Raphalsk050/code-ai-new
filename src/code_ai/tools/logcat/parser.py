from __future__ import annotations

import re
from dataclasses import dataclass

from code_ai.tools.logcat.models import LogFormat, LogLevel, LogLine

# Timestamp fragment shared by the timestamped formats. The year is optional
# because ``adb logcat`` omits it by default but ``-v year`` includes it.
_TS = r"(?:\d{4}-)?\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}"

_THREADTIME_RE = re.compile(
    rf"^(?P<ts>{_TS})\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEFS])\s+(?P<tag>.*?):\s?(?P<msg>.*)$"
)
_TIME_RE = re.compile(
    rf"^(?P<ts>{_TS})\s+(?P<level>[VDIWEFS])/(?P<tag>.*?)\(\s*(?P<pid>\d+)\):\s?(?P<msg>.*)$"
)
_BRIEF_RE = re.compile(
    r"^(?P<level>[VDIWEFS])/(?P<tag>.*?)\(\s*(?P<pid>\d+)\):\s?(?P<msg>.*)$"
)
_TAG_RE = re.compile(r"^(?P<level>[VDIWEFS])/(?P<tag>.*?):\s?(?P<msg>.*)$")

_FORMAT_PATTERNS: tuple[tuple[LogFormat, re.Pattern[str]], ...] = (
    (LogFormat.THREADTIME, _THREADTIME_RE),
    (LogFormat.TIME, _TIME_RE),
    (LogFormat.BRIEF, _BRIEF_RE),
    (LogFormat.TAG, _TAG_RE),
)

# How many non-empty lines to sample when guessing the format, and the minimum
# fraction of them a candidate must match to win.
_SAMPLE_LINES = 400
_MATCH_THRESHOLD = 0.4


@dataclass(slots=True)
class LogcatParser:
    """Turns raw log text into a flat list of :class:`LogLine` records.

    The parser is deliberately format-tolerant: it detects the dominant logcat
    layout, strips the metadata prefix from lines that match it, and treats
    everything else as a continuation of the preceding entry (which is exactly
    how multi-line Java stack traces and native tombstones appear). This keeps
    the downstream detectors working purely on message content regardless of
    whether the input is filtered logcat, a raw dump, a bugreport, or a stack
    trace pasted with no logcat prefix at all.
    """

    def detect_format(self, text: str) -> LogFormat:
        counts: dict[LogFormat, int] = {fmt: 0 for fmt, _ in _FORMAT_PATTERNS}
        sampled = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            sampled += 1
            for fmt, pattern in _FORMAT_PATTERNS:
                if pattern.match(line):
                    counts[fmt] += 1
                    break  # first (most specific) match wins for this line
            if sampled >= _SAMPLE_LINES:
                break
        if sampled == 0:
            return LogFormat.RAW
        best_fmt, best_count = max(counts.items(), key=lambda item: item[1])
        if best_count / sampled >= _MATCH_THRESHOLD:
            return best_fmt
        return LogFormat.RAW

    def parse(self, text: str, *, log_format: LogFormat | None = None) -> list[LogLine]:
        fmt = log_format or self.detect_format(text)
        pattern = dict(_FORMAT_PATTERNS).get(fmt)
        lines: list[LogLine] = []
        last_structured: LogLine | None = None
        for index, raw in enumerate(text.splitlines()):
            message = raw.rstrip("\r")
            match = pattern.match(message) if pattern is not None else None
            if match is not None:
                line = _from_match(index, raw, match)
                last_structured = line
                lines.append(line)
                continue
            # Unmatched line: a continuation frame, a blank line, or free-form
            # text (RAW format). Inherit the previous entry's identity so a
            # trace pasted without prefixes still attaches to its crash.
            inherit = last_structured if fmt is not LogFormat.RAW else None
            lines.append(
                LogLine(
                    index=index,
                    raw=raw,
                    message=message,
                    level=inherit.level if inherit else LogLevel.UNKNOWN,
                    tag=inherit.tag if inherit else None,
                    pid=inherit.pid if inherit else None,
                    tid=inherit.tid if inherit else None,
                    timestamp=inherit.timestamp if inherit else None,
                    is_continuation=inherit is not None,
                )
            )
        return lines


def _from_match(index: int, raw: str, match: re.Match[str]) -> LogLine:
    groups = match.groupdict()
    pid = _to_int(groups.get("pid"))
    tid = _to_int(groups.get("tid"))
    tag = (groups.get("tag") or "").strip() or None
    return LogLine(
        index=index,
        raw=raw,
        message=groups.get("msg", ""),
        level=LogLevel.from_token(groups.get("level", "")),
        tag=tag,
        pid=pid,
        tid=tid,
        timestamp=(groups.get("ts") or None),
    )


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
