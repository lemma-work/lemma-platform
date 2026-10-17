"""The reads behind a pod's list of groups, each asked for every group at once.

The list shows, for every group, what is waiting on the person reading; who
has spoken there is the group's channel's to say. Asked one group at a time that was a set of queries per
group -- a few thousand for a busy pod -- so each read here takes all of the
pod's groups and answers in one statement.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.notification import NotificationStatus
from app.modules.agent_surfaces.infrastructure.models import NotificationModel


class GroupPageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
