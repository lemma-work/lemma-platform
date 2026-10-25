"""One tool call per id in a response.

A provider can return the same call twice under one id (Fireworks did, on
every call of a 1,399-call loop). pydantic-ai refuses such a response outright
-- the run fails with "tool calls must have unique tool_call_id values" -- and
a history storing both pairs one return with two calls. A response is acted on
once per id: the first call stands, later ones under its id are dropped.
Applied twice, with this one rule -- to the response pydantic-ai executes, and
to the messages the run stores.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence


def first_per_call_id[T](
    items: Sequence[T], call_id: Callable[[T], str | None]
) -> tuple[list[T], list[T]]:
    """``items`` with any repeat of an earlier call id dropped; and the dropped.

    Items with no call id (text, thinking) are always kept.
    """
    seen: set[str] = set()
    kept: list[T] = []
    dropped: list[T] = []
    for item in items:
        identity = call_id(item)
        if identity is None:
            kept.append(item)
        elif identity in seen:
            dropped.append(item)
        else:
            seen.add(identity)
            kept.append(item)
    return kept, dropped


__all__ = ["first_per_call_id"]
