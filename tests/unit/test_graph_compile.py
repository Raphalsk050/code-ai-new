from __future__ import annotations

from typing import Any

from code_ai.core.graph.compile import compile_graph
from code_ai.core.graph.models import SCHEMA_VERSION, catalog_payload
from code_ai.core.graph.professions import professions_payload

PROFESSIONS = tuple(row["name"] for row in professions_payload())


def graph(**overrides: Any) -> dict[str, Any]:
    """A minimal valid graph: Start -> Agent -> Print -> End, with a Model node."""

    base: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "name": "test",
        "variables": [],
        "nodes": [
            {"id": "n1", "kind": "start", "pos": [0, 0], "data": {}},
            {
                "id": "n2",
                "kind": "model",
                "pos": [0, 200],
                "data": {"model": "m", "base_url": "http://localhost:11434/v1"},
            },
            {
                "id": "n3",
                "kind": "profession",
                "pos": [200, 0],
                "data": {"profession": "architect", "task": "design it"},
            },
            {"id": "n4", "kind": "print", "pos": [400, 0], "data": {"label": "out"}},
            {"id": "n5", "kind": "end", "pos": [600, 0], "data": {}},
        ],
        "edges": [
            {"id": "e1", "kind": "exec", "from": ["n1", "out"], "to": ["n3", "in"]},
            {"id": "e2", "kind": "data", "from": ["n2", "model"], "to": ["n3", "model"]},
            {"id": "e3", "kind": "exec", "from": ["n3", "out"], "to": ["n4", "in"]},
            {"id": "e4", "kind": "data", "from": ["n3", "report"], "to": ["n4", "value"]},
            {"id": "e5", "kind": "exec", "from": ["n4", "out"], "to": ["n5", "in"]},
        ],
        "view": {"pan": [0, 0], "zoom": 1},
    }
    base.update(overrides)
    return base


def errors(issues: list[Any]) -> list[str]:
    return [item.message for item in issues if item.severity == "error"]


def compile_ok(doc: dict[str, Any]):
    program, issues = compile_graph(doc, professions=PROFESSIONS)
    assert program is not None, errors(issues)
    return program, issues


def test_valid_graph_compiles_with_resolved_wiring() -> None:
    program, issues = compile_ok(graph())

    assert errors(issues) == []
    assert program.start_id == "n1"
    assert program.nodes["n1"].exec_out["out"] == ("n3", "e1")
    assert program.nodes["n3"].data_in["model"] == ("n2", "model", "e2")
    assert program.nodes["n4"].data_in["value"] == ("n3", "report", "e4")


def test_unknown_node_kind_is_refused() -> None:
    doc = graph()
    doc["nodes"].append({"id": "nx", "kind": "teleporter", "pos": [0, 0], "data": {}})
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("Unknown node kind" in message for message in errors(issues))


def test_graph_without_start_is_refused() -> None:
    doc = graph()
    doc["nodes"] = [node for node in doc["nodes"] if node["kind"] != "start"]
    doc["edges"] = [edge for edge in doc["edges"] if edge["from"][0] != "n1"]
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("no Start node" in message for message in errors(issues))


def test_two_start_nodes_are_refused() -> None:
    doc = graph()
    doc["nodes"].append({"id": "n9", "kind": "start", "pos": [0, 400], "data": {}})
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("only 1 Start" in message for message in errors(issues))


def test_exec_output_may_drive_only_one_wire() -> None:
    doc = graph()
    doc["edges"].append(
        {"id": "e9", "kind": "exec", "from": ["n1", "out"], "to": ["n4", "in"]}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("Sequence node to fan out" in message for message in errors(issues))


def test_data_input_takes_one_value() -> None:
    doc = graph()
    doc["nodes"].append(
        {"id": "n6", "kind": "model", "pos": [0, 400], "data": {"model": "second"}}
    )
    doc["edges"].append(
        {"id": "e9", "kind": "data", "from": ["n6", "model"], "to": ["n3", "model"]}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("takes one value" in message for message in errors(issues))


def test_exec_pin_cannot_reach_a_data_pin() -> None:
    doc = graph()
    doc["edges"].append(
        {"id": "e9", "kind": "exec", "from": ["n3", "report"], "to": ["n5", "in"]}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("cannot be wired to a data pin" in message for message in errors(issues))


def test_incompatible_pin_types_are_refused() -> None:
    doc = graph()
    doc["variables"] = [{"id": "v1", "name": "flag", "type": "bool", "default": False}]
    doc["nodes"].append(
        {"id": "n7", "kind": "var_get", "pos": [0, 600], "data": {"variable": "flag"}}
    )
    # bool -> model is not assignable in either direction.
    doc["edges"] = [edge for edge in doc["edges"] if edge["id"] != "e2"]
    doc["edges"].append(
        {"id": "e9", "kind": "data", "from": ["n7", "value"], "to": ["n3", "model"]}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("cannot feed a model pin" in message for message in errors(issues))


def test_var_get_reports_the_declared_variable_type() -> None:
    doc = graph()
    doc["variables"] = [{"id": "v1", "name": "rounds", "type": "int", "default": 2}]
    doc["nodes"].append(
        {"id": "n7", "kind": "var_get", "pos": [0, 600], "data": {"variable": "rounds"}}
    )
    doc["edges"] = [edge for edge in doc["edges"] if edge["id"] != "e4"]
    doc["edges"].append(
        {"id": "e9", "kind": "data", "from": ["n7", "value"], "to": ["n4", "value"]}
    )
    program, _ = compile_ok(doc)

    assert program.pin_type("n7", "value") == "int"


def test_unknown_variable_reference_is_refused() -> None:
    doc = graph()
    doc["nodes"].append(
        {"id": "n7", "kind": "var_get", "pos": [0, 600], "data": {"variable": "ghost"}}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("No variable named 'ghost'" in message for message in errors(issues))


def test_duplicate_variable_names_are_refused() -> None:
    doc = graph(
        variables=[
            {"id": "v1", "name": "x", "type": "int", "default": 0},
            {"id": "v2", "name": "x", "type": "string", "default": ""},
        ]
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("Duplicate variable name" in message for message in errors(issues))


def test_literal_api_key_is_refused() -> None:
    doc = graph()
    doc["nodes"][1]["data"]["api_key_ref"] = "sk-live-abcdef123456"
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("must not be stored in a graph file" in message for message in errors(issues))


def test_key_references_the_runtime_understands_are_accepted() -> None:
    for reference in ("session", "env:OPENAI_KEY", "secret:work-endpoint"):
        doc = graph()
        doc["nodes"][1]["data"]["api_key_ref"] = reference
        program, issues = compile_graph(doc, professions=PROFESSIONS)
        assert program is not None, (reference, errors(issues))


def test_unknown_profession_is_refused() -> None:
    doc = graph()
    doc["nodes"][2]["data"]["profession"] = "wizard"
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("Unknown profession" in message for message in errors(issues))


def test_agent_without_a_task_is_refused() -> None:
    doc = graph()
    doc["nodes"][2]["data"]["task"] = "   "
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("needs task" in message for message in errors(issues))


def test_wired_task_satisfies_the_requirement() -> None:
    doc = graph()
    doc["nodes"][2]["data"].pop("task")
    doc["variables"] = [{"id": "v1", "name": "brief", "type": "string", "default": "do it"}]
    doc["nodes"].append(
        {"id": "n7", "kind": "var_get", "pos": [0, 600], "data": {"variable": "brief"}}
    )
    doc["edges"].append(
        {"id": "e9", "kind": "data", "from": ["n7", "value"], "to": ["n3", "task"]}
    )

    compile_ok(doc)


def test_data_cycle_is_refused() -> None:
    doc = graph()
    doc["nodes"].append(
        {
            "id": "n6",
            "kind": "profession",
            "pos": [800, 0],
            "data": {"profession": "teacher", "task": "explain"},
        }
    )
    doc["edges"] += [
        {"id": "e9", "kind": "data", "from": ["n3", "report"], "to": ["n6", "task"]},
        {"id": "e10", "kind": "data", "from": ["n6", "report"], "to": ["n3", "task"]},
    ]
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("cycle" in message for message in errors(issues))


def test_node_cannot_feed_itself() -> None:
    doc = graph()
    doc["edges"].append(
        {"id": "e9", "kind": "data", "from": ["n3", "report"], "to": ["n3", "task"]}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("cannot feed its own input" in message for message in errors(issues))


def test_unknown_field_is_a_warning_not_an_error() -> None:
    doc = graph()
    doc["nodes"][3]["data"]["colour"] = "red"
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is not None
    assert any(
        item.severity == "warning" and "no field 'colour'" in item.message for item in issues
    )


def test_orphan_node_is_a_warning() -> None:
    doc = graph()
    doc["nodes"].append({"id": "n8", "kind": "print", "pos": [0, 800], "data": {}})
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is not None
    assert any(
        item.node_id == "n8" and "never runs" in item.message
        for item in issues
        if item.severity == "warning"
    )


def test_unread_pure_node_is_a_warning() -> None:
    doc = graph()
    doc["nodes"].append({"id": "n8", "kind": "model", "pos": [0, 800], "data": {"model": "x"}})
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is not None
    assert any(item.node_id == "n8" and "Nothing reads" in item.message for item in issues)


def test_future_schema_version_is_refused() -> None:
    program, issues = compile_graph(graph(version=99), professions=PROFESSIONS)

    assert program is None
    assert any("Update Code-AI" in message for message in errors(issues))


def test_unknown_top_level_key_is_refused() -> None:
    doc = graph()
    doc["surprise"] = True
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None


def test_loop_cap_above_the_configured_ceiling_is_refused() -> None:
    doc = graph()
    doc["nodes"].append(
        {
            "id": "n6",
            "kind": "loop",
            "pos": [200, 300],
            "data": {"kind": "count", "count": 2, "max_iterations": 999},
        }
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS, max_loop_iterations=50)

    assert program is None
    assert any("capped at 50" in message for message in errors(issues))


def test_judge_decision_needs_a_question() -> None:
    doc = graph()
    doc["nodes"].append(
        {"id": "n6", "kind": "decision", "pos": [200, 300], "data": {"mode": "judge"}}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("needs a question" in message for message in errors(issues))


def test_sequence_outputs_are_bounded() -> None:
    doc = graph()
    doc["nodes"].append(
        {"id": "n6", "kind": "sequence", "pos": [200, 300], "data": {"outputs": 40}}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("between 1 and 12" in message for message in errors(issues))


def test_sequence_exposes_its_dynamic_pins_in_order() -> None:
    doc = graph()
    doc["nodes"].append(
        {"id": "n6", "kind": "sequence", "pos": [200, 300], "data": {"outputs": 3}}
    )
    doc["edges"] = [edge for edge in doc["edges"] if edge["id"] != "e1"]
    doc["edges"] += [
        {"id": "e1", "kind": "exec", "from": ["n1", "out"], "to": ["n6", "in"]},
        {"id": "e9", "kind": "exec", "from": ["n6", "out_0"], "to": ["n3", "in"]},
        {"id": "e10", "kind": "exec", "from": ["n6", "out_2"], "to": ["n5", "in"]},
    ]
    doc["edges"] = [edge for edge in doc["edges"] if edge["id"] != "e5"]
    program, _ = compile_ok(doc)

    assert program.nodes["n6"].ordered_exec == ("out_0", "out_1", "out_2")


def test_catalog_lists_every_kind_with_pins_and_colours() -> None:
    payload = catalog_payload(professions_payload())
    kinds = {row["kind"] for row in payload["node_kinds"]}

    assert {"start", "end", "model", "profession", "sequence", "decision", "loop"} <= kinds
    assert payload["pin_colors"]["exec"] == "#FFFFFF"
    assert all(pin["color"] for row in payload["node_kinds"] for pin in row["pins"])
    assert {"architect", "code_writer", "ui_designer", "teacher"} <= {
        row["name"] for row in payload["professions"]
    }


def test_read_only_professions_never_hold_write_capabilities() -> None:
    for row in professions_payload():
        if row["read_only"]:
            assert "local_write" not in row["capabilities"], row["name"]
            assert "process" not in row["capabilities"], row["name"]


# -- generic agents and wired tools -----------------------------------------


def with_tools(*toolsets: dict[str, Any]) -> dict[str, Any]:
    """The base graph plus one Tools node per mapping, each wired into the agent."""

    doc = graph()
    for index, data in enumerate(toolsets):
        node_id = f"t{index}"
        doc["nodes"].append({"id": node_id, "kind": "tools", "pos": [0, 400], "data": data})
        doc["edges"].append(
            {
                "id": f"et{index}",
                "kind": "data",
                "from": [node_id, "tools"],
                "to": ["n3", "tools"],
            }
        )
    return doc


def warnings(issues: list[Any]) -> list[str]:
    return [item.message for item in issues if item.severity == "warning"]


def test_the_tools_pin_takes_several_wires_in_file_order() -> None:
    doc = with_tools({"tools": ["read_file"]}, {"tools": ["web_search"]})
    program, issues = compile_ok(doc)

    assert errors(issues) == []
    assert program.nodes["n3"].multi_in["tools"] == (
        ("t0", "tools", "et0"),
        ("t1", "tools", "et1"),
    )
    assert "tools" not in program.nodes["n3"].data_in


def test_a_single_data_input_still_takes_one_wire() -> None:
    doc = graph()
    doc["nodes"].append(
        {"id": "n6", "kind": "model", "pos": [0, 300], "data": {"model": "other"}}
    )
    doc["edges"].append(
        {"id": "e6", "kind": "data", "from": ["n6", "model"], "to": ["n3", "model"]}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert "A data input takes one value." in errors(issues)


def test_the_same_wire_twice_into_a_multi_pin_is_refused() -> None:
    doc = with_tools({"tools": ["read_file"]})
    doc["edges"].append(
        {"id": "dup", "kind": "data", "from": ["t0", "tools"], "to": ["n3", "tools"]}
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert "That connection is drawn twice." in errors(issues)


def test_an_agent_without_a_profession_is_a_custom_agent() -> None:
    doc = graph()
    doc["nodes"][2]["data"] = {"task": "audit it", "role_override": "You are an auditor."}
    _, issues = compile_ok(doc)

    assert errors(issues) == []


def test_an_unknown_profession_is_still_refused() -> None:
    doc = graph()
    doc["nodes"][2]["data"]["profession"] = "astronaut"
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("Unknown profession" in message for message in errors(issues))


def test_tool_names_this_build_does_not_have_are_a_warning() -> None:
    doc = with_tools({"tools": ["read_file", "teleport"]})
    doc["nodes"][2]["data"]["blocked_tools"] = ["warp_drive"]
    _, issues = compile_graph(
        doc, professions=PROFESSIONS, tools=("read_file", "write_file")
    )

    assert errors(issues) == []
    found = warnings(issues)
    assert any("teleport" in message for message in found)
    assert any("warp_drive" in message for message in found)


def test_a_tool_list_that_is_not_a_list_is_refused() -> None:
    doc = with_tools({"tools": "read_file"})
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert "This must be a list of tool names." in errors(issues)


def test_an_empty_tools_node_is_a_warning() -> None:
    _, issues = compile_ok(with_tools({"name": "nothing", "tools": []}))

    assert any("No tools picked" in message for message in warnings(issues))


def test_a_permission_switch_must_be_on_or_off() -> None:
    doc = graph()
    doc["nodes"][2]["data"]["can_write"] = "sometimes"
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert "can_write must be on or off." in errors(issues)


def test_a_profession_with_every_switch_off_only_warns() -> None:
    doc = graph()
    doc["nodes"].append(
        {
            "id": "p1",
            "kind": "custom_profession",
            "pos": [0, 500],
            "data": {
                "name": "Ana",
                "role": "You talk.",
                "can_read": False,
                "can_write": False,
                "can_run": False,
                "can_browse": False,
                "can_review": False,
            },
        }
    )
    doc["edges"].append(
        {"id": "ep", "kind": "data", "from": ["p1", "profession"], "to": ["n3", "profession"]}
    )
    _, issues = compile_ok(doc)

    assert any("can only talk" in message for message in warnings(issues))


def test_a_named_node_is_labelled_by_its_name() -> None:
    doc = with_tools({"name": "git kit", "tools": ["git_review"]})
    doc["nodes"][2]["data"]["name"] = "Auditor"
    program, _ = compile_ok(doc)

    assert program.nodes["n3"].label == "Auditor"
    assert program.nodes["t0"].label == "git kit"


def test_the_catalog_serves_the_permissions_and_the_tool_groups() -> None:
    payload = catalog_payload(professions_payload(), tools=[{"name": "read_file"}])

    assert [row["field"] for row in payload["permissions"]] == [
        "can_read",
        "can_write",
        "can_run",
        "can_browse",
        "can_review",
    ]
    assert payload["tools"] == [{"name": "read_file"}]
    assert payload["tool_groups"][0]["name"] == "read"
    agent = next(row for row in payload["node_kinds"] if row["kind"] == "profession")
    tools_pin = next(pin for pin in agent["pins"] if pin["name"] == "tools")
    assert tools_pin["multi"] is True
    assert sum(1 for row in payload["professions"] if row["plain"]) == 1


def test_a_clean_agent_needs_a_task_and_nothing_more() -> None:
    doc = graph()
    doc["nodes"][2] = {"id": "n3", "kind": "clean_agent", "pos": [200, 0], "data": {}}
    doc["edges"] = [edge for edge in doc["edges"] if edge["id"] != "e2"]
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("needs task" in message for message in errors(issues))

    doc["nodes"][2]["data"] = {"task": "Oi", "name": "Ana"}
    program, issues = compile_ok(doc)
    assert errors(issues) == []
    assert program.nodes["n3"].label == "Ana"


# -- function nodes -----------------------------------------------------------


def function_graph(*nodes: dict[str, Any], edges: list[dict[str, Any]] | None = None):
    doc = graph()
    doc["nodes"].extend(nodes)
    doc["edges"].extend(edges or [])
    return doc


def fnode(node_id: str, node_kind: str, **data: Any) -> dict[str, Any]:
    return {"id": node_id, "kind": node_kind, "pos": [0, 600], "data": data}


def test_an_int_math_result_may_feed_an_int_pin_and_a_float_one_may_not() -> None:
    loop = fnode("lp", "loop", kind="count")
    ok = function_graph(
        fnode("m", "math", type="int"),
        loop,
        edges=[{"id": "x1", "kind": "data", "from": ["m", "result"], "to": ["lp", "count"]}],
    )
    _, issues = compile_graph(ok, professions=PROFESSIONS)
    assert not any("cannot feed" in message for message in errors(issues))

    bad = function_graph(
        fnode("m", "math", type="float"),
        fnode("lp", "loop", kind="count"),
        edges=[{"id": "x1", "kind": "data", "from": ["m", "result"], "to": ["lp", "count"]}],
    )
    program, issues = compile_graph(bad, professions=PROFESSIONS)
    assert program is None
    assert "A float value cannot feed a int pin." in errors(issues)


def test_a_wire_into_an_input_the_node_no_longer_has_is_refused() -> None:
    doc = function_graph(
        fnode("j", "append", inputs=2),
        fnode("s", "var_get", variable=""),
        edges=[{"id": "x1", "kind": "data", "from": ["n3", "report"], "to": ["j", "in_4"]}],
    )
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("in_4" in message and "not on this node" in message for message in errors(issues))


def test_a_function_dropdown_must_hold_one_of_its_options() -> None:
    doc = function_graph(fnode("m", "math", op="teleport"))
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert any("Operation must be one of" in message for message in errors(issues))


def test_dynamic_input_counts_are_bounded_and_their_literals_are_known() -> None:
    doc = function_graph(fnode("j", "append", inputs=3, in_2="x"))
    _, issues = compile_graph(doc, professions=PROFESSIONS)
    assert not any("has no field" in message for message in warnings(issues))

    too_many = function_graph(fnode("j", "append", inputs=13))
    program, issues = compile_graph(too_many, professions=PROFESSIONS)
    assert program is None
    assert any("between 1 and 12 inputs" in message for message in errors(issues))


def test_a_model_node_reasoning_effort_must_be_a_known_level() -> None:
    doc = graph()
    doc["nodes"][1]["data"]["reasoning_effort"] = "ludicrous"
    program, issues = compile_graph(doc, professions=PROFESSIONS)

    assert program is None
    assert "Unknown reasoning effort 'ludicrous'." in errors(issues)

    doc["nodes"][1]["data"]["reasoning_effort"] = "high"
    compile_ok(doc)
