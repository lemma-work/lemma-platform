"""Detaching images the model has already had several turns to read."""

from __future__ import annotations

import dataclasses
from typing import Any


#: How many trailing messages keep their images live. A model that has just
#: looked at a screenshot needs it; one twenty steps past it does not.
IMAGE_RETENTION_MESSAGES = 8

STALE_IMAGE_MARKER = (
    "[an image was shown here earlier in this run; it is no longer attached -- "
    "open it again if you need to look at it]"
)


def _is_binary(value: object) -> bool:
    return isinstance(getattr(value, "data", None), (bytes, bytearray, memoryview))


def _without_images(content: object) -> tuple[object, bool]:
    """`content` with binary swapped for a marker, and whether anything changed."""
    if _is_binary(content):
        return STALE_IMAGE_MARKER, True
    if isinstance(content, (list, tuple)):
        changed = False
        items: list[object] = []
        for item in content:
            replaced, item_changed = _without_images(item)
            changed = changed or item_changed
            items.append(replaced)
        return (type(content)(items) if changed else content), changed
    return content, False


def strip_stale_images(messages: list[Any]) -> list[Any]:
    """Detach images the model has already had several turns to read.

    pydantic-ai keeps a tool's image content in the run's in-memory history, so
    every image is re-uploaded on *every* model request for the rest of the run.
    A ten-page document viewed at step 2 of a fifteen-step run is sent fourteen
    more times, and the model reads it once.

    Only the trailing window keeps its pixels. Older ones become a marker saying
    an image was there, which is both cheaper and more honest than a silent
    disappearance. Idempotent: a message already stripped has no binary left.
    """
    if len(messages) <= IMAGE_RETENTION_MESSAGES:
        return messages
    cutoff = len(messages) - IMAGE_RETENTION_MESSAGES
    result = list(messages)
    for index in range(cutoff):
        message = result[index]
        parts = getattr(message, "parts", None)
        if not parts:
            continue
        new_parts: list[Any] = []
        changed = False
        for part in parts:
            if not hasattr(part, "content"):
                new_parts.append(part)
                continue
            content, part_changed = _without_images(part.content)
            if part_changed:
                changed = True
                new_parts.append(_replaced(part, content=content))
            else:
                new_parts.append(part)
        if changed:
            result[index] = _replaced(message, parts=new_parts)
    return result


def _replaced(obj: Any, **changes: Any) -> Any:
    """A copy of a pydantic-ai message or part with fields changed.

    They are dataclasses in this version and pydantic models in others, and the
    two have no copy method in common. Getting this wrong raises inside a
    history processor, which runs on every model request.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.replace(obj, **changes)
    return obj.model_copy(update=changes)
