"""What other modules tell channels' live subscribers directly.

Everything durable reaches the socket by itself: a line, an edit, a question
closing or a read moves the channel's change counter, and its hubs read it.
What is not stored has to be said -- that an agent has started answering a
line, the stream its answer is being written on, and that it is done. The
module running the agent says so here, after the commit that started the run.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live.signals import publish_signal, signal_after_commit


def announce_answering(
    *,
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    agent_id: UUID,
    for_user_id: UUID,
    state: Literal["started", "finished"],
    conversation_id: UUID | None = None,
    agent_run_id: UUID | None = None,
    stream: tuple[str, str] | None = None,
    after_commit_of: AsyncSession | None = None,
) -> None:
    """``agent_id`` started (or finished) answering line ``message_id``, for
    the member ``for_user_id`` -- the one reader its answer is not withheld
    from. ``stream``: a live stream (source, key) the answer's deltas are
    written on, which the channel's hubs relay to that reader as
    ``line.streaming``. Best effort: a missed signal costs the indicator,
    never the answer, which arrives as its line. ``after_commit_of``: say it
    once that session's transaction commits (the answer's line with it), not
    now."""
    signal: dict[str, JsonValue] = {
        "k": "answering",
        "c": str(channel_id),
        "m": str(message_id),
        "a": str(agent_id),
        "for": str(for_user_id),
        "state": state,
    }
    if conversation_id is not None:
        signal["conv"] = str(conversation_id)
    if agent_run_id is not None:
        signal["run"] = str(agent_run_id)
    if stream is not None:
        signal["stream"] = {"source": stream[0], "key": stream[1]}
    if after_commit_of is not None:
        signal_after_commit(after_commit_of, pod_id, signal)
    else:
        publish_signal(pod_id, signal)


__all__ = ["announce_answering"]
