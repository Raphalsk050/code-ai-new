from __future__ import annotations

import asyncio
import os
import shlex
import sys

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.process import ExecuteCommandTool
from code_ai.tools.process.execute_command import _strip_timeout_wrapper
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


def test_execute_command_schema_exposes_simple_command_string() -> None:
    schema = ExecuteCommandTool.input_schema

    assert schema["properties"]["command"]["type"] == "string"
    assert schema["properties"]["command"]["description"]
    # strict-mode: every declared property is required; optionals are nullable.
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["cwd"]["type"] == ["string", "null"]
    assert schema["properties"]["timeout"]["type"] == ["number", "null"]
    assert "argv" not in schema["properties"]
    assert "shell" not in schema["properties"]
    # env is exposed so the model can set variables without a shell prefix.
    assert schema["properties"]["env"]["type"] == ["object", "null"]
    assert schema["properties"]["env"]["additionalProperties"] == {"type": "string"}


async def test_execute_command_separates_stdout_stderr(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    script = "import sys; print('out'); print('err', file=sys.stderr)"
    result = await tool.execute(
        {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"},
        context,
    )
    assert result["exit_code"] == 0
    assert "out" in result["stdout"]
    assert "err" in result["stderr"]


# Prints the working directory in the platform's own notation. `pwd` cannot do
# that portably: on Windows the one on PATH is usually Git's, which answers in
# MSYS form ("/tmp/...") and never matches the native path under test.
_PRINT_CWD = "import os;print(os.getcwd())"


async def test_execute_command_defaults_to_workspace(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    result = await tool.execute(
        {"command": f'{shlex.quote(sys.executable)} -c "{_PRINT_CWD}"'}, context
    )
    assert result["cwd"] == str(tmp_path)
    assert result["stdout"].strip() == str(tmp_path)


@pytest.mark.parametrize("cwd_value", [None, "", ".", "None", "null", "  none  "])
async def test_execute_command_tolerates_sentinel_cwd(tmp_path, cwd_value) -> None:
    # Models often fill the nullable cwd with a stringified sentinel; it must map
    # to the workspace root instead of failing with "Path does not exist: None".
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    result = await tool.execute({"command": "pwd", "cwd": cwd_value}, context)
    assert result["cwd"] == str(tmp_path)


def test_relative_workdir_maps_sentinels_to_root(tmp_path) -> None:
    policy = WorkspacePolicy.from_path(tmp_path)
    for sentinel in (None, "", ".", "None", "NULL", " none "):
        assert policy.relative_workdir(sentinel) == tmp_path


async def test_execute_command_keeps_legacy_argv_execution(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()

    result = await tool.execute({"argv": [sys.executable, "-c", _PRINT_CWD]}, context)

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == str(tmp_path)


async def test_execute_command_sets_environment_variables(tmp_path) -> None:
    # The regression that stranded the agent: it needed an env var to run/test a
    # project but execute_command runs without a shell. The env argument provides
    # it directly instead of a (failing) VAR=value prefix.
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    script = "import os; print(os.environ.get('USE_FAKE_LLM', 'unset'))"

    result = await tool.execute(
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
            "env": {"USE_FAKE_LLM": "true"},
        },
        context,
    )

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "true"


async def test_execute_command_coerces_scalar_env_values(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    script = "import os; print(os.environ['PORT'], os.environ['DEBUG'])"

    result = await tool.execute(
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
            "env": {"PORT": 8080, "DEBUG": True},
        },
        context,
    )

    assert result["stdout"].strip() == "8080 true"


async def test_execute_command_rejects_malformed_env(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()

    with pytest.raises(ToolArgumentError, match="env"):
        await tool.execute({"command": "pwd", "env": ["USE_FAKE_LLM=true"]}, context)


async def test_execute_command_rejects_empty_command(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()

    with pytest.raises(ToolArgumentError, match="command"):
        await tool.execute({"command": ""}, context)


async def test_execute_command_rejects_shell_mode(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()

    with pytest.raises(ToolArgumentError, match="shell"):
        await tool.execute({"command": "pwd", "shell": True}, context)


async def test_execute_command_does_not_inherit_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    script = "import os; print(os.environ.get('OPENAI_API_KEY', 'missing'))"
    result = await tool.execute(
        {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"},
        context,
    )
    assert "secret-value" not in result["stdout"]
    assert "missing" in result["stdout"]


async def test_execute_command_timeout(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    with pytest.raises(ToolExecutionError):
        await tool.execute(
            {
                "command": (
                    f"{shlex.quote(sys.executable)} -c "
                    f"{shlex.quote('import time; time.sleep(2)')}"
                ),
                "timeout": 0.1,
            },
            context,
        )


async def test_execute_command_missing_binary_is_tool_error(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    with pytest.raises(ToolExecutionError, match="failed to start"):
        await tool.execute({"command": "definitely-missing-code-ai-binary"}, context)


@pytest.mark.parametrize(
    ("argv", "expected_inner", "expected_seconds"),
    [
        (["timeout", "30", "pytest"], ["pytest"], 30.0),
        (["gtimeout", "1.5m", "pytest", "-v"], ["pytest", "-v"], 90.0),
        (["timeout", "-k", "5", "-s", "KILL", "10", "ls"], ["ls"], 10.0),
        (["timeout", "--kill-after=5s", "--", "ls", "-l"], ["ls", "-l"], None),
        (["timeout", "ls"], ["ls"], None),  # token "ls" is not a duration
        (["pytest", "-v"], ["pytest", "-v"], None),  # not a wrapper, untouched
        (["timeout"], ["timeout"], None),  # bare wrapper, nothing to run
    ],
)
def test_strip_timeout_wrapper(argv, expected_inner, expected_seconds) -> None:
    inner, seconds = _strip_timeout_wrapper(argv)
    assert inner == expected_inner
    assert seconds == expected_seconds


async def test_execute_command_unwraps_timeout_prefix(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    # `timeout` may not exist on the host (e.g. macOS); the tool must run the
    # inner command anyway instead of failing to start the wrapper binary.
    result = await tool.execute(
        {"command": f"timeout 30 {shlex.quote(sys.executable)} -c {shlex.quote('print(1)')}"},
        context,
    )
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "1"
    assert result["argv"][0] != "timeout"


async def test_execute_command_wrapper_duration_bounds_execution(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    # The wrapper's duration is honored as the effective timeout when the caller
    # passes no explicit one, so a too-long inner command still times out.
    sleep = "import time; time.sleep(2)"
    with pytest.raises(ToolExecutionError, match="timed out"):
        await tool.execute(
            {"command": f"timeout 0.1 {shlex.quote(sys.executable)} -c {shlex.quote(sleep)}"},
            context,
        )


def test_command_split_keeps_windows_path_separators() -> None:
    # Regression: POSIX-mode shlex reads the backslash as an escape and eats it,
    # so a Windows path in a command silently lost its separators and the
    # command ran - exit code 0 - against a mangled path.
    from code_ai.tools.process.execute_command import _split_command_line

    argv = _split_command_line(r'del C:\ws\build\out.txt')

    if os.name == "nt":
        assert argv == ["del", r"C:\ws\build\out.txt"]
    else:
        # POSIX keeps its escapes; the backslash means what it means there.
        assert argv == ["del", "C:wsbuildout.txt"]


def test_command_split_still_honours_quoting() -> None:
    from code_ai.tools.process.execute_command import _split_command_line

    assert _split_command_line('echo "a b"') == ["echo", "a b"]
    assert _split_command_line("git commit -m 'x y'") == ["git", "commit", "-m", "x y"]
    assert _split_command_line("ls   -la") == ["ls", "-la"]


def test_command_split_rejects_an_unterminated_quote() -> None:
    from code_ai.tools.process.execute_command import _split_command_line

    with pytest.raises(ValueError):
        _split_command_line('echo "unterminated')
