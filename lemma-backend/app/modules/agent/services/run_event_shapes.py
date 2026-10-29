"""Shapes of harness events the pump reads: token lanes, split messages, usage."""

from __future__ import annotations

from collections.abc import Sequence

from app.core.text.thinking_tags import RESET as SPLITTER_RESET
from app.modules.agent.domain.approval_message import with_approval_message
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunUsage,
    MessageDraft,
)
from app.modules.agent.services.run_message_writer import split_reasoning_drafts

#: The retry path's "discard what you have" signal, sent as a token with no
#: payload (`PydanticAIHarness._stream_reset_event`).
STREAM_RESET_KIND = "stream_reset"


def _as_token_lanes(segments: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """The splitter's segments as the token lanes clients read.

    Its ``reset`` -- what it released of this stream was not what it said --
    is the lane a retry already uses to clear the bubble, and every client of
    the stream (the web chat, a visitor's page, a channel) acts on it.
    """
    return [
        (STREAM_RESET_KIND if kind == SPLITTER_RESET else kind, chunk)
        for kind, chunk in segments
    ]


def _split_message_event(event: AgentEvent) -> list[AgentEvent]:
    """One message event, split into the messages it should actually become.

    Identity for everything that is not an assistant message carrying reasoning
    in its text, or an approval the agent asked for -- which is preceded by
    what it tells the person about it (``domain/approval_message``) -- so all
    but a handful of events; the list is returned unchanged rather than rebuilt
    so the common path allocates nothing.
    """
    if event.type is not AgentEventType.MESSAGE or not isinstance(
        event.data, MessageDraft
    ):
        return [event]
    drafts = [
        split
        for draft in with_approval_message(event.data)
        for split in split_reasoning_drafts(draft)
    ]
    if len(drafts) == 1 and drafts[0] is event.data:
        return [event]
    return [event.model_copy(update={"data": draft}) for draft in drafts]


def _usage_from_event(event: AgentEvent) -> AgentRunUsage | None:
    """The usage an event carries, however the harness chose to shape it."""
    if isinstance(event.data, AgentRunUsage):
        return event.data
    if isinstance(event.data, dict):
        return AgentRunUsage.model_validate(event.data)
    return None
