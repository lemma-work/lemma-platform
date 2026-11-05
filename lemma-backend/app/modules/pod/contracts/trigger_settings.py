"""A pod's defaults for its DATASTORE triggers (``config.triggers``).

A trigger's change-log subscriber records what the defaults made it want (which
writes it reads rows for, whether it batches), so a pod whose defaults change
says so with :class:`PodTriggerDefaultsChanged` and the triggers resync.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work import Fact, pod_namespace


class PodTriggerDefaultsChanged(Fact):
    """A pod's ``config.triggers`` changed."""

    pod_id: UUID

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


async def pod_bulk_writes_default(session: AsyncSession, pod_id: UUID) -> str | None:
    """``triggers.bulk_writes_default`` -- ``skip`` | ``each`` | ``batch`` -- or ``None``."""
    from app.modules.pod.infrastructure.models import Pod

    value = await session.scalar(
        select(Pod.config["triggers"]["bulk_writes_default"].astext).where(
            Pod.id == pod_id
        )
    )
    return value if value in ("skip", "each", "batch") else None


__all__ = ["PodTriggerDefaultsChanged", "pod_bulk_writes_default"]
