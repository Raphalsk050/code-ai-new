from __future__ import annotations

from typing import Any


def _nullable(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``spec`` whose JSON type also accepts ``null``.

    OpenAI strict function calling forbids leaving a property out of
    ``required``; an optional input is instead modelled as nullable so the
    model may pass ``null`` to omit it while still satisfying the contract.
    """

    spec = dict(spec)
    declared = spec.get("type")
    if isinstance(declared, str):
        if declared != "null":
            spec["type"] = [declared, "null"]
    elif isinstance(declared, list):
        if "null" not in declared:
            spec["type"] = [*declared, "null"]
    return spec


def tool_schema(
    properties: dict[str, dict[str, Any]],
    *,
    required: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Build an OpenAI strict-mode-compliant JSON Schema for a tool.

    The contract enforced here mirrors the OpenAI structured-outputs rules:

    * ``additionalProperties`` is always ``false``.
    * Every declared property is listed in ``required`` (strict requirement).
    * Properties absent from ``required`` are made nullable so they remain
      semantically optional.

    Schemas stay intentionally atomic: callers declare only the inputs the
    model should control. Genuinely required inputs are passed in ``required``;
    everything else becomes nullable.
    """

    required_set = set(required)
    unknown = required_set - set(properties)
    if unknown:
        raise ValueError(f"required references undeclared properties: {sorted(unknown)}")
    built: dict[str, Any] = {}
    for name, spec in properties.items():
        built[name] = spec if name in required_set else _nullable(spec)
    return {
        "type": "object",
        "properties": built,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }
