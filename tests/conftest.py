from __future__ import annotations

import pytest

from code_ai import bootstrap
from code_ai.config.defaults import SANDBOX_DIR_ENV


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
