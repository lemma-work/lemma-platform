"""A member writing to a contact first, on one of the contact's handles.

Most of a contact's conversation starts with them; this is the other way
round -- a follow-up by email, a reply on WhatsApp the next morning. The
member names a handle the contact is known by and writes; the line is theirs,
in the contact's channel, at the place that handle is reached through, and
goes there as a delivery like any line they write in the app.

Where each handle is reached is the platform's rule:

* **Email**, any time: the place for that address on the pod's mailbox,
  opened the first time (the send then starts the thread, ``cold_email_thread``).
* **WhatsApp**, only within 24 hours of the contact's last message to that
  number, and only where they have written before: past it Meta takes only
  approved templates, which are not built here.
* **Telegram**, only where they have written to the bot: a bot cannot start
  a chat.
* Anything else -- a Slack or Teams user, a web visitor, a host app's user --
  is answered where they write, never written to first.

What may be sent is the contacts module's to say (``contacts.contracts.outreach``):
opt-out first, then the handle, then the platform's window. Refused, nothing
is posted; the member reads why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.external_accounts import account_of
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (  # noqa: E501
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.contact_reach import reach_for
from app.modules.agent_surfaces.services.notification_channels import can_cold_open
from app.modules.channels.contracts import (
    ChannelMessage,
    ContactsHome,
    Delivery,
    LineRefused,
    Place,
    Source,
    SourceKind,
    contact_channel,
    source_for,
    sources_of,
    write_as_member,
)
from app.modules.channels.contracts.deliveries import deliveries_of
from app.modules.contacts.contracts import (
    ContactHandle,
    IdentityKind,
    contact_by_id,
    contact_handles,
    normalize_handle,
)
from app.modules.contacts.contracts.outreach import (
    OutreachKind,
    RefusalCode,
    may_send,
)

#: The platforms a handle of each kind is written to on.
_PLATFORMS: dict[IdentityKind, tuple[SurfacePlatform, ...]] = {
    IdentityKind.EMAIL: tuple(p for p in SurfacePlatform if p.is_email),
    IdentityKind.PHONE: (SurfacePlatform.WHATSAPP,),
    IdentityKind.TELEGRAM: (SurfacePlatform.TELEGRAM,),
}

#: The most surfaces of a pod looked through for one that writes email.
_SURFACES_READ = 100


class OutreachRefused(Exception):
    """The member may not write to this contact there; the reason is theirs."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Outreach:
    pod_id: UUID
    contact_id: UUID
    user_id: UUID
    #: One of the contact's handles, as it is shown or as typed.
    handle: str
    text: str
    #: Which kind of handle ``handle`` is, when the value alone could be two.
    kind: IdentityKind | None = None
    client_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class Reached:
    line: ChannelMessage
    delivery: Delivery


class ContactOutreach:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def write(
        self, outreach: Outreach, *, now: datetime | None = None
    ) -> Reached:
        contact = await contact_by_id(
            self.uow, pod_id=outreach.pod_id, contact_id=outreach.contact_id
        )
        if contact is None:
            raise OutreachRefused("Contact not found", status_code=404)
        handle = _the_handle(
            await contact_handles(
                self.uow, pod_id=outreach.pod_id, contact_id=contact.id
            ),
            outreach.handle,
            kind=outreach.kind,
        )
        channel = await contact_channel(
            self.uow, pod_id=outreach.pod_id, contact_id=contact.id
        )
        source = await self._place(channel.id, outreach.pod_id, contact.id, handle)
        await self._allowed(outreach.pod_id, contact.id, handle, source, now=now)
        try:
            line = await write_as_member(
                self.uow,
                channel_id=channel.id,
                user_id=outreach.user_id,
                text=outreach.text,
                source_id=source.id,
                client_message_id=outreach.client_message_id,
            )
        except LineRefused as refused:
            raise OutreachRefused(
                str(refused), status_code=refused.status_code
            ) from refused
        delivery = next(
            (
                delivery
                for delivery in await deliveries_of(self.uow, [line.id])
                if delivery.source_id == source.id
            ),
            None,
        )
        if delivery is None:
            # The key named a line already written for another of their
            # handles: a retry must be the same request.
            raise OutreachRefused(
                "That key was already used for a line to another of their handles.",
                status_code=409,
            )
        return Reached(line=line, delivery=delivery)

    async def _place(
        self, channel_id: UUID, pod_id: UUID, contact_id: UUID, handle: ContactHandle
    ) -> Source:
        """The place this handle is reached at: the one the contact was heard
        from last, else -- email only -- the pod's mailbox's, opened now."""
        platforms = {platform.value for platform in _PLATFORMS.get(handle.kind, ())}
        if not platforms:
            raise OutreachRefused(
                "Lemma cannot write first to a handle like that; answer them "
                "where they write."
            )
        at_handle = [
            source
            for source in await sources_of(self.uow, channel_id)
            if source.platform in platforms
            and not source.is_pending
            and _same(handle, source.external_user_id)
        ]
        if at_handle:
            return at_handle[0]
        if handle.kind is not IdentityKind.EMAIL:
            raise OutreachRefused(
                "They have not written to the pod there yet, and a bot cannot "
                "start that conversation: write to them by email, or wait for "
                "them to write."
            )
        mailbox = await self._mailbox(pod_id)
        if mailbox is None:
            raise OutreachRefused(
                "The pod has no email address to write from: connect one first.",
                status_code=409,
            )
        return await source_for(
            self.uow,
            pod_id=pod_id,
            place=Place(
                surface_id=mailbox.id,
                platform=mailbox.surface_type.value,
                account=account_of(mailbox),
                kind=SourceKind.PERSON,
                # Keyed as their reply is heard: by their address.
                external_channel_id=None,
                external_user_id=handle.value,
            ),
            home=ContactsHome(contact_id=contact_id),
        )

    async def _mailbox(self, pod_id: UUID) -> AgentSurfaceEntity | None:
        surfaces, _ = await SurfaceRepository(self.uow).list_by_pod(
            pod_id, limit=_SURFACES_READ
        )
        return next(
            (
                surface
                for surface in surfaces
                if surface.is_active
                and surface.surface_type.is_email
                and can_cold_open(surface)
            ),
            None,
        )

    async def _allowed(
        self,
        pod_id: UUID,
        contact_id: UUID,
        handle: ContactHandle,
        source: Source,
        *,
        now: datetime | None,
    ) -> None:
        reach = reach_for(
            source.platform,
            address=handle.value,
            # The place's own window where it has been heard from; a place
            # just opened has only the handle's.
            last_inbound_at=source.last_inbound_at or handle.last_inbound_at,
        )
        if reach is None:
            raise OutreachRefused("Lemma cannot write first to a handle like that.")
        address, window = reach
        verdict = await may_send(
            self.uow,
            pod_id=pod_id,
            contact_id=contact_id,
            to=address,
            kind=OutreachKind.MEMBER_LINE,
            at=now or datetime.now(timezone.utc),
            window=window,
        )
        if verdict.allowed:
            return
        raise OutreachRefused(
            verdict.reason or "That cannot be sent to them.",
            status_code=(
                409
                if verdict.code in (RefusalCode.SUPPRESSED, RefusalCode.NO_HANDLE)
                else 503
                if verdict.code is RefusalCode.UNAVAILABLE
                else 422
            ),
        )


def _the_handle(
    handles: list[ContactHandle], value: str, *, kind: IdentityKind | None
) -> ContactHandle:
    """The contact's handle the member named, by its stored or shown value."""
    typed = value.strip()
    for handle in handles:
        if kind is not None and handle.kind is not kind:
            continue
        if (
            typed in (handle.value, handle.value_raw)
            or normalize_handle(handle.kind, typed) == handle.value
        ):
            return handle
    raise OutreachRefused("That is not one of this contact's handles.")


def _same(handle: ContactHandle, external_user_id: str | None) -> bool:
    """Whether a place's person is this handle: a WhatsApp place keeps the
    number without its ``+``, an inbox the address as it was heard."""
    if not external_user_id:
        return False
    raw = external_user_id.strip()
    if handle.kind is IdentityKind.PHONE and not raw.startswith("+"):
        raw = f"+{raw}"
    return normalize_handle(handle.kind, raw) == handle.value
