"""Durable records for public short file links (``/s/{code}``)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, insert, literal, select, tuple_, update

from app.core.authorization.context import Context, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.datastore.domain.file_entities import DatastoreSignedLinkEntity
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_visibility_sql import (
    has_unreadable_ancestor,
)
from app.modules.datastore.infrastructure.models.datastore_models import (
    DatastoreSignedLink,
)
from app.modules.datastore.infrastructure.repositories._base import (
    DatastoreRepositoryBase,
)


class SignedLinkRepository(DatastoreRepositoryBase):
    async def create_within_allowance(
        self, entity: DatastoreSignedLinkEntity, *, max_active: int
    ) -> bool:
        """Insert the link unless this person is already at their limit.

        Returns whether it was inserted. One statement, so the count and the
        insert cannot be separated: `INSERT ... SELECT ... WHERE (count) < n`.

        Counting first and inserting second — even in the same transaction —
        lets every concurrent mint read the same below-limit total and proceed,
        so the ceiling could be passed by as many links as there were requests
        in flight. Serializing them instead, on a per-user
        `pg_advisory_xact_lock`, fixes that and introduces something worse: each
        waiter holds its pooled connection for the whole wait, and a burst of
        ten concurrent mints exhausted the pool (`QueuePool limit of size 10
        reached`) and 500ed requests that had nothing to do with signed links.

        This keeps the check and the write in one statement without anything
        blocking. Two statements executing at literally the same instant can
        still both see room under READ COMMITTED, so the bound is not a hard
        serialization — but the window is one statement rather than two
        transactions and a round trip, and nothing queues.
        """
        source = select(
            literal(entity.id).label("id"),
            literal(entity.code).label("code"),
            literal(entity.pod_id).label("pod_id"),
            literal(entity.created_by_user_id).label("created_by_user_id"),
            literal(entity.path).label("path"),
            literal(entity.object_key).label("object_key"),
            literal(entity.content_type).label("content_type"),
            literal(entity.filename).label("filename"),
            literal(entity.content_sha256).label("content_sha256"),
            literal(entity.size_bytes).label("size_bytes"),
            literal(entity.max_hits).label("max_hits"),
            literal(entity.expires_at).label("expires_at"),
            literal(datetime.now(timezone.utc)).label("created_at"),
            literal(datetime.now(timezone.utc)).label("updated_at"),
        ).where(
            self._live_for_user_count(entity.pod_id, entity.created_by_user_id)
            < max_active
        )
        result = await self.session.execute(
            insert(DatastoreSignedLink).from_select(
                [
                    "id",
                    "code",
                    "pod_id",
                    "created_by_user_id",
                    "path",
                    "object_key",
                    "content_type",
                    "filename",
                    "content_sha256",
                    "size_bytes",
                    "max_hits",
                    "expires_at",
                    "created_at",
                    "updated_at",
                ],
                source,
            )
        )
        return bool(result.rowcount)

    @staticmethod
    def _live_for_user_count(pod_id: UUID, user_id: UUID | None):
        """Scalar subquery: this person's live links in this pod."""
        return (
            select(func.count())
            .select_from(DatastoreSignedLink)
            .where(
                DatastoreSignedLink.pod_id == pod_id,
                DatastoreSignedLink.created_by_user_id == user_id,
                DatastoreSignedLink.revoked_at.is_(None),
                DatastoreSignedLink.exhausted_at.is_(None),
                DatastoreSignedLink.expires_at > datetime.now(timezone.utc),
            )
            .scalar_subquery()
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

    @staticmethod
    def _readable_file_exists(ctx: Context, pod_id: UUID):
        """EXISTS: the file this link points at, readable by *this* caller.

        Two different questions hide behind "whose link is this". Who minted it
        is `created_by_user_id`; who may open what it points at is the file's
        own authorization, and for a delegated agent those are not the same
        person. `ctx.user_id` on a delegated context is the *invoking* person,
        while the agent's own grants may be narrower — so filtering on the user
        id alone let an agent list a share its principal had minted for a file
        the agent itself may not read, and the `code` in that row is the whole
        capability. The intersection is the rule (PS-ACCESS-020: a workload gets
        the person's access ∩ its own grants), and `allowed_actions_expr` is
        what already computes it everywhere else files are listed.
        """
        # `DatastoreFile` itself, not an alias: `has_unreadable_ancestor`
        # correlates against the un-aliased table, so aliasing here silently
        # decoupled the ancestor walk from the row being checked and the whole
        # EXISTS came back empty. There is no ambiguity to avoid — the outer
        # query selects from `DatastoreSignedLink`.
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.DOCUMENT,
            resource_id_col=DatastoreFile.id,
            pod_id_col=DatastoreFile.pod_id,
            owner_user_id_col=DatastoreFile.owner_user_id,
            visibility_col=DatastoreFile.visibility,
            resource_path_col=DatastoreFile.path,
        )
        return (
            select(literal(1))
            .select_from(DatastoreFile)
            .where(
                DatastoreFile.pod_id == DatastoreSignedLink.pod_id,
                DatastoreFile.path == DatastoreSignedLink.path,
                allowed_actions_contains(actions, Permissions.FOLDER_READ),
                ~has_unreadable_ancestor(ctx, pod_id),
            )
            .exists()
        )

    async def list_visible(
        self,
        pod_id: UUID,
        ctx: Context,
        *,
        include_dead: bool = False,
        limit: int = 100,
        before: datetime | None = None,
        before_id: UUID | None = None,
    ) -> list[DatastoreSignedLinkEntity]:
        """Links this caller minted *and* may still read, newest first.

        Both halves are needed. Minted-by keeps one member out of another's
        shares; readable-by keeps a delegated agent out of the files its
        principal can reach and it cannot.

        Paginated by `(created_at, id)` rather than an offset: `created_at`
        alone is not unique — a burst of mints shares a timestamp — so an offset
        or a bare timestamp cursor can skip or repeat rows across pages. The id
        is the tie-breaker, and `uuid7` makes it agree with creation order.
        """
        stmt = select(DatastoreSignedLink).where(
            DatastoreSignedLink.pod_id == pod_id,
            DatastoreSignedLink.created_by_user_id == ctx.user_id,
            self._readable_file_exists(ctx, pod_id),
        )
        if not include_dead:
            stmt = stmt.where(
                DatastoreSignedLink.revoked_at.is_(None),
                DatastoreSignedLink.exhausted_at.is_(None),
                DatastoreSignedLink.expires_at > datetime.now(timezone.utc),
            )
        if before is not None:
            stmt = stmt.where(
                tuple_(DatastoreSignedLink.created_at, DatastoreSignedLink.id)
                < tuple_(before, before_id or UUID(int=0))
            )
        stmt = stmt.order_by(
            DatastoreSignedLink.created_at.desc(), DatastoreSignedLink.id.desc()
        ).limit(limit)
        rows = await self.session.scalars(stmt)
        return [row.to_entity() for row in rows]

    async def is_visible(self, pod_id: UUID, code: str, ctx: Context) -> bool:
        """Whether this caller may act on this link at all.

        Revocation used to take only `pod_id`, which let a delegated agent kill
        its principal's link to a file the agent may not read — a capability
        taken away by something that could not have been given it.
        """
        return bool(
            await self.session.scalar(
                select(literal(1))
                .select_from(DatastoreSignedLink)
                .where(
                    DatastoreSignedLink.code == code,
                    DatastoreSignedLink.pod_id == pod_id,
                    DatastoreSignedLink.created_by_user_id == ctx.user_id,
                    self._readable_file_exists(ctx, pod_id),
                )
                .limit(1)
            )
        )

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
                select(self._live_for_user_count(pod_id, user_id))
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

    async def owns_revoked(self, pod_id: UUID, code: str) -> bool:
        """Whether this pod holds an already-revoked row for this code.

        Lets a retry finish a revocation whose cache invalidation failed: the
        `UPDATE` reports nothing changed the second time, but the invalidation
        still has to happen. Pod-scoped like the update itself, so it grants a
        caller nothing it did not already have.
        """
        return bool(
            await self.session.scalar(
                select(literal(1))
                .select_from(DatastoreSignedLink)
                .where(
                    DatastoreSignedLink.code == code,
                    DatastoreSignedLink.pod_id == pod_id,
                    DatastoreSignedLink.revoked_at.is_not(None),
                )
                .limit(1)
            )
        )

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
