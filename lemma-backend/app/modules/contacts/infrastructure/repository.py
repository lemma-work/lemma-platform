"""Reading and writing contacts and their handles."""

from __future__ import annotations

import re

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import (
    column,
    delete,
    exists,
    func,
    literal,
    select,
    table,
    tuple_,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.domain.entities import (
    Contact,
    ContactHandle,
    IdentityKind,
    IdentityStrength,
    normalize_handle,
)
from app.modules.contacts.domain.handles import issuer_of
from app.modules.contacts.infrastructure.models import (
    ContactIdentityModel,
    ContactModel,
)

#: The most contacts one page of a listing returns.
MAX_PAGE = 200


#: Contacts merged into one, read a page at a time.
ABSORBED_PAGE = 500


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
        issuer=row.issuer,
        value_raw=row.value_raw,
    )


def _contact(row: ContactModel, identities: Sequence[ContactIdentityModel]) -> Contact:
    return Contact(
        id=row.id,
        pod_id=row.pod_id,
        display_name=row.display_name,
        created_at=row.created_at,
        identities=tuple(_identity(identity) for identity in identities),
        merged_into_id=row.merged_into_id,
        answered_at=row.answered_at,
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

    async def names(
        self, *, pod_id: UUID, contact_ids: Sequence[UUID]
    ) -> dict[UUID, str | None]:
        """What to call each of these contacts: the name they were given, if any."""
        if not contact_ids:
            return {}
        rows = await self.session.execute(
            select(ContactModel.id, ContactModel.display_name).where(
                ContactModel.pod_id == pod_id,
                ContactModel.id.in_(list(contact_ids)),
            )
        )
        return dict(rows.tuples().all())

    async def names_across(
        self, *, contact_ids_by_pod: Mapping[UUID, Sequence[UUID]]
    ) -> dict[UUID, str | None]:
        """``names`` for several pods' contacts in one read.

        Each contact is named only under the pod it was asked for: a contact
        id that belongs to another pod than the one it came with is left out,
        exactly as ``names`` would leave it out.
        """
        wanted = {
            (pod_id, contact_id)
            for pod_id, contact_ids in contact_ids_by_pod.items()
            for contact_id in contact_ids
        }
        if not wanted:
            return {}
        rows = await self.session.execute(
            select(ContactModel.pod_id, ContactModel.id, ContactModel.display_name)
            .where(
                ContactModel.pod_id.in_(sorted({pod_id for pod_id, _ in wanted})),
                ContactModel.id.in_(sorted({contact_id for _, contact_id in wanted})),
            )
            .limit(len(wanted))
        )
        return {
            contact_id: name
            for pod_id, contact_id, name in rows.tuples()
            if (pod_id, contact_id) in wanted
        }

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

    async def find_by_user_part(
        self, *, pod_id: UUID, kind: IdentityKind, user_id: str
    ) -> Contact | None:
        """The contact whose ``{workspace}:{user}`` handle has this user part,
        when exactly one does: a Slack mention names the user, not the
        workspace, and a user id is one person across workspaces."""
        if not user_id or ":" in user_id:
            return None
        escaped = user_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = await self.session.scalars(
            select(ContactIdentityModel.contact_id)
            .where(
                ContactIdentityModel.pod_id == pod_id,
                ContactIdentityModel.kind == kind.value,
                ContactIdentityModel.value.like(f"%:{escaped}", escape="\\"),
            )
            .distinct()
            .limit(2)
        )
        found = list(rows)
        if len(found) != 1:
            return None
        return await self.get(pod_id=pod_id, contact_id=found[0])

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
                        issuer=issuer_of(kind, handle),
                        value_raw=value.strip()[:320],
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
        self,
        *,
        pod_id: UUID,
        limit: int = 50,
        before: ContactCursor | None = None,
        matching: str | None = None,
    ) -> list[Contact]:
        """The pod's contacts, newest first, a page at a time. ``matching``:
        whose name, or a handle as stored or as it arrived, holds these
        characters, in any case."""
        query = select(ContactModel).where(
            ContactModel.pod_id == pod_id, ContactModel.merged_into_id.is_(None)
        )
        if matching:
            pattern = f"%{_escaped(matching)}%"
            # A number typed as it is said -- "+1 555 0100" -- is stored as one.
            compact = f"%{_escaped(re.sub(r'[\s().-]', '', matching))}%"
            handle = ContactIdentityModel
            query = query.where(
                ContactModel.display_name.ilike(pattern, escape="\\")
                | exists().where(
                    handle.contact_id == ContactModel.id,
                    handle.value.ilike(pattern, escape="\\")
                    | handle.value.ilike(compact, escape="\\")
                    | handle.value_raw.ilike(pattern, escape="\\"),
                )
            )
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

    async def absorbed_by(
        self, *, pod_id: UUID, contact_id: UUID, after: UUID | None = None
    ) -> list[UUID]:
        """A page of the contacts merged into this one, after ``after`` -- every
        one of them across the pages, since a merge points them all at the
        survivor directly."""
        statement = select(ContactModel.id).where(
            ContactModel.pod_id == pod_id,
            ContactModel.merged_into_id == contact_id,
        )
        if after is not None:
            statement = statement.where(ContactModel.id > after)
        rows = await self.session.scalars(
            statement.order_by(ContactModel.id).limit(ABSORBED_PAGE)
        )
        return list(rows)

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
        """The contact just wrote from this handle: they want an answer here."""
        await self.session.execute(
            update(ContactIdentityModel)
            .where(
                ContactIdentityModel.pod_id == pod_id,
                ContactIdentityModel.kind == kind.value,
                ContactIdentityModel.value == normalize_handle(kind, value),
            )
            .values(last_inbound_at=datetime.now(timezone.utc))
        )

    async def lock(self, *, pod_id: UUID, contact_ids: Sequence[UUID]) -> list[Contact]:
        """These contacts, row-locked in id order, so two merges of one pair queue.

        Id order on both sides is what keeps two merges over the same contacts
        from each holding one lock and waiting on the other.
        """
        rows = list(
            await self.session.scalars(
                select(ContactModel)
                .where(ContactModel.pod_id == pod_id, ContactModel.id.in_(contact_ids))
                .order_by(ContactModel.id)
                .with_for_update()
            )
        )
        identities: dict[UUID, list[ContactIdentityModel]] = {
            row.id: [] for row in rows
        }
        for identity in await self._identities_of(list(identities)):
            identities[identity.contact_id].append(identity)
        return [_contact(row, identities[row.id]) for row in rows]

    async def attach(
        self,
        *,
        contact: Contact,
        kind: IdentityKind,
        value: str,
        strength: IdentityStrength,
    ) -> bool:
        """Give the contact one more handle; ``False`` if another holds it now.

        A savepoint, like ``open``: a handle proved twice at once is held by
        whichever insert lands first, and the caller plans again from there.
        """
        handle = normalize_handle(kind, value)
        if not handle:
            raise UnusableHandle("A contact needs a handle")
        try:
            async with self.session.begin_nested():
                self.session.add(
                    ContactIdentityModel(
                        contact_id=contact.id,
                        pod_id=contact.pod_id,
                        kind=kind.value,
                        value=handle,
                        strength=strength.value,
                        verified_at=datetime.now(timezone.utc),
                        issuer=issuer_of(kind, handle),
                        value_raw=value.strip()[:320],
                    )
                )
                await self.session.flush()
        except IntegrityError:
            return False
        return True

    async def merge(self, *, survivor: Contact, merged: Contact) -> None:
        """Make ``merged`` into ``survivor``: its handles move, and it points there.

        Anything already merged into ``merged`` is pointed at the survivor too,
        so following a contact to the one it became is always a single step.
        The survivor keeps its own name and takes the other's when it has none.
        """
        await self.session.execute(
            update(ContactIdentityModel)
            .where(ContactIdentityModel.contact_id == merged.id)
            .values(contact_id=survivor.id)
        )
        await self.session.execute(
            update(ContactModel)
            .where(ContactModel.merged_into_id == merged.id)
            .values(merged_into_id=survivor.id)
        )
        await self.session.execute(
            update(ContactModel)
            .where(ContactModel.id == merged.id)
            .values(merged_into_id=survivor.id)
        )
        if survivor.display_name is None and merged.display_name is not None:
            await self.session.execute(
                update(ContactModel)
                .where(ContactModel.id == survivor.id)
                .values(display_name=merged.display_name)
            )
        if merged.answered_at is not None and (
            survivor.answered_at is None or merged.answered_at < survivor.answered_at
        ):
            await self.session.execute(
                update(ContactModel)
                .where(ContactModel.id == survivor.id)
                .values(answered_at=merged.answered_at)
            )

    async def note_answered(self, *, pod_id: UUID, contact_id: UUID) -> bool:
        """Record the first time an agent answered them; whether this was it."""
        result = await self.session.execute(
            update(ContactModel)
            .where(
                ContactModel.id == contact_id,
                ContactModel.pod_id == pod_id,
                ContactModel.answered_at.is_(None),
            )
            .values(answered_at=datetime.now(timezone.utc))
        )
        return bool(result.rowcount)

    async def note_never_answered(self, *, pod_id: UUID, contact_id: UUID) -> bool:
        """Take back ``note_answered``; whether they had been marked."""
        result = await self.session.execute(
            update(ContactModel)
            .where(
                ContactModel.id == contact_id,
                ContactModel.pod_id == pod_id,
                ContactModel.answered_at.is_not(None),
            )
            .values(answered_at=None)
        )
        return bool(result.rowcount)

    async def answered_in_organization(self, organization_id: UUID) -> int:
        """How many contacts any agent of the organization's pods has answered.

        Merged contacts are counted once, as the contact they became.
        """
        pods = table("pods", column("id"), column("organization_id"))
        total = await self.session.scalar(
            select(func.count(ContactModel.id))
            .join(pods, pods.c.id == ContactModel.pod_id)
            .where(
                pods.c.organization_id == organization_id,
                ContactModel.answered_at.is_not(None),
                ContactModel.merged_into_id.is_(None),
            )
        )
        return int(total or 0)

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


def _escaped(text: str) -> str:
    """``text`` matched as itself in a LIKE pattern."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clean_name(display_name: str | None) -> str | None:
    cleaned = (display_name or "").strip()
    return cleaned[:255] or None
