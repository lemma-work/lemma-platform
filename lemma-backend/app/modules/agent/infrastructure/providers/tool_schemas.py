"""Tool input schemas made acceptable to every provider and MCP client."""

from __future__ import annotations

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.providers.pydantic_ai_compat import (
    InlineDefsJsonSchemaTransformer,
)


def normalize_json_schema(schema: JsonObject | None) -> JsonObject:
    """A stored schema as a tool's object schema, permissive where it is silent."""
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    normalized = dict(schema)
    if normalized.get("type") != "object":
        normalized["type"] = "object"
    normalized.setdefault("properties", {})
    normalized.setdefault("additionalProperties", True)
    return normalized


def inline_schema(schema: JsonObject) -> JsonObject:
    return InlineDefsJsonSchemaTransformer(schema, strict=False).walk()


def inline_tool_schema_refs(schema: JsonObject) -> JsonObject:
    """Inline non-recursive ``$defs``/``$ref`` in a tool's input schema.

    OpenAI-compatible providers (notably Fireworks' GLM) can't resolve
    ``$ref`` -> ``#/$defs/...`` server-side and reject the request. The
    pydantic-ai model path inlines refs through a model profile; schemas served
    over MCP to remote harnesses are inlined here. Best-effort: a schema the
    transformer can't process falls back to the original.
    """
    if not isinstance(schema, dict):
        return schema
    try:
        return InlineDefsJsonSchemaTransformer(dict(schema), strict=False).walk()
    except Exception:  # noqa: BLE001 -- never break tool listing over a schema quirk
        return dict(schema)
