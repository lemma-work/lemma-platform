"""A member left the pod: nothing of theirs keeps a channel waiting.

Subscribed to the pod's ``PodMemberRemoved``. Their assignments are let go --
the contact's agent answers again -- the questions waiting on them are
cancelled, they are taken out of every channel, and their own channel is
archived. A FAST item: main-database statements only.
"""

from __future__ import annotations

from sqlalchemy import update

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
from app.modules.channels.domain.entities import ChannelKind
from app.modules.channels.infrastructure.cases import CaseRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.models import ChannelModel
from app.modules.channels.infrastructure.rows import now
from app.modules.channels.services.questions import cancel_waiting_on
from app.modules.pod.contracts.lifecycle import PodMemberRemoved

CHANNELS_MEMBER_LEFT = "channels.member_left"


async def member_left(ctx: FastContext, payload: PodMemberRemoved) -> None:
    session = ctx.uow.session
    cases = CaseRepository(session)
    for channel_id in await cases.assigned_to(
        pod_id=payload.pod_id, user_id=payload.user_id
    ):
        await cases.assign(channel_id, None)
    # Through the question service: each line is touched, so its watchers see
    # it close, and each close is announced to whatever asked it.
    await cancel_waiting_on(ctx.uow, pod_id=payload.pod_id, user_id=payload.user_id)
    await MemberRepository(session).remove_from_pod(
        pod_id=payload.pod_id, user_id=payload.user_id
    )
    await session.execute(
        update(ChannelModel)
        .where(
            ChannelModel.pod_id == payload.pod_id,
            ChannelModel.kind == ChannelKind.DIRECT.value,
            ChannelModel.member_user_id == payload.user_id,
            ChannelModel.archived_at.is_(None),
        )
        .values(archived_at=now())
    )


def register_member_left_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=CHANNELS_MEMBER_LEFT,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=PodMemberRemoved,
    )(member_left)


register_member_left_work(default_registry)
facts.subscribe(
    PodMemberRemoved,
    kind=CHANNELS_MEMBER_LEFT,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"channels-member-left:{fact.pod_id}:{fact.user_id}",
)
