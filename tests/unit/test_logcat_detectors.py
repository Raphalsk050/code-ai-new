from __future__ import annotations

from code_ai.tools.logcat.analyzer import LogcatAnalyzer
from code_ai.tools.logcat.detectors import (
    AnrDetector,
    JavaExceptionDetector,
    LowMemoryKillDetector,
    MemoryLeakDetector,
    NativeCrashDetector,
    WatchdogDetector,
)
from code_ai.tools.logcat.models import CrashKind, Severity
from code_ai.tools.logcat.parser import LogcatParser


def _parse(text: str):
    parser = LogcatParser()
    return parser.parse(text)


FATAL_JAVA = (
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: FATAL EXCEPTION: main\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: Process: com.acme.app, PID: 4321\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: java.lang.RuntimeException:"
    " Unable to start activity\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: \tat android.app.ActivityThread.perform"
    "(ActivityThread.java:3782)\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: Caused by:"
    " java.lang.NullPointerException: name is null\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: \tat com.acme.app.MainActivity.onCreate"
    "(MainActivity.java:42)\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: \t... 12 more\n"
)


def test_java_detector_extracts_root_cause_and_process() -> None:
    signals = JavaExceptionDetector().detect(_parse(FATAL_JAVA), context_lines=2)
    assert len(signals) == 1
    crash = signals[0]
    assert crash.kind is CrashKind.JAVA_EXCEPTION
    assert crash.severity is Severity.CRITICAL
    assert crash.process == "com.acme.app"
    assert crash.pid == 4321
    assert "NullPointerException" in crash.root_cause
    assert "RuntimeException" in crash.title
    # Signature keys off the deepest cause + its top frame so retries group.
    assert "NullPointerException" in crash.signature
    assert "MainActivity.onCreate" in crash.signature


def test_java_detector_ignores_mere_mention_without_frames() -> None:
    text = (
        "06-01 12:00:00.100  1  1 I Tag: handled java.lang.IllegalStateException gracefully\n"
        "06-01 12:00:00.100  1  1 I Tag: moving on\n"
    )
    assert JavaExceptionDetector().detect(_parse(text), context_lines=0) == []


def test_oom_is_classified_apart_from_generic_exceptions() -> None:
    text = (
        "06-01 12:00:00.100  9  9 E AndroidRuntime: FATAL EXCEPTION: main\n"
        "06-01 12:00:00.100  9  9 E AndroidRuntime: java.lang.OutOfMemoryError:"
        " Failed to allocate a 8388608 byte allocation\n"
        "06-01 12:00:00.100  9  9 E AndroidRuntime: \tat com.acme.Big.alloc(Big.java:7)\n"
    )
    signals = JavaExceptionDetector().detect(_parse(text), context_lines=0)
    assert signals[0].kind is CrashKind.OUT_OF_MEMORY
    assert signals[0].severity is Severity.CRITICAL


NATIVE = (
    "06-01 12:00:01.000  5000  5000 F DEBUG   : *** *** *** *** *** *** *** *** *** ***\n"
    "06-01 12:00:01.000  5000  5000 F DEBUG   : pid: 4999, tid: 4999, name: com.acme.native"
    "  >>> com.acme.native <<<\n"
    "06-01 12:00:01.000  5000  5000 F DEBUG   : signal 11 (SIGSEGV), code 1 (SEGV_MAPERR),"
    " fault addr 0x0\n"
    "06-01 12:00:01.000  5000  5000 F DEBUG   : Abort message: 'null pointer dereference'\n"
    "06-01 12:00:01.000  5000  5000 F DEBUG   : backtrace:\n"
    "06-01 12:00:01.000  5000  5000 F DEBUG   : \t#00 pc 0000000000012abc"
    " /data/app/libnative.so (compute+40)\n"
    "06-01 12:00:01.000  5000  5000 F DEBUG   : \t#01 pc 0000000000034def"
    " /system/lib/libc.so (__start+8)\n"
)


def test_native_detector_builds_tombstone_signal() -> None:
    signals = NativeCrashDetector().detect(_parse(NATIVE), context_lines=1)
    assert len(signals) == 1
    crash = signals[0]
    assert crash.kind is CrashKind.NATIVE_CRASH
    assert crash.severity is Severity.CRITICAL
    assert "SIGSEGV" in crash.signature
    assert "libnative.so" in crash.signature
    assert crash.details["abort_message"] == "null pointer dereference"
    assert crash.details["fault_addr"] == "0x0"
    assert len(crash.stacktrace) == 2


def test_native_detector_skips_signal_without_backtrace() -> None:
    text = "06-01 12:00:01.000  1  1 I Tag: received signal 9 (SIGKILL) from system\n"
    assert NativeCrashDetector().detect(_parse(text), context_lines=0) == []


ANR = (
    "06-01 12:00:02.000  1000  1000 E ActivityManager: ANR in com.acme.app"
    " (com.acme.app/.MainActivity)\n"
    "06-01 12:00:02.000  1000  1000 E ActivityManager: Reason: Input dispatching timed out\n"
    "06-01 12:00:02.000  1000  1000 E ActivityManager: Load: 12.0 / 8.0 / 6.0\n"
)


def test_anr_detector_captures_package_and_reason() -> None:
    signals = AnrDetector().detect(_parse(ANR), context_lines=0)
    assert len(signals) == 1
    crash = signals[0]
    assert crash.kind is CrashKind.ANR
    assert crash.process == "com.acme.app"
    assert "Input dispatching timed out" in crash.root_cause


def test_low_memory_kill_detector() -> None:
    text = (
        "06-01 12:00:03.000  50  50 I lowmemorykiller: Killing 'com.acme.app' (4999)\n"
    )
    signals = LowMemoryKillDetector().detect(_parse(text), context_lines=0)
    assert signals[0].kind is CrashKind.LOW_MEMORY_KILL
    assert signals[0].process == "com.acme.app"


def test_watchdog_detector() -> None:
    text = (
        "06-01 12:00:04.000  600  600 W Watchdog: *** WATCHDOG KILLING SYSTEM PROCESS:"
        " Blocked in handler on main thread\n"
        "06-01 12:00:04.000  600  600 W Watchdog: foregroundThread stack trace\n"
    )
    signals = WatchdogDetector().detect(_parse(text), context_lines=0)
    assert signals[0].kind is CrashKind.WATCHDOG
    assert signals[0].severity is Severity.CRITICAL


def test_memory_leak_detector_flags_leakcanary() -> None:
    text = (
        "06-01 12:00:05.000  700  700 D LeakCanary: ====================================\n"
        "06-01 12:00:05.000  700  700 D LeakCanary: com.acme.app.LeakyActivity instance\n"
        "06-01 12:00:05.000  700  700 D LeakCanary: leaking: YES\n"
    )
    signals = MemoryLeakDetector().detect(_parse(text), context_lines=0)
    assert any(s.kind is CrashKind.MEMORY_LEAK for s in signals)


def test_gc_thrash_heuristic_requires_many_events() -> None:
    healthy = "".join(
        f"06-01 12:00:0{i%10}.000  8  8 I art: Background concurrent copying GC freed\n"
        for i in range(5)
    )
    assert MemoryLeakDetector().detect(_parse(healthy), context_lines=0) == []
    thrash = "".join(
        "06-01 12:00:06.000  8  8 I art: Background concurrent copying GC freed 1MB\n"
        for _ in range(20)
    )
    signals = MemoryLeakDetector().detect(_parse(thrash), context_lines=0)
    assert any("Excessive GC" in s.title for s in signals)


def test_analyzer_groups_repeated_crashes() -> None:
    text = FATAL_JAVA * 3 + NATIVE
    report = LogcatAnalyzer().analyze(text, source="inline")
    assert report.crash_count == 4
    java_group = next(g for g in report.groups if g.kind is CrashKind.JAVA_EXCEPTION)
    assert java_group.count == 3
    assert report.groups[0].severity is Severity.CRITICAL


def test_analyzer_limits_frames_without_cutting_lines() -> None:
    frames = "".join(
        f"06-01 12:00:00.100  9  9 E AndroidRuntime: \tat com.acme.C.m{i}(C.java:{i})\n"
        for i in range(40)
    )
    text = (
        "06-01 12:00:00.100  9  9 E AndroidRuntime: FATAL EXCEPTION: main\n"
        "06-01 12:00:00.100  9  9 E AndroidRuntime: java.lang.RuntimeException: boom\n"
        + frames
    )
    report = LogcatAnalyzer().analyze(text, source="inline", max_frames=14)
    trace = report.groups[0].representative.stacktrace
    assert len(trace) <= 14
    assert any("frames omitted" in line for line in trace)
    # Every kept line is intact (no mid-line character truncation marker).
    assert all(not line.endswith("...[cut]") for line in trace)


def test_analyzer_filters_by_min_severity() -> None:
    text = FATAL_JAVA + (
        "06-01 12:00:05.000  700  700 D LeakCanary: com.acme.app.Leaky instance\n"
        "06-01 12:00:05.000  700  700 D LeakCanary: leaking: YES\n"
    )
    report = LogcatAnalyzer().analyze(text, source="inline", min_severity=Severity.HIGH)
    assert all(g.severity.rank >= Severity.HIGH.rank for g in report.groups)


def test_empty_log_reports_no_crashes() -> None:
    report = LogcatAnalyzer().analyze("", source="inline")
    assert report.crash_count == 0
    assert "no crashes" in report.summary.lower()
