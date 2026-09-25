"""Which of a user's paired hosts can run their commands right now.

A read, not a registry: the answer is the host rows as the link last wrote
them. "Online" is the same judgement the rest of Agent Host makes -- a status
the link set and a heartbeat inside the 90-second offline threshold -- so a
host the app shows as connected is the host this finds. Whether its socket is
really there is settled by the op itself: nothing picking the request up within
``OP_PICKUP_TIMEOUT_SECONDS`` is "This Mac is not connected".
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.agent_host import AgentHostStatus
from app.modules.agent.domain.agent_host_link import HostCapabilities
from app.modules.agent.infrastructure.agent_host.repository_common import utcnow
from app.modules.agent.infrastructure.runtime_models import AgentHostModel

#: Matches the offline sweep: three missed heartbeats and a margin.
ONLINE_WITHIN_SECONDS = 90

_LIVE_STATUSES = (AgentHostStatus.ONLINE.value, AgentHostStatus.DRAINING.value)


def host_capabilities(host: AgentHostModel) -> HostCapabilities:
    """The stored capabilities, read leniently: a bad row means "none"."""
    try:
        return HostCapabilities.model_validate(host.capabilities or {})
    except ValidationError:
        return HostCapabilities()


async def host_execution_host(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, now: datetime | None = None
) -> AgentHostModel | None:
    """The user's most recently seen online host with host execution usable."""
    timestamp = now or utcnow()
    rows = await uow.session.execute(
        select(AgentHostModel)
        .where(
            AgentHostModel.user_id == user_id,
            AgentHostModel.revoked_at.is_(None),
            AgentHostModel.status.in_(_LIVE_STATUSES),
            AgentHostModel.last_seen_at
            > timestamp - timedelta(seconds=ONLINE_WITHIN_SECONDS),
        )
        .order_by(AgentHostModel.last_seen_at.desc())
    )
    for host in rows.scalars():
        if host_capabilities(host).host_execution.usable:
            return host
    return None


async def host_execution_host_id(user_id: UUID) -> UUID | None:
    """``host_execution_host`` in a unit of work of its own; just the id."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        host = await host_execution_host(uow, user_id=user_id)
        return host.id if host is not None else None


async def is_paired_to_any_of(user_id: UUID, host_ids: Collection[UUID]) -> bool:
    """Whether ``user_id`` holds a live (unrevoked) pairing among ``host_ids``.

    ``host_ids`` are the hosts one machine's Agent Host holds pairings for, as
    its own ``config.json`` records them. A host id is minted by this backend
    at pairing and handed only to the host that paired, so no other machine can
    claim one. Whether that host is online, or has host execution switched on,
    is deliberately not asked: those change while a sandbox lives, and the
    loopback relay's other end checks the switch on every connection.
    """
    if not host_ids:
        return False
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        found = await uow.session.scalar(
            select(AgentHostModel.id)
            .where(
                AgentHostModel.id.in_(list(host_ids)),
                AgentHostModel.user_id == user_id,
                AgentHostModel.revoked_at.is_(None),
            )
            .limit(1)
        )
        return found is not None
