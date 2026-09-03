"""A message frame with no run says so, rather than saying the string "None".

`message_payload` took a `UUID` and stringified it unconditionally. A message
can exist without a run -- a tool return closed by the MCP bridge outside one, a
superseded return replayed from an older turn -- and `str(None)` put the four
characters `None` on the wire where a run id belongs. A consumer that checks the
field is a string, as the TypeScript SDK's event parser does, would take that
for a real id.

The frame is routed by conversation, so nothing broke visibly; it was wrong
data, not a broken stream, which is why it survived. Everything else in the
module is genuinely run-scoped, so the widening stops here.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.modules.agent.services.realtime import (
    conversation_channel,
    message_payload,
    status_payload,
    token_payload,
)

pytestmark = pytest.mark.unit


def test_a_message_with_no_run_carries_a_null_run_id() -> None:
    frame = message_payload(None, {"id": "m1"})

    assert frame["agent_run_id"] is None
    assert frame["type"] == "message"
    assert frame["data"] == {"id": "m1"}


def test_the_null_run_id_survives_serialization_as_json_null() -> None:
    """What a subscriber actually receives. `"None"` was the bug."""
    encoded = json.loads(json.dumps(message_payload(None, {"id": "m1"})))

    assert encoded["agent_run_id"] is None
    assert encoded["agent_run_id"] != "None"


def test_a_message_belonging_to_a_run_still_names_it() -> None:
    agent_run_id = uuid4()

    frame = message_payload(agent_run_id, {"id": "m1"})

    assert frame["agent_run_id"] == str(agent_run_id)


def test_the_run_scoped_frames_are_unchanged() -> None:
    """The widening is confined to the one frame that needed it."""
    agent_run_id = uuid4()

    assert status_payload(agent_run_id, {"status": "RUNNING"})["agent_run_id"] == str(
        agent_run_id
    )
    assert token_payload(agent_run_id, "hi")["agent_run_id"] == str(agent_run_id)


def test_frames_are_routed_by_conversation_not_by_run() -> None:
    """Why a runless message still belongs on the stream at all."""
    conversation_id = uuid4()

    assert conversation_channel(conversation_id) == (
        f"agent:conversation:{conversation_id}"
    )


class TestAChannelOutageIsVisible:
    """Every token, message and terminal frame a watching client sees goes
    through `publish_conversation_event`. When the channel is down the symptom
    is "the agent never answers" while runs complete normally in the database --
    and at `logger.debug` production (LOG_LEVEL=INFO) had nothing at all to
    distinguish that from a quiet day."""

    @pytest.mark.asyncio
    async def test_repeated_publish_failures_are_reported_once(self, caplog) -> None:
        from app.modules.agent.services import realtime

        class _DeadChannel:
            async def publish(self, _channel, _payload) -> None:
                raise ConnectionError("redis is gone")

        realtime._publish_incident.record_success()
        with caplog.at_level("WARNING"):
            for _ in range(5):
                await realtime.publish_conversation_event(
                    uuid4(), {"type": "token"}, channel_service=_DeadChannel()
                )

        assert caplog.text.count("dependency.degraded") == 1, caplog.text
        assert "agent.realtime.publish" in caplog.text
        realtime._publish_incident.record_success()
