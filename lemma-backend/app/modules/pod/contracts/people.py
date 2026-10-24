"""The people in a pod, for a module that has to go through them one by one.

`connectors` asks which members a required per-member connector is still
waiting on: everyone in the pod who may use it, and whether each has an account.
That needs the whole membership, not a search (`directory.list_pod_members`
pages through the service and answers an agent's lookup by name), and it needs
it in the caller's own unit of work, because the caller goes on to read its own
tables beside it.

The caller's authority is not checked here: this is a fact about the pod, and
the caller has already decided it may know it (`pod.connector.manage`).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.modules.identity.contracts.orm import OrganizationMember, User
from app.modules.pod.infrastructure.models.pod_models import PodMember

#: A pod is a team. Past this many, a per-member answer belongs in SQL.
MAX_POD_PEOPLE = 500


@dataclass(frozen=True, slots=True)
class PodPerson:
    user_id: UUID
    pod_member_id: UUID
    #: Their name as the pod shows it, or ``None`` when they gave none.
    name: str | None
    email: str | None


async def pod_people(
    uow, pod_id: UUID, *, limit: int = MAX_POD_PEOPLE
) -> list[PodPerson]:
    """The pod's live members, oldest first, at most ``limit`` of them."""
    rows = await uow.session.execute(
        select(
            OrganizationMember.user_id,
            PodMember.id,
            User.first_name,
            User.last_name,
            User.email,
        )
        .join(PodMember, PodMember.organization_member_id == OrganizationMember.id)
        .join(User, User.id == OrganizationMember.user_id)
        .where(
            PodMember.pod_id == pod_id,
            User.is_active.is_(True),
            User.is_deleted.is_(False),
        )
        .order_by(PodMember.created_at, PodMember.id)
        .limit(limit)
    )
    return [
        PodPerson(
            user_id=user_id,
            pod_member_id=member_id,
            name=" ".join(part for part in (first, last) if part) or None,
            email=email,
        )
        for user_id, member_id, first, last, email in rows
    ]


__all__ = ["MAX_POD_PEOPLE", "PodPerson", "pod_people"]
