"""Whether a column names one contact under any id they have had.

A merge points the absorbed contact, and everything already merged into it,
straight at the survivor: one hop, always. So the person a contact id names
is that contact, the one it was merged into, and every contact merged into
that one. This is that test as SQL, not as a list: embedded in another
statement, it is read from the same snapshot as the rest of that statement,
so a merge committing in between cannot split the two.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ColumnElement, Subquery, and_, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, aliased

from app.modules.contacts.infrastructure.models import ContactModel


def same_person_ids(*, pod_id: UUID) -> Subquery:
    """Each of the pod's contacts (``contact_id``) beside every id of the
    person it names (``same_id``), itself among them: a table for another
    statement to join, never a read of its own."""
    asked = aliased(ContactModel)
    same = aliased(ContactModel)
    survivor = func.coalesce(asked.merged_into_id, asked.id)
    return (
        select(asked.id.label("contact_id"), same.id.label("same_id"))
        .join(
            same,
            and_(
                same.pod_id == asked.pod_id,
                or_(same.id == survivor, same.merged_into_id == survivor),
            ),
        )
        .where(asked.pod_id == pod_id)
        .subquery("same_person_ids")
    )


def same_person(
    column: InstrumentedAttribute[UUID | None], *, pod_id: UUID, contact_id: UUID
) -> ColumnElement[bool]:
    """Whether ``column`` holds an id of the person ``contact_id`` names: a
    condition for another statement's ``where``, never a read of its own."""
    ids = same_person_ids(pod_id=pod_id)
    return column.in_(select(ids.c.same_id).where(ids.c.contact_id == contact_id))


__all__ = ["same_person", "same_person_ids"]
