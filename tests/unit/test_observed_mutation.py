"""The mutation discipline keys off observed evidence, not the surface label.

Three independent holes let the deterministic layer go dark on real sessions:
the keyword classifier dropping continuation and low-level turns to
``CONVERSATION``, ``execute_command`` mutating the workspace invisibly, and the
only hard gate hanging off a ``complete_task`` call weak models never make.
These tests pin the fix: what the agent *did* drives the discipline, and the
checkpoint sits at the loop's natural stop.
"""

from __future__ import annotations

from code_ai.config.models import PlannerConfig
from code_ai.core.planning import PlannerService
from code_ai.core.planning.evidence import command_mutates_workspace
from code_ai.core.verification import (
    CommandKind,
    ProjectVerification,
    VerificationCommand,
)
from code_ai.events.bus import AsyncEventBus


def _project_with_tests() -> ProjectVerification:
    return ProjectVerification(
        commands=(
            VerificationCommand(
                kind=CommandKind.TEST,
                argv=("pytest", "-q"),
                description="run tests",
                source="pyproject.toml",
            ),
        ),
        ecosystems=("python",),
    )


def _planner(tmp_path, *, verifiable: bool = True) -> PlannerService:
    return PlannerService(
        config=PlannerConfig(double_check_completion=False),
        event_bus=AsyncEventBus(session_id="session"),
        session_id="session",
        workspace=tmp_path,
        verification_detector=lambda _ws: (
            _project_with_tests() if verifiable else ProjectVerification()
        ),
    )


async def _run_command(service: PlannerService, argv: list[str], *, exit_code: int = 0):
    return await service.record_tool_result(
        tool_call_id=f"c-{len(service.ledger.records)}",
        tool_name="execute_command",
        payload={"argv": argv, "exit_code": exit_code, "stdout": "", "stderr": ""},
        success=True,
    )


async def _write_file(service: PlannerService, path: str) -> None:
    await service.record_tool_result(
        tool_call_id=f"w-{len(service.ledger.records)}",
        tool_name="write_file",
        payload={"path": path, "old_sha256": None, "new_sha256": "abc"},
        success=True,
    )


# --------------------------------------------------------------------------- #
# Shell command shapes that write to the filesystem
# --------------------------------------------------------------------------- #


def test_mutating_shell_shapes_are_detected() -> None:
    for argv in [
        ["bash", "-lc", "echo 'x = 1' > src/app.py"],
        ["bash", "-lc", "cat header.txt >> README.md"],
        ["sh", "-c", "cp config.example.toml config.toml"],
        ["sh", "-c", "mv old.py new.py"],
        ["sh", "-c", "rm -rf build"],
        ["sh", "-c", "sed -i 's/a/b/' main.py"],
        ["sh", "-c", "mkdir -p src/pkg"],
        ["pwsh", "-c", "Set-Content -Path app.py -Value 'x = 1'"],
        ["pwsh", "-c", "Copy-Item a.py b.py"],
        ["python", "-c", "open('out.txt', 'w').write('hi')"],
        ["python", "-c", "from pathlib import Path; Path('a.py').write_text('x')"],
        ["python", "-c", "import shutil; shutil.copy('a', 'b')"],
    ]:
        assert command_mutates_workspace(argv) is True, argv


def test_read_only_shell_shapes_are_not_mutations() -> None:
    for argv in [
        ["ls", "-la"],
        ["cat", "README.md"],
        ["sh", "-c", "grep -rn 'def ' src | head -20"],
        ["sh", "-c", "git status"],
        ["sh", "-c", "git diff --stat"],
        ["sh", "-c", "python -c \"print(open('a.py').read())\""],
        # Stream plumbing writes nothing to disk.
        ["sh", "-c", "make check 2>&1"],
        ["sh", "-c", "noisy_tool > /dev/null"],
        ["pwsh", "-c", "Get-Content app.py"],
        # A word merely *containing* a mutating command must not trip it.
        ["sh", "-c", "./scripts/rmdir_helper_test.sh"],
        ["sh", "-c", "echo done"],
    ]:
        assert command_mutates_workspace(argv) is False, argv


def test_mutation_detection_accepts_a_plain_string_command() -> None:
    assert command_mutates_workspace("echo hi > out.txt") is True
    assert command_mutates_workspace("ls") is False
    assert command_mutates_workspace(None) is False


# --------------------------------------------------------------------------- #
# A shell mutation is a real change: the gate must demand verification for it
# --------------------------------------------------------------------------- #


async def test_shell_mutation_requires_verification_before_completion(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("rode o gerador de stubs", provider_supports_tools=True)

    await _run_command(service, ["bash", "-lc", "echo 'x = 1' > src/app.py"])

    assert service.ledger.command_mutated_workspace is True
    decision = await service.evaluate_completion({"summary": "stub gerado"})
    assert decision.accepted is False
    assert any("verification" in item for item in decision.missing_requirements)


async def test_shell_mutation_completes_once_verified(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("rode o gerador de stubs", provider_supports_tools=True)

    await _run_command(service, ["bash", "-lc", "echo 'x = 1' > src/app.py"])
    await _run_command(service, ["pytest", "-q"])

    decision = await service.evaluate_completion({"summary": "stub gerado e testado"})
    assert decision.accepted is True


async def test_read_only_commands_never_demand_verification(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("liste os arquivos do projeto", provider_supports_tools=True)

    await _run_command(service, ["ls", "-la"])
    await _run_command(service, ["cat", "README.md"])

    assert service.ledger.command_mutated_workspace is False
    has_change, verified, _ = service._change_verification_state()
    assert has_change is False
    assert verified is True


async def test_a_shell_mutation_after_a_passing_check_invalidates_it(tmp_path) -> None:
    # The check described the workspace as it no longer is.
    service = _planner(tmp_path)
    await service.begin_turn("rode o gerador de stubs", provider_supports_tools=True)

    await _run_command(service, ["pytest", "-q"])
    assert service.ledger.latest_verification_passed is True

    await _run_command(service, ["bash", "-lc", "sed -i 's/a/b/' src/app.py"])

    assert service.ledger.latest_verification_passed is False


async def test_verification_runs_do_not_count_as_mutations(tmp_path) -> None:
    # Test and build runners write caches and artefacts; treating that as a
    # workspace change would invalidate the very evidence they just produced.
    service = _planner(tmp_path)
    await service.begin_turn("rode a suite de testes", provider_supports_tools=True)

    await _run_command(service, ["pytest", "-q"])

    assert service.ledger.command_mutated_workspace is False
    assert service.ledger.latest_verification_passed is True


async def test_claimed_paths_are_not_called_phantom_after_a_shell_mutation(
    tmp_path,
) -> None:
    # A command-driven change is real but path-less; nothing in the ledger can
    # confirm or refute the claim, so it must not read as fabrication.
    service = _planner(tmp_path)
    await service.begin_turn("rode o gerador de stubs", provider_supports_tools=True)

    await _run_command(service, ["bash", "-lc", "echo 'x = 1' > src/app.py"])
    await _run_command(service, ["pytest", "-q"])

    decision = await service.evaluate_completion(
        {"summary": "stub gerado", "changed_paths": ["src/app.py"]}
    )
    assert decision.accepted is True


async def test_unverifiable_project_still_completes_after_a_shell_mutation(
    tmp_path,
) -> None:
    # No detectable test/build system: degrade gracefully rather than demanding
    # evidence the agent cannot produce.
    service = _planner(tmp_path, verifiable=False)
    await service.begin_turn("rode o gerador de stubs", provider_supports_tools=True)

    await _run_command(service, ["bash", "-lc", "echo 'x = 1' > src/app.py"])

    decision = await service.evaluate_completion({"summary": "stub gerado"})
    assert decision.accepted is True


# --------------------------------------------------------------------------- #
# A mislabelled task that mutates gets the mutation discipline anyway
# --------------------------------------------------------------------------- #


async def test_conversation_task_that_writes_gains_the_task_context_block(
    tmp_path,
) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("faça um jogo pong em python", provider_supports_tools=True)
    assert service.profile.intent == "conversation"
    assert service.task_context_block(recommended_tool_names={"write_file"}) == ""

    await _write_file(service, "pong.py")

    block = service.task_context_block(recommended_tool_names={"write_file"})
    assert "Runtime task state" in block
    assert "READ-ONLY TASK" not in block


async def test_conversation_task_that_writes_must_verify_before_completing(
    tmp_path,
) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("faça um jogo pong em python", provider_supports_tools=True)

    await _write_file(service, "pong.py")

    assert service._effective_profile().requires_workspace_mutation is True
    decision = await service.evaluate_completion({"summary": "pong pronto"})
    assert decision.accepted is False
    assert any("verification" in item for item in decision.missing_requirements)


async def test_a_conversation_that_stays_a_conversation_is_left_alone(
    tmp_path,
) -> None:
    # The escalation must cost nothing until a change is actually observed:
    # re-injecting the state block into chat is the regression to avoid.
    service = _planner(tmp_path)
    await service.begin_turn("olá, tudo bem?", provider_supports_tools=True)

    assert service.task_context_block(recommended_tool_names=set()) == ""
    assert service._effective_profile().requires_workspace_mutation is False
    has_change, verified, _ = service._change_verification_state()
    assert (has_change, verified) == (False, True)


async def test_read_only_task_keeps_its_read_only_rules(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("leia o arquivo config.toml", provider_supports_tools=True)

    await service.record_tool_result(
        tool_call_id="r1",
        tool_name="read_file",
        payload={"path": "config.toml", "sha256": "abc"},
        success=True,
    )

    block = service.task_context_block(recommended_tool_names={"read_file"})
    assert "READ-ONLY TASK" in block
    assert service._effective_profile().requires_workspace_mutation is False


# --------------------------------------------------------------------------- #
# The checkpoint at the loop's natural stop
# --------------------------------------------------------------------------- #


async def test_prose_ending_after_an_unverified_change_is_nudged_once(
    tmp_path,
) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("implemente o parser", provider_supports_tools=True)
    await _write_file(service, "src/parser.py")

    first = await service.note_final_answer_verification_debt()
    second = await service.note_final_answer_verification_debt()

    assert first is not None
    assert "verification" in first
    assert second is None  # bounded to one nudge, then fail open


async def test_prose_ending_after_a_verified_change_is_not_nudged(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("implemente o parser", provider_supports_tools=True)
    await _write_file(service, "src/parser.py")
    await _run_command(service, ["pytest", "-q"])

    assert await service.note_final_answer_verification_debt() is None


async def test_prose_ending_on_a_read_only_task_is_never_nudged(tmp_path) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("o que faz o modulo de parsing?", provider_supports_tools=True)
    await service.record_tool_result(
        tool_call_id="r1",
        tool_name="read_file",
        payload={"path": "src/parser.py", "sha256": "abc"},
        success=True,
    )

    assert await service.note_final_answer_verification_debt() is None


async def test_prose_ending_after_an_unverified_shell_mutation_is_nudged(
    tmp_path,
) -> None:
    service = _planner(tmp_path)
    await service.begin_turn("rode o gerador de stubs", provider_supports_tools=True)
    await _run_command(service, ["bash", "-lc", "echo 'x = 1' > src/app.py"])

    nudge = await service.note_final_answer_verification_debt()

    assert nudge is not None
    assert "pytest" in nudge


async def test_a_denied_mutation_is_never_nudged_for_verification(tmp_path) -> None:
    # The user's refusal is a decision, not a misreading: do not push back.
    service = _planner(tmp_path)
    await service.begin_turn("implemente o parser", provider_supports_tools=True)
    await _write_file(service, "src/parser.py")
    await service.note_user_denial("write_file", "user declined")

    assert await service.note_final_answer_verification_debt() is None
