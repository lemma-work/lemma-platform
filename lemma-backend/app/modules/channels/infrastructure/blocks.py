"""Reading and writing who a member has blocked (``block_models``)."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, delete, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import Principal
from app.modules.channels.infrastructure.block_models import PersonBlockModel

#: The most blocks one member keeps in one pod: what one read returns, and
#: what a page shows.
MAX_BLOCKS = 500
#: The most members one read finds who blocked one person: whom a push
#: leaves out. Past it, a member who blocked them may still be told of a
#: line -- told, never shown: what the line says stays withheld from them.
MAX_BLOCKERS = 2000


def _blocked(row: PersonBlockModel) -> Principal:
    if row.blocked_user_id is not None:
        return Principal.member(row.blocked_user_id)
    assert row.blocked_contact_id is not None
    return Principal.contact(row.blocked_contact_id)


def _is(person: Principal) -> ColumnElement[bool]:
    b = PersonBlockModel
    if person.user_id is not None:
        return b.blocked_user_id == person.user_id
    return b.blocked_contact_id == person.contact_id


class BlockRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def of_reader(self, *, pod_id: UUID, user_id: UUID) -> frozenset[Principal]:
        """Everybody this member blocked here."""
        b = PersonBlockModel
        rows = await self._session.scalars(
            select(b).where(b.pod_id == pod_id, b.user_id == user_id).limit(MAX_BLOCKS)
        )
        return frozenset(_blocked(row) for row in rows)

    async def listed(
        self, *, pod_id: UUID, user_id: UUID
    ) -> list[tuple[Principal, datetime]]:
        """This member's blocks here, newest first, with when each was made."""
        b = PersonBlockModel
        rows = await self._session.scalars(
            select(b)
            .where(b.pod_id == pod_id, b.user_id == user_id)
            .order_by(b.created_at.desc(), b.id.desc())
            .limit(MAX_BLOCKS)
        )
        return [(_blocked(row), row.created_at) for row in rows]

    async def blockers_of(self, *, pod_id: UUID, person: Principal) -> set[UUID]:
        """The members who blocked this person here, up to ``MAX_BLOCKERS``."""
        b = PersonBlockModel
        rows = await self._session.scalars(
            select(b.user_id).where(b.pod_id == pod_id, _is(person)).limit(MAX_BLOCKERS)
        )
        return set(rows)

    async def among(
        self, *, pod_id: UUID, people: Collection[Principal]
    ) -> dict[UUID, frozenset[Principal]]:
        """For each member who blocked any of ``people`` here: which of them.
        What a batch of lines needs to be shown to each of its readers."""
        if not people:
            return {}
        b = PersonBlockModel
        members = {person.user_id for person in people if person.user_id is not None}
        contacts = {
            person.contact_id for person in people if person.contact_id is not None
        }
        rows = await self._session.scalars(
            select(b).where(
                b.pod_id == pod_id,
                or_(
                    b.blocked_user_id.in_(members),
                    b.blocked_contact_id.in_(contacts),
                ),
            )
        )
        found: dict[UUID, set[Principal]] = {}
        for row in rows:
            found.setdefault(row.user_id, set()).add(_blocked(row))
        return {user_id: frozenset(blocked) for user_id, blocked in found.items()}

    async def block(
        self, *, pod_id: UUID, user_id: UUID, person: Principal
    ) -> datetime:
        """Block them; blocking them again changes nothing. When it was made."""
        b = PersonBlockModel
        await self._session.execute(
            insert(b)
            .values(
                pod_id=pod_id,
                user_id=user_id,
                blocked_user_id=person.user_id,
                blocked_contact_id=person.contact_id,
            )
            .on_conflict_do_nothing()
        )
        made = await self._session.scalar(
            select(b.created_at).where(
                b.pod_id == pod_id, b.user_id == user_id, _is(person)
            )
        )
        assert made is not None
        return made

    async def carry(self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID) -> None:
        """A contact turned out to be another: whoever blocked the one blocks
        the other."""
        b = PersonBlockModel
        # In one statement, so it holds however many blocked them: each
        # blocker's row for the survivor, unless they had one already.
        await self._session.execute(
            insert(b)
            .from_select(
                ["id", "created_at", "pod_id", "user_id", "blocked_contact_id"],
                select(
                    func.gen_random_uuid(),
                    b.created_at,
                    b.pod_id,
                    b.user_id,
                    literal(survivor_id, type_=b.blocked_contact_id.type),
                ).where(b.pod_id == pod_id, b.blocked_contact_id == merged_id),
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(
            delete(b).where(b.pod_id == pod_id, b.blocked_contact_id == merged_id)
        )

    async def unblock(self, *, pod_id: UUID, user_id: UUID, person: Principal) -> bool:
        """Lift the block; whether there was one."""
        b = PersonBlockModel
        result = await self._session.execute(
            delete(b).where(b.pod_id == pod_id, b.user_id == user_id, _is(person))
        )
        return bool(getattr(result, "rowcount", 0) or 0)
