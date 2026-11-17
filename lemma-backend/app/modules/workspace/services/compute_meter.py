"""Metering machine time, and stopping a machine whose allowance is spent.

A running machine has one open ``machine_sessions`` row. The sweeper writes
its time to the usage ledger in slices of at most five minutes; a release or
a destroy writes the last slice and closes it. A slice is idempotent on
``(session, slice start)``, so the sweeper and a release racing each other
write it once. Connected machines are someone's own computer and cost
nothing; they are never metered.

A session ends however the machine stopped. Release, a home reset and a
delete close it themselves; every other stop -- the sweeper giving up on an
unreachable sandbox, a provider pausing one on its own timeout, a local
client or a size move destroying one -- is found by the next pass, which
closes every open session whose machine it no longer sees running at the
time it last saw it running. A session whose machine came back since
(``metered_until`` before the instance's ``ready_at``) is ended there and a
fresh one opened, so the time between is never billed and the resume is
noticed.

Metering is always on. Enforcement is behind ``COMPUTE_BILLING_ENABLED``:

- at 80% and 95% of whichever window applies, a warning (once per window);
- at 100%, no new processes; work already running gets a grace period, then
  the browser is closed cleanly, the logins copied off, and the machine
  released. The refusal names when the window resets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine import MachineSize
from app.modules.workspace.domain.machine_errors import MachinePolicyRefused
from app.modules.workspace.domain.sandbox import (
    SandboxDesiredState,
    SandboxInstanceState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.infrastructure.machine_models import MachineSessionModel
from app.modules.workspace.infrastructure.models import (
    SandboxInstanceModel,
    SandboxModel,
)

logger = get_logger(__name__)

SLICE = timedelta(minutes=5)
#: How far back a machine's first session may start. A machine that has been
#: up since before metering existed is billed from about when it began, not
#: from an old ``ready_at``.
FIRST_SESSION_LOOKBACK = timedelta(minutes=10)
GRACE = timedelta(minutes=10)
WARN_AT = (0.8, 0.95)


@dataclass(frozen=True, slots=True)
class RunningMachine:
    sandbox_id: UUID
    owner_kind: SandboxOwnerKind
    owner_id: UUID
    created_by: UUID | None
    size: str
    ready_at: datetime | None


def _units_per_hour(size: str) -> float:
    try:
        return MachineSize.parse(size).compute_units_per_hour
    except ValueError:
        return 0.5


#: Running machines read per query. The meter walks all of them every tick, so
#: it pages by id rather than holding the whole deployment in one result.
_PAGE = 500


async def _running(uow, *, after: UUID | None = None) -> list[RunningMachine]:
    """One page of running, metered machines, in id order after ``after``."""
    query = (
        select(SandboxModel, SandboxInstanceModel.ready_at)
        .join(
            SandboxInstanceModel,
            and_(
                SandboxInstanceModel.sandbox_id == SandboxModel.id,
                SandboxInstanceModel.epoch == SandboxModel.epoch,
            ),
        )
        .where(
            SandboxModel.kind == SandboxKind.WORKSPACE.value,
            SandboxModel.desired_state == SandboxDesiredState.PRESENT.value,
            SandboxModel.connection_id.is_(None),
            SandboxInstanceModel.state == SandboxInstanceState.READY.value,
        )
        .order_by(SandboxModel.id)
        .limit(_PAGE)
    )
    if after is not None:
        query = query.where(SandboxModel.id > after)
    rows = await uow.session.execute(query)
    return [
        RunningMachine(
            sandbox_id=row.id,
            owner_kind=SandboxOwnerKind(row.owner_kind),
            owner_id=row.owner_id,
            created_by=row.created_by,
            size=row.size,
            ready_at=ready_at,
        )
        for row, ready_at in rows.all()
    ]


async def _all_running(uow_factory: UnitOfWorkFactory) -> list[RunningMachine]:
    machines: list[RunningMachine] = []
    after: UUID | None = None
    while True:
        async with uow_factory() as uow:
            page = await _running(uow, after=after)
        machines.extend(page)
        if len(page) < _PAGE:
            return machines
        after = page[-1].sandbox_id


async def running_machines(uow_factory: UnitOfWorkFactory) -> list[RunningMachine]:
    """Every machine the rows say is running and metered."""
    return await _all_running(uow_factory)


async def _billing(
    uow, machine: RunningMachine
) -> tuple[UUID, UUID | None, UUID | None]:
    """``(user, organization, pod)`` a machine's time is recorded against."""
    if machine.owner_kind is SandboxOwnerKind.POD:
        from app.modules.pod.contracts.members import pod_organization_id

        organization = await pod_organization_id(uow, machine.owner_id)
        return machine.created_by or machine.owner_id, organization, machine.owner_id
    return machine.owner_id, None, None


async def _open_session_row(uow, machine_id: UUID) -> MachineSessionModel | None:
    return await uow.session.scalar(
        select(MachineSessionModel)
        .where(
            MachineSessionModel.sandbox_id == machine_id,
            MachineSessionModel.ended_at.is_(None),
        )
        .with_for_update()
    )


async def _first_start(uow, machine: RunningMachine, now: datetime) -> datetime:
    """Where a new session begins: the instance's ``ready_at``, but never
    before the machine's last session ended, and -- for a machine with no
    session at all -- never further back than ``FIRST_SESSION_LOOKBACK``."""
    last_stop = await uow.session.scalar(
        select(func.max(MachineSessionModel.ended_at)).where(
            MachineSessionModel.sandbox_id == machine.sandbox_id
        )
    )
    floor = last_stop if last_stop is not None else now - FIRST_SESSION_LOOKBACK
    started = max(machine.ready_at or now, floor)
    return min(started, now)


async def _open_machine_session(
    uow, machine: RunningMachine, now: datetime
) -> tuple[MachineSessionModel, bool]:
    """The machine's open session, and whether it was opened just now -- a
    machine that started or resumed since the last pass.

    An open session the machine has since restarted past (it was metered up
    to before the instance came up) is ended where its metering stopped, so
    the stopped time between is never billed, and a fresh one is opened.
    """
    found = await _open_session_row(uow, machine.sandbox_id)
    if found is not None:
        if machine.ready_at is None or found.metered_until >= machine.ready_at:
            return found, False
        found.ended_at = found.metered_until
        await uow.session.flush()
    user_id, organization_id, pod_id = await _billing(uow, machine)
    started = await _first_start(uow, machine, now)
    # ON CONFLICT: the open-session index is unique, and a second pass racing
    # this one may have opened it first. Theirs is the one to use.
    await uow.session.execute(
        insert(MachineSessionModel)
        .values(
            id=uuid7(),
            sandbox_id=machine.sandbox_id,
            size=machine.size,
            compute_units_per_hour=_units_per_hour(machine.size),
            billing_scope="org" if pod_id is not None else "user",
            user_id=user_id,
            organization_id=organization_id,
            pod_id=pod_id,
            started_at=started,
            metered_until=started,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=[MachineSessionModel.sandbox_id],
            index_where=MachineSessionModel.ended_at.is_(None),
        )
    )
    opened = await _open_session_row(uow, machine.sandbox_id)
    assert opened is not None
    return opened, True


async def _write_slices(uow, session: MachineSessionModel, until: datetime) -> int:
    from app.modules.usage.contracts.compute import (
        ComputeSlice,
        record_compute_slice,
        resolve_limits,
    )

    if session.user_id is None or until <= session.metered_until:
        return 0
    limits = await resolve_limits(
        uow, organization_id=session.organization_id, user_id=session.user_id
    )
    written = 0
    start = session.metered_until
    while start < until:
        end = min(start + SLICE, until)
        hours = (end - start).total_seconds() / 3600
        if await record_compute_slice(
            uow,
            ComputeSlice(
                session_id=session.id,
                machine_id=session.sandbox_id,
                slice_start=start,
                slice_end=end,
                compute_units=round(hours * session.compute_units_per_hour, 6),
                user_id=session.user_id,
                organization_id=session.organization_id,
                pod_id=session.pod_id,
                billed_to_org=session.billing_scope == "org",
                size=session.size,
            ),
            unit_usd=limits.compute_unit_usd,
        ):
            written += 1
        start = end
    session.metered_until = until
    return written


@dataclass(frozen=True, slots=True)
class MeterTick:
    """What one metering pass saw, for the rest of the sweep to act on."""

    written: int
    #: Every machine running now.
    running: list[UUID]
    #: Those that started or resumed since the last pass.
    resumed: list[UUID]


async def meter_running(
    uow_factory: UnitOfWorkFactory,
    *,
    now: datetime | None = None,
    stopped: frozenset[UUID] = frozenset(),
) -> MeterTick:
    """Write every running machine's time up to now, and end the sessions of
    machines that stopped without saying so.

    ``stopped`` is what the provider reports as not running though the row
    says READY -- an E2B sandbox it paused on its own timeout. Those count as
    stopped: their session ends and no time is written for them.
    """
    now = now or datetime.now(timezone.utc)
    machines = [
        machine
        for machine in await _all_running(uow_factory)
        if machine.sandbox_id not in stopped
    ]
    written = 0
    resumed: list[UUID] = []
    for machine in machines:
        async with uow_factory() as uow:
            session, opened = await _open_machine_session(uow, machine, now)
            written += await _write_slices(uow, session, now)
            await uow.commit()
        if opened:
            resumed.append(machine.sandbox_id)
    running = [machine.sandbox_id for machine in machines]
    await end_unseen_sessions(uow_factory, running=frozenset(running))
    return MeterTick(written=written, running=running, resumed=resumed)


async def end_unseen_sessions(
    uow_factory: UnitOfWorkFactory, *, running: frozenset[UUID]
) -> list[UUID]:
    """End every open session whose machine is not running, at the last time
    it was seen running. Returns those machines."""
    ended: list[UUID] = []
    while True:
        async with uow_factory() as uow:
            query = (
                select(MachineSessionModel)
                .where(MachineSessionModel.ended_at.is_(None))
                .order_by(MachineSessionModel.sandbox_id)
                .limit(_PAGE)
                .with_for_update(skip_locked=True)
            )
            if running:
                query = query.where(MachineSessionModel.sandbox_id.not_in(running))
            sessions = (await uow.session.scalars(query)).all()
            for session in sessions:
                session.ended_at = session.metered_until
                ended.append(session.sandbox_id)
            await uow.commit()
        # Each page is ended as it is read, so the next read starts past it.
        if len(sessions) < _PAGE:
            break
    if ended:
        logger.info(
            "workspace.compute_meter.unseen_sessions_ended.observed",
            ended_count=len(ended),
        )
    return ended


async def close_sessions(
    uow_factory: UnitOfWorkFactory, machine_id: UUID, *, now: datetime | None = None
) -> None:
    """The last slice of a machine that just stopped, and the end of its session."""
    now = now or datetime.now(timezone.utc)
    async with uow_factory() as uow:
        sessions = (
            await uow.session.scalars(
                select(MachineSessionModel)
                .where(
                    MachineSessionModel.sandbox_id == machine_id,
                    MachineSessionModel.ended_at.is_(None),
                )
                .with_for_update()
            )
        ).all()
        for session in sessions:
            await _write_slices(uow, session, now)
            await uow.session.execute(
                update(MachineSessionModel)
                .where(MachineSessionModel.id == session.id)
                .values(ended_at=now, metered_until=now)
            )
        await uow.commit()


# --- enforcement -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComputeVerdict:
    fraction: float | None
    resets_at: datetime
    exhausted: bool

    @property
    def message(self) -> str:
        return (
            "This machine's compute for the month is used up; it resets on "
            f"{self.resets_at:%d %B}."
        )


async def verdict_for(
    uow, *, owner_kind: SandboxOwnerKind, owner_id: UUID, created_by: UUID | None
) -> ComputeVerdict:
    from app.modules.usage.contracts.compute import compute_standing

    machine = RunningMachine(
        sandbox_id=owner_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        created_by=created_by,
        size="2x4",
        ready_at=None,
    )
    user_id, organization_id, pod_id = await _billing(uow, machine)
    standing = await compute_standing(
        uow,
        user_id=user_id,
        organization_id=organization_id,
        billed_to_org=pod_id is not None,
    )
    fraction = standing.fraction
    return ComputeVerdict(
        fraction=fraction,
        resets_at=standing.resets_at,
        exhausted=fraction is not None and fraction >= 1.0,
    )


async def refuse_if_exhausted(
    uow, *, owner_kind: SandboxOwnerKind, owner_id: UUID, created_by: UUID | None
) -> None:
    """Called before new work starts on a machine: a process, a resume."""
    if not workspace_settings.compute_billing_enabled:
        return
    verdict = await verdict_for(
        uow, owner_kind=owner_kind, owner_id=owner_id, created_by=created_by
    )
    if verdict.exhausted:
        raise MachinePolicyRefused(verdict.message, code="MACHINE_COMPUTE_EXHAUSTED")


async def enforce(
    uow_factory: UnitOfWorkFactory, *, now: datetime | None = None
) -> list[UUID]:
    """Warn at the thresholds; release machines past their grace. Returns the
    machines released."""
    if not workspace_settings.compute_billing_enabled:
        return []
    from app.modules.workspace.services.compute_notices import ComputeNotices

    now = now or datetime.now(timezone.utc)
    notices = ComputeNotices()
    released: list[UUID] = []
    machines = await _all_running(uow_factory)
    for machine in machines:
        async with uow_factory() as uow:
            verdict = await verdict_for(
                uow,
                owner_kind=machine.owner_kind,
                owner_id=machine.owner_id,
                created_by=machine.created_by,
            )
        subject = f"{machine.owner_kind.value}:{machine.owner_id}"
        await notices.warn_if_crossed(subject, verdict, sandbox_id=machine.sandbox_id)
        if not verdict.exhausted:
            await notices.clear_exhausted(machine.sandbox_id)
            continue
        since = await notices.exhausted_since(machine.sandbox_id, now)
        if now - since < GRACE:
            continue
        await _release_for_compute(uow_factory, machine)
        released.append(machine.sandbox_id)
    return released


async def _release_for_compute(
    uow_factory: UnitOfWorkFactory, machine: RunningMachine
) -> None:
    from app.modules.workspace.infrastructure.machine_repository import (
        MachineRepository,
    )
    from app.modules.workspace.services.machine_lifecycle import MachineLifecycle

    async with uow_factory() as uow:
        found = await MachineRepository(uow).get(machine.sandbox_id)
    if found is None:
        return
    await MachineLifecycle(uow_factory).release(
        found, acting_user=machine.created_by or machine.owner_id
    )
    logger.info(
        "workspace.compute_meter.released_out_of_compute.observed",
        sandbox_id=str(machine.sandbox_id),
    )
