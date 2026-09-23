"""The editor's catalog fixture has to be what the backend actually serves.

`vscode-extension/e2e/fixtures/catalog.json` is what the webview tests boot the
panel with. If the backend grows a node kind, a pin, a profession or a tool and
the fixture stays behind, those tests keep passing against a catalog no user
will ever see - which is the one failure mode a green suite must not hide.

Regenerate it, keeping the run limits it already carries, with:

    python tests/unit/test_graph_catalog_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_ai.bootstrap import build_tool_registry
from code_ai.core.graph.models import catalog_payload
from code_ai.core.graph.professions import professions_payload
from code_ai.core.graph.toolbox import tools_payload

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "vscode-extension"
    / "e2e"
    / "fixtures"
    / "catalog.json"
)


def served() -> dict:
    """The catalog a fresh session serves, tools included."""

    return catalog_payload(professions_payload(), tools=tools_payload(build_tool_registry()))


def _fixture() -> dict:
    if not FIXTURE.exists():
        pytest.skip("the extension's e2e fixtures are not part of this checkout")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_matches_the_catalog_the_bridge_serves() -> None:
    expected = served()
    actual = _fixture()
    # The run limits come from the session config, not the catalog, so the
    # fixture carries a copy the backend never sends.
    actual.get("limits", {}).pop("run", None)

    assert actual == expected, (
        "vscode-extension/e2e/fixtures/catalog.json is stale; regenerate it "
        "(see this module's docstring)."
    )


def test_the_fixture_carries_the_run_limits_the_editor_expects() -> None:
    limits = _fixture()["limits"].get("run")

    assert limits, "the fixture lost the run limits the toolbar shows"
    assert set(limits) == {"max_nodes_executed", "max_seconds", "max_loop_iterations"}


if __name__ == "__main__":
    catalog = served()
    previous = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    run_limits = previous.get("limits", {}).get("run")
    if run_limits:
        catalog["limits"]["run"] = run_limits
    FIXTURE.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")
