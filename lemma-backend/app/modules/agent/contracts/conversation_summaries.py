"""What other modules may know about one person's conversations across many pods.

An organization's overview wants, for every pod a member can open, what is
waiting on them, their latest chats and how busy each pod has been. Asked per
pod that is a request per pod per question; each read here is one statement
for the whole set.

**The pods are the caller's to have authorized.** Membership is the
precondition of conversation access (``CONVERSATION_ENUMERATION``), so a caller
passes only pods it has already found the person may open -- the identity
module passes ``pod.contracts.list_visible_pods``. Inside those pods every read
is of the person's own conversations, the same rows the per-pod lists return,
and never another member's.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.listed_origin import (
    CHATS,
    STARTED_BY_AN_AGENT,
    ListedOrigin,
)
from app.modules.agent.domain.pending_interactions import (
    PendingInteraction,
    PendingInteractionKind,
    PodPendingInteraction,
)
from app.modules.agent.domain.recent_conversations import DayActivity, RecentChat
from app.modules.agent.infrastructure.db import recent_conversations
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent_surfaces.contracts.conversations import conversation_surfaces


async def list_pending_interactions_in_pods(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    pod_ids: Collection[UUID],
    limit: int,
) -> tuple[list[PodPendingInteraction], bool]:
    """This person's pauses across these pods, longest-waiting first, and
    whether more were waiting than ``limit``: the per-pod pending list's rule,
    in one statement."""
    return await Pauses(uow).waiting_on_person_in_pods(
        user_id=user_id, pod_ids=pod_ids, limit=limit
    )


async def recent_chats_by_pod(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    pod_ids: Sequence[UUID],
    per_pod: int,
) -> dict[UUID, list[RecentChat]]:
    """Each pod's latest ``per_pod`` chats of this person's, newest first, each
    with the platform and channel name it was heard on, if any.

    Where a chat was heard is agent_surfaces' record, not the conversation's,
    so it is read for the whole page at once after the chats are.
    """
    by_pod = await recent_conversations.recent_chats_by_pod(
        uow.session, user_id=user_id, pod_ids=pod_ids, per_pod=per_pod
    )
    heard = await conversation_surfaces(
        uow, [chat.conversation_id for chats in by_pod.values() for chat in chats]
    )
    return {
        pod_id: [
            replace(
                chat,
                platform=surface.surface_platform,
                channel_name=surface.channel_name,
            )
            if (surface := heard.get(chat.conversation_id)) is not None
            else chat
            for chat in chats
        ]
        for pod_id, chats in by_pod.items()
    }


async def daily_activity_by_pod(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    pod_ids: Sequence[UUID],
    since: datetime,
    zone: str,
) -> list[DayActivity]:
    """How many of this person's conversations were last active each day in
    ``zone``, by pod, split by whether an agent started them."""
    return await recent_conversations.daily_activity_by_pod(
        uow.session, user_id=user_id, pod_ids=pod_ids, since=since, zone=zone
    )


__all__ = [
    "CHATS",
    "DayActivity",
    "ListedOrigin",
    "PendingInteraction",
    "PendingInteractionKind",
    "PodPendingInteraction",
    "RecentChat",
    "STARTED_BY_AN_AGENT",
    "daily_activity_by_pod",
    "list_pending_interactions_in_pods",
    "recent_chats_by_pod",
]
