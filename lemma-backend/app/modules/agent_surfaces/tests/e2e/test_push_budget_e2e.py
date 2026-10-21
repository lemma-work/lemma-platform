"""E2E for what the hourly push budget holds back, and what it never does.

A member who has had ``PushBudget``'s worth of pushes from a pod this hour is
not pushed again: the line waits in Lemma, and goes with their next push from
that channel, which says how many lines it stands for. What names them or
asks them something is never held, nor held back while they have the app
open; anything else is held a few minutes while they do, and is not pushed
once they have read it. Every try of a push is recorded. A plain line in a group is not
worth an email. Against a real database, real Redis and the fake mail server.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.redis.client import get_redis
from app.core.live.presence import mark_online
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)
from app.modules.agent_surfaces.services.notification_rate_limiter import (
    DEFAULT_MAX_PER_RECIPIENT_PER_HOUR,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
    member_writes,
)
from app.modules.agent_surfaces.tests.e2e.test_channel_deliveries_e2e import (
    _colleague,
    _deliver,
    _due_now,
    _resend_bot,
    _resend_unreachable,
    _row,
    _tell,
)
from app.modules.channels.contracts import DeliveryReasonCode, DeliveryStatus
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.channels.infrastructure.models import (
    ChannelDeliveryAttemptModel,
    ChannelMemberModel,
)
from app.modules.channels.tests.e2e.push_support import (
    run_pushes,
    waiting,
    window_ends_now,
)
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


def _budget_key(pod_id: str, user_id: str) -> str:
    return f"notify:rate:{pod_id}:{user_id}"


async def _spend_their_budget(pod_id: str, user_id: str) -> None:
    """They have had the hour's pushes from this pod already."""
    await get_redis().set(
        _budget_key(pod_id, user_id), DEFAULT_MAX_PER_RECIPIENT_PER_HOUR, ex=3600
    )


async def _charged(pod_id: str, user_id: str) -> int:
    return int(await get_redis().get(_budget_key(pod_id, user_id)) or 0)


async def _a_group_with(
    client: AsyncClient, pod_id: str, colleague: dict, *, notify: str = "ALL"
) -> str:
    created = await client.post(
        f"/pods/{pod_id}/channels",
        json={"title": "Launch", "members": [colleague["id"]]},
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == UUID(channel_id),
                ChannelMemberModel.user_id == UUID(colleague["id"]),
            )
            .values(notify=notify)
        )
        await session.commit()
    return channel_id


async def _pushes_to(line_id: str, user_id: str) -> list:
    return [
        delivery
        for delivery in await deliveries_of_line(line_id)
        if delivery.recipient_user_id == UUID(user_id)
    ]


async def _push_to(line_id: str, user_id: str):
    [push] = await _pushes_to(line_id, user_id)
    return push


async def _tries(delivery_id: UUID) -> list[tuple[str | None, str | None]]:
    a = ChannelDeliveryAttemptModel
    async with async_session_maker() as session:
        rows = await session.execute(
            select(a.outcome, a.error_code)
            .where(a.delivery_id == delivery_id)
            .order_by(a.n)
        )
    return [(outcome, code) for outcome, code in rows]


@pytest.fixture
async def setting(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
):
    """A pod with a mailbox, and a colleague in it to be pushed to."""
    pod_id = test_pod["id"]
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        api_base=fake_resend.api_base,
    )
    colleague = await _colleague(
        authenticated_client, async_client, org_id=fixed_test_org["id"], pod_id=pod_id
    )
    return pod_id, colleague


async def test_a_line_naming_them_is_pushed_however_many_they_had(
    authenticated_client, setting, message_store
):
    """The budget keeps room for exactly this: a line that names them goes,
    even after the hour's plain pushes are spent -- by email, from a group."""
    pod_id, colleague = setting
    group = await _a_group_with(authenticated_client, pod_id, colleague)
    await _spend_their_budget(pod_id, colleague["id"])

    named = await member_writes(
        authenticated_client,
        pod_id,
        group,
        "Can you take the release notes?",
        parts=[{"type": "mention", "kind": "MEMBER", "id": colleague["id"]}],
    )
    push = await _push_to(named["id"], colleague["id"])
    await _deliver(push.id)

    assert (await _row(push.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert mail["to"] == [colleague["email"]]


async def test_a_plain_group_line_is_not_emailed(
    authenticated_client, setting, message_store
):
    """No chat app reaches them, and the line does not name them: it waits in
    Lemma rather than spending the pod's emails on a group's chatter."""
    pod_id, colleague = setting
    group = await _a_group_with(authenticated_client, pod_id, colleague)

    plain = await member_writes(authenticated_client, pod_id, group, "Lunch at one.")
    push = await _push_to(plain["id"], colleague["id"])
    await _deliver(push.id)

    skipped = await _row(push.id)
    assert skipped.status is DeliveryStatus.SKIPPED
    assert skipped.reason == UndeliverableReason.NOT_WORTH_AN_EMAIL
    assert message_store.get_all("RESEND") == []


async def test_held_pushes_go_with_the_next_one_from_their_channel(
    authenticated_client, setting, message_store
):
    """A notice the budget held is not sent, and is not forgotten: the next
    notice waits with it, and once the hour's budget is back the two go as
    one push that says the other is waiting."""
    pod_id, colleague = setting
    await _spend_their_budget(pod_id, colleague["id"])
    first = await _tell(authenticated_client, pod_id, colleague["email"], text="One.")
    held = await _push_to(first["message_id"], colleague["id"])
    await _deliver(held.id)
    row = await _row(held.id)
    assert (row.status, row.reason_code) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.FOLDED,
    )
    second = await _tell(authenticated_client, pod_id, colleague["email"], text="Two.")
    assert await _pushes_to(second["message_id"], colleague["id"]) == []
    assert await waiting(first["channel_id"], colleague["id"]) == 2
    assert message_store.get_all("RESEND") == []

    await get_redis().delete(_budget_key(pod_id, colleague["id"]))
    await window_ends_now(first["channel_id"], colleague["id"])
    together = await _push_to(second["message_id"], colleague["id"])
    await _deliver(together.id)

    assert (together.lines, together.from_seq) == (2, row.seq)
    assert (await _row(together.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "Two." in mail["text"] and "1 more line waiting in Lemma" in mail["text"]


async def test_a_line_naming_them_is_pushed_while_they_have_the_app_open(
    authenticated_client, setting, message_store
):
    pod_id, colleague = setting
    group = await _a_group_with(authenticated_client, pod_id, colleague)
    await mark_online(UUID(pod_id), UUID(colleague["id"]))

    named = await member_writes(
        authenticated_client,
        pod_id,
        group,
        "Your slides?",
        parts=[{"type": "mention", "kind": "MEMBER", "id": colleague["id"]}],
    )
    called = await _push_to(named["id"], colleague["id"])
    await _deliver(called.id)

    assert (await _row(called.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "Your slides?" in mail["text"]


async def test_having_the_app_open_is_not_reading_the_line(
    authenticated_client, setting, message_store
):
    """The app open somewhere in the pod holds a push a few minutes, not for
    good: still unread once its line is that old, it goes -- and the hold
    says so, rather than that they read it."""
    pod_id, colleague = setting
    await mark_online(UUID(pod_id), UUID(colleague["id"]))
    told = await _tell(authenticated_client, pod_id, colleague["email"], text="One.")
    held = await _push_to(told["message_id"], colleague["id"])
    await _deliver(held.id)

    row = await _row(held.id)
    assert (row.status, row.reason_code) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.FOLDED,
    )
    assert "read" not in (row.reason or "").replace("unread", "")
    assert await waiting(told["channel_id"], colleague["id"]) == 1
    assert message_store.get_all("RESEND") == []

    async with async_session_maker() as session:
        await session.execute(
            update(ChannelMessageModel)
            .where(ChannelMessageModel.id == UUID(told["message_id"]))
            .values(created_at=ChannelMessageModel.created_at - timedelta(minutes=10))
        )
        await session.commit()
    await window_ends_now(told["channel_id"], colleague["id"])
    await run_pushes()
    # The same push, planned again once the hold ended.
    later = await _push_to(told["message_id"], colleague["id"])
    assert later.status is DeliveryStatus.PENDING
    await _deliver(later.id)

    assert (await _row(later.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "One." in mail["text"]


async def test_a_line_they_read_in_the_app_is_not_pushed(
    authenticated_client, async_client, setting, message_store
):
    pod_id, colleague = setting
    told = await _tell(authenticated_client, pod_id, colleague["email"], text="One.")
    push = await _push_to(told["message_id"], colleague["id"])
    read = await async_client.post(
        f"/pods/{pod_id}/channels/{told['channel_id']}/read",
        json={"seq": push.seq},
        headers=auth_headers(colleague),
    )
    assert read.status_code == 200, read.text

    await _deliver(push.id)

    skipped = await _row(push.id)
    assert (skipped.status, skipped.reason_code) == (
        DeliveryStatus.SKIPPED,
        DeliveryReasonCode.IN_APP,
    )
    assert skipped.reason == "They read it in the app before it was pushed."
    assert message_store.get_all("RESEND") == []


async def test_a_push_is_charged_to_the_budget_once_however_often_it_is_tried(
    authenticated_client, db_session, setting, fixed_test_user
):
    pod_id, colleague = setting
    await _resend_unreachable(db_session, fixed_test_user)
    told = await _tell(authenticated_client, pod_id, colleague["email"])
    push = await _push_to(told["message_id"], colleague["id"])

    await _deliver(push.id)
    assert (await _row(push.id)).status is DeliveryStatus.FAILED
    charged = await _charged(pod_id, colleague["id"])
    await _due_now(push.id)
    await _deliver(push.id)

    assert (await _row(push.id)).attempts == 2
    assert await _charged(pod_id, colleague["id"]) == charged == 1
    # Each try is kept: the first failed and why, the second likewise.
    assert await _tries(push.id) == [
        ("FAILED", DeliveryReasonCode.TRANSIENT.value),
        ("FAILED", DeliveryReasonCode.TRANSIENT.value),
    ]
