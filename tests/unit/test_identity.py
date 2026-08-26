from __future__ import annotations

import subprocess

import pytest

from code_ai.core.identity import _plausible, detect_user_name


@pytest.mark.parametrize(
    "candidate",
    ["Rafael Aragao Morais", "Ada Lovelace", "Jean-Luc Picard", "José da Silva"],
)
def test_real_names_are_accepted(candidate) -> None:
    assert _plausible(candidate) == candidate


@pytest.mark.parametrize(
    "candidate",
    [
        "rapha",  # a login, not a name
        "dev01",
        "administrator",
        "your name",  # the git config placeholder
        "",
        "   ",
        "user2 name",  # digits mean an account
    ],
)
def test_account_names_and_placeholders_are_rejected(candidate) -> None:
    """Greeting someone by their login is worse than not greeting them by name."""

    assert _plausible(candidate) is None


def test_whitespace_is_normalised() -> None:
    assert _plausible("  Ada   Lovelace  ") == "Ada Lovelace"


def test_absurdly_long_values_are_rejected() -> None:
    assert _plausible("Ada " * 40) is None


def _without_ambient_git(tmp_path, monkeypatch) -> None:
    """Point git at empty config files so the developer's own name stays out."""

    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))


def test_git_config_is_preferred_over_the_environment(tmp_path, monkeypatch) -> None:
    _without_ambient_git(tmp_path, monkeypatch)
    monkeypatch.setenv("CODE_AI_USER_NAME", "Environment Person")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Git Person"], check=True
    )

    assert detect_user_name(tmp_path) == "Git Person"


def test_environment_is_used_when_git_has_no_name(tmp_path, monkeypatch) -> None:
    _without_ambient_git(tmp_path, monkeypatch)
    for variable in ("FULLNAME", "NAME", "USERNAME", "USER"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("CODE_AI_USER_NAME", "Environment Person")

    assert detect_user_name(tmp_path) == "Environment Person"


def test_nothing_is_returned_when_no_source_has_a_real_name(
    tmp_path, monkeypatch
) -> None:
    _without_ambient_git(tmp_path, monkeypatch)
    for variable in ("CODE_AI_USER_NAME", "FULLNAME", "NAME", "USER"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("USERNAME", "dev01")

    assert detect_user_name(tmp_path) is None


def test_a_global_git_identity_is_found_outside_a_repository(
    tmp_path, monkeypatch
) -> None:
    """git config reads the global file anywhere, so a bare folder still works."""

    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Global Person\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "missing"))

    assert detect_user_name(tmp_path) == "Global Person"
