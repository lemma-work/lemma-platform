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
from app.modules.agent.infrastructure.mcp import normalize_local_mcp_tool_name

_MAX_TOOL_STRING_CHARACTERS = 4_096
_MAX_TOOL_COLLECTION_ITEMS = 32
_MAX_TOOL_VALUE_DEPTH = 4


def first_present(payload: JsonObject, *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def tool_name_from_payload(payload: JsonObject) -> str:
    """The tool's own name, in the spelling the rest of Lemma uses.

    ACP's ``ToolCall`` has no required name field, so the name has to be found
    among several optional ones — and the answer decides how the call renders,
    because every label, icon and contextual card is keyed on the tool name.

    ``_meta`` is checked before ``kind`` because ``kind`` is a *category*, not
    an identity: every web search, page fetch and MCP call an agent makes
    arrives as ``fetch`` / ``other``, so reading it as the name collapsed
    unrelated tools into one and lost the only word worth showing. Claude Code
    reports the real name under ``_meta.claudeCode.toolName``, and this reads
    any vendor's ``_meta.<vendor>.toolName`` rather than that one convention.

    The result is normalized, so a Lemma MCP tool a local agent calls as
    ``mcp__lemma__lemma_exec_command`` is the same ``exec_command`` the pod
    agent calls directly. Third-party MCP names are left alone.
    """
    return normalize_local_mcp_tool_name(_reported_tool_name(payload))


def _reported_tool_name(payload: JsonObject) -> str:
    for key in ("name", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    meta_name = _meta_tool_name(payload.get("_meta"))
    if meta_name:
        return meta_name
    kind = payload.get("kind")
    if isinstance(kind, str) and kind.strip():
        normalized = kind.strip().lower()
        if normalized == "execute":
            return "exec_command"
        # "other" is the ACP kind for everything an adapter has no category
        # for — every MCP tool included. It names nothing, so the title (which
        # for those calls *is* the tool name) is the better answer.
        if normalized != "other":
            return normalized
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "tool"


def _meta_tool_name(meta: object) -> str | None:
    """Read ``_meta.<vendor>.toolName`` from an ACP payload's extension point."""
    if not isinstance(meta, dict):
        return None
    for value in meta.values():
        if not isinstance(value, dict):
            continue
        for key in ("toolName", "tool_name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


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
