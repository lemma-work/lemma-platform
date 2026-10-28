"""A message sent twice is stored once.

Two ways a send arrives twice. The client retries one it never heard back
from, carrying the same client id; or the same words are sent again moments
later by something else -- a second tab, a double tap. Each copy used to be its
own message, answered twice or starting a second run. Against real Postgres and
Redis, because the first is a question about the conversation lock and the
second a Redis window, and a stand-in for either answers whatever it is told.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from starlette import status

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.modules.agent.infrastructure.live import conversation_channel
from app.modules.agent.domain.value_objects import AgentRunStatus, MessageRole
from app.modules.agent.services.send_window import WINDOW_SECONDS, SendWindow
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    messages,
    owner_uow,
    runs,
    seeded_conversation,
    send_message,
)

pytestmark = [pytest.mark.e2e]


async def _append(scenario, seeded: Seeded, body: dict):
    sent = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations/{seeded.conversation_id}/messages/append",
        json=body,
    )
    assert sent.status_code == status.HTTP_200_OK, sent.text
    return sent.json()


async def _stream_resend(scenario, seeded: Seeded, body: dict):
    """Re-send on the streaming route; every data frame until the stream ends.

    Bounded: the failure this guards against is a stream that never ends.
    """
    frames: list[dict] = []
    async with scenario.owner_client.stream(
        "POST",
        f"/pods/{scenario.pod_id}/conversations/{seeded.conversation_id}/messages",
        json=body,
        timeout=30,
    ) as response:
        assert response.status_code == status.HTTP_200_OK, await response.aread()
        async with asyncio.timeout(15):
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                frame = json.loads(line.removeprefix("data: "))
                frames.append(frame)
    return frames


async def _typed(seeded: Seeded, text: str):
    return [
        message
        for message in await messages(seeded.conversation_id)
        if message.role is MessageRole.USER and message.text == text
    ]


async def test_a_retried_send_with_the_same_client_id_is_stored_once(scenario):
    seeded = await seeded_conversation(scenario)
    client_id = f"web-{uuid4()}"

    first = await _append(
        scenario, seeded, {"content": "Draft the memo.", "client_message_id": client_id}
    )
    second = await _append(
        scenario,
        seeded,
        # Reworded on purpose: the id, not the words, says it is the same send.
        {"content": "Draft the memo!", "client_message_id": client_id},
    )

    assert first["started_new_run"] is True and first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert second["started_new_run"] is False
    assert second["agent_run_id"] == first["agent_run_id"]
    assert len(await runs(seeded.conversation_id)) == 1
    (stored,) = await _typed(seeded, "Draft the memo.")
    assert stored.state.client_message_id == client_id
    assert await _typed(seeded, "Draft the memo!") == []


async def test_two_copies_of_a_send_racing_each_other_store_one_message(scenario):
    """Both copies in flight at once, so neither has stored anything when the
    other looks. Only the conversation lock makes "look, then store" one step:
    there is no unique index behind it."""
    seeded = await seeded_conversation(scenario)
    client_id = f"web-{uuid4()}"

    sent = await asyncio.gather(
        *(
            _append(
                scenario,
                seeded,
                # Different words, so the Redis window cannot be what folds
                # them: only the client id says they are one send.
                {"content": f"Race copy {copy}.", "client_message_id": client_id},
            )
            for copy in (1, 2)
        )
    )

    assert sorted(item["deduplicated"] for item in sent) == [False, True]
    assert sent[0]["agent_run_id"] == sent[1]["agent_run_id"]
    stored = [
        message
        for message in await messages(seeded.conversation_id)
        if message.role is MessageRole.USER
        and message.state.client_message_id == client_id
    ]
    assert len(stored) == 1


async def test_a_client_id_sent_only_in_the_metadata_still_counts(scenario):
    """The web app first carried its id in the message's metadata."""
    seeded = await seeded_conversation(scenario)
    body = {"content": "Ping.", "metadata": {"client_message_id": f"web-{uuid4()}"}}

    first = await _append(scenario, seeded, body)
    second = await _append(scenario, seeded, {**body, "content": "Ping again."})

    assert second["deduplicated"] is True
    assert second["agent_run_id"] == first["agent_run_id"]
    assert await _typed(seeded, "Ping again.") == []


async def test_the_same_words_twice_in_a_moment_are_one_message(scenario):
    seeded = await seeded_conversation(scenario)

    first = await _append(scenario, seeded, {"content": "Summarise the inbox."})
    second = await _append(scenario, seeded, {"content": "Summarise the inbox."})
    other = await _append(scenario, seeded, {"content": "And the calendar."})

    assert second["deduplicated"] is True
    assert second["agent_run_id"] == first["agent_run_id"]
    assert len(await _typed(seeded, "Summarise the inbox.")) == 1
    # Different words are a different message, joining the run in flight.
    assert other["deduplicated"] is False
    assert len(await _typed(seeded, "And the calendar.")) == 1
    assert WINDOW_SECONDS == 15


async def test_a_redis_outage_never_stops_a_send(scenario):
    """The window fails open: nobody is kept from sending by a cache."""
    seeded = await seeded_conversation(scenario)
    unreachable = RedisJsonCache(
        redis_url="redis://127.0.0.1:1/0",
        key_prefix="agent:send-window",
        ttl_seconds=15,
    )

    async with owner_uow(seeded) as uow:
        result = await send_message(
            uow, send_window=SendWindow(uow.session, unreachable)
        ).send(
            conversation_id=seeded.conversation_id,
            user_id=seeded.user_id,
            content="Still works?",
            pod_id=seeded.pod_id,
            typed_in_lemma=True,
        )

    assert result.started_new_run is True
    assert len(await _typed(seeded, "Still works?")) == 1


async def test_a_resend_after_its_run_finished_streams_the_ending_and_closes(
    scenario,
):
    """The streaming route used to subscribe for a run that had already ended,
    so the re-send waited forever on keepalives for an ending already sent."""
    seeded = await seeded_conversation(scenario)
    client_id = f"web-{uuid4()}"
    first = await _append(
        scenario,
        seeded,
        {"content": "File the report.", "client_message_id": client_id},
    )
    await finish(UUID(first["agent_run_id"]), status=AgentRunStatus.COMPLETED)

    frames = await _stream_resend(
        scenario,
        seeded,
        {"content": "File the report.", "client_message_id": client_id},
    )

    assert [frame["type"] for frame in frames] == ["status", "completed"]
    assert frames[0]["data"] == {"started_new_run": False, "deduplicated": True}
    assert {frame["agent_run_id"] for frame in frames} == {first["agent_run_id"]}


@pytest.mark.drives_work
async def test_a_resend_while_its_run_is_working_follows_the_run(scenario):
    """Held: the session worker must not pick up and stream the turn, whose
    only frames here are the ones the test publishes."""
    seeded = await seeded_conversation(scenario)
    client_id = f"web-{uuid4()}"
    first = await _append(
        scenario, seeded, {"content": "Draft it.", "client_message_id": client_id}
    )
    run_id = first["agent_run_id"]
    assert (await runs(seeded.conversation_id))[0].status not in {
        "COMPLETED",
        "FAILED",
        "STOPPED",
    }
    channel = await get_channel_service()

    async def run_ends() -> None:
        # After the re-send is listening: the ending it must still hear.
        await asyncio.sleep(0.5)
        await channel.publish(
            conversation_channel(seeded.conversation_id),
            {
                "type": "completed",
                "agent_run_id": run_id,
                "data": {"status": "COMPLETED"},
            },
        )

    publishing = asyncio.create_task(run_ends())
    frames = await _stream_resend(
        scenario, seeded, {"content": "Draft it.", "client_message_id": client_id}
    )
    await publishing

    # The first send started a real run, which may stream while the re-send
    # follows it. Those tokens are that run's, and seeing them is what
    # following means -- requiring none failed whenever the run spoke first.
    assert frames[0]["type"] == "status"
    assert frames[0]["data"] == {"started_new_run": False, "deduplicated": True}
    assert frames[-1]["type"] == "completed"
    assert all(
        frame["type"] == "token" and frame.get("agent_run_id") == run_id
        for frame in frames[1:-1]
    ), frames
