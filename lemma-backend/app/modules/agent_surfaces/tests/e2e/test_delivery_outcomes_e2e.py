"""E2E for what one try of a channel delivery ends as, and what follows it.

A delivery is retried only when a retry cannot be the duplicate the person
sees (``delivery_outcome``): against a real database, the real surface
service and the fake platform servers --

* a send whose answer never came back (the platform took it, the connection
  dropped) is ``DEAD``, never sent twice -- except on Resend, which dedupes a
  resend by the delivery's key, so a cold email retried is still one email;
* a platform's refusal is ``DEAD`` at once, not six tries later; a
  ``Retry-After`` longer than a send waits inline holds the retry back at
  least that long;
* something going wrong before any platform was called is ``FAILED`` and
  tried again, not given up as possibly sent;
* a channel's lines reach a place in the order they were written;
* a sender that died mid-send to an inbox is sent again (Resend dedupes it);
* a removed bot's answer is ``SKIPPED``, not ``SENT``;
* a retried line is one follow-up in the contact's conversation;
* a WhatsApp failure reported before the sender recorded its outcome stands.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import MessageModel
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.events.work_kinds import SURFACE_CHANNEL_DELIVERY
from app.modules.agent_surfaces.services import channel_deliveries, cold_open_sends
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    deliveries_of_line,
    drain,
    member_writes,
)
from app.modules.agent_surfaces.tests.e2e.test_channel_deliveries_e2e import (
    _contact_channel,
    _deliver,
    _due_now,
    _resend_bot,
    _resend_unreachable,
    _row,
    _tell,
)
from app.modules.agent_surfaces.tests.e2e.test_run_answer_deliveries_e2e import (
    _a_finished_run,
    _answer_lines,
    _lines_only_registry,
    _telegram_answers,
)
from app.modules.channels.contracts import DeliveryStatus
from app.modules.channels.contracts.deliveries import (
    claim_delivery,
    delivery_by_external,
    delivery_noted,
    delivery_sent,
    delivery_undelivered,
)
from app.modules.channels.infrastructure.deliveries import (
    CLAIM_LEASE,
    INTERRUPTED,
    DeliveryRepository,
)
from app.modules.channels.infrastructure.models import (
    ChannelDeliveryAttemptModel,
    ChannelMessageDeliveryModel,
    ExternalAccountModel,
)
from app.modules.contacts.config import contacts_settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work"),
]


# ------------------------------------------------------------------ harness


async def _planned_answer(
    client: AsyncClient, db_session: AsyncSession, test_pod, user, fake_telegram, name
) -> UUID:
    """A run's answer line to a Telegram place, planned and not yet sent."""
    _conversation, run_id = await _a_finished_run(
        client, db_session, test_pod, user, fake_telegram, name
    )
    await drain(_lines_only_registry())
    [line] = await _answer_lines(run_id)
    [planned] = await deliveries_of_line(line.id)
    assert planned.status is DeliveryStatus.PENDING
    return planned.id


@pytest.fixture
def telegram_webhooks(monkeypatch):
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)


async def _lapse(delivery_id: UUID) -> None:
    """A sender took the delivery, and its lease ran out with nothing recorded."""
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        assert await claim_delivery(uow, delivery_id) is not None
        await uow.commit()
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelMessageDeliveryModel)
            .where(ChannelMessageDeliveryModel.id == delivery_id)
            .values(next_attempt_at=datetime.now(timezone.utc) - CLAIM_LEASE)
        )
        await session.commit()


async def _forget_jobs(delivery_id: UUID) -> None:
    async with async_session_maker() as session:
        await session.execute(
            text(
                "DELETE FROM work_items WHERE kind = :kind "
                "AND payload->>'delivery_id' = :delivery_id"
            ),
            {"kind": SURFACE_CHANNEL_DELIVERY, "delivery_id": str(delivery_id)},
        )
        await session.commit()


async def _queued(delivery_id: UUID) -> int:
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


# ------------------------------------------------- what a try ends as (H4)


@pytest.mark.usefixtures("telegram_webhooks")
async def test_a_send_whose_answer_never_came_back_is_not_sent_again(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
):
    """Telegram took the answer and the connection dropped before it said so:
    it may be on their screen, so it is ``DEAD`` saying so -- and a second
    job for the row sends nothing."""
    delivery_id = await _planned_answer(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "dropped-bot",
    )
    fake_telegram.drop_answers["sendRichMessage"] = 1

    await _deliver(delivery_id)

    dead = await _row(delivery_id)
    assert dead.status is DeliveryStatus.DEAD
    assert dead.attempts == 1
    assert "may or may not have arrived" in (dead.reason or "")
    assert len(_telegram_answers(message_store)) == 1
    await _due_now(delivery_id)
    await _deliver(delivery_id)
    assert len(_telegram_answers(message_store)) == 1, "sent twice"


@pytest.mark.usefixtures("telegram_webhooks")
async def test_a_refusal_is_given_up_at_once_not_after_every_retry(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
):
    """A 403 is the platform's answer, not an outage: tried once, ``DEAD``."""
    delivery_id = await _planned_answer(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "refused-bot",
    )
    fake_telegram.refuse_next["sendRichMessage"] = 1

    await _deliver(delivery_id)

    dead = await _row(delivery_id)
    assert (dead.status, dead.attempts) == (DeliveryStatus.DEAD, 1)
    assert dead.next_attempt_at is None
    assert "refused" in (dead.reason or "")


@pytest.mark.usefixtures("telegram_webhooks")
async def test_a_bot_whose_token_is_refused_says_so_on_its_surface(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
):
    """A 401 refuses the bot, not the message: the send is ``DEAD`` with the
    platform's answer kept on it, and the surface says why until a send
    goes through again."""
    delivery_id = await _planned_answer(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "token-refused-bot",
    )
    fake_telegram.unauthorized |= {"sendRichMessage", "sendMessage"}

    await _deliver(delivery_id)

    dead = await _row(delivery_id)
    assert dead.status is DeliveryStatus.DEAD
    async with async_session_maker() as session:
        answered = await session.scalar(
            select(ChannelDeliveryAttemptModel.provider_status).where(
                ChannelDeliveryAttemptModel.delivery_id == delivery_id
            )
        )
    assert answered == "401"
    surface = f"/pods/{test_pod['id']}/surfaces/token-refused-bot"
    shown = (await authenticated_client.get(surface)).json()
    assert shown["status"] == "ACTIVE"
    assert "refused the bot's credentials" in shown["problem"]
    assert (await authenticated_client.get(f"{surface}/setup")).json()["ready"] is False


@pytest.mark.usefixtures("telegram_webhooks")
async def test_a_retry_after_longer_than_a_send_waits_holds_the_retry_back(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
):
    """Asked to wait two minutes -- past what a send waits inline -- the row
    is ``FAILED`` and due again no sooner than that, not after the first
    backoff (30 s)."""
    delivery_id = await _planned_answer(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "throttled-bot",
    )
    fake_telegram.fail_next["sendRichMessage"] = 1
    fake_telegram.retry_after = 120
    before = datetime.now(timezone.utc)

    await _deliver(delivery_id)

    failed = await _row(delivery_id)
    assert failed.status is DeliveryStatus.FAILED
    assert failed.next_attempt_at is not None
    assert failed.next_attempt_at >= before + timedelta(seconds=115)


@pytest.mark.usefixtures("telegram_webhooks")
async def test_an_error_before_any_platform_call_is_tried_again(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """Our own database blinking before the send leaves nothing on any
    platform: ``FAILED`` and due again -- not a lapsed claim settled as
    "may or may not have arrived"."""
    delivery_id = await _planned_answer(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "blink-bot",
    )

    async def blink(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection reset"))

    # Only the blink is undone: the fixtures' fake-platform wiring stays.
    with monkeypatch.context() as blinking:
        blinking.setattr(channel_deliveries, "source_by_id", blink)
        with pytest.raises(OperationalError):
            await _deliver(delivery_id)

    failed = await _row(delivery_id)
    assert failed.status is DeliveryStatus.FAILED
    assert failed.claimed_at is None
    assert len(_telegram_answers(message_store)) == 0
    await _due_now(delivery_id)
    await _deliver(delivery_id)
    assert (await _row(delivery_id)).status is DeliveryStatus.SENT
    assert len(_telegram_answers(message_store)) == 1


# ------------------------------------------- cold email retried is one (H5)


async def test_a_cold_email_push_retried_is_one_email(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """Resend took the push and its answer was lost, then it failed to answer
    at all: the row is retried, and the retry -- under the delivery's key --
    is the email already sent, not a second one."""
    pod_id = test_pod["id"]
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        api_base=fake_resend.api_base,
    )
    told = await _tell(authenticated_client, pod_id, fixed_test_user["email"])
    [push] = await deliveries_of_line(told["message_id"])
    fake_resend.drop_answers = 1
    fake_resend.fail_next = [503] * 10

    await _deliver(push.id)

    assert (await _row(push.id)).status is DeliveryStatus.FAILED
    assert len(message_store.get_all("RESEND")) == 1
    fake_resend.fail_next = []
    await _due_now(push.id)
    await _deliver(push.id)

    sent = await _row(push.id)
    assert sent.status is DeliveryStatus.SENT
    assert len(message_store.get_all("RESEND")) == 1, "a second email"
    assert fake_resend.replayed_keys == [f"channel-delivery-{push.id}"]
    # And one copy of it in their conversation, however often it was tried.
    async with async_session_maker() as session:
        copies = (
            await session.scalars(
                select(MessageModel.id).where(
                    MessageModel.message_metadata["notification_id"].astext
                    == told["message_id"]
                )
            )
        ).all()
    assert len(copies) == 1


# ------------------------------------------------ one place, in order (M-D2)


async def test_a_places_lines_go_in_the_order_they_were_written(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """The second line's job runs first: it waits, and goes once the first
    has -- announced by the first's outcome, not left for the sweeper."""
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
        contacts_settings, "contacts_member_lines_per_contact_per_day", 100
    )
    first = await member_writes(authenticated_client, pod_id, channel, "First.")
    second = await member_writes(authenticated_client, pod_id, channel, "Second.")
    [one] = await deliveries_of_line(first["id"])
    [two] = await deliveries_of_line(second["id"])
    mailed = len(message_store.get_all("RESEND"))

    await _deliver(two.id)
    waiting = await _row(two.id)
    assert (waiting.status, waiting.attempts) == (DeliveryStatus.PENDING, 0)
    assert len(message_store.get_all("RESEND")) == mailed

    # Its job is spent: only the first line's outcome can start it again.
    await _forget_jobs(two.id)
    await _deliver(one.id)
    assert (await _row(one.id)).status is DeliveryStatus.SENT
    assert await _queued(two.id) == 1, "the next line was not announced"
    await _deliver(two.id)
    assert (await _row(two.id)).status is DeliveryStatus.SENT

    texts = [mail["text"] for mail in message_store.get_all("RESEND")[mailed:]]
    assert [t.split("\n")[0] for t in texts] == ["First.", "Second."]


# ------------------------------------------- a restart mid-send (gap 7)


async def test_an_email_whose_sender_died_mid_send_is_sent_again_once(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """A deploy killed the sender after it claimed a line to an inbox. Resend
    dedupes on the delivery's key, so the lapsed claim is simply sent again
    -- not given up as "may or may not have arrived"."""
    pod_id = test_pod["id"]
    channel = await _contact_channel(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    written = await member_writes(authenticated_client, pod_id, channel, "Shipped.")
    [delivery] = await deliveries_of_line(written["id"])
    mailed = len(message_store.get_all("RESEND"))
    await _lapse(delivery.id)

    await _deliver(delivery.id)

    sent = await _row(delivery.id)
    assert sent.status is DeliveryStatus.SENT, sent.reason
    assert sent.reason != INTERRUPTED
    assert len(message_store.get_all("RESEND")) == mailed + 1


# -------------------------------------------- a removed bot's answer (M-D4)


@pytest.mark.usefixtures("telegram_webhooks")
async def test_an_answer_to_a_place_whose_bot_was_removed_is_skipped(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
):
    """Not ``SENT`` as if it were a web chat's: nothing carries it, and the
    line says why."""
    _conversation, run_id = await _a_finished_run(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "removed-bot",
    )
    async with async_session_maker() as session:
        # The bot is gone: nothing reaches the account's places now.
        await session.execute(
            update(ExternalAccountModel)
            .where(ExternalAccountModel.platform == "TELEGRAM")
            .where(ExternalAccountModel.pod_id == UUID(test_pod["id"]))
            .values(surface_id=None)
        )
        await session.commit()
    await drain(_lines_only_registry())
    [line] = await _answer_lines(run_id)
    [planned] = await deliveries_of_line(line.id)
    assert planned.status is DeliveryStatus.PENDING

    await _deliver(planned.id)

    skipped = await _row(planned.id)
    assert skipped.status is DeliveryStatus.SKIPPED
    assert "no longer connected" in (skipped.reason or "")
    assert _telegram_answers(message_store) == []


# ---------------------------------------- one follow-up per line (M-D5)


async def test_a_retried_line_is_one_follow_up_in_their_conversation(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The first try failed, the retry went: the contact's conversation holds
    the member's line once, now marked delivered."""
    from app.modules.agent_surfaces.tests.e2e.test_contact_care_e2e import (
        _dana_writes,
    )
    from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import (
        _email_bot,
    )
    from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
        contact_channel_id,
    )

    pod_id = test_pod["id"]
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    context = await _dana_writes(db_session, address, f"dana-{uuid4().hex[:8]}")
    [dana] = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ]
    channel = await contact_channel_id(authenticated_client, pod_id, dana["id"])
    monkeypatch.setattr(
        contacts_settings, "contacts_member_lines_per_contact_per_day", 100
    )
    await _resend_unreachable(db_session, fixed_test_user)
    written = await member_writes(
        authenticated_client, pod_id, channel, "Your refund is on its way."
    )
    [delivery] = await deliveries_of_line(written["id"])
    await _deliver(delivery.id)
    assert (await _row(delivery.id)).status is DeliveryStatus.FAILED

    from app.modules.agent_surfaces.tests.e2e.helpers import (
        _ensure_connector_account,
    )
    from app.modules.connectors.domain.connector import AuthProvider

    await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={"api_key": "resend-token", "api_base_url": fake_resend.api_base},
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    await _due_now(delivery.id)
    await _deliver(delivery.id)
    assert (await _row(delivery.id)).status is DeliveryStatus.SENT

    async with async_session_maker() as session:
        follow_ups = list(
            (
                await session.scalars(
                    select(MessageModel).where(
                        MessageModel.conversation_id == context.conversation_id,
                        MessageModel.message_metadata["line_id"].astext
                        == str(written["id"]),
                    )
                )
            ).all()
        )
    [follow_up] = follow_ups
    assert (follow_up.message_metadata or {}).get("delivered") is True


# --------------------------------- a failure reported before SENT (Low)


async def test_a_platform_failure_reported_before_the_outcome_is_recorded_stands(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    telegram_webhooks,
):
    """The platform's id is noted as it answers; its failure report finds the
    row while the sender is still recording, and the sender's ``SENT``
    afterwards does not undo it. A report naming no delivery changes nothing
    else."""
    delivery_id = await _planned_answer(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "early-report-bot",
    )
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        assert await claim_delivery(uow, delivery_id) is not None
        await delivery_noted(uow, delivery_id, external_message_id="wamid.early")
        await uow.commit()

    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        found = await delivery_by_external(
            uow, platform="WHATSAPP", external_message_id="wamid.early"
        )
        assert found is not None and found.id == delivery_id
        assert await delivery_undelivered(uow, found.id, reason="Never arrived.")
        await uow.commit()
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await delivery_sent(
            uow, delivery_id, platform="WHATSAPP", external_message_id="wamid.early"
        )
        await uow.commit()

    settled = await _row(delivery_id)
    assert (settled.status, settled.reason) == (DeliveryStatus.DEAD, "Never arrived.")
    async with async_session_maker() as session:
        assert (
            await DeliveryRepository(session).by_external(
                platform="WHATSAPP", external_message_id="wamid.nobody"
            )
            is None
        )


# ------------------------------- the day counted once it can go (Low)


async def test_a_line_that_could_not_go_does_not_spend_the_contacts_day(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """A member's first word to an address nobody wrote from, on a mailbox
    that cannot write first, goes nowhere -- and leaves the contact's one
    line of the day for the next try that can go."""
    from app.modules.agent_surfaces.tests.e2e.test_contact_outreach_e2e import (
        _contact,
        _reach,
    )
    from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import (
        _email_bot,
    )
    from app.modules.contacts.contracts import IdentityKind

    pod_id = test_pod["id"]
    await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    monkeypatch.setattr(
        contacts_settings, "contacts_member_lines_per_contact_per_day", 1
    )
    lead = f"lead-{uuid4().hex[:6]}@prospect.example"
    contact_id = await _contact(pod_id, IdentityKind.EMAIL, lead, "Lee")
    monkeypatch.setattr(cold_open_sends, "can_cold_open", lambda _surface: False)
    first = await _reach(authenticated_client, pod_id, str(contact_id), lead, "Hi")
    assert first.status_code == 201, first.text
    [nowhere] = await deliveries_of_line(first.json()["line"]["id"])
    await _deliver(nowhere.id)
    assert (await _row(nowhere.id)).status is DeliveryStatus.SKIPPED

    monkeypatch.setattr(cold_open_sends, "can_cold_open", lambda _surface: True)
    second = await _reach(authenticated_client, pod_id, str(contact_id), lead, "Hi?")
    assert second.status_code == 201, second.text
    [goes] = await deliveries_of_line(second.json()["line"]["id"])
    await _deliver(goes.id)

    sent = await _row(goes.id)
    assert sent.status is DeliveryStatus.SENT, sent.reason


# ------------------------- a follow-up before they reply threads (Low)


async def test_a_second_email_before_they_reply_is_in_the_first_ones_thread(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Two lines to somebody who has not written back: one thread in their
    inbox, under one subject -- not a new thread per line."""
    from app.modules.agent_surfaces.tests.e2e.test_contact_outreach_e2e import (
        _contact,
        _reach,
    )
    from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import (
        _email_bot,
    )
    from app.modules.contacts.contracts import IdentityKind

    pod_id = test_pod["id"]
    await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    monkeypatch.setattr(
        contacts_settings, "contacts_member_lines_per_contact_per_day", 100
    )
    lead = f"lead-{uuid4().hex[:6]}@prospect.example"
    contact_id = await _contact(pod_id, IdentityKind.EMAIL, lead, "Lee")
    mailed = len(message_store.get_all("RESEND"))
    for text_ in ("Following up on your enquiry.", "And one more thing."):
        reached = await _reach(
            authenticated_client, pod_id, str(contact_id), lead, text_
        )
        assert reached.status_code == 201, reached.text
        [delivery] = await deliveries_of_line(reached.json()["line"]["id"])
        await _deliver(delivery.id)
        assert (await _row(delivery.id)).status is DeliveryStatus.SENT

    first, second = message_store.get_all("RESEND")[mailed:]
    assert first["headers"]["References"] == second["headers"]["References"]
    assert first["subject"] == second["subject"]
