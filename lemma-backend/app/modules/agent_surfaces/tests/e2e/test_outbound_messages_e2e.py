"""What the bot sent, written down and read back by the platform's id.

Against Postgres because each guarantee here is a statement's, not a
function's: the unique index that keeps a status delivered twice to one row,
the ``WHERE status != 'FAILED'`` that makes acting on a failure happen once,
the retention sweep's batch delete, and the single-statement JSONB merge two
taps on a multi-question card race through.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.outbound_models import (
    AgentSurfaceOutboundMessageModel,
)
from app.modules.agent_surfaces.infrastructure.repositories.outbound_message_repository import (
    SurfaceOutboundMessageRepository,
    prune_outbound_messages,
)
from app.modules.agent_surfaces.services.partial_answers import forget_answers

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _surface_and_conversation(sessions, test_pod, fixed_test_user):
    async with sessions() as session:
        surface = AgentSurface(
            pod_id=UUID(test_pod["id"]),
            organization_id=UUID(test_pod["organization_id"]),
            agent_id=UUID(test_pod["id"]),
            name=f"whatsapp-{uuid4().hex[:6]}",
            surface_type="WHATSAPP",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        conversation = ConversationModel(
            user_id=fixed_test_user["id"], pod_id=UUID(test_pod["id"])
        )
        session.add_all([surface, conversation])
        await session.flush()
        ids = surface.id, conversation.id
        await session.commit()
    return ids


async def test_a_send_is_recorded_once_and_its_failure_acted_on_once(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    surface_id, conversation_id = await _surface_and_conversation(
        sessions, test_pod, fixed_test_user
    )
    first, second = f"wamid.{uuid4().hex}", f"wamid.{uuid4().hex}"

    for _ in range(2):  # a retried send records the same ids again
        async with sessions() as session:
            await SurfaceOutboundMessageRepository(session).record_sent(
                surface_id=surface_id,
                platform="WHATSAPP",
                external_message_ids=[first, second],
                kind="REPLY",
                conversation_id=conversation_id,
                recipient="447700900123",
                body="Here is the summary.",
            )
            await session.commit()

    async with sessions() as session:
        repository = SurfaceOutboundMessageRepository(session)
        head = await repository.get_by_external_id(
            platform="WHATSAPP", external_message_id=first
        )
        part = await repository.get_by_external_id(
            platform="WHATSAPP", external_message_id=second
        )
        assert head is not None and part is not None
        assert (head.kind, part.kind) == ("REPLY", "REPLY_PART")
        assert head.body == "Here is the summary."
        assert await repository.mark_failed(head.id, error="131047 Re-engagement")
        assert not await repository.mark_failed(head.id, error="131047 again")
        await session.commit()


async def test_sends_past_the_window_are_pruned(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    surface_id, conversation_id = await _surface_and_conversation(
        sessions, test_pod, fixed_test_user
    )
    old, recent = f"wamid.{uuid4().hex}", f"wamid.{uuid4().hex}"
    async with sessions() as session:
        repository = SurfaceOutboundMessageRepository(session)
        for message_id in (old, recent):
            await repository.record_sent(
                surface_id=surface_id,
                platform="WHATSAPP",
                external_message_ids=[message_id],
                kind="REPLY",
                conversation_id=conversation_id,
            )
        await session.execute(
            update(AgentSurfaceOutboundMessageModel)
            .where(AgentSurfaceOutboundMessageModel.external_message_id == old)
            .values(created_at=datetime.now(timezone.utc) - timedelta(days=31))
        )
        await session.commit()

    assert await prune_outbound_messages(sessions) >= 1

    async with sessions() as session:
        repository = SurfaceOutboundMessageRepository(session)
        assert (
            await repository.get_by_external_id(
                platform="WHATSAPP", external_message_id=old
            )
            is None
        )
        assert await repository.get_by_external_id(
            platform="WHATSAPP", external_message_id=recent
        )


async def test_two_answers_merged_at_once_are_both_kept(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    _, conversation_id = await _surface_and_conversation(
        sessions, test_pod, fixed_test_user
    )

    async def answer(header: str, value: str) -> None:
        async with sessions() as session:
            await agent_conversations.merge_conversation_metadata_mapping(
                SqlAlchemyUnitOfWork(session),
                conversation_id,
                "surface_partial_answers",
                {f"call-1|{header}": value},
            )
            await session.commit()

    await asyncio.gather(answer("Size", "Small"), answer("Colour", "Blue"))

    async with sessions() as session:
        held = await agent_conversations.merge_conversation_metadata_mapping(
            SqlAlchemyUnitOfWork(session),
            conversation_id,
            "surface_partial_answers",
            {},
        )
    assert held == {"call-1|Size": "Small", "call-1|Colour": "Blue"}

    # Cleared, then merged into again: the null a clear leaves is not an object
    # and must not turn the next merge into an array.
    async with sessions() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await forget_answers(uow, conversation_id=conversation_id)
        held = await agent_conversations.merge_conversation_metadata_mapping(
            uow, conversation_id, "surface_partial_answers", {"call-2|Size": "Large"}
        )
        await session.commit()
    assert held == {"call-2|Size": "Large"}
