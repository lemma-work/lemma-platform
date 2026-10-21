"""Proving a handle: the one place two contacts can become one.

``domain/merging`` decides what proving a handle comes to; this carries it out
against the pod's rows. A merge moves what this module keeps -- the handles,
the web sessions -- in the transaction that proved the handle, and announces
``ContactMerged`` for what other modules keep: the agent repoints the merged
contact's conversations, the datastore its rows. Those follow as work items
written in the same transaction, so a merge cannot happen without them.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.core.log.log import get_logger
from app.modules.contacts.contracts.facts import ContactMerged
from app.modules.contacts.domain.entities import (
    Contact,
    IdentityKind,
    IdentityStrength,
    normalize_handle,
)
from app.modules.contacts.domain.merging import ProofStep, plan_proof
from app.modules.contacts.infrastructure.repository import (
    ContactRepository,
    UnusableHandle,
)
from app.modules.contacts.infrastructure.visitor_sessions import (
    VisitorSessionRepository,
)

logger = get_logger(__name__)

#: A proof that loses a race re-reads and plans again; twice is enough for the
#: one competing writer a handle can have.
_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class ProvenContact:
    """Who the person is once the handle is proved, and whom they absorbed."""

    contact: Contact
    merged_id: UUID | None = None


class ContactProofs:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.contacts = ContactRepository(uow.session)
        self.sessions = VisitorSessionRepository(uow.session)

    async def prove(
        self,
        *,
        pod_id: UUID,
        claimant_id: UUID | None,
        kind: IdentityKind,
        value: str,
        strength: IdentityStrength,
        display_name: str | None = None,
    ) -> ProvenContact:
        """The contact this person is, having proved they hold ``kind:value``.

        ``claimant_id`` is who they are so far, if anybody -- an anonymous web
        visitor's contact, say. Raises ``ProofTooWeak`` when the handle is
        another contact's and ``strength`` proves nothing, and
        ``UnusableHandle`` for a handle with nothing left once normalised.
        """
        handle = normalize_handle(kind, value)
        if not handle:
            raise UnusableHandle("A contact needs a handle")
        for _ in range(_ATTEMPTS):
            proven = await self._attempt(
                pod_id=pod_id,
                claimant_id=claimant_id,
                kind=kind,
                handle=handle,
                strength=strength,
                display_name=display_name,
            )
            if proven is not None:
                return proven
        raise LookupError("A handle kept changing hands while it was proved")

    async def _attempt(
        self,
        *,
        pod_id: UUID,
        claimant_id: UUID | None,
        kind: IdentityKind,
        handle: str,
        strength: IdentityStrength,
        display_name: str | None,
    ) -> ProvenContact | None:
        holder = await self.contacts.find_by_handle(
            pod_id=pod_id, kind=kind, value=handle
        )
        claimant = await self._current(pod_id, claimant_id)
        plan = plan_proof(claimant=claimant, holder=holder, strength=strength)
        if plan.step is ProofStep.OPEN:
            opened = await self.contacts.open(
                pod_id=pod_id,
                kind=kind,
                value=handle,
                strength=strength,
                display_name=display_name,
            )
            return ProvenContact(opened)
        if plan.step is ProofStep.ATTACH:
            if plan.survivor is None:  # pragma: no cover - ATTACH names one
                raise LookupError("A proof to attach named nobody")
            if not await self.contacts.attach(
                contact=plan.survivor, kind=kind, value=handle, strength=strength
            ):
                return None
            return ProvenContact(await self._reread(plan.survivor))
        if plan.step is ProofStep.MERGE:
            return await self._merge(pod_id, plan.survivor, plan.merged)
        if plan.survivor is None:  # pragma: no cover - every other step names one
            raise LookupError("A proof named nobody")
        return ProvenContact(plan.survivor)

    async def _merge(
        self, pod_id: UUID, survivor: Contact | None, merged: Contact | None
    ) -> ProvenContact | None:
        if survivor is None or merged is None:  # pragma: no cover - MERGE names both
            raise LookupError("A merge named one contact")
        locked = {
            contact.id: contact
            for contact in await self.contacts.lock(
                pod_id=pod_id, contact_ids=[survivor.id, merged.id]
            )
        }
        survivor, merged = locked.get(survivor.id), locked.get(merged.id)
        if (
            survivor is None
            or merged is None
            or survivor.merged_into_id is not None
            or merged.merged_into_id is not None
        ):
            # Another merge got there first, or one of them was forgotten:
            # plan again from what is there now.
            return None
        await self.contacts.merge(survivor=survivor, merged=merged)
        await self.sessions.repoint_contact(
            merged_id=merged.id, survivor_id=survivor.id
        )
        await facts.emit(
            self.uow,
            ContactMerged(pod_id=pod_id, survivor_id=survivor.id, merged_id=merged.id),
        )
        logger.info(
            "contacts.contact_merged.observed",
            pod_id=str(pod_id),
            survivor_id=str(survivor.id),
            merged_id=str(merged.id),
        )
        return ProvenContact(await self._reread(survivor), merged_id=merged.id)

    async def _current(self, pod_id: UUID, contact_id: UUID | None) -> Contact | None:
        """The contact ``contact_id`` names now, following a merge."""
        if contact_id is None:
            return None
        contact = await self.contacts.get(pod_id=pod_id, contact_id=contact_id)
        if contact is not None and contact.merged_into_id is not None:
            contact = await self.contacts.get(
                pod_id=pod_id, contact_id=contact.merged_into_id
            )
        return contact

    async def _reread(self, contact: Contact) -> Contact:
        found = await self.contacts.get(pod_id=contact.pod_id, contact_id=contact.id)
        if found is None:  # pragma: no cover - locked or written in this transaction
            raise LookupError("A contact vanished while a handle was proved")
        return found
