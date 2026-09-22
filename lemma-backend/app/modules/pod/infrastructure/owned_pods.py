"""How many pods a person owns, counted so that two creations cannot both fit.

Apart from ``pod_repositories`` because that file is at the size ratchet, and
because this is one question with one lock rather than a repository concern.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from uuid import UUID

from sqlalchemy import func, select, text

from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod.infrastructure.models.pod_models import Pod


class OwnedPods:
    """Satisfies ``OwnedPodsPort``."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._session = uow.session

    async def lock_and_count(
        self, *, user_id: UUID, excluding_organization_ids: Collection[UUID]
    ) -> int:
        """Hold this person's pod count until the transaction ends, and read it.

        A transaction-scoped advisory lock keyed on the person, so two requests
        creating a pod at once are serialised: the second counts the first's
        pod. Without it both read the same number, both pass, and a person on a
        two-pod plan ends up with three.
        """
        digest = hashlib.blake2b(
            str(user_id).encode(), digest_size=8, person=b"lemma-pod-quota"
        ).digest()
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": int.from_bytes(digest, "big", signed=True)},
        )
        mark_transaction_scoped_lock(self._session)

        query = (
            select(func.count())
            .select_from(Pod)
            .where(Pod.user_id == user_id, Pod.is_deleted.is_(False))
        )
        if excluding_organization_ids:
            query = query.where(Pod.organization_id.not_in(excluding_organization_ids))
        return int((await self._session.execute(query)).scalar_one())
