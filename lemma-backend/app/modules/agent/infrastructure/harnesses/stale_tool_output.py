"""Shrinking tool output the model read several steps ago.

A tool result stays in the run's history verbatim for the rest of the run, so a
40,000-character file read at step 3 is re-sent on every request after it --
and it is the largest thing in most histories, which is what brings compaction
forward. Long after the model acted on it, its head (what it was) and its tail
(where it ended) are what is still worth carrying, with a note saying how to get
the rest back.

Cache-friendly by construction. Clipping a message rewrites the history from
that message on, and a provider's prompt cache is a prefix: so the boundary
behind which results are clipped moves in blocks of messages, not one message
per step. Between moves the clipped prefix is byte-identical from request to
request, and a replay of the same history clips the same messages the same way.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ToolReturnPart

#: Trailing messages whose tool output is always left whole: what the model is
#: working from right now.
RECENT_MESSAGES = 12

#: The clipping boundary moves in steps of this many messages, so the clipped
#: prefix -- and the provider's cache of it -- changes only once per block.
BOUNDARY_BLOCK = 8

#: Output at or under this many characters is left alone however old it is.
CLIP_ABOVE_CHARS = 6_000

#: How much of an old result survives: its opening and its end.
_HEAD_CHARS = 2_000
_TAIL_CHARS = 1_000


def _clip_boundary(length: int) -> int:
    """Index before which tool output may be clipped, aligned to a block."""
    stale = length - RECENT_MESSAGES
    if stale <= 0:
        return 0
    return (stale // BOUNDARY_BLOCK) * BOUNDARY_BLOCK


def _as_text(content: object) -> str | None:
    """The result as the model reads it, or None when it is not plain data.

    Anything carrying binary is left for the image detacher, which owns that.
    """
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError, ValueError:
        return None


def clipped_preview(text: str, *, tool_name: str) -> str:
    omitted = len(text) - _HEAD_CHARS - _TAIL_CHARS
    return (
        f"{text[:_HEAD_CHARS]}\n"
        f"[... {omitted} characters of this earlier {tool_name} result are not "
        "shown, to keep the conversation inside the model's context. Call the "
        "tool again if you need them ...]\n"
        f"{text[-_TAIL_CHARS:]}"
    )


def _clipped_part(part: ToolReturnPart) -> ToolReturnPart | None:
    text = _as_text(part.content)
    if text is None or len(text) <= CLIP_ABOVE_CHARS:
        return None
    return dataclasses.replace(
        part, content=clipped_preview(text, tool_name=part.tool_name)
    )


def clip_stale_tool_returns(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """The history with large, old tool results cut to a preview.

    Idempotent: a preview is under the clipping size, so a message already
    clipped is left as it is, and so is the object identity of every message
    nothing was clipped from.
    """
    result = list(messages)
    for index in range(_clip_boundary(len(result))):
        message = result[index]
        parts = getattr(message, "parts", None)
        if not parts:
            continue
        changed = False
        new_parts: list[object] = []
        for part in parts:
            clipped = _clipped_part(part) if isinstance(part, ToolReturnPart) else None
            changed = changed or clipped is not None
            new_parts.append(clipped if clipped is not None else part)
        if changed:
            result[index] = dataclasses.replace(message, parts=new_parts)  # type: ignore[arg-type]
    return result
