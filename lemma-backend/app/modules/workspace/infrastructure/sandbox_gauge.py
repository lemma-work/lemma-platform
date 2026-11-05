"""``lemma.sandbox.sessions_active``: E2B sandboxes running right now.

Read from ``sandbox_instances`` rather than counted in-process: a sandbox is
created on one replica and released by another (or by the sweeper), so an
up-down counter per process would drift. ``ix_sandbox_instances_live`` is on
``(state, provider)``, so the count is an index-only read.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work.gauges import register_gauge_sampler
from app.core.observability import product_metrics
from app.modules.workspace.domain.sandbox import SandboxInstanceState
from app.modules.workspace.infrastructure.models import SandboxInstanceModel
from app.modules.workspace.infrastructure.sandbox_repository import E2B_PROVIDER

RUNNING_E2B = (
    select(func.count())
    .select_from(SandboxInstanceModel)
    .where(
        SandboxInstanceModel.state == SandboxInstanceState.READY.value,
        SandboxInstanceModel.provider == E2B_PROVIDER,
    )
)


async def sample_active_sandboxes(session: AsyncSession) -> None:
    product_metrics.snapshot.sandboxes_active = int(
        await session.scalar(RUNNING_E2B) or 0
    )


def _forget() -> None:
    product_metrics.snapshot.sandboxes_active = None


register_gauge_sampler("sandboxes_active", sample_active_sandboxes, forget=_forget)
