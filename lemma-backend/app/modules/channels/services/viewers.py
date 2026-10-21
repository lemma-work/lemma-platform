"""Whether a pod member may see a channel: the access rule, asked about one
member by id rather than by their own request."""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.access import Viewer, may_see
from app.modules.channels.domain.entities import Channel
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.pod.contracts.members import pod_member_holds, pod_member_id

#: ``contact.read``: who may see the pod's contacts, and so their channels.
CONTACT_READ = "contact.read"


async def member_sees(
    uow: SqlAlchemyUnitOfWork, channel: Channel, user_id: UUID
) -> bool:
    """Whether ``user_id``, a member of the channel's pod, may see it."""
    if await pod_member_id(uow, channel.pod_id, user_id) is None:
        return False
    viewer = Viewer(
        user_id=user_id,
        pod_id=channel.pod_id,
        sees_contacts=await pod_member_holds(
            uow, channel.pod_id, user_id, CONTACT_READ
        ),
    )
    seated = await MemberRepository(uow.session).of_user(channel.id, user_id)
    return may_see(channel, viewer, is_member=seated is not None)
