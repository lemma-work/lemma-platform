"""A contact who proved to be one of the pod's members.

Somebody the pod took for a stranger -- a member whose platform account it did
not know -- proves the account is theirs (``agent_surfaces``'
``telegram_identity_link``). In each pod where they are a member, the contact
the pod made for that account is them: the handle is taken from it, and a
contact that was nothing but that handle is marked as the member
(``merged_into_user_id``), holds nothing, and is no longer listed or counted.
``ContactBecameMember`` tells the channels, which move the contact's own lines
and seats in the pod's groups onto the member.

A contact with other handles too keeps them and stays a contact: one proof
about one account says nothing about the rest, and the caller tells the pod's
administrators so a person can look.

Only on proof, never on a name: nothing here is reached any other way.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.core.log.log import get_logger
from app.modules.contacts.contracts.facts import ContactBecameMember
from app.modules.contacts.domain.entities import IdentityKind
from app.modules.contacts.infrastructure.repository import ContactRepository

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MemberFold:
    pod_id: UUID
    contact_id: UUID
    #: The contact was only this handle, and is now the member. ``False``: it
    #: keeps other handles, and only this one was taken from it.
    folded: bool


async def fold_into_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    kind: IdentityKind,
    value: str,
    user_id: UUID,
    pods: Collection[UUID],
) -> list[MemberFold]:
    """In each of ``pods`` -- the ones the person is a member of -- the contact
    holding this handle becomes them."""
    repository = ContactRepository(uow.session)
    done: list[MemberFold] = []
    for holder in await repository.holders_of(kind=kind, value=value):
        if holder.pod_id not in pods:
            continue
        [contact] = await repository.lock(
            pod_id=holder.pod_id, contact_ids=[holder.id]
        ) or [None]
        if contact is None or contact.merged_into_id is not None:
            continue
        folded = await repository.fold_into_member(
            contact=contact, kind=kind, value=value, user_id=user_id
        )
        if folded:
            await facts.emit(
                uow,
                ContactBecameMember(
                    pod_id=contact.pod_id, contact_id=contact.id, user_id=user_id
                ),
            )
        logger.info(
            "contacts.contact_became_member.observed",
            pod_id=str(contact.pod_id),
            contact_id=str(contact.id),
            folded=folded,
        )
        done.append(
            MemberFold(pod_id=contact.pod_id, contact_id=contact.id, folded=folded)
        )
    return done
