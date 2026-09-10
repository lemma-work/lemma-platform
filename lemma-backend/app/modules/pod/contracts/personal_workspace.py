"""Provision a private, solely owned pod within an authorized organization."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.provisioning import ensure_pod_default_agent
from app.modules.pod.api.dependencies import get_pod_service
from app.modules.pod.domain.pod_entities import PodConfig, PodEntity, PodJoinPolicy
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember


@dataclass(frozen=True, slots=True)
class PersonalWorkspace:
    pod_id: UUID
    assistant_id: UUID
    created: bool


async def ensure_personal_workspace(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_id: UUID,
    owner_user_id: UUID,
    owner_membership_id: UUID,
    saved_pod_id: UUID | None,
    name: str,
) -> PersonalWorkspace:
    member_count = (
        select(func.count(PodMember.id))
        .where(PodMember.pod_id == Pod.id)
        .correlate(Pod)
        .scalar_subquery()
    )
    candidates = list(
        (
            await uow.session.scalars(
                select(Pod)
                .where(
                    Pod.organization_id == organization_id,
                    Pod.user_id == owner_user_id,
                    Pod.is_deleted.is_(False),
                    member_count == 1,
                    select(PodMember.id)
                    .where(
                        PodMember.pod_id == Pod.id,
                        PodMember.organization_member_id == owner_membership_id,
                    )
                    .exists(),
                )
                .order_by(Pod.created_at, Pod.id)
            )
        ).all()
    )
    eligible = [
        pod
        for pod in candidates
        if PodConfig.from_raw(pod.config).join_policy == PodJoinPolicy.INVITE_ONLY
    ]
    chosen = next(
        (pod for pod in eligible if pod.id == saved_pod_id),
        eligible[0] if eligible else None,
    )
    if chosen is not None:
        assistant_id = await ensure_pod_default_agent(
            uow, pod_id=chosen.id, user_id=owner_user_id
        )
        return PersonalWorkspace(chosen.id, assistant_id, False)
    # Names are unique inside an organization, including colleagues with the
    # same first name. The caller holds the organization provisioning lock.
    existing_name = await uow.session.scalar(
        select(Pod.id).where(
            Pod.organization_id == organization_id,
            Pod.name == name,
            Pod.is_deleted.is_(False),
        )
    )
    if existing_name is not None:
        name = f"{name} {owner_user_id.hex[:8]}"
    pod = await get_pod_service(uow).create_pod(
        PodEntity(user_id=owner_user_id, organization_id=organization_id, name=name),
        owner_user_id,
    )
    assistant_id = await ensure_pod_default_agent(
        uow, pod_id=pod.id, user_id=owner_user_id
    )
    return PersonalWorkspace(pod.id, assistant_id, True)
