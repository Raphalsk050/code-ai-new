from __future__ import annotations

import pytest

from code_ai import bootstrap


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
