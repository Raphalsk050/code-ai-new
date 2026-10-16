"""The agent's graph tools: a spec becomes a graph the editor opens and runs.

`vscode-extension/e2e/fixtures/agent-written.flow.json` is what write_graph makes
of :func:`review_loop`; the editor's end-to-end specs open it. Regenerate it with:

    python tests/unit/test_graph_authoring.py
"""

from __future__ import annotations

import asyncio
import json
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from code_ai.bootstrap import build_tool_registry
from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.core.graph import authoring
from code_ai.core.graph.compile import compile_graph
from code_ai.core.graph.professions import graph_profile_registry
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.groups import group_of
from code_ai.util.paths import WorkspacePolicy

PROFESSIONS = tuple(graph_profile_registry().names())


def review_loop() -> dict[str, Any]:
    return {
        "name": "review loop",
        "variables": [{"name": "rounds", "type": "int", "default": 3}],
        "nodes": [
            {"id": "start", "kind": "start"},
            {
                "id": "llm",
                "kind": "model",
                "data": {"model": "qwen3", "base_url": "http://localhost:11434/v1"},
            },
            {
                "id": "plan",
                "kind": "profession",
                "data": {"profession": "architect", "task": "Plan the importer"},
                "note": "plans before code",
            },
            {"id": "split", "kind": "sequence"},
            {"id": "write", "kind": "profession", "data": {"profession": "code_writer"}},
            {
                "id": "review",
                "kind": "profession",
                "data": {"profession": "architect", "task": "Review it"},
            },
            {"id": "log", "kind": "print", "data": {"label": "report"}},
            {"id": "end", "kind": "end"},
        ],
        "wires": [
            "start -> plan -> split",
            "split.out_0 -> write",
            "split.out_1 -> review",
            "split.out_2 -> log -> end",
            "llm.model -> plan.model",
            "llm.model -> write.model",
            "plan.report -> write.task",
            "write.report -> log.value",
            "write.report -> end.result",
        ],
        "comments": [{"title": "Design", "nodes": ["plan", "llm"], "color": "#2563eb"}],
    }


def compiles(doc: dict[str, Any]) -> list[str]:
    program, issues = compile_graph(doc, professions=PROFESSIONS)
    errors = [item.message for item in issues if item.severity == "error"]
    assert program is not None, errors
    return errors


def node(doc: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(item for item in doc["nodes"] if item["id"] == node_id)


def boxes(doc: dict[str, Any]) -> dict[str, tuple[int, int, int, int]]:
    return {item["id"]: authoring._box(item) for item in doc["nodes"] if item["kind"] != "comment"}


# -- building -----------------------------------------------------------------


def test_a_spec_becomes_a_graph_the_compiler_accepts() -> None:
    doc = authoring.build(review_loop())

    assert compiles(doc) == []
    kinds = {(e["from"][0], e["to"][0]): e["kind"] for e in doc["edges"]}
    assert kinds[("start", "plan")] == "exec"
    assert kinds[("llm", "plan")] == "data"
    assert node(doc, "split")["data"]["outputs"] == 3
    assert node(doc, "plan")["data"]["note_shown"] is True
    assert doc["variables"][0] == {"id": "v1", "name": "rounds", "type": "int", "default": 3}


def test_the_layout_runs_left_to_right_and_overlaps_nothing() -> None:
    doc = authoring.build(review_loop())
    placed = boxes(doc)

    for a, b in combinations(placed, 2):
        assert not authoring._overlaps(placed[a], placed[b], margin=0), (a, b)
    for edge in doc["edges"]:
        if edge["kind"] == "exec":
            assert placed[edge["to"][0]][0] > placed[edge["from"][0]][0], edge
    # A model node sits under the agent it feeds.
    assert node(doc, "llm")["pos"][0] == node(doc, "plan")["pos"][0]
    assert node(doc, "llm")["pos"][1] > node(doc, "plan")["pos"][1]
    assert all(value % 10 == 0 for item in doc["nodes"] for value in item["pos"])


def test_a_comment_is_drawn_around_the_nodes_it_names() -> None:
    doc = authoring.build(review_loop())
    comment = doc["nodes"][0]

    assert comment["kind"] == "comment"
    x, y = comment["pos"]
    right, bottom = x + comment["data"]["width"], y + comment["data"]["height"]
    for held in ("plan", "llm"):
        left, top, far, low = authoring._box(node(doc, held))
        assert x < left and y < top and far < right and low < bottom


def test_positions_given_in_the_spec_are_kept() -> None:
    spec = review_loop()
    spec["nodes"][0]["pos"] = [-400, 300]
    doc = authoring.build(spec)
    assert node(doc, "start")["pos"] == [-400, 300]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda s: s["nodes"].append({"id": "x", "kind": "teleporter"}), "Unknown node kind"),
        (lambda s: s["wires"].append("plan.summary -> end.result"), "has no output 'summary'"),
        (lambda s: s["wires"].append("ghost -> end"), "no node 'ghost'"),
        (lambda s: s["wires"].append("start -> review"), "fan out with a sequence"),
        (lambda s: s["wires"].append("plan.report -> end.result"), "takes one value"),
        (lambda s: s["nodes"].append({"id": "plan", "kind": "print"}), "Two nodes"),
        (lambda s: s["comments"].append({"title": "x", "nodes": ["nope"]}), "no node nope"),
    ],
)
def test_what_cannot_become_a_graph_says_why(change, expected) -> None:
    spec = review_loop()
    change(spec)
    with pytest.raises(authoring.GraphSpecError) as caught:
        authoring.build(spec)
    assert expected in str(caught.value)


def test_an_unknown_pin_names_the_ones_the_node_has() -> None:
    spec = review_loop()
    spec["wires"].append("plan.out -> end.answer")
    with pytest.raises(authoring.GraphSpecError) as caught:
        authoring.build(spec)
    assert "it has in, result" in str(caught.value)


def test_wire_text_reads_chains_and_bare_ends() -> None:
    wires = authoring.parse_wires("a -> b.task")
    assert [w.text() for w in wires] == ["a.out -> b.task"]
    assert [w.text() for w in authoring.parse_wires("a -> b -> c")] == [
        "a.out -> b.in",
        "b.out -> c.in",
    ]
    assert authoring.parse_wires({"from": "a.report", "to": "b.value"})[0].text() == (
        "a.report -> b.value"
    )
    with pytest.raises(authoring.GraphSpecError):
        authoring.parse_wires("a -> b.task -> c")


def test_the_file_is_written_the_way_the_editor_writes_it() -> None:
    text = authoring.serialize(authoring.build(review_loop()))
    doc = json.loads(text)

    assert list(doc) == ["version", "name", "variables", "nodes", "edges", "view"]
    assert list(doc["nodes"][1]) == ["id", "kind", "pos", "data"]
    for item in doc["nodes"]:
        assert list(item["data"]) == sorted(item["data"])
    assert text.endswith("}\n")
    assert authoring.serialize(authoring.parse_document(text)) == text


def test_a_graph_reads_back_as_the_spec_that_rebuilds_it() -> None:
    doc = authoring.build(review_loop())
    spec = authoring.to_spec(doc)

    assert set(spec["comments"][0]["nodes"]) == {"llm", "plan"}
    assert "start.out -> plan.in" in spec["wires"]
    rebuilt = authoring.build(spec)
    assert compiles(rebuilt) == []
    assert {e["id"] for e in rebuilt["edges"]} == {e["id"] for e in doc["edges"]}


# -- editing ------------------------------------------------------------------


def test_an_added_node_lands_next_to_what_it_follows_and_nothing_else_moves() -> None:
    doc = authoring.build(review_loop())
    before = {item["id"]: list(item["pos"]) for item in doc["nodes"]}

    authoring.apply_edits(
        doc,
        [
            {"op": "add_node", "id": "check", "kind": "print", "data": {"label": "check"}},
            {"op": "connect", "wire": "review -> check"},
        ],
    )

    assert {i["id"]: i["pos"] for i in doc["nodes"] if i["id"] in before} == before
    review, check = node(doc, "review"), node(doc, "check")
    assert check["pos"][0] > review["pos"][0]
    placed = boxes(doc)
    assert not any(
        authoring._overlaps(placed["check"], placed[other], margin=0)
        for other in placed
        if other != "check"
    )
    assert compiles(doc) == []


def test_connect_replaces_and_disconnect_removes() -> None:
    doc = authoring.build(review_loop())
    authoring.apply_edits(doc, [{"op": "connect", "wire": "review.report -> end.result"}])
    feeding = [e for e in doc["edges"] if e["to"] == ["end", "result"]]
    assert [e["from"] for e in feeding] == [["review", "report"]]

    authoring.apply_edits(doc, [{"op": "disconnect", "wire": "review.report -> end.result"}])
    assert not any(e["to"] == ["end", "result"] for e in doc["edges"])
    with pytest.raises(authoring.GraphSpecError):
        authoring.apply_edits(doc, [{"op": "disconnect", "wire": "review.report -> end.result"}])


def test_updates_merge_fields_and_null_removes_one() -> None:
    doc = authoring.build(review_loop())
    authoring.apply_edits(
        doc, [{"op": "update_node", "id": "log", "data": {"label": None, "value": "hi"}}]
    )
    assert "label" not in node(doc, "log")["data"]
    assert node(doc, "log")["data"]["value"] == "hi"


def test_removing_a_node_takes_its_wires() -> None:
    doc = authoring.build(review_loop())
    authoring.apply_edits(doc, [{"op": "remove_node", "id": "review"}])
    assert not any("review" in (e["from"][0], e["to"][0]) for e in doc["edges"])


def test_a_renamed_variable_is_followed_by_the_nodes_that_read_it() -> None:
    doc = authoring.build(review_loop())
    authoring.apply_edits(
        doc,
        [
            {"op": "add_node", "id": "get", "kind": "var_get", "data": {"variable": "rounds"}},
            {"op": "update_variable", "name": "rounds", "new_name": "laps"},
        ],
    )
    assert node(doc, "get")["data"]["variable"] == "laps"
    assert doc["variables"][0]["name"] == "laps"


def test_notes_and_comments_are_edits_too() -> None:
    doc = authoring.build(review_loop())
    authoring.apply_edits(
        doc,
        [
            {"op": "set_note", "id": "write", "note": "writes the importer"},
            {"op": "add_comment", "title": "Build", "nodes": ["write", "review"]},
            {"op": "set_note", "id": "plan", "note": None},
        ],
    )
    assert node(doc, "write")["data"]["note"] == "writes the importer"
    assert "note" not in node(doc, "plan")["data"]
    assert sum(1 for item in doc["nodes"] if item["kind"] == "comment") == 2


def test_a_failing_operation_names_its_place() -> None:
    doc = authoring.build(review_loop())
    with pytest.raises(authoring.GraphSpecError) as caught:
        authoring.apply_edits(doc, [{"op": "rename", "name": "x"}, {"op": "teleport"}])
    assert "Operation 2" in str(caught.value)


# -- the tools ----------------------------------------------------------------


def context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


@pytest.fixture(scope="module")
def registry():
    return build_tool_registry()


def test_the_graph_tools_load_on_demand_as_one_group(registry) -> None:
    for name in ("graph_reference", "write_graph", "edit_graph"):
        assert registry.tool(name) is not None
        assert group_of(name).name == "graphs"


async def test_write_graph_writes_a_file_the_editor_reads(tmp_path, registry) -> None:
    result = await registry.tool("write_graph").execute(
        {"path": "flows/review.flow.json", "graph": json.dumps(review_loop())},
        context(tmp_path),
    )

    assert result["path"] == "flows/review.flow.json"
    assert (result["nodes"], result["comments"]) == (8, 1)
    doc = json.loads((tmp_path / "flows" / "review.flow.json").read_text(encoding="utf-8"))
    assert compiles(doc) == []


async def test_write_graph_refuses_an_invalid_graph_and_writes_nothing(tmp_path, registry) -> None:
    spec = review_loop()
    spec["nodes"] = [item for item in spec["nodes"] if item["id"] != "start"]
    spec["wires"] = [wire for wire in spec["wires"] if not wire.startswith("start")]
    with pytest.raises(ToolExecutionError) as caught:
        await registry.tool("write_graph").execute(
            {"path": "review.flow.json", "graph": json.dumps(spec)}, context(tmp_path)
        )
    assert "not written" in str(caught.value)
    assert not (tmp_path / "review.flow.json").exists()


async def test_write_graph_keeps_an_existing_graph_unless_told(tmp_path, registry) -> None:
    tool = registry.tool("write_graph")
    args = {"path": "review.flow.json", "graph": json.dumps(review_loop())}
    await tool.execute(args, context(tmp_path))
    with pytest.raises(ToolExecutionError, match="edit_graph"):
        await tool.execute(args, context(tmp_path))
    await tool.execute({**args, "replace": True}, context(tmp_path))


async def test_a_graph_path_must_be_a_flow_file(tmp_path, registry) -> None:
    with pytest.raises(ToolArgumentError, match=r"\.flow\.json"):
        await registry.tool("write_graph").execute(
            {"path": "review.json", "graph": "{}"}, context(tmp_path)
        )


async def test_edit_graph_is_all_or_nothing(tmp_path, registry) -> None:
    await registry.tool("write_graph").execute(
        {"path": "review.flow.json", "graph": json.dumps(review_loop())}, context(tmp_path)
    )
    path = tmp_path / "review.flow.json"
    before = path.read_text(encoding="utf-8")
    edit = registry.tool("edit_graph")

    # The second operation leaves Start with no way forward: an orphan is a
    # warning, but a removed Start is an error, and nothing is written.
    with pytest.raises(ToolExecutionError):
        await edit.execute(
            {
                "path": "review.flow.json",
                "operations": json.dumps(
                    [{"op": "rename", "name": "renamed"}, {"op": "remove_node", "id": "start"}]
                ),
            },
            context(tmp_path),
        )
    assert path.read_text(encoding="utf-8") == before

    result = await edit.execute(
        {
            "path": "review.flow.json",
            "operations": json.dumps([{"op": "set_note", "id": "end", "note": "the answer"}]),
        },
        context(tmp_path),
    )
    assert result["done"] == ["set the note on end"]
    assert '"note": "the answer"' in path.read_text(encoding="utf-8")


async def test_the_reference_lists_the_kinds_and_reads_a_graph_back(tmp_path, registry) -> None:
    tool = registry.tool("graph_reference")
    overview = (await tool.execute({}, context(tmp_path)))["reference"]
    assert "comment (Comment)" in overview
    assert "start -> agent -> end" in overview
    assert "write_file" in overview  # a tool an agent node can be given

    detail = (await tool.execute({"kinds": "loop, comment"}, context(tmp_path)))["reference"]
    assert "move_mode" in detail and "count" in detail

    await registry.tool("write_graph").execute(
        {"path": "review.flow.json", "graph": json.dumps(review_loop())}, context(tmp_path)
    )
    read = await tool.execute({"path": "review.flow.json"}, context(tmp_path))
    assert "plan.report -> write.task" in json.loads(read["spec"])["wires"]


# -- the editor's fixture -----------------------------------------------------

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "vscode-extension"
    / "e2e"
    / "fixtures"
    / "agent-written.flow.json"
)


def _fixture_text() -> str:
    return authoring.serialize(authoring.build(review_loop()))


def test_the_editor_fixture_is_what_write_graph_makes() -> None:
    assert FIXTURE.read_text(encoding="utf-8") == _fixture_text(), (
        "vscode-extension/e2e/fixtures/agent-written.flow.json is stale; regenerate it "
        "(see this module's docstring)."
    )


if __name__ == "__main__":
    FIXTURE.write_text(_fixture_text(), encoding="utf-8")
    print(f"wrote {FIXTURE}")
