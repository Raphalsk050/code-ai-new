from __future__ import annotations

import re
from typing import Protocol

from code_ai.tools.logcat.models import CrashKind, CrashSignal, LogLine, Severity

# --- shared bounds -----------------------------------------------------------
# A single log line is capped so one pathological line cannot bloat the report;
# a whole trace is capped in frame *count* (never mid-line) by the analyzer.
_MAX_LINE = 300
# Hard ceiling on how many lines any one detector will walk while collecting a
# crash block, guarding against a runaway (e.g. a malformed endless "trace").
_MAX_BLOCK = 500

# --- shared patterns ---------------------------------------------------------
_EXC_HEADER_RE = re.compile(
    r"^(?P<cls>(?:[A-Za-z_$][\w$]*\.)+[A-Za-z_$][\w$]*(?:Exception|Error|Throwable))"
    r"(?::\s?(?P<detail>.*))?$"
)
_CAUSED_BY_RE = re.compile(r"^Caused by:\s*(?P<rest>.*)$")
_MORE_RE = re.compile(r"^\.\.\.\s+\d+\s+more\b")
_FRAME_LOC_RE = re.compile(r"at\s+(?P<loc>[\w$.<>]+)\(")
_PROCESS_RE = re.compile(r"Process:\s*(?P<pkg>[\w.]+)(?:,\s*PID:\s*(?P<pid>\d+))?")


def _cap(text: str) -> str:
    text = text.rstrip("\r\n")
    if len(text) <= _MAX_LINE:
        return text
    return text[:_MAX_LINE] + " ...[cut]"


def _context(
    lines: list[LogLine], start: int, end: int, context_lines: int
) -> tuple[list[str], list[str]]:
    if context_lines <= 0:
        return [], []
    before = [_cap(item.raw) for item in lines[max(0, start - context_lines) : start]]
    after = [_cap(item.raw) for item in lines[end + 1 : end + 1 + context_lines]]
    return before, after


def _strip(message: str) -> str:
    return message.strip()


class CrashDetector(Protocol):
    """Strategy interface: one detector per family of failure.

    Each detector scans the full parsed stream and returns every occurrence it
    recognises. Detectors are stateless and independent, so new failure types
    are added by writing a new class and registering it - the analyzer and tool
    never change (open/closed).
    """

    def detect(self, lines: list[LogLine], *, context_lines: int) -> list[CrashSignal]: ...


# --- Java / Kotlin exceptions ------------------------------------------------


class JavaExceptionDetector:
    """Uncaught JVM exceptions, including ``FATAL EXCEPTION`` and cause chains.

    Also classifies ``OutOfMemoryError`` as its own kind, since it is the one
    place that knows the exception type - keeping OOM knowledge out of every
    other detector.
    """

    def detect(self, lines: list[LogLine], *, context_lines: int) -> list[CrashSignal]:
        signals: list[CrashSignal] = []
        index = 0
        total = len(lines)
        while index < total:
            message = lines[index].message
            is_fatal = "FATAL EXCEPTION" in message
            header = _EXC_HEADER_RE.match(_strip(message))
            if is_fatal or (header is not None and self._has_frame_ahead(lines, index)):
                signal, end = self._collect(
                    lines, index, is_fatal=is_fatal, context_lines=context_lines
                )
                if signal is not None:
                    signals.append(signal)
                    index = end + 1
                    continue
            index += 1
        return signals

    @staticmethod
    def _has_frame_ahead(lines: list[LogLine], index: int) -> bool:
        # A bare exception header only starts a crash when a stack frame follows
        # shortly after; otherwise it is just a log line naming an exception.
        for item in lines[index + 1 : index + 4]:
            if _strip(item.message).startswith("at "):
                return True
        return False

    def _collect(
        self, lines: list[LogLine], start: int, *, is_fatal: bool, context_lines: int
    ) -> tuple[CrashSignal | None, int]:
        anchor = lines[start]
        end = start
        headers: list[tuple[str, str]] = []  # (class, detail)
        top_locations: dict[int, str] = {}  # header ordinal -> first frame loc
        process: str | None = None
        pid: int | None = anchor.pid
        limit = min(len(lines), start + _MAX_BLOCK)

        cursor = start
        while cursor < limit:
            stripped = _strip(lines[cursor].message)
            if cursor != start and not _is_trace_body(stripped):
                break
            end = cursor
            proc = _PROCESS_RE.search(stripped)
            if proc is not None:
                process = proc.group("pkg")
                if proc.group("pid"):
                    pid = int(proc.group("pid"))
            caused = _CAUSED_BY_RE.match(stripped)
            header_text = caused.group("rest") if caused else stripped
            header = _EXC_HEADER_RE.match(header_text)
            if header is not None:
                headers.append((header.group("cls"), (header.group("detail") or "").strip()))
            elif stripped.startswith("at ") and headers:
                loc = _FRAME_LOC_RE.search(stripped)
                if loc is not None:
                    top_locations.setdefault(len(headers) - 1, loc.group("loc"))
            cursor += 1

        if not headers:
            return None, end

        primary_cls, primary_detail = headers[0]
        root_cls, root_detail = headers[-1]
        top_loc = top_locations.get(len(headers) - 1) or top_locations.get(0)
        signature = f"{root_cls} @ {top_loc}" if top_loc else root_cls

        kind = CrashKind.JAVA_EXCEPTION
        severity = Severity.CRITICAL if is_fatal else Severity.HIGH
        if root_cls.endswith("OutOfMemoryError"):
            kind = CrashKind.OUT_OF_MEMORY
            severity = Severity.CRITICAL

        stacktrace = self._clean_trace(lines, start, end)
        before, after = _context(lines, start, end, context_lines)
        return (
            CrashSignal(
                kind=kind,
                severity=severity,
                title=_join(primary_cls, primary_detail),
                signature=signature,
                start_index=start,
                end_index=end,
                timestamp=anchor.timestamp,
                process=process,
                pid=pid,
                root_cause=_join(root_cls, root_detail),
                stacktrace=stacktrace,
                context_before=before,
                context_after=after,
                details={"fatal": "true" if is_fatal else "false"},
            ),
            end,
        )

    @staticmethod
    def _clean_trace(lines: list[LogLine], start: int, end: int) -> list[str]:
        trace: list[str] = []
        for item in lines[start : end + 1]:
            stripped = _strip(item.message)
            if not stripped:
                continue
            if _EXC_HEADER_RE.match(stripped) or _CAUSED_BY_RE.match(stripped):
                trace.append(_cap(stripped))
            elif stripped.startswith("at ") or _MORE_RE.match(stripped):
                trace.append("\t" + _cap(stripped))
            elif stripped.startswith("FATAL EXCEPTION") or stripped.startswith("Process:"):
                trace.append(_cap(stripped))
        return trace


def _is_trace_body(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped.startswith(("at ", "Caused by:", "Suppressed:", "Process:")):
        return True
    if _MORE_RE.match(stripped):
        return True
    return _EXC_HEADER_RE.match(stripped) is not None


# --- Native crashes / tombstones --------------------------------------------

_SIGNAL_RE = re.compile(r"signal\s+(?P<num>\d+)\s+\((?P<name>SIG[A-Z]+)\)")
_NATIVE_FRAME_RE = re.compile(
    r"#\d+\s+pc\s+[0-9a-fA-F]+\s+(?P<path>\S+)(?:\s+\((?P<sym>[^)]*)\))?"
)
_ABORT_RE = re.compile(r"Abort message:\s*'(?P<msg>.*)'")
_PROC_NAME_RE = re.compile(r">>>\s*(?P<name>\S+)\s*<<<")
_TOMBSTONE_DIVIDER = "*** *** ***"


class NativeCrashDetector:
    """Native (NDK) crashes: SIGSEGV/SIGABRT tombstones with a backtrace."""

    def detect(self, lines: list[LogLine], *, context_lines: int) -> list[CrashSignal]:
        signals: list[CrashSignal] = []
        index = 0
        total = len(lines)
        while index < total:
            if _SIGNAL_RE.search(lines[index].message):
                signal, end = self._collect(lines, index, context_lines=context_lines)
                if signal is not None:
                    signals.append(signal)
                    index = end + 1
                    continue
            index += 1
        return signals

    def _collect(
        self, lines: list[LogLine], anchor_index: int, *, context_lines: int
    ) -> tuple[CrashSignal | None, int]:
        anchor = lines[anchor_index]
        start = self._find_start(lines, anchor_index)
        signal_match = _SIGNAL_RE.search(anchor.message)
        signal_text = signal_match.group(0) if signal_match else "signal"

        frames: list[str] = []
        process: str | None = None
        abort: str | None = None
        fault_addr: str | None = None
        end = anchor_index
        cursor = anchor_index
        limit = min(len(lines), anchor_index + _MAX_BLOCK)
        gap = 0
        while cursor < limit:
            stripped = _strip(lines[cursor].message)
            frame = _NATIVE_FRAME_RE.search(stripped)
            if frame is not None:
                frames.append("\t" + _cap(stripped))
                end = cursor
                gap = 0
            else:
                if process is None:
                    proc = _PROC_NAME_RE.search(stripped)
                    if proc is not None:
                        process = proc.group("name")
                if abort is None:
                    abort_match = _ABORT_RE.search(stripped)
                    if abort_match is not None:
                        abort = abort_match.group("msg")
                        end = cursor
                if fault_addr is None and "fault addr" in stripped:
                    fault = re.search(r"fault addr\s+(?P<addr>\S+)", stripped)
                    if fault is not None:
                        fault_addr = fault.group("addr")
                if frames:
                    gap += 1
                    if gap > 3:  # backtrace has clearly ended
                        break
            cursor += 1

        if not frames:
            # A stray "signal N" mention with no backtrace is not a tombstone.
            return None, anchor_index

        top = _native_top_frame(frames)
        signature = f"{signal_text} @ {top}" if top else signal_text
        details = {"signal": signal_text}
        if fault_addr:
            details["fault_addr"] = fault_addr
        if abort:
            details["abort_message"] = _cap(abort)
        before, after = _context(lines, start, end, context_lines)
        return (
            CrashSignal(
                kind=CrashKind.NATIVE_CRASH,
                severity=Severity.CRITICAL,
                title=f"Native crash ({signal_text})"
                + (f" in {process}" if process else ""),
                signature=signature,
                start_index=start,
                end_index=end,
                timestamp=anchor.timestamp,
                process=process,
                pid=anchor.pid,
                root_cause=abort or signal_text,
                stacktrace=frames,
                context_before=before,
                context_after=after,
                details=details,
            ),
            end,
        )

    @staticmethod
    def _find_start(lines: list[LogLine], anchor_index: int) -> int:
        for offset in range(1, 12):
            probe = anchor_index - offset
            if probe < 0:
                break
            stripped = _strip(lines[probe].message)
            if _TOMBSTONE_DIVIDER in stripped or stripped.startswith("pid:"):
                return probe
        return anchor_index


def _native_top_frame(frames: list[str]) -> str | None:
    for frame in frames:
        match = _NATIVE_FRAME_RE.search(frame)
        if match is None:
            continue
        path = match.group("path").rsplit("/", 1)[-1]
        sym = (match.group("sym") or "").split("+")[0].strip()
        return f"{path} ({sym})" if sym else path
    return None


# --- ANRs --------------------------------------------------------------------

_ANR_RE = re.compile(r"^ANR in (?P<pkg>\S+)")
_REASON_RE = re.compile(r"^Reason:\s*(?P<reason>.*)$")


class AnrDetector:
    """Application Not Responding events reported by ActivityManager."""

    def detect(self, lines: list[LogLine], *, context_lines: int) -> list[CrashSignal]:
        signals: list[CrashSignal] = []
        index = 0
        total = len(lines)
        while index < total:
            match = _ANR_RE.match(_strip(lines[index].message))
            if match is not None:
                signal, end = self._collect(
                    lines, index, package=match.group("pkg"), context_lines=context_lines
                )
                signals.append(signal)
                index = end + 1
                continue
            index += 1
        return signals

    def _collect(
        self, lines: list[LogLine], start: int, *, package: str, context_lines: int
    ) -> tuple[CrashSignal, int]:
        anchor = lines[start]
        reason: str | None = None
        detail_lines: list[str] = [_cap(_strip(anchor.message))]
        end = start
        limit = min(len(lines), start + _MAX_BLOCK)
        for cursor in range(start + 1, limit):
            stripped = _strip(lines[cursor].message)
            reason_match = _REASON_RE.match(stripped)
            if reason_match is not None:
                reason = reason_match.group("reason")
            if stripped.startswith(("Reason:", "Load:", "CPU usage", "PID:")) or (
                stripped.startswith('"main"') or stripped.startswith("at ")
            ):
                detail_lines.append(_cap(stripped))
                end = cursor
            elif cursor - start > 1 and not stripped:
                break
            elif cursor > end + 2:
                break
        signature = f"ANR {package}" + (f" | {reason}" if reason else "")
        return (
            CrashSignal(
                kind=CrashKind.ANR,
                severity=Severity.HIGH,
                title=f"ANR in {package}" + (f": {reason}" if reason else ""),
                signature=signature,
                start_index=start,
                end_index=end,
                timestamp=anchor.timestamp,
                process=package,
                pid=anchor.pid,
                root_cause=reason,
                stacktrace=detail_lines[:_MAX_BLOCK],
                context_before=_context(lines, start, end, context_lines)[0],
                context_after=_context(lines, start, end, context_lines)[1],
                details={"reason": reason} if reason else {},
            ),
            end,
        )


# --- Low-memory kills (kernel / lmkd) ---------------------------------------

_LMK_PATTERNS = (
    re.compile(r"Out of memory:\s*Kill(?:ed)? process\s+(?P<pid>\d+)\s*\((?P<name>[^)]+)\)"),
    re.compile(r"lowmemorykiller:.*'(?P<name>[^']+)'"),
    re.compile(r"lmkd.*Kill(?:ing)?\s+'(?P<name>[^']+)'"),
)


class LowMemoryKillDetector:
    """Processes reaped by the kernel OOM killer or userspace lmkd."""

    def detect(self, lines: list[LogLine], *, context_lines: int) -> list[CrashSignal]:
        signals: list[CrashSignal] = []
        for line in lines:
            # The daemon/kernel name (``lowmemorykiller``/``lmkd``) lives in the
            # tag, which is stripped from ``message``; match the raw line so the
            # pattern can anchor on it, then display the readable message.
            stripped = _strip(line.message)
            for pattern in _LMK_PATTERNS:
                match = pattern.search(line.raw)
                if match is None:
                    continue
                name = match.groupdict().get("name") or "unknown"
                before, after = _context(lines, line.index, line.index, context_lines)
                signals.append(
                    CrashSignal(
                        kind=CrashKind.LOW_MEMORY_KILL,
                        severity=Severity.MEDIUM,
                        title=f"Low-memory kill: {name}",
                        signature=f"lmk {name}",
                        start_index=line.index,
                        end_index=line.index,
                        timestamp=line.timestamp,
                        process=name,
                        pid=line.pid,
                        root_cause="Process killed under memory pressure",
                        stacktrace=[_cap(stripped)],
                        context_before=before,
                        context_after=after,
                    )
                )
                break
        return signals


# --- Watchdog kills ----------------------------------------------------------

_WATCHDOG_SUBSYSTEM_RE = re.compile(r"Blocked in (?:handler on )?(?P<sub>[\w /.$]+)")


class WatchdogDetector:
    """system_server watchdog aborts (soft reboots)."""

    def detect(self, lines: list[LogLine], *, context_lines: int) -> list[CrashSignal]:
        signals: list[CrashSignal] = []
        index = 0
        total = len(lines)
        while index < total:
            line = lines[index]
            message = line.message
            if "WATCHDOG" in message.upper() and (
                "watchdog" == (line.tag or "").lower()
                or "KILLING" in message.upper()
                or "Watchdog" in message
            ):
                signal, end = self._collect(lines, index, context_lines=context_lines)
                signals.append(signal)
                index = end + 1
                continue
            index += 1
        return signals

    def _collect(
        self, lines: list[LogLine], start: int, *, context_lines: int
    ) -> tuple[CrashSignal, int]:
        anchor = lines[start]
        block: list[str] = []
        subsystem: str | None = None
        end = start
        limit = min(len(lines), start + _MAX_BLOCK)
        for cursor in range(start, limit):
            line = lines[cursor]
            if cursor != start and (line.tag or "").lower() != "watchdog":
                break
            stripped = _strip(line.message)
            if subsystem is None:
                sub_match = _WATCHDOG_SUBSYSTEM_RE.search(stripped)
                if sub_match is not None:
                    subsystem = sub_match.group("sub").strip()
            block.append(_cap(stripped))
            end = cursor
        signature = f"watchdog {subsystem}" if subsystem else "watchdog"
        return (
            CrashSignal(
                kind=CrashKind.WATCHDOG,
                severity=Severity.CRITICAL,
                title="Watchdog kill" + (f": {subsystem}" if subsystem else ""),
                signature=signature,
                start_index=start,
                end_index=end,
                timestamp=anchor.timestamp,
                process="system_server",
                pid=anchor.pid,
                root_cause=f"Blocked in {subsystem}" if subsystem else "system_server watchdog",
                stacktrace=block[:_MAX_BLOCK],
                context_before=_context(lines, start, end, context_lines)[0],
                context_after=_context(lines, start, end, context_lines)[1],
                details={"subsystem": subsystem} if subsystem else {},
            ),
            end,
        )


# --- Memory leaks / GC thrash ------------------------------------------------

_LEAK_CLASS_RE = re.compile(r"(?P<cls>[\w$.]+) instance$|leaking:.*?(?P<cls2>[\w$.]+)")
_GC_MARKERS = (
    "GC_FOR_ALLOC",
    "concurrent copying GC",
    "WaitForGcToComplete",
    "Clamp target GC heap",
    "Grow heap",
    "GC_EXPLICIT",
)
# A single process must exceed this many GC events to be flagged as thrashing,
# keeping the heuristic quiet on healthy logs that GC occasionally.
_GC_THRESHOLD = 12


class MemoryLeakDetector:
    """LeakCanary reports plus a conservative GC-thrash heuristic."""

    def detect(self, lines: list[LogLine], *, context_lines: int) -> list[CrashSignal]:
        return self._leakcanary(lines, context_lines) + self._gc_thrash(lines, context_lines)

    def _leakcanary(self, lines: list[LogLine], context_lines: int) -> list[CrashSignal]:
        signals: list[CrashSignal] = []
        index = 0
        total = len(lines)
        while index < total:
            line = lines[index]
            is_leak = ("leakcanary" in (line.tag or "").lower()) or (
                "LeakCanary" in line.message and "leak" in line.message.lower()
            )
            if not is_leak:
                index += 1
                continue
            end = index
            block: list[str] = []
            limit = min(total, index + _MAX_BLOCK)
            for cursor in range(index, limit):
                probe = lines[cursor]
                if cursor != index and "leakcanary" not in (probe.tag or "").lower():
                    break
                block.append(_cap(_strip(probe.message)))
                end = cursor
            leaking = self._leaking_class(block) or "unknown"
            before, after = _context(lines, index, end, context_lines)
            signals.append(
                CrashSignal(
                    kind=CrashKind.MEMORY_LEAK,
                    severity=Severity.MEDIUM,
                    title=f"Memory leak (LeakCanary): {leaking}",
                    signature=f"leak {leaking}",
                    start_index=index,
                    end_index=end,
                    timestamp=line.timestamp,
                    process=line.tag,
                    pid=line.pid,
                    root_cause=f"Retained instance of {leaking}",
                    stacktrace=block[:_MAX_BLOCK],
                    context_before=before,
                    context_after=after,
                )
            )
            index = end + 1
        return signals

    @staticmethod
    def _leaking_class(block: list[str]) -> str | None:
        for entry in block:
            match = _LEAK_CLASS_RE.search(entry)
            if match is not None:
                return match.group("cls") or match.group("cls2")
        return None

    def _gc_thrash(self, lines: list[LogLine], context_lines: int) -> list[CrashSignal]:
        by_pid: dict[int | None, list[LogLine]] = {}
        for line in lines:
            if any(marker in line.message for marker in _GC_MARKERS):
                by_pid.setdefault(line.pid, []).append(line)
        signals: list[CrashSignal] = []
        for pid, events in by_pid.items():
            if len(events) < _GC_THRESHOLD:
                continue
            first = events[0]
            before, after = _context(lines, first.index, first.index, context_lines)
            signals.append(
                CrashSignal(
                    kind=CrashKind.MEMORY_LEAK,
                    severity=Severity.MEDIUM,
                    title=f"Excessive GC / memory pressure (pid {pid})",
                    signature=f"gc-thrash {pid}",
                    start_index=first.index,
                    end_index=events[-1].index,
                    timestamp=first.timestamp,
                    process=first.tag,
                    pid=pid,
                    root_cause=f"{len(events)} GC events - likely churn or a leak",
                    stacktrace=[_cap(_strip(first.message))],
                    context_before=before,
                    context_after=after,
                    details={"gc_events": str(len(events))},
                )
            )
        return signals


def default_detectors() -> list[CrashDetector]:
    """The built-in detector suite, ordered most-specific first."""

    return [
        JavaExceptionDetector(),
        NativeCrashDetector(),
        AnrDetector(),
        WatchdogDetector(),
        LowMemoryKillDetector(),
        MemoryLeakDetector(),
    ]


def _join(cls: str, detail: str) -> str:
    return _cap(f"{cls}: {detail}" if detail else cls)
