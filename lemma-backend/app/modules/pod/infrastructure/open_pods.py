"""The pods a member may walk into: offered to them, or joined for them on arrival.

Apart from ``pod_repositories`` because that file is at the size ratchet, and
because it is one question asked two ways -- which pods would let this member
in -- rather than a repository concern.

Filtered in SQL, policy included, so a page is a page of pods the member can
actually join rather than a page of the organization trimmed afterwards. A
config with no ``join_policy`` key is ``PodConfig``'s default, invite-only, and
matches nothing here.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import ColumnElement, ScalarSelect, Select, func, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod.domain.pod_entities import (
    OPEN_JOIN_POLICIES,
    JoinablePod,
    PodJoinPolicy,
)
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember


def open_to_member_outside(
    organization_id: UUID, organization_member_id: UUID
) -> tuple[ColumnElement[bool], ...]:
    """The organization's live pods open to its members that this one is not in."""
    return (
        Pod.organization_id == organization_id,
        Pod.is_deleted.is_(False),
        Pod.config["join_policy"].astext.in_(
            sorted(policy.value for policy in OPEN_JOIN_POLICIES)
        ),
        ~select(PodMember.id)
        .where(
            PodMember.pod_id == Pod.id,
            PodMember.organization_member_id == organization_member_id,
        )
        .exists(),
    )


def member_count() -> ScalarSelect[int]:
    """How many people are in the pod on the row this is read beside."""
    return (
        select(func.count(PodMember.id))
        .where(PodMember.pod_id == Pod.id)
        .correlate(Pod)
        .scalar_subquery()
    )


def joinable_pods_sql(
    organization_id: UUID,
    organization_member_id: UUID,
    limit: int,
    cursor: Optional[str],
) -> Select[tuple[UUID, str, str | None, str | None, str, int]]:
    """A page of the pods the member may join, one past ``limit`` to show more."""
    query = select(
        Pod.id,
        Pod.name,
        Pod.description,
        Pod.icon_url,
        Pod.config["join_policy"].astext.label("join_policy"),
        member_count().label("member_count"),
    ).where(*open_to_member_outside(organization_id, organization_member_id))
    if cursor:
        query = query.where(Pod.id > UUID(cursor))
    return query.order_by(Pod.id).limit(limit + 1)


def default_pod_ids_sql(
    organization_id: UUID,
    organization_member_id: UUID,
    limit: int,
    cursor: Optional[str],
) -> Select[tuple[UUID]]:
    """A page of those that take in everyone who joins the organization.

    The open policy is required here as well as when the flag is saved: the
    config is a blob more than one writer puts back whole, and an invite-only
    pod must take nobody in, whatever its flag says.
    """
    query = select(Pod.id).where(
        *open_to_member_outside(organization_id, organization_member_id),
        Pod.config["auto_join_new_members"].astext == "true",
    )
    if cursor:
        query = query.where(Pod.id > UUID(cursor))
    return query.order_by(Pod.id).limit(limit + 1)


class OpenPods:
    """Satisfies ``OpenPodsPort``."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._session = uow.session

    async def list_joinable(
        self,
        organization_id: UUID,
        organization_member_id: UUID,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[JoinablePod], Optional[str]]:
        rows = (
            await self._session.execute(
                joinable_pods_sql(
                    organization_id, organization_member_id, limit, cursor
                )
            )
        ).all()
        pods = [
            JoinablePod(
                id=row.id,
                name=row.name,
                description=row.description,
                icon_url=row.icon_url,
                join_policy=PodJoinPolicy(row.join_policy),
                member_count=row.member_count,
            )
            for row in rows
        ]
        if len(pods) > limit:
            return pods[:limit], str(pods[limit - 1].id)
        return pods, None

    async def list_auto_join_pod_ids(
        self,
        organization_id: UUID,
        organization_member_id: UUID,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[UUID], Optional[str]]:
        pod_ids = list(
            (
                await self._session.execute(
                    default_pod_ids_sql(
                        organization_id, organization_member_id, limit, cursor
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(pod_ids) > limit:
            return pod_ids[:limit], str(pod_ids[limit - 1])
        return pod_ids, None
