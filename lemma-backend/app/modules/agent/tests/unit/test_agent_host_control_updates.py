"""A host must never be able to wedge itself by reporting the same update.

Delivery is at-least-once and the host only clears a control update from its
outbox once the backend accepts it. So an update the backend refuses is an
update the host resends every poll, forever. And because the poll that carries
control updates up is the same poll that carries commands down, refusing one
checkpoint stops CANCEL_RUN and RESOLVE_PERMISSION reaching that host for
*every* run it is executing.

The trigger is not a buggy host. A laptop sleeps for half an hour; Lemma
reconciles the run to RECOVERING and then to the terminal DISPATCH_UNKNOWN; the
laptop wakes and heartbeats the RUNNING it still believes in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest

from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostRunCheckpoint,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host_dispatch_repository import (
    AgentHostDispatchRepository,
)


pytestmark = pytest.mark.asyncio


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _Lease:
    run_id: UUID
    host_id: UUID
    lease_epoch: int = 1
    state: str = AgentHostRunState.RUNNING.value
    accepted_at: datetime | None = None
    terminal_at: datetime | None = None
    lease_expires_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    error_code: str | None = None
    error_detail: str | None = None


@dataclass
class _Command:
    id: UUID
    host_id: UUID
    run_id: UUID | None
    kind: str
    lease_epoch: int | None
    state: str = AgentHostCommandState.QUEUED.value
    created_at: datetime = field(default_factory=_now)
    expires_at: datetime = field(
        default_factory=lambda: _now() + timedelta(minutes=5)
    )
    delivered_at: datetime | None = None
    acknowledged_at: datetime | None = None
    rejection: dict | None = None
    payload: dict = field(default_factory=dict)


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _Session:
    """Stands in for the session, serving leases by id and commands by query.

    Deliberately dumb: the point of these tests is which rows the repository
    decides to touch, not how SQLAlchemy renders the query.
    """

    def __init__(self, *, leases: list[_Lease], commands: list[_Command]) -> None:
        self.leases = {lease.run_id: lease for lease in leases}
        self.commands = commands
        self.flushes = 0

    async def get(self, model, pk, with_for_update: bool = False):
        if model.__name__ == "AgentHostRunLeaseModel":
            return self.leases.get(pk)
        return next((command for command in self.commands if command.id == pk), None)

    async def execute(self, _statement):
        return _Result(list(self.commands))

    async def flush(self) -> None:
        self.flushes += 1


class _Uow:
    def __init__(self, session: _Session) -> None:
        self.session = session


def _repo(session: _Session) -> AgentHostDispatchRepository:
    return AgentHostDispatchRepository(_Uow(session), event_stream=object())


def _checkpoint(
    run_id: UUID,
    state: AgentHostRunState,
    *,
    epoch: int = 1,
) -> AgentHostRunCheckpoint:
    return AgentHostRunCheckpoint(run_id=run_id, lease_epoch=epoch, state=state)


class TestUnappliableCheckpointsAreNoOps:
    async def test_a_woken_host_reporting_running_on_a_terminal_run(self) -> None:
        """The laptop-sleep case. This used to raise and wedge the host."""
        run_id, host_id = uuid7(), uuid7()
        lease = _Lease(
            run_id=run_id,
            host_id=host_id,
            state=AgentHostRunState.DISPATCH_UNKNOWN.value,
            accepted_at=_now(),
        )
        session = _Session(leases=[lease], commands=[])

        applied = await _repo(session).apply_checkpoint(
            host_id=host_id,
            checkpoint=_checkpoint(run_id, AgentHostRunState.RUNNING),
        )

        assert applied is None
        assert lease.state == AgentHostRunState.DISPATCH_UNKNOWN.value

    async def test_a_regressed_state_is_ignored_rather_than_refused(self) -> None:
        run_id, host_id = uuid7(), uuid7()
        lease = _Lease(run_id=run_id, host_id=host_id, accepted_at=_now())
        session = _Session(leases=[lease], commands=[])

        applied = await _repo(session).apply_checkpoint(
            host_id=host_id,
            checkpoint=_checkpoint(run_id, AgentHostRunState.LEASED),
        )

        assert applied is None
        assert lease.state == AgentHostRunState.RUNNING.value

    async def test_a_superseded_lease_epoch_is_ignored(self) -> None:
        run_id, host_id = uuid7(), uuid7()
        lease = _Lease(run_id=run_id, host_id=host_id, lease_epoch=2)
        session = _Session(leases=[lease], commands=[])

        applied = await _repo(session).apply_checkpoint(
            host_id=host_id,
            checkpoint=_checkpoint(run_id, AgentHostRunState.RUNNING, epoch=1),
        )

        assert applied is None

    async def test_a_lease_that_is_gone_is_ignored(self) -> None:
        """Retention can collect a lease the host still remembers."""
        applied = await _repo(_Session(leases=[], commands=[])).apply_checkpoint(
            host_id=uuid7(),
            checkpoint=_checkpoint(uuid7(), AgentHostRunState.RUNNING),
        )

        assert applied is None

    async def test_a_lease_belonging_to_another_host_is_ignored(self) -> None:
        run_id = uuid7()
        session = _Session(leases=[_Lease(run_id=run_id, host_id=uuid7())], commands=[])

        applied = await _repo(session).apply_checkpoint(
            host_id=uuid7(),
            checkpoint=_checkpoint(run_id, AgentHostRunState.RUNNING),
        )

        assert applied is None

    async def test_a_real_advance_still_applies(self) -> None:
        """The tolerance must not have cost us the heartbeat itself."""
        run_id, host_id = uuid7(), uuid7()
        lease = _Lease(
            run_id=run_id,
            host_id=host_id,
            state=AgentHostRunState.LEASED.value,
        )
        session = _Session(leases=[lease], commands=[])

        applied = await _repo(session).apply_checkpoint(
            host_id=host_id,
            checkpoint=_checkpoint(run_id, AgentHostRunState.RUNNING),
        )

        assert applied is lease
        assert lease.state == AgentHostRunState.RUNNING.value
        assert lease.accepted_at is not None


class TestOneBadUpdateCannotStopTheWholePoll:
    async def test_commands_are_still_delivered_alongside_a_terminal_checkpoint(
        self,
    ) -> None:
        """The wedge: no CANCEL_RUN could reach this host until it restarted."""
        host_id = uuid7()
        wedged_run, live_run = uuid7(), uuid7()
        session = _Session(
            leases=[
                _Lease(
                    run_id=wedged_run,
                    host_id=host_id,
                    state=AgentHostRunState.DISPATCH_UNKNOWN.value,
                    accepted_at=_now(),
                ),
                _Lease(run_id=live_run, host_id=host_id),
            ],
            commands=[
                _Command(
                    id=uuid7(),
                    host_id=host_id,
                    run_id=live_run,
                    kind=AgentHostCommandKind.CANCEL_RUN.value,
                    lease_epoch=1,
                )
            ],
        )

        commands = await _repo(session).poll_commands(
            host_id=host_id,
            limit=16,
            acknowledged_command_ids=[],
            checkpoints=[_checkpoint(wedged_run, AgentHostRunState.RUNNING)],
            rejections=[],
            available_run_slots=1,
        )

        assert [command.kind for command in commands] == [
            AgentHostCommandKind.CANCEL_RUN
        ]

    async def test_an_acknowledgement_for_a_collected_command_is_ignored(self) -> None:
        """Retention outliving an in-flight ack must not become a 409 loop."""
        host_id = uuid7()
        session = _Session(leases=[], commands=[])

        commands = await _repo(session).poll_commands(
            host_id=host_id,
            limit=16,
            acknowledged_command_ids=[uuid7()],
            checkpoints=[],
            rejections=[],
            available_run_slots=1,
        )

        assert commands == []
