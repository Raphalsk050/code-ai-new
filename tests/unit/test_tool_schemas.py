from __future__ import annotations

from code_ai.bootstrap import build_tool_registry


def test_default_tool_schemas_stay_atomic_and_simple() -> None:
    registry = build_tool_registry()
    definitions = registry.definitions()
    names = {definition.name for definition in definitions}

    assert "control_terminal" not in names
    assert {
        "start_terminal",
        "send_terminal_text",
        "terminal_enter",
        "interrupt_terminal",
        "terminate_terminal",
        "request_external_gap",
    } <= names

    for definition in definitions:
        schema = definition.input_schema
        assert schema.get("type") == "object", definition.name
        # OpenAI strict-mode contract: no extra properties and every declared
        # property must be listed in ``required`` (optionals are nullable).
        assert schema.get("additionalProperties") is False, definition.name
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        assert set(required) == set(properties), definition.name
        for name, spec in properties.items():
            assert spec.get("description"), f"{definition.name}.{name} missing description"
        # Stay atomic for weak local models: no enums, no nested object arrays.
        assert not _has_nested_object_array(schema), definition.name
        assert not _has_enum(schema), definition.name


def test_tool_schema_helper_makes_optionals_nullable_and_all_required() -> None:
    from code_ai.tools.schema import tool_schema

    schema = tool_schema(
        {
            "must": {"type": "string", "description": "required"},
            "maybe": {"type": "integer", "description": "optional"},
        },
        required=("must",),
    )
    assert schema["additionalProperties"] is False
    # strict: every property listed in required
    assert set(schema["required"]) == {"must", "maybe"}
    # truly-required keeps its bare type; optional becomes nullable
    assert schema["properties"]["must"]["type"] == "string"
    assert schema["properties"]["maybe"]["type"] == ["integer", "null"]


def _has_enum(value: object) -> bool:
    if isinstance(value, dict):
        return "enum" in value or any(_has_enum(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_enum(item) for item in value)
    return False


def _has_nested_object_array(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "array":
            items = value.get("items")
            if isinstance(items, dict) and items.get("type") == "object":
                return True
        return any(_has_nested_object_array(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_nested_object_array(item) for item in value)
    return False
