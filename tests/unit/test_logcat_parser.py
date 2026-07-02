from __future__ import annotations

from code_ai.tools.logcat.models import LogFormat, LogLevel
from code_ai.tools.logcat.parser import LogcatParser

THREADTIME = (
    "06-01 12:34:56.789  1234  5678 E AndroidRuntime: FATAL EXCEPTION: main\n"
    "06-01 12:34:56.789  1234  5678 E AndroidRuntime: java.lang.RuntimeException: boom\n"
    "06-01 12:34:56.789  1234  5678 E AndroidRuntime: \tat com.example.App.f(App.java:10)\n"
)

BRIEF = (
    "E/AndroidRuntime( 1234): FATAL EXCEPTION: main\n"
    "E/AndroidRuntime( 1234): java.lang.RuntimeException: boom\n"
)

TIME = (
    "06-01 12:34:56.789 E/AndroidRuntime( 1234): FATAL EXCEPTION: main\n"
    "06-01 12:34:56.789 I/ActivityManager( 999): Start proc\n"
)


def test_detects_threadtime() -> None:
    assert LogcatParser().detect_format(THREADTIME) is LogFormat.THREADTIME


def test_detects_brief() -> None:
    assert LogcatParser().detect_format(BRIEF) is LogFormat.BRIEF


def test_detects_time() -> None:
    assert LogcatParser().detect_format(TIME) is LogFormat.TIME


def test_unstructured_text_is_raw() -> None:
    text = "java.lang.NullPointerException: oops\n\tat com.example.App.f(App.java:1)\n"
    assert LogcatParser().detect_format(text) is LogFormat.RAW


def test_threadtime_line_is_fully_parsed() -> None:
    lines = LogcatParser().parse(THREADTIME)
    first = lines[0]
    assert first.level is LogLevel.ERROR
    assert first.tag == "AndroidRuntime"
    assert first.pid == 1234
    assert first.tid == 5678
    assert first.timestamp == "06-01 12:34:56.789"
    assert first.message == "FATAL EXCEPTION: main"


def test_threadtime_preserves_frame_indentation_in_message() -> None:
    lines = LogcatParser().parse(THREADTIME)
    frame = lines[2]
    # The metadata prefix is stripped but the leading tab that marks a stack
    # frame survives, so detectors can still recognise it.
    assert frame.message.startswith("\tat com.example.App.f")


def test_year_prefixed_timestamp_is_accepted() -> None:
    text = "2026-06-01 12:34:56.789  1234  5678 I Tag: hello\n"
    lines = LogcatParser().parse(text)
    assert lines[0].message == "hello"
    assert lines[0].pid == 1234


def test_raw_lines_do_not_inherit_metadata() -> None:
    text = "java.lang.NullPointerException: oops\n\tat com.example.App.f(App.java:1)\n"
    lines = LogcatParser().parse(text)
    assert lines[0].tag is None
    assert lines[1].message == "\tat com.example.App.f(App.java:1)"
    assert lines[1].is_continuation is False


def test_continuation_lines_inherit_previous_identity() -> None:
    # A structured header followed by a bare continuation line (no prefix).
    text = (
        "06-01 12:34:56.789  1234  5678 E AndroidRuntime: java.lang.RuntimeException: boom\n"
        "continued detail with no prefix\n"
    )
    lines = LogcatParser().parse(text)
    assert lines[1].is_continuation is True
    assert lines[1].tag == "AndroidRuntime"
    assert lines[1].pid == 1234
    assert lines[1].message == "continued detail with no prefix"


def test_index_matches_physical_line_order() -> None:
    lines = LogcatParser().parse(THREADTIME)
    assert [line.index for line in lines] == [0, 1, 2]
