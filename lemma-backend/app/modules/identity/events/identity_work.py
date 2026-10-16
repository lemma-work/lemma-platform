"""Identity's outbound messages as delivery work items.

Each is written in the transaction that makes it owed -- the sign-up, the
invitation, the accepted invitation, the phone change, the verification code
that arrived -- keyed by the event that caused it, so the message exists
exactly when its cause commits and the same cause queued twice sends once.

Both kinds are ``delivery`` JOBs: they send mail or talk to WhatsApp and
Redis, after any database read has closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work.writer import HasSession
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    facts,
    org_namespace,
    registry as default_registry,
)
from app.modules.identity.contracts.profile_facts import UserMobileChanged
from app.modules.identity.domain.events import (
    OrganizationInvitationAcceptedEvent,
    OrganizationInvitationCreatedEvent,
    UserMobileChangedEvent,
    UserPhoneReplacedEvent,
    UserSignedUpEvent,
)
from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.domain.ports import IdentityEmailPort

IDENTITY_EMAIL = "identity.email"
IDENTITY_WHATSAPP_VERIFICATION = "identity.whatsapp_verification"

#: The events that owe somebody an email.
_EMAIL_EVENTS: tuple[type[DomainEvent], ...] = (
    UserSignedUpEvent,
    UserPhoneReplacedEvent,
    OrganizationInvitationCreatedEvent,
    OrganizationInvitationAcceptedEvent,
)


class IdentityEmailPayload(BaseModel):
    event_type: str
    #: The event as it was raised. A snapshot on purpose: an invitation's
    #: accept link and the inviter's address are not state anything can
    #: re-read later.
    event: dict[str, JsonValue]


class WhatsAppVerificationPayload(BaseModel):
    code: str
    sender_wa_id: str
    destination_phone_number_id: str
    whatsapp_message_id: str


def _email_port() -> IdentityEmailPort:
    from app.modules.identity.infrastructure.adapters.email_adapter import (
        SmtpIdentityEmailAdapter,
    )

    return SmtpIdentityEmailAdapter()


class PhoneNoticeNotDelivered(RuntimeError):
    """The phone-change notice was refused; retried under the attempt budget."""


async def send_identity_email(
    event_type: str, event: Mapping[str, JsonValue], email_port: IdentityEmailPort
) -> None:
    """Send the one email ``event`` owes. Unknown types send nothing."""
    if event_type == UserPhoneReplacedEvent.get_event_type():
        phone = UserPhoneReplacedEvent.model_validate(event)
        if not await email_port.send_phone_changed_email(
            to_email=phone.email, mobile_number=phone.mobile_number
        ):
            raise PhoneNoticeNotDelivered("Phone change notice could not be delivered")
        return
    if event_type == OrganizationInvitationCreatedEvent.get_event_type():
        invited = OrganizationInvitationCreatedEvent.model_validate(event)
        await email_port.send_invitation_email(
            to_email=invited.invited_email,
            organization_name=invited.organization_name,
            inviter_email=invited.invited_by_email,
            role=OrganizationRole(invited.role),
            accept_url=invited.accept_url,
            pod_name=invited.pod_name,
            pod_description=invited.pod_description,
        )
        return
    if event_type == UserSignedUpEvent.get_event_type():
        signed_up = UserSignedUpEvent.model_validate(event)
        await email_port.send_signup_welcome_email(
            to_email=signed_up.email, first_name=signed_up.first_name
        )
        return
    if event_type == OrganizationInvitationAcceptedEvent.get_event_type():
        accepted = OrganizationInvitationAcceptedEvent.model_validate(event)
        await email_port.send_invitation_accepted_email(
            to_email=accepted.accepted_email,
            organization_name=accepted.organization_name,
            role=OrganizationRole(accepted.role),
        )


async def run_identity_email(ctx: JobContext, payload: IdentityEmailPayload) -> None:
    del ctx
    await send_identity_email(payload.event_type, payload.event, _email_port())


async def run_whatsapp_verification(
    ctx: JobContext, payload: WhatsAppVerificationPayload
) -> None:
    from app.modules.identity.services.whatsapp_mobile_verification import (
        get_whatsapp_mobile_verification_service,
    )

    del ctx
    # Claimed per WhatsApp message id inside the service, so a repeat of this
    # item answers the sender again rather than verifying twice.
    await get_whatsapp_mobile_verification_service().consume_message(
        code=payload.code,
        sender_wa_id=payload.sender_wa_id,
        destination_phone_number_id=payload.destination_phone_number_id,
        whatsapp_message_id=payload.whatsapp_message_id,
    )


def register_identity_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=IDENTITY_EMAIL,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=IdentityEmailPayload,
    )(run_identity_email)
    registry.handler(
        kind=IDENTITY_WHATSAPP_VERIFICATION,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=WhatsAppVerificationPayload,
    )(run_whatsapp_verification)


register_identity_work(default_registry)


def _namespace(event: DomainEvent) -> str:
    organization_id = getattr(event, "organization_id", None)
    if isinstance(organization_id, UUID):
        return org_namespace(organization_id)
    return SYSTEM_NAMESPACE


async def stage_identity_work(
    uow: SqlAlchemyUnitOfWork, events: Sequence[DomainEvent]
) -> None:
    """Write the email each of ``events`` owes, on ``uow``'s transaction.

    A changed mobile number is also announced as the ``UserMobileChanged``
    fact in the same transaction, so what was bound to the old number (a
    surface identity) is unbound by work that commits with the change.
    """
    work = WorkWriter.of(uow)
    for event in events:
        if isinstance(event, UserMobileChangedEvent):
            await emit_mobile_changed(uow, event.user_id, writer=work)
            continue
        if not isinstance(event, _EMAIL_EVENTS):
            continue
        await work.add(
            IDENTITY_EMAIL,
            _namespace(event),
            IdentityEmailPayload(
                event_type=event.event_type, event=event.model_dump(mode="json")
            ),
            dedup_key=f"identity-email:{event.event_id}",
        )


@dataclass(frozen=True, slots=True)
class _OnSession:
    session: AsyncSession


async def emit_mobile_changed(
    on: HasSession | AsyncSession, user_id: UUID, *, writer: WorkWriter | None = None
) -> None:
    """Announce a changed (or cleared) mobile number in that transaction."""
    holder = _OnSession(on) if isinstance(on, AsyncSession) else on
    await facts.emit(holder, UserMobileChanged(user_id=user_id), writer=writer)


async def add_whatsapp_verification_work(
    uow: SqlAlchemyUnitOfWork, payload: WhatsAppVerificationPayload
) -> None:
    """Write the verification of one received code, keyed by its message."""
    await WorkWriter.of(uow).add(
        IDENTITY_WHATSAPP_VERIFICATION,
        SYSTEM_NAMESPACE,
        payload,
        dedup_key=f"whatsapp-verification:{payload.whatsapp_message_id}",
    )
