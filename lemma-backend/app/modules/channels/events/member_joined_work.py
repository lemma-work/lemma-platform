"""A member joined the pod: their own channel with it is made now.

Subscribed to the pod's ``PodMemberAdded``, which every way into a pod writes
in the transaction that makes the membership -- so a member's DIRECT channel
exists without anybody having to read for it, and a GET never has to write.
Somebody who left and came back gets their channel back: leaving archived it.
A member from before this existed has theirs made by ``POST /channels/me``.
A FAST item: main-database statements only.
"""

from __future__ import annotations

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
)
from app.core.infrastructure.work import (
    registry as default_registry,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.pod.contracts.lifecycle import PodMemberAdded

CHANNELS_MEMBER_JOINED = "channels.member_joined"


async def member_joined(ctx: FastContext, payload: PodMemberAdded) -> None:
    channels = ChannelRepository(ctx.uow.session)
    channel = await channels.direct(pod_id=payload.pod_id, user_id=payload.user_id)
    if channel.is_archived:
        await channels.change(channel.id, {"archived_at": None})


def register_member_joined_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=CHANNELS_MEMBER_JOINED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=PodMemberAdded,
    )(member_joined)


register_member_joined_work(default_registry)
facts.subscribe(
    PodMemberAdded,
    kind=CHANNELS_MEMBER_JOINED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"channels-member-joined:{fact.pod_id}:{fact.user_id}",
)
