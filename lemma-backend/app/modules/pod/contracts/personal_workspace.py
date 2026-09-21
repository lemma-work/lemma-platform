"""Provision a private, solely owned pod within an authorized organization."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.provisioning import ensure_pod_default_agent
from app.modules.pod.api.dependencies import get_pod_service
from app.modules.pod.domain.pod_entities import PodEntity, PodJoinPolicy
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
    name: str,
) -> PersonalWorkspace:
    member_count = (
        select(func.count(PodMember.id))
        .where(PodMember.pod_id == Pod.id)
        .correlate(Pod)
        .scalar_subquery()
    )
    # `join_policy` is filtered in SQL rather than in Python so that the saved
    # pod is one row and the fallback is `LIMIT 1`. A person's own pods are few,
    # but "few" is a property of their data, not of this query. An absent key is
    # `PodConfig`'s own default, which is why NULL counts as invite-only.
    eligible = (
        Pod.organization_id == organization_id,
        Pod.user_id == owner_user_id,
        Pod.is_deleted.is_(False),
        or_(
            Pod.config["join_policy"].astext.is_(None),
            Pod.config["join_policy"].astext == PodJoinPolicy.INVITE_ONLY.value,
        ),
        member_count == 1,
        select(PodMember.id)
        .where(
            PodMember.pod_id == Pod.id,
            PodMember.organization_member_id == owner_membership_id,
        )
        .exists(),
    )
    # The oldest eligible pod, and nothing cleverer. A saved-selection hint used
    # to sit in front of this, but nothing ever recorded a selection -- the only
    # writer was this function storing back what it had just picked -- so the
    # hint could only ever agree with the fallback it was shadowing.
    chosen_id = await uow.session.scalar(
        select(Pod.id).where(*eligible).order_by(Pod.created_at, Pod.id).limit(1)
    )
    if chosen_id is not None:
        assistant_id = await ensure_pod_default_agent(
            uow, pod_id=chosen_id, user_id=owner_user_id
        )
        return PersonalWorkspace(chosen_id, assistant_id, False)
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


async def create_named_workspace(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_id: UUID,
    owner_user_id: UUID,
    name: str,
) -> PersonalWorkspace:
    """Make a pod the person named, and give it its default agent.

    Unlike `ensure_personal_workspace`, this always creates: the caller asked
    for a *new* workspace by name, so silently handing back an existing one
    would answer a different question than the one they were asked.
    """
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
