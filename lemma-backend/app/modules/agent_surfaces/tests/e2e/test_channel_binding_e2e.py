"""A person has one conversation per agent in a channel -- end to end.

The binder places every thread on a channel source and continues the person's
conversation there, against the real tables: the source keyed by the person
for a private chat, the channel by whose it is, the conversation by
(channel, agent, person). So a chat that moves -- another pool number, another
of the pod's bots, another platform, a week's silence -- is the same
conversation, and only a different agent or a different person opens another.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.agent_surfaces.composition import build_conversation_binder
from app.modules.agent_surfaces.services.agent_naming import agent_name_for_agent_id
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _create_agent
from app.modules.channels.contracts import ChannelKind, channel_by_id, source_by_id

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

PERSON = "tg-41"


@dataclass(frozen=True)
class Bound:
    conversation_id: UUID
    channel_id: UUID
    source_id: UUID
    created: bool


class _Pod:
    def __init__(self, db_session: AsyncSession, pod: dict, user: dict) -> None:
        self.session = db_session
        self.pod_id = UUID(pod["id"])
        self.organization_id = UUID(pod["organization_id"])
        self.user_id = UUID(user["id"])

    async def bot(
        self, platform: SurfacePlatform, *, agent_id: UUID | None = None
    ) -> AgentSurfaceEntity:
        row = AgentSurface(
            pod_id=self.pod_id,
            organization_id=self.organization_id,
            agent_id=agent_id or self.pod_id,
            name=f"{platform.value.lower()}-{uuid4().hex[:6]}",
            surface_type=platform.value,
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        self.session.add(row)
        await self.session.commit()
        return row.to_entity()

    async def says(
        self,
        surface: AgentSurfaceEntity,
        *,
        chat: str,
        person: str = PERSON,
        agent_id: UUID | None = None,
    ) -> Bound:
        """``person`` writes privately to ``surface``, arriving on ``chat``."""
        uow = SqlAlchemyUnitOfWork(self.session)
        agent = agent_id or surface.agent_id or self.pod_id
        bound = await build_conversation_binder(uow).bind_conversation(
            surface=surface,
            parsed=ParsedInboundSurfaceEvent(
                platform=surface.surface_type,
                conversation_type=ConversationType.EXTERNAL_DM,
                external_channel_id=chat,
                external_thread_id=chat,
                external_message_id=uuid4().hex,
                sender_external_user_id=person,
                message_text="hello again",
                is_dm=True,
            ),
            resolved_user=ResolvedSurfaceUser(
                internal_user_id=self.user_id, external_user_id=person
            ),
            # Named, as routing names it: the conversation is opened by name.
            route=ResolvedSurfaceRoute(
                pod_id=self.pod_id,
                agent_id=agent,
                agent_name=await agent_name_for_agent_id(uow, agent),
                agent_display_name="Assistant",
                conversation_kind="DM",
                route_key="dm",
            ),
        )
        await uow.commit()
        source = await source_by_id(uow, bound.source_id)
        assert source is not None
        return Bound(
            conversation_id=bound.conversation_id,
            channel_id=source.channel_id,
            source_id=source.id,
            created=bound.created_title is not None,
        )

    async def conversations(self) -> list[ConversationModel]:
        return list(
            await self.session.scalars(
                select(ConversationModel).where(
                    ConversationModel.pod_id == self.pod_id,
                    ConversationModel.user_id == self.user_id,
                    ConversationModel.channel_id.is_not(None),
                )
            )
        )


@pytest.fixture
def pod(db_session, test_pod, fixed_test_user) -> _Pod:
    return _Pod(db_session, test_pod, fixed_test_user)


async def test_a_members_first_message_opens_their_direct_channel(pod):
    telegram = await pod.bot(SurfacePlatform.TELEGRAM)

    first = await pod.says(telegram, chat="100")

    assert first.created
    channel = await channel_by_id(SqlAlchemyUnitOfWork(pod.session), first.channel_id)
    assert channel is not None
    assert (channel.kind, channel.member_user_id) == (ChannelKind.DIRECT, pod.user_id)
    [conversation] = await pod.conversations()
    assert conversation.channel_id == first.channel_id


async def test_a_chat_that_arrives_on_another_number_is_the_same_conversation(pod):
    """A pool number reassigned: a new address, the same person, the same place."""
    whatsapp = await pod.bot(SurfacePlatform.WHATSAPP)
    before = await pod.says(whatsapp, chat="15550100")

    after = await pod.says(whatsapp, chat="15550199")

    assert after.conversation_id == before.conversation_id
    assert after.source_id == before.source_id
    assert not after.created


async def test_one_person_on_two_platforms_is_one_relationship(pod):
    """Two bots, two sources, one direct channel -- and one conversation."""
    telegram = await pod.bot(SurfacePlatform.TELEGRAM)
    whatsapp = await pod.bot(SurfacePlatform.WHATSAPP)
    on_telegram = await pod.says(telegram, chat="100")

    on_whatsapp = await pod.says(whatsapp, chat="15550100")

    assert on_whatsapp.source_id != on_telegram.source_id
    assert on_whatsapp.channel_id == on_telegram.channel_id
    assert on_whatsapp.conversation_id == on_telegram.conversation_id
    assert len(await pod.conversations()) == 1


async def test_another_agent_is_another_conversation_in_the_same_channel(
    pod, authenticated_client, test_pod
):
    telegram = await pod.bot(SurfacePlatform.TELEGRAM)
    other = UUID((await _create_agent(authenticated_client, test_pod["id"]))["id"])
    with_the_assistant = await pod.says(telegram, chat="100")

    with_the_other = await pod.says(telegram, chat="100", agent_id=other)

    assert with_the_other.channel_id == with_the_assistant.channel_id
    assert with_the_other.conversation_id != with_the_assistant.conversation_id
    assert with_the_other.created


async def test_a_member_under_a_second_platform_account_is_still_them(pod):
    """Two Telegram accounts resolved to one member: two sources, one channel."""
    telegram = await pod.bot(SurfacePlatform.TELEGRAM)
    first_account = await pod.says(telegram, chat="100")

    second_account = await pod.says(telegram, chat="101", person="tg-42")

    assert second_account.source_id != first_account.source_id
    assert second_account.channel_id == first_account.channel_id
    assert second_account.conversation_id == first_account.conversation_id
