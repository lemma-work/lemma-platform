"""Choose a person's first organization, and the Lemma they land in, atomically.

One rule for every way in -- the web, a plugin's install page, a chat sign-up,
a company's Slack or Teams install -- so where somebody starts does not depend
on the door they used. In order:

1. The organization a company's installation fixes, when they arrived through
   one.
2. Their pending invitations, accepted, for a verified address.
3. An organization they already belong to.
4. Their company's organization, when it is open to their verified work domain.
5. A new organization: their company's, named after it and open to colleagues
   from the same domain, or a workspace of their own.

A new organization is made with its own Lemma, which everyone in it shares. They
land on the pod they were invited to, else that Lemma, else -- only in an older
organization that has none, and only when asked -- a private pod of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.domain.email_domains import work_domain_from_email
from app.modules.identity.domain.errors import (
    IdentityAccessDeniedError,
    OrganizationConflictError,
    OrganizationMemberLimitError,
)
from app.modules.identity.domain.organization_entities import (
    OrganizationEntity,
    OrganizationJoinPolicy,
)
from app.modules.identity.domain.workspace_names import (
    first_pod_name,
    organization_lemma_name,
    organization_name_candidate,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.identity.infrastructure.workspace_locks import lock_workspace_selection
from app.modules.identity.services.organization_roles import sync_organization_role
from app.modules.identity.services.organization_service import OrganizationService
from app.modules.identity.services.pending_invitations import (
    accept_pending_invitations,
)
from app.modules.pod.contracts.members import pod_name
from app.modules.pod.contracts.organization_lemma import (
    create_organization_lemma,
    enter_organization_lemma,
    organization_lemma_id,
)
from app.modules.pod.contracts.personal_workspace import (
    ensure_personal_workspace,
    invited_workspace,
)

WorkspaceEntry = Literal[
    "existing", "surface_join", "invitation", "domain_join", "new_org"
]
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
    organization_name: str = ""
    pod_name: str | None = None
    #: The email domain the organization takes colleagues from, when it does.
    organization_open_to_domain: str | None = None
    #: The organization's own Lemma, which is not ``pod_id`` when an
    #: invitation brought them to some other pod.
    organization_pod_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _Landing:
    pod_id: UUID
    assistant_id: UUID
    created: bool


@dataclass(frozen=True, slots=True)
class _Organization:
    id: UUID
    entry: WorkspaceEntry
    #: A company's organization rather than a workspace of one. Read only for
    #: one that was just made: it decides what its Lemma is called.
    team: bool = False
    #: The pod an accepted invitation named, which is where they land.
    invited_pod_id: UUID | None = None
    #: Their membership of the organization was made by this request, so they
    #: are new to its Lemma too. Anybody else who is not in it left it.
    newcomer: bool = False


async def ensure_first_workspace(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_service: OrganizationService,
    user_id: UUID,
    email: str,
    full_name: str | None = None,
    with_pod: bool = True,
    own_organization: bool = False,
    team: bool | None = None,
    arrived_through_organization_id: UUID | None = None,
) -> ProvisionedWorkspace:
    """Where this person works, made if it has to be.

    ``with_pod`` asks for a private pod only where the organization has no
    Lemma of its own to land in. ``own_organization`` skips their company's
    open organization for a workspace of their own. ``team=False`` makes a new
    organization a workspace of one whatever the address; ``None`` lets a
    verified work domain decide. Installation context is trusted server input,
    never a public organization hint. ``email`` is not trusted either: the
    account's own address is read instead.
    """
    del email
    await lock_workspace_selection(uow.session, f"user:{user_id}")
    user = await _workspace_user(uow, user_id)
    if arrived_through_organization_id is not None:
        # The installation fixes the destination even if the user belongs to
        # other organizations. A missing installation organization is an error.
        chosen = await _join_installation_organization(
            uow,
            organization_service=organization_service,
            user_id=user_id,
            organization_id=arrived_through_organization_id,
        )
        if chosen is None:
            return await _access_required(
                organization_service, arrived_through_organization_id
            )
    else:
        chosen = await _choose_organization(
            uow,
            organization_service=organization_service,
            user=user,
            full_name=full_name,
            own_organization=own_organization,
            team=team,
        )

    await lock_workspace_selection(uow.session, f"organization:{chosen.id}")
    membership_id = await uow.session.scalar(
        select(OrganizationMember.id).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == chosen.id,
        )
    )
    organization = await organization_service.organization_repository.get(chosen.id)
    if membership_id is None or organization is None:
        raise IdentityAccessDeniedError("Organization membership is required")
    landing, shared_pod_id = await _land(
        uow,
        chosen=chosen,
        organization=organization,
        user_id=user_id,
        membership_id=membership_id,
        full_name=full_name,
        with_pod=with_pod,
    )
    await uow.session.flush()
    return ProvisionedWorkspace(
        chosen.id,
        landing.pod_id if landing is not None else None,
        chosen.entry,
        assistant_id=landing.assistant_id if landing is not None else None,
        organization_created=chosen.entry == "new_org",
        pod_created=landing is not None and landing.created,
        organization_name=organization.name,
        pod_name=(
            await pod_name(uow.session, landing.pod_id) if landing is not None else None
        ),
        organization_open_to_domain=_open_to_domain(organization),
        organization_pod_id=shared_pod_id,
    )


async def _choose_organization(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_service: OrganizationService,
    user: User,
    full_name: str | None,
    own_organization: bool,
    team: bool | None,
) -> _Organization:
    """Somebody who came in through no installation: where do they belong?"""
    # Invitations first: they are the one place somebody else already said
    # where this person belongs. Only a verified address may accept one.
    member_of = set(
        await uow.session.scalars(
            select(OrganizationMember.organization_id).where(
                OrganizationMember.user_id == user.id
            )
        )
    )
    invited = (
        await accept_pending_invitations(
            uow,
            organization_service=organization_service,
            user_id=user.id,
            email=user.email,
        )
        if user.is_verified
        else None
    )
    if invited is not None:
        return _Organization(
            invited.organization_id,
            "invitation",
            invited_pod_id=invited.pod_id,
            # An invitation to a pod in an organization they were already in
            # brings them to that pod, not back into a Lemma they left.
            newcomer=invited.organization_id not in member_of,
        )
    existing_id = await uow.session.scalar(
        select(OrganizationMember.organization_id)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.organization_id)
        .limit(1)
    )
    if existing_id is not None:
        return _Organization(existing_id, "existing")
    return await _join_or_create_organization(
        uow,
        organization_service=organization_service,
        user=user,
        full_name=full_name,
        own_organization=own_organization,
        team=team,
    )


async def _land(
    uow: SqlAlchemyUnitOfWork,
    *,
    chosen: _Organization,
    organization: OrganizationEntity,
    user_id: UUID,
    membership_id: UUID,
    full_name: str | None,
    with_pod: bool,
) -> tuple[_Landing | None, UUID | None]:
    """Where they land, and which pod is the organization's own Lemma.

    A new organization's Lemma is made here, with the person as its admin.
    """
    made = (
        await create_organization_lemma(
            uow,
            organization_id=organization.id,
            owner_user_id=user_id,
            name=organization_lemma_name(organization.name, team=chosen.team),
        )
        if chosen.entry == "new_org"
        else None
    )
    if made is not None:
        return _Landing(made.pod_id, made.assistant_id, made.created), made.pod_id
    entered = await enter_organization_lemma(
        uow,
        organization_id=organization.id,
        user_id=user_id,
        newcomer=chosen.newcomer,
    )
    invited = await _invited_landing(uow, pod_id=chosen.invited_pod_id, user_id=user_id)
    if invited is not None:
        # They land where they were invited, already in the organization's
        # Lemma too when they are new to the organization, so the first
        # screen shows them in it rather than offering it.
        shared_id = (
            entered.pod_id
            if entered is not None
            else await organization_lemma_id(uow, organization_id=organization.id)
        )
        return invited, shared_id
    if entered is not None:
        return _Landing(entered.pod_id, entered.assistant_id, False), entered.pod_id
    # Named even when they are not in it -- they left it -- so a screen can
    # offer it back among the Lemmas they could join.
    shared_pod_id = await organization_lemma_id(uow, organization_id=organization.id)
    if not with_pod:
        return None, shared_pod_id
    # An organization with no Lemma of its own, or one this person left: the
    # private pod a first workspace used to be, so a chat has somewhere to go.
    personal = await ensure_personal_workspace(
        uow,
        organization_id=organization.id,
        owner_user_id=user_id,
        owner_membership_id=membership_id,
        name=first_pod_name(full_name),
    )
    if personal is None:
        return None, shared_pod_id
    return (
        _Landing(personal.pod_id, personal.assistant_id, personal.created),
        shared_pod_id,
    )


async def _join_installation_organization(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_service: OrganizationService,
    user_id: UUID,
    organization_id: UUID,
) -> _Organization | None:
    """Into the organization a company's install fixes, or ``None`` if it is shut."""
    already_member = (
        await uow.session.scalar(
            select(OrganizationMember.id).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == organization_id,
            )
        )
        is not None
    )
    try:
        await organization_service.join_auto_join_organization(organization_id, user_id)
    except IdentityAccessDeniedError, OrganizationMemberLimitError:
        # Full is answered like closed: an admin has to act either way.
        return None
    await sync_organization_role(
        uow.session,
        organization_id=organization_id,
        user_id=user_id,
        assigned_by_user_id=user_id,
    )
    return _Organization(organization_id, "surface_join", newcomer=not already_member)


async def _access_required(
    organization_service: OrganizationService, organization_id: UUID
) -> ProvisionedWorkspace:
    organization = await organization_service.organization_repository.get(
        organization_id
    )
    return ProvisionedWorkspace(
        organization_id,
        None,
        "surface_join",
        status="organization_access_required",
        organization_name=organization.name if organization is not None else "",
    )


async def _invited_landing(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID | None, user_id: UUID
) -> _Landing | None:
    if pod_id is None:
        return None
    invited = await invited_workspace(uow, pod_id=pod_id, user_id=user_id)
    if invited is None:
        return None
    return _Landing(invited.pod_id, invited.assistant_id, False)


async def _join_or_create_organization(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_service: OrganizationService,
    user: User,
    full_name: str | None,
    own_organization: bool,
    team: bool | None,
) -> _Organization:
    """No invitation and no membership: their company's organization, or a new one."""
    email = user.email
    # An address nobody proved claims nothing: not a colleague's organization,
    # and not a domain for one of its own.
    work_domain = work_domain_from_email(email) if user.is_verified else None
    joins_colleagues = work_domain is not None and not own_organization
    if joins_colleagues:
        await lock_workspace_selection(uow.session, f"domain:{work_domain}")
        suggested, _ = await organization_service.list_suggested_organizations(
            user.id, limit=1
        )
        if suggested:
            try:
                await organization_service.join_auto_join_organization(
                    suggested[0].id, user.id
                )
            except OrganizationMemberLimitError:
                # Their company's organization is as full as its plan allows.
                # Signing up must not dead-end on that, so they start one of
                # their own, which cannot claim the domain the company holds.
                pass
            else:
                await sync_organization_role(
                    uow.session,
                    organization_id=suggested[0].id,
                    user_id=user.id,
                    assigned_by_user_id=user.id,
                )
                return _Organization(suggested[0].id, "domain_join", newcomer=True)
    is_team = joins_colleagues and team is not False
    company_domain = work_domain if is_team else None
    organization_id = await _create_organization(
        uow,
        organization_service=organization_service,
        owner_user_id=user.id,
        name=organization_name_candidate(
            email=email, work_domain=company_domain, full_name=full_name
        ),
        open_to_domain=company_domain,
    )
    await sync_organization_role(
        uow.session,
        organization_id=organization_id,
        user_id=user.id,
        assigned_by_user_id=user.id,
    )
    return _Organization(organization_id, "new_org", team=is_team)


async def _create_organization(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_service: OrganizationService,
    owner_user_id: UUID,
    name: str,
    open_to_domain: str | None,
) -> UUID:
    """A new organization, open to colleagues from its domain where it may be.

    The name stands even when another organization has it: names are labels
    two organizations may share, and only the handle moves aside.
    """
    if open_to_domain is not None:
        try:
            async with uow.session.begin_nested():
                created = await organization_service.create_organization(
                    OrganizationEntity(
                        name=name,
                        slug="",
                        join_policy=OrganizationJoinPolicy.EMAIL_DOMAIN,
                        email_domain=open_to_domain,
                    ),
                    owner_user_id,
                    resolve_name_conflicts=True,
                )
            return created.id
        except OrganizationConflictError as conflict:
            # Another organization holds the domain -- one that was full, or
            # one made a moment ago by a request this one did not wait for.
            # Opening is the owner's to do later in Settings; signing up does
            # not fail over it.
            if conflict.code != OrganizationConflictError.DOMAIN_TAKEN:
                raise
    created = await organization_service.create_organization(
        OrganizationEntity(
            name=name, slug="", join_policy=OrganizationJoinPolicy.INVITE_ONLY
        ),
        owner_user_id,
        resolve_name_conflicts=True,
    )
    return created.id


def _open_to_domain(organization: OrganizationEntity) -> str | None:
    if organization.join_policy != OrganizationJoinPolicy.EMAIL_DOMAIN:
        return None
    return organization.email_domain


async def _workspace_user(uow: SqlAlchemyUnitOfWork, user_id: UUID) -> User:
    user = await uow.session.get(User, user_id)
    if user is None or not user.is_active or user.is_deleted:
        raise IdentityAccessDeniedError("This account cannot create a workspace")
    return user
