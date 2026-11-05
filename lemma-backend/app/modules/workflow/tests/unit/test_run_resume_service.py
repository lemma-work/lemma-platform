"""RunResumeService: an AGENT wait at its ceiling -- resumed, failed, or re-armed."""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.services.run_resume_service import RunResumeService

pytestmark = pytest.mark.asyncio


def _ref(label: str) -> str:
    """A conversation id, named for what the test is about."""
    return str(uuid5(NAMESPACE_URL, label))


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


class FakeAdapter:
    def __init__(self):
        self.status: dict = {"status": "RUNNING"}

    async def get_conversation_status(self, conversation_id):
        del conversation_id
        return self.status


class FakeEngine:
    def __init__(self):
        self.uow = None
        self.failures: list[dict] = []
        self.stopped: list[object] = []
        self.agent_adapter = FakeAdapter()

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


async def _expire(service, wait, *, now, status=None) -> bool:
    """True when the wait was acted on; False when it was re-armed for later."""
    if status is not None:
        service._engine.agent_adapter.status = status
    return await service.expire_agent_wait(wait, now=now) is None


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

    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-overdue"))
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await _expire(service, wait, now=datetime.now(timezone.utc))

    assert handled is True
    assert len(engine.failures) == 1
    assert engine.failures[0]["external_ref"] == _ref("conversation-overdue")
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

    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-snoozed"))
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await _expire(
        service,
        wait,
        now=datetime.now(timezone.utc),
        status={"status": "WAITING", "wait_reason": "WAIT"},
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

    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-blocked"))
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await _expire(
        service,
        wait,
        now=datetime.now(timezone.utc),
        status={"status": "WAITING", "wait_reason": "HUMAN"},
    )

    assert handled is False
    assert engine.failures == []


async def test_a_human_blocked_wait_still_expires_eventually():
    """Larger, not absent — a truly abandoned wait is still bounded."""
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-abandoned"))
    wait.created_at = datetime.now(timezone.utc) - timedelta(days=30)

    handled = await _expire(
        service,
        wait,
        now=datetime.now(timezone.utc),
        status={"status": "WAITING", "wait_reason": "HUMAN"},
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

    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-unknown-reason"))
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await _expire(
        service,
        wait,
        now=datetime.now(timezone.utc),
        status={"status": "WAITING", "wait_reason": "SOMETHING_NEW"},
    )

    assert handled is True


async def test_expiry_stops_the_work_it_is_failing_the_run_for():
    """Cancel already stops the agent/function; expiry must too.

    Otherwise a run is marked failed for hanging while the agent it was waiting
    on keeps burning a sandbox — the exact cost the ceiling exists to end.
    """
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-overdue"))
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await _expire(service, wait, now=datetime.now(timezone.utc))

    assert handled is True
    assert engine.stopped == [wait]


async def test_an_agent_step_with_its_own_timeout_is_stopped_at_it():
    """The step's timeout replaces the deployment ceiling in both directions:
    a step the author bounded at twenty minutes does not run for six hours."""
    engine = FakeEngine()
    service = RunResumeService(engine)
    now = datetime.now(timezone.utc)
    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-bounded"))
    wait.payload = {"agent_name": "triage", "timeout_seconds": 1200}
    wait.created_at = now - timedelta(minutes=30)

    handled = await _expire(service, wait, now=now)

    assert handled is True
    assert "did not finish within 20 min" in engine.failures[0]["error"]


async def test_an_agent_step_given_longer_than_the_ceiling_keeps_running():
    engine = FakeEngine()
    service = RunResumeService(engine)
    now = datetime.now(timezone.utc)
    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-long"))
    wait.payload = {"agent_name": "triage", "timeout_seconds": 24 * 3600}
    wait.created_at = now - timedelta(hours=10)

    handled = await _expire(service, wait, now=now)

    assert handled is False
    assert engine.failures == []


async def test_a_finished_conversation_resumes_rather_than_expires(monkeypatch):
    """A completion that reached nobody (the cutover's backlog) is applied."""
    engine = FakeEngine()
    resumed: list[tuple] = []

    async def resume_internal(wait_type, external_ref, output, ctx=None):
        resumed.append((wait_type, external_ref, output))

    engine.resume_internal = resume_internal
    engine.agent_adapter.status = {"status": "COMPLETED", "output_data": {"a": 1}}
    service = RunResumeService(engine)

    async def no_auth_context(wait):
        return None

    monkeypatch.setattr(service, "_run_context_for_wait", no_auth_context)
    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-done"))

    again = await service.expire_agent_wait(wait, now=datetime.now(timezone.utc))

    assert again is None
    assert resumed == [(WorkflowRunWaitType.AGENT, _ref("conversation-done"), {"a": 1})]
    assert engine.failures == []


async def test_a_wait_not_due_yet_is_checked_again_at_its_ceiling():
    engine = FakeEngine()
    service = RunResumeService(engine)
    now = datetime.now(timezone.utc)
    wait = _wait(WorkflowRunWaitType.AGENT, _ref("conversation-young"))
    wait.payload = {"agent_name": "triage", "timeout_seconds": 1200}
    wait.created_at = now - timedelta(minutes=5)

    again = await service.expire_agent_wait(wait, now=now)

    assert again == wait.created_at + timedelta(seconds=1200)
