from __future__ import annotations

import pytest

from code_ai import bootstrap


@pytest.fixture(autouse=True)
def _isolate_failure_memories(tmp_path_factory, monkeypatch):
    """Keep the persistent failure-memory store out of the user's real config dir.

    Any test that builds an application without an explicit ``memories_dir``
    would otherwise read and write ``~/.code-ai/memories``. Redirect the default
    to a throwaway directory for every test.
    """

    directory = tmp_path_factory.mktemp("code_ai_memories")
    monkeypatch.setattr(bootstrap, "default_memories_dir", lambda: directory)
    yield
