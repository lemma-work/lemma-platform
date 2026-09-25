"""Which of a user's paired hosts can run their commands right now.

A read, not a registry: the answer is the host rows as the link last wrote
them. "Online" is the same judgement the rest of Agent Host makes -- a status
the link set and a heartbeat inside the 90-second offline threshold -- so a
host the app shows as connected is the host this finds. Whether its socket is
really there is settled by the op itself: nothing picking the request up within
``OP_PICKUP_TIMEOUT_SECONDS`` is "This Mac is not connected".

**Which host, when a user has more than one.** A host is paired to one user,
but a user may pair several Macs. Two questions are asked, and answered from
rows that already exist -- the host rows, and the ``execution`` record each
run keeps in its metadata (``run_execution_record``):

* *Where a new run executes* (``host_execution_host_id``): among the user's
  usable hosts, the one the conversation's most recent host run chose, so a
  conversation keeps its folder; otherwise the most recently seen.
* *Where a host sandbox's operations go* (``host_for_host_sandbox``): the host
  the conversation's most recent host run chose, **whatever its state now** --
  a run never moves, so a Mac that went away answers "This Mac is not
  connected" rather than its operations landing on another Mac. Only a
  conversation no host run has chosen for yet (nothing has run there) falls to
  the user's usable host, and with none of those it has none.
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
from app.modules.agent.domain.agent_host_link import HostExecutionCapability
from app.modules.agent.infrastructure.agent_host.repository import (
    HOST_EXECUTION_CAPACITY_KEY,
)
from app.modules.agent.infrastructure.agent_host.repository_common import utcnow
from app.modules.agent.infrastructure.run_execution_record import (
    latest_host_execution,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostModel

#: Matches the offline sweep: three missed heartbeats and a margin.
ONLINE_WITHIN_SECONDS = 90

_LIVE_STATUSES = (AgentHostStatus.ONLINE.value, AgentHostStatus.DRAINING.value)


def host_execution_of(host: AgentHostModel) -> HostExecutionCapability:
    """The stored report, read leniently: a bad or missing one means "off"."""
    try:
        return HostExecutionCapability.model_validate(
            (host.capacity or {}).get(HOST_EXECUTION_CAPACITY_KEY) or {}
        )
    except ValidationError:
        return HostExecutionCapability()


def _recorded_host(record: dict[str, object] | None) -> UUID | None:
    host_id = record.get("host_id") if record is not None else None
    try:
        return UUID(host_id) if isinstance(host_id, str) else None
    except ValueError:
        return None


async def host_execution_host(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    prefer: UUID | None = None,
    now: datetime | None = None,
) -> AgentHostModel | None:
    """The user's online host with host execution usable: ``prefer`` if it is
    one, otherwise the most recently seen."""
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
    usable = [host for host in rows.scalars() if host_execution_of(host).usable]
    preferred = [host for host in usable if host.id == prefer]
    return (preferred or usable or [None])[0]


async def host_execution_host_id(
    user_id: UUID, conversation_id: UUID | None = None
) -> UUID | None:
    """Where a new run of ``user_id`` in ``conversation_id`` would execute.

    See the module: the conversation's last host, if it is usable now,
    otherwise the most recently seen usable host.
    """
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        prefer = (
            _recorded_host(await latest_host_execution(uow, conversation_id))
            if conversation_id is not None
            else None
        )
        host = await host_execution_host(uow, user_id=user_id, prefer=prefer)
        return host.id if host is not None else None


async def host_for_host_sandbox(
    *, conversation_id: UUID, user_id: UUID
) -> tuple[UUID | None, str | None]:
    """The host a conversation's host sandbox is on, and the root it opened.

    ``(host, root)`` from the conversation's most recent host run, whether or
    not that host is online (see the module); ``(usable host, None)`` for a
    conversation no run has opened a host sandbox in yet; ``(None, None)``
    when there is neither.
    """
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        record = await latest_host_execution(uow, conversation_id)
        recorded = _recorded_host(record)
        if recorded is not None and record is not None:
            root = record.get("root")
            return recorded, root if isinstance(root, str) else None
        host = await host_execution_host(uow, user_id=user_id)
        return (host.id if host is not None else None), None


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
