"""The reads behind a pod's list of groups, each asked for every group at once.

The list shows, for every group, who has spoken there and what is waiting on
the person reading. Asked one group at a time that was a set of queries per
group -- a few thousand for a busy pod -- so each read here takes all of the
pod's groups and answers in one statement.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.groups import OUTSIDERS_LINK_USER, SurfaceGroup
from app.modules.agent_surfaces.domain.notification import NotificationStatus
from app.modules.agent_surfaces.infrastructure.group_models import (
    AgentSurfaceGroupMessageModel,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
    AgentSurfaceExternalUser,
    NotificationModel,
)

#: Ceilings on what one list read returns. A pod lists at most a few hundred
#: groups, each a chat a person can read.
_MAX_SPEAKERS = 5000
_MAX_CONVERSATIONS = 2000


@dataclass(frozen=True, slots=True)
class GroupSpeaker:
    """Somebody who has said something in a group the pod keeps a log of."""

    group_id: UUID
    external_id: str
    name: str | None
    #: The Lemma user they proved to be, when they have.
    user_id: UUID | None
    last_said_at: datetime


class GroupPageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def speakers(self, groups: Sequence[SurfaceGroup]) -> list[GroupSpeaker]:
        """Everyone who has spoken in these groups, once per group."""
        if not groups:
            return []
        message = AgentSurfaceGroupMessageModel
        rows = (
            await self.session.execute(
                select(
                    message.group_id,
                    message.author_external_id,
                    func.max(message.author_name),
                    func.max(message.created_at),
                )
                .where(
                    message.group_id.in_([group.id for group in groups]),
                    message.from_agent.is_(False),
                    message.author_external_id.is_not(None),
                )
                .group_by(message.group_id, message.author_external_id)
                .limit(_MAX_SPEAKERS)
            )
        ).all()
        platform_of = {group.id: group.platform for group in groups}
        by_platform: dict[str, set[str]] = defaultdict(set)
        for group_id, external_id, _, _ in rows:
            by_platform[platform_of[group_id]].add(external_id)
        proven = {
            platform: await self._proven_users(platform, ids)
            for platform, ids in by_platform.items()
        }
        return [
            GroupSpeaker(
                group_id=group_id,
                external_id=external_id,
                name=name,
                user_id=proven[platform_of[group_id]].get(external_id),
                last_said_at=last_said_at,
            )
            for group_id, external_id, name, last_said_at in rows
        ]

    async def outsider_conversations(
        self, groups: Sequence[SurfaceGroup]
    ) -> dict[UUID, list[UUID]]:
        """Each group's conversations with its people outside the pod."""
        keyed = {
            (group.surface_id, group.external_channel_id): group.id
            for group in groups
            if group.external_channel_id
        }
        if not keyed:
            return {}
        link = AgentSurfaceConversationLinkModel
        rows = (
            await self.session.execute(
                select(link.surface_id, link.external_channel_id, link.conversation_id)
                .where(
                    tuple_(link.surface_id, link.external_channel_id).in_(list(keyed)),
                    link.external_user_id == OUTSIDERS_LINK_USER,
                )
                .limit(_MAX_CONVERSATIONS)
            )
        ).all()
        found: dict[UUID, list[UUID]] = defaultdict(list)
        for surface_id, channel_id, conversation_id in rows:
            found[keyed[(surface_id, channel_id)]].append(conversation_id)
        return dict(found)

    async def open_asks_by_conversation(
        self, *, recipient_user_id: UUID, conversation_ids: Sequence[UUID]
    ) -> dict[UUID, int]:
        """How many questions each conversation has open with this person."""
        if not conversation_ids:
            return {}
        notification = NotificationModel
        rows = (
            await self.session.execute(
                select(notification.origin_conversation_id, func.count())
                .where(
                    notification.recipient_user_id == recipient_user_id,
                    notification.origin_conversation_id.in_(list(conversation_ids)),
                    notification.expects_response.is_(True),
                    notification.status == NotificationStatus.OPEN.value,
                )
                .group_by(notification.origin_conversation_id)
                .limit(len(conversation_ids))
            )
        ).all()
        return dict(rows)

    async def _proven_users(self, platform: str, ids: set[str]) -> dict[str, UUID]:
        external = AgentSurfaceExternalUser
        rows = (
            await self.session.execute(
                select(external.external_user_id, external.resolved_user_id)
                .where(
                    external.platform == platform,
                    external.external_user_id.in_(sorted(ids)),
                    external.resolved_user_id.is_not(None),
                )
                .limit(len(ids) * 4)
            )
        ).all()
        return {
            external_id: user_id
            for external_id, user_id in rows
            if external_id and user_id is not None
        }
