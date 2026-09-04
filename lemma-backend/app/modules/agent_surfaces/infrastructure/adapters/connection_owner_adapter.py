from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, select

from app.core.domain.uow import IUnitOfWork
from app.modules.pod.contracts.orm import PodMember
from app.modules.identity.contracts.orm import OrganizationMember, User
from app.modules.agent_surfaces.domain.ports import SurfaceConnectionOwnerInfo


class SqlAlchemySurfaceConnectionOwnerAdapter:
    """Names the people behind a pod's connected accounts, in one query.

    Identity and pod membership are resolved together on purpose: the surfaces
    read path never wants one without the other — a name with no membership
    answer can't say whether anyone can still re-authorize the account.
    """

    def __init__(self, uow: IUnitOfWork):
        self._session = uow.session

    async def list_pod_owners(
        self, user_ids: Sequence[UUID], *, pod_id: UUID
    ) -> dict[UUID, SurfaceConnectionOwnerInfo]:
        ids = {user_id for user_id in user_ids if user_id is not None}
        if not ids:
            return {}

        # Outer joins throughout: an owner who has left the pod (or the whole
        # organization) must still come back with a name, because "Priya
        # connected this and is gone" is the answer worth showing.
        rows = await self._session.execute(
            select(
                User.id,
                User.first_name,
                User.last_name,
                User.email,
                PodMember.id.label("pod_member_id"),
            )
            .select_from(User)
            .outerjoin(OrganizationMember, OrganizationMember.user_id == User.id)
            .outerjoin(
                PodMember,
                and_(
                    PodMember.organization_member_id == OrganizationMember.id,
                    PodMember.pod_id == pod_id,
                ),
            )
            .where(User.id.in_(ids))
        )

        owners: dict[UUID, SurfaceConnectionOwnerInfo] = {}
        for row in rows:
            # One row per organization the user belongs to, so membership is the
            # OR across rows rather than whichever row arrives last.
            existing = owners.get(row.id)
            is_member = row.pod_member_id is not None or bool(
                existing and existing.is_pod_member
            )
            owners[row.id] = SurfaceConnectionOwnerInfo(
                user_id=row.id,
                name=_display_name(row.first_name, row.last_name),
                email=row.email,
                is_pod_member=is_member,
            )
        return owners


def _display_name(first_name: str | None, last_name: str | None) -> str | None:
    name = " ".join(part for part in (first_name, last_name) if part and part.strip())
    return name.strip() or None
