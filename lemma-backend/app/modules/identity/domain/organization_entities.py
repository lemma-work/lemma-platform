from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import UUID

from pydantic import Field
from pydantic import field_validator

from app.core.authorization.permissions import (
    equivalent_permission_ids,
    SYSTEM_ROLE_PERMISSIONS,
)
from app.core.domain.aggregate import AggregateRoot
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.user_entities import UserEntity


class OrganizationRole(str, Enum):
    """Roles for organization membership."""

    ORG_OWNER = "ORG_OWNER"
    ORG_EDITOR = "ORG_EDITOR"
    ORG_MEMBER = "ORG_MEMBER"


def org_role_permission_ids(role: "OrganizationRole") -> frozenset[str]:
    """What an organization role lets its holder do.

    Read from the same table the authorization layer resolves a request's
    ``Context`` from, so the answer here cannot drift from what the role
    actually carries. ``ORG_OWNER`` is ``ORG_EDITOR`` plus billing, and
    ``ORG_EDITOR`` is ``ORG_MEMBER`` plus everything that manages people.
    """
    return SYSTEM_ROLE_PERMISSIONS[role.value]


def org_role_holds(role: "OrganizationRole", permission_id: str) -> bool:
    """Whether ``role`` carries ``permission_id``, implied permissions included."""
    return bool(
        equivalent_permission_ids(permission_id) & org_role_permission_ids(role)
    )


def can_grant_org_role(
    approver_role: "OrganizationRole", target_role: "OrganizationRole"
) -> bool:
    """Whether ``approver_role`` may hand ``target_role`` to somebody.

    Nobody confers a permission they do not hold (PS-ACCESS-010), so the test is
    a comparison of permission *sets*: every permission ``target_role`` carries
    must be one ``approver_role`` carries. An owner confers anything; an editor
    confers editor and member but not owner, whose only extra is billing; a
    member confers member.

    This was a rank cap that let only owners hand out anything above member,
    which kept editors from doing the one job the role exists for. It also
    covers the side channels -- approving a join request, inviting -- because
    they all ask this one question.
    """
    # Ownership is more than the permissions the catalog lists for it: the
    # authorizer reaches every pod in the organization on the *name* ORG_OWNER,
    # and changing the organization itself is owner-only. Neither is a
    # permission a set comparison can see, so the comparison alone would stop
    # protecting ownership the day some permission moved from owner to editor.
    if target_role == OrganizationRole.ORG_OWNER:
        return approver_role == OrganizationRole.ORG_OWNER
    held = org_role_permission_ids(approver_role)
    return all(
        equivalent_permission_ids(permission_id) & held
        for permission_id in org_role_permission_ids(target_role)
    )


def can_act_on_org_member(
    actor_role: "OrganizationRole", member_role: "OrganizationRole"
) -> bool:
    """Whether ``actor_role`` may change or remove a member holding ``member_role``.

    Nobody reaches over a person who holds authority they lack: an editor cannot
    demote or remove an owner. Stated as the same comparison as granting,
    because taking away a permission you do not hold is the mirror of giving it
    (PS-ONB-042).
    """
    return can_grant_org_role(actor_role, member_role)


class OrganizationJoinPolicy(str, Enum):
    """Who may self-join an organization, ordered from closed to open."""

    INVITE_ONLY = "INVITE_ONLY"  # default — invitation/approval only
    EMAIL_DOMAIN = "EMAIL_DOMAIN"  # users whose email domain matches self-join
    PUBLIC = "PUBLIC"  # any Lemma user may self-join


class OrganizationInvitationStatus(str, Enum):
    """Statuses for organization invitations."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class OrganizationEntity(AggregateRoot):
    """Organization aggregate root."""

    name: str
    slug: str
    email_domain: str | None = None
    join_policy: OrganizationJoinPolicy = OrganizationJoinPolicy.INVITE_ONLY


class OrganizationMemberEntity(AggregateRoot):
    """Organization member entity."""

    user_id: UUID
    organization_id: UUID
    role: OrganizationRole
    user: UserEntity | None = None

    def update_role(self, new_role: OrganizationRole) -> None:
        self.role = new_role


class OrganizationInvitationEntity(AggregateRoot):
    """Organization invitation aggregate root."""

    email: str
    organization_id: UUID
    organization_name: str | None = None
    role: OrganizationRole
    pod_id: UUID | None = None
    pod_role: str | None = None
    redirect_uri: str | None = None
    pod_name: str | None = None
    pod_description: str | None = None
    status: OrganizationInvitationStatus = OrganizationInvitationStatus.PENDING
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=7)
    )
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> str:
        return normalize_identity_email(str(value))

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.status != OrganizationInvitationStatus.PENDING:
            return False
        comparison_time = now or datetime.now(timezone.utc)
        return self.expires_at <= comparison_time

    def mark_expired(self, now: datetime | None = None) -> None:
        self.status = OrganizationInvitationStatus.EXPIRED
        self.updated_at = now or datetime.now(timezone.utc)

    def mark_created(
        self,
        *,
        organization_name: str,
        invited_by_user_id: UUID,
        invited_by_email: str,
        accept_url: str,
        pod_name: str | None = None,
        pod_description: str | None = None,
    ) -> None:
        from app.modules.identity.domain.events import (
            OrganizationInvitationCreatedEvent,
        )

        self.add_event(
            OrganizationInvitationCreatedEvent(
                invitation_id=self.id,
                organization_id=self.organization_id,
                organization_name=organization_name,
                invited_email=self.email,
                role=self.role.value,
                invited_by_user_id=invited_by_user_id,
                invited_by_email=invited_by_email,
                accept_url=accept_url,
                pod_name=pod_name,
                pod_description=pod_description,
            )
        )

    def mark_accepted(
        self,
        *,
        accepted_user_id: UUID,
        accepted_email: str,
        organization_name: str,
    ) -> None:
        from app.modules.identity.domain.events import (
            OrganizationInvitationAcceptedEvent,
        )

        now = datetime.now(timezone.utc)
        self.status = OrganizationInvitationStatus.ACCEPTED
        self.accepted_at = now
        self.updated_at = now
        self.add_event(
            OrganizationInvitationAcceptedEvent(
                invitation_id=self.id,
                organization_id=self.organization_id,
                organization_name=organization_name,
                accepted_user_id=accepted_user_id,
                accepted_email=accepted_email,
                role=self.role.value,
            )
        )

    def mark_revoked(self, now: datetime | None = None) -> None:
        revoked_at = now or datetime.now(timezone.utc)
        self.status = OrganizationInvitationStatus.REVOKED
        self.revoked_at = revoked_at
        self.updated_at = revoked_at
