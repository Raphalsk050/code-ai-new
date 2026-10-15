from __future__ import annotations

import pytest

from code_ai import bootstrap, updates
from code_ai.config import models as config_models
from code_ai.config.defaults import SANDBOX_DIR_ENV
from code_ai.core import orchestration
from code_ai.interop.paths import HOME_ENV as INTEROP_HOME_ENV


@pytest.fixture(autouse=True)
def _isolate_failure_memories(tmp_path_factory, monkeypatch):
    """Keep every persistent memory store out of the user's real config dir.

    Any test that builds an application without explicit dirs would otherwise
    read and write ``~/.code-ai/memories`` (failure lessons), plus the global
    knowledge and per-project memory stores. Redirect them all to throwaway
    directories for every test.
    """

    directory = tmp_path_factory.mktemp("code_ai_memories")
    knowledge = tmp_path_factory.mktemp("code_ai_knowledge")
    projects = tmp_path_factory.mktemp("code_ai_projects")
    monkeypatch.setattr(bootstrap, "default_memories_dir", lambda: directory)
    monkeypatch.setattr(bootstrap, "global_knowledge_dir", lambda: knowledge)
    monkeypatch.setattr(bootstrap, "project_memories_dir", lambda _workspace: projects)
    # The code index is one more per-project store under the config dir; a
    # test that builds an application must not create or reuse a real one.
    index_dir = tmp_path_factory.mktemp("code_ai_index")
    monkeypatch.setattr(config_models, "project_index_dir", lambda _workspace: index_dir)
    yield


@pytest.fixture(autouse=True)
def _isolate_other_agents(tmp_path_factory, monkeypatch):
    """Keep the developer's own Claude Code, Codex, OpenCode and Cline setup out.

    Every foreign location resolves under this empty home, machine-wide ones are
    switched off, and the agents' own path variables are cleared.
    """

    monkeypatch.setenv(INTEROP_HOME_ENV, str(tmp_path_factory.mktemp("other_agents_home")))
    for name in ("CODEX_HOME", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "CODE_AI_CLINE_HOME"):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_release_lookups(monkeypatch, tmp_path):
    """No test asks GitHub for releases or profiles; the ones about updates stub
    their own.

    Nor does any read the developer's own logins: no gh, no git credentials -
    nor open a browser for a picture, nor keep one in the real home.
    """

    async def nothing(**_kwargs):
        return None

    monkeypatch.setattr(updates, "newest_release", nothing)
    monkeypatch.setattr(updates, "profile", nothing)
    monkeypatch.setattr(updates, "sign_in_for_picture", nothing)
    pictures = tmp_path / "code-ai-pictures"
    monkeypatch.setattr(updates, "picture_profile_dir", lambda: pictures / "browser" / "github")
    monkeypatch.setattr(updates, "_kept_path", lambda: pictures / "avatars" / "github.json")
    monkeypatch.setattr(updates, "_run_tool", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(updates, "_refused", set())
    for name in (
        updates.RELEASES_TOKEN_ENV,
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def _retry_without_waiting(monkeypatch):
    """Failed model steps retry at once: the real backoff runs to minutes."""

    monkeypatch.setattr(orchestration, "_retry_delay", lambda attempt, exc=None: 0.0)
    yield


@pytest.fixture(autouse=True)
def _isolate_sandboxes(tmp_path_factory, monkeypatch):
    """Keep test sandboxes out of the shared system temp directory.

    Any test that builds an application creates a session sandbox, and the
    startup sweep walks the base directory looking for expired ones. Pointing
    the base at a throwaway directory keeps a test run from meeting - or
    reaping - a sandbox belonging to a real session on the same machine.
    """

    monkeypatch.setenv(SANDBOX_DIR_ENV, str(tmp_path_factory.mktemp("code_ai_sandboxes")))
    yield
