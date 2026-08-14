"""Applying what a host reports about its runs: acks, checkpoints, rejections.

Plain functions over a session, following ``agent_host_recovery`` and
``agent_host_event_intake``: one caller, no polymorphism to add.

Every rule here exists because delivery is at-least-once and the host only
clears its outbox once we accept an update. Anything we refuse is something
it resends forever, and the poll carrying these updates is also the only way
commands reach that host -- so a stale update must be a no-op, never an
error, and never a reason to fail the request around it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostCommandKind,
    AgentHostCommandRejection,
    AgentHostCommandState,
    AgentHostRejectionCode,
    AgentHostRunCheckpoint,
    AgentHostRunState,
    run_state_progresses,
)
from app.modules.agent.infrastructure import agent_host_session_memory
from app.modules.agent.infrastructure.agent_host_command_remint import (
    RemintOutcome,
    remint_for_current_revision,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
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


async def apply_control_updates(
    session: AsyncSession,
    uow: SqlAlchemyUnitOfWork,
    *,
    host_id: UUID,
    checkpoints: list[AgentHostRunCheckpoint],
    rejections: list[AgentHostCommandRejection],
    now: datetime,
    lease_seconds: int,
) -> int:
    """Apply the host's reported updates, isolating each one.

    Returns how many of them actually changed something, so a poll carrying
    nothing but repeated heartbeats can be told apart from one carrying
    news.

    A failure here is one run's problem and must never become this host's:
    the poll that carries these updates is also the only way commands reach
    the host, so raising would stop CANCEL_RUN and RESOLVE_PERMISSION
    reaching every other run it is executing. The host would then resend the
    same update on its next poll and wedge itself permanently.

    ValueError is caught alongside the typed failures because a lease row
    carrying a state this build no longer parses would otherwise raise out
    of the enum conversion and wedge the host just as effectively.
    """
    changed = 0
    for checkpoint in checkpoints:
        try:
            if await agent_host_session_memory.remember_provider_session(
                uow, checkpoint
            ):
                changed += 1
            _, advanced = await _apply_checkpoint(
                session,
                host_id=host_id,
                checkpoint=checkpoint,
                now=now,
                lease_seconds=lease_seconds,
            )
            changed += int(advanced)
        except (AgentHostRepositoryError, ValueError) as exc:
            _log_unappliable_update(
                kind="checkpoint",
                host_id=host_id,
                run_id=checkpoint.run_id,
                exc=exc,
            )
    for rejection in rejections:
        try:
            changed += int(
                await apply_rejection(
                session,
                    host_id=host_id,
                    rejection=rejection,
                    now=now,
                )
            )
        except (AgentHostRepositoryError, ValueError) as exc:
            _log_unappliable_update(
                kind="rejection",
                host_id=host_id,
                run_id=rejection.run_id,
                exc=exc,
            )
    return changed

async def _rejection_target(
    session: AsyncSession,
    *,
    host_id: UUID,
    rejection: AgentHostCommandRejection,
) -> tuple[AgentHostCommandModel, AgentHostRunLeaseModel] | None:
    """The rows this receipt is allowed to move, or ``None`` if it is stale.

    Two different kinds of "no" live here. A receipt whose identity does not
    match its command is a protocol violation and raises -- that is a host
    sending us something incoherent. A receipt for a lease that has moved on is
    simply late, and must be a silent no-op: the host resends anything we
    refuse, and the poll carrying it is also the only way commands reach that
    host, so an error here would wedge it.
    """
    command = await session.get(
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
    lease = await session.get(
        AgentHostRunLeaseModel,
        rejection.run_id,
        with_for_update=True,
    )
    if (
        lease is None
        or lease.host_id != host_id
        or lease.lease_epoch != rejection.lease_epoch
    ):
        return None
    if (
        lease.accepted_at is not None
        or command.state == AgentHostCommandState.ACKNOWLEDGED.value
        or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
    ):
        return None
    return command, lease


async def _answer_stale_revision(
    session: AsyncSession,
    *,
    host_id: UUID,
    rejection: AgentHostCommandRejection,
    command: AgentHostCommandModel,
) -> RemintOutcome:
    """Re-aim a command the host refused for naming a superseded revision.

    Every other rejection gets an inert outcome and is simply recorded.

    Decided on the *code*, not on the host's ``retryable`` flag, and this is the
    only place in the backend that reads it. That way an older Agent Host still
    gets the repair -- it has no idea this exists -- and a newer one talking to
    an older Lemma still fails fast rather than spinning on a payload nobody
    re-aims.
    """
    if rejection.code is not AgentHostRejectionCode.CONFIG_REVISION_STALE:
        return RemintOutcome(requeue=False, attempts=0)
    remint = await remint_for_current_revision(session, command=command)
    if remint.refusal is not None:
        logger.warning(
            "agent.infrastructure.agent_host_command_remint.refused",
            agent_run_id=str(rejection.run_id),
            host_id=str(host_id),
            harness_key=None,
            attempt=remint.attempts,
            refusal=remint.refusal,
        )
    return remint


async def apply_rejection(
    session: AsyncSession,
    *,
    host_id: UUID,
    rejection: AgentHostCommandRejection,
    now: datetime | None = None,
) -> bool:
    """Persist one fenced pre-dispatch rejection atomically.

    A receipt can requeue an unaccepted command or terminalize it, but it
    can never move an accepted lease backwards. Duplicate and stale
    receipts therefore become harmless no-ops.

    Returns whether this receipt changed anything, so a resent one is not
    mistaken for news.
    """
    timestamp = now or utcnow()
    target = await _rejection_target(session, host_id=host_id, rejection=rejection)
    if target is None:
        return False
    command, lease = target

    remint = await _answer_stale_revision(
        session, host_id=host_id, rejection=rejection, command=command
    )
    detail = remint.refusal or rejection.detail

    command.rejection = {
        "code": rejection.code.value,
        "retryable": rejection.retryable,
        "detail": detail,
        "rejected_at": timestamp.isoformat(),
        # Carried on the command rather than in a new column, because it is the
        # only thing that bounds the re-aim loop: the poll hands back whatever
        # is QUEUED and counts nothing.
        **remint.receipt,
    }
    if rejection.retryable or remint.requeue:
        command.state = AgentHostCommandState.QUEUED.value
        command.delivered_at = None
        lease.state = AgentHostRunState.QUEUED_FOR_HOST.value
    else:
        command.state = AgentHostCommandState.ACKNOWLEDGED.value
        command.acknowledged_at = timestamp
        lease.state = AgentHostRunState.FAILED.value
        lease.terminal_at = timestamp
        lease.error_code = rejection.code.value
        lease.error_detail = detail
    lease.updated_at = timestamp
    await session.flush()
    return True

async def acknowledge_commands(
    session: AsyncSession,
    *,
    host_id: UUID,
    command_ids: list[UUID],
    now: datetime,
) -> int:
    """Mark delivered commands done; returns how many actually changed."""
    if not command_ids:
        return 0
    result = await session.execute(
        select(AgentHostCommandModel)
        .where(
            AgentHostCommandModel.host_id == host_id,
            AgentHostCommandModel.id.in_(command_ids),
        )
        .with_for_update()
    )
    found = {command.id: command for command in result.scalars()}
    acknowledged = 0
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
            AgentHostCommandState.ACKNOWLEDGED.value,
        }:
            continue
        command.state = AgentHostCommandState.ACKNOWLEDGED.value
        command.acknowledged_at = now
        acknowledged += 1
    return acknowledged

async def apply_checkpoint(
    session: AsyncSession,
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

    Idempotent in exactly the way :func:`apply_rejection` is, and for the
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
    lease, _ = await _apply_checkpoint(
                session,
        host_id=host_id,
        checkpoint=checkpoint,
        now=now,
        lease_seconds=lease_seconds,
    )
    return lease

async def _apply_checkpoint(
    session: AsyncSession,
    *,
    host_id: UUID,
    checkpoint: AgentHostRunCheckpoint,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_RUN_LEASE_SECONDS,
) -> tuple[AgentHostRunLeaseModel | None, bool]:
    """:meth:`apply_checkpoint`, also saying whether the state advanced.

    The lease alone cannot answer that: a terminal run re-reporting the
    terminal state it already holds returns its lease and changes nothing,
    and a still-RUNNING run re-reporting RUNNING is the heartbeat rather
    than news. Both extend the lease; neither is a reason to cut a long
    poll short.
    """
    timestamp = now or utcnow()
    lease = await session.get(
        AgentHostRunLeaseModel,
        checkpoint.run_id,
        with_for_update=True,
    )
    if lease is None or lease.host_id != host_id:
        return None, False
    if lease.lease_epoch != checkpoint.lease_epoch:
        return None, False
    current_state = AgentHostRunState(lease.state)
    reported = checkpoint.state
    if current_state in TERMINAL_AGENT_HOST_RUN_STATES:
        return (lease, False) if reported is current_state else (None, False)
    if not run_state_progresses(current_state, reported):
        return None, False
    advanced = reported is not current_state
    # accepted_at is the single fence between pre-dispatch (safe to retry
    # or fall back) and accepted (never repeated). Crossing it is news in
    # its own right, whatever the state did.
    if lease.accepted_at is None:
        lease.accepted_at = timestamp
        advanced = True
    if reported in TERMINAL_AGENT_HOST_RUN_STATES:
        lease.terminal_at = timestamp
    lease.state = reported.value
    lease.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
    lease.updated_at = timestamp
    await session.flush()
    return lease, advanced

# ------------------------------------------------------------------ events
