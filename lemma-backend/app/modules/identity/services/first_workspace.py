"""Give a person somewhere to work, without asking them anything.

Signing up creates a person and nothing else, deliberately -- a first
organization is a decision about who you work with, and the journey spec says
guessing it wrong is worse than asking. This is what happens *after* that
decision point, when someone has arrived and needs a workspace: the doors the
web onboarding walks, in the same order, plus one only a chat surface can offer.

    1. An organization they already belong to. Nothing to make.
    2. The organization whose surface they arrived through, if it will have
       them. Somebody standing inside a company's own Slack is not a candidate
       for a private organization of one.
    3. One that already claimed their email domain -- joined, not duplicated.
       A colleague got here first, and fragmenting the company across two
       workspaces is worse than landing in theirs with the least privilege.
    4. Otherwise a new one, named after the company where the address names a
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
from app.modules.identity.domain.errors import (
    IdentityAccessDeniedError,
    OrganizationNotFoundError,
)
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
    #: ``existing`` | ``surface_join`` | ``domain_join`` | ``new_org`` -- the
    #: door they came through.
    entry: str


async def _try_join(
    organization_service: OrganizationService,
    *,
    organization_id: UUID,
    user_id: UUID,
) -> UUID | None:
    """Join this organization if it will have them, else ``None``.

    Refusal is an ordinary answer here rather than a failure: an invite-only
    organization declining somebody who wandered in from a chat surface is the
    policy working, and the caller carries on to the next door.
    """
    try:
        joined = await organization_service.join_auto_join_organization(
            organization_id, user_id
        )
    except IdentityAccessDeniedError, OrganizationNotFoundError:
        return None
    return joined.id


async def ensure_first_workspace(
    uow,
    *,
    organization_service: OrganizationService,
    user_id: UUID,
    email: str,
    full_name: str | None = None,
    with_pod: bool = True,
    arrived_through_organization_id: UUID | None = None,
) -> ProvisionedWorkspace:
    """Return the workspace this person should be in, making one if they have none.

    Idempotent by construction: someone who already belongs somewhere gets that
    organization back and nothing is created, so a retried onboarding cannot
    leave a second empty workspace behind.

    ``arrived_through_organization_id`` is the organization whose surface they
    messaged -- a Slack workspace an organization installed Lemma into, say.
    Somebody standing inside a company's own Slack is not a candidate for a
    private organization of one, so that organization is tried before the domain
    match. It is only *tried*: the organization's own join policy decides, and an
    invite-only one still refuses, because the surface being reachable is not the
    same as its organization being open.
    """
    existing, _ = await organization_service.list_user_organizations(user_id, limit=1)
    if existing:
        return ProvisionedWorkspace(
            organization_id=existing[0].id,
            pod_id=None,
            entry="existing",
        )

    if arrived_through_organization_id is not None:
        joined_id = await _try_join(
            organization_service,
            organization_id=arrived_through_organization_id,
            user_id=user_id,
        )
        if joined_id is not None:
            logger.info(
                "identity.first_workspace.joined_through_surface", user_id=str(user_id)
            )
            return ProvisionedWorkspace(
                organization_id=joined_id, pod_id=None, entry="surface_join"
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
