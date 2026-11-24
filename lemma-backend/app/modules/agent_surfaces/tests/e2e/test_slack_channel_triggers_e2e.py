"""A message in a connected Slack channel starts the schedule watching it.

The Slack connector advertised three triggers -- a channel message, a thread
reply, an app mention -- that nothing routed: a schedule made from one was
refused, or stored and never fired. Slack sends every event to the pod's bot,
so that is where they are heard. Each test is one promise of that path,
asserted at the schedule's runs rather than at a mock: it fires, once however
often Slack retries, and only in a pod that connected the channel.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    slack as slack_payloads,
)
from app.modules.agent_surfaces.tests.e2e.test_workspace_channels_e2e import (
    SUPPORT_CHANNEL,
    _hear,
    _slack_in_channel,
    _slack_surface,
    _ts,
)
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_pod,
    _seed_connector_trigger,
)

pytestmark = pytest.mark.e2e

TRIGGER_ID = "slack:http:slack_channel_message"


async def _runs_for(db_session: AsyncSession, schedule_id: str) -> list[ScheduleRun]:
    db_session.expire_all()
    result = await db_session.execute(
        select(ScheduleRun).where(ScheduleRun.schedule_id == UUID(str(schedule_id)))
    )
    return list(result.scalars().all())


async def _watching_the_channel(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod_id: str,
    agent: dict,
    surface: dict,
) -> dict[str, Any]:
    """A schedule on the channel's messages, made from the catalog's trigger
    through the pod's Slack connector -- the way a plugin makes one."""
    await _seed_connector_trigger(
        db_session,
        connector_id="slack",
        trigger_id=TRIGGER_ID,
        event_type="message",
    )
    response = await client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "WEBHOOK",
            "agent_name": agent["name"],
            "instruction": "Add the message to the incident timeline.",
            "pod_connector": surface["pod_connector"],
            "connector_trigger_id": TRIGGER_ID,
            "config": {
                "team_id": slack_payloads.TEAM_ID,
                "channel_id": SUPPORT_CHANNEL,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_channel_message_fires_its_schedule_once_however_often_slack_sends_it(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    pod_id = test_pod["id"]
    agent, surface = await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    schedule = await _watching_the_channel(
        authenticated_client, db_session, pod_id=pod_id, agent=agent, surface=surface
    )
    assert schedule["config"]["source"] == "slack"
    assert schedule["config"]["event"] == "message"

    said = _slack_in_channel("failover to the replica started", ts=_ts())
    await _hear(db_session, "slack", said)
    # Slack retries a delivery it saw no answer to, with the same body.
    await _hear(db_session, "slack", said)

    (run,) = await _runs_for(db_session, schedule["id"])
    assert run.payload["event"]["text"] == "failover to the replica started"
    assert run.payload["event"]["channel"] == SUPPORT_CHANNEL
    assert "token" not in run.payload

    # A thread reply in the channel is the channel's too.
    await _hear(
        db_session,
        "slack",
        _slack_in_channel(
            "replica is healthy", ts=_ts(), thread_ts=said["event"]["ts"]
        ),
    )
    assert len(await _runs_for(db_session, schedule["id"])) == 2


async def test_a_pod_that_never_connected_the_channel_does_not_hear_it(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    fake_slack,
):
    """Its schedule names the very same workspace and channel -- a channel id
    is anybody's to type -- and still never runs on that channel's messages."""
    pod_id = test_pod["id"]
    agent, surface = await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    ours = await _watching_the_channel(
        authenticated_client, db_session, pod_id=pod_id, agent=agent, surface=surface
    )
    other_pod = await _create_pod(authenticated_client, fixed_test_org["id"])
    theirs = uuid4()
    foreign = Schedule(
        id=theirs,
        user_id=UUID(fixed_test_user["id"]),
        pod_id=UUID(other_pod),
        schedule_type=ScheduleType.WEBHOOK,
        instruction="Copy it somewhere else.",
        config={
            "source": "slack",
            "event": "message",
            "team_id": slack_payloads.TEAM_ID,
            "channel_id": SUPPORT_CHANNEL,
        },
        is_active=True,
    )
    db_session.add(foreign)
    await db_session.commit()

    await _hear(db_session, "slack", _slack_in_channel("rolling back", ts=_ts()))

    assert len(await _runs_for(db_session, ours["id"])) == 1
    assert await _runs_for(db_session, str(theirs)) == []
