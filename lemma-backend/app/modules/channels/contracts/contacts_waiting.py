"""Contacts waiting on a person, across every pod a member looks after them in.

A pod's channel list answers this for one pod (``needs_person=true``); an
organization's overview wants every pod's at once, and asking per pod is the
fan-out it exists to remove. One statement for the channels, on the two small
indexes ``cases.waiting_on_a_person`` reads, and one for the contacts' names.

**The pods are the caller's to have authorized.** A contact's channel is seen by
whoever holds ``contact.read`` in its pod (``domain.access.may_see``), and the
list behind it needs ``channel.read`` too. The caller decides both per pod --
against that pod's own context -- and passes only the pods where the member
holds them; every contact's channel in those pods is then theirs to see, so
nothing here narrows further.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import ChannelKind
from app.modules.channels.infrastructure.case_models import ChannelCaseModel
from app.modules.channels.infrastructure.cases import waiting_on_a_person
from app.modules.channels.infrastructure.models import ChannelModel
from app.modules.contacts.contracts import contact_names_across


@dataclass(frozen=True, slots=True)
class ContactWaiting:
    """A contact's channel waiting on a person: nobody holds it, it is open,
    and either the contact asked for a person or its agents are off."""

    pod_id: UUID
    channel_id: UUID
    contact_id: UUID | None
    #: The name the pod gave the contact, else the channel's own title.
    name: str | None
    #: Why the contact asked for a person, when it was recorded.
    reason: str | None
    #: When it started waiting: when the contact asked, else its last line.
    since: datetime


def _waiting_channels(pod_ids: Sequence[UUID]) -> list[ColumnElement[bool]]:
    """A contact's channel in these pods that waits on a person, unarchived --
    as the pod's own list shows it."""
    channel = ChannelModel
    return [
        channel.id.in_(waiting_on_a_person(pod_ids)),
        channel.kind == ChannelKind.CONTACT.value,
        channel.archived_at.is_(None),
    ]


async def pods_with_contacts_waiting(
    uow: SqlAlchemyUnitOfWork, *, pod_ids: Sequence[UUID]
) -> set[UUID]:
    """Which of these pods have a contact waiting on a person at all.

    Lets a caller ask who may see contacts only where there is one to see:
    deciding ``contact.read`` can cost a grant lookup in a pod where the
    member's role does not settle it, and on most days no pod has anybody
    waiting.
    """
    if not pod_ids:
        return set()
    rows = await uow.session.execute(
        select(ChannelModel.pod_id)
        .where(*_waiting_channels(pod_ids))
        .group_by(ChannelModel.pod_id)
        .limit(len(pod_ids))
    )
    return set(rows.scalars())


async def contacts_waiting_on_a_person(
    uow: SqlAlchemyUnitOfWork, *, pod_ids: Sequence[UUID], limit: int
) -> tuple[list[ContactWaiting], bool]:
    """Contacts waiting on a person in these pods, longest-waiting first.

    Archived channels are left out, as the pod's own list leaves them out.
    Whether more were waiting than ``limit`` comes back beside the page.
    """
    if not pod_ids:
        return [], False
    channel, case = ChannelModel, ChannelCaseModel
    since = func.coalesce(case.attention_at, channel.last_line_at)
    rows = (
        await uow.session.execute(
            select(
                channel.pod_id,
                channel.id,
                channel.contact_id,
                channel.title,
                case.attention_reason,
                since.label("since"),
            )
            .join(
                case,
                and_(
                    case.id == channel.current_case_id,
                    case.channel_id == channel.id,
                ),
            )
            .where(*_waiting_channels(pod_ids))
            .order_by(since, channel.id)
            .limit(limit + 1)
        )
    ).all()
    page = rows[:limit]
    contacts_by_pod: dict[UUID, list[UUID]] = {}
    for pod_id, _, contact_id, *_rest in page:
        if contact_id is not None:
            contacts_by_pod.setdefault(pod_id, []).append(contact_id)
    names = await contact_names_across(uow, contact_ids_by_pod=contacts_by_pod)
    return [
        ContactWaiting(
            pod_id=pod_id,
            channel_id=channel_id,
            contact_id=contact_id,
            name=(names.get(contact_id) if contact_id else None) or title,
            reason=reason,
            since=waited_since,
        )
        for pod_id, channel_id, contact_id, title, reason, waited_since in page
    ], len(rows) > limit


__all__ = [
    "ContactWaiting",
    "contacts_waiting_on_a_person",
    "pods_with_contacts_waiting",
]
