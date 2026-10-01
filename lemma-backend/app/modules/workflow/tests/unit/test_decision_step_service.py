"""The decision job: read the wait, ask with no session open, settle the run.

The decisions port is faked here, which is the point of it being a port: what
the job owes the run -- one unit of work to read, none while asking, another to
settle, and a failed run only for a refusal -- does not depend on which rung
answers.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.test_support.fakes import FakeUnitOfWork
from app.modules.workflow.domain.decision_step import DecisionAsk, DecisionOutcome
from app.modules.workflow.domain.errors import DecisionStepError
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.services.decision_step_service import (
    DecisionStepService,
    PendingDecision,
)
from app.modules.workflow.services.run_resume_service import RunResumeService

pytestmark = pytest.mark.asyncio

WAIT_REF = "workflow:run:triage:0"


class _Units:
    """A unit-of-work factory that counts what it has open."""

    def __init__(self) -> None:
        self.open = 0
        self.opened = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeUnitOfWork]:
        self.open += 1
        self.opened += 1
        try:
            yield FakeUnitOfWork()
        finally:
            self.open -= 1


class _Decisions:
    """`DecisionPort`, answering from a script."""

    def __init__(
        self,
        units: _Units,
        *,
        outcome: DecisionOutcome | None = None,
        failure: Exception | None = None,
    ) -> None:
        self._units = units
        self._outcome = outcome or DecisionOutcome(choice="act", decision_id=uuid4())
        self._failure = failure
        self.asked: list[tuple[DecisionAsk, UUID, UUID]] = []
        self.open_while_asking: list[int] = []
        self.requested: list[str] = []

    async def request(self, wait_ref: str) -> None:
        self.requested.append(wait_ref)

    async def decide(
        self, ask: DecisionAsk, *, user_id: UUID, pod_id: UUID
    ) -> DecisionOutcome:
        self.asked.append((ask, user_id, pod_id))
        self.open_while_asking.append(self._units.open)
        if self._failure is not None:
            raise self._failure
        return self._outcome


class _Resumer:
    """`DecisionResumer`, recording how the run was settled."""

    def __init__(self, pending: PendingDecision | None) -> None:
        self._pending = pending
        self.settled: list[tuple[str, str, object]] = []

    async def pending_decision(self, wait_ref: str) -> PendingDecision | None:
        return self._pending

    async def resume_for_decision(
        self, wait_ref: str, outcome: DecisionOutcome
    ) -> bool:
        self.settled.append(("resumed", wait_ref, outcome))
        return True

    async def fail_for_decision(self, wait_ref: str, error: str) -> bool:
        self.settled.append(("failed", wait_ref, error))
        return True


def _pending(decisions: _Decisions) -> PendingDecision:
    return PendingDecision(
        ask=DecisionAsk(subject=WAIT_REF, state={"text": "hello"}, decider="triage"),
        user_id=uuid4(),
        pod_id=uuid4(),
        decisions=decisions,
    )


def _service(units: _Units, resumer: _Resumer) -> DecisionStepService:
    return DecisionStepService(uow_factory=units, resumer_for=lambda uow: resumer)


async def test_the_question_is_asked_with_no_unit_of_work_open():
    units = _Units()
    decisions = _Decisions(units)
    pending = _pending(decisions)
    resumer = _Resumer(pending)

    assert await _service(units, resumer).answer(WAIT_REF) is True

    assert decisions.asked == [(pending.ask, pending.user_id, pending.pod_id)]
    assert decisions.open_while_asking == [0]
    # One to read the wait, one to resume the run.
    assert units.opened == 2
    assert resumer.settled == [("resumed", WAIT_REF, decisions._outcome)]


async def test_nothing_waiting_asks_nothing():
    units = _Units()
    resumer = _Resumer(None)

    assert await _service(units, resumer).answer(WAIT_REF) is False
    assert resumer.settled == []
    assert units.opened == 1


async def test_a_refused_question_fails_the_run_with_the_reason():
    units = _Units()
    decisions = _Decisions(
        units, failure=DecisionStepError("No decider named 'triage' in this pod.")
    )
    resumer = _Resumer(_pending(decisions))

    assert await _service(units, resumer).answer(WAIT_REF) is True

    assert resumer.settled == [
        ("failed", WAIT_REF, "No decider named 'triage' in this pod.")
    ]


async def test_an_unexpected_failure_is_left_for_the_job_to_retry():
    # Only a refusal is the run's fault. Anything else -- a database blip -- is
    # raised to the queue, which retries, and the run stays waiting.
    units = _Units()
    decisions = _Decisions(units, failure=ConnectionError("database went away"))
    resumer = _Resumer(_pending(decisions))

    with pytest.raises(ConnectionError):
        await _service(units, resumer).answer(WAIT_REF)
    assert resumer.settled == []
    assert units.open == 0


# -- the resume service's half -------------------------------------------------


def _wait(created_at: datetime | None = None) -> WorkflowRunWaitEntity:
    return WorkflowRunWaitEntity(
        run_id=uuid4(),
        flow_id=uuid4(),
        pod_id=uuid4(),
        node_id="triage",
        wait_type=WorkflowRunWaitType.DECISION,
        external_ref=WAIT_REF,
        payload=DecisionAsk(
            subject=WAIT_REF, state={"text": "hello"}, decider="triage"
        ).model_dump(mode="json"),
        created_at=created_at or datetime.now(timezone.utc),
    )


class _Run:
    def __init__(self, wait: WorkflowRunWaitEntity) -> None:
        self.id = wait.run_id
        self.user_id = uuid4()
        self.pod_id = wait.pod_id


class _Engine:
    """The parts of the engine the resume service reaches for a DECISION wait."""

    def __init__(self, wait: WorkflowRunWaitEntity | None) -> None:
        self.uow = None
        self.decision_adapter = _Decisions(_Units())
        self.failures: list[tuple[WorkflowRunWaitType, str, str]] = []
        self.run = _Run(wait) if wait is not None else None
        outer = self

        class _Waits:
            async def find_active_by_external_ref(self, wait_type, external_ref):
                return wait if wait_type is WorkflowRunWaitType.DECISION else None

            async def list_active_older_than(
                self, *, wait_types, created_before, limit
            ):
                return [wait] if wait is not None else []

        class _Runs:
            async def get(self, run_id):
                return outer.run

        self.wait_repo = _Waits()
        self.run_repo = _Runs()

    async def fail_internal(self, wait_type, external_ref, error, output=None):
        self.failures.append((wait_type, external_ref, error))
        return object()

    async def fail_for_wait(self, wait, *, error):
        self.failures.append((wait.wait_type, wait.external_ref, error))
        return object()

    async def stop_underlying_work(self, wait):
        return None


async def test_a_pending_decision_is_read_off_its_wait_and_its_run():
    wait = _wait()
    engine = _Engine(wait)

    pending = await RunResumeService(engine).pending_decision(WAIT_REF)

    assert pending is not None
    assert pending.ask.subject == WAIT_REF
    assert pending.ask.state == {"text": "hello"}
    assert pending.user_id == engine.run.user_id
    assert pending.pod_id == wait.pod_id
    assert pending.decisions is engine.decision_adapter
    assert await RunResumeService(_Engine(None)).pending_decision(WAIT_REF) is None


async def test_a_refusal_fails_the_run_on_its_decision_wait():
    engine = _Engine(_wait())

    assert await RunResumeService(engine).fail_for_decision(WAIT_REF, "No.") is True
    assert engine.failures == [(WorkflowRunWaitType.DECISION, WAIT_REF, "No.")]


async def test_the_sweep_asks_a_stale_decision_again():
    # A lost job leaves the run waiting on a question nobody is asking.
    engine = _Engine(_wait(datetime.now(timezone.utc) - timedelta(minutes=30)))

    acted = await RunResumeService(engine).reconcile_stale_waits()

    assert acted == 1
    assert engine.decision_adapter.requested == [WAIT_REF]
    assert engine.failures == []


async def test_the_sweep_gives_up_on_a_decision_past_the_ceiling():
    engine = _Engine(_wait(datetime.now(timezone.utc) - timedelta(days=30)))

    acted = await RunResumeService(engine).reconcile_stale_waits()

    assert acted == 1
    assert engine.decision_adapter.requested == []
    assert engine.failures and "Decision step did not finish" in engine.failures[0][2]
