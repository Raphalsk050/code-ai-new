from __future__ import annotations

import errno
import os

from code_ai.cli.file_probe import PROBE_FILENAME, probe_directory, run_file_probe
from code_ai.config.models import AppConfig
from code_ai.util.fileio import ERROR_SHARING_VIOLATION, RetryPolicy

FAST = RetryPolicy(attempts=3, initial_delay_s=0.0, max_delay_s=0.0)


def sharing_violation() -> OSError:
    exc = OSError(errno.EACCES, "The process cannot access the file")
    exc.winerror = ERROR_SHARING_VIOLATION
    return exc


def make_config(tmp_path, **file_io) -> AppConfig:
    settings = {"retry_attempts": 3, "retry_initial_delay_ms": 0, "retry_max_delay_ms": 0}
    settings.update(file_io)
    return AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "file_io": settings}
    )


def test_a_quiet_host_reports_nothing_wrong(tmp_path) -> None:
    report = probe_directory(tmp_path, policy=FAST, rounds=5, fallback=True)

    assert report.ok is True
    assert report.clean == 5
    assert report.retried == 0
    assert report.non_atomic == 0


def test_the_probe_cleans_up_after_itself(tmp_path) -> None:
    probe_directory(tmp_path, policy=FAST, rounds=3, fallback=True)

    assert list(tmp_path.iterdir()) == []


def test_interference_that_passes_is_counted_as_a_retry(tmp_path, monkeypatch) -> None:
    real = os.replace
    calls = {"n": 0}

    def guarded(source, destination, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            raise sharing_violation()
        return real(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", guarded)

    report = probe_directory(tmp_path, policy=FAST, rounds=4, fallback=True)

    assert report.ok is True
    assert report.retried == 4
    assert report.clean == 0


def test_a_lock_that_never_lets_go_is_reported_as_lost_atomicity(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(sharing_violation()))

    report = probe_directory(tmp_path, policy=FAST, rounds=3, fallback=True)

    assert report.ok is True
    assert report.non_atomic == 3


def test_a_host_where_writes_simply_fail_is_reported_with_its_cause(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(sharing_violation()))

    report = probe_directory(tmp_path, policy=FAST, rounds=2, fallback=False)

    assert report.ok is False
    assert report.failed == 2
    assert any("another process" in cause for cause in report.causes)


def test_the_command_exits_non_zero_when_writes_fail(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(sharing_violation()))
    config = make_config(tmp_path, allow_non_atomic_fallback=False)

    code = run_file_probe(config, rounds=2)
    output = capsys.readouterr().out

    assert code == 1
    assert "failed outright:   2" in output
    assert "file_io.retry_attempts" in output


def test_the_command_exits_zero_on_a_quiet_host(tmp_path, capsys) -> None:
    code = run_file_probe(make_config(tmp_path), rounds=3)
    output = capsys.readouterr().out

    assert code == 0
    assert "Nothing is interfering" in output
    assert PROBE_FILENAME not in [p.name for p in tmp_path.iterdir()]
