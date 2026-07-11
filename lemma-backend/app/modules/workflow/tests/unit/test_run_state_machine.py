"""Run entity state machine: transitions and guards."""

from uuid import uuid4

import pytest

from app.modules.workflow.domain.context import TriggerContext
from app.modules.workflow.domain.run import (
    WorkflowRunEntity,
    WorkflowRunStatus,
    StepStatus,
    summarize_error,
)
from app.modules.workflow.domain.start import WorkflowStartType


def _run(trigger: TriggerContext | None = None) -> WorkflowRunEntity:
    return WorkflowRunEntity.create(
        flow_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        entry_node_id="intake",
        trigger=trigger,
    )


def test_create_manual_run():
    run = _run()
    assert run.status == WorkflowRunStatus.RUNNING
    assert run.start_type == "MANUAL"
    assert run.current_node_id == "intake"
    assert run.execution_context.start is None
    assert run.start_payload == {}


def test_create_triggered_run_sets_start_type_from_trigger():
    run = _run(
        TriggerContext(
            trigger_type=WorkflowStartType.DATASTORE_EVENT,
            payload={"record": 1},
        )
    )
    assert run.start_type == "DATASTORE_EVENT"
    assert run.execution_context.start.payload == {"record": 1}
    assert run.start_payload["payload"] == {"record": 1}


def test_resume_requires_waiting_and_matching_node():
    run = _run()
    with pytest.raises(ValueError, match="Cannot resume"):
        run.resume("intake", {})

    step = run.begin_step("intake")
    run.suspend_step(step, None, human_wait=True)
    assert run.status == WorkflowRunStatus.WAITING

    with pytest.raises(ValueError, match="suspended on node"):
        run.resume("other", {})

    run.resume("intake", {"amount": 1})
    assert run.status == WorkflowRunStatus.RUNNING
    assert run.step_history[-1].status == StepStatus.COMPLETED
    assert run.step_history[-1].output_data == {"amount": 1}
    assert run.execution_context.to_view()["intake"] == {"amount": 1}


def test_machine_suspend_keeps_run_running_and_resumes_open_step():
    run = _run()
    step = run.begin_step("agent")
    run.current_node_id = "agent"
    run.suspend_step(step, None, human_wait=False)

    assert run.status == WorkflowRunStatus.RUNNING
    assert run.step_history[-1].status == StepStatus.RUNNING

    run.resume("agent", {"answer": 42})

    assert run.status == WorkflowRunStatus.RUNNING
    assert run.step_history[-1].status == StepStatus.COMPLETED
    assert run.execution_context.to_view()["agent"] == {"answer": 42}


def test_cancel_from_waiting_cancels_step():
    run = _run()
    step = run.begin_step("intake")
    run.suspend_step(step, None, human_wait=True)
    run.cancel()
    assert run.status == WorkflowRunStatus.CANCELLED
    assert run.completed_at is not None
    assert run.step_history[-1].status == StepStatus.CANCELLED


def test_cancel_terminal_run_rejected():
    run = _run()
    run.complete()
    with pytest.raises(ValueError, match="Cannot cancel"):
        run.cancel()


def test_fail_marks_last_open_step_and_truncates():
    run = _run()
    run.begin_step("intake")
    long_error = "x" * 5000
    run.fail(long_error, node_id="intake")
    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_node_id == "intake"
    assert len(run.error) <= 2000
    assert run.step_history[-1].status == StepStatus.FAILED


def test_summarize_error_keeps_head_and_tail():
    text = "HEAD " + ("middle " * 1000) + "TAIL"
    summary = summarize_error(text)
    assert summary.startswith("HEAD")
    assert summary.endswith("TAIL")
    assert "[truncated]" in summary
    assert len(summary) <= 2000
