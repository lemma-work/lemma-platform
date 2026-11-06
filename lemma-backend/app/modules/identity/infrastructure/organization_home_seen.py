"""When a member last looked at their organization's Home.

Home says what moved since the member last looked. The moment used to live in
the browser's storage, so a second browser, a phone, or a cleared profile said
everything was new. It is kept in the person's own preferences now
(``UserPreferences.home_seen``), one entry per organization, read and set only
by that person and only while they are a member.

It only moves forward. A tab left open since yesterday that marks Home seen as
of when it loaded must not undo what a newer tab already marked, so the write
keeps the later of the stored moment and the new one.

The rest of the preferences are not this module's. Only the one entry is read,
and only the ``home_seen`` key is written, merged in SQL, so a mark never
rewrites -- or empties -- a default surface or anything else stored beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Text, case, exists, func, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationMember,
)
from app.modules.identity.infrastructure.models.user_models import User

_MOMENT = TypeAdapter(datetime)
_KEY = "home_seen"


@dataclass(frozen=True, slots=True)
class HomeSeen:
    #: None until the member first marks it.
    seen_at: datetime | None
    #: What it was before this write; equal to ``seen_at`` on a read.
    previous_seen_at: datetime | None


def _seen_in(preferences: object, organization_id: UUID) -> datetime | None:
    """This organization's entry in stored preferences, or None. Whatever else
    is stored there, readable or not, is left alone."""
    if not isinstance(preferences, dict):
        return None
    seen = preferences.get(_KEY)
    if not isinstance(seen, dict):
        return None
    try:
        moment = _MOMENT.validate_python(seen.get(str(organization_id)))
    except ValidationError:
        return None
    # Every mark is written aware; one that is not was not written here.
    return moment if moment.tzinfo is not None else None


async def _preferences_of_member(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID, lock: bool
) -> tuple[object] | None:
    """The member's stored preferences as a one-tuple (they may be NULL), or
    None when they are not a member."""
    stmt = select(User.preferences).where(
        User.id == user_id,
        exists().where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id,
        ),
    )
    if lock:
        stmt = stmt.with_for_update(of=User)
    row = (await session.execute(stmt)).one_or_none()
    return None if row is None else (row.preferences,)


async def read_home_seen(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID
) -> HomeSeen | None:
    """The member's mark. None when they are not a member."""
    found = await _preferences_of_member(
        session, user_id=user_id, organization_id=organization_id, lock=False
    )
    if found is None:
        return None
    seen_at = _seen_in(found[0], organization_id)
    return HomeSeen(seen_at=seen_at, previous_seen_at=seen_at)


async def mark_home_seen(
    session: AsyncSession, *, user_id: UUID, organization_id: UUID, at: datetime
) -> HomeSeen | None:
    """Move the member's mark to ``at``, unless it is already later.

    The user's row is locked for the read of what it was, so two tabs marking
    at once each see the other's write rather than both reporting the same
    previous moment. Stored preferences that are not an object are not this
    module's to replace: the mark is refused and reported as unset.
    ``updated_at`` is left alone: looking at a page is not a change to the
    account.
    """
    found = await _preferences_of_member(
        session, user_id=user_id, organization_id=organization_id, lock=True
    )
    if found is None:
        return None
    stored = found[0]
    if stored is not None and not isinstance(stored, dict):
        return HomeSeen(seen_at=None, previous_seen_at=None)
    previous = _seen_in(stored, organization_id)
    seen_at = at if previous is None else max(previous, at)
    entry = {str(organization_id): _MOMENT.dump_python(seen_at, mode="json")}

    # Only the one key is set. A missing or JSON-null blob starts empty, and
    # a `home_seen` that is not an object is replaced: neither holds anything
    # another reader could use.
    empty = literal({}, JSONB)
    seen = User.preferences[_KEY]
    base = case(
        (func.jsonb_typeof(User.preferences) == "object", User.preferences),
        else_=empty,
    )
    kept = case((func.jsonb_typeof(seen) == "object", seen), else_=empty)
    merged = func.jsonb_set(
        base,
        literal([_KEY], ARRAY(Text)),
        kept.op("||", return_type=JSONB)(literal(entry, JSONB)),
    )
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(preferences=merged, updated_at=User.updated_at)
    )
    return HomeSeen(seen_at=seen_at, previous_seen_at=previous)
