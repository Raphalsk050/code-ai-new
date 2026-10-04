from __future__ import annotations

import shutil
import sys
import time
from collections.abc import Callable

import pytest

from code_ai.tools.terminal.shell_integration import ShellIntegration, prepare_shell, shell_name

A, B, C = "\x1b]633;A\x07", "\x1b]633;B\x07", "\x1b]633;C\x07"


def D(code: int | None = None) -> str:
    return f"\x1b]633;D{'' if code is None else f';{code}'}\x07"


def clock() -> Callable[[], float]:
    ticks = iter(range(1000, 100000, 250))
    return lambda: next(ticks) / 1000


def test_marks_are_removed_and_cut_the_stream_into_blocks() -> None:
    shell = ShellIntegration(clock=clock())
    shown = shell.feed(f"{A}$ {B}")
    assert shown == "$ " and shell.state == "input" and shell.active

    shell.typed("make test", "agent")
    shell.submitted("agent")
    shown = shell.feed(f"make test\r\n{C}building\r\n\x1b[31m1 failed\x1b[0m\r\n{D(2)}{A}$ {B}")
    assert "\x1b]633" not in shown

    [block] = shell.blocks
    assert (block.command, block.author, block.exit_code) == ("make test", "agent", 2)
    assert block.output == "building\n\x1b[31m1 failed\x1b[0m"
    assert block.ended_at is not None and block.ended_at > block.started_at
    assert shell.running is None and shell.state == "input"


def test_a_shell_without_the_start_mark_still_gets_blocks() -> None:
    # cmd.exe and bash 3 never send C: the block opens on Enter, the echo of
    # the newline is trimmed, and D without a code leaves the code unknown.
    shell = ShellIntegration(clock=clock())
    shell.feed(f"{D()}{A}C:\\w>{B}")
    shell.typed("dir\n", "user")
    shell.feed("\r\n Volume in drive C\r\n")
    assert shell.running is not None and shell.running.author == "user"
    shell.feed(f"{D()}{A}C:\\w>{B}")
    [block] = shell.blocks
    assert block.command == "dir" and block.exit_code is None and not block.running
    assert block.output == " Volume in drive C"


def test_the_shell_names_the_command_and_the_directory() -> None:
    shell = ShellIntegration(clock=clock())
    shell.feed(f"\x1b]633;P;Cwd=/w/a\\x3bb\x07{A}{B}")
    shell.feed(f"\x1b]633;E;echo a\\x3b echo b\x07{C}a\r\nb\r\n{D(0)}")
    [block] = shell.blocks
    assert block.command == "echo a; echo b" and block.cwd == "/w/a;b" and block.exit_code == 0


def test_a_mark_split_across_reads_is_put_back_together() -> None:
    shell = ShellIntegration(clock=clock())
    shown = shell.feed("hello \x1b]63")
    shown += shell.feed("3;A\x07world \x1b]633;B")
    shown += shell.feed("\x07!")
    assert shown == "hello world !" and shell.state == "input"


def test_carriage_returns_overwrite_and_cursor_moves_are_dropped() -> None:
    shell = ShellIntegration(clock=clock())
    shell.feed(f"{A}{B}")
    shell.typed("pip install x\n", "agent")
    shell.feed(f"{C}10%\r50%\r100%\r\n\x1b[2K\x1b[1Gdone\r\n{D(0)}")
    assert shell.blocks[0].output == "100%\ndone"


def test_output_is_capped_and_blocks_are_bounded() -> None:
    shell = ShellIntegration(clock=clock(), max_blocks=3, max_output_chars=10)
    for n in range(5):
        shell.feed(f"{A}{B}")
        shell.typed(f"echo {n}\n", "user")
        shell.feed(f"{C}{'x' * 25}\r\n{D(0)}")
    assert [b.command for b in shell.blocks] == ["echo 2", "echo 3", "echo 4"]
    assert all(b.truncated and len(b.output) <= 10 for b in shell.blocks)
    payload = shell.payload(max_blocks=2, max_output=4)
    assert [b["command"] for b in payload["blocks"]] == ["echo 3", "echo 4"]
    assert payload["integrated"] is True and payload["running"] is False


def test_an_unmarked_shell_leaves_the_stream_alone() -> None:
    shell = ShellIntegration(clock=clock())
    shell.typed("ls\n", "user")
    assert shell.feed("a b c\r\n$ ") == "a b c\r\n$ "
    assert shell.blocks == [] and not shell.active


def test_only_a_bare_known_shell_is_prepared(tmp_path) -> None:
    assert shell_name(r'"C:\Program Files\PowerShell\7\pwsh.exe"') == "pwsh"
    plain = prepare_shell("python manage.py runserver", {"A": "1"}, scratch_root=tmp_path)
    assert plain.command == "python manage.py runserver"
    assert plain.shell == "" and plain.scratch is None

    cmd = prepare_shell(r"C:\Windows\system32\cmd.exe", {"PROMPT": "$P$G"}, scratch_root=tmp_path)
    assert cmd.env["PROMPT"].startswith("$e]633;D$e\\") and "$P$G" in cmd.env["PROMPT"]

    bash = prepare_shell("/bin/bash", {"HOME": str(tmp_path)}, scratch_root=tmp_path)
    assert bash.shell == "bash" and "--init-file" in bash.command and bash.scratch is not None
    assert (bash.scratch / "bashrc").read_text(encoding="utf-8").count("633;") >= 4

    zsh = prepare_shell("zsh", {"HOME": "/home/u"}, scratch_root=tmp_path)
    assert zsh.env["ZDOTDIR"] == str(zsh.scratch) and zsh.env["CODEAI_ZDOTDIR"] == "/home/u"

    pwsh = prepare_shell("pwsh", {}, scratch_root=tmp_path)
    assert "-NoExit" in pwsh.command and (pwsh.scratch / "integration.ps1").exists()


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")
def test_a_real_bash_marks_its_commands(tmp_path) -> None:
    pexpect = pytest.importorskip("pexpect")
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "TERM": "xterm"}
    prepared = prepare_shell("bash", env, scratch_root=tmp_path)
    child = pexpect.spawn(prepared.command, env=prepared.env, encoding="utf-8", timeout=5)
    shell = ShellIntegration()
    try:
        def pump(until: Callable[[], bool]) -> None:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not until():
                try:
                    shell.feed(child.read_nonblocking(4096, timeout=0.2))
                except pexpect.TIMEOUT:
                    pass

        pump(lambda: shell.state == "input")
        for line in ("echo marker-1", "false"):
            shell.typed(line + "\n", "agent")
            child.send(line + "\n")
            pump(lambda: bool(shell.blocks) and shell.running is None and shell.state == "input")
            time.sleep(0.05)
            pump(lambda: shell.state == "input" and shell.running is None)
        pump(lambda: len(shell.blocks) == 2 and shell.blocks[-1].exit_code is not None)
    finally:
        child.terminate(force=True)
    assert [(b.command, b.exit_code) for b in shell.blocks] == [("echo marker-1", 0), ("false", 1)]
    assert shell.blocks[0].output == "marker-1"
