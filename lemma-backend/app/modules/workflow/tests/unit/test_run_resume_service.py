"""RunResumeService failure transitions for machine waits."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.services.run_resume_service import RunResumeService

pytestmark = pytest.mark.asyncio


def _wait(wait_type: WorkflowRunWaitType, external_ref: str) -> WorkflowRunWaitEntity:
    return WorkflowRunWaitEntity(
        run_id=uuid4(),
        flow_id=uuid4(),
        pod_id=uuid4(),
        node_id="machine_node",
        wait_type=wait_type,
        external_ref=external_ref,
    )


class FakeEngine:
    def __init__(self):
        self.uow = None
        self.failures: list[dict] = []

    async def fail_internal(self, wait_type, external_ref, error, output=None):
        self.failures.append(
            {
                "wait_type": wait_type,
                "external_ref": external_ref,
                "error": error,
                "output": output,
            }
        )


async def test_failed_agent_status_fails_workflow_with_adapter_error(monkeypatch):
    engine = FakeEngine()
    service = RunResumeService(engine)

    async def no_auth_context(wait):
        return None

    monkeypatch.setattr(service, "_run_context_for_wait", no_auth_context)

    handled = await service._apply_agent_status(
        _wait(WorkflowRunWaitType.AGENT, "conversation-1"),
        "conversation-1",
        {"status": "FAILED", "error": "Agent conversation FAILED"},
    )

    assert handled is True
    assert engine.failures == [
        {
            "wait_type": WorkflowRunWaitType.AGENT,
            "external_ref": "conversation-1",
            "error": "Agent conversation FAILED",
            "output": {"error": "Agent conversation FAILED"},
        }
    ]


async def test_failed_function_status_fails_workflow():
    engine = FakeEngine()
    service = RunResumeService(engine)
    wait = _wait(WorkflowRunWaitType.FUNCTION, "function-run-1")

    handled = await service._apply_function_status(
        wait,
        {"status": "FAILED", "error": "Function exploded"},
    )

    assert handled is True
    assert engine.failures == [
        {
            "wait_type": WorkflowRunWaitType.FUNCTION,
            "external_ref": "function-run-1",
            "error": "Function exploded",
            "output": None,
        }
    ]


class _FakeWaitRepo:
    def __init__(self, wait):
        self._wait = wait

    async def find_active_by_external_ref(self, wait_type, external_ref):
        return self._wait


class _ResumeEngine:
    def __init__(self, wait):
        self.uow = None
        self.wait_repo = _FakeWaitRepo(wait)
        self.resumed: list[dict] = []
        self.failures: list[dict] = []
        self.adapter_calls = 0
        outer = self

        class _Adapter:
            async def get_run_status(self, run_id):
                outer.adapter_calls += 1
                return {"status": "COMPLETED", "output_data": {"from": "adapter"}}

        self.function_adapter = _Adapter()

    async def resume_internal(self, wait_type, external_ref, output, ctx=None):
        self.resumed.append(
            {"wait_type": wait_type, "external_ref": external_ref, "output": output}
        )

    async def fail_internal(self, wait_type, external_ref, error, output=None):
        self.failures.append(
            {"wait_type": wait_type, "external_ref": external_ref, "error": error}
        )


async def _no_ctx(wait):
    return None


async def test_resume_for_function_run_trusts_event_output(monkeypatch):
    fr_id = str(uuid4())
    engine = _ResumeEngine(_wait(WorkflowRunWaitType.FUNCTION, fr_id))
    service = RunResumeService(engine)
    monkeypatch.setattr(service, "_run_context_for_wait", _no_ctx)

    handled = await service.resume_for_function_run(
        function_run_id=fr_id, run_status="COMPLETED", output={"x": 1}
    )

    assert handled is True
    # Output came from the event, so the adapter is never consulted.
    assert engine.adapter_calls == 0
    assert engine.resumed == [
        {"wait_type": WorkflowRunWaitType.FUNCTION, "external_ref": fr_id, "output": {"x": 1}}
    ]


async def test_resume_for_function_run_falls_back_to_adapter_when_output_none(monkeypatch):
    fr_id = str(uuid4())
    engine = _ResumeEngine(_wait(WorkflowRunWaitType.FUNCTION, fr_id))
    service = RunResumeService(engine)
    monkeypatch.setattr(service, "_run_context_for_wait", _no_ctx)

    handled = await service.resume_for_function_run(
        function_run_id=fr_id, run_status="COMPLETED", output=None
    )

    assert handled is True
    assert engine.adapter_calls == 1
    assert engine.resumed == [
        {
            "wait_type": WorkflowRunWaitType.FUNCTION,
            "external_ref": fr_id,
            "output": {"from": "adapter"},
        }
    ]


async def test_resume_for_function_run_no_active_wait_is_noop():
    engine = _ResumeEngine(None)
    service = RunResumeService(engine)

    handled = await service.resume_for_function_run(
        function_run_id=str(uuid4()), run_status="COMPLETED", output={"x": 1}
    )

    assert handled is False
    assert engine.resumed == []
    assert engine.failures == []


async def test_overdue_agent_wait_is_expired_rather_than_left_running():
    """A wait past the configured ceiling fails the run.

    Before this, an agent that hung rather than failed kept its run
    non-terminal forever: `_apply_agent_status` leaves RUNNING conversations
    alone by design, so nothing ever moved the run off "still going".
    """
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.AGENT, "conversation-overdue")
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await service._expire_overdue_wait(
        wait,
        datetime.now(timezone.utc) - timedelta(hours=6),
    )

    assert handled is True
    assert len(engine.failures) == 1
    assert engine.failures[0]["external_ref"] == "conversation-overdue"
    assert "did not finish within" in engine.failures[0]["error"]


async def test_overdue_agent_wait_is_left_alone_while_the_agent_is_snoozed():
    """A snoozed agent is healthy and wakes itself, so the ceiling must not fire.

    Without this, an agent that snoozes longer than
    ``workflow_wait_max_age_seconds`` fails the *workflow* while nothing is
    actually wrong — a silent wrong outcome rather than a visible error. An agent
    blocked on a person stays subject to the ceiling; that is the hang it exists
    to catch.
    """
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.AGENT, "conversation-snoozed")
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await service._expire_overdue_wait(
        wait,
        datetime.now(timezone.utc) - timedelta(hours=6),
        agent_status={"status": "WAITING", "wait_reason": "SNOOZE"},
    )

    assert handled is False
    assert engine.failures == []


async def test_overdue_agent_wait_blocked_on_a_human_still_expires():
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.AGENT, "conversation-blocked")
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await service._expire_overdue_wait(
        wait,
        datetime.now(timezone.utc) - timedelta(hours=6),
        agent_status={"status": "WAITING", "wait_reason": "HUMAN"},
    )

    assert handled is True
    assert len(engine.failures) == 1


async def test_wait_within_the_ceiling_is_left_alone():
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.FUNCTION, "run-recent")
    wait.created_at = datetime.now(timezone.utc) - timedelta(minutes=20)

    handled = await service._expire_overdue_wait(
        wait,
        datetime.now(timezone.utc) - timedelta(hours=6),
    )

    assert handled is False
    assert engine.failures == []


async def test_time_waits_are_exempt_from_the_ceiling():
    """A wait-until node is supposed to sit for as long as it was told to."""
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.TIME, "timer-1")
    wait.created_at = datetime.now(timezone.utc) - timedelta(days=30)

    handled = await service._expire_overdue_wait(
        wait,
        datetime.now(timezone.utc) - timedelta(hours=6),
    )

    assert handled is False
    assert engine.failures == []
