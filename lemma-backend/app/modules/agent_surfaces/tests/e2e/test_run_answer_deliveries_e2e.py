"""E2E for a run's answer as a channel line with a delivery: one outbound path.

A finished run's answer is posted as the agent's line at the place it was
asked (``surface.reply_for_run``), with a ``channel_message_deliveries`` row
for that place; ``surface.channel_delivery`` sends it and records the
platform's id for the message on the row. Against a real database, the real
surface service and the fake platform servers:

* the run's finish told twice is one line and one send, whose row carries the
  platform's message id;
* a send whose sender died before recording it is never sent again: ``DEAD``
  saying it may not have arrived -- or ``SENT``, when the platform's id was
  noted before the call (a stream the answer closes);
* WhatsApp's later report that a message never arrived (131047) finds the
  delivery by the id it recorded and marks it ``DEAD`` with the reason;
* a Slack answer that streamed is one line whose delivery is the stream's
  message: ``SENT`` with the stream's ``ts``, and nothing posted again.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass, WorkRegistry, facts
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.events.channel_answer_work import register_channel_answer_work
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.reply_for_run_work import (
    register_reply_for_run_work,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _seed_external_user,
    _set_user_mobile_number,
    _telegram_payload,
    _whatsapp_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_telegram_secret_headers,
    build_whatsapp_signature_headers,
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_progress,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import stage_surface
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    a_telegram_bot,
    clean_surface_work,  # noqa: F401  (fixture)
    deliveries_of_line,
    drain,
    scalar,
    surface_registry,
)
from app.modules.channels.contracts import DeliveryStatus, NewLine, Sender, post
from app.modules.channels.contracts.deliveries import claim_delivery, delivery_noted
from app.modules.channels.infrastructure.deliveries import CLAIM_LEASE, INTERRUPTED
from app.modules.channels.infrastructure.models import (
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
)

# Deliveries are claimed in-process here; a session worker taking the same
# items would race what these count.
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work"),
]

ANSWER = "Three invoices are due."


# ------------------------------------------------------------------- harness


async def _finished(run_id: UUID, conversation_id: UUID, pod_id: UUID) -> None:
    """The finalize's fact, as the agent module emits it."""
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await facts.emit(
            uow,
            AgentRunFinished(
                run_id=run_id,
                conversation_id=conversation_id,
                pod_id=pod_id,
                status="COMPLETED",
                conversation_status="COMPLETED",
                origin="surface",
            ),
        )
        await uow.commit()


async def _a_finished_run(
    client: AsyncClient, db_session: AsyncSession, test_pod, user, fake_telegram, name
) -> tuple[UUID, UUID]:
    """A real Telegram conversation whose run finished with ``ANSWER``, and the
    fact that says so -- not yet worked."""
    surface_id, secret, _ = await a_telegram_bot(
        db_session,
        client,
        pod_id=test_pod["id"],
        user_id=user["id"],
        api_base=fake_telegram.api_base,
        bot_token=f"e2e-{name}-token",
        surface_name=name,
    )
    sender = 6130000 + len(name)
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender),
        resolved_user_id=UUID(user["id"]),
    )
    response = await client.post(
        f"/surfaces/{surface_id}/webhook",
        content=json.dumps(
            _telegram_payload(
                text="What is due?", message_id=7800 + len(name), sender_id=sender
            )
        ).encode(),
        headers=build_telegram_secret_headers(secret),
    )
    assert response.status_code == 200
    await drain(surface_registry())
    conversation_id = await scalar(
        "SELECT conversation_id FROM surface_inbound_messages WHERE surface_id = :s",
        s=surface_id,
    )
    run_id = await scalar(
        "SELECT id FROM agent_runs WHERE conversation_id = :c", c=conversation_id
    )
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE agent_runs SET status = 'COMPLETED', "
                "output_data = jsonb_build_object('answer', CAST(:a AS text)) "
                "WHERE id = :r"
            ),
            {"r": run_id, "a": ANSWER},
        )
        await session.commit()
    await _finished(run_id, conversation_id, UUID(test_pod["id"]))
    return conversation_id, run_id


async def _answer_lines(run_id: UUID) -> list[ChannelMessageModel]:
    async with async_session_maker() as session:
        return list(
            (
                await session.scalars(
                    select(ChannelMessageModel).where(
                        ChannelMessageModel.agent_run_id == run_id
                    )
                )
            ).all()
        )


def _telegram_answers(message_store) -> list[dict]:
    return [
        item
        for item in message_store.get_all("TELEGRAM")
        if "Three invoices are due" in json.dumps(item)
    ]


def _lines_only_registry() -> WorkRegistry:
    """The answer's line is written; nothing sends it."""
    registry = WorkRegistry()
    register_reply_for_run_work(registry)
    return registry


# --------------------------------------------------------------- one send


async def test_a_run_finished_twice_is_one_line_and_one_send(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    conversation_id, run_id = await _a_finished_run(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "twice-bot",
    )
    await drain(surface_registry())
    # Told again -- a redelivered fact, a retried finalize.
    await _finished(run_id, conversation_id, UUID(test_pod["id"]))
    await drain(surface_registry(), due=True)

    [line] = await _answer_lines(run_id)
    assert line.body == ANSWER
    assert line.sender_kind == "AGENT"
    assert line.client_message_id == f"run:{run_id}"
    [delivery] = await deliveries_of_line(line.id)
    assert delivery.status is DeliveryStatus.SENT
    assert delivery.platform == "TELEGRAM"
    # The platform's own id for the message, which its later reports name.
    assert delivery.external_message_id
    assert len(_telegram_answers(message_store)) == 1, message_store.get_all("TELEGRAM")
    # No second record of the send: the answer is not a surface_deliveries row.
    assert await scalar("SELECT count(*) FROM surface_deliveries") == 0


# -------------------------------------------------------- send-then-crash


@pytest.mark.parametrize("noted", [False, True], ids=["id-unknown", "id-noted"])
async def test_a_send_whose_sender_died_unrecorded_is_never_sent_again(
    noted,
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A sender claimed the answer's delivery, (perhaps) sent it, and died
    before recording what happened. Whoever finds the lapsed claim cannot know
    whether it arrived: it is not sent again. Known to have arrived -- its
    platform id was noted before the call -- it is ``SENT``; otherwise
    ``DEAD``, saying so, for a member to retry."""
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    _conversation, run_id = await _a_finished_run(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        f"died-{noted}-bot",
    )
    await drain(_lines_only_registry())
    [line] = await _answer_lines(run_id)
    [planned] = await deliveries_of_line(line.id)
    # The dead sender's claim -- taken, perhaps sent, never recorded.
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        claimed = await claim_delivery(uow, planned.id)
        assert claimed is not None
        if noted:
            await delivery_noted(uow, planned.id, external_message_id="stream-1")
        await uow.commit()
    # Its lease runs out.
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelMessageDeliveryModel)
            .where(ChannelMessageDeliveryModel.id == planned.id)
            .values(next_attempt_at=datetime.now(UTC) - CLAIM_LEASE)
        )
        await session.commit()
    sent_before = len(_telegram_answers(message_store))

    await drain(surface_registry(), due=True)

    [settled] = await deliveries_of_line(line.id)
    if noted:
        assert settled.status is DeliveryStatus.SENT
        assert settled.external_message_id == "stream-1"
    else:
        assert settled.status is DeliveryStatus.DEAD
        assert settled.reason == INTERRUPTED
    assert len(_telegram_answers(message_store)) == sent_before, "sent again"


# --------------------------------------------------- a late failure, by id


async def test_whatsapp_reporting_131047_marks_the_delivery_it_names(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.client._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    await _create_agent_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )
    await _set_user_mobile_number(
        db_session, user_id=fixed_test_user["id"], mobile_number="15550555556"
    )
    payload = _whatsapp_payload(
        text="Anything due?",
        message_id="wamid-e2e-131047",
        phone_number_id="1234567890",
        waba_id="waba-001",
        sender_phone="15550555556",
    )
    await _webhook(authenticated_client, payload)
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="whatsapp", payload=payload, headers={}),
        script=[script_text("Nothing is due today.")],
    )
    [line] = [
        line
        for line in await _conversation_lines(context.conversation_id)
        if line.agent_run_id is not None
    ]
    [delivery] = await deliveries_of_line(line.id)
    assert delivery.status is DeliveryStatus.SENT
    wamid = delivery.external_message_id
    assert wamid and wamid.startswith("wamid.")

    # Meta took it with a 200, and now says it never arrived.
    await _webhook(authenticated_client, _failed_status(wamid, code=131047))
    await drain(surface_registry())

    [failed] = await deliveries_of_line(line.id)
    assert failed.status is DeliveryStatus.DEAD
    assert failed.reason is not None and "24 hours" in failed.reason
    assert failed.external_message_id == wamid


async def _webhook(client: AsyncClient, payload: dict) -> None:
    raw = json.dumps(payload).encode()
    response = await client.post(
        "/surfaces/webhooks/whatsapp",
        content=raw,
        headers=build_whatsapp_signature_headers(raw_body=raw, app_secret="wa-secret"),
    )
    assert response.status_code == 200, response.text


def _failed_status(message_id: str, *, code: int) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-001",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "1234567890"},
                            "statuses": [
                                {
                                    "id": message_id,
                                    "status": "failed",
                                    "recipient_id": "15550555556",
                                    "timestamp": "1700000100",
                                    "errors": [
                                        {
                                            "code": code,
                                            "title": "Re-engagement message",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


async def _conversation_lines(conversation_id: UUID) -> list[ChannelMessageModel]:
    async with async_session_maker() as session:
        return list(
            (
                await session.scalars(
                    select(ChannelMessageModel)
                    .where(ChannelMessageModel.conversation_id == conversation_id)
                    .order_by(ChannelMessageModel.seq)
                )
            ).all()
        )


# ------------------------------------------------------ a streamed answer


async def test_a_streamed_slack_answer_is_one_line_sent_as_the_stream(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
):
    """The answer streamed into one Slack message while it was written; its
    delivery finishes that message and is it -- ``SENT`` with the stream's
    ``ts`` -- and no second message carries the answer."""
    from app.modules.agent_surfaces.services import progress_display

    monkeypatch.setattr(progress_display, "_MIN_TEXT_PROGRESS_INTERVAL_SECONDS", 0.0)
    final = "Here is the streamed answer."
    stage = await stage_surface(
        SurfacePlatform.SLACK,
        fake=platform_fake[SurfacePlatform.SLACK],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )

    context = await stage.say(
        "do some work",
        script=script_progress(
            ["Looking"],
            final_text=final,
            tool_name="slack_get_recent_channel_messages",
        ),
    )

    stops = await wait_for_messages(message_store, "SLACK_STREAM_STOP", min_count=1)
    stream_ts = stops[-1]["ts"]
    answers = [
        line
        for line in await _conversation_lines(context.conversation_id)
        if line.agent_run_id is not None and line.sender_kind == "AGENT"
    ]
    assert [line.body for line in answers] == [final]
    [delivery] = await deliveries_of_line(answers[0].id)
    assert delivery.status is DeliveryStatus.SENT
    assert delivery.external_message_id == stream_ts
    posted = [
        call
        for call in message_store.get_all("SLACK")
        if "streamed answer" in json.dumps(call)
    ]
    assert posted == [], "the streamed answer was posted a second time"
    assert len(message_store.get_all("SLACK_STREAM_STOP")) == 1


# ------------------------------------------- asked in Lemma, heard on a platform


async def test_an_answer_asked_in_lemma_is_not_sent_to_the_platform(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """The member wrote to the bot on Telegram, then named it in Lemma, in
    their own channel: one conversation, since it is one person and one
    agent. The answer to the line in Lemma is said in Lemma -- the Telegram
    chat, which never saw the question, is sent nothing."""
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    conversation_id, _ = await _a_finished_run(
        authenticated_client,
        db_session,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "lemma-too-bot",
    )
    await drain(surface_registry(), due=True)
    assert len(_telegram_answers(message_store)) == 1

    channel_id = await scalar(
        "SELECT channel_id FROM channel_messages WHERE conversation_id = :c LIMIT 1",
        c=conversation_id,
    )
    agent_id = await scalar(
        "SELECT agent_id FROM agent_conversations WHERE id = :c", c=conversation_id
    )
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        line = await post(
            uow,
            channel_id=channel_id,
            line=NewLine(
                sender=Sender.member(UUID(fixed_test_user["id"])),
                text="And what is due next week?",
            ),
            mentioned_agents=[agent_id],
        )
        await uow.commit()
    assert line is not None
    answers = WorkRegistry()
    register_channel_answer_work(answers)
    await make_due(async_session_maker)
    with suppress_agent_run_enqueue():
        await run_until_idle(
            async_session_maker, registry=answers, classes=[WorkClass.INTERACTIVE]
        )
    asked_in = await scalar(
        "SELECT conversation_id FROM channel_line_answers WHERE message_id = :m",
        m=line.id,
    )
    assert asked_in == conversation_id, "the member's one conversation with it"
    run_id = await scalar(
        "SELECT agent_run_id FROM channel_line_answers WHERE message_id = :m",
        m=line.id,
    )
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE agent_runs SET status = 'COMPLETED', "
                "output_data = jsonb_build_object('answer', CAST(:a AS text)) "
                "WHERE id = :r"
            ),
            {"r": run_id, "a": ANSWER},
        )
        await session.commit()
    await _finished(run_id, conversation_id, UUID(test_pod["id"]))
    await drain(surface_registry(), due=True)

    assert len(_telegram_answers(message_store)) == 1, message_store.get_all("TELEGRAM")
