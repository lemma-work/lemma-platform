"""Failing the run a wait belongs to, when a resume got to the run first."""

from uuid import uuid4

import pytest

from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.execution.wait_failure import fail_run_for_wait

pytestmark = pytest.mark.asyncio


class _Runs:
    def __init__(self, run: WorkflowRunEntity) -> None:
        self.run = run
        self.updated: list[WorkflowRunEntity] = []

    async def get_for_update(self, run_id):
        return self.run if run_id == self.run.id else None

    async def update(self, run: WorkflowRunEntity) -> WorkflowRunEntity:
        self.updated.append(run)
        return run


class _Waits:
    """What the database says once the run's lock is held."""

    def __init__(self, active: WorkflowRunWaitEntity | None) -> None:
        self.active = active
        self.updated: list[WorkflowRunWaitEntity] = []

    async def get_active_for_run(self, run_id):
        return self.active

    async def update(self, wait: WorkflowRunWaitEntity) -> WorkflowRunWaitEntity:
        self.updated.append(wait)
        return wait


class _UnitOfWork:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Engine:
    def __init__(self, runs: _Runs, waits: _Waits) -> None:
        self.run_repo = runs
        self.wait_repo = waits
        self.uow = _UnitOfWork()
        self.terminal: list[WorkflowRunEntity] = []
        self.announced: list[WorkflowRunEntity] = []

    def _collect_terminal_event(self, run: WorkflowRunEntity) -> None:
        self.terminal.append(run)

    async def _announce(self, run: WorkflowRunEntity) -> None:
        self.announced.append(run)


def _live_run() -> WorkflowRunEntity:
    run = WorkflowRunEntity(flow_id=uuid4(), pod_id=uuid4(), user_id=uuid4())
    run.status = WorkflowRunStatus.RUNNING
    return run


def _wait_for(run: WorkflowRunEntity, node_id: str) -> WorkflowRunWaitEntity:
    return WorkflowRunWaitEntity(
        run_id=run.id,
        flow_id=run.flow_id,
        pod_id=run.pod_id,
        node_id=node_id,
        wait_type=WorkflowRunWaitType.DECISION,
        external_ref=str(uuid4()),
    )


async def test_a_wait_resumed_before_the_lock_does_not_fail_the_run() -> None:
    # Read as active, then a resume took the run's lock first, completed this
    # wait and moved the run on to its next step, which now waits on its own.
    run = _live_run()
    stale = _wait_for(run, "triage")
    next_step = _wait_for(run, "notify")
    engine = _Engine(_Runs(run), _Waits(active=next_step))

    failed = await fail_run_for_wait(engine, stale, error="unanswered")  # type: ignore[arg-type]

    assert failed is None
    assert run.status == WorkflowRunStatus.RUNNING
    assert engine.wait_repo.updated == []
    assert engine.run_repo.updated == []
    assert engine.announced == []


async def test_a_wait_completed_with_nothing_after_it_does_not_fail_the_run() -> None:
    run = _live_run()
    stale = _wait_for(run, "triage")
    engine = _Engine(_Runs(run), _Waits(active=None))

    assert await fail_run_for_wait(engine, stale, error="unanswered") is None  # type: ignore[arg-type]
    assert run.status == WorkflowRunStatus.RUNNING


async def test_the_run_fails_when_the_wait_is_still_its_active_one() -> None:
    run = _live_run()
    wait = _wait_for(run, "triage")
    engine = _Engine(_Runs(run), _Waits(active=wait))

    failed = await fail_run_for_wait(engine, wait, error="unanswered")  # type: ignore[arg-type]

    assert failed is run
    assert run.status == WorkflowRunStatus.FAILED
    assert engine.wait_repo.updated == [wait]
    assert engine.uow.commits == 1
    assert engine.announced == [run]
