"""Reading an ACP tool-call payload safely.

Adapters vary in where they put a tool call's name, arguments, and result, so
these probe several shapes rather than assuming one. ``bounded_tool_value`` is
the guard that keeps a pathological payload — a megabyte of stdout, a deeply
nested object — from being persisted verbatim into a conversation message.

That bounding is lossy on purpose, so anything that must survive intact (a
structured final answer, say) has to be read from the raw payload *before* it
passes through here.
"""

from __future__ import annotations

from app.modules.agent.domain.value_objects import JsonObject, JsonValue

_MAX_TOOL_STRING_CHARACTERS = 4_096
_MAX_TOOL_COLLECTION_ITEMS = 32
_MAX_TOOL_VALUE_DEPTH = 4


def first_present(payload: JsonObject, *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def tool_name_from_payload(payload: JsonObject) -> str:
    for key in ("name", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    kind = payload.get("kind")
    if isinstance(kind, str) and kind.strip():
        normalized = kind.strip().lower()
        return "exec_command" if normalized == "execute" else normalized
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "tool"


def tool_metadata(metadata: JsonObject, payload: JsonObject) -> JsonObject:
    result = dict(metadata)
    for source, target in (("title", "tool_title"), ("kind", "tool_kind")):
        value = payload.get(source)
        if isinstance(value, str) and value.strip():
            result[target] = value.strip()
    return result


def tool_args(payload: JsonObject, tool_name: str) -> JsonValue:
    value = first_present(payload, "arguments", "args", "rawInput")
    if tool_name == "exec_command" and isinstance(value, dict):
        normalized = dict(value)
        command = normalized.pop("command", None)
        if "cmd" not in normalized and isinstance(command, str):
            normalized["cmd"] = command
        value = normalized
    return bounded_tool_value(value)


def bounded_tool_value(value: object, *, depth: int = 0) -> JsonValue:
    if depth >= _MAX_TOOL_VALUE_DEPTH:
        return {"omitted": "nested tool payload"}
    if isinstance(value, str):
        if len(value) <= _MAX_TOOL_STRING_CHARACTERS:
            return value
        return {
            "omitted": "large tool payload",
            "character_count": len(value),
        }
    if isinstance(value, dict):
        items = list(value.items())
        result = {
            str(key): bounded_tool_value(item, depth=depth + 1)
            for key, item in items[:_MAX_TOOL_COLLECTION_ITEMS]
        }
        if len(items) > _MAX_TOOL_COLLECTION_ITEMS:
            result["_omitted_item_count"] = len(items) - _MAX_TOOL_COLLECTION_ITEMS
        return result
    if isinstance(value, list):
        result = [
            bounded_tool_value(item, depth=depth + 1)
            for item in value[:_MAX_TOOL_COLLECTION_ITEMS]
        ]
        if len(value) > _MAX_TOOL_COLLECTION_ITEMS:
            result.append(
                {"omitted_item_count": len(value) - _MAX_TOOL_COLLECTION_ITEMS}
            )
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def json_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}
