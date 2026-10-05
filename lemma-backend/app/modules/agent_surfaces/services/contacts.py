"""Answering somebody outside the pod who writes to its bot privately.

Routing has refused every private message from somebody who is not in the pod,
pointing them at sign-up or at a request for access. That stays the answer
unless the bot is the pod's own and its contact policy says otherwise (see
``SurfaceContactPolicy``). Then the sender is a **contact**:

* **Who they are** is a handle something trustworthy vouched for: the WhatsApp
  number in a payload Meta signed, the Telegram user id in one Telegram did, an
  email address the receiving mail service authenticated. Nothing else -- not a
  display name, not an address typed into a message -- makes somebody a contact.
* **Where it runs.** In a private conversation of their own, belonging to the
  member who looks after the bot's contacts and marked with the contact's id,
  so every run in it authorizes as nobody and reads only what is Public (see
  ``agent.domain.outsiders``).
* **How often.** ``ContactTurnLimiter``, per contact and per bot, and a ceiling
  on how many strangers a bot may meet in a day.

**Email that was not authenticated gets no reply at all.** A reply goes to the
``From:`` line, which is text the sender chose, so answering a forged message
mails whoever the forger named. Such mail is parked instead: the member who
looks after contacts is told, and may answer by hand if it is genuine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.redis.counters import incr_with_ttl
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import contact_link_user
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
)
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.agent_surfaces.domain.surface_config import ContactAnswer
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
)
from app.modules.agent_surfaces.services.chat_context_builder import build_chat_context
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.fallback_reply_service import to_sender_alone
from app.modules.agent_surfaces.services.outsider_limits import ContactTurnLimiter
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.contacts.contracts import (
    ContactRef,
    IdentityKind,
    IdentityStrength,
    find_contact,
    note_inbound,
    open_contact,
)
from app.modules.pod.contracts.members import pod_member_id

logger = get_logger(__name__)

#: How long one unauthenticated sender's mail is parked under a single notice.
#: Repeats inside it are logged, not announced: a forged sender can be made to
#: write as often as the forger likes, and the member should hear of it once.
_PARK_NOTICE_SECONDS = 3600

_PARKED_BODY_CHARS = 500


@dataclass(frozen=True, slots=True)
class ContactHandle:
    """A sender's handle, and whether anything vouched for it."""

    kind: IdentityKind
    value: str
    vouched_for: bool


def contact_handle(parsed: ParsedInboundSurfaceEvent) -> ContactHandle | None:
    """The handle this private message names its sender by, if it has one.

    Slack and Teams are not here: a person writing to a workspace bot is in
    that workspace, which is the pod's own people, not its customers.
    """
    platform = parsed.platform
    if platform is SurfacePlatform.WHATSAPP:
        value = parsed.sender_phone or parsed.sender_external_user_id
        return ContactHandle(IdentityKind.PHONE, value, True) if value else None
    if platform is SurfacePlatform.TELEGRAM:
        value = parsed.sender_external_user_id
        return ContactHandle(IdentityKind.TELEGRAM, value, True) if value else None
    if platform.is_email:
        value = parsed.sender_email or parsed.sender_external_user_id
        if not value:
            return None
        return ContactHandle(
            IdentityKind.EMAIL,
            value,
            parsed.sender_authentication == EmailAuthenticationVerdict.PASS,
        )
    return None


def is_pods_own_bot(surface: AgentSurfaceEntity) -> bool:
    """Whether this surface is the pod's own, rather than Lemma's shared bot.

    An email surface always is: it receives on an address of its own. A chat
    bot is when it runs on the pod's own credentials or account -- a shared
    system bot or a pooled number is every pod's at once, and a stranger
    writing to it is writing to Lemma, not to this pod.
    """
    if surface.surface_type.is_email:
        return True
    return (
        surface.account_id is not None
        or surface.credential_mode is not SurfaceCredentialMode.SYSTEM
    )


class ContactBook(Protocol):
    """The pod's contacts, as the door needs them."""

    async def find(
        self, *, pod_id: UUID, kind: IdentityKind, value: str
    ) -> ContactRef | None: ...

    async def open(
        self,
        *,
        pod_id: UUID,
        kind: IdentityKind,
        value: str,
        strength: IdentityStrength,
        display_name: str | None,
    ) -> ContactRef: ...

    async def note_inbound(
        self, *, pod_id: UUID, kind: IdentityKind, value: str
    ) -> None: ...


class ParkedMail(Protocol):
    """Telling the member who looks after contacts about mail nobody answered."""

    async def tell(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        owner: UUID,
    ) -> None: ...


class PodContactBook:
    """``ContactBook`` over the contacts module, on this unit of work."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def find(
        self, *, pod_id: UUID, kind: IdentityKind, value: str
    ) -> ContactRef | None:
        return await find_contact(self.uow, pod_id=pod_id, kind=kind, value=value)

    async def open(
        self,
        *,
        pod_id: UUID,
        kind: IdentityKind,
        value: str,
        strength: IdentityStrength,
        display_name: str | None,
    ) -> ContactRef:
        return await open_contact(
            self.uow,
            pod_id=pod_id,
            kind=kind,
            value=value,
            strength=strength,
            display_name=display_name,
        )

    async def note_inbound(
        self, *, pod_id: UUID, kind: IdentityKind, value: str
    ) -> None:
        await note_inbound(self.uow, pod_id=pod_id, kind=kind, value=value)


class InboxParkedMail:
    """``ParkedMail`` as an inbox note, once an hour per sender."""

    def __init__(self, uow: SqlAlchemyUnitOfWork, *, redis=None) -> None:
        self.uow = uow
        self._redis = redis

    async def tell(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        owner: UUID,
    ) -> None:
        sender = (parsed.sender_email or parsed.sender_external_user_id or "")[:320]
        if not await self._first_notice(surface.id, sender):
            return
        member_id = await pod_member_id(self.uow, surface.pod_id, owner)
        if member_id is None:
            return
        await NotificationRepository(self.uow).create(
            parked_mail_notice(
                surface=surface, parsed=parsed, owner=owner, member_id=member_id
            )
        )

    async def _first_notice(self, surface_id: UUID, sender: str) -> bool:
        try:
            count = await incr_with_ttl(
                self._redis or get_redis(),
                f"contact:parked:{surface_id}:{sender.lower()}",
                _PARK_NOTICE_SECONDS,
            )
        except (RedisError, OSError) as exc:
            # Unannounced rather than announced twice: the mail is still in the
            # sender's outbox, and a flood of notices is the worse failure.
            logger.warning(
                "agent_surfaces.contacts.park_notice_unavailable.degraded",
                error_type=type(exc).__name__,
            )
            return False
        return count == 1


def parked_mail_notice(
    *,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    owner: UUID,
    member_id: UUID,
) -> NotificationEntity:
    """The inbox note for one unanswered, unauthenticated email."""
    sender = (parsed.sender_email or parsed.sender_external_user_id or "")[:320]
    subject = str(parsed.metadata.get("subject") or "").strip()
    excerpt = parsed.message_text.strip()[:_PARKED_BODY_CHARS]
    return NotificationEntity(
        pod_id=surface.pod_id,
        recipient_user_id=owner,
        recipient_pod_member_id=member_id,
        origin_kind=NotificationOriginKind.API,
        title=f"Unverified email from {sender}"[:120],
        body=(
            f"An email to {surface.surface_identity_email or surface.name} could "
            "not be verified as coming from the address it names, so the bot did "
            "not reply: a reply would go to that address, whoever really sent it. "
            "If it is genuine, answer it from your own email.\n\n"
            + (f"Subject: {subject}\n\n" if subject else "")
            + excerpt
        ),
        expects_response=False,
        delivery_status=NotificationDeliveryStatus.UNDELIVERABLE,
    )


class ContactDoor:
    """Decides whether a private message from outside the pod is a contact's."""

    def __init__(
        self,
        *,
        membership: SurfacePodMembershipPort,
        book: ContactBook,
        parked_mail: ParkedMail,
        limiter: ContactTurnLimiter,
    ) -> None:
        self.membership = membership
        self.book = book
        self.parked_mail = parked_mail
        self.limiter = limiter

    @classmethod
    def for_unit_of_work(
        cls, uow: SqlAlchemyUnitOfWork, *, membership: SurfacePodMembershipPort
    ) -> "ContactDoor":
        return cls(
            membership=membership,
            book=PodContactBook(uow),
            parked_mail=InboxParkedMail(uow),
            limiter=ContactTurnLimiter(),
        )

    async def applies(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
    ) -> bool:
        """Whether this message is for the contact path rather than refusal.

        A member is never a contact, wherever they write from: they route as
        themselves. Asked the way routing asks it, so a sender cannot be a
        member to one and a contact to the other.
        """
        policy = surface.config.contacts
        if (
            not parsed.is_dm
            or policy.answer is ContactAnswer.OFF
            or not is_pods_own_bot(surface)
            or contact_handle(parsed) is None
        ):
            return False
        if sender.internal_user_id is None:
            return True
        return not await self._in_pod(sender.internal_user_id, surface.pod_id)

    async def prepare(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        sender: ResolvedSurfaceUser,
        route: ResolvedSurfaceRoute,
        binder: ConversationBinder,
    ) -> SurfaceChatContext | None:
        """The contact's run, or None when nothing should run.

        None covers every quiet outcome: unauthenticated mail (parked), a
        stranger at a bot that answers only known contacts, nobody left to look
        after contacts, and a limit reached. None of them gets a reply -- each is
        either somebody we cannot be sure is there, or somebody we already
        decided not to answer.
        """
        handle = contact_handle(parsed)
        owner = surface.config.contacts.looked_after_by
        if handle is None or owner is None:
            return None
        # A member who has left the pod looks after nothing: every question
        # passed on would reach somebody who can no longer act on it.
        if not await self._in_pod(owner, surface.pod_id):
            logger.info(
                "agent_surfaces.contacts.nobody_looks_after.observed",
                surface_id=str(surface.id),
            )
            return None
        if not handle.vouched_for:
            logger.info(
                "agent_surfaces.contacts.unverified_email_parked.observed",
                surface_id=str(surface.id),
            )
            await self.parked_mail.tell(surface=surface, parsed=parsed, owner=owner)
            return None
        contact = await self._contact(surface=surface, handle=handle, parsed=parsed)
        if contact is None:
            return None
        if not await self.limiter.allow(surface_id=surface.id, contact_id=contact.id):
            return None
        await self.book.note_inbound(
            pod_id=surface.pod_id, kind=handle.kind, value=handle.value
        )
        # Only the contact. On an email thread a reply would otherwise copy
        # everybody else on it, and a contact's conversation is theirs.
        parsed = to_sender_alone(parsed)
        link, created_title = await binder.bind_conversation(
            surface=surface,
            parsed=parsed,
            resolved_user=ResolvedSurfaceUser(
                internal_user_id=owner,
                external_user_id=contact_link_user(contact.id),
                display_name=contact.display_name or sender.display_name,
            ),
            route=route,
            for_contact=contact.id,
        )
        return build_chat_context(
            surface=surface,
            parsed=parsed,
            resolved_user=sender,
            user_id=owner,
            route=route,
            conversation_id=link.conversation_id,
            created_conversation_title=created_title,
            answers_outsider=True,
        )

    async def _in_pod(self, user_id: UUID, pod_id: UUID) -> bool:
        return pod_id in set(await self.membership.get_user_pod_ids(user_id))

    async def _contact(
        self,
        *,
        surface: AgentSurfaceEntity,
        handle: ContactHandle,
        parsed: ParsedInboundSurfaceEvent,
    ) -> ContactRef | None:
        known = await self.book.find(
            pod_id=surface.pod_id, kind=handle.kind, value=handle.value
        )
        if known is not None:
            return known
        if surface.config.contacts.answer is not ContactAnswer.ANYONE:
            logger.info(
                "agent_surfaces.contacts.stranger_refused.observed",
                surface_id=str(surface.id),
            )
            return None
        if not await self.limiter.allow_new_contact(surface_id=surface.id):
            return None
        contact = await self.book.open(
            pod_id=surface.pod_id,
            kind=handle.kind,
            value=handle.value,
            strength=IdentityStrength.CHANNEL,
            display_name=parsed.sender_display_name,
        )
        logger.info(
            "agent_surfaces.contacts.contact_opened.observed",
            surface_id=str(surface.id),
            contact_id=str(contact.id),
        )
        return contact
