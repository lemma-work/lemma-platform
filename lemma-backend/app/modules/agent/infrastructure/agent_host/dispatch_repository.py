"""Dispatch state for Agent Host: commands, run leases, and event intake.

Transport delivery is intentionally at-least-once. This repository enforces the
durable fencing and acceptance rules that make replay safe across API and host
restarts.

Two things live in PostgreSQL because they need guarantees Redis does not give
cheaply: the command queue depends on ``SELECT FOR UPDATE SKIP LOCKED`` so
concurrent API replicas never hand one command to two pollers, and the run
lease is keyed by ``run_id`` as its primary key, which is what makes
double-dispatch structurally impossible rather than merely guarded in code.

Run *events* are not stored here at all. They go to the run's Redis Stream, so
appending a batch performs no row write: the lease is read under a row lock
purely to serialize concurrent batches and validate the epoch, and the ack
watermark comes from the stream's last entry.

One consequence of at-least-once delivery shapes everything the host reports
up: a control update the backend rejects is a control update the host resends
forever, because it only clears its outbox once we accept it. Anything that
travels on the poll — acknowledgements, checkpoints, rejections — must
therefore be a no-op when it is stale rather than an error, and a single bad
one must not fail the poll that carries the rest. A poll that 409s delivers no
commands at all, so one un-appliable checkpoint would otherwise stop CANCEL_RUN
and RESOLVE_PERMISSION reaching that host for every run it is executing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import case, select

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostCommand,
    AgentHostCommandKind,
    AgentHostCommandRejection,
    AgentHostCommandState,
    AgentHostEventAck,
    AgentHostEventBatch,
    AgentHostRunCheckpoint,
    AgentHostRunSpec,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host.event_stream import (
    AgentHostEventStream,
    agent_host_event_stream,
)
from app.modules.agent.infrastructure.agent_host import admission
from app.modules.agent.infrastructure.agent_host import control_updates
from app.modules.agent.infrastructure.agent_host import event_intake
from app.modules.agent.infrastructure.agent_host import recovery
from app.modules.agent.infrastructure.agent_host.repository_common import (
    DEFAULT_COMMAND_TTL_SECONDS,
    DEFAULT_PERMISSION_COMMAND_TTL_SECONDS,
    DEFAULT_RUN_LEASE_SECONDS,
    AgentHostProtocolViolation,
    utcnow,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
)


logger = get_logger(__name__)


# A START_RUN the host has no slot for is skipped but still consumes a row of
# the poll's limit, so a queue with more starts than the limit could hide every
# CANCEL_RUN and RESOLVE_PERMISSION behind it — and a saturated host is exactly
# when cancelling matters. Control commands are always deliverable, so sorting
# them first means the limit can never bury one.
_CONTROL_COMMANDS_FIRST = case(
    (AgentHostCommandModel.kind == AgentHostCommandKind.START_RUN.value, 1),
    else_=0,
)


class PolledCommands(list[AgentHostCommand]):
    """The commands one poll produced, plus whether anything actually changed.

    ``progressed`` is false for a poll whose control updates were all no-ops.
    That is the common case for a busy host: a non-terminal checkpoint *is* the
    lease heartbeat, so the host resends it every poll, and re-applying an
    unchanged state is not news anyone needs to come back promptly for. The
    caller uses it to decide between a short backoff and an ordinary long poll.

    A list subclass rather than a wrapper so callers keep iterating commands
    directly, following ``StreamBatch`` in ``agent_host_event_stream``.
    """

    __slots__ = ("progressed",)

    def __init__(
        self,
        commands: list[AgentHostCommand],
        *,
        progressed: bool,
    ) -> None:
        super().__init__(commands)
        self.progressed = progressed


def _log_unappliable_update(
    *,
    kind: str,
    host_id: UUID,
    run_id: UUID | None,
    exc: Exception,
) -> None:
    """Record a control update we dropped so it is never silently discarded."""
    logger.warning(
        "agent.infrastructure.agent_host_dispatch_repository.control_update_dropped",
        host_id=str(host_id),
        agent_run_id=str(run_id) if run_id is not None else None,
        update_kind=kind,
        error_type=type(exc).__name__,
    )


class AgentHostDispatchRepository:
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        event_stream: AgentHostEventStream | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        self._events = event_stream or agent_host_event_stream()

    # ---------------------------------------------------------------- dispatch

    async def enqueue_run(
        self,
        *,
        host_id: UUID,
        harness_id: UUID,
        runtime_profile_id: UUID,
        run_spec: AgentHostRunSpec,
        encrypted_mcp_payload: dict,
        now: datetime | None = None,
        command_ttl_seconds: int = DEFAULT_COMMAND_TTL_SECONDS,
    ) -> AgentHostCommandModel:
        """Admit one run onto a host; see admission."""
        return await admission.enqueue_run(
            self.uow,
            host_id=host_id,
            harness_id=harness_id,
            runtime_profile_id=runtime_profile_id,
            run_spec=run_spec,
            encrypted_mcp_payload=encrypted_mcp_payload,
            now=now,
            command_ttl_seconds=command_ttl_seconds,
        )

    async def poll_commands(
        self,
        *,
        host_id: UUID,
        limit: int,
        acknowledged_command_ids: list[UUID],
        checkpoints: list[AgentHostRunCheckpoint],
        rejections: list[AgentHostCommandRejection],
        available_run_slots: int,
        now: datetime | None = None,
        lease_seconds: int = DEFAULT_RUN_LEASE_SECONDS,
    ) -> PolledCommands:
        timestamp = now or utcnow()
        acknowledged = await control_updates.acknowledge_commands(
            self.session,
            host_id=host_id,
            command_ids=acknowledged_command_ids,
            now=timestamp,
        )
        applied = await control_updates.apply_control_updates(
            self.session,
            self.uow,
            host_id=host_id,
            checkpoints=checkpoints,
            rejections=rejections,
            now=timestamp,
            lease_seconds=lease_seconds,
        )

        result = await self.session.execute(
            select(AgentHostCommandModel)
            .where(
                AgentHostCommandModel.host_id == host_id,
                AgentHostCommandModel.state.in_(
                    [
                        AgentHostCommandState.QUEUED.value,
                        AgentHostCommandState.DELIVERED.value,
                    ]
                ),
                AgentHostCommandModel.expires_at >= timestamp,
            )
            .order_by(
                _CONTROL_COMMANDS_FIRST.asc(),
                AgentHostCommandModel.created_at.asc(),
            )
            .limit(limit)
            # Competitive handout: concurrent API replicas polling the same
            # host must never be given the same command.
            .with_for_update(skip_locked=True)
        )
        commands = list(result.scalars())
        wire_commands: list[AgentHostCommand] = []
        remaining_run_slots = max(0, available_run_slots)
        for command in commands:
            if (
                command.kind == AgentHostCommandKind.START_RUN.value
                and remaining_run_slots == 0
            ):
                continue
            command.state = AgentHostCommandState.DELIVERED.value
            command.delivered_at = timestamp
            if (
                command.run_id is not None
                and command.lease_epoch is not None
                and command.kind == AgentHostCommandKind.START_RUN.value
            ):
                lease = await self.session.get(
                    AgentHostRunLeaseModel,
                    command.run_id,
                    with_for_update=True,
                )
                if lease is None or lease.lease_epoch != command.lease_epoch:
                    command.state = AgentHostCommandState.CANCELLED.value
                    continue
                if AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES:
                    command.state = AgentHostCommandState.CANCELLED.value
                    continue
                if lease.state == AgentHostRunState.QUEUED_FOR_HOST.value:
                    lease.state = AgentHostRunState.LEASED.value
                lease.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
                lease.updated_at = timestamp
                remaining_run_slots -= 1
            wire_commands.append(await self._wire_command(command))
        await self.session.flush()
        return PolledCommands(
            wire_commands,
            progressed=bool(acknowledged or applied),
        )

    async def append_events(
        self,
        *,
        host_id: UUID,
        batch: AgentHostEventBatch,
    ) -> AgentHostEventAck:
        """Accept one ordered batch of run events; see event_intake."""
        return await event_intake.append_events(
            self.session,
            self._events,
            host_id=host_id,
            batch=batch,
        )

    async def delete_run_events(self, *, run_id: UUID) -> None:
        """Drop a terminalized run's stream."""
        await self._events.delete(run_id=run_id)

    # -------------------------------------------------------- control updates
    #
    # The rules live in agent_host_control_updates; these keep one entry point
    # for callers, as the recovery and intake delegates above do.

    async def apply_checkpoint(
        self,
        *,
        host_id: UUID,
        checkpoint: AgentHostRunCheckpoint,
        now: datetime | None = None,
        lease_seconds: int = DEFAULT_RUN_LEASE_SECONDS,
    ) -> AgentHostRunLeaseModel | None:
        """Advance a run's state and extend its lease."""
        return await control_updates.apply_checkpoint(
            self.session,
            host_id=host_id,
            checkpoint=checkpoint,
            now=now,
            lease_seconds=lease_seconds,
        )

    async def apply_rejection(
        self,
        *,
        host_id: UUID,
        rejection: AgentHostCommandRejection,
        now: datetime | None = None,
    ) -> bool:
        """Persist one fenced pre-dispatch rejection atomically."""
        return await control_updates.apply_rejection(
            self.session,
            host_id=host_id,
            rejection=rejection,
            now=now,
        )

    # --------------------------------------------------------------- lifecycle

    async def get_run_lease(self, *, run_id: UUID) -> AgentHostRunLeaseModel | None:
        return await self.session.get(AgentHostRunLeaseModel, run_id)

    async def enqueue_cancel(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
    ) -> AgentHostCommandModel | None:
        """Queue one CANCEL_RUN for a live run, at most one at a time.

        Every path that gives up on a run calls this — the deadline, a stop
        request, a stream outage — so without the liveness check a run being
        abandoned stacks a fresh command on each attempt. They all say the same
        thing, they all occupy the poll's command limit, and the same guard
        already protects the abandoned-run sweep.
        """
        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            run_id,
            with_for_update=True,
        )
        if (
            lease is None
            or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
        ):
            return None
        if await recovery.cancel_already_queued(
            self.session,
            run_id=run_id,
            lease_epoch=lease.lease_epoch,
        ):
            return None
        command = AgentHostCommandModel(
            host_id=lease.host_id,
            run_id=run_id,
            kind=AgentHostCommandKind.CANCEL_RUN.value,
            lease_epoch=lease.lease_epoch,
            payload={"agent_run_id": str(run_id)},
            state=AgentHostCommandState.QUEUED.value,
            expires_at=timestamp + timedelta(seconds=DEFAULT_COMMAND_TTL_SECONDS),
        )
        self.session.add(command)
        await self.session.flush()
        return command

    # Recovery and retention live in agent_host_recovery as plain functions;
    # these keep a single entry point for callers.

    async def enqueue_permission_decision(
        self,
        *,
        run_id: UUID,
        request_id: str,
        option_id: str | None,
        now: datetime | None = None,
    ) -> AgentHostCommandModel | None:
        """Answer a permission request the host is holding open.

        ``option_id`` selects one of the options the agent offered; ``None``
        denies. Returns None when the run already ended, in which case there is
        nothing left holding the request and the host's own timeout applies.
        """
        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            run_id,
            with_for_update=True,
        )
        if (
            lease is None
            or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
        ):
            return None
        command = AgentHostCommandModel(
            host_id=lease.host_id,
            run_id=run_id,
            kind=AgentHostCommandKind.RESOLVE_PERMISSION.value,
            lease_epoch=lease.lease_epoch,
            payload={"request_id": request_id, "option_id": option_id},
            state=AgentHostCommandState.QUEUED.value,
            expires_at=timestamp
            + timedelta(seconds=DEFAULT_PERMISSION_COMMAND_TTL_SECONDS),
        )
        self.session.add(command)
        await self.session.flush()
        return command

    async def enqueue_credential_refresh(
        self,
        *,
        run_id: UUID,
        encrypted_mcp_payload: dict,
        now: datetime | None = None,
    ) -> AgentHostCommandModel | None:
        """Hand a run still in flight a replacement Lemma MCP credential.

        Returns None once the run is over, when there is nothing left to
        refresh. Fenced on the current lease epoch so a credential minted for a
        superseded dispatch cannot land on the run executing now.
        """
        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            run_id,
            with_for_update=True,
        )
        if (
            lease is None
            or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
        ):
            return None
        command = AgentHostCommandModel(
            host_id=lease.host_id,
            run_id=run_id,
            kind=AgentHostCommandKind.REFRESH_CREDENTIAL.value,
            lease_epoch=lease.lease_epoch,
            payload={"encrypted_mcp": encrypted_mcp_payload},
            state=AgentHostCommandState.QUEUED.value,
            expires_at=timestamp + timedelta(seconds=DEFAULT_COMMAND_TTL_SECONDS),
        )
        self.session.add(command)
        await self.session.flush()
        return command

    async def expire_unaccepted_run(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
    ) -> AgentHostRunState | None:
        return await recovery.expire_unaccepted_run(
            self.session, run_id=run_id, now=now
        )

    async def reconcile_expired_run(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
        recovery_grace_seconds: int = (recovery.DEFAULT_RECOVERY_GRACE_SECONDS),
    ) -> AgentHostRunLeaseModel | None:
        return await recovery.reconcile_expired_run(
            self.session,
            run_id=run_id,
            now=now,
            recovery_grace_seconds=recovery_grace_seconds,
        )

    async def cleanup_retained_state(self, *, now: datetime | None = None) -> None:
        return await recovery.cleanup_retained_state(self.session, now=now)

    @staticmethod
    async def _wire_command(command: AgentHostCommandModel) -> AgentHostCommand:
        payload = dict(command.payload or {})
        encrypted_mcp = payload.pop("encrypted_mcp", None)
        if encrypted_mcp is not None:
            mcp = await get_secret_cipher().decrypt_json_async(encrypted_mcp)
            if mcp is None:
                raise AgentHostProtocolViolation("MCP payload is unavailable")
            payload["mcp"] = mcp
        return AgentHostCommand(
            command_id=command.id,
            kind=AgentHostCommandKind(command.kind),
            created_at=command.created_at,
            expires_at=command.expires_at,
            run_id=command.run_id,
            lease_epoch=command.lease_epoch,
            payload=payload,
        )
