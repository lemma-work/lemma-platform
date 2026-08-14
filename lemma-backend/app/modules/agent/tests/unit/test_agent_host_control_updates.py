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
    AgentHostCommandRejection,
    AgentHostCommandState,
    AgentHostRejectionCode,
    AgentHostRunCheckpoint,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host_control_updates import (
    apply_rejection,
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


@dataclass
class _Harness:
    id: UUID
    host_id: UUID
    harness_key: str = "claude-code"
    display_name: str = "Claude Code"
    health: str = "READY"
    config_revision: str = "rev-2"
    config_options: list = field(default_factory=list)


class _Session:
    """Stands in for the session, serving leases by id and commands by query.

    Deliberately dumb: the point of these tests is which rows the repository
    decides to touch, not how SQLAlchemy renders the query.
    """

    def __init__(
        self,
        *,
        leases: list[_Lease],
        commands: list[_Command],
        harnesses: list[_Harness] | None = None,
    ) -> None:
        self.leases = {lease.run_id: lease for lease in leases}
        self.commands = commands
        self.harnesses = {harness.id: harness for harness in harnesses or []}
        self.flushes = 0

    async def get(self, model, pk, with_for_update: bool = False):
        if model.__name__ == "AgentHostRunLeaseModel":
            return self.leases.get(pk)
        if model.__name__ == "AgentHostHarnessModel":
            return self.harnesses.get(pk)
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


class TestAStaleRevisionIsAnsweredRatherThanRecorded:
    """A re-published harness must not cost the run that was already queued.

    The host re-probes on a 15-minute timer, whenever the coding agent updates
    itself, and again after any publish that failed during a backend restart.
    Each of those mints a new `config_revision`, and a START_RUN already in
    flight names the old one. Before this, that was a permanent failure with a
    sentence nobody could act on: "harness configuration revision changed".

    Note what these assert about the *code* rather than the flag: the host
    still reports `retryable=False`, honestly, because it cannot run the
    command as given. Lemma is the side holding the revision that would work.
    """

    @staticmethod
    def _stale(
        *,
        harness_health: str = "READY",
        harness_revision: str = "rev-2",
        config_options: list | None = None,
        selections: dict | None = None,
        model_name: str | None = None,
        remint_attempts: int | None = None,
    ) -> tuple[_Session, _Command, _Lease, AgentHostCommandRejection, UUID]:
        host_id = uuid7()
        run_id = uuid7()
        harness_id = uuid7()
        lease = _Lease(
            run_id=run_id,
            host_id=host_id,
            state=AgentHostRunState.LEASED.value,
        )
        command = _Command(
            id=uuid7(),
            host_id=host_id,
            run_id=run_id,
            kind=AgentHostCommandKind.START_RUN.value,
            lease_epoch=1,
            state=AgentHostCommandState.DELIVERED.value,
            payload={
                "harness_id": str(harness_id),
                "profile_revision": "rev-1",
                "config_selections": selections or {},
                "model_name": model_name,
            },
        )
        if remint_attempts is not None:
            command.rejection = {"remint_attempts": remint_attempts}
        harness = _Harness(
            id=harness_id,
            host_id=host_id,
            health=harness_health,
            config_revision=harness_revision,
            config_options=config_options or [],
        )
        session = _Session(
            leases=[lease], commands=[command], harnesses=[harness]
        )
        rejection = AgentHostCommandRejection(
            command_id=command.id,
            run_id=run_id,
            lease_epoch=1,
            code=AgentHostRejectionCode.CONFIG_REVISION_STALE,
            retryable=False,
            detail="harness configuration revision changed",
        )
        return session, command, lease, rejection, host_id

    async def test_the_command_is_re_aimed_at_the_revision_that_exists_now(
        self,
    ) -> None:
        session, command, lease, rejection, host_id = self._stale()

        changed = await apply_rejection(
            session, host_id=host_id, rejection=rejection
        )

        assert changed
        assert command.payload["profile_revision"] == "rev-2"
        assert command.state == AgentHostCommandState.QUEUED.value
        assert command.delivered_at is None
        assert lease.state == AgentHostRunState.QUEUED_FOR_HOST.value
        assert lease.terminal_at is None

    async def test_a_second_re_aim_is_the_last_one(self) -> None:
        """Bounded inside the rejection blob, because nothing else counts.

        The poll hands back whatever is QUEUED, verbatim, and the command row
        has no attempt column. Without this the requeue is a 1s spin until the
        command's five-minute TTL.
        """
        session, command, lease, rejection, host_id = self._stale(
            remint_attempts=2
        )

        await apply_rejection(session, host_id=host_id, rejection=rejection)

        assert command.state == AgentHostCommandState.ACKNOWLEDGED.value
        assert lease.state == AgentHostRunState.FAILED.value
        assert "kept changing" in (lease.error_detail or "")

    async def test_a_selection_the_new_options_dropped_is_left_behind(
        self,
    ) -> None:
        """The harness is the authority on its own options.

        A value it stopped offering is news about the agent, not a mistake by
        the person who saved it, so it is dropped and the harness applies its
        own default rather than the run failing over it.
        """
        session, command, _lease, rejection, host_id = self._stale(
            config_options=[
                {
                    "id": "verbosity",
                    "category": "verbosity",
                    "options": [{"value": "low"}],
                }
            ],
            selections={"verbosity": "high", "gone": "x"},
        )

        await apply_rejection(session, host_id=host_id, rejection=rejection)

        assert command.payload["config_selections"] == {}
        assert command.state == AgentHostCommandState.QUEUED.value

    async def test_a_model_the_harness_no_longer_offers_is_cleared_not_fatal(
        self,
    ) -> None:
        session, command, _lease, rejection, host_id = self._stale(
            config_options=[
                {
                    "id": "model",
                    "category": "model",
                    "options": [{"value": "sonnet"}],
                }
            ],
            model_name="opus[1m]",
        )

        await apply_rejection(session, host_id=host_id, rejection=rejection)

        assert command.payload["model_name"] is None
        assert command.state == AgentHostCommandState.QUEUED.value

    async def test_an_escalating_policy_value_still_fails_the_run(self) -> None:
        """The one carried-over value that must not survive a re-aim.

        Harnesses enumerate their own permission modes, so `bypassPermissions`
        is a legal member of the option's list; the deny-list is what stops a
        stored profile turning off the approval gate. "The harness changed" is
        not a reason to stop enforcing that.
        """
        session, command, lease, rejection, host_id = self._stale(
            config_options=[
                {
                    "id": "permission_mode",
                    "category": "mode",
                    "options": [{"value": "default"}, {"value": "bypassPermissions"}],
                },
                {
                    "id": "approval",
                    "category": "approval",
                    "options": [{"value": "ask"}, {"value": "bypassPermissions"}],
                },
            ],
            selections={"approval": "bypassPermissions"},
        )

        await apply_rejection(session, host_id=host_id, rejection=rejection)

        assert command.state == AgentHostCommandState.ACKNOWLEDGED.value
        assert lease.state == AgentHostRunState.FAILED.value

    async def test_an_unready_harness_keeps_its_own_reason(self) -> None:
        """Re-aiming at a harness that cannot take work loses the sentence.

        AUTH_REQUIRED is the one a user can act on, so it must reach them
        rather than being replaced by a second revision mismatch later.
        """
        session, command, lease, rejection, host_id = self._stale(
            harness_health="AUTH_REQUIRED"
        )

        await apply_rejection(session, host_id=host_id, rejection=rejection)

        assert command.state == AgentHostCommandState.ACKNOWLEDGED.value
        assert "AUTH_REQUIRED" in (lease.error_detail or "")

    async def test_agreeing_revisions_are_not_requeued(self) -> None:
        """The spin this exists to avoid, in its purest form.

        If the revision we hold is the one the host just refused, re-sending it
        is guaranteed to be refused again.
        """
        session, command, lease, rejection, host_id = self._stale(
            harness_revision="rev-1"
        )

        await apply_rejection(session, host_id=host_id, rejection=rejection)

        assert command.state == AgentHostCommandState.ACKNOWLEDGED.value
        assert lease.state == AgentHostRunState.FAILED.value

    async def test_other_rejection_codes_are_untouched(self) -> None:
        """Only the stale revision is answered; everything else is recorded."""
        session, command, lease, rejection, host_id = self._stale()
        rejection = rejection.model_copy(
            update={"code": AgentHostRejectionCode.ADAPTER_UNAVAILABLE}
        )

        await apply_rejection(session, host_id=host_id, rejection=rejection)

        assert command.state == AgentHostCommandState.ACKNOWLEDGED.value
        assert command.payload["profile_revision"] == "rev-1"
        assert lease.error_code == "ADAPTER_UNAVAILABLE"
