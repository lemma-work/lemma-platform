"""A conversation's run events on the pod's live socket, over a real websocket.

The ``conversation`` topic carries what the SSE stream carries, unchanged, to
whoever may read the conversation -- and to nobody else.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import WorkClass, facts
from app.modules.agent.services.realtime import (
    publish_conversation_event,
    token_payload,
)
from app.modules.agent.tests.e2e.lifecycle_helpers import runs
from app.modules.agent.tests.e2e.test_channel_answers_e2e import (
    _answering_conversation,
    _drain,
    _finish_with,
    _finished,
    _member_says,
)
from app.modules.channels.contracts import direct_channel
from app.modules.test_support.e2e.live_socket import LiveClient, live_socket
from app.modules.test_support.e2e.runtime import backend_server as _backend_server

pytestmark = [pytest.mark.e2e]

backend_server = _backend_server


async def _publish_until_heard(
    live: LiveClient, sub: str, conversation_id: UUID, payload: dict[str, object]
) -> dict[str, object]:
    """Publish until it arrives: the socket's lease is taken before
    ``subscribed``, so the first publish is normally the one heard."""
    for _ in range(10):
        await publish_conversation_event(conversation_id, payload)
        if live.events(sub, str(payload["type"])):
            break
        await live.quiet(0.3)
    return await live.event(sub, str(payload["type"]))


async def test_run_tokens_reach_who_may_read_the_conversation(
    scenario, backend_server
) -> None:
    await scenario.create_org_with_pod(name_prefix="Live conversation")
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "live"}
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])
    other = await scenario.create_user("live-conversation-other")
    await scenario.add_user_to_pod(user=other, role="POD_USER")
    run_id = uuid4()

    async with (
        live_socket(
            backend_server, scenario.pod_id, scenario.owner_user["token"]
        ) as owner,
        live_socket(backend_server, scenario.pod_id, other["token"]) as outsider,
    ):
        subscribed = await owner.subscribe(
            "v", "conversation", conversation_id=str(conversation_id)
        )
        assert subscribed["type"] == "subscribed", subscribed
        refused = await outsider.subscribe(
            "v", "conversation", conversation_id=str(conversation_id)
        )
        assert refused["type"] == "error" and refused["code"] == "not_found"
        missing = await owner.subscribe(
            "w", "conversation", conversation_id=str(uuid4())
        )
        assert missing["code"] == "not_found"

        payload = token_payload(run_id, "Hel", kind="text")
        heard = await _publish_until_heard(owner, "v", conversation_id, payload)
        # The SSE stream's own frame, unchanged.
        assert heard == payload
        await outsider.quiet(1.0)
        assert "Hel" not in str(outsider.frames)

        # Run events are not kept: a resumed subscriber refetches.
        await owner.subscribe(
            "again", "conversation", conversation_id=str(conversation_id), since="x"
        )
        resync = await owner.until("resync", "again")
        assert resync["reason"] == "no_replay"


@pytest.mark.drives_work
async def test_an_agent_answering_a_line_is_seen_live_from_start_to_answer(
    scenario, backend_server
) -> None:
    """A member's line in their own channel: the turn it starts says so, its
    tokens reach the member as the answer is written, and the answer arrives
    as its line -- through the real work kinds, over the real socket."""
    await scenario.create_org_with_pod(name_prefix="Live answer")
    pod_id, owner = UUID(scenario.pod_id), UUID(scenario.owner_user["id"])
    async with create_uow_from_session_maker(async_session_maker) as uow:
        direct = await direct_channel(uow, pod_id=pod_id, user_id=owner)
        await uow.commit()

    async with live_socket(
        backend_server, scenario.pod_id, scenario.owner_user["token"]
    ) as live:
        await live.subscribe("d", "channel", channel_id=str(direct.id))
        said = await _member_says(pod_id, owner, "What is on today?")
        await live.event("d", "line.created", message__id=str(said.id))

        await _drain(WorkClass.INTERACTIVE)
        conversation_id = await _answering_conversation(said.id)
        started = await live.event("d", "answering.started", message_id=str(said.id))
        assert started["conversation_id"] == str(conversation_id)
        [run] = await runs(conversation_id)
        assert started["agent_run_id"] == str(run.id)
        # Watching the conversation names the run being written, at once.
        await live.subscribe("v", "conversation", conversation_id=str(conversation_id))
        watched = await live.event("v", "run")
        assert watched["agent_run_id"] == str(run.id)
        assert watched["status"] == "RUNNING"

        for _ in range(10):
            await publish_conversation_event(
                conversation_id, token_payload(run.id, "Two")
            )
            if live.events("d", "line.streaming"):
                break
            await live.quiet(0.3)
        delta = await live.event("d", "line.streaming", message_id=str(said.id))
        assert delta["delta"] == "Two"

        await _finish_with(run.id, "Two meetings.")
        async with create_uow_from_session_maker(async_session_maker) as uow:
            await facts.emit(uow, _finished(run, pod_id=pod_id, user_id=owner))
            await uow.commit()
        await _drain(WorkClass.EVENTS)
        answer = await live.event("d", "line.created", message__text="Two meetings.")
        # In the member's own channel the answer follows the line, unthreaded.
        assert answer["message"]["thread_root_id"] is None
        assert answer["message"]["reply_to_id"] == str(said.id)
        await live.event("d", "answering.finished", message_id=str(said.id))

        # Joining after it finished: the run, and how it ended -- what the
        # SSE stream replays.
        await live.subscribe(
            "after",
            "conversation",
            conversation_id=str(conversation_id),
            agent_run_id=str(run.id),
        )
        over = await live.event("after", "run")
        assert over["status"] == "COMPLETED"
        ending = await live.event("after", "completed")
        assert ending["agent_run_id"] == str(run.id)


async def test_a_member_taken_out_of_the_pod_stops_hearing_their_conversation(
    scenario, backend_server
) -> None:
    """Access is decided again when the pod's grants move: a subscriber who
    may no longer read the conversation is told so and hears nothing more."""
    await scenario.create_org_with_pod(name_prefix="Live conversation access")
    member = await scenario.create_user("live-conversation-leaver")
    added = await scenario.add_user_to_pod(user=member, role="POD_USER")
    headers = {"Authorization": f"Bearer {member['token']}"}
    created = await scenario.async_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={"title": "mine"},
        headers=headers,
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])

    async with live_socket(backend_server, scenario.pod_id, member["token"]) as live:
        subscribed = await live.subscribe(
            "v", "conversation", conversation_id=str(conversation_id)
        )
        assert subscribed["type"] == "subscribed", subscribed
        member_id = added.get("pod_member_id") or added["id"]
        removed = await scenario.owner_client.delete(
            f"/pods/{scenario.pod_id}/members/{member_id}"
        )
        assert removed.status_code == 204, removed.text
        ended = await live.until("unsubscribed", "v", timeout=20)
        assert ended["reason"] == "access_revoked"
        await publish_conversation_event(
            conversation_id, token_payload(uuid4(), "after", kind="text")
        )
        await live.quiet(1.0)
        assert live.events("v", "token") == []
