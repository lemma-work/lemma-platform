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
        {
            "wait_type": WorkflowRunWaitType.FUNCTION,
            "external_ref": fr_id,
            "output": {"x": 1},
        }
    ]


async def test_resume_for_function_run_falls_back_to_adapter_when_output_none(
    monkeypatch,
):
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
        now=datetime.now(timezone.utc),
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
        now=datetime.now(timezone.utc),
        agent_status={"status": "WAITING", "wait_reason": "SNOOZE"},
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
        datetime.now(timezone.utc) - timedelta(hours=6),
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
        datetime.now(timezone.utc) - timedelta(hours=6),
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
        datetime.now(timezone.utc) - timedelta(hours=6),
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
        wait,
        datetime.now(timezone.utc) - timedelta(hours=6),
        now=datetime.now(timezone.utc),
    )

    assert handled is True
    assert engine.stopped == [wait]


async def test_wait_within_the_ceiling_is_left_alone():
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.FUNCTION, "run-recent")
    wait.created_at = datetime.now(timezone.utc) - timedelta(minutes=20)

    handled = await service._expire_overdue_wait(
        wait,
        datetime.now(timezone.utc) - timedelta(hours=6),
        now=datetime.now(timezone.utc),
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
        now=datetime.now(timezone.utc),
    )

    assert handled is False
    assert engine.failures == []


async def test_a_form_wait_gets_the_human_ceiling_not_the_machine_one():
    """A form is answered by a person, so the machine ceiling must not apply.

    The reason it needs saying: the human ceiling used to be reachable only
    through an *agent conversation's* `wait_reason`, never through the wait
    type a FORM node creates.
    """
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.HUMAN, None)
    wait.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    handled = await service._expire_overdue_wait(
        wait,
        datetime.now(timezone.utc) - timedelta(hours=6),
        now=datetime.now(timezone.utc),
    )

    assert handled is False, "a form waiting two days is ordinary, not a fault"
    assert engine.failures == []


async def test_an_abandoned_form_wait_is_eventually_resolved():
    """PS-FLOW-011: resolve a stuck run rather than leaving it waiting forever.

    A form assigned to someone who leaves used to hold its run WAITING with no
    ceiling at all — and the inbox notification expires at 72h, so the only
    visible trace of it disappeared while the run stayed open.
    """
    engine = FakeEngine()
    service = RunResumeService(engine)

    wait = _wait(WorkflowRunWaitType.HUMAN, None)
    wait.created_at = datetime.now(timezone.utc) - timedelta(days=90)

    handled = await service._expire_overdue_wait(
        wait,
        datetime.now(timezone.utc) - timedelta(hours=6),
        now=datetime.now(timezone.utc),
    )

    assert handled is True
    assert len(engine.failures) == 1
    assert "Nobody answered" in engine.failures[0]["error"]


async def test_the_sweep_asks_for_human_waits_and_polls_nothing_for_them():
    """The batch must include HUMAN, and must not try to reconcile one.

    There is no source of truth to ask: a form is resolved by somebody
    answering it. Selecting the type without this would send a form wait into
    the function adapter with a null external ref.
    """
    engine = FakeEngine()
    requested: list[list] = []

    class _WaitRepo:
        async def list_active_older_than(self, *, wait_types, created_before, limit):
            requested.append(list(wait_types))
            wait = _wait(WorkflowRunWaitType.HUMAN, None)
            wait.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
            return [wait]

    class _NeverCalled:
        async def get_run_status(self, run_id):
            raise AssertionError("a form wait has nothing to poll")

    engine.wait_repo = _WaitRepo()
    engine.function_adapter = _NeverCalled()
    engine.agent_adapter = _NeverCalled()

    acted = await RunResumeService(engine).reconcile_stale_waits()

    assert acted == 0
    assert WorkflowRunWaitType.HUMAN in requested[0]
    assert engine.failures == []
