"""Contacts on the ``org`` topic: who is waiting on a person.

A contact whose channel waits on a person -- an open case nobody holds that
asked for one, or whose agents are off: the channel list's ``needs_person`` --
is told to every reader who may see the pod's contacts (``contact.read``) as
``waiting.started`` (``kind: "contact"``), and as ``waiting.stopped`` once
somebody takes it, it is handed back, or it closes. Every change to a case
moves its channel's change counter, so the ``channel`` signal says when to
read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.org_feeds import Audience, OrgItem, PodItems
from app.modules.channels.infrastructure.cases import contacts_waiting_on_a_person

#: Most waiting contacts one read returns, across the pods it covers.
MAX_WAITING = 1000

_SEES_CONTACTS = Audience(permission=Permissions.CONTACT_READ)


def _waiting(channel_id: UUID, contact_id: UUID | None) -> OrgItem:
    said = {
        "kind": "contact",
        "channel_id": str(channel_id),
        "contact_id": str(contact_id) if contact_id is not None else None,
    }
    return OrgItem(
        audience=_SEES_CONTACTS,
        version="waiting",
        held={"type": "waiting.started", **said},
        ended={"type": "waiting.stopped", **said},
    )


class ContactsWaiting:
    name = "channels.contacts_waiting"
    kinds = frozenset({"channel"})

    async def read(self, pod_ids: Sequence[UUID]) -> Mapping[UUID, PodItems]:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            rows = await contacts_waiting_on_a_person(
                uow.session, pod_ids, limit=MAX_WAITING
            )
        found: dict[UUID, dict[str, OrgItem]] = {}
        for pod_id, channel_id, contact_id in rows:
            found.setdefault(pod_id, {})[str(channel_id)] = _waiting(
                channel_id, contact_id
            )
        return found


__all__ = ["ContactsWaiting"]
