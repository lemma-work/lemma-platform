"""Reads that answer "what is here", separated from the row lifecycle.

Four questions with one thing in common: each is a listing, and each has a
bound it must not exceed. Kept together beside the statements they run (see
``file_listing_sql``) because the distinction between them -- a folder, a
subtree, a named set, the whole pod -- is the distinction that matters, and it
was invisible when they sat among the file repository's thirty other methods.

A mixin rather than a separate repository so callers keep one object, the same
seam ``file_recovery_queries`` already draws.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_listing_sql import (
    descendants_of,
    direct_children_of,
)


class DatastoreFileListingMixin:
    """Listing reads for the host repository's session."""

    session: AsyncSession

    async def get_direct_children(
        self,
        pod_id: UUID,
        directory_path: str,
    ) -> Sequence[DatastoreFileEntity]:
        """One directory's own entries -- `PS-DATA-031`, as a statement."""
        result = await self.session.execute(direct_children_of(pod_id, directory_path))
        return [instance.to_entity() for instance in result.scalars().all()]

    async def get_descendants(
        self,
        pod_id: UUID,
        path_prefix: str,
    ) -> Sequence[DatastoreFileEntity]:
        result = await self.session.execute(descendants_of(pod_id, path_prefix))
        return [instance.to_entity() for instance in result.scalars().all()]

    async def get_by_paths(
        self,
        pod_id: UUID,
        paths: Sequence[str],
    ) -> Sequence[DatastoreFileEntity]:
        if not paths:
            return []
        result = await self.session.execute(
            select(DatastoreFile)
            .where(
                DatastoreFile.pod_id == pod_id,
                DatastoreFile.path.in_(list(paths)),
            )
            .order_by(DatastoreFile.path)
        )
        return [instance.to_entity() for instance in result.scalars().all()]

    async def get_all_by_datastore(
        self,
        pod_id: UUID,
        owner_user_id: UUID | None = None,
    ) -> Sequence[DatastoreFileEntity]:
        """Every file row in a pod. Deliberately not on the port.

        Nothing in production calls this, and nothing should: it is O(files) for
        any question, and the last caller — the directory tree — was using it to
        render a handful of files per folder. `get_tree_items` and
        `get_descendants` are the bounded ways to ask.

        It stays here because the tests that check the visibility predicate have
        to enumerate a pod to compare against, which is a fair thing to do to a
        fixture and not a thing to do to a pod.
        """
        stmt = select(DatastoreFile).where(DatastoreFile.pod_id == pod_id)
        if owner_user_id is not None:
            stmt = stmt.where(DatastoreFile.owner_user_id == owner_user_id)
        result = await self.session.execute(stmt.order_by(DatastoreFile.path))
        return [instance.to_entity() for instance in result.scalars().all()]
