"""Reading and writing who a member has blocked (``block_models``)."""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, delete, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.modules.channels.domain.entities import Principal
from app.modules.channels.infrastructure.block_models import PersonBlockModel
from app.modules.contacts.contracts.same_person import same_person

#: The most blocks one member keeps in one pod: what one read returns, so
#: what ``block`` admits -- a block past it would be kept and never applied.
MAX_BLOCKS = 500
#: How many readers one read asks about at once.
_BATCH = 500


class BlockLimitReached(Exception):
    """The member already blocked ``MAX_BLOCKS`` people in this pod."""


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


def _lock_key(pod_id: UUID, user_id: UUID) -> int:
    digest = hashlib.blake2b(
        f"{pod_id}:{user_id}".encode(), digest_size=8, person=b"lemma-blocks"
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


class BlockRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def of_reader(self, *, pod_id: UUID, user_id: UUID) -> frozenset[Principal]:
        """Everybody this member blocked here: never more than ``MAX_BLOCKS``,
        which ``block`` holds to."""
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

    async def blockers_among(
        self, *, pod_id: UUID, people: Collection[Principal], user_ids: Collection[UUID]
    ) -> set[UUID]:
        """Which of these members blocked any of ``people`` here -- one person
        under every id they have had -- every member asked, a batch at a
        time, however many there are."""
        if not people:
            return set()
        b = PersonBlockModel
        readers = sorted(set(user_ids))
        anyone = or_(*(_is(person) for person in people))
        found: set[UUID] = set()
        for start in range(0, len(readers), _BATCH):
            rows = await self._session.scalars(
                select(b.user_id).where(
                    b.pod_id == pod_id,
                    anyone,
                    b.user_id.in_(readers[start : start + _BATCH]),
                )
            )
            found.update(rows)
        return found

    async def blockers_of_contact(
        self, *, pod_id: UUID, contact_id: UUID, user_ids: Collection[UUID]
    ) -> set[UUID]:
        """Which of these members blocked this contact under any id they have
        had. Merges, and the work carrying blocks to each survivor, commit
        apart, so a block may sit under any of them; each read names those
        ids in its own statement (``same_person``), so it sees the merges and
        the blocks at one moment. Every member asked, a batch at a time."""
        b = PersonBlockModel
        readers = sorted(set(user_ids))
        sam = same_person(b.blocked_contact_id, pod_id=pod_id, contact_id=contact_id)
        found: set[UUID] = set()
        for start in range(0, len(readers), _BATCH):
            rows = await self._session.scalars(
                select(b.user_id)
                .distinct()
                .where(
                    b.pod_id == pod_id,
                    sam,
                    b.user_id.in_(readers[start : start + _BATCH]),
                )
            )
            found.update(rows)
        return found

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
        """Block them; blocking them again changes nothing. When it was made.

        A member's blocks here are counted and added under one lock for that
        member and pod, so two at once cannot both take the last place:
        ``BlockLimitReached`` once ``MAX_BLOCKS`` are kept.
        """
        b = PersonBlockModel
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _lock_key(pod_id, user_id)},
        )
        mark_transaction_scoped_lock(self._session)
        mine = (b.pod_id == pod_id, b.user_id == user_id)
        made = await self._session.scalar(
            select(b.created_at).where(*mine, _is(person))
        )
        if made is not None:
            return made
        kept = await self._session.scalar(
            select(func.count()).select_from(b).where(*mine)
        )
        if (kept or 0) >= MAX_BLOCKS:
            raise BlockLimitReached
        made = await self._session.scalar(
            insert(b)
            .values(
                pod_id=pod_id,
                user_id=user_id,
                blocked_user_id=person.user_id,
                blocked_contact_id=person.contact_id,
            )
            .returning(b.created_at)
        )
        assert made is not None
        return made

    async def carry(self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID) -> None:
        """A contact turned out to be another: whoever blocked the one blocks
        the other. Nobody's count grows: each row moves, or goes."""
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
