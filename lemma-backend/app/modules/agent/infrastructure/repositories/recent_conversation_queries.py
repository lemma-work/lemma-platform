"""One person's conversations across many pods: the latest chats, and a count a day.

Both read ``ix_agent_conv_user_pod_roots_activity`` -- ``(user_id, pod_id,
last_activity_at, id)`` over root conversations -- and both are one statement
however many pods are asked about. Asking per pod is what an organization's
overview did before these existed, and it read only each pod's first page, so a
busy pod's fortnight was counted from its newest twenty-five conversations.

Only the person's own conversations, which is all the per-pod list ever shows
them: a conversation belongs to the member who started it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Date,
    Uuid,
    and_,
    case,
    cast,
    column,
    func,
    or_,
    select,
    true,
    values,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.conversation_origin import (
    AUDIENCE_KEY,
    CHATS,
    OUTSIDERS,
    RESOURCE_KEY,
    SOURCE_ORIGINS,
    STARTED_BY_AN_AGENT,
    TASK_TYPE,
    ConversationOrigin,
)
from app.modules.agent.domain.recent_conversations import DayActivity, RecentChat
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.infrastructure.repositories.pending_interaction_queries import (
    has_pending_interaction_column,
)


def origin_column() -> ColumnElement[str]:
    """``conversation_origin.origin_of``, as SQL over ``ConversationModel``.

    The branches are tried in the same order as the Python rule, which is the
    whole of the agreement between them; ``test_conversation_origin_sql_agrees``
    runs both over every shape.
    """
    metadata = ConversationModel.conversation_metadata
    source = metadata["source"].astext
    resource = metadata[RESOURCE_KEY]
    return case(
        (metadata[AUDIENCE_KEY].astext == OUTSIDERS, ConversationOrigin.CHANNEL.value),
        *[(source == value, origin.value) for value, origin in SOURCE_ORIGINS],
        (
            and_(
                func.jsonb_typeof(resource) == "string",
                func.btrim(resource.astext, " \t\r\n") != "",
            ),
            ConversationOrigin.RESOURCE.value,
        ),
        (
            ConversationModel.conversation_type == TASK_TYPE,
            ConversationOrigin.TASK.value,
        ),
        else_=ConversationOrigin.CHAT.value,
    )


def _origins(origins: frozenset[ConversationOrigin]) -> list[str]:
    return sorted(origin.value for origin in origins)


async def recent_chats_by_pod(
    session: AsyncSession,
    *,
    user_id: UUID,
    pod_ids: Sequence[UUID],
    per_pod: int,
) -> dict[UUID, list[RecentChat]]:
    """Each pod's latest ``per_pod`` chats of this person's, newest first.

    The chats list is the conversations of no channel -- a channel's are reached
    through the channel -- that a person talks in, plus any conversation
    waiting on its owner, whatever started it, since that is where it is
    answered. Archived ones are left out: archiving is how a person clears one.

    A lateral read per pod rather than a window over all of them: each pod
    walks its own range of the index and stops at ``per_pod``, so a pod with a
    long history costs what a new one does.
    """
    if not pod_ids:
        return {}
    pods = values(column("pod_id", Uuid), name="pods").data(
        [(pod_id,) for pod_id in dict.fromkeys(pod_ids)]
    )
    origin = origin_column()
    waiting = has_pending_interaction_column()
    metadata = ConversationModel.conversation_metadata
    chats = (
        select(
            ConversationModel.id.label("conversation_id"),
            ConversationModel.title.label("title"),
            origin.label("origin"),
            ConversationModel.status.label("status"),
            ConversationModel.agent_id.label("agent_id"),
            ConversationModel.last_activity_at.label("last_activity_at"),
            waiting.label("waiting_on_you"),
            metadata["surface_platform"].astext.label("platform"),
            metadata["channel_name"].astext.label("channel_name"),
        )
        .where(
            ConversationModel.user_id == user_id,
            ConversationModel.pod_id == pods.c.pod_id,
            ConversationModel.parent_id.is_(None),
            ConversationModel.is_archived.is_(False),
            ConversationModel.channel_id.is_(None),
            or_(origin.in_(_origins(CHATS)), waiting),
        )
        .order_by(
            ConversationModel.last_activity_at.desc(), ConversationModel.id.desc()
        )
        .limit(per_pod)
        .lateral("chats")
    )
    rows = await session.execute(
        select(pods.c.pod_id, *chats.c)
        .select_from(pods)
        .join(chats, true())
        .order_by(
            pods.c.pod_id,
            chats.c.last_activity_at.desc(),
            chats.c.conversation_id.desc(),
        )
    )
    by_pod: dict[UUID, list[RecentChat]] = {}
    for row in rows:
        by_pod.setdefault(row.pod_id, []).append(
            RecentChat(
                pod_id=row.pod_id,
                conversation_id=row.conversation_id,
                title=row.title,
                origin=ConversationOrigin(row.origin),
                status=row.status,
                agent_id=row.agent_id,
                last_activity_at=row.last_activity_at,
                waiting_on_you=bool(row.waiting_on_you),
                platform=row.platform,
                channel_name=row.channel_name,
            )
        )
    return by_pod


async def daily_activity_by_pod(
    session: AsyncSession,
    *,
    user_id: UUID,
    pod_ids: Sequence[UUID],
    since: datetime,
    zone: str,
) -> list[DayActivity]:
    """How many of this person's conversations were last active each day.

    A conversation counts once, on the day in ``zone`` it was last active --
    the same moment the history list orders by. Every root conversation
    counts, archived ones and a channel's included: both were work that
    happened, whatever the person did with it afterwards. ``zone`` is an IANA
    name the caller has already checked.

    Days with nothing are absent; the caller knows which days it asked for.
    The day is worked out in an inner select and grouped outside it, because
    Postgres would read the same expression grouped with a second bind of the
    zone as a different expression.
    """
    if not pod_ids:
        return []
    by_agent = origin_column().in_(_origins(STARTED_BY_AN_AGENT))
    worked = (
        select(
            ConversationModel.pod_id.label("pod_id"),
            cast(func.timezone(zone, ConversationModel.last_activity_at), Date).label(
                "day"
            ),
            by_agent.label("by_agent"),
        )
        .where(
            ConversationModel.user_id == user_id,
            ConversationModel.pod_id.in_(list(pod_ids)),
            ConversationModel.parent_id.is_(None),
            ConversationModel.last_activity_at >= since,
        )
        .subquery("worked")
    )
    rows = await session.execute(
        select(
            worked.c.pod_id,
            worked.c.day,
            func.count().filter(worked.c.by_agent).label("by_agent"),
            func.count().filter(~worked.c.by_agent).label("by_people"),
        ).group_by(worked.c.pod_id, worked.c.day)
    )
    return [
        DayActivity(
            pod_id=row.pod_id,
            day=row.day,
            by_agent=int(row.by_agent),
            by_people=int(row.by_people),
        )
        for row in rows
    ]


__all__ = ["daily_activity_by_pod", "origin_column", "recent_chats_by_pod"]
