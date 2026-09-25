"""The database half of the Agent Host link: one short transaction per frame.

A link lives for hours, and a transaction must never live across an await on
the socket. So nothing here is handed a unit of work; each method opens its own,
does its repository work, commits, and returns plain values. The session calls
these between frames and holds no database connection while it waits for the
next one -- which is also why this is a separate object: the session can be
tested against a stand-in without a database, and the rules a stand-in would
otherwise have to copy stay in the repositories they already lived in.

Everything the HTTP device routes did is here, one method each, with the same
repository calls. The link moved the transport and kept every rule.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import DBAPIError

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostCapacity,
    AgentHostCommand,
    AgentHostCommandRejection,
    AgentHostEventAck,
    AgentHostEventBatch,
    AgentHostHarnessSnapshot,
    AgentHostPairingCompleted,
    AgentHostRunCheckpoint,
    AgentHostStatus,
    HostHello,
)
from app.modules.agent.domain.agent_host_link import (
    AgentHostHarnessRecord,
    HostExecutionCapability,
    PairBody,
)
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host.repository import (
    AgentHostRepository,
)
from app.modules.agent.services.agent_host_auth import (
    generate_host_secret,
    host_secret_hash,
    pairing_code_hash,
)


logger = get_logger(__name__)

#: How many commands one read of the queue hands out, as the poll did.
MAX_COMMANDS_PER_READ = 16


@dataclass(frozen=True, slots=True)
class LinkedHost:
    """Who a ``hello`` authenticated as, and what state that left it in."""

    host_id: UUID
    user_id: UUID
    status: AgentHostStatus
    #: The link generation this ``hello`` claimed; 0 when it claimed none
    #: because the host must upgrade and the link is about to close.
    link_generation: int = 0


@dataclass(frozen=True, slots=True)
class ControlUpdates:
    """The parts of one ``control`` frame that parsed."""

    capacity: AgentHostCapacity
    acknowledged_command_ids: list[UUID]
    checkpoints: list[AgentHostRunCheckpoint]
    rejections: list[AgentHostCommandRejection]
    #: None when the frame did not carry it, which means "unchanged".
    host_execution: HostExecutionCapability | None = None


def _stored_host_execution(
    host_execution: HostExecutionCapability | None,
) -> dict[str, object]:
    """What the host row's ``capacity`` keeps under ``host_execution``."""
    return (host_execution or HostExecutionCapability()).model_dump(mode="json")


def is_deadlock(exc: DBAPIError) -> bool:
    """Whether Postgres aborted this transaction to break a deadlock (40P01).

    Read off the driver error rather than the message: asyncpg surfaces the
    SQLSTATE, and matching on text would break the moment a locale or a driver
    changes.
    """
    return getattr(getattr(exc, "orig", None), "sqlstate", None) == "40P01"


class AgentHostLinkStore:
    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork] | None = None,
    ) -> None:
        self._uow_factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)

    async def consume_pairing_code(self, body: PairBody) -> AgentHostPairingCompleted:
        """Consume a pairing code and issue the host secret, shown exactly once.

        Raises ``AgentHostRepositoryError`` for a code that is unknown, expired,
        or already used.
        """
        secret = generate_host_secret()
        async with self._uow_factory() as uow:
            host = await AgentHostRepository(uow).consume_pairing(
                code_hash=pairing_code_hash(body.pairing_code),
                host_secret_hash=host_secret_hash(secret),
                display_name=body.display_name,
                hello=body.hello,
            )
            await uow.commit()
        return AgentHostPairingCompleted(
            host_id=host.id, user_id=host.user_id, host_secret=secret
        )

    async def open_link(
        self,
        *,
        secret: str,
        hello: HostHello,
        capacity: AgentHostCapacity,
        host_execution: HostExecutionCapability | None = None,
    ) -> LinkedHost | None:
        """Authenticate a ``hello`` and record it as a heartbeat.

        ``None`` for an unknown or revoked secret, deliberately the same answer
        for both. A protocol mismatch is not an error here: ``mark_seen``
        records the host as UPGRADE_REQUIRED, which is what the person sees in
        the app, and the caller closes the socket on that status.
        """
        async with self._uow_factory() as uow:
            repository = AgentHostRepository(uow)
            host = await repository.get_by_secret_hash(host_secret_hash(secret))
            if host is None or host.revoked_at is not None:
                return None
            host = await repository.mark_seen(
                host_id=host.id,
                hello=hello,
                capacity=capacity.model_dump(mode="json"),
                # A hello always states it: a host too old to know the field
                # is a host without host execution, not "unchanged".
                host_execution=_stored_host_execution(host_execution),
            )
            status = AgentHostStatus(host.status)
            # Claimed in the same transaction that authenticated the hello, so
            # the order of generations is the order of accepted handshakes. A
            # host told to upgrade claims none: its link closes at once, and
            # claiming would close a live link it never replaces.
            generation = (
                0
                if status is AgentHostStatus.UPGRADE_REQUIRED
                else await repository.claim_link_generation(host.id)
            )
            await uow.commit()
        return LinkedHost(
            host_id=host.id,
            user_id=host.user_id,
            status=status,
            link_generation=generation,
        )

    async def link_generation(self, host_id: UUID) -> int | None:
        """The generation of the link that owns ``host_id`` now."""
        async with self._uow_factory() as uow:
            return await AgentHostRepository(uow).link_generation(host_id)

    async def apply_control(
        self,
        *,
        host_id: UUID,
        hello: HostHello,
        updates: ControlUpdates,
    ) -> list[AgentHostCommand]:
        """Heartbeat, apply the host's updates, and read the queue.

        Retried once on a deadlock. This pass and the five-minute dispatch cron
        both walk leases and commands, and the cron's two sweeps were split into
        separate transactions precisely so they cannot hold a lease lock across
        a command acquisition. That removes the cycle we know about; this
        catches one we do not. A deadlock aborts the whole transaction, so the
        retry re-runs the block rather than resuming inside it -- safe because
        everything in it is idempotent: ``mark_seen`` is a heartbeat write, and
        acknowledgements, checkpoints and rejections are all keyed and
        re-appliable. Anything that is not a deadlock propagates; swallowing a
        real database error here would hide it behind a silent retry.
        """
        for attempt in range(2):
            try:
                return await self._apply_control_once(
                    host_id=host_id, hello=hello, updates=updates
                )
            except DBAPIError as exc:
                if attempt or not is_deadlock(exc):
                    raise
                logger.warning(
                    "agent.agent_host_link.control_deadlock_retried.degraded",
                    host_id=str(host_id),
                    exc_info=True,
                )
        raise AssertionError("unreachable: the second attempt returns or raises")

    async def _apply_control_once(
        self,
        *,
        host_id: UUID,
        hello: HostHello,
        updates: ControlUpdates,
    ) -> list[AgentHostCommand]:
        async with self._uow_factory() as uow:
            await AgentHostRepository(uow).mark_seen(
                host_id=host_id,
                hello=hello,
                capacity=updates.capacity.model_dump(mode="json"),
                host_execution=(
                    _stored_host_execution(updates.host_execution)
                    if updates.host_execution is not None
                    else None
                ),
            )
            commands = await AgentHostDispatchRepository(uow).poll_commands(
                host_id=host_id,
                limit=MAX_COMMANDS_PER_READ,
                acknowledged_command_ids=updates.acknowledged_command_ids,
                checkpoints=updates.checkpoints,
                rejections=updates.rejections,
                available_run_slots=updates.capacity.available_runs,
            )
            await uow.commit()
        return list(commands)

    async def read_commands(
        self, *, host_id: UUID, available_run_slots: int
    ) -> list[AgentHostCommand]:
        """Read the queue with nothing to report: the push path."""
        async with self._uow_factory() as uow:
            commands = await AgentHostDispatchRepository(uow).poll_commands(
                host_id=host_id,
                limit=MAX_COMMANDS_PER_READ,
                acknowledged_command_ids=[],
                checkpoints=[],
                rejections=[],
                available_run_slots=available_run_slots,
            )
            await uow.commit()
        return list(commands)

    async def append_events(
        self, *, host_id: UUID, batch: AgentHostEventBatch
    ) -> AgentHostEventAck:
        """Append one ordered batch to the run's stream; see event_intake."""
        async with self._uow_factory() as uow:
            ack = await AgentHostDispatchRepository(uow).append_events(
                host_id=host_id, batch=batch
            )
            await uow.commit()
        return ack

    async def publish_harnesses(
        self, *, host_id: UUID, snapshots: list[AgentHostHarnessSnapshot]
    ) -> list[AgentHostHarnessRecord]:
        """Replace this host's harness snapshots with the reported set."""
        async with self._uow_factory() as uow:
            repository = AgentHostRepository(uow)
            published = [
                await repository.publish_harness(host_id=host_id, snapshot=snapshot)
                for snapshot in snapshots
            ]
            records = [
                AgentHostHarnessRecord.model_validate(harness) for harness in published
            ]
            await uow.commit()
        return records

    async def revoke_host(self, *, host_id: UUID, user_id: UUID) -> None:
        """Let a host retire its own credential, e.g. on uninstall."""
        async with self._uow_factory() as uow:
            await AgentHostRepository(uow).revoke(host_id=host_id, user_id=user_id)
            await uow.commit()
