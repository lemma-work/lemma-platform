"""The runtime profiles bound to a set of harnesses, read for rewriting.

Its own query because a harness publish is the one writer that reaches profiles
by harness rather than by id, and it must not read more than that: the
``harness_id`` index keeps the read to the profiles of the harnesses this host
just reported, however many profiles the deployment holds.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.infrastructure.runtime_models import AgentRuntimeProfileModel


async def agent_host_profiles_for_update(
    session: AsyncSession, harness_ids: Collection[UUID]
) -> list[AgentRuntimeProfileModel]:
    """Every Agent Host profile on these harnesses, locked until commit.

    Locked so a person saving one of them at the same moment either lands
    first and is read here, or waits and then finds this write -- never a
    stale-version failure that would roll the whole publish back.
    """
    if not harness_ids:
        return []
    result = await session.execute(
        select(AgentRuntimeProfileModel)
        .where(
            AgentRuntimeProfileModel.harness_id.in_(list(harness_ids)),
            AgentRuntimeProfileModel.protocol
            == RuntimeProfileProtocol.AGENT_HOST.value,
        )
        .order_by(AgentRuntimeProfileModel.id)
        .with_for_update()
    )
    return list(result.scalars())
