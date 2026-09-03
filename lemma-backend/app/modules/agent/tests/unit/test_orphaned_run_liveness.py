"""How long a conversation stays wedged behind a worker that was killed.

A run whose worker is SIGKILLed stays RUNNING, and RUNNING holds the
conversation's one active run slot: every message the person sends afterwards
is accepted, attached to the dead run, and answered by nobody. The only thing
that ever freed it was this sweep, and on age alone the sweep had to wait out
the entire agent-run task timeout -- four hours and ten minutes -- because
staleness cannot tell a dead worker from a legitimately long turn.

The job heartbeat can. These pin both halves of what that buys: a run whose job
stopped reporting is reclaimed two minutes in, and a run whose job is still
reporting is left alone however long it has been going.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fakeredis import aioredis as fake_aioredis

from app.core.infrastructure.jobs import job_liveness
from app.modules.agent.domain.run_projections import StaleAgentRunRef
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.events import handlers


class _UowFactory:
    def __init__(self) -> None:
        self.events: list[object] = []

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def collect_events(self, events: list[object]) -> None:
        self.events.extend(events)


@pytest.fixture
def sweep(monkeypatch: pytest.MonkeyPatch):
    """The cron, wired to a fake repository and a real Redis-shaped fake."""
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(job_liveness, "get_redis", lambda **_kwargs: redis)
    monkeypatch.setattr(handlers, "publish_conversation_event", _no_realtime)
    uow_factory = _UowFactory()
    monkeypatch.setattr(
        handlers,
        "streaq_worker",
        SimpleNamespace(context=SimpleNamespace(uow=lambda: uow_factory)),
    )
    return SimpleNamespace(redis=redis, monkeypatch=monkeypatch)


async def _no_realtime(conversation_id, payload) -> None:
    return None


def _install_repository(monkeypatch: pytest.MonkeyPatch, *, undecided, finished):
    """A repository whose only stale runs are the ones the wall clock cannot judge."""
    asked: dict[str, int] = {}

    class _Repo:
        def __init__(self, uow) -> None:
            self.uow = uow

        async def list_stale_active_runs(self, *, cutoff_seconds, limit=200):
            asked["backstop"] = cutoff_seconds
            return []

        async def list_active_runs_pending_liveness(
            self, *, cutoff_seconds, decided_after_seconds, limit=200
        ):
            asked["unresponsive"] = cutoff_seconds
            return list(undecided)

        async def list_runs_stuck_stopping(self, *, cutoff_seconds, limit=200):
            return []

        async def list_conversations_stranded_by_a_finished_run(
            self, *, cutoff_seconds, limit=200
        ):
            return []

        async def finish_agent_run(self, *, agent_run_id, status, error=None):
            finished.append((agent_run_id, status))
            return SimpleNamespace(updated=True, status=status)

        def collect_events(self, events: list[object]) -> None:
            self.uow.collect_events(events)

    monkeypatch.setattr(handlers, "ConversationRepository", _Repo)
    return asked


async def test_a_run_whose_worker_was_killed_is_reclaimed_in_minutes(sweep) -> None:
    """The four-hour wedge: reclaimed once the heartbeat lapses, not once the
    task timeout does."""
    conversation_id, run_id = uuid4(), uuid4()
    finished: list[tuple[object, AgentRunStatus]] = []
    asked = _install_repository(
        sweep.monkeypatch,
        undecided=[StaleAgentRunRef(id=run_id, conversation_id=conversation_id)],
        finished=finished,
    )
    job_id = handlers.agent_run_job_id(run_id)
    await job_liveness.publish_job_liveness(sweep.redis, job_id, mark_seen=True)
    # The worker is killed here: nothing renews the key, and it lapses.
    await sweep.redis.pexpire(job_liveness.job_alive_key(job_id), 1)
    await asyncio.sleep(0.05)

    await handlers.reconcile_orphaned_agent_runs()

    assert finished == [(run_id, AgentRunStatus.FAILED)]
    # ...and it did not have to be four hours old to qualify.
    assert asked["unresponsive"] == handlers._UNRESPONSIVE_RUN_CUTOFF_SECONDS
    assert asked["unresponsive"] < handlers._ORPHANED_RUN_CUTOFF_SECONDS


async def test_a_run_still_reporting_is_left_alone_however_long_it_has_run(
    sweep,
) -> None:
    """The reason the cutoff could not simply be shortened. A run executing one
    long shell command writes nothing for an hour and is perfectly healthy."""
    conversation_id, run_id = uuid4(), uuid4()
    finished: list[tuple[object, AgentRunStatus]] = []
    _install_repository(
        sweep.monkeypatch,
        undecided=[StaleAgentRunRef(id=run_id, conversation_id=conversation_id)],
        finished=finished,
    )
    await job_liveness.publish_job_liveness(
        sweep.redis, handlers.agent_run_job_id(run_id), mark_seen=True
    )

    await handlers.reconcile_orphaned_agent_runs()

    assert finished == []


async def test_a_run_whose_job_never_reported_waits_for_the_wall_clock(
    sweep,
) -> None:
    """A job queued behind a backlog has not started, so it has no heartbeat.

    Reading that as death would fail a run before its worker ever picked it up.
    It stays for the backstop, which is where a run from before the heartbeat
    shipped is collected too.
    """
    conversation_id, run_id = uuid4(), uuid4()
    finished: list[tuple[object, AgentRunStatus]] = []
    _install_repository(
        sweep.monkeypatch,
        undecided=[StaleAgentRunRef(id=run_id, conversation_id=conversation_id)],
        finished=finished,
    )

    await handlers.reconcile_orphaned_agent_runs()

    assert finished == []
