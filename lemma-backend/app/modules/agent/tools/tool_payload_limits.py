"""A ceiling on what one tool result may put into the conversation.

Most tools already bound what they return. Six did not: `run_connector_operation`
and `describe_connector_operation` (which echoes whole input and output schemas,
including on a validation error), a sandboxed `function_<name>`'s output,
`query_subagents(mode="messages")` and `interact_subagent(action="await")` (which
carry a *child* run's transcript, its own large tool results included), and
`load_skill`, which returns an entire file.

An unbounded result is not a one-turn cost. It is persisted, replayed on every
later turn of the conversation, and counted against the model's window each time.
One oversized connector response can crowd a conversation out of its own context.

`pod_read_file` is the pattern being copied: a cap *and* a flag saying the cap
bit, so the model can tell a complete answer from a clipped one and go back for
the rest.
"""

from __future__ import annotations

import json
from typing import Any

#: Roughly 12k tokens. `workspace_cli.helper` carried a constant of exactly this
#: value, for exactly this purpose, that nothing ever referenced; it has been
#: removed in favour of this one. Declared here rather than shared from there
#: because the connector and function toolsets use this module, and reaching into
#: workspace_cli would make an import cycle.
DEFAULT_TOOL_PAYLOAD_LIMIT = 50_000


def _describe(value: Any) -> str:
    """A stand-in for a value JSON cannot render, that is never its raw bytes."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    for name in ("data", "content", "bytes", "payload", "blob", "raw"):
        attribute = getattr(value, name, None)
        if isinstance(attribute, (bytes, bytearray, memoryview)):
            return f"<{type(value).__name__}: {len(attribute)} bytes>"
    return str(value)


def _rendered_length(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    try:
        # Never `default=str` here: that is what renders a bytes payload as its
        # repr, so a value being measured *for being too large* gets measured at
        # several times its real size -- and this decides whether it is clipped.
        return len(json.dumps(value, default=_describe))
    except TypeError, ValueError:  # pragma: no cover - defensive
        return len(_describe(value))


def bounded_tool_payload(
    value: Any, *, limit: int = DEFAULT_TOOL_PAYLOAD_LIMIT, what: str = "result"
) -> Any:
    """`value`, or a marked stand-in when it is too large to carry.

    A structure is replaced rather than clipped: half a JSON document is not a
    smaller document, it is an unparseable one. Text keeps its head, which is
    where a schema, a document or a message thread introduces itself.
    """
    if _rendered_length(value) <= limit:
        return value

    if isinstance(value, str):
        return {
            "truncated": True,
            "note": (
                f"This {what} was {len(value)} characters and has been clipped to "
                f"{limit}. Narrow the request if you need the rest."
            ),
            "preview": value[:limit],
        }
    return {
        "truncated": True,
        "note": (
            f"This {what} was too large to include ("
            f"{_rendered_length(value)} characters, limit {limit}). Ask for a "
            "narrower slice of it -- fewer items, or one field at a time."
        ),
    }
