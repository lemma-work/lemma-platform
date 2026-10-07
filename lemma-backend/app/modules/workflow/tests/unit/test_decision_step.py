"""A decision node that asks a question: suspend, ask, route, and every failure.

The engine, the job queue and the provider are stand-ins injected through the
seams the code already takes -- the `DecisionPort` on the stepper, the
`DecisionWaits` and `DecisionMaker` the job is handed, and the engine and
authorization context the resume service is built with.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.decisions.contracts import (
    Answer,
    DecisionCaller,
    DecisionInvalidError,
    DecisionLimitedError,
    DecisionRequest,
    DecisionResult,
    DecisionUnavailableError,
)
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.workflow.domain.context import TriggerContext
from app.modules.workflow.domain.decision_questions import DecisionAsk, choose_route
from app.modules.workflow.domain.graph import WorkflowEdge
from app.modules.workflow.domain.nodes import (
    DecisionNode,
    DecisionNodeConfig,
    DecisionQuestion,
    EndNode,
    FunctionNode,
    FunctionNodeConfig,
)
from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.execution.stepper import RunStepper
from app.modules.workflow.infrastructure.decision_queue import (
    AfterCommitDecisionQueue,
)
from app.modules.workflow.services.decision_resume_service import (
    MAX_DECISION_REQUEUES,
    DecisionResumeService,
    PendingDecision,
)
from app.modules.workflow.services.decision_step import (
    DECISION_STEP_MAX_ATTEMPTS,
    RetryDecision,
    ask_waiting_decision,
)
from app.modules.workflow.services.run_resume_service import RunResumeService

pytestmark = pytest.mark.unit

CATEGORY = {
    "type": "string",
    "enum": ["billing", "bug", "other"],
    "description": "What is this email about?",
}


def _question(**fields: object) -> DecisionQuestion:
    return DecisionQuestion.model_validate(
        {
            "instruction": "Triage incoming support email.",
            "evidence": {"type": "expression", "value": "start.payload.email"},
            "answer": CATEGORY,
            "examples": [{"evidence": "I was charged twice", "answer": "billing"}],
            "routes": {"billing": "refund", "bug": "file_bug"},
            **fields,
        }
    )


def _flow(question: DecisionQuestion, *, default_edge: bool = True) -> WorkflowEntity:
    edges = [
        WorkflowEdge(id="e1", source="refund", target="done"),
        WorkflowEdge(id="e2", source="file_bug", target="done"),
    ]
    if default_edge:
        edges.append(WorkflowEdge(id="e3", source="triage", target="done"))
    flow = WorkflowEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="triage",
        nodes=[
            DecisionNode(id="triage", config=DecisionNodeConfig(question=question)),
            FunctionNode(
                id="refund", config=FunctionNodeConfig(function_name="refund")
            ),
            FunctionNode(id="file_bug", config=FunctionNodeConfig(function_name="bug")),
            EndNode(id="done"),
        ],
        edges=edges,
    )
    flow.validate_graph()
    return flow


def _run(
    flow: WorkflowEntity, *, email: object = "Please refund me"
) -> WorkflowRunEntity:
    return WorkflowRunEntity.create(
        flow_id=flow.id,
        pod_id=flow.pod_id,
        user_id=uuid4(),
        entry_node_id=flow.entry_node_id,
        trigger=TriggerContext(payload={"email": email} if email is not None else {}),
    )


class _Ports:
    """The four ports a stepper drives; only `decision` matters here."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.functions: list[str] = []

    def ask_once_committed(self, external_ref: str) -> None:
        self.asked.append(external_ref)

    async def execute_function(self, function_name, inputs, pod_id, user_id, ctx=None):
        self.functions.append(function_name)
        return {"done": function_name}

    async def schedule_workflow_wake(self, run_id, scheduled_at, pod_id, user_id):
        return run_id


def _stepper(ports: _Ports) -> RunStepper:
    return RunStepper(agent=ports, function=ports, schedule=ports, decision=ports)


# -- the executor suspends; it never answers -------------------------------------


async def test_a_question_suspends_the_run_on_a_decision_wait_holding_the_request():
    ports = _Ports()
    flow = _flow(_question())
    run = _run(flow)

    result = await _stepper(ports).advance(run, flow)

    assert run.status == WorkflowRunStatus.RUNNING
    assert run.current_node_id == "triage"
    assert result.wait is not None
    assert result.wait.wait_type == WorkflowRunWaitType.DECISION
    assert ports.asked == [result.wait.external_ref]
    assert run.step_history[-1].external_ref == result.wait.external_ref
    ask = DecisionAsk.from_payload(result.wait.payload)
    assert ask.node_id == "triage"
    assert ask.evidence == "Please refund me"
    assert ask.schema_ == {"type": "object", "properties": {"answer": CATEGORY}}
    assert ask.examples[0].answers == {"answer": "billing"}


async def test_evidence_that_resolves_to_nothing_fails_the_run_before_asking():
    ports = _Ports()
    flow = _flow(_question())
    run = _run(flow, email=None)

    result = await _stepper(ports).advance(run, flow)

    assert result.wait is None
    assert run.status == WorkflowRunStatus.FAILED
    assert "start.payload.email" in (run.error or "")
    assert ports.asked == []


async def test_resuming_with_a_chosen_branch_takes_it_rather_than_the_edge():
    ports = _Ports()
    flow = _flow(_question())
    run = _run(flow)
    await _stepper(ports).advance(run, flow)

    run.resume("triage", {"answer": "billing", "route": "refund"})
    await _stepper(ports).continue_after(run, flow, "triage", forced="refund")

    assert run.status == WorkflowRunStatus.COMPLETED
    assert ports.functions == ["refund"]
    assert run.execution_context.to_view()["triage"]["route"] == "refund"


# -- routing an answer --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "confidence", "expected", "unsure"),
    [
        ("billing", None, "refund", False),
        ("other", None, "default", False),
        (None, None, "unsure", True),
        ("billing", 0.4, "unsure", True),
        ("billing", 0.9, "refund", False),
    ],
)
def test_an_answer_routes_by_value_unsure_or_default(
    value, confidence, expected, unsure
):
    question = _question(
        routes={"billing": "refund"}, unsure_next_node_id="unsure", min_confidence=0.5
    )

    route = choose_route(
        question, value=value, confidence=confidence, default_next_node_id="default"
    )

    assert route.next_node_id == expected
    assert route.unsure is unsure


def test_booleans_and_levels_route_as_strings():
    yes_no = _question(
        answer={"type": "boolean", "description": "Refund?"},
        routes={"false": "no", "true": "yes"},
    )
    scale = _question(
        answer={
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "description": "How bad?",
        },
        routes={"3": "page"},
        examples=[],
    )

    assert (
        choose_route(
            yes_no, value=False, confidence=None, default_next_node_id=None
        ).next_node_id
        == "no"
    )
    assert (
        choose_route(
            scale, value=3, confidence=None, default_next_node_id=None
        ).next_node_id
        == "page"
    )


def test_an_unsure_answer_without_an_unsure_route_falls_to_the_default_edge():
    route = choose_route(
        _question(), value=None, confidence=None, default_next_node_id="default"
    )

    assert route.next_node_id == "default"


def test_with_no_route_and_no_default_edge_there_is_nowhere_to_go():
    route = choose_route(
        _question(routes={"billing": "refund"}),
        value="bug",
        confidence=None,
        default_next_node_id=None,
    )

    assert route.next_node_id is None
    assert route.unsure is False


# -- asking: retry, fail, never branch ---------------------------------------------


def _pending() -> PendingDecision:
    return PendingDecision(
        request=DecisionRequest(
            instruction="Triage.", evidence="refund me", schema={"type": "object"}
        ),
        caller=DecisionCaller(user_id=uuid4(), organization_id=None, pod_id=uuid4()),
    )


def _result(
    value: object = "billing", confidence: float | None = None
) -> DecisionResult:
    return DecisionResult(
        answers={"answer": Answer(value, confidence)}, provider="model", model="m"
    )


class _Waits:
    def __init__(self, pending: PendingDecision | None) -> None:
        self.pending = pending
        self.routed: list[DecisionResult] = []
        self.failed: list[str] = []

    async def pending_decision(self, external_ref: str) -> PendingDecision | None:
        return self.pending

    async def route_decision(self, external_ref: str, result: DecisionResult) -> None:
        self.routed.append(result)

    async def fail_decision(self, external_ref: str, error: str) -> None:
        self.failed.append(error)


class _Maker:
    def __init__(self, outcome: DecisionResult | Exception) -> None:
        self.outcome = outcome
        self.calls = 0

    async def decide(self, request, caller) -> DecisionResult:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


async def _ask(outcome: DecisionResult | Exception, *, attempt: int = 1, pending=True):
    waits = _Waits(_pending() if pending else None)
    maker = _Maker(outcome)
    retry = await ask_waiting_decision("ref", attempt=attempt, waits=waits, maker=maker)
    return retry, waits, maker


async def test_an_answer_is_handed_on_to_be_routed():
    retry, waits, _ = await _ask(_result())

    assert retry is None
    assert waits.routed == [_result()]
    assert waits.failed == []


async def test_a_run_no_longer_waiting_is_not_asked_about():
    """A cancelled run, or a duplicate job after the first answered."""
    retry, waits, maker = await _ask(_result(), pending=False)

    assert retry is None
    assert maker.calls == 0
    assert waits.routed == waits.failed == []


@pytest.mark.parametrize(
    "reason", ["timeout", "transport", "provider_error", "invalid_output"]
)
async def test_a_provider_that_did_not_answer_is_asked_again_later(reason):
    retry, waits, _ = await _ask(DecisionUnavailableError(reason), attempt=2)

    assert retry == RetryDecision(delay_seconds=10)
    assert waits.routed == waits.failed == []


async def test_asking_too_fast_waits_at_least_as_long_as_it_was_told():
    retry, waits, _ = await _ask(DecisionLimitedError(retry_after_seconds=40))

    assert retry == RetryDecision(delay_seconds=40)
    assert waits.failed == []


async def test_the_last_attempt_fails_the_run_naming_the_cause_and_takes_no_branch():
    retry, waits, _ = await _ask(
        DecisionUnavailableError("timeout"), attempt=DECISION_STEP_MAX_ATTEMPTS
    )

    assert retry is None
    assert waits.routed == []
    assert waits.failed == [
        (
            f"The question went unanswered after {DECISION_STEP_MAX_ATTEMPTS} "
            "attempts: The decision provider did not answer in time."
        )
    ]


@pytest.mark.parametrize(
    "error",
    [
        DecisionUnavailableError("token_limit"),
        DecisionUnavailableError("not_configured"),
        UsageLimitExceededError(),
        DecisionInvalidError([{"path": "evidence", "message": "Must not be empty."}]),
    ],
)
async def test_what_retrying_cannot_fix_fails_the_run_at_once(error):
    retry, waits, _ = await _ask(error)

    assert retry is None
    assert waits.routed == []
    [failure] = waits.failed
    assert failure.startswith("The question")


async def test_an_invalid_question_says_what_was_wrong_with_it():
    _, waits, _ = await _ask(
        DecisionInvalidError([{"path": "evidence", "message": "Must not be empty."}])
    )

    assert waits.failed == [
        "The question could not be asked: evidence: Must not be empty."
    ]


# -- the transaction side ------------------------------------------------------


class _Ctx:
    organization_id = UUID(int=7)


async def _context(user_id: UUID, pod_id: UUID) -> _Ctx:
    return _Ctx()


class _Repo:
    def __init__(self, engine: "_Engine") -> None:
        self._engine = engine

    async def find_active_by_external_ref(self, wait_type, external_ref):
        wait = self._engine.wait
        if wait is None or wait.external_ref != external_ref:
            return None
        return wait

    async def update(self, wait):
        self._engine.updated.append(dict(wait.payload))
        return wait

    async def get(self, entity_id):
        if entity_id == self._engine.flow.id:
            return self._engine.flow
        return self._engine.run

    async def get_for_update(self, run_id):
        return self._engine.run

    async def list_active_older_than(self, *, wait_types, created_before, limit):
        return [self._engine.wait] if self._engine.wait is not None else []


class _Uow:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Engine:
    def __init__(self, flow: WorkflowEntity, *, run_status=WorkflowRunStatus.RUNNING):
        self.flow = flow
        self.run = _run(flow)
        self.run.status = run_status
        self.wait: WorkflowRunWaitEntity | None = WorkflowRunWaitEntity(
            run_id=self.run.id,
            flow_id=flow.id,
            pod_id=flow.pod_id,
            node_id="triage",
            wait_type=WorkflowRunWaitType.DECISION,
            external_ref="ref",
            payload=DecisionAsk.for_question(
                "triage", flow.get_node("triage").config.question, "refund me"
            ).to_payload(),
        )
        self.wait_repo = self.run_repo = self.flow_repo = _Repo(self)
        self.uow = _Uow()
        self.decision_adapter = _Ports()
        self.updated: list[dict] = []
        self.resumed: list[dict] = []
        self.failed: list[dict] = []

    async def resume_internal(
        self, wait_type, external_ref, output, *, ctx, next_node_id
    ):
        self.resumed.append({"output": output, "next_node_id": next_node_id})

    async def fail_internal(self, wait_type, external_ref, error, output=None):
        self.failed.append({"error": error, "output": output})
        return self.run

    async def fail_for_wait(self, wait, *, error):
        self.failed.append({"error": error, "output": None})
        return self.run

    async def stop_underlying_work(self, wait):
        return None


def _service(engine: _Engine) -> DecisionResumeService:
    return DecisionResumeService(engine, user_context=_context)


async def test_the_pending_request_is_the_one_stored_and_asked_as_the_run_owner():
    engine = _Engine(_flow(_question()))

    pending = await _service(engine).find_pending("ref")

    assert pending is not None
    assert pending.request.instruction == "Triage incoming support email."
    assert pending.request.evidence == "refund me"
    assert pending.request.priority == "background"
    assert pending.caller.user_id == engine.run.user_id
    assert pending.caller.organization_id == UUID(int=7)
    assert pending.caller.source_type == "workflow_decision"
    assert pending.caller.workload_type == "workflow"
    assert pending.caller.workload_id == engine.flow.id


@pytest.mark.parametrize(
    "status", [WorkflowRunStatus.CANCELLED, WorkflowRunStatus.FAILED]
)
async def test_a_finished_run_has_nothing_pending(status):
    engine = _Engine(_flow(_question()), run_status=status)

    assert await _service(engine).find_pending("ref") is None


async def test_an_answer_resumes_down_its_route_and_records_why():
    engine = _Engine(_flow(_question()))

    await _service(engine).resume_on_answer("ref", _result("billing", 0.8))

    assert engine.resumed == [
        {
            "output": {
                "answer": "billing",
                "confidence": 0.8,
                "provider": "model",
                "model": "m",
                "route": "refund",
            },
            "next_node_id": "refund",
        }
    ]


async def test_an_unrouted_answer_takes_the_default_edge():
    engine = _Engine(_flow(_question()))

    await _service(engine).resume_on_answer("ref", _result("other"))

    assert engine.resumed[0]["next_node_id"] == "done"


async def test_an_unsure_answer_takes_the_unsure_route():
    engine = _Engine(
        _flow(_question(routes={"billing": "refund"}, unsure_next_node_id="file_bug"))
    )

    await _service(engine).resume_on_answer("ref", _result(None))

    assert engine.resumed[0]["next_node_id"] == "file_bug"
    assert engine.resumed[0]["output"]["answer"] is None


async def test_an_answer_with_no_route_fails_the_run_instead_of_guessing():
    engine = _Engine(
        _flow(
            _question(
                routes={"billing": "refund", "bug": "file_bug", "other": "refund"},
                unsure_next_node_id="file_bug",
            ),
            default_edge=False,
        )
    )
    # The author removed a route while the run waited.
    triage = engine.flow.get_node("triage")
    triage.config.question.routes.pop("other")

    await _service(engine).resume_on_answer("ref", _result("other"))

    assert engine.resumed == []
    [failure] = engine.failed
    assert "answered 'other', which has no route" in failure["error"]
    assert failure["output"]["answer"] == "other"


async def test_a_step_removed_while_it_waited_fails_the_run():
    engine = _Engine(_flow(_question()))
    engine.flow.nodes = [node for node in engine.flow.nodes if node.id != "triage"]

    await _service(engine).resume_on_answer("ref", _result())

    assert engine.resumed == []
    assert "was removed from the workflow" in engine.failed[0]["error"]


async def test_an_answer_for_a_decision_no_longer_waited_on_is_dropped():
    engine = _Engine(_flow(_question()))
    engine.wait = None

    assert await _service(engine).resume_on_answer("ref", _result()) is False
    assert engine.resumed == engine.failed == []


# -- recovering a lost job ---------------------------------------------------------


async def test_a_lost_decision_is_queued_again_and_counted():
    engine = _Engine(_flow(_question()))

    assert await _service(engine).recover_lost(engine.wait) is True

    assert engine.decision_adapter.asked == ["ref"]
    assert engine.updated[-1]["requeues"] == 1
    assert engine.uow.commits == 1
    assert engine.failed == []


async def test_a_decision_answered_meanwhile_is_left_alone():
    """The job answered it between the sweep's read and its row lock."""
    engine = _Engine(_flow(_question()))
    stale = engine.wait
    engine.wait = None

    assert await _service(engine).recover_lost(stale) is False

    assert engine.decision_adapter.asked == []
    assert engine.updated == engine.failed == []
    assert engine.uow.commits == 1, "the run's row lock is released"


async def test_a_decision_lost_too_often_fails_the_run():
    engine = _Engine(_flow(_question()))
    engine.wait.payload = {**engine.wait.payload, "requeues": MAX_DECISION_REQUEUES}

    assert await _service(engine).recover_lost(engine.wait) is True

    assert engine.decision_adapter.asked == []
    assert "was not answered after" in engine.failed[0]["error"]


async def test_the_sweep_recovers_a_stale_decision_wait():
    engine = _Engine(_flow(_question()))
    engine.wait.created_at = datetime.now(timezone.utc) - timedelta(minutes=11)

    acted = await RunResumeService(engine).reconcile_stale_waits()

    assert acted == 1
    assert engine.decision_adapter.asked == ["ref"]


# -- queueing the job after the commit -------------------------------------------


class _AfterCommitUow:
    def __init__(self) -> None:
        self.callbacks: list = []

    def after_commit(self, callback) -> None:
        self.callbacks.append(callback)


class _JobQueue:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.enqueued: list[dict] = []

    async def enqueue(self, job_name: str, **kwargs: object) -> None:
        if self.error is not None:
            raise self.error
        self.enqueued.append({"job_name": job_name, **kwargs})


async def test_the_job_is_queued_only_once_the_wait_is_committed():
    uow = _AfterCommitUow()
    queue = _JobQueue()

    AfterCommitDecisionQueue(uow, queue=queue).ask_once_committed("ref")
    assert queue.enqueued == []
    for callback in uow.callbacks:
        await callback()

    assert queue.enqueued == [
        {
            "job_name": "decide_workflow_step",
            "external_ref": "ref",
            "_job_id": "workflow-decision:ref",
        }
    ]


async def test_a_queue_that_cannot_be_reached_leaves_the_decision_to_the_sweep():
    uow = _AfterCommitUow()

    AfterCommitDecisionQueue(
        uow, queue=_JobQueue(ConnectionRefusedError())
    ).ask_once_committed("ref")

    await uow.callbacks[0]()  # does not raise into the committed request
