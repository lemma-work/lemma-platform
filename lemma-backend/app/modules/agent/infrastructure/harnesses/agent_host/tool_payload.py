"""Bounding what a tool call persists.

The host now names every tool and shapes its arguments and result itself (see
docs/architecture/agent-host-events.md), so nothing here reads an adapter's
payload any more. What is left is the one guard the backend still owns:
``bounded_tool_value`` keeps a pathological *result* -- a megabyte of stdout, a
deeply nested object -- from being persisted verbatim into a conversation
message.

That bounding is lossy on purpose, so anything that must survive intact (a
structured final answer, say) has to be read from the raw payload *before* it
passes through here.

Arguments are deliberately exempt: see :func:`unbounded_tool_value`.
"""

from __future__ import annotations

from app.modules.agent.domain.value_objects import JsonValue

_MAX_TOOL_STRING_CHARACTERS = 4_096
_MAX_TOOL_COLLECTION_ITEMS = 32
_MAX_TOOL_VALUE_DEPTH = 4


def unbounded_tool_value(value: object) -> JsonValue:
    """A tool's arguments, coerced to JSON-safe types but never truncated.

    Bounding a *result* is a guard against a megabyte of stdout. Bounding the
    *arguments* is data loss: they are what the conversation renders and what a
    view is built from. ``display_resource(type="WIDGET")`` carries its whole
    HTML document in ``content``, far past ``_MAX_TOOL_STRING_CHARACTERS``, so
    the bound replaced the widget with a placeholder and left the card nothing
    to render. The in-process harness stores arguments unbounded; this is the
    same promise, so a conversation reads the same either way.
    """
    if isinstance(value, dict):
        return {str(key): unbounded_tool_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unbounded_tool_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def bounded_tool_value(value: object, *, depth: int = 0) -> JsonValue:
    if depth >= _MAX_TOOL_VALUE_DEPTH:
        return {"omitted": "nested tool payload"}
    if isinstance(value, str):
        if len(value) <= _MAX_TOOL_STRING_CHARACTERS:
            return value
        # Keep the head. This replaced the whole string, so a 4,097-character
        # result lost all 4,097 -- and because this payload *is* the persisted
        # transcript, a `carries_history=True` resume replayed
        # `{"omitted": ...}` where the file the agent had read used to be. The
        # collection branches below already report what they dropped; this one
        # did not even keep a sample.
        return {
            "truncated": True,
            "character_count": len(value),
            "preview": value[:_MAX_TOOL_STRING_CHARACTERS],
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
