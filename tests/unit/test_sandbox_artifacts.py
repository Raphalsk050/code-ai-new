from __future__ import annotations

import json

import pytest

from code_ai.sandbox.artifacts import ArtifactRecorder


def make_recorder(tmp_path, *, max_bytes: int = 1_000_000) -> ArtifactRecorder:
    root = tmp_path / "artifacts"
    root.mkdir()
    return ArtifactRecorder(root, max_bytes=max_bytes)


def test_a_run_is_persisted_as_two_streams_and_a_summary(tmp_path) -> None:
    recorder = make_recorder(tmp_path)

    record = recorder.record(
        label="pytest -q",
        stdout="2 passed",
        stderr="",
        metadata={"exit_code": 0, "argv": ["pytest", "-q"]},
    )

    assert record.stdout.read_text(encoding="utf-8") == "2 passed"
    assert record.stderr.read_text(encoding="utf-8") == ""
    summary = json.loads(record.result.read_text(encoding="utf-8"))
    assert summary["exit_code"] == 0
    assert summary["argv"] == ["pytest", "-q"]
    assert summary["artifacts"]["stdout"] == "stdout.log"
    assert record.truncated is False


def test_runs_are_numbered_in_order_and_labelled(tmp_path) -> None:
    recorder = make_recorder(tmp_path)

    first = recorder.record(label="npm run build", stdout="", stderr="", metadata={})
    second = recorder.record(label="npm test", stdout="", stderr="", metadata={})

    assert first.directory.name == "0001-npm-run-build"
    assert second.directory.name == "0002-npm-test"


def test_a_label_with_nothing_usable_still_produces_a_directory(tmp_path) -> None:
    recorder = make_recorder(tmp_path)

    record = recorder.record(label="///", stdout="", stderr="", metadata={})

    assert record.directory.name == "0001-run"


def test_a_new_recorder_does_not_overwrite_earlier_runs(tmp_path) -> None:
    first = make_recorder(tmp_path).record(label="build", stdout="one", stderr="", metadata={})
    rebuilt = ArtifactRecorder(first.directory.parent, max_bytes=1000)

    second = rebuilt.record(label="build", stdout="two", stderr="", metadata={})

    assert second.directory != first.directory
    assert first.stdout.read_text(encoding="utf-8") == "one"


def test_an_oversized_stream_is_capped_and_flagged(tmp_path) -> None:
    recorder = make_recorder(tmp_path, max_bytes=64)

    record = recorder.record(label="build", stdout="x" * 5000, stderr="", metadata={})

    written = record.stdout.read_text(encoding="utf-8")
    assert record.truncated is True
    assert "truncated" in written
    assert len(written.encode("utf-8")) < 5000
    summary = json.loads(record.result.read_text(encoding="utf-8"))
    assert summary["artifacts"]["stdout_truncated"] is True
    assert summary["artifacts"]["stderr_truncated"] is False


def test_a_capped_stream_stays_valid_utf8(tmp_path) -> None:
    recorder = make_recorder(tmp_path, max_bytes=9)

    # A cut in the middle of a multi-byte character must not corrupt the log.
    record = recorder.record(label="build", stdout="ação" * 20, stderr="", metadata={})

    assert record.stdout.read_text(encoding="utf-8").startswith("aç")


def test_paths_are_reported_relative_to_the_sandbox_root(tmp_path) -> None:
    recorder = make_recorder(tmp_path)

    record = recorder.record(label="build", stdout="", stderr="", metadata={})
    reported = record.to_dict(relative_to=tmp_path)

    assert reported["stdout"] == "artifacts/0001-build/stdout.log"
    assert reported["directory"] == "artifacts/0001-build"
    assert reported["truncated"] is False


def test_a_non_positive_cap_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError):
        ArtifactRecorder(tmp_path, max_bytes=0)
