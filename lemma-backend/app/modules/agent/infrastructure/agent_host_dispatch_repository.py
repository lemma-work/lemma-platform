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

from sqlalchemy import select

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
    run_state_progresses,
)
from app.modules.agent.infrastructure.agent_host_event_stream import (
    AgentHostEventStream,
    agent_host_event_stream,
)
from app.modules.agent.infrastructure import (
    agent_host_admission,
    agent_host_event_intake,
    agent_host_recovery,
    agent_host_session_memory,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    DEFAULT_COMMAND_TTL_SECONDS,
    DEFAULT_PERMISSION_COMMAND_TTL_SECONDS,
    DEFAULT_RUN_LEASE_SECONDS,
    AgentHostNotFound,
    AgentHostProtocolViolation,
    AgentHostRepositoryError,
    utcnow,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
)


logger = get_logger(__name__)


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
        """Admit one run onto a host; see agent_host_admission."""
        return await agent_host_admission.enqueue_run(
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
    ) -> list[AgentHostCommand]:
        timestamp = now or utcnow()
        await self._acknowledge_commands(
            host_id=host_id,
            command_ids=acknowledged_command_ids,
            now=timestamp,
        )
        await self._apply_control_updates(
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
            .order_by(AgentHostCommandModel.created_at.asc())
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
        return wire_commands

    async def _apply_control_updates(
        self,
        *,
        host_id: UUID,
        checkpoints: list[AgentHostRunCheckpoint],
        rejections: list[AgentHostCommandRejection],
        now: datetime,
        lease_seconds: int,
    ) -> None:
        """Apply the host's reported updates, isolating each one.

        A failure here is one run's problem and must never become this host's:
        the poll that carries these updates is also the only way commands reach
        the host, so raising would stop CANCEL_RUN and RESOLVE_PERMISSION
        reaching every other run it is executing. The host would then resend the
        same update on its next poll and wedge itself permanently.

        ValueError is caught alongside the typed failures because a lease row
        carrying a state this build no longer parses would otherwise raise out
        of the enum conversion and wedge the host just as effectively.
        """
        for checkpoint in checkpoints:
            try:
                await agent_host_session_memory.remember_provider_session(
                    self.uow, checkpoint
                )
                await self.apply_checkpoint(
                    host_id=host_id,
                    checkpoint=checkpoint,
                    now=now,
                    lease_seconds=lease_seconds,
                )
            except (AgentHostRepositoryError, ValueError) as exc:
                _log_unappliable_update(
                    kind="checkpoint",
                    host_id=host_id,
                    run_id=checkpoint.run_id,
                    exc=exc,
                )
        for rejection in rejections:
            try:
                await self.apply_rejection(
                    host_id=host_id,
                    rejection=rejection,
                    now=now,
                )
            except (AgentHostRepositoryError, ValueError) as exc:
                _log_unappliable_update(
                    kind="rejection",
                    host_id=host_id,
                    run_id=rejection.run_id,
                    exc=exc,
                )

    async def apply_rejection(
        self,
        *,
        host_id: UUID,
        rejection: AgentHostCommandRejection,
        now: datetime | None = None,
    ) -> None:
        """Persist one fenced pre-dispatch rejection atomically.

        A receipt can requeue an unaccepted command or terminalize it, but it
        can never move an accepted lease backwards. Duplicate and stale
        receipts therefore become harmless no-ops.
        """
        timestamp = now or utcnow()
        command = await self.session.get(
            AgentHostCommandModel,
            rejection.command_id,
            with_for_update=True,
        )
        if command is None or command.host_id != host_id:
            raise AgentHostProtocolViolation(
                "rejected command does not belong to this host"
            )
        if (
            command.kind != AgentHostCommandKind.START_RUN.value
            or command.run_id != rejection.run_id
            or command.lease_epoch != rejection.lease_epoch
        ):
            raise AgentHostProtocolViolation(
                "rejection identity does not match command"
            )
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            rejection.run_id,
            with_for_update=True,
        )
        if (
            lease is None
            or lease.host_id != host_id
            or lease.lease_epoch != rejection.lease_epoch
        ):
            return
        if (
            lease.accepted_at is not None
            or command.state == AgentHostCommandState.ACKNOWLEDGED.value
            or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
        ):
            return

        command.rejection = {
            "code": rejection.code.value,
            "retryable": rejection.retryable,
            "detail": rejection.detail,
            "rejected_at": timestamp.isoformat(),
        }
        if rejection.retryable:
            command.state = AgentHostCommandState.QUEUED.value
            command.delivered_at = None
            lease.state = AgentHostRunState.QUEUED_FOR_HOST.value
        else:
            command.state = AgentHostCommandState.ACKNOWLEDGED.value
            command.acknowledged_at = timestamp
            lease.state = AgentHostRunState.FAILED.value
            lease.terminal_at = timestamp
            lease.error_code = rejection.code.value
            lease.error_detail = rejection.detail
        lease.updated_at = timestamp
        await self.session.flush()

    async def _acknowledge_commands(
        self,
        *,
        host_id: UUID,
        command_ids: list[UUID],
        now: datetime,
    ) -> None:
        if not command_ids:
            return
        result = await self.session.execute(
            select(AgentHostCommandModel)
            .where(
                AgentHostCommandModel.host_id == host_id,
                AgentHostCommandModel.id.in_(command_ids),
            )
            .with_for_update()
        )
        found = {command.id: command for command in result.scalars()}
        for command_id in command_ids:
            command = found.get(command_id)
            if command is None:
                # Nothing to mark: the command was swept by retention, or it
                # was never ours. Either way the host has already stopped
                # executing it, and refusing the poll would only make it resend
                # the same acknowledgement forever.
                _log_unappliable_update(
                    kind="acknowledgement",
                    host_id=host_id,
                    run_id=None,
                    exc=AgentHostNotFound(f"command {command_id} is unknown"),
                )
                continue
            if command.state in {
                AgentHostCommandState.CANCELLED.value,
                AgentHostCommandState.EXPIRED.value,
            }:
                continue
            command.state = AgentHostCommandState.ACKNOWLEDGED.value
            command.acknowledged_at = now

    async def apply_checkpoint(
        self,
        *,
        host_id: UUID,
        checkpoint: AgentHostRunCheckpoint,
        now: datetime | None = None,
        lease_seconds: int = DEFAULT_RUN_LEASE_SECONDS,
    ) -> AgentHostRunLeaseModel | None:
        """Advance a run's state and extend its lease.

        This is the lease heartbeat. Event batches deliberately do not extend
        it, so an active run is kept alive by its poll cycle rather than by a
        row write per batch of output.

        Idempotent in exactly the way :meth:`apply_rejection` is, and for the
        same reason: a checkpoint the host cannot get us to accept is one it
        resends every poll forever. A checkpoint for a lease we no longer have,
        for a superseded epoch, for an already-terminal run, or reporting a
        state behind the one we hold is *information we have already acted on*,
        not a protocol breach. All four return ``None`` and change nothing.

        The realistic case is not a buggy host: a laptop sleeps, we reconcile
        the run to RECOVERING and then to the terminal DISPATCH_UNKNOWN, and the
        laptop wakes up still believing it is RUNNING. Its next checkpoint is
        both terminal-violating and regressive, and it will resend it until we
        stop refusing it.
        """
        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            checkpoint.run_id,
            with_for_update=True,
        )
        if lease is None or lease.host_id != host_id:
            return None
        if lease.lease_epoch != checkpoint.lease_epoch:
            return None
        current_state = AgentHostRunState(lease.state)
        reported = checkpoint.state
        if current_state in TERMINAL_AGENT_HOST_RUN_STATES:
            return lease if reported is current_state else None
        if not run_state_progresses(current_state, reported):
            return None
        # accepted_at is the single fence between pre-dispatch (safe to retry
        # or fall back) and accepted (never repeated).
        if lease.accepted_at is None:
            lease.accepted_at = timestamp
        if reported in TERMINAL_AGENT_HOST_RUN_STATES:
            lease.terminal_at = timestamp
        lease.state = reported.value
        lease.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
        lease.updated_at = timestamp
        await self.session.flush()
        return lease

    # ------------------------------------------------------------------ events

    async def append_events(
        self,
        *,
        host_id: UUID,
        batch: AgentHostEventBatch,
    ) -> AgentHostEventAck:
        """Accept one ordered batch of run events; see agent_host_event_intake."""
        return await agent_host_event_intake.append_events(
            self.session,
            self._events,
            host_id=host_id,
            batch=batch,
        )

    async def delete_run_events(self, *, run_id: UUID) -> None:
        """Drop a terminalized run's stream."""
        await self._events.delete(run_id=run_id)

    # --------------------------------------------------------------- lifecycle

    async def get_run_lease(self, *, run_id: UUID) -> AgentHostRunLeaseModel | None:
        return await self.session.get(AgentHostRunLeaseModel, run_id)

    async def enqueue_cancel(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
    ) -> AgentHostCommandModel | None:
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

    async def expire_unaccepted_run(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
    ) -> AgentHostRunState | None:
        return await agent_host_recovery.expire_unaccepted_run(
            self.session, run_id=run_id, now=now
        )

    async def reconcile_expired_run(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
        recovery_grace_seconds: int = (
            agent_host_recovery.DEFAULT_RECOVERY_GRACE_SECONDS
        ),
    ) -> AgentHostRunLeaseModel | None:
        return await agent_host_recovery.reconcile_expired_run(
            self.session,
            run_id=run_id,
            now=now,
            recovery_grace_seconds=recovery_grace_seconds,
        )

    async def cleanup_retained_state(self, *, now: datetime | None = None) -> None:
        return await agent_host_recovery.cleanup_retained_state(self.session, now=now)

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
