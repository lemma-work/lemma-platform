"""Reaching a WhatsApp member whose 24-hour window has closed.

Past 24 hours since their last message, WhatsApp takes only an approved
template from a business; anything else is accepted and dropped. Without a
template on the surface, a message to them is refused, as before. With one,
the template asks them to reply and the message is held on the conversation;
their reply reopens the window and the held message arrives first, once.

Driven through the real send endpoint, ingress and run path against Postgres,
with the WhatsApp fake recording every message the Graph API was given.
"""

from __future__ import annotations

import asyncio

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.modules.agent.infrastructure.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.agent_surfaces.domain.entities import (
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.held_replies import (
    HELD_REPLIES_KEY,
    HeldReplies,
    hold_reply,
    stage_held_replies,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import script_text
from app.modules.agent_surfaces.tests.e2e.surface_journey import stage_surface

pytestmark = pytest.mark.e2e

TEMPLATE = "lemma_reengage"
FIRST = "Your weekly report is ready."
SECOND = "And the invoice went out."


async def _close_the_window(db_session: AsyncSession, conversation_id: UUID) -> None:
    """Their last message, a day and an hour ago."""
    await db_session.execute(
        text(
            "UPDATE agent_surface_conversation_links "
            "SET last_inbound_at = now() - interval '25 hours' "
            "WHERE conversation_id = :conversation_id"
        ),
        {"conversation_id": conversation_id},
    )
    await db_session.commit()


def _bodies(store) -> list[str]:
    return [
        str((message.get("text") or {}).get("body") or "")
        for message in store.get_all("WHATSAPP")
        if message.get("type") == "text"
    ]


def _templates(store) -> list[dict[str, Any]]:
    return [
        message["template"]
        for message in store.get_all("WHATSAPP")
        if message.get("type") == "template"
    ]


async def test_a_closed_window_is_reopened_with_a_template_and_the_message_waits(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await stage_surface(
        SurfacePlatform.WHATSAPP,
        fake=platform_fake[SurfacePlatform.WHATSAPP],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    context = await stage.say("hello", script=[script_text("Hi there.")])
    assert await stage.saw("Hi there.")
    await _close_the_window(db_session, context.conversation_id)
    surface_path = f"/pods/{stage.pod_id}/surfaces/{stage.surface['name']}"

    async def send(message: str):
        return await authenticated_client.post(
            f"{surface_path}/send",
            json={"user_id": fixed_test_user["id"], "message": message},
        )

    # No template: refused, as before -- Meta would take it and drop it.
    refused = await send(FIRST)
    assert refused.status_code == 404, refused.text
    assert UndeliverableReason.REPLY_WINDOW_CLOSED in refused.text, refused.text
    assert _templates(message_store) == []

    configured = await authenticated_client.patch(
        surface_path,
        json={"config": {"whatsapp": {"reengage_template": TEMPLATE}}},
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["config"]["whatsapp"]["reengage_template"] == TEMPLATE

    # With one: the template goes, the message waits. A second message the
    # same day waits beside it rather than asking again.
    for message in (FIRST, SECOND):
        sent = await send(message)
        assert sent.status_code == 200, sent.text
    assert _templates(message_store) == [
        {"name": TEMPLATE, "language": {"code": "en_US"}}
    ]
    assert FIRST not in _bodies(message_store), "sent into a closed window"

    # Their reply reopens the window: what waited arrives first, in order.
    await stage.say("sure, go ahead", script=[script_text("Anything else?")])
    assert await stage.saw("Anything else?")
    bodies = _bodies(message_store)
    held = [bodies.index(FIRST), bodies.index(SECOND)]
    assert held == sorted(held) and held[-1] < bodies.index("Anything else?")

    # Delivered once: the next message finds nothing held.
    await stage.say("thanks", script=[script_text("You're welcome.")])
    assert await stage.saw("You're welcome.")
    assert _bodies(message_store).count(FIRST) == 1


async def _held_messages(
    uow_factory: UnitOfWorkFactory, conversation_id: UUID
) -> list[str]:
    async with uow_factory() as uow:
        record = await ConversationRepository(uow).get_conversation_metadata_key(
            conversation_id, HELD_REPLIES_KEY
        )
    if record is None:
        return []
    return [reply.message for reply in HeldReplies.model_validate(record).replies]


async def _staged_held(conversation_id: UUID) -> list[str]:
    async with async_session_maker() as session:
        rows = await session.scalars(
            text(
                "SELECT content->>'text' FROM surface_deliveries "
                "WHERE conversation_id = :conversation "
                "AND content->>'purpose' = 'held' ORDER BY created_at, id"
            ),
            {"conversation": conversation_id},
        )
        return list(rows.all())


async def test_a_message_held_while_the_hold_is_staged_is_kept_for_next_time(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    """Staging clears what it staged, never a message held meanwhile.

    The member's reply stages what was held as deliveries in its ingest's
    transaction, under the conversation's row lock. A second message held
    while that transaction is open waits for the lock, then starts a new set:
    it is not cleared unsent, and the next reply stages it -- once.
    """
    stage = await stage_surface(
        SurfacePlatform.WHATSAPP,
        fake=platform_fake[SurfacePlatform.WHATSAPP],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    context = await stage.say("hello", script=[script_text("Hi there.")])
    assert await stage.saw("Hi there.")
    conversation_id = context.conversation_id
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    async with uow_factory() as uow:
        await hold_reply(uow, conversation_id, FIRST, asked=True)

    async with uow_factory() as staging:
        staged = await stage_held_replies(
            staging,
            conversation_id=conversation_id,
            pod_id=UUID(str(test_pod["id"])),
            platform=SurfacePlatform.WHATSAPP,
        )
        assert staged == 1

        async def hold_second() -> None:
            async with uow_factory() as uow:
                await hold_reply(uow, conversation_id, SECOND, asked=False)
                await uow.commit()

        racing = asyncio.create_task(hold_second())
        await asyncio.sleep(0.3)
        assert not racing.done(), "the second hold did not wait for the staging"
        await staging.commit()
    await asyncio.wait_for(racing, timeout=10)

    assert await _staged_held(conversation_id) == [FIRST]
    assert await _held_messages(uow_factory, conversation_id) == [SECOND], (
        "the message held mid-staging was cleared without being staged"
    )

    async with uow_factory() as uow:
        assert (
            await stage_held_replies(
                uow,
                conversation_id=conversation_id,
                pod_id=None,
                platform=SurfacePlatform.WHATSAPP,
            )
            == 1
        )
        await uow.commit()
    assert await _staged_held(conversation_id) == [FIRST, SECOND]
    assert await _held_messages(uow_factory, conversation_id) == []
