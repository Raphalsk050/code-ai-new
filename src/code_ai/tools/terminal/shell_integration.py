"""Command blocks for the shared terminal, from shell integration marks.

Shells set up by :func:`prepare_shell` mark their prompt and each command with
the OSC 633 sequences VS Code's terminal uses:

- ``A`` prompt starts, ``B`` prompt ends (the command line is being typed),
- ``C`` the command started running, ``D;<exit>`` it finished,
- ``E;<command line>`` the command line as the shell saw it,
- ``P;Cwd=<path>`` the working directory.

:class:`ShellIntegration` takes the PTY stream, removes those marks before the
screen emulator sees them, and cuts the stream into :class:`CommandBlock` s:
who ran what, for how long, with which exit code and output. A shell that
never marks anything leaves no blocks, and the raw screen stays the view.

Pure apart from :func:`prepare_shell`, which writes the integration scripts.
"""

from __future__ import annotations

import os
import platform
import re
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

ESC = "\x1b"
BEL = "\x07"
_MARK_PREFIX = f"{ESC}]633;"
# A mark longer than this is garbage, not a command line still arriving.
_MAX_MARK = 16_384

# Everything an output block keeps besides text: SGR colour runs. Cursor moves,
# other OSC strings, charset switches and the like only make sense on a screen.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ESC_OTHER = re.compile(r"\x1b[@-Z\\-_]|\x1b[()#][0-9A-Za-z]")


@dataclass(slots=True)
class CommandBlock:
    block_id: int
    command: str
    author: str
    cwd: str
    started_at: float
    ended_at: float | None = None
    exit_code: int | None = None
    output: str = ""
    truncated: bool = False

    @property
    def running(self) -> bool:
        return self.ended_at is None

    def to_payload(self, max_output: int) -> dict[str, object]:
        output = self.output
        truncated = self.truncated
        if len(output) > max_output:
            output = output[-max_output:]
            truncated = True
        return {
            "id": self.block_id,
            "command": self.command,
            "author": self.author,
            "cwd": self.cwd,
            "started_at": round(self.started_at * 1000),
            "ended_at": None if self.ended_at is None else round(self.ended_at * 1000),
            "exit_code": self.exit_code,
            "running": self.running,
            "output": output,
            "truncated": truncated,
        }


@dataclass(slots=True)
class ShellIntegration:
    """Splits one session's output into command blocks."""

    clock: Callable[[], float] = time.time
    max_blocks: int = 50
    max_output_chars: int = 64_000
    blocks: list[CommandBlock] = field(default_factory=list)
    cwd: str = ""
    # "idle" until a mark is seen, then "prompt" (A), "input" (B), "running".
    state: str = "idle"
    # Bumped on every change a UI would draw differently.
    revision: int = 0
    _active: bool = False
    _carry: str = ""
    _typed: str = ""
    _typed_author: str = "user"
    _submitted: tuple[str, str] | None = None
    _pending_command: str | None = None
    _next_id: int = 1

    @property
    def active(self) -> bool:
        """The shell has marked at least once: blocks are meaningful."""
        return self._active

    @property
    def running(self) -> CommandBlock | None:
        last = self.blocks[-1] if self.blocks else None
        return last if last is not None and last.running else None

    # -- input, as the manager sends it ----------------------------------

    def typed(self, text: str, author: str) -> None:
        """Text sent to the shell. A newline in it submits the line."""
        self._typed_author = author
        for part in re.split(r"(\r\n|\r|\n)", text):
            if part in ("\r\n", "\r", "\n"):
                self.submitted(author)
            else:
                self._typed += part

    def submitted(self, author: str) -> None:
        """Enter was pressed. Opens a block right away when the shell is at its
        prompt, so a shell that never sends ``C`` (cmd.exe, bash 3) still gets
        one; a later ``C`` only moves where the output starts."""
        line = self._typed.strip()
        self._typed = ""
        self._submitted = (line, author)
        if self._active and self.state == "input" and line:
            self._open(line, author)
            self.state = "running"

    # -- output, as the PTY produces it ----------------------------------

    def feed(self, data: str) -> str:
        """Record the marks and output in ``data``; return it without the marks."""
        data = self._carry + data
        self._carry = ""
        out: list[str] = []
        pos = 0
        while True:
            start = data.find(_MARK_PREFIX, pos)
            if start < 0:
                tail = data[pos:]
                # A mark cut in half by the read size: hold its start back.
                for keep in range(min(len(_MARK_PREFIX) - 1, len(tail)), 0, -1):
                    if tail.endswith(_MARK_PREFIX[:keep]):
                        self._carry = tail[-keep:]
                        tail = tail[:-keep]
                        break
                out.append(tail)
                self._output(tail)
                break
            before = data[pos:start]
            out.append(before)
            self._output(before)
            end, terminator = _find_terminator(data, start + len(_MARK_PREFIX))
            if end < 0:
                if len(data) - start > _MAX_MARK:
                    # Never terminated: not a mark after all.
                    rest = data[start:]
                    out.append(rest)
                    self._output(rest)
                else:
                    self._carry = data[start:]
                break
            self._mark(data[start + len(_MARK_PREFIX) : end])
            pos = end + terminator
        return "".join(out)

    def closed(self) -> None:
        """The session ended; a command still running ends with it."""
        block = self.running
        if block is not None:
            block.ended_at = self.clock()
            self.revision += 1
        self.state = "idle"

    def payload(self, *, max_blocks: int = 20, max_output: int = 4000) -> dict[str, object]:
        running = self.running
        return {
            "integrated": self._active,
            "cwd": self.cwd,
            "running": running is not None,
            "running_author": running.author if running is not None else "",
            "blocks": [block.to_payload(max_output) for block in self.blocks[-max_blocks:]],
        }

    # -- internals -------------------------------------------------------

    def _mark(self, body: str) -> None:
        kind, _, rest = body.partition(";")
        self._active = True
        if kind == "A":
            self.state = "prompt"
        elif kind == "B":
            self.state = "input"
        elif kind == "E":
            self._pending_command = _unescape(rest.split(";", 1)[0])
        elif kind == "C":
            block = self.running
            command = self._pending_command
            self._pending_command = None
            if block is None:
                line, author = self._submitted or ("", "user")
                block = self._open(command or line, author)
            else:
                # Opened at Enter: what came between was the echo of the line.
                block.output = ""
                if command:
                    block.command = command
            self._submitted = None
            self.state = "running"
        elif kind == "D":
            block = self.running
            if block is not None:
                block.ended_at = self.clock()
                code = rest.strip()
                block.exit_code = int(code) if re.fullmatch(r"-?\d+", code) else None
                block.output = block.output.strip("\r\n")
            self._submitted = None
            self.state = "prompt"
        elif kind == "P":
            key, _, value = rest.partition("=")
            if key == "Cwd":
                self.cwd = _unescape(value)
        self.revision += 1

    def _open(self, command: str, author: str) -> CommandBlock:
        block = CommandBlock(
            block_id=self._next_id,
            command=command,
            author=author,
            cwd=self.cwd,
            started_at=self.clock(),
        )
        self._next_id += 1
        self.blocks.append(block)
        if len(self.blocks) > self.max_blocks:
            del self.blocks[: len(self.blocks) - self.max_blocks]
        self.revision += 1
        return block

    def _output(self, text: str) -> None:
        block = self.running
        if block is None or not text or self.state != "running":
            return
        block.output = _append(block.output, _clean(text))
        if len(block.output) > self.max_output_chars:
            block.output = block.output[-self.max_output_chars :]
            block.truncated = True
        self.revision += 1


def _find_terminator(data: str, start: int) -> tuple[int, int]:
    """Where the string started at ``start`` ends, and the terminator's length."""
    bel = data.find(BEL, start)
    st = data.find(f"{ESC}\\", start)
    candidates = [(i, n) for i, n in ((bel, 1), (st, 2)) if i >= 0]
    return min(candidates) if candidates else (-1, 0)


def _unescape(value: str) -> str:
    """Undo the escaping the scripts apply: ``\\x3b`` for ``;``, ``\\\\`` for ``\\``."""
    def one(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16)) if match.group(1) else "\\"

    return re.sub(r"\\x([0-9a-fA-F]{2})|\\\\", one, value)


def _clean(text: str) -> str:
    """Output as a log keeps it: colours, no cursor control, Unix newlines."""
    text = _OSC.sub("", text)
    text = _CSI.sub(lambda m: m.group(0) if m.group(0).endswith("m") else "", text)
    text = _ESC_OTHER.sub("", text)
    return text.replace("\r\n", "\n")


def _append(existing: str, text: str) -> str:
    """Append, letting a lone carriage return overwrite the line it is on."""
    if "\r" not in text:
        return existing + text
    out = existing
    for piece in re.split(r"(\r)", text):
        if piece == "\r":
            out = out[: out.rfind("\n") + 1]
        else:
            out += piece
    return out


# -- injecting the marks into a shell ---------------------------------------

_BASH_RC = r"""# Code-AI shell integration: the user's own rc, then the command marks.
if [ -f ~/.bashrc ]; then . ~/.bashrc; fi
__codeai_cwd() { local p="$PWD"; p="${p//\\/\\\\}"; printf '%s' "${p//;/\\x3b}"; }
__codeai_precmd() {
  local code=$?
  printf '\033]633;D;%s\007\033]633;P;Cwd=%s\007' "$code" "$(__codeai_cwd)"
  return $code
}
if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
  PROMPT_COMMAND=(__codeai_precmd "${PROMPT_COMMAND[@]}")
else
  PROMPT_COMMAND="__codeai_precmd${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
fi
PS1='\[\033]633;A\007\]'"$PS1"'\[\033]633;B\007\]'
PS0=$'\033]633;C\007'"$PS0"
"""

_ZSH_ENV = r"""# Code-AI shell integration: the user's zshenv, from where it really lives.
[ -f "${CODEAI_ZDOTDIR:-$HOME}/.zshenv" ] && . "${CODEAI_ZDOTDIR:-$HOME}/.zshenv"
"""

_ZSH_RC = r"""# Code-AI shell integration: the user's own rc, then the command marks.
__codeai_dir="$ZDOTDIR"
ZDOTDIR="${CODEAI_ZDOTDIR:-$HOME}"
[ -f "$ZDOTDIR/.zshrc" ] && . "$ZDOTDIR/.zshrc"
__codeai_esc() { local s="${1//\\/\\\\}"; print -rn -- "${s//;/\\x3b}"; }
__codeai_precmd() {
  local code=$?
  print -n "\e]633;D;$code\a\e]633;P;Cwd=$(__codeai_esc "$PWD")\a\e]633;A\a"
  [[ "$PS1" == *$'\e]633;B'* ]] || PS1="$PS1%{"$'\e]633;B\a'"%}"
}
__codeai_preexec() { print -n "\e]633;E;$(__codeai_esc "$1")\a\e]633;C\a"; }
autoload -Uz add-zsh-hook
add-zsh-hook precmd __codeai_precmd
add-zsh-hook preexec __codeai_preexec
"""

_PWSH = r"""# Code-AI shell integration: wraps the prompt the profile set up.
$Global:__CodeAiPrompt = $function:prompt
function Global:prompt {
  $ok = $?
  $code = if ($ok) { 0 } elseif ($Global:LASTEXITCODE) { $Global:LASTEXITCODE } else { 1 }
  $e = [char]27; $b = [char]7
  $cwd = $PWD.Path.Replace('\', '\\').Replace(';', '\x3b')
  "$e]633;D;$code$b$e]633;P;Cwd=$cwd$b$e]633;A$b" + (& $Global:__CodeAiPrompt) + "$e]633;B$b"
}
try {
  Set-PSReadLineKeyHandler -Chord Enter -ScriptBlock {
    $line = $null; $cursor = $null
    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
    $e = [char]27; $b = [char]7
    [Console]::Write("$e]633;E;$($line.Replace('\', '\\').Replace(';', '\x3b'))$b$e]633;C$b")
    [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
  }
} catch {}
"""

_CMD_PROMPT_HEAD = "$e]633;D$e\\$e]633;P;Cwd=$P$e\\$e]633;A$e\\"
_CMD_PROMPT_TAIL = "$e]633;B$e\\"


@dataclass(slots=True)
class PreparedShell:
    command: str
    env: dict[str, str]
    #: Scratch folder holding the scripts; removed with the session.
    scratch: Path | None
    #: The shell's short name ("bash", "pwsh", ...), "" when not integrated.
    shell: str


def _split_program(command: str) -> tuple[str, str]:
    """The program and the rest; a quoted program may hold spaces."""
    text = command.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end], text[end + 1 :].strip()
    program, _, rest = text.partition(" ")
    return program, rest.strip()


def shell_name(command: str) -> str:
    """The program's short name: ``C:\\...\\pwsh.exe -NoLogo`` -> ``pwsh``."""
    program, _ = _split_program(command)
    name = re.split(r"[\\/]", program)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def prepare_shell(
    command: str,
    env: Mapping[str, str] | None,
    *,
    scratch_root: Path | None = None,
) -> PreparedShell:
    """The command and environment that start ``command`` with the marks on.

    Only a bare shell is touched: ``bash``, ``zsh``, ``pwsh``, ``powershell``
    and ``cmd``, with no arguments of their own. Anything else (a dev server,
    a REPL, a shell with flags) starts exactly as asked, without blocks.
    """

    base = dict(env) if env is not None else dict(os.environ)
    name = shell_name(command)
    program_path, args = _split_program(command)
    if args or name not in {"bash", "zsh", "pwsh", "powershell", "cmd"}:
        return PreparedShell(command, base, None, "")
    if name == "cmd":
        user_prompt = base.get("PROMPT") or "$P$G"
        base["PROMPT"] = _CMD_PROMPT_HEAD + user_prompt + _CMD_PROMPT_TAIL
        return PreparedShell(command, base, None, name)
    scratch = Path(tempfile.mkdtemp(prefix="code-ai-shell-", dir=scratch_root))
    program = _quote(program_path)
    if name == "bash":
        rc = scratch / "bashrc"
        rc.write_text(_BASH_RC, encoding="utf-8", newline="\n")
        return PreparedShell(f"{program} --init-file {_quote(str(rc))} -i", base, scratch, name)
    if name == "zsh":
        (scratch / ".zshenv").write_text(_ZSH_ENV, encoding="utf-8", newline="\n")
        (scratch / ".zshrc").write_text(_ZSH_RC, encoding="utf-8", newline="\n")
        base["CODEAI_ZDOTDIR"] = base.get("ZDOTDIR", base.get("HOME", ""))
        base["ZDOTDIR"] = str(scratch)
        return PreparedShell(f"{program} -i", base, scratch, name)
    script = scratch / "integration.ps1"
    windows = platform.system() == "Windows"
    script.write_text(_PWSH, encoding="utf-8-sig", newline="\r\n" if windows else "\n")
    policy = " -ExecutionPolicy Bypass" if name == "powershell" or windows else ""
    return PreparedShell(
        f"{program} -NoLogo -NoExit{policy} -Command \". '{script}'\"", base, scratch, name
    )


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value and not value.startswith('"') else value
