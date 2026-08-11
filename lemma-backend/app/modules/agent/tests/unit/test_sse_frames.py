"""The seam between the realtime channel and the SSE wire.

Everything an agent run tells the UI passes through these two functions, and
neither had a test. Both defects below are the same shape: a frame that is
produced correctly, published correctly, and then silently dropped or reshaped
on its way out — which the user experiences as a chat pane that does nothing
until the page is reloaded.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.modules.agent.api.controllers.shared import (
    encode_stream_chunk,
    iter_subscription,
)

pytestmark = pytest.mark.unit


async def _frames(messages, agent_run_id):
    async def iterator():
        for message in messages:
            yield message

    return [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in iter_subscription(iterator(), agent_run_id)
    ]


class TestSubscriptionFilter:
    @pytest.mark.asyncio
    async def test_another_runs_events_are_not_forwarded(self) -> None:
        """Two runs share one conversation channel; a client watches one."""
        mine, theirs = uuid4(), uuid4()
        frames = await _frames(
            [
                {"type": "token", "agent_run_id": str(theirs), "data": "not mine"},
                {"type": "token", "agent_run_id": str(mine), "data": "mine"},
            ],
            mine,
        )
        assert [f["data"] for f in frames] == ["mine"]

    @pytest.mark.asyncio
    async def test_a_conversation_wide_event_still_reaches_the_client(self) -> None:
        """A payload with no run id belongs to the conversation, not a run.

        The generated title is the one in production. It was compared against
        the run id anyway — `None != "<uuid>"` — so it was dropped for exactly
        the clients that were streaming, which is all of them.
        """
        run_id = uuid4()
        frames = await _frames(
            [{"type": "title", "data": {"title": "Ashwin's records"}}], run_id
        )
        assert [f["type"] for f in frames] == ["title"]

    @pytest.mark.asyncio
    async def test_the_stream_stops_at_a_terminal_event(self) -> None:
        run_id = uuid4()
        frames = await _frames(
            [
                {"type": "token", "agent_run_id": str(run_id), "data": "hi"},
                {"type": "completed", "agent_run_id": str(run_id), "data": {}},
                {"type": "token", "agent_run_id": str(run_id), "data": "after the end"},
            ],
            run_id,
        )
        assert [f["type"] for f in frames] == ["token", "completed"]


class TestFrameEncoding:
    def test_a_token_carries_its_kind_and_no_run_id(self) -> None:
        """Matches what the SDK parses: `data` is the string, `kind` routes it
        to the text, thinking or tool bubble."""
        frame = json.loads(
            encode_stream_chunk(
                event_type="token", data="wickets", agent_run_id=uuid4(), kind="text"
            ).removeprefix("data: ")
        )
        assert frame == {"type": "token", "data": "wickets", "kind": "text"}

    def test_every_other_frame_carries_the_run_id(self) -> None:
        run_id = uuid4()
        frame = json.loads(
            encode_stream_chunk(
                event_type="completed", data={"status": "COMPLETED"}, agent_run_id=run_id
            ).removeprefix("data: ")
        )
        assert frame["agent_run_id"] == str(run_id)

    def test_a_frame_is_one_line_terminated_by_a_blank_line(self) -> None:
        """SSE framing: a newline inside `data:` would split one event into two,
        and the client would fail to parse both halves."""
        chunk = encode_stream_chunk(
            event_type="token",
            data="first line\nsecond line",
            agent_run_id=None,
            kind="text",
        )
        assert chunk.endswith("\n\n")
        assert chunk.count("\n") == 2, chunk
