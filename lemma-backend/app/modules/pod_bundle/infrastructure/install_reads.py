"""The imports one plugin install queued, read back together.

An install has no row of its own: each of its bundles is an ordinary import
job (``pod_bundle_jobs``) whose state names the install and its place in it
(``ImportState.install``). Reading the install is reading those rows -- one
pod's, by JSON containment on the snapshot -- in the order the request named
them. A separate module because the state store is at the file-size ceiling,
and this is the one read across several jobs.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.modules.pod_bundle.domain.plugin_install import MAX_BUNDLES_PER_INSTALL
from app.modules.pod_bundle.domain.state import BundleJobKind, ImportState
from app.modules.pod_bundle.infrastructure.models import PodBundleJob


async def imports_of_install(
    *, pod_id: UUID, install_id: UUID, session: AsyncSession | None = None
) -> list[ImportState]:
    """The install's imports, first named first; empty when there is none.

    ``session`` reads inside a caller's transaction -- a dead item's hook,
    which has just ended one of them there -- instead of on a fresh one.
    """
    query = (
        select(PodBundleJob)
        .where(
            PodBundleJob.pod_id == pod_id,
            PodBundleJob.job_kind == BundleJobKind.IMPORT.value,
            PodBundleJob.snapshot.contains(
                {"install": {"install_id": str(install_id)}}
            ),
        )
        .limit(MAX_BUNDLES_PER_INSTALL)
    )
    if session is not None:
        rows = (await session.scalars(query)).all()
    else:
        async with async_session_maker() as own:
            rows = (await own.scalars(query)).all()
    states = [_state_of(row) for row in rows]
    return sorted(
        states, key=lambda state: state.install.position if state.install else 0
    )


def _state_of(row: PodBundleJob) -> ImportState:
    """As the store reads one: the row's own version, attempt and heartbeat."""
    state = ImportState.model_validate(row.snapshot)
    state.version = row.version
    state.attempt = row.attempt
    state.heartbeat_at = row.heartbeat_at
    return state


__all__ = ["imports_of_install"]
