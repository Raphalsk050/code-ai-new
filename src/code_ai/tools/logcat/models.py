from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class LogFormat(StrEnum):
    """Recognised logcat serialisation formats.

    ``adb logcat`` emits one of several layouts depending on the ``-v`` flag.
    We only need to tell them apart well enough to strip the metadata prefix
    from each physical line; the detectors then work off the bare message.
    """

    THREADTIME = "threadtime"  # ``MM-DD HH:MM:SS.mmm PID TID L TAG: msg``
    TIME = "time"  # ``MM-DD HH:MM:SS.mmm L/TAG( PID): msg``
    BRIEF = "brief"  # ``L/TAG( PID): msg``
    TAG = "tag"  # ``L/TAG: msg``
    RAW = "raw"  # unstructured text (pasted trace, tombstone, bugreport noise)


class LogLevel(StrEnum):
    VERBOSE = "V"
    DEBUG = "D"
    INFO = "I"
    WARN = "W"
    ERROR = "E"
    FATAL = "F"
    SILENT = "S"
    UNKNOWN = "?"

    @classmethod
    def from_token(cls, token: str) -> LogLevel:
        token = (token or "").strip().upper()
        try:
            return cls(token)
        except ValueError:
            return cls.UNKNOWN

    @property
    def rank(self) -> int:
        return _LEVEL_RANK.get(self, 0)


_LEVEL_RANK: dict[LogLevel, int] = {
    LogLevel.VERBOSE: 0,
    LogLevel.DEBUG: 1,
    LogLevel.INFO: 2,
    LogLevel.WARN: 3,
    LogLevel.ERROR: 4,
    LogLevel.FATAL: 5,
    LogLevel.SILENT: 6,
    LogLevel.UNKNOWN: 0,
}


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


class CrashKind(StrEnum):
    JAVA_EXCEPTION = "java_exception"
    NATIVE_CRASH = "native_crash"
    ANR = "anr"
    OUT_OF_MEMORY = "out_of_memory"
    LOW_MEMORY_KILL = "low_memory_kill"
    WATCHDOG = "watchdog"
    MEMORY_LEAK = "memory_leak"


@dataclass(slots=True)
class LogLine:
    """A single parsed physical line of a log stream.

    ``index`` is the 0-based position in the parsed list so detectors can slice
    surrounding context deterministically. ``message`` is the payload with the
    logcat metadata prefix removed (leading whitespace preserved so stack-trace
    frames such as ``\\tat ...`` remain recognisable); ``raw`` keeps the
    original text for faithful context reporting.
    """

    index: int
    raw: str
    message: str
    level: LogLevel = LogLevel.UNKNOWN
    tag: str | None = None
    pid: int | None = None
    tid: int | None = None
    timestamp: str | None = None
    is_continuation: bool = False


@dataclass(slots=True)
class CrashSignal:
    """One detected crash/bug occurrence, before grouping."""

    kind: CrashKind
    severity: Severity
    title: str
    signature: str
    start_index: int
    end_index: int
    timestamp: str | None = None
    process: str | None = None
    pid: int | None = None
    root_cause: str | None = None
    stacktrace: list[str] = field(default_factory=list)
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CrashGroup:
    """Crashes sharing a normalised signature, folded into one entry."""

    signature: str
    kind: CrashKind
    severity: Severity
    title: str
    count: int
    representative: CrashSignal
    first_timestamp: str | None
    last_timestamp: str | None
    occurrence_indices: list[int]

    def to_dict(self) -> dict[str, object]:
        rep = self.representative
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "title": self.title,
            "signature": self.signature,
            "count": self.count,
            "first_seen": self.first_timestamp,
            "last_seen": self.last_timestamp,
            "process": rep.process,
            "pid": rep.pid,
            "root_cause": rep.root_cause,
            "stacktrace": list(rep.stacktrace),
            "context_before": list(rep.context_before),
            "context_after": list(rep.context_after),
            "details": dict(rep.details),
            "line_span": [rep.start_index + 1, rep.end_index + 1],
        }


@dataclass(slots=True)
class AnalysisReport:
    """The compact, structured result handed back to the model.

    Everything here is bounded by construction (number of groups, frames per
    trace, context lines) so a multi-megabyte log never turns into a giant blob
    that gets truncated mid-stack-trace downstream.
    """

    source: str
    log_format: LogFormat
    total_lines: int
    parsed_lines: int
    level_counts: dict[str, int]
    crash_count: int
    groups: list[CrashGroup]
    truncated_groups: bool
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "log_format": self.log_format.value,
            "total_lines": self.total_lines,
            "parsed_lines": self.parsed_lines,
            "level_counts": dict(self.level_counts),
            "crash_count": self.crash_count,
            "group_count": len(self.groups),
            "truncated_groups": self.truncated_groups,
            "summary": self.summary,
            "groups": [group.to_dict() for group in self.groups],
        }
