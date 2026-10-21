"""E2E for carrying a channel line out of Lemma: one delivery row at a time.

Posting a line plans a ``channel_message_deliveries`` row for each place it
was written for and each member who hears of it, and announces it
(``ChannelDeliveryDue``). ``ChannelDeliveries.deliver`` claims one row, sends,
and records on that row what became of it: ``SENT``, ``SKIPPED`` with the
reason a member reads, or ``FAILED`` -- due again after a backoff, until it is
``DEAD``. These follow that path against a real database, the real surface
service and the fake platform servers:

* a line in a member's own channel is the line, and its push one row; what
  the push could not reach is a reason on the row, never a lost line;
* the push goes by the member's own choice (``notify_via``), and a channel
  they turned off or muted plans no push at all;
* a push by cold email opens a thread the reply finds its way back from, and
  says on whose behalf it was written;
* a row is sent once: a second ``deliver`` of a sent row does nothing, and a
  retried send with the same key is the same line and the same row;
* a line deleted before it went is skipped; one the platform keeps refusing
  fails, is tried again, and is given up after the last try.

A member's line to a contact's email place -- the unsubscribe line it
carries, and the ``SKIPPED`` after they unsubscribe or send STOP -- is
followed in ``test_email_contacts_e2e.py`` and ``test_contact_care_e2e.py``;
a web chat's, which is ``SENT`` with no platform call, in
``test_web_widgets_e2e.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.composition import (
    build_channel_deliveries,
    build_direct_messages,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.contacts.config import contacts_settings
from app.modules.contacts.contracts import IdentityKind
from app.modules.contacts.contracts.outreach import SuppressionReason, opt_out
from app.modules.agent_surfaces.contracts.direct_messages import (
    send_direct_message,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.events.work_kinds import SURFACE_CHANNEL_DELIVERY
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.services.direct_messages import DirectMessage
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _ensure_connector_account
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    contact_channel_id,
    deliveries_of_line,
    direct_lines,
    member_writes,
)
from app.modules.agent_surfaces.tests.e2e.test_contact_care_e2e import _dana_writes
from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import (
    CUSTOMER,
    _email_bot,
)
from app.modules.channels.contracts import (
    Delivery,
    DeliveryStatus,
    NewLine,
    Principal,
    Sender,
    direct_channel,
    post,
    sources_of,
)
from app.modules.channels.contracts.deliveries import delivery_by_id
from app.modules.channels.infrastructure.deliveries import MAX_ATTEMPTS
from app.modules.channels.tests.e2e.push_support import run_pushes, window_ends_now
from app.modules.channels.infrastructure.models import (
    ChannelMessageDeliveryModel,
    ChannelModel,
    ChannelSourceModel,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    invite_org_member,
    signup_user,
)

# Every test here claims deliveries in-process; a session worker taking the
# same rows would race what they count.
pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

#: Nothing answers here: a platform that cannot be reached.
UNREACHABLE = "http://127.0.0.1:9"


# ------------------------------------------------------------------- harness


async def _deliver(delivery_id: UUID) -> None:
    """What ``surface.channel_delivery`` does with one row."""
    await build_channel_deliveries(
        SessionUnitOfWorkFactory(async_session_maker)
    ).deliver(delivery_id)


async def _row(delivery_id: UUID) -> Delivery:
    async with async_session_maker() as session:
        found = await delivery_by_id(SqlAlchemyUnitOfWork(session), delivery_id)
    assert found is not None
    return found


async def _due_now(delivery_id: UUID) -> None:
    """The backoff has passed."""
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelMessageDeliveryModel)
            .where(ChannelMessageDeliveryModel.id == delivery_id)
            .values(next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await session.commit()


async def _queued_for(delivery_id: UUID) -> int:
    """How many ``surface.channel_delivery`` items name this row."""
    async with async_session_maker() as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM work_items WHERE kind = :kind "
                    "AND payload->>'delivery_id' = :delivery_id"
                ),
                {"kind": SURFACE_CHANNEL_DELIVERY, "delivery_id": str(delivery_id)},
            )
            or 0
        )


async def _tell(client: AsyncClient, pod_id: str, to: str, **overrides) -> dict:
    payload = {"text": "What did you ship yesterday?"} | overrides
    response = await client.post(f"/pods/{pod_id}/members/{to}/notices", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _resend_bot(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod_id: str,
    user,
    api_base: str,
) -> str:
    """The pod's mailbox; its address."""
    from app.modules.agent_surfaces.tests.e2e.helpers import _create_surface

    account = await _ensure_connector_account(
        db_session,
        user_id=user["id"],
        connector_id="resend",
        credentials={"api_key": "resend-token", "api_base_url": api_base},
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    surface = await _create_surface(
        client, pod_id, config={"type": "RESEND", "account_id": str(account.id)}
    )
    address = surface.get("surface_identity_email")
    if not address:
        row = await db_session.get(AgentSurface, UUID(surface["id"]))
        assert row is not None
        address = row.surface_identity_email
    assert address
    return address


async def _resend_unreachable(db_session: AsyncSession, user) -> None:
    """The mailbox's provider stops answering."""
    await _ensure_connector_account(
        db_session,
        user_id=user["id"],
        connector_id="resend",
        credentials={"api_key": "resend-token", "api_base_url": UNREACHABLE},
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )


async def _colleague(
    owner_client: AsyncClient, async_client: AsyncClient, *, org_id: str, pod_id: str
) -> dict:
    user = await signup_user(async_client, "colleague")
    member = await invite_org_member(
        owner_client, async_client, org_id=org_id, user=user
    )
    await add_pod_member(
        owner_client,
        pod_id=pod_id,
        organization_member_id=member["id"],
        role="POD_EDITOR",
    )
    return user


async def _my_channel(db_session: AsyncSession, pod_id: str, user_id: str) -> str:
    channel = await direct_channel(
        SqlAlchemyUnitOfWork(db_session), pod_id=UUID(pod_id), user_id=UUID(user_id)
    )
    await db_session.commit()
    return str(channel.id)


async def _hear_of(client: AsyncClient, pod_id: str, channel_id: str, **prefs) -> None:
    """How the caller hears of this channel outside the app."""
    changed = await client.patch(
        f"/pods/{pod_id}/channels/{channel_id}/members/me", json=prefs
    )
    assert changed.status_code == 200, changed.text


async def _pod_surfaces(db_session: AsyncSession, *pod_ids: str) -> list[AgentSurface]:
    await db_session.commit()
    return list(
        (
            await db_session.execute(
                select(AgentSurface).where(
                    AgentSurface.pod_id.in_([UUID(pod_id) for pod_id in pod_ids])
                )
            )
        )
        .scalars()
        .all()
    )


# ----------------------------------------------------- a member's own channel


async def test_a_line_is_in_their_channel_and_its_push_says_why_it_went_no_further(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    monkeypatch,
):
    """No surface and no mailbox to mint: not a failure. The line is in their
    channel; its push is one planned row, announced for the worker; and
    delivering it records the actionable reason it went no further."""
    from app.core.config import settings as core_settings

    monkeypatch.setattr(core_settings, "resend_api_key", None)
    pod_id = test_pod["id"]

    told = await _tell(authenticated_client, pod_id, fixed_test_user["email"])

    [line] = await direct_lines(
        db_session, pod_id=pod_id, user_id=fixed_test_user["id"]
    )
    assert str(line.id) == told["message_id"]
    assert line.body == "What did you ship yesterday?"
    assert not line.members_only
    [push] = await deliveries_of_line(told["message_id"])
    assert push.status is DeliveryStatus.PENDING
    assert (push.recipient_user_id, push.source_id) == (
        UUID(fixed_test_user["id"]),
        None,
    )
    assert await _queued_for(push.id) == 1

    await _deliver(push.id)

    skipped = await _row(push.id)
    assert skipped.status is DeliveryStatus.SKIPPED
    assert skipped.reason == UndeliverableReason.EMAIL_NOT_CONFIGURED
    listed = await authenticated_client.get(
        f"/pods/{pod_id}/channels", params={"kind": "DIRECT"}
    )
    [direct] = listed.json()["items"]
    assert direct["id"] == told["channel_id"]
    assert direct["unread"]["lines"] >= 1


async def test_nobody_outside_the_pod_is_ever_a_recipient(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
):
    """404, not 422: an id naming nobody and an id naming somebody outside the
    pod are the same fact from the caller's side, and separating them would
    confirm the person exists. And below the API the service fails closed, so
    no wiring bug becomes "any user id can be messaged"."""
    pod_id = test_pod["id"]
    response = await authenticated_client.post(
        f"/pods/{pod_id}/members/{uuid4()}/notices", json={"text": "Nope."}
    )
    assert response.status_code == 404, response.text

    outsider = uuid4()
    with pytest.raises(AgentSurfaceValidationError):
        await build_direct_messages(SqlAlchemyUnitOfWork(db_session)).send(
            DirectMessage(pod_id=UUID(pod_id), recipient_user_id=outsider, text="Hi")
        )
    await db_session.rollback()
    assert (
        await db_session.scalar(
            select(ChannelModel.id).where(ChannelModel.member_user_id == outsider)
        )
    ) is None


async def test_a_channel_turned_off_or_muted_plans_no_push(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """How a member hears of their channel is theirs: off, or muted for now,
    the line lands and no row is planned to carry it anywhere."""
    pod_id = test_pod["id"]
    mine = await _my_channel(db_session, pod_id, fixed_test_user["id"])

    await _hear_of(authenticated_client, pod_id, mine, notify="NONE")
    off = await _tell(authenticated_client, pod_id, fixed_test_user["email"])
    # Each line's pushes are worked out as it is said, by the settings then.
    assert await deliveries_of_line(off["message_id"]) == []
    await _hear_of(
        authenticated_client,
        pod_id,
        mine,
        notify="ALL",
        muted_until=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    muted = await _tell(authenticated_client, pod_id, fixed_test_user["email"])
    assert await deliveries_of_line(muted["message_id"]) == []
    await _hear_of(authenticated_client, pod_id, mine, muted_until=None)
    heard = await _tell(authenticated_client, pod_id, fixed_test_user["email"])

    assert {off["channel_id"], muted["channel_id"]} == {mine}
    assert len(await deliveries_of_line(heard["message_id"])) == 1
    assert (
        len(
            await direct_lines(db_session, pod_id=pod_id, user_id=fixed_test_user["id"])
        )
        == 3
    )


async def test_the_way_a_member_chose_is_honoured_or_refused_never_swapped(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """``notify_via`` names where they want to hear. A way nothing here can
    carry is refused, with what would work -- never quietly swapped for email,
    which would leave them waiting somewhere it never arrives."""
    pod_id = test_pod["id"]
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        api_base=fake_resend.api_base,
    )
    mine = await _my_channel(db_session, pod_id, fixed_test_user["id"])

    await _hear_of(authenticated_client, pod_id, mine, notify_via=["TELEGRAM"])
    refused = await _tell(authenticated_client, pod_id, fixed_test_user["email"])
    [push] = await deliveries_of_line(refused["message_id"])
    await _deliver(push.id)

    skipped = await _row(push.id)
    assert skipped.status is DeliveryStatus.SKIPPED
    assert "telegram" in (skipped.reason or "").lower()
    assert message_store.get_all("RESEND") == []

    await _hear_of(authenticated_client, pod_id, mine, notify_via=["EMAIL"])
    honoured = await _tell(authenticated_client, pod_id, fixed_test_user["email"])
    # Within the window after the last push, it waits for the window's end.
    await run_pushes()
    await window_ends_now(mine, fixed_test_user["id"])
    [push] = await deliveries_of_line(honoured["message_id"])
    await _deliver(push.id)

    sent = await _row(push.id)
    assert (sent.status, sent.platform) == (DeliveryStatus.SENT, "RESEND")
    assert len(message_store.get_all("RESEND")) == 1


# ----------------------------------------------------- by email, and once


async def test_a_push_cold_opens_an_email_thread_the_reply_can_find(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """The mail goes out, it is *not* prefixed "Re:", it carries a seed in
    ``References``, and a reply quoting that seed lands in the conversation the
    mail was written into, in the member's own channel. Delivering the sent
    row again sends nothing."""
    from app.modules.agent_surfaces.domain.ingress_request import (
        SurfacePlatformWebhookIngress,
    )
    from app.modules.agent_surfaces.tests.e2e.helpers import (
        _conversation_by_external_thread,
        _resend_payload,
    )
    from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
        wait_for_messages,
    )
    from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
        process_ingress_and_run_scripted,
        script_text,
    )

    pod_id = test_pod["id"]
    address = await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        api_base=fake_resend.api_base,
    )

    told = await _tell(authenticated_client, pod_id, fixed_test_user["email"])
    [push] = await deliveries_of_line(told["message_id"])
    await _deliver(push.id)

    sent = await _row(push.id)
    assert (sent.platform, sent.status) == ("RESEND", DeliveryStatus.SENT)
    assert sent.attempts == 1 and sent.sent_at is not None
    mail = json.loads(
        json.dumps((await wait_for_messages(message_store, "RESEND", min_count=1))[-1])
    )
    # First contact is not a reply, and must not read as one.
    assert not str(mail.get("subject") or "").lower().startswith("re:")
    seed = json.dumps(mail).split('"References": "')[1].split('"')[0]
    assert seed.startswith("<lemma-")

    # Sent once: the row is not due, so a second job finds nothing to do.
    await _deliver(push.id)
    assert len(message_store.get_all("RESEND")) == 1
    assert (await _row(push.id)).attempts == 1

    # Their MUA answers with References = [our seed, their message id], so the
    # parser's thread root is our seed.
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload={
                **_resend_payload(
                    sender_email=fixed_test_user["email"],
                    assistant_address=address,
                    message_id="reply-to-standup",
                    text="Shipped the importer and reviewed two PRs.",
                ),
                "references": [seed, "<reply-to-standup@resend-e2e.test>"],
            },
            headers={},
        ),
        script=[script_text("Thanks, recorded.")],
    )

    threaded = await _conversation_by_external_thread(
        authenticated_client, pod_id=pod_id, external_thread_id=seed
    )
    assert threaded is not None, "the reply did not land in the mail's thread"
    # One place for the correspondent, keyed as their reply is heard: by
    # their address, not by the thread.
    [place] = (
        await db_session.execute(
            select(ChannelSourceModel).where(
                ChannelSourceModel.external_user_id
                == fixed_test_user["email"].strip().lower()
            )
        )
    ).scalars()
    assert place.external_channel_id is None
    assert place.kind == "PERSON"
    assert str(place.channel_id) == told["channel_id"]
    channel = await db_session.get(ChannelModel, place.channel_id)
    assert channel is not None and channel.kind == "DIRECT"
    assert channel.member_user_id == UUID(fixed_test_user["id"])


async def test_a_line_for_somebody_else_says_on_whose_behalf(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """The pod's agent writes for one member to another: the mail says whose
    words they are, and the line is in the recipient's channel, not the
    writer's."""
    pod_id = test_pod["id"]
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        api_base=fake_resend.api_base,
    )
    colleague = await _colleague(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
    )

    told = await _tell(
        authenticated_client, pod_id, colleague["email"], text="Review the Q3 deck?"
    )
    [push] = await deliveries_of_line(told["message_id"])
    assert push.recipient_user_id == UUID(colleague["id"])
    await _deliver(push.id)

    assert (await _row(push.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert mail["to"] == [colleague["email"]]
    assert mail["text"].startswith("On behalf of ")
    assert "Review the Q3 deck?" in mail["text"]
    # One line: the mail carried it, and is not a second line beside it.
    [line] = await direct_lines(db_session, pod_id=pod_id, user_id=colleague["id"])
    assert line.on_behalf_user_id == UUID(fixed_test_user["id"])
    assert (
        await direct_lines(db_session, pod_id=pod_id, user_id=fixed_test_user["id"])
        == []
    )


async def test_a_retried_send_with_the_same_key_is_the_same_line_and_row(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """A worker that retries an agent's ``channel_post`` replays the same call;
    the key makes it the first line -- and so its one push -- not a second."""
    pod_id, owner = test_pod["id"], UUID(fixed_test_user["id"])

    async def send():
        return await send_direct_message(
            pod_id=UUID(pod_id),
            recipient_user_id=owner,
            text="Does the 14th work for the offsite?",
            actor_user_id=owner,
            key="run-1:call-1",
        )

    first = await send()
    again = await send()

    assert again.message_id == first.message_id
    lines = await direct_lines(db_session, pod_id=pod_id, user_id=owner)
    assert [line.id for line in lines] == [first.message_id]
    assert len(await deliveries_of_line(first.message_id)) == 1


# ------------------------------------------------------- what a row ends as


async def _contact_channel(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
) -> str:
    """Dana, a contact who emailed the pod's bot, and her channel."""
    pod_id = test_pod["id"]
    _surface, address = await _email_bot(
        client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    await _dana_writes(db_session, address, f"dana-{uuid4().hex[:8]}")
    [dana] = (await client.get(f"/pods/{pod_id}/contacts")).json()["items"]
    return await contact_channel_id(client, pod_id, dana["id"])


async def test_a_line_deleted_before_it_went_is_skipped(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    channel = await _contact_channel(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    mailed = len(message_store.get_all("RESEND"))
    written = await member_writes(
        authenticated_client, pod_id, channel, "Sorry, wrong customer."
    )
    [delivery] = await deliveries_of_line(written["id"])
    assert delivery.source_id is not None
    deleted = await authenticated_client.delete(
        f"/pods/{pod_id}/channels/{channel}/messages/{written['id']}"
    )
    assert deleted.status_code == 204, deleted.text

    await _deliver(delivery.id)

    skipped = await _row(delivery.id)
    assert skipped.status is DeliveryStatus.SKIPPED
    assert skipped.reason == "The line was deleted before it went."
    assert len(message_store.get_all("RESEND")) == mailed


async def test_a_platform_that_keeps_refusing_is_tried_again_then_given_up(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """Not taken is not final: the row is due again after a backoff, and only
    the last try gives it up -- ``DEAD``, never sent twice and never again."""
    pod_id = test_pod["id"]
    channel = await _contact_channel(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    # Each try is one more line to her today; the cap is not what is tested.
    monkeypatch.setattr(
        contacts_settings, "contacts_member_lines_per_contact_per_day", 100
    )
    await _resend_unreachable(db_session, fixed_test_user)
    written = await member_writes(
        authenticated_client, pod_id, channel, "Your order shipped today."
    )
    [delivery] = await deliveries_of_line(written["id"])

    await _deliver(delivery.id)

    failed = await _row(delivery.id)
    assert (failed.status, failed.attempts) == (DeliveryStatus.FAILED, 1)
    assert failed.next_attempt_at is not None
    assert failed.next_attempt_at > datetime.now(timezone.utc)
    # Not due yet: a job that runs early finds nothing to do.
    await _deliver(delivery.id)
    assert (await _row(delivery.id)).attempts == 1

    for attempt in range(2, MAX_ATTEMPTS + 1):
        await _due_now(delivery.id)
        await _deliver(delivery.id)
        assert (await _row(delivery.id)).attempts == attempt

    dead = await _row(delivery.id)
    assert dead.status is DeliveryStatus.DEAD
    assert dead.next_attempt_at is None
    assert "RESEND" in (dead.reason or "")
    await _due_now(delivery.id)
    await _deliver(delivery.id)
    assert (await _row(delivery.id)).attempts == MAX_ATTEMPTS


async def test_retrying_a_failed_line_does_not_spend_the_contacts_day(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """One line is one of the day's lines to a contact, however often the
    platform has to be asked to take it."""
    pod_id = test_pod["id"]
    channel = await _contact_channel(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    monkeypatch.setattr(
        contacts_settings, "contacts_member_lines_per_contact_per_day", 1
    )
    await _resend_unreachable(db_session, fixed_test_user)
    written = await member_writes(
        authenticated_client, pod_id, channel, "Your order shipped today."
    )
    [delivery] = await deliveries_of_line(written["id"])

    await _deliver(delivery.id)
    await _due_now(delivery.id)
    await _deliver(delivery.id)

    assert (await _row(delivery.id)).status is DeliveryStatus.FAILED


async def test_a_push_the_platform_failed_is_tried_again(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
):
    """A member's mailbox provider that is down is an outage, not a fact about
    the member: the push is due again, not given up as unreachable."""
    pod_id = test_pod["id"]
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        api_base=fake_resend.api_base,
    )
    await _resend_unreachable(db_session, fixed_test_user)

    told = await _tell(authenticated_client, pod_id, fixed_test_user["email"])
    [push] = await deliveries_of_line(told["message_id"])
    await _deliver(push.id)

    assert (await _row(push.id)).status is DeliveryStatus.FAILED


# ----------------------------------------------------- a mailbox, on first use


async def test_a_pod_with_nothing_connected_mints_itself_a_readable_mailbox(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """A personal pod with no surfaces at all, asked to reach a member, mints
    the pod's assistant a mailbox on the push -- one with an address a person
    could be asked to type -- rather than reporting it has nothing."""
    from app.core.config import settings as core_settings

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")

    pod_id = test_pod["id"]
    assert await _pod_surfaces(db_session, pod_id) == [], (
        "this test is only meaningful in a pod with no surfaces"
    )

    told = await _tell(authenticated_client, pod_id, fixed_test_user["id"])
    [push] = await deliveries_of_line(told["message_id"])
    await _deliver(push.id)

    assert (await _row(push.id)).reason != UndeliverableReason.NO_ACTIVE_SURFACE
    [surface] = await _pod_surfaces(db_session, pod_id)
    assert surface.surface_type == "RESEND"
    # The assistant's, whose row id is its pod's.
    assert surface.agent_id == UUID(pod_id)
    assert surface.surface_identity_email.endswith("@ops.example.com")
    # Readable, not pod-<32 hex chars>: people are asked to write to this.
    assert not surface.surface_identity_email.split("@")[0].startswith("pod-")


async def test_a_second_pod_in_the_org_also_gets_a_mailbox(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The system Resend key is one API key over a catch-all domain, not an
    identity one pod claims: the first mailbox in an organization must not
    refuse every mailbox after it."""
    from app.core.config import settings as core_settings

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")

    second = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Second Pod {uuid4()}",
            "slug": f"second-pod-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert second.status_code in (200, 201), second.text
    second_pod_id = second.json()["id"]

    for pod_id in (test_pod["id"], second_pod_id):
        told = await _tell(authenticated_client, pod_id, fixed_test_user["id"])
        [push] = await deliveries_of_line(told["message_id"])
        await _deliver(push.id)
        assert "creating a mailbox" not in ((await _row(push.id)).reason or "")

    addresses = {
        row.pod_id: row.surface_identity_email
        for row in await _pod_surfaces(db_session, test_pod["id"], second_pod_id)
    }
    assert len(addresses) == 2, "both pods should hold a mailbox of their own"
    # Distinct addresses are what makes sharing the key safe: inbound routes on
    # the address, which carries a unique index.
    assert len(set(addresses.values())) == 2


@pytest.mark.parametrize("polling", [False, True], ids=["webhook-only", "polling"])
async def test_a_mailbox_on_a_local_url_needs_polling(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
    polling: bool,
):
    """The desktop app: a localhost API URL has no public webhook. Without
    polling the mailbox cannot be minted and the push says so; with it, the
    mailbox is minted and the member can be mailed."""
    from app.core.config import settings as core_settings

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")
    monkeypatch.setattr(core_settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(surface_settings, "enable_resend_polling_mode", polling)

    pod_id = test_pod["id"]
    told = await _tell(authenticated_client, pod_id, fixed_test_user["id"])
    [push] = await deliveries_of_line(told["message_id"])
    await _deliver(push.id)

    reason = (await _row(push.id)).reason or ""
    surfaces = await _pod_surfaces(db_session, pod_id)
    if polling:
        assert "creating a mailbox for it failed" not in reason
        [surface] = surfaces
        assert surface.surface_type == "RESEND"
        assert surface.surface_identity_email.endswith("@ops.example.com")
    else:
        assert (await _row(push.id)).status is DeliveryStatus.SKIPPED
        assert "creating a mailbox for it failed" in reason
        assert surfaces == [], "no surface when the gate blocks it"


# ------------------------------------------------------- a contact's rules


async def _dana_opts_out(pod_id: str) -> None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await opt_out(
            uow,
            pod_id=UUID(pod_id),
            kind=IdentityKind.EMAIL,
            value=CUSTOMER,
            reason=SuppressionReason.STOP,
        )
        await uow.commit()


async def test_an_agents_line_to_a_contact_who_opted_out_is_skipped(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Opt-out first on every path: not only a member's lines -- an agent's
    answer, an acknowledgement -- and before anything else is asked."""
    pod_id = test_pod["id"]
    channel = await _contact_channel(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    await _dana_opts_out(pod_id)
    mailed = len(message_store.get_all("RESEND"))
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        [place] = await sources_of(uow, UUID(channel))
        line = await post(
            uow,
            channel_id=UUID(channel),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod_id),
                    on_behalf_of=Principal.member(UUID(fixed_test_user["id"])),
                ),
                text="Following up on your order.",
            ),
            deliver_to=[place.id],
        )
        await uow.commit()
    assert line is not None
    [delivery] = await deliveries_of_line(line.id)

    await _deliver(delivery.id)

    skipped = await _row(delivery.id)
    assert skipped.status is DeliveryStatus.SKIPPED
    assert skipped.reason == "The contact asked not to be written to there"
    assert len(message_store.get_all("RESEND")) == mailed


async def test_a_member_line_waits_out_redis_rather_than_going_uncounted(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """The contact's day could not be counted: tried again, never given up,
    and never sent uncounted."""
    pod_id = test_pod["id"]
    channel = await _contact_channel(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    mailed = len(message_store.get_all("RESEND"))
    written = await member_writes(
        authenticated_client, pod_id, channel, "Your order shipped today."
    )
    [delivery] = await deliveries_of_line(written["id"])

    class _RedisAway:
        async def eval(self, *args, **kwargs):
            raise RedisConnectionError("connection refused")

    blind = build_channel_deliveries(SessionUnitOfWorkFactory(async_session_maker))
    blind.redis = _RedisAway()  # type: ignore[assignment]
    await blind.deliver(delivery.id)

    failed = await _row(delivery.id)
    assert failed.status is DeliveryStatus.FAILED
    assert len(message_store.get_all("RESEND")) == mailed

    await _due_now(delivery.id)
    await _deliver(delivery.id)
    assert (await _row(delivery.id)).status is DeliveryStatus.SENT
