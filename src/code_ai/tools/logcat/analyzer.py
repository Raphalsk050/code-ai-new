from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from code_ai.tools.logcat.detectors import CrashDetector, default_detectors
from code_ai.tools.logcat.models import (
    AnalysisReport,
    CrashGroup,
    CrashKind,
    CrashSignal,
    LogFormat,
    Severity,
)
from code_ai.tools.logcat.parser import LogcatParser

# Defaults keep the report compact by construction so it survives downstream
# size limits without ever being truncated mid-stack-trace.
_DEFAULT_CONTEXT_LINES = 3
_DEFAULT_MAX_GROUPS = 20
_DEFAULT_MAX_FRAMES = 14


@dataclass(slots=True)
class LogcatAnalyzer:
    """Orchestrates parsing and detection into a single structured report.

    The detector list is injected, so callers (or tests) can restrict or extend
    the strategy suite without touching this class.
    """

    parser: LogcatParser = field(default_factory=LogcatParser)
    detectors: Sequence[CrashDetector] = field(default_factory=default_detectors)

    def analyze(
        self,
        text: str,
        *,
        source: str,
        context_lines: int = _DEFAULT_CONTEXT_LINES,
        max_groups: int = _DEFAULT_MAX_GROUPS,
        max_frames: int = _DEFAULT_MAX_FRAMES,
        kinds: frozenset[CrashKind] | None = None,
        min_severity: Severity | None = None,
    ) -> AnalysisReport:
        log_format = self.parser.detect_format(text)
        lines = self.parser.parse(text, log_format=log_format)

        signals: list[CrashSignal] = []
        for detector in self.detectors:
            signals.extend(detector.detect(lines, context_lines=context_lines))

        signals = _dedupe(signals)
        if kinds is not None:
            signals = [signal for signal in signals if signal.kind in kinds]
        if min_severity is not None:
            floor = min_severity.rank
            signals = [signal for signal in signals if signal.severity.rank >= floor]

        for signal in signals:
            signal.stacktrace = _limit_frames(signal.stacktrace, max_frames)

        groups = _group(signals)
        groups.sort(key=lambda group: (group.severity.rank, group.count), reverse=True)
        truncated_groups = len(groups) > max_groups
        visible = groups[:max_groups]

        return AnalysisReport(
            source=source,
            log_format=log_format,
            total_lines=len(lines),
            parsed_lines=sum(1 for line in lines if not line.is_continuation),
            level_counts=_level_counts(lines),
            crash_count=len(signals),
            groups=visible,
            truncated_groups=truncated_groups,
            summary=_summary(log_format, len(lines), signals, visible),
        )


def _dedupe(signals: list[CrashSignal]) -> list[CrashSignal]:
    """Drop signals that start at the same line with the same kind.

    Independent detectors scan for distinct patterns, so genuine overlap is
    rare; this only guards against the same block being reported twice.
    """

    seen: set[tuple[CrashKind, int]] = set()
    unique: list[CrashSignal] = []
    for signal in signals:
        key = (signal.kind, signal.start_index)
        if key in seen:
            continue
        seen.add(key)
        unique.append(signal)
    return unique


def _limit_frames(frames: list[str], max_frames: int) -> list[str]:
    if max_frames <= 0 or len(frames) <= max_frames:
        return frames
    if max_frames < 3:
        return frames[:max_frames]
    # Keep the head (exception header + top frames, where the fault is) and the
    # tail (the deepest cause / root frames); elide the middle by *count*, never
    # by cutting inside a line. The elision marker itself counts as one line, so
    # head + tail = max_frames - 1.
    tail = min(4, max_frames - 2)
    head = max_frames - 1 - tail
    omitted = len(frames) - head - tail
    kept = frames[:head] + [f"\t... ({omitted} frames omitted) ..."]
    if tail:
        kept += frames[-tail:]
    return kept


def _group(signals: list[CrashSignal]) -> list[CrashGroup]:
    order: list[str] = []
    buckets: dict[str, list[CrashSignal]] = {}
    for signal in signals:
        key = f"{signal.kind.value}::{signal.signature}"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(signal)

    groups: list[CrashGroup] = []
    for key in order:
        members = buckets[key]
        representative = max(members, key=lambda item: item.severity.rank)
        stamps = [item.timestamp for item in members if item.timestamp]
        groups.append(
            CrashGroup(
                signature=representative.signature,
                kind=representative.kind,
                severity=representative.severity,
                title=representative.title,
                count=len(members),
                representative=representative,
                first_timestamp=stamps[0] if stamps else None,
                last_timestamp=stamps[-1] if stamps else None,
                occurrence_indices=[item.start_index + 1 for item in members],
            )
        )
    return groups


def _level_counts(lines) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in lines:
        if line.is_continuation:
            continue
        counts[line.level.value] = counts.get(line.level.value, 0) + 1
    return counts


def _summary(
    log_format: LogFormat,
    total_lines: int,
    signals: list[CrashSignal],
    groups: list[CrashGroup],
) -> str:
    if not signals:
        return (
            f"Parsed {total_lines} lines ({log_format.value}); no crashes, ANRs, "
            "OOMs, or leaks detected."
        )
    kind_counts: dict[str, int] = {}
    for signal in signals:
        kind_counts[signal.kind.value] = kind_counts.get(signal.kind.value, 0) + 1
    breakdown = ", ".join(f"{count} {kind}" for kind, count in sorted(kind_counts.items()))
    top = groups[0] if groups else None
    headline = (
        f" Most severe: [{top.severity.value}] {top.title} (x{top.count})." if top else ""
    )
    return (
        f"Parsed {total_lines} lines ({log_format.value}); found {len(signals)} issue(s) "
        f"across {len(groups)} group(s): {breakdown}.{headline}"
    )
