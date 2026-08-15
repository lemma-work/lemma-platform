"""Identity module domain events."""

from __future__ import annotations

from uuid import UUID

from app.core.domain.events import DomainEvent


IDENTITY_EVENTS_STREAM = "identity_events"


class UserSignedUpEvent(DomainEvent):
    event_type: str = "identity.user.signed_up"
    user_id: UUID
    email: str
    first_name: str | None = None

    @classmethod
    def stream_name(cls) -> str:
        return IDENTITY_EVENTS_STREAM


class OrganizationCreatedEvent(DomainEvent):
    event_type: str = "identity.organization.created"
    organization_id: UUID
    created_by_user_id: UUID | None = None

    @classmethod
    def stream_name(cls) -> str:
        return IDENTITY_EVENTS_STREAM


class OrganizationMemberAddedEvent(DomainEvent):
    """Somebody joined an organization, by any of the three routes.

    Raised in the repository rather than at the call sites: membership is added
    on org creation, by auto-join, and by accepting an invitation, and only the
    repository sees all three. Projecting the invitation-accepted event instead
    would undercount every organization that grew any other way.
    """

    event_type: str = "identity.organization.member_added"
    organization_id: UUID
    user_id: UUID
    role: str

    @classmethod
    def stream_name(cls) -> str:
        return IDENTITY_EVENTS_STREAM


class OrganizationInvitationCreatedEvent(DomainEvent):
    event_type: str = "identity.organization.invitation.created"
    invitation_id: UUID
    organization_id: UUID
    organization_name: str
    invited_email: str
    role: str
    invited_by_user_id: UUID
    invited_by_email: str
    accept_url: str
    pod_name: str | None = None
    pod_description: str | None = None

    @classmethod
    def stream_name(cls) -> str:
        return IDENTITY_EVENTS_STREAM


class OrganizationInvitationAcceptedEvent(DomainEvent):
    event_type: str = "identity.organization.invitation.accepted"
    invitation_id: UUID
    organization_id: UUID
    organization_name: str
    accepted_user_id: UUID
    accepted_email: str
    role: str

    @classmethod
    def stream_name(cls) -> str:
        return IDENTITY_EVENTS_STREAM


class WhatsAppMobileVerificationReceivedEvent(DomainEvent):
    """A reserved, signed message received by Lemma's global WhatsApp number."""

    event_type: str = "identity.mobile_verification.whatsapp.received"
    code: str
    sender_wa_id: str
    destination_phone_number_id: str
    whatsapp_message_id: str

    @classmethod
    def stream_name(cls) -> str:
        return IDENTITY_EVENTS_STREAM


class UserMobileChangedEvent(DomainEvent):
    """Invalidate external identity caches after a profile phone change."""

    event_type: str = "identity.user.mobile.changed"
    user_id: UUID

    @classmethod
    def stream_name(cls) -> str:
        return IDENTITY_EVENTS_STREAM


class IdentityEvents:
    STREAM = IDENTITY_EVENTS_STREAM
