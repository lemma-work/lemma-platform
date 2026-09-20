"""Two facts about a pod that a run needs before it can start.

Replaces `app/composition/agent_pod.py`, which published `PodRepository` so
`agent` could construct it. Agent called exactly two of its methods, both lean
single-column reads, from three places. A repository was the wrong unit: it
carries thirty other methods, and publishing it made every one of them part of
what `agent` could reach.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
model layer, and everything importing any pod contract would otherwise pay for
it.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select

from app.modules.pod.infrastructure.models.pod_models import Pod


async def pod_organization_id(uow, pod_id: UUID) -> UUID | None:
    """The organization holding this pod, for callers that need only the scope."""
    return (
        await uow.session.execute(select(Pod.organization_id).where(Pod.id == pod_id))
    ).scalar_one_or_none()


async def pod_organization_ids(
    uow, pod_ids: Collection[UUID]
) -> dict[UUID, UUID | None]:
    """The organization holding each of these pods, in one statement.

    The singular form in a loop is what this replaces: resolving a company
    installation asked it once per surface in order to check that they all
    belong to one organization -- N statements to compute a set the caller then
    asserts has size one.

    A pod with no row is absent from the result rather than mapped to None, so a
    caller that wants "every pod answered" compares lengths.
    """
    if not pod_ids:
        return {}
    rows = await uow.session.execute(
        select(Pod.id, Pod.organization_id).where(Pod.id.in_(list(pod_ids)))
    )
    return dict(rows.all())


async def live_pod_organization_id(uow, pod_id: UUID) -> UUID | None:
    return await uow.session.scalar(
        select(Pod.organization_id).where(Pod.id == pod_id, Pod.is_deleted.is_(False))
    )


async def pod_config(uow, pod_id: UUID) -> dict[str, object]:
    """The pod's config blob, empty when the pod is gone or never set one."""
    return (
        await uow.session.execute(select(Pod.config).where(Pod.id == pod_id))
    ).scalar_one_or_none() or {}


__all__ = ["pod_config", "pod_organization_id", "live_pod_organization_id"]
