"""When each tool result came back, written at its head for the model.

A tool result stays in the conversation for as long as the conversation runs:
the rows a table read returned on Monday are still in the history on Friday,
exactly as they were, and nothing in them says they are Monday's. A model
reading them then cannot tell an old answer from a fresh one, so it picks from a
list of industries that has grown since, or quotes a record somebody has edited
-- confidently, because the data is right there.

So every result the model reads opens with the time it came back, written the
way a person's message carries when they sent it (``user_turn_text``):
``<notes>Returned 2026-11-03T14:02:05Z (UTC).</notes>``. The prompt says what to
do with it (``prompts/the_pod.md``).

Cache-safe because it is written from stored data and nothing else. The time is
the result's own ``timestamp``, which the run log stores with it, so a replay
writes the note the run sent; and every result is noted on every request, this
run's included, so a result's bytes do not change when its run becomes an
earlier one. Never relative ("two days ago"): that would change on every
request, and so would everything after it.

Written into the request only, never into the history: a ``before_model_request``
hook after the history processors, not a ``ProcessHistory``. The log keeps what
the tool returned, so a result keeps its type for whatever reads it back, and a
log written before notes existed needs no rewrite -- its results are noted from
the times it already holds. After the processors, too, so a clipped result
(``stale_tool_output``) is noted like a whole one.

Only results that are data: a failed, denied or interrupted call has nothing in
it to go stale, and a result carrying a file is the image detacher's.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from datetime import datetime, timezone

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

from app.modules.agent.infrastructure.providers.pydantic_ai_compat import (
    ModelRequestContext,
)

_NOTED = re.compile(r"^<notes>Returned [^<]*</notes>\s*")


def return_time_note(returned: datetime) -> str:
    """The note a result opens with; a naive time is taken as UTC."""
    if returned.tzinfo is None:
        returned = returned.replace(tzinfo=timezone.utc)
    stamp = returned.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"<notes>Returned {stamp} (UTC).</notes>"


def without_return_time(text: str) -> str:
    """``text`` without the note it opens with, for a reader of the result."""
    return _NOTED.sub("", text, count=1)


def _noted(part: ToolReturnPart) -> ToolReturnPart:
    if part.outcome != "success" or part.files:
        return part
    text = part.model_response_str()
    if _NOTED.match(text):
        return part
    note = return_time_note(part.timestamp)
    return dataclasses.replace(part, content=f"{note}\n\n{text}" if text else note)


def with_return_times(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """``messages`` with every data result noted with when it came back.

    New messages and parts where a note is added, never the ones passed in:
    those are the run's history, which keeps what the tool returned.
    """
    result = list(messages)
    for index, message in enumerate(result):
        if not isinstance(message, ModelRequest):
            continue
        parts = [
            _noted(part) if isinstance(part, ToolReturnPart) else part
            for part in message.parts
        ]
        if any(new is not old for new, old in zip(parts, message.parts)):
            result[index] = dataclasses.replace(message, parts=parts)
    return result


class ToolReturnTimes(AbstractCapability[object]):
    """Note every tool result in the request with when it came back."""

    async def before_model_request(
        self,
        ctx: RunContext[object],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        request_context.messages = with_return_times(request_context.messages)
        return request_context
