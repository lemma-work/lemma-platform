"""Facts about one person's membership of one pod.

Replaces `app/composition/workflow_pod.py`, which published `PodMemberRepository`
so `workflow` could construct it. Both callers wanted the same single fact —
this person's pod-member id, or nothing — to check that a form's assignee is the
person submitting it. A repository was three layers more than the question.

`pod_name` is here for the same reason: `agent`'s context brief was reaching
`Pod` itself, through `app/composition/agent_context_models.py`, to select one
column.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.modules.pod.infrastructure.models.pod_models import Pod
from app.modules.pod.infrastructure.pod_repositories import PodMemberRepository


async def pod_member_id(uow, pod_id: UUID, user_id: UUID) -> UUID | None:
    """This user's membership of this pod, or ``None`` if they are not in it.

    The id rather than the row: every caller outside this module wants it to
    compare against something already assigned to a member, and handing back the
    entity invites a second module to start reading fields off it.
    """
    member = await PodMemberRepository(uow).get_by_pod_and_user_id(pod_id, user_id)
    return member.id if member is not None else None


async def pod_name(session, pod_id: UUID) -> str | None:
    """The pod's display name, or ``None`` if it no longer exists."""
    return (
        await session.execute(select(Pod.name).where(Pod.id == pod_id))
    ).scalar_one_or_none()


__all__ = ["pod_member_id", "pod_name"]
