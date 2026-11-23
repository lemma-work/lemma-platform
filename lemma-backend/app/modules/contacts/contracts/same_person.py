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

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute

from app.modules.contacts.infrastructure.models import ContactModel


def same_person(
    column: InstrumentedAttribute[UUID | None], *, pod_id: UUID, contact_id: UUID
) -> ColumnElement[bool]:
    """Whether ``column`` holds an id of the person ``contact_id`` names: a
    condition for another statement's ``where``, never a read of its own."""
    c = ContactModel
    survivor = (
        select(func.coalesce(c.merged_into_id, c.id))
        .where(c.id == contact_id, c.pod_id == pod_id)
        .scalar_subquery()
    )
    return column.in_(
        select(c.id).where(
            c.pod_id == pod_id,
            or_(c.id == contact_id, c.id == survivor, c.merged_into_id == survivor),
        )
    )


__all__ = ["same_person"]
