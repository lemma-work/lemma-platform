"""Reading and writing contacts and their handles."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, literal, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.domain.entities import (
    Contact,
    ContactHandle,
    IdentityKind,
    IdentityStrength,
    normalize_handle,
)
from app.modules.contacts.infrastructure.models import (
    ContactIdentityModel,
    ContactModel,
)

#: The most contacts one page of a listing returns.
MAX_PAGE = 200


class UnusableHandle(ValueError):
    """A handle with nothing left once it is spelled the one way handles are.

    A phone number of punctuation alone, an address of spaces: nobody can be
    found or written to by one, so no contact is opened for it.
    """


@dataclass(frozen=True, slots=True)
class ContactCursor:
    """Where one page of the listing ended: the last contact's place in it.

    Both halves, because two contacts made in one transaction share a
    timestamp, and a cursor of the time alone skips whichever came second.
    """

    created_at: datetime
    contact_id: UUID


def _identity(row: ContactIdentityModel) -> ContactHandle:
    return ContactHandle(
        id=row.id,
        contact_id=row.contact_id,
        kind=IdentityKind(row.kind),
        value=row.value,
        strength=IdentityStrength(row.strength),
        verified_at=row.verified_at,
        last_inbound_at=row.last_inbound_at,
        unsubscribed_at=row.unsubscribed_at,
    )


def _contact(row: ContactModel, identities: Sequence[ContactIdentityModel]) -> Contact:
    return Contact(
        id=row.id,
        pod_id=row.pod_id,
        display_name=row.display_name,
        created_at=row.created_at,
        identities=tuple(_identity(identity) for identity in identities),
    )


class ContactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, *, pod_id: UUID, contact_id: UUID) -> Contact | None:
        row = await self.session.scalar(
            select(ContactModel).where(
                ContactModel.id == contact_id, ContactModel.pod_id == pod_id
            )
        )
        if row is None:
            return None
        return _contact(row, await self._identities_of([row.id]))

    async def find_by_handle(
        self, *, pod_id: UUID, kind: IdentityKind, value: str
    ) -> Contact | None:
        handle = normalize_handle(kind, value)
        if not handle:
            return None
        contact_id = await self.session.scalar(
            select(ContactIdentityModel.contact_id).where(
                ContactIdentityModel.pod_id == pod_id,
                ContactIdentityModel.kind == kind.value,
                ContactIdentityModel.value == handle,
            )
        )
        if contact_id is None:
            return None
        return await self.get(pod_id=pod_id, contact_id=contact_id)

    async def open(
        self,
        *,
        pod_id: UUID,
        kind: IdentityKind,
        value: str,
        strength: IdentityStrength,
        display_name: str | None,
    ) -> Contact:
        """The contact this handle names in this pod, created if there is none.

        Two first messages from one number arrive together often enough on a
        chat platform, and both read "no contact". The insert runs in a
        savepoint, so the loser's unique-constraint failure undoes only its own
        rows and it reads the winner's contact instead.
        """
        handle = normalize_handle(kind, value)
        if not handle:
            raise UnusableHandle("A contact needs a handle")
        existing = await self.find_by_handle(pod_id=pod_id, kind=kind, value=handle)
        if existing is not None:
            return existing
        now = datetime.now(timezone.utc)
        try:
            async with self.session.begin_nested():
                contact = ContactModel(
                    pod_id=pod_id, display_name=_clean_name(display_name)
                )
                self.session.add(contact)
                await self.session.flush()
                self.session.add(
                    ContactIdentityModel(
                        contact_id=contact.id,
                        pod_id=pod_id,
                        kind=kind.value,
                        value=handle,
                        strength=strength.value,
                        verified_at=now,
                    )
                )
                await self.session.flush()
        except IntegrityError:
            winner = await self.find_by_handle(pod_id=pod_id, kind=kind, value=handle)
            if winner is None:
                raise
            return winner
        found = await self.get(pod_id=pod_id, contact_id=contact.id)
        if found is None:  # pragma: no cover - just written in this transaction
            raise LookupError("A contact written in this transaction vanished")
        return found

    async def list(
        self, *, pod_id: UUID, limit: int = 50, before: ContactCursor | None = None
    ) -> list[Contact]:
        """The pod's contacts, newest first, a page at a time."""
        query = select(ContactModel).where(ContactModel.pod_id == pod_id)
        if before is not None:
            query = query.where(
                tuple_(ContactModel.created_at, ContactModel.id)
                < tuple_(
                    literal(before.created_at, ContactModel.created_at.type),
                    literal(before.contact_id, ContactModel.id.type),
                )
            )
        rows = list(
            await self.session.scalars(
                query.order_by(
                    ContactModel.created_at.desc(), ContactModel.id.desc()
                ).limit(max(1, min(limit, MAX_PAGE)))
            )
        )
        identities: dict[UUID, list[ContactIdentityModel]] = {
            row.id: [] for row in rows
        }
        for identity in await self._identities_of(list(identities)):
            identities[identity.contact_id].append(identity)
        return [_contact(row, identities[row.id]) for row in rows]

    async def rename(
        self, *, pod_id: UUID, contact_id: UUID, display_name: str | None
    ) -> bool:
        result = await self.session.execute(
            update(ContactModel)
            .where(ContactModel.id == contact_id, ContactModel.pod_id == pod_id)
            .values(display_name=_clean_name(display_name))
        )
        return bool(result.rowcount)

    async def delete(self, *, pod_id: UUID, contact_id: UUID) -> bool:
        result = await self.session.execute(
            delete(ContactModel).where(
                ContactModel.id == contact_id, ContactModel.pod_id == pod_id
            )
        )
        return bool(result.rowcount)

    async def note_inbound(
        self, *, pod_id: UUID, kind: IdentityKind, value: str
    ) -> None:
        """The contact just wrote from this handle: they want an answer here.

        Writing again is also how a contact who unsubscribed opts back in.
        """
        await self.session.execute(
            update(ContactIdentityModel)
            .where(
                ContactIdentityModel.pod_id == pod_id,
                ContactIdentityModel.kind == kind.value,
                ContactIdentityModel.value == normalize_handle(kind, value),
            )
            .values(last_inbound_at=datetime.now(timezone.utc), unsubscribed_at=None)
        )

    async def unsubscribe(self, *, identity_id: UUID) -> None:
        await self.session.execute(
            update(ContactIdentityModel)
            .where(ContactIdentityModel.id == identity_id)
            .values(unsubscribed_at=datetime.now(timezone.utc))
        )

    async def unsubscribe_handle(
        self, *, pod_id: UUID, kind: IdentityKind, value: str
    ) -> bool:
        """The contact wrote "STOP" from this handle. Whether there was one."""
        result = await self.session.execute(
            update(ContactIdentityModel)
            .where(
                ContactIdentityModel.pod_id == pod_id,
                ContactIdentityModel.kind == kind.value,
                ContactIdentityModel.value == normalize_handle(kind, value),
            )
            .values(unsubscribed_at=datetime.now(timezone.utc))
        )
        return bool(result.rowcount)

    async def _identities_of(
        self, contact_ids: list[UUID]
    ) -> list[ContactIdentityModel]:
        """The handles of these contacts, oldest first, in one read."""
        if not contact_ids:
            return []
        return list(
            await self.session.scalars(
                select(ContactIdentityModel)
                .where(ContactIdentityModel.contact_id.in_(contact_ids))
                .order_by(ContactIdentityModel.created_at)
            )
        )


def _clean_name(display_name: str | None) -> str | None:
    cleaned = (display_name or "").strip()
    return cleaned[:255] or None
