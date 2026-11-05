"""A message edited or deleted on Slack is edited or deleted on the timeline.

The line the bot heard is the record of what was said, so Slack's
``message_changed`` changes what it says -- and whom it names -- and
``message_deleted`` empties it where it stands, keeping its number. Only the
line heard at that place: an event naming another workspace's channel, or a
message the pod never heard, changes nothing here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.line_changes import apply_line_changes
from app.modules.agent_surfaces.tests.e2e.helpers import _seed_external_user
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
from app.modules.channels.infrastructure.models import ChannelMessageModel

pytestmark = pytest.mark.e2e


async def _staged(client, db_session, *, pod_id, user, fake_slack) -> None:
    await _slack_surface(
        client, db_session, pod_id=pod_id, user_id=user["id"], fake_slack=fake_slack
    )
    await _seed_external_user(
        db_session,
        platform="SLACK",
        external_user_id=slack_payloads.SENDER_ID,
        tenant_id=slack_payloads.TEAM_ID,
        resolved_user_id=UUID(user["id"]),
    )


def _changed(event: dict[str, Any], *, team: str = slack_payloads.TEAM_ID):
    payload = slack_payloads.envelope()
    payload["event"] = {"type": "message", "channel": SUPPORT_CHANNEL, **event}
    payload["team_id"] = team
    return SurfacePlatformWebhookIngress(source="slack", payload=payload, headers={})


async def _apply(db_session: AsyncSession, request) -> int:
    uow = SqlAlchemyUnitOfWork(db_session)
    changed = await apply_line_changes(
        uow, request, adapters=SurfacePlatformAdapterRegistry()
    )
    await uow.commit()
    return changed


async def _line(db_session: AsyncSession, ts: str) -> ChannelMessageModel:
    db_session.expire_all()
    row = await db_session.scalar(
        select(ChannelMessageModel).where(
            ChannelMessageModel.external_message_id == ts,
            ChannelMessageModel.source_id.is_not(None),
        )
    )
    assert row is not None, "the message was never heard"
    return row


async def test_a_slack_edit_changes_the_heard_line_and_a_deletion_empties_it(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    await _staged(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user=fixed_test_user,
        fake_slack=fake_slack,
    )
    ts = _ts()
    await _hear(db_session, "slack", _slack_in_channel("we ship on Friday", ts=ts))
    heard = await _line(db_session, ts)
    seq = heard.seq

    edited = _changed(
        {
            "subtype": "message_changed",
            "message": {
                "type": "message",
                "user": slack_payloads.SENDER_ID,
                "text": "we ship on Monday",
                "ts": ts,
                "edited": {"user": slack_payloads.SENDER_ID, "ts": _ts()},
            },
            "previous_message": {"user": slack_payloads.SENDER_ID, "ts": ts},
        }
    )
    assert await _apply(db_session, edited) == 1
    line = await _line(db_session, ts)
    assert line.body == "we ship on Monday"
    assert line.edited_at is not None
    # The same edit again -- a redelivery -- says nothing new.
    assert await _apply(db_session, edited) == 0

    deleted = _changed(
        {
            "subtype": "message_deleted",
            "deleted_ts": ts,
            "previous_message": {"user": slack_payloads.SENDER_ID, "ts": ts},
        }
    )
    assert await _apply(db_session, deleted) == 1
    line = await _line(db_session, ts)
    assert line.deleted_at is not None and line.body == ""
    assert line.seq == seq, "a deleted line keeps its place"


async def test_a_change_from_another_workspace_or_of_an_unheard_message_is_ignored(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """A channel id is its workspace's: the same id and ts in another team's
    event is another message, and is not this pod's to change."""
    await _staged(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user=fixed_test_user,
        fake_slack=fake_slack,
    )
    ts = _ts()
    await _hear(db_session, "slack", _slack_in_channel("the budget is 40k", ts=ts))

    elsewhere = _changed(
        {
            "subtype": "message_changed",
            "message": {"user": "U0OTHER", "text": "the budget is 0", "ts": ts},
        },
        team="T0OTHER99",
    )
    assert await _apply(db_session, elsewhere) == 0
    never_heard = _changed({"subtype": "message_deleted", "deleted_ts": _ts()})
    assert await _apply(db_session, never_heard) == 0

    line = await _line(db_session, ts)
    assert line.body == "the budget is 40k" and line.deleted_at is None
