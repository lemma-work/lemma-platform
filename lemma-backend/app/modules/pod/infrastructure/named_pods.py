"""The pods of an organization whose names make one word of an address.

The word is made in SQL the way ``domain.pod_addresses.lemma_word`` makes it,
so the organization's pods are not read into memory to find one. Pod names
are ASCII letters, digits, spaces, hyphens and underscores: ``pod_names``
holds every create and rename to that (``PodService._normalize_name``). So the
Unicode folding the Python rule adds has nothing to fold here. Allow anything
wider and this has to fold too; ``test_pod_by_name_e2e`` fails first.
"""

from __future__ import annotations

from typing import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod.domain.pod_addresses import NamedPod
from app.modules.pod.domain.pod_entities import PodJoinPolicy
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember


def word_of_name() -> ColumnElement[str]:
    """The pod's name as one word of an address, as a column."""
    return func.btrim(
        func.regexp_replace(func.lower(Pod.name), "[^a-z0-9]+", "-", "g"), "-"
    )


def pods_called_sql(
    organization_id: UUID, word: str
) -> Select[tuple[UUID, str | None]]:
    """Two at most of the organization's live pods called ``word``: one says
    which, a second says the word names neither."""
    return (
        select(Pod.id, Pod.config["join_policy"].astext.label("join_policy"))
        .where(
            Pod.organization_id == organization_id,
            Pod.is_deleted.is_(False),
            word_of_name() == word,
        )
        .order_by(Pod.id)
        .limit(2)
    )


class NamedPods:
    """Satisfies ``NamedPodsPort``."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._session = uow.session

    async def called(self, organization_id: UUID, word: str) -> Sequence[NamedPod]:
        rows = (
            await self._session.execute(pods_called_sql(organization_id, word))
        ).all()
        return [
            NamedPod(
                id=row.id,
                # A config with no policy is `PodConfig`'s default.
                join_policy=PodJoinPolicy(row.join_policy or PodJoinPolicy.INVITE_ONLY),
            )
            for row in rows
        ]

    async def has_member(self, pod_id: UUID, organization_member_id: UUID) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    select(PodMember.id)
                    .where(
                        PodMember.pod_id == pod_id,
                        PodMember.organization_member_id == organization_member_id,
                    )
                    .exists()
                )
            )
        )
