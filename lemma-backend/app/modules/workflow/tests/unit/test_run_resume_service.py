"""RunResumeService: the AGENT waits the sweep still reconciles and expires."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.services.run_resume_service import RunResumeService

pytestmark = pytest.mark.asyncio


def _wait(
    wait_type: WorkflowRunWaitType, external_ref: str | None
) -> WorkflowRunWaitEntity:
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
        self.stopped: list[object] = []

    async def stop_underlying_work(self, wait):
        self.stopped.append(wait)

    async def fail_internal(self, wait_type, external_ref, error, output=None):
        self.failures.append(
            {
                "wait_type": wait_type,
                "external_ref": external_ref,
                "error": error,
                "output": output,
            }
        )

    async def fail_for_wait(self, wait, *, error, output=None):
        await self.fail_internal(wait.wait_type, wait.external_ref, error, output)


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
        wait, now=datetime.now(timezone.utc), agent_status={}
    )

    assert handled is True
    assert len(engine.failures) == 1
    assert engine.failures[0]["external_ref"] == "conversation-overdue"
    assert "did not finish within" in engine.failures[0]["error"]


async def test_overdue_agent_wait_is_left_alone_while_the_agent_is_waiting():
    """A waiting agent is healthy and wakes itself, so the ceiling must not fire.

    Without this, an agent that waits longer than
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
        now=datetime.now(timezone.utc),
        agent_status={"status": "WAITING", "wait_reason": "WAIT"},
    )

    assert handled is False
    assert engine.failures == []


async def test_a_human_blocked_wait_gets_a_much_larger_ceiling():
    """A person not answering overnight is not a hang.

    The machine ceiling catches work that stopped making progress. Applied to a
    wait on a person it only ever catches someone being asleep — a workflow that
    asks a question at 18:00 and kills itself at midnight is a worse outcome than
    one that reads as still waiting.
    """
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.AGENT, "conversation-blocked")
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await service._expire_overdue_wait(
        wait,
        now=datetime.now(timezone.utc),
        agent_status={"status": "WAITING", "wait_reason": "HUMAN"},
    )

    assert handled is False
    assert engine.failures == []


async def test_a_human_blocked_wait_still_expires_eventually():
    """Larger, not absent — a truly abandoned wait is still bounded."""
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.AGENT, "conversation-abandoned")
    wait.created_at = datetime.now(timezone.utc) - timedelta(days=30)

    handled = await service._expire_overdue_wait(
        wait,
        now=datetime.now(timezone.utc),
        agent_status={"status": "WAITING", "wait_reason": "HUMAN"},
    )

    assert handled is True
    assert len(engine.failures) == 1


async def test_an_unknown_wait_reason_is_not_exempt():
    """The exemption is a whitelist, not "anything that is not HUMAN".

    A wait reason added later must stay subject to the ceiling until someone
    decides it wakes itself; the other default lets a reason nobody thought
    about silently disable the ceiling.
    """
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.AGENT, "conversation-unknown-reason")
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await service._expire_overdue_wait(
        wait,
        now=datetime.now(timezone.utc),
        agent_status={"status": "WAITING", "wait_reason": "SOMETHING_NEW"},
    )

    assert handled is True


async def test_expiry_stops_the_work_it_is_failing_the_run_for():
    """Cancel already stops the agent/function; expiry must too.

    Otherwise a run is marked failed for hanging while the agent it was waiting
    on keeps burning a sandbox — the exact cost the ceiling exists to end.
    """
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.AGENT, "conversation-overdue")
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await service._expire_overdue_wait(
        wait, now=datetime.now(timezone.utc), agent_status={}
    )

    assert handled is True
    assert engine.stopped == [wait]


async def test_an_agent_step_with_its_own_timeout_is_stopped_at_it():
    """The step's timeout replaces the deployment ceiling in both directions:
    a step the author bounded at twenty minutes does not run for six hours."""
    engine = FakeEngine()
    service = RunResumeService(engine)
    now = datetime.now(timezone.utc)
    wait = _wait(WorkflowRunWaitType.AGENT, "conversation-bounded")
    wait.payload = {"agent_name": "triage", "timeout_seconds": 1200}
    wait.created_at = now - timedelta(minutes=30)

    handled = await service._expire_overdue_wait(wait, now=now, agent_status={})

    assert handled is True
    assert "did not finish within 20 min" in engine.failures[0]["error"]


async def test_an_agent_step_given_longer_than_the_ceiling_keeps_running():
    engine = FakeEngine()
    service = RunResumeService(engine)
    now = datetime.now(timezone.utc)
    wait = _wait(WorkflowRunWaitType.AGENT, "conversation-long")
    wait.payload = {"agent_name": "triage", "timeout_seconds": 24 * 3600}
    wait.created_at = now - timedelta(hours=10)

    handled = await service._expire_overdue_wait(wait, now=now, agent_status={})

    assert handled is False
    assert engine.failures == []


async def test_the_sweep_reads_agent_waits_only():
    """Every other wait is woken or expired by its own work items."""
    engine = FakeEngine()
    requested: list[list] = []

    class _WaitRepo:
        async def list_active_older_than(
            self, *, wait_types, created_before, limit, after, **_filters
        ):
            requested.append(list(wait_types))
            return []

    engine.wait_repo = _WaitRepo()

    assert await RunResumeService(engine).reconcile_stale_waits() == 0
    assert requested == [[WorkflowRunWaitType.AGENT]]
