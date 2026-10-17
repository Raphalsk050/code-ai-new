from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import code_ai.desktop_avatar as desktop_avatar
from code_ai.desktop_avatar import DesktopAvatar
from code_ai.desktop_avatar.mood import Mood, tool_pose

# Stands in for code-ai-avatar: writes down every line it is given, and
# answers a pose the way the page does when it comes to rest.
FAKE_HOST = """
import json, sys
log = open(sys.argv[1], "a", encoding="utf-8")
for line in sys.stdin:
    log.write(line)
    log.flush()
    if "pose" in json.loads(line):
        print(json.dumps({"rest": {"right": False, "bottom": True, "x": 40, "y": 12}}), flush=True)
"""


def feed(mood: Mood, *events: tuple[str, dict]) -> list[dict]:
    out: list[dict] = []
    for kind, payload in events:
        out += mood.feed(kind, payload)
    return out


def test_the_pose_follows_the_turn() -> None:
    mood = Mood()

    assert feed(mood, ("user.message", {}), ("status.changed", {"state": "CALLING_MODEL"})) == [
        {"pose": "think"}
    ]
    assert feed(mood, ("model.stream.delta", {})) == [{"pose": "type"}]
    assert feed(
        mood,
        ("tool.call.started", {"tool_call_id": "a", "name": "search_files"}),
        ("status.changed", {"state": "EXECUTING_TOOL"}),
    ) == [{"pose": "think"}, {"pose": "search"}]  # the page holds a pose 400 ms: no flicker
    assert feed(mood, ("tool.call.started", {"tool_call_id": "b", "name": "write_file"})) == [
        {"pose": "type"}
    ]
    assert feed(mood, ("status.changed", {"state": "READY"})) == [
        {"pose": "idle"},
        {"cue": "went-well"},
    ]


def test_a_plain_answer_is_nothing_to_it_and_an_error_is() -> None:
    mood = Mood()
    feed(mood, ("user.message", {}), ("status.changed", {"state": "CALLING_MODEL"}))
    assert {"cue": "went-well"} not in feed(mood, ("status.changed", {"state": "READY"}))

    feed(mood, ("user.message", {}), ("status.changed", {"state": "CALLING_MODEL"}))
    assert {"cue": "went-wrong"} in feed(mood, ("status.changed", {"state": "ERROR"}))


def test_it_waits_on_an_approval_for_the_turn_and_on_a_question_past_it() -> None:
    mood = Mood()
    feed(mood, ("status.changed", {"state": "EXECUTING_TOOL"}))

    assert {"waiting": True} in feed(mood, ("tool.approval.requested", {"call_id": "c"}))
    assert feed(mood, ("tool.approval.resolved", {"call_id": "c", "approved": False})) == [
        {"cue": "refused"},
        {"waiting": False},
    ]
    feed(mood, ("tool.approval.requested", {"call_id": "d"}))
    assert {"waiting": False} in feed(mood, ("status.changed", {"state": "READY"}))

    # Asking ends the turn: the question still waits after it.
    feed(mood, ("status.changed", {"state": "EXECUTING_TOOL"}))
    feed(mood, ("interaction.question.requested", {"id": "q"}))
    assert mood.waiting
    feed(mood, ("status.changed", {"state": "READY"}))
    assert mood.waiting
    assert feed(mood, ("interaction.question.answered", {"answer": "yes"})) == [
        {"cue": "answered"},
        {"waiting": False},
    ]


def test_a_new_conversation_is_greeted() -> None:
    assert Mood().feed("conversation.reset", {}) == [{"cue": "greet"}]


@pytest.mark.parametrize(
    ("name", "pose"),
    [
        ("read_file", "search"),
        ("web_search", "search"),
        ("browser_open", "search"),
        ("write_file", "type"),
        ("execute_command", "type"),
        ("docx_create", "type"),
        ("dispatch_agent", "think"),
        ("something_else", "think"),
    ],
)
def test_each_tool_has_its_pose(name: str, pose: str) -> None:
    assert tool_pose(name) == pose


def _until(check, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("never happened")


def _lines(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _avatar(tmp_path: Path, on: bool = True, name: str = "host.log"):
    script = tmp_path / "host.py"
    script.write_text(FAKE_HOST, encoding="utf-8")
    log = tmp_path / name
    config = SimpleNamespace(desktop_avatar=on, language="pt-BR")
    handlers: list = []
    app = SimpleNamespace(subscribe=handlers.append)
    avatar = DesktopAvatar(
        lambda: config,
        state_dir=tmp_path,
        command=lambda: [sys.executable, str(script), str(log)],
    )

    def emit(kind: str, **payload) -> None:
        for handler in handlers:
            handler(SimpleNamespace(event_type=kind, payload=payload))

    return avatar, app, config, log, emit


def test_it_shows_only_once_switched_on(tmp_path: Path) -> None:
    avatar, app, config, log, _ = _avatar(tmp_path, on=False)
    avatar.attach(app)
    assert not avatar.running

    config.desktop_avatar = True
    assert avatar.set_enabled(True) is None
    try:
        first = _until(lambda: _lines(log))[0]
        assert first == {"at": None, "lang": "pt-BR"}
    finally:
        avatar.close()
    assert not avatar.running


def test_the_turn_reaches_it_and_where_it_rests_is_kept(tmp_path: Path) -> None:
    avatar, app, _, log, emit = _avatar(tmp_path)
    avatar.attach(app)
    try:
        emit("user.message")
        emit("status.changed", state="CALLING_MODEL")
        _until(lambda: {"pose": "think"} in _lines(log))
        saved = _until(lambda: (tmp_path / "desktop-avatar.json").exists())
        assert saved
    finally:
        avatar.close()

    # Shown again, it starts where it was left.
    log.unlink()
    avatar.set_enabled(True)
    try:
        first = _until(lambda: _lines(log))[0]
        assert first["at"] == {"right": False, "bottom": True, "x": 40, "y": 12}
    finally:
        avatar.close()


def test_one_desktop_shows_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(desktop_avatar, "_LOCK_RETRY_S", 0.0)
    first, first_app, _, _, _ = _avatar(tmp_path, name="first.log")
    second, second_app, _, second_log, emit = _avatar(tmp_path, name="second.log")
    first.attach(first_app)
    second.attach(second_app)
    try:
        assert first.running
        assert not second.running and second.problem is None

        first.close()
        emit("status.changed", state="CALLING_MODEL")
        assert second.running
        _until(lambda: _lines(second_log))
    finally:
        first.close()
        second.close()


def test_a_host_that_cannot_start_is_reported_once(tmp_path: Path) -> None:
    config = SimpleNamespace(desktop_avatar=True, language="en")
    handlers: list = []
    starts: list[int] = []

    def command():
        starts.append(1)
        said = "print('libwebkit2gtk-4.1.so.0: not found', file=sys.stderr)"
        return [sys.executable, "-c", f"import sys; {said}; sys.exit(127)"]

    avatar = DesktopAvatar(lambda: config, state_dir=tmp_path, command=command)
    avatar.attach(SimpleNamespace(subscribe=handlers.append))
    _until(lambda: avatar._process is not None and avatar._process.poll() is not None)
    time.sleep(0.3)  # what it said on the way out
    busy = SimpleNamespace(event_type="status.changed", payload={"state": "CALLING_MODEL"})
    for _ in range(3):
        handlers[0](busy)

    assert "libwebkit2gtk" in (avatar.problem or "")
    assert len(starts) == 1


def test_switching_it_on_says_at_once_when_the_host_cannot_start(tmp_path: Path) -> None:
    config = SimpleNamespace(desktop_avatar=False, language="en")
    said = "print('WebView2 runtime not found', file=sys.stderr)"
    avatar = DesktopAvatar(
        lambda: config,
        state_dir=tmp_path,
        command=lambda: [sys.executable, "-c", f"import sys; {said}; sys.exit(1)"],
    )
    avatar.attach(SimpleNamespace(subscribe=lambda handler: None))

    problem = avatar.set_enabled(True)

    assert problem is not None and "WebView2 runtime not found" in problem
    assert not avatar.running


def test_a_build_without_the_host_says_so(tmp_path: Path) -> None:
    config = SimpleNamespace(desktop_avatar=False, language="en")
    avatar = DesktopAvatar(lambda: config, state_dir=tmp_path, command=lambda: None)
    avatar.attach(SimpleNamespace(subscribe=lambda handler: None))

    assert avatar.set_enabled(True) == "The desktop avatar is not part of this build."
    assert not avatar.running


def test_the_page_and_the_host_are_found_beside_the_package(tmp_path: Path, monkeypatch) -> None:
    page = tmp_path / "avatar.html"
    assert desktop_avatar.host_command(page) is None  # no page, nothing to show

    page.write_text("<html></html>", encoding="utf-8")
    host = tmp_path / desktop_avatar.HOST_NAME
    host.write_bytes(b"")
    monkeypatch.setenv(desktop_avatar.HOST_ENV, str(host))
    command = desktop_avatar.host_command(page)

    if sys.platform == "darwin":
        assert command == [sys.executable, "-m", "code_ai.desktop_avatar.cocoa", str(page)]
    else:
        assert command == [str(host), str(page)]
