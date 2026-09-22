"""Choose an organization and ensure a usable personal workspace atomically."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.domain.email_domains import work_domain_from_email
from app.modules.identity.domain.errors import (
    IdentityAccessDeniedError,
    OrganizationMemberLimitError,
)
from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationJoinPolicy,
)
from app.modules.identity.domain.workspace_names import (
    first_pod_name,
    organization_name_candidate,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.identity.infrastructure.workspace_locks import lock_workspace_selection
from app.modules.identity.services.organization_service import OrganizationService
from app.modules.pod.contracts.personal_workspace import ensure_personal_workspace

WorkspaceEntry = Literal["existing", "surface_join", "domain_join", "new_org"]
WorkspaceStatus = Literal["ready", "organization_access_required"]


@dataclass(frozen=True, slots=True)
class ProvisionedWorkspace:
    organization_id: UUID
    pod_id: UUID | None
    entry: WorkspaceEntry
    assistant_id: UUID | None = None
    status: WorkspaceStatus = "ready"
    organization_created: bool = False
    pod_created: bool = False


async def ensure_first_workspace(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_service: OrganizationService,
    user_id: UUID,
    email: str,
    full_name: str | None = None,
    with_pod: bool = True,
    arrived_through_organization_id: UUID | None = None,
) -> ProvisionedWorkspace:
    """Installation context is trusted server input, never a public org hint."""
    await lock_workspace_selection(uow.session, f"user:{user_id}")
    user = await _workspace_user(uow, user_id)
    email = user.email
    entry: WorkspaceEntry = "existing"
    organization_id = arrived_through_organization_id
    if organization_id is not None:
        # The installation fixes the destination even if the user belongs to
        # other organizations. A missing installation organization is an error.
        try:
            await organization_service.join_auto_join_organization(
                organization_id, user_id
            )
        except IdentityAccessDeniedError, OrganizationMemberLimitError:
            # Full is answered like closed: an admin has to act either way.
            return ProvisionedWorkspace(
                organization_id,
                None,
                "surface_join",
                status="organization_access_required",
            )
        entry = "surface_join"
    else:
        organization_id = await uow.session.scalar(
            select(OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == user_id)
            .order_by(OrganizationMember.organization_id)
            .limit(1)
        )
        if organization_id is None:
            work_domain = work_domain_from_email(email) if user.is_verified else None
            if work_domain:
                await lock_workspace_selection(uow.session, f"domain:{work_domain}")
            suggested = []
            if work_domain:
                suggested, _ = await organization_service.list_suggested_organizations(
                    user_id, limit=1
                )
            organization = None
            if suggested:
                try:
                    organization = (
                        await organization_service.join_auto_join_organization(
                            suggested[0].id, user_id
                        )
                    )
                    entry = "domain_join"
                except OrganizationMemberLimitError:
                    # Their company's organization is as full as its plan
                    # allows. Signing up must not dead-end on that, so they
                    # start one of their own -- claiming nothing, since the
                    # company already holds the domain.
                    work_domain = None
            if organization is None:
                organization = await organization_service.create_organization(
                    OrganizationEntity(
                        name=organization_name_candidate(
                            email=email, work_domain=work_domain
                        ),
                        slug="",
                        join_policy=OrganizationJoinPolicy.EMAIL_DOMAIN
                        if work_domain
                        else OrganizationJoinPolicy.INVITE_ONLY,
                        email_domain=work_domain,
                    ),
                    user_id,
                    resolve_name_conflicts=True,
                )
                entry = "new_org"
            organization_id = organization.id

    await lock_workspace_selection(uow.session, f"organization:{organization_id}")
    membership_id = await uow.session.scalar(
        select(OrganizationMember.id).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id,
        )
    )
    if membership_id is None:
        raise IdentityAccessDeniedError("Organization membership is required")
    pod_id = assistant_id = None
    pod_created = False
    if with_pod:
        personal = await ensure_personal_workspace(
            uow,
            organization_id=organization_id,
            owner_user_id=user_id,
            owner_membership_id=membership_id,
            name=first_pod_name(full_name),
        )
        if personal is not None:
            pod_id, assistant_id, pod_created = (
                personal.pod_id,
                personal.assistant_id,
                personal.created,
            )
    await uow.session.flush()
    return ProvisionedWorkspace(
        organization_id,
        pod_id,
        entry,
        assistant_id=assistant_id,
        organization_created=entry == "new_org",
        pod_created=pod_created,
    )


async def _workspace_user(uow: SqlAlchemyUnitOfWork, user_id: UUID) -> User:
    user = await uow.session.get(User, user_id)
    if user is None or not user.is_active or user.is_deleted:
        raise IdentityAccessDeniedError("This account cannot create a workspace")
    return user
