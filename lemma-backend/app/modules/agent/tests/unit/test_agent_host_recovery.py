"""Lease recovery and retention sweeps for Agent Host dispatch.

``agent_host_recovery`` is a background-sweep module: nothing HTTP-reachable
calls it directly, so it has no e2e coverage. It is exercised here with fake
sessions that mimic exactly the query shapes each function issues (``get``,
``execute``, ``scalar``, ``add``, ``flush``), following the pattern in
``test_orphaned_agent_run_repository.py``: canned rows/results drive the
function's own branching logic, and the constructed statements are compiled
to SQL text to check the filters that a canned fake cannot otherwise verify.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostRunState,
)
from app.modules.agent.infrastructure import agent_host_recovery
from app.modules.agent.infrastructure.agent_host_repository_common import (
    DEFAULT_COMMAND_TTL_SECONDS,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
)


def _compile(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _lease(
    *,
    run_id: UUID | None = None,
    host_id: UUID | None = None,
    state: AgentHostRunState = AgentHostRunState.QUEUED_FOR_HOST,
    accepted_at: datetime | None = None,
    lease_expires_at: datetime,
    lease_epoch: int = 1,
    terminal_at: datetime | None = None,
) -> AgentHostRunLeaseModel:
    return AgentHostRunLeaseModel(
        run_id=run_id or uuid4(),
        host_id=host_id or uuid4(),
        harness_id=uuid4(),
        lease_epoch=lease_epoch,
        state=state.value,
        accepted_at=accepted_at,
        lease_expires_at=lease_expires_at,
        error_code=None,
        error_detail=None,
        terminal_at=terminal_at,
        created_at=lease_expires_at,
        updated_at=lease_expires_at,
    )


def _command(
    *,
    run_id: UUID,
    kind: AgentHostCommandKind = AgentHostCommandKind.START_RUN,
    state: AgentHostCommandState = AgentHostCommandState.QUEUED,
) -> AgentHostCommandModel:
    return AgentHostCommandModel(
        host_id=uuid4(),
        run_id=run_id,
        kind=kind.value,
        lease_epoch=1,
        payload={},
        state=state.value,
        expires_at=datetime.now(timezone.utc),
    )


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------- expire_unaccepted_run


class _CommandScalars:
    def __init__(self, commands: list[AgentHostCommandModel]) -> None:
        self._commands = commands

    def scalars(self):
        return self._commands


class _ExpireSession:
    def __init__(
        self,
        lease: AgentHostRunLeaseModel | None,
        commands: list[AgentHostCommandModel] | None = None,
    ) -> None:
        self.lease = lease
        self.commands = commands or []
        self.get_calls: list[tuple] = []
        self.execute_calls: list = []
        self.flushed = False

    async def get(self, model, pk, *, with_for_update=False):
        self.get_calls.append((model, pk, with_for_update))
        return self.lease

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        return _CommandScalars(self.commands)

    async def flush(self):
        self.flushed = True


@pytest.mark.asyncio
async def test_expire_unaccepted_run_returns_none_when_lease_missing() -> None:
    session = _ExpireSession(lease=None)

    result = await agent_host_recovery.expire_unaccepted_run(
        session, run_id=uuid4(), now=NOW
    )

    assert result is None
    assert session.flushed is False


@pytest.mark.asyncio
async def test_expire_unaccepted_run_returns_none_when_already_accepted() -> None:
    lease = _lease(
        state=AgentHostRunState.LEASED,
        accepted_at=NOW - timedelta(seconds=5),
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    session = _ExpireSession(lease=lease)

    result = await agent_host_recovery.expire_unaccepted_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert result is None
    assert session.flushed is False


@pytest.mark.asyncio
async def test_expire_unaccepted_run_returns_none_when_not_yet_expired() -> None:
    lease = _lease(
        state=AgentHostRunState.QUEUED_FOR_HOST,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session = _ExpireSession(lease=lease)

    result = await agent_host_recovery.expire_unaccepted_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert result is None
    assert session.flushed is False


@pytest.mark.asyncio
async def test_expire_unaccepted_run_returns_none_for_non_pre_dispatch_state() -> None:
    lease = _lease(
        state=AgentHostRunState.RUNNING,
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    session = _ExpireSession(lease=lease)

    result = await agent_host_recovery.expire_unaccepted_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert result is None
    assert session.flushed is False


@pytest.mark.asyncio
async def test_expire_unaccepted_run_times_out_queued_for_host() -> None:
    lease = _lease(
        state=AgentHostRunState.QUEUED_FOR_HOST,
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    stale_command = _command(
        run_id=lease.run_id,
        kind=AgentHostCommandKind.START_RUN,
        state=AgentHostCommandState.DELIVERED,
    )
    session = _ExpireSession(lease=lease, commands=[stale_command])

    result = await agent_host_recovery.expire_unaccepted_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert result is AgentHostRunState.FAILED
    assert lease.state == AgentHostRunState.FAILED.value
    assert lease.error_code == "HOST_WAIT_TIMEOUT"
    assert lease.error_detail
    assert lease.terminal_at == NOW
    assert lease.lease_expires_at == NOW
    assert lease.updated_at == NOW
    assert stale_command.state == AgentHostCommandState.CANCELLED.value
    assert session.flushed is True
    # session.get was made with a row lock, matching the sibling repository.
    assert session.get_calls == [(AgentHostRunLeaseModel, lease.run_id, True)]


@pytest.mark.asyncio
async def test_expire_unaccepted_run_times_out_leased_as_dispatch_unknown() -> None:
    lease = _lease(
        state=AgentHostRunState.LEASED,
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    session = _ExpireSession(lease=lease, commands=[])

    result = await agent_host_recovery.expire_unaccepted_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert result is AgentHostRunState.DISPATCH_UNKNOWN
    assert lease.state == AgentHostRunState.DISPATCH_UNKNOWN.value
    assert lease.error_code == "HOST_ACCEPTANCE_UNKNOWN"
    assert session.flushed is True


@pytest.mark.asyncio
async def test_expire_unaccepted_run_command_query_targets_start_run_in_flight() -> (
    None
):
    """The command sweep must be scoped to this run's START_RUN, not any command."""
    lease = _lease(
        state=AgentHostRunState.QUEUED_FOR_HOST,
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    session = _ExpireSession(lease=lease, commands=[])

    await agent_host_recovery.expire_unaccepted_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert len(session.execute_calls) == 1
    sql = _compile(session.execute_calls[0])
    assert f"agent_host_commands.run_id = '{lease.run_id}'" in sql
    assert "agent_host_commands.kind = 'START_RUN'" in sql
    assert "'QUEUED'" in sql
    assert "'DELIVERED'" in sql
    assert "FOR UPDATE" in sql


# --------------------------------------------------------------------- reconcile_expired_run


class _ReconcileRunSession:
    def __init__(self, lease: AgentHostRunLeaseModel | None) -> None:
        self.lease = lease
        self.flushed = False

    async def get(self, model, pk, *, with_for_update=False):
        return self.lease

    async def flush(self):
        self.flushed = True


@pytest.mark.asyncio
async def test_reconcile_expired_run_returns_none_when_lease_missing() -> None:
    session = _ReconcileRunSession(lease=None)

    result = await agent_host_recovery.reconcile_expired_run(
        session, run_id=uuid4(), now=NOW
    )

    assert result is None
    assert session.flushed is False


@pytest.mark.asyncio
async def test_reconcile_expired_run_leaves_unexpired_lease_untouched() -> None:
    lease = _lease(
        state=AgentHostRunState.RUNNING,
        accepted_at=NOW - timedelta(seconds=5),
        lease_expires_at=NOW + timedelta(seconds=5),
    )
    session = _ReconcileRunSession(lease=lease)

    result = await agent_host_recovery.reconcile_expired_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert result is lease
    assert lease.state == AgentHostRunState.RUNNING.value
    assert session.flushed is False


@pytest.mark.asyncio
async def test_reconcile_expired_run_leaves_terminal_lease_untouched() -> None:
    lease = _lease(
        state=AgentHostRunState.FAILED,
        accepted_at=NOW - timedelta(seconds=5),
        lease_expires_at=NOW - timedelta(seconds=1),
        terminal_at=NOW - timedelta(seconds=1),
    )
    session = _ReconcileRunSession(lease=lease)

    result = await agent_host_recovery.reconcile_expired_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert result is lease
    assert lease.state == AgentHostRunState.FAILED.value
    assert session.flushed is False


@pytest.mark.asyncio
async def test_reconcile_expired_run_leaves_unaccepted_lease_untouched() -> None:
    """expire_unaccepted_run owns the pre-accept path; this one only advances accepted runs."""
    lease = _lease(
        state=AgentHostRunState.LEASED,
        accepted_at=None,
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    session = _ReconcileRunSession(lease=lease)

    result = await agent_host_recovery.reconcile_expired_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert result is lease
    assert lease.state == AgentHostRunState.LEASED.value
    assert session.flushed is False


@pytest.mark.asyncio
async def test_reconcile_expired_run_moves_running_lease_into_recovering() -> None:
    lease = _lease(
        state=AgentHostRunState.RUNNING,
        accepted_at=NOW - timedelta(seconds=30),
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    session = _ReconcileRunSession(lease=lease)

    result = await agent_host_recovery.reconcile_expired_run(
        session,
        run_id=lease.run_id,
        now=NOW,
        recovery_grace_seconds=45,
    )

    assert result is lease
    assert lease.state == AgentHostRunState.RECOVERING.value
    assert lease.error_code == "HOST_RECOVERING"
    assert lease.lease_expires_at == NOW + timedelta(seconds=45)
    assert lease.terminal_at is None
    assert lease.updated_at == NOW
    assert session.flushed is True


@pytest.mark.asyncio
async def test_reconcile_expired_run_moves_recovering_lease_into_dispatch_unknown() -> (
    None
):
    lease = _lease(
        state=AgentHostRunState.RECOVERING,
        accepted_at=NOW - timedelta(seconds=200),
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    session = _ReconcileRunSession(lease=lease)

    result = await agent_host_recovery.reconcile_expired_run(
        session, run_id=lease.run_id, now=NOW
    )

    assert result is lease
    assert lease.state == AgentHostRunState.DISPATCH_UNKNOWN.value
    assert lease.error_code == "HOST_LEASE_EXPIRED"
    assert lease.terminal_at == NOW
    assert lease.lease_expires_at == NOW
    assert session.flushed is True


# --------------------------------------------------------------------- cancel_already_queued


class _ScalarSession:
    def __init__(self, value) -> None:
        self.value = value
        self.scalar_calls: list = []

    async def scalar(self, stmt):
        self.scalar_calls.append(stmt)
        return self.value


@pytest.mark.asyncio
async def test_cancel_already_queued_true_when_a_live_cancel_exists() -> None:
    session = _ScalarSession(True)

    result = await agent_host_recovery.cancel_already_queued(
        session, run_id=uuid4(), lease_epoch=3
    )

    assert result is True


@pytest.mark.asyncio
async def test_cancel_already_queued_false_when_none_exists() -> None:
    session = _ScalarSession(None)

    result = await agent_host_recovery.cancel_already_queued(
        session, run_id=uuid4(), lease_epoch=3
    )

    assert result is False


@pytest.mark.asyncio
async def test_cancel_already_queued_is_fenced_on_run_and_lease_epoch() -> None:
    run_id = uuid4()
    session = _ScalarSession(False)

    await agent_host_recovery.cancel_already_queued(
        session, run_id=run_id, lease_epoch=7
    )

    assert len(session.scalar_calls) == 1
    sql = _compile(session.scalar_calls[0])
    assert f"agent_host_commands.run_id = '{run_id}'" in sql
    assert "agent_host_commands.lease_epoch = 7" in sql
    assert "agent_host_commands.kind = 'CANCEL_RUN'" in sql
    for state in ("QUEUED", "DELIVERED", "ACKNOWLEDGED"):
        assert f"'{state}'" in sql


# --------------------------------------------------------------------- cancel_abandoned_host_runs


class _LeaseScalars:
    def __init__(self, leases: list[AgentHostRunLeaseModel]) -> None:
        self._leases = leases

    def scalars(self):
        return self._leases


class _AbandonedSession:
    def __init__(self, leases: list[AgentHostRunLeaseModel]) -> None:
        self.leases = leases
        self.added: list = []
        self.execute_calls: list = []
        self.flushed = False

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        return _LeaseScalars(self.leases)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True


@pytest.mark.asyncio
async def test_cancel_abandoned_host_runs_with_no_matches_returns_empty() -> None:
    session = _AbandonedSession(leases=[])

    result = await agent_host_recovery.cancel_abandoned_host_runs(session, now=NOW)

    assert result == []
    assert session.added == []
    assert session.flushed is True


@pytest.mark.asyncio
async def test_cancel_abandoned_host_runs_queues_one_cancel_per_orphaned_lease() -> (
    None
):
    lease_a = _lease(
        state=AgentHostRunState.RUNNING, lease_expires_at=NOW, lease_epoch=4
    )
    lease_b = _lease(
        state=AgentHostRunState.DISPATCHING, lease_expires_at=NOW, lease_epoch=9
    )
    session = _AbandonedSession(leases=[lease_a, lease_b])

    result = await agent_host_recovery.cancel_abandoned_host_runs(session, now=NOW)

    assert result == [lease_a.host_id, lease_b.host_id]
    assert len(session.added) == 2
    for lease, command in zip((lease_a, lease_b), session.added, strict=True):
        assert isinstance(command, AgentHostCommandModel)
        assert command.host_id == lease.host_id
        assert command.run_id == lease.run_id
        assert command.kind == AgentHostCommandKind.CANCEL_RUN.value
        assert command.lease_epoch == lease.lease_epoch
        assert command.payload == {"agent_run_id": str(lease.run_id)}
        assert command.state == AgentHostCommandState.QUEUED.value
        assert command.expires_at == NOW + timedelta(
            seconds=DEFAULT_COMMAND_TTL_SECONDS
        )
    assert session.flushed is True


@pytest.mark.asyncio
async def test_cancel_abandoned_host_runs_does_not_dedupe_repeated_hosts() -> None:
    """Dedup is the cron caller's job (dict.fromkeys before poking); this
    function reports one host id per orphaned lease, even if a host owns two.
    """
    host_id = uuid4()
    lease_a = _lease(host_id=host_id, lease_expires_at=NOW)
    lease_b = _lease(host_id=host_id, lease_expires_at=NOW)
    session = _AbandonedSession(leases=[lease_a, lease_b])

    result = await agent_host_recovery.cancel_abandoned_host_runs(session, now=NOW)

    assert result == [host_id, host_id]


@pytest.mark.asyncio
async def test_cancel_abandoned_host_runs_query_shape() -> None:
    session = _AbandonedSession(leases=[])

    await agent_host_recovery.cancel_abandoned_host_runs(session, now=NOW, limit=17)

    assert len(session.execute_calls) == 1
    sql = _compile(session.execute_calls[0])
    assert "agent_host_run_leases" in sql
    assert "agent_runs" in sql
    assert "NOT (EXISTS" in sql or "NOT EXISTS" in sql
    assert "LIMIT 17" in sql
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql


# --------------------------------------------------------------------- reconcile_expired_leases


class _RunIdScalars:
    def __init__(self, run_ids: list[UUID]) -> None:
        self._run_ids = run_ids

    def scalars(self):
        return self._run_ids


class _ReconcileLeasesSession:
    def __init__(self, run_ids: list[UUID]) -> None:
        self.run_ids = run_ids
        self.execute_calls: list = []

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        return _RunIdScalars(self.run_ids)


@pytest.mark.asyncio
async def test_reconcile_expired_leases_returns_zero_with_no_expired_leases() -> None:
    session = _ReconcileLeasesSession(run_ids=[])

    result = await agent_host_recovery.reconcile_expired_leases(session, now=NOW)

    assert result == 0


@pytest.mark.asyncio
async def test_reconcile_expired_leases_sweeps_expire_and_reconcile_per_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_ids = [uuid4(), uuid4(), uuid4()]
    session = _ReconcileLeasesSession(run_ids=run_ids)
    expire_calls: list[tuple] = []
    reconcile_calls: list[tuple] = []

    async def fake_expire(session_arg, *, run_id, now=None):
        assert session_arg is session
        expire_calls.append((run_id, now))
        return None

    async def fake_reconcile(
        session_arg, *, run_id, now=None, recovery_grace_seconds=120
    ):
        assert session_arg is session
        reconcile_calls.append((run_id, now))
        return None

    monkeypatch.setattr(agent_host_recovery, "expire_unaccepted_run", fake_expire)
    monkeypatch.setattr(agent_host_recovery, "reconcile_expired_run", fake_reconcile)

    result = await agent_host_recovery.reconcile_expired_leases(
        session, now=NOW, limit=50
    )

    assert result == 3
    assert [run_id for run_id, _ in expire_calls] == run_ids
    assert [run_id for run_id, _ in reconcile_calls] == run_ids
    assert all(now == NOW for _, now in expire_calls)
    assert all(now == NOW for _, now in reconcile_calls)


@pytest.mark.asyncio
async def test_reconcile_expired_leases_query_shape() -> None:
    session = _ReconcileLeasesSession(run_ids=[])

    await agent_host_recovery.reconcile_expired_leases(session, now=NOW, limit=64)

    assert len(session.execute_calls) == 1
    sql = _compile(session.execute_calls[0])
    assert "agent_host_run_leases.lease_expires_at <" in sql
    assert "LIMIT 64" in sql


# --------------------------------------------------------------------- cleanup_retained_state


class _CleanupSession:
    def __init__(self) -> None:
        self.execute_calls: list = []
        self.flushed = False

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        return None

    async def flush(self):
        self.flushed = True


@pytest.mark.asyncio
async def test_cleanup_retained_state_issues_three_scoped_deletes_and_flushes() -> None:
    session = _CleanupSession()

    await agent_host_recovery.cleanup_retained_state(session, now=NOW)

    assert len(session.execute_calls) == 3
    assert session.flushed is True

    pairing_sql = _compile(session.execute_calls[0])
    assert "DELETE FROM agent_host_pairings" in pairing_sql
    assert str(NOW - timedelta(hours=24)) in pairing_sql

    commands_sql = _compile(session.execute_calls[1])
    assert "DELETE FROM agent_host_commands" in commands_sql
    assert "agent_host_run_leases" in commands_sql

    leases_sql = _compile(session.execute_calls[2])
    assert "DELETE FROM agent_host_run_leases" in leases_sql
    assert "terminal_at IS NOT NULL" in leases_sql
    assert str(NOW - timedelta(days=30)) in leases_sql


@pytest.mark.asyncio
async def test_cleanup_retained_state_defaults_now_when_omitted() -> None:
    session = _CleanupSession()

    # Should not raise, and should still run all three deletes using utcnow().
    await agent_host_recovery.cleanup_retained_state(session)

    assert len(session.execute_calls) == 3
    assert session.flushed is True
