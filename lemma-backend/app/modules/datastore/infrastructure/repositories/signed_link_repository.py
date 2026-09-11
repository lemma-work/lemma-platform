"""Durable records for public short file links (``/s/{code}``)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update

from app.modules.datastore.domain.file_entities import DatastoreSignedLinkEntity
from app.modules.datastore.infrastructure.models.datastore_models import (
    DatastoreSignedLink,
)
from app.modules.datastore.infrastructure.repositories._base import (
    DatastoreRepositoryBase,
)


class SignedLinkRepository(DatastoreRepositoryBase):
    async def create(self, entity: DatastoreSignedLinkEntity) -> None:
        self.session.add(
            DatastoreSignedLink(
                id=entity.id,
                code=entity.code,
                pod_id=entity.pod_id,
                created_by_user_id=entity.created_by_user_id,
                path=entity.path,
                object_key=entity.object_key,
                content_type=entity.content_type,
                filename=entity.filename,
                content_sha256=entity.content_sha256,
                size_bytes=entity.size_bytes,
                max_hits=entity.max_hits,
                expires_at=entity.expires_at,
            )
        )

    async def get_by_code(self, code: str) -> DatastoreSignedLinkEntity | None:
        """The link this code names, whether or not it is still live.

        Liveness is the caller's to judge (``entity.is_live``) so that an
        expired or revoked link can be told apart from one that never existed —
        the serving route answers both with 404, but the pod's own listing
        should not.
        """
        row = await self.session.scalar(
            select(DatastoreSignedLink).where(DatastoreSignedLink.code == code)
        )
        return row.to_entity() if row else None

    async def list_for_user(
        self,
        pod_id: UUID,
        user_id: UUID | None,
        *,
        include_dead: bool = False,
        limit: int = 100,
    ) -> list[DatastoreSignedLinkEntity]:
        """This person's links in this pod, newest first.

        Scoped to the caller, not the pod, because each row carries the ``code``
        — which is the entire capability. A pod-wide listing would therefore let
        any member open any other member's links, including the ones pointing at
        files only their owner can read: personal (``/me/...``) files are an
        authorization rule at the file layer, and a listing that leaked their
        codes would route straight around it.
        """
        stmt = select(DatastoreSignedLink).where(
            DatastoreSignedLink.pod_id == pod_id,
            DatastoreSignedLink.created_by_user_id == user_id,
        )
        if not include_dead:
            stmt = stmt.where(
                DatastoreSignedLink.revoked_at.is_(None),
                DatastoreSignedLink.exhausted_at.is_(None),
                DatastoreSignedLink.expires_at > datetime.now(timezone.utc),
            )
        stmt = stmt.order_by(DatastoreSignedLink.created_at.desc()).limit(limit)
        rows = await self.session.scalars(stmt)
        return [row.to_entity() for row in rows]

    async def count_live_for_user(self, pod_id: UUID, user_id: UUID | None) -> int:
        """How many of this person's links in this pod still resolve.

        Scoped per person rather than per pod so that one member's runaway agent
        cannot spend a shared pod's whole allowance, and so two people working in
        the same busy pod do not compete for it.

        Counts live links only, using the same three conditions as ``is_live``,
        which is what makes the limit self-clearing: revoke one, or let one
        expire, and the slot is back.
        """
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(DatastoreSignedLink)
                .where(
                    DatastoreSignedLink.pod_id == pod_id,
                    DatastoreSignedLink.created_by_user_id == user_id,
                    DatastoreSignedLink.revoked_at.is_(None),
                    DatastoreSignedLink.exhausted_at.is_(None),
                    DatastoreSignedLink.expires_at > datetime.now(timezone.utc),
                )
            )
            or 0
        )

    async def revoke(self, pod_id: UUID, code: str) -> bool:
        """Mark a link dead. Returns whether this call is what killed it.

        Scoped by ``pod_id`` in the statement rather than checked beforehand, so
        a caller authorized for one pod cannot revoke another pod's link by
        guessing a code. Already-revoked links return False rather than
        refreshing the timestamp, which keeps the record of when it happened.
        """
        result = await self.session.execute(
            update(DatastoreSignedLink)
            .where(
                DatastoreSignedLink.code == code,
                DatastoreSignedLink.pod_id == pod_id,
                # All three, not just `revoked_at`. The endpoint documents
                # `revoked: false` for a link that was already dead, and an
                # expired or exhausted one is dead — reporting True for those
                # would tell the caller they had just stopped something that had
                # stopped on its own.
                DatastoreSignedLink.revoked_at.is_(None),
                DatastoreSignedLink.exhausted_at.is_(None),
                DatastoreSignedLink.expires_at > datetime.now(timezone.utc),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        return bool(result.rowcount)

    async def mark_exhausted(self, code: str) -> None:
        """Record that a link spent its budget, so it stays spent.

        Written once per link: the serving path drops the Redis key at the same
        moment, and every later fetch rehydrates, sees this and stops without
        touching the database again.
        """
        await self.session.execute(
            update(DatastoreSignedLink)
            .where(
                DatastoreSignedLink.code == code,
                DatastoreSignedLink.exhausted_at.is_(None),
            )
            .values(exhausted_at=datetime.now(timezone.utc))
        )

    async def delete_expired(self, *, before: datetime, limit: int = 1000) -> int:
        """Drop rows whose links expired before ``before``. Returns how many.

        Bounded per call so the sweep cannot take a long lock on a table the
        public serving path reads.
        """
        codes = (
            await self.session.scalars(
                select(DatastoreSignedLink.code)
                .where(DatastoreSignedLink.expires_at < before)
                .limit(limit)
            )
        ).all()
        if not codes:
            return 0
        await self.session.execute(
            delete(DatastoreSignedLink).where(DatastoreSignedLink.code.in_(codes))
        )
        return len(codes)
