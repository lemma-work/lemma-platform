"""Give a person somewhere to work, without asking them anything.

Signing up creates a person and nothing else, deliberately -- a first
organization is a decision about who you work with, and the journey spec says
guessing it wrong is worse than asking. This is what happens *after* that
decision point, when someone has arrived and needs a workspace: the same three
doors the web onboarding walks, in the same order.

    1. An organization they already belong to. Nothing to make.
    2. One that already claimed their email domain -- joined, not duplicated.
       A colleague got here first, and fragmenting the company across two
       workspaces is worse than landing in theirs with the least privilege.
    3. Otherwise a new one, named after the company where the address names a
       company and generated where it does not.

The order matters more than any single step: the failure this exists to prevent
is somebody messaging an agent from `ada@acme.com` and being handed a private
organization of one while the rest of Acme is already inside Lemma.

Backend rather than frontend because chat surfaces onboard people who never load
the app. `account-onboarding-helpers.ts` and `use-ensure-organization.ts` are
being moved onto this; until they are, the two implementations agree because
`workspace_names` is a bit-exact port with the frontend's own output pinned in
its tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.identity.domain.email_domains import work_domain_from_email
from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationJoinPolicy,
)
from app.modules.identity.domain.workspace_names import (
    first_pod_name,
    organization_name_candidate,
)
from app.modules.identity.services.organization_service import OrganizationService
from app.modules.pod.contracts.provisioning import create_first_pod

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProvisionedWorkspace:
    """Where a person ended up, and how they got there."""

    organization_id: UUID
    pod_id: UUID | None
    #: ``existing`` | ``domain_join`` | ``new_org`` -- the door they came through.
    entry: str


async def ensure_first_workspace(
    uow,
    *,
    organization_service: OrganizationService,
    user_id: UUID,
    email: str,
    full_name: str | None = None,
    with_pod: bool = True,
) -> ProvisionedWorkspace:
    """Return the workspace this person should be in, making one if they have none.

    Idempotent by construction: someone who already belongs somewhere gets that
    organization back and nothing is created, so a retried onboarding cannot
    leave a second empty workspace behind.
    """
    existing, _ = await organization_service.list_user_organizations(user_id, limit=1)
    if existing:
        return ProvisionedWorkspace(
            organization_id=existing[0].id,
            pod_id=None,
            entry="existing",
        )

    suggested, _cursor = await organization_service.list_suggested_organizations(
        user_id, limit=1
    )
    if suggested:
        joined = await organization_service.join_auto_join_organization(
            suggested[0].id, user_id
        )
        logger.info("identity.first_workspace.joined_by_domain", user_id=str(user_id))
        pod_id = None
        return ProvisionedWorkspace(
            organization_id=joined.id, pod_id=pod_id, entry="domain_join"
        )

    work_domain = work_domain_from_email(email)
    organization = await organization_service.create_organization(
        OrganizationEntity(
            name=organization_name_candidate(email=email, work_domain=work_domain),
            slug="",
            # A work address opens the door for the next colleague to arrive;
            # a personal one has no domain worth opening it to.
            join_policy=(
                OrganizationJoinPolicy.EMAIL_DOMAIN
                if work_domain
                else OrganizationJoinPolicy.INVITE_ONLY
            ),
            email_domain=work_domain,
        ),
        user_id,
        resolve_name_conflicts=True,
    )
    logger.info("identity.first_workspace.organization_created", user_id=str(user_id))

    pod_id = None
    if with_pod:
        pod = await create_first_pod(
            uow,
            organization_id=organization.id,
            owner_user_id=user_id,
            name=first_pod_name(full_name),
        )
        pod_id = pod.id

    return ProvisionedWorkspace(
        organization_id=organization.id, pod_id=pod_id, entry="new_org"
    )
