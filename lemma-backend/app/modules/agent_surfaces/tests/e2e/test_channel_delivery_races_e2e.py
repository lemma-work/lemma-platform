"""E2E for two senders on one delivery row: it is sent once.

A delivery row is claimed by a conditional ``UPDATE`` and held for
``CLAIM_LEASE``; nothing else stops two workers -- a redelivered job, the
sweeper, a retry -- from sending the same line twice. These race real
senders over separate database sessions against Postgres, with only the
platform's half played by hand: a push that counts its calls and can be held
mid-send.

* Senders started together on one row call the platform once.
* A sender parked inside the platform call keeps the row: a second one finds
  it claimed and leaves it.
* A sender whose lease lapses mid-send is not sent again by whoever finds the
  lapsed claim; the receipt it brings back when it does return is the row's.

What a sender that *died* leaves behind is in
``test_run_answer_deliveries_e2e.py``
(``test_a_send_whose_sender_died_unrecorded_is_never_sent_again``).
"""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.composition import build_surface_egress
from app.modules.agent_surfaces.services.channel_deliveries import ChannelDeliveries
from app.modules.agent_surfaces.services.member_pushes import MemberPushes, Push
from app.modules.agent_surfaces.services.notification_delivery import Delivered
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
)
from app.modules.agent_surfaces.tests.e2e.test_channel_deliveries_e2e import (
    _colleague,
)
from app.modules.channels.contracts import (
    DeliveryStatus,
    NewLine,
    Principal,
    Sender,
    direct_channel,
    post,
)
from app.modules.channels.infrastructure.deliveries import CLAIM_LEASE
from app.modules.channels.infrastructure.models import ChannelMessageDeliveryModel

# These claim deliveries in-process; the session worker taking the same rows
# would be a third sender.
pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

#: A platform that does not dedupe a resend: a second call is a second message.
PLATFORM = "TELEGRAM"

#: Long enough for a sender that was going to touch the row to have done so.
SETTLE = 0.3


class _Platform:
    """The member's phone: counts what reaches it, and can hold a send open."""

    def __init__(self, *, held: bool = False) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not held:
            self.release.set()

    def pushes_for(self, _uow: SqlAlchemyUnitOfWork) -> MemberPushes:
        # Duck-typed: the sender only calls ``push``.
        return cast(MemberPushes, self)

    async def push(self, push: Push) -> Delivered:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return Delivered(platform=PLATFORM, external_message_id=f"tg-{self.calls}")


def _senders(platform: _Platform) -> ChannelDeliveries:
    return ChannelDeliveries(
        SessionUnitOfWorkFactory(async_session_maker),
        egress_for=build_surface_egress,
        pushes_for=platform.pushes_for,
    )


async def _a_push(
    client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    *,
    org_id: str,
    pod_id: str,
) -> tuple[UUID, UUID]:
    """An agent's line in a newly added member's own channel, and its push.

    A fresh member: pushes to one person go in order, so another test's
    leftover push to a shared user would hold this one back.
    """
    member = await _colleague(client, async_client, org_id=org_id, pod_id=pod_id)
    member_id = UUID(member["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    channel = await direct_channel(uow, pod_id=UUID(pod_id), user_id=member_id)
    line = await post(
        uow,
        channel_id=channel.id,
        line=NewLine(
            sender=Sender.agent(UUID(pod_id), on_behalf_of=Principal.member(member_id)),
            text="Your report is ready.",
        ),
    )
    await uow.commit()
    assert line is not None
    [push] = await deliveries_of_line(line.id)
    assert push.recipient_user_id == member_id
    assert push.status is DeliveryStatus.PENDING
    return line.id, push.id


async def _lease_lapses(delivery_id: UUID) -> None:
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelMessageDeliveryModel)
            .where(ChannelMessageDeliveryModel.id == delivery_id)
            .values(next_attempt_at=ChannelMessageDeliveryModel.claimed_at)
        )
        await session.commit()


@pytest.fixture
async def a_push(
    authenticated_client, async_client, db_session, test_pod
) -> tuple[UUID, UUID]:
    return await _a_push(
        authenticated_client,
        async_client,
        db_session,
        org_id=test_pod["organization_id"],
        pod_id=test_pod["id"],
    )


async def test_senders_started_together_on_one_row_send_it_once(a_push):
    line_id, delivery_id = a_push
    platform = _Platform()
    senders = _senders(platform)

    await asyncio.gather(*(senders.deliver(delivery_id) for _ in range(4)))

    assert platform.calls == 1
    [row] = await deliveries_of_line(line_id)
    assert row.status is DeliveryStatus.SENT, row.reason
    assert row.attempts == 1
    assert row.external_message_id == "tg-1"


async def test_a_row_being_sent_is_left_alone_by_a_second_sender(a_push):
    line_id, delivery_id = a_push
    platform = _Platform(held=True)
    senders = _senders(platform)

    first = asyncio.create_task(senders.deliver(delivery_id))
    await asyncio.wait_for(platform.entered.wait(), timeout=10)
    # A redelivered job for the same row, while the first is mid-send.
    await senders.deliver(delivery_id)
    assert platform.calls == 1
    [meanwhile] = await deliveries_of_line(line_id)
    assert meanwhile.status is DeliveryStatus.PENDING
    assert meanwhile.attempts == 1

    platform.release.set()
    await asyncio.wait_for(first, timeout=10)

    assert platform.calls == 1
    [row] = await deliveries_of_line(line_id)
    assert row.status is DeliveryStatus.SENT, row.reason
    assert row.attempts == 1
    assert row.external_message_id == "tg-1"


async def test_a_lease_that_lapses_mid_send_is_not_sent_again(a_push):
    """The first sender is still waiting on the platform when its lease runs
    out. Whoever finds the lapsed claim cannot tell whether it arrived, and a
    platform that does not dedupe would show it twice: it is not sent again.
    The first sender's receipt, when it comes, is what the row says."""
    line_id, delivery_id = a_push
    platform = _Platform(held=True)
    senders = _senders(platform)
    assert CLAIM_LEASE.total_seconds() > 0

    first = asyncio.create_task(senders.deliver(delivery_id))
    await asyncio.wait_for(platform.entered.wait(), timeout=10)
    await _lease_lapses(delivery_id)

    # The sweeper's job for the lapsed row.
    await senders.deliver(delivery_id)
    await asyncio.sleep(SETTLE)
    assert platform.calls == 1, "a lapsed claim was sent again"

    platform.release.set()
    await asyncio.wait_for(first, timeout=10)

    assert platform.calls == 1
    [row] = await deliveries_of_line(line_id)
    assert row.status is DeliveryStatus.SENT, row.reason
    assert row.external_message_id == "tg-1"
