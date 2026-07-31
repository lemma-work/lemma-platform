from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import UUID

from pydantic import Field
from pydantic import field_validator

from app.core.domain.aggregate import AggregateRoot
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.user_entities import UserEntity


class OrganizationRole(str, Enum):
    """Roles for organization membership."""

    ORG_OWNER = "ORG_OWNER"
    ORG_EDITOR = "ORG_EDITOR"
    ORG_MEMBER = "ORG_MEMBER"


# Organization roles ordered from least to most privileged. Mirrors the implicit
# hierarchy already relied on in organization_service (e.g. an editor cannot
# remove an owner).
ORG_ROLE_HIERARCHY: dict["OrganizationRole", int] = {
    OrganizationRole.ORG_MEMBER: 1,
    OrganizationRole.ORG_EDITOR: 2,
    OrganizationRole.ORG_OWNER: 3,
}


def can_grant_org_role(
    approver_role: "OrganizationRole", target_role: "OrganizationRole"
) -> bool:
    """Whether ``approver_role`` may grant ``target_role`` to another member.

    Only an ORG_OWNER may grant an elevated role (ORG_OWNER/ORG_EDITOR); every
    lesser approver is capped at ORG_MEMBER. This blocks an editor — or a pod
    admin who is only an org member — from minting an org owner/editor through a
    side channel such as approving a join request.
    """
    if approver_role == OrganizationRole.ORG_OWNER:
        return True
    return (
        ORG_ROLE_HIERARCHY[target_role]
        <= ORG_ROLE_HIERARCHY[OrganizationRole.ORG_MEMBER]
    )


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
