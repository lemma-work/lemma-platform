"""What happens to the approval a dying worker was in the middle of running.

Approving a `request_approval` runs the wrapped tool with the user's authority,
so it must happen at most once -- and the guard for that is a one-shot claim
row, deliberately never handed back. The gap is what the claim leaves behind
when its holder dies: an approval nothing can finish, because the tool return is
the only record the resumed run replays.

Left alone, the next reconcile skipped the execution (the claim is gone),
`unresolved_pausing_call_ids` reported nothing outstanding (it counts a decision
as an answer), and the resume run rebuilt a history with the pausing call
dropped. The agent carried on as though it had never asked, and the person
believed their approval had been carried out -- PS-AGENT-020 says an approval
means the described action is performed.

So the two halves here: while the holder could still be working, the pause is
left alone and the card stays up; once it definitively cannot be, the transcript
says so.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4, uuid7

import pytest

from app.modules.agent.domain.entities import Agent, Message
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRuntimeConfig,
    MessageKind,
    MessageRole,
)
from app.modules.agent.services.conversation_approvals import ApprovalCoordinator
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)
from app.modules.agent.services.pause_resume import PauseResume

pytestmark = pytest.mark.unit

_APPROVAL_ID = "call-approved"
_POD_ID = uuid7()


class _Uow:
    def collect_events(self, _events) -> None:
        return None

    def after_commit(self, _callback) -> None:
        return None

    async def commit(self) -> None:
        return None


class _Repository:
    """One conversation whose approval was claimed and never returned."""

    def __init__(self, *, claim_expired: bool) -> None:
        self.claim_expired = claim_expired
        self.conversation_id = uuid7()
        self.run_id = uuid7()
        self.call = Message.create(
            conversation_id=self.conversation_id,
            sequence=1,
            agent_run_id=self.run_id,
            role=MessageRole.ASSISTANT,
            kind=MessageKind.TOOL_CALL,
            tool_name="request_approval",
            tool_call_id=_APPROVAL_ID,
            tool_args={"tool_name": "exec_command", "args": {"command": "deploy"}},
        )
        self.appended: list[Message] = []
        self.created_runs = 0

    # The claim is already held by the attempt that died, and it is one-shot.
    async def claim_approval_execution(self, **_kwargs) -> bool:
        return False

    async def approval_execution_claim_expired(
        self, *, conversation_id, approval_id, stale_after_seconds
    ) -> bool:
        del conversation_id, approval_id
        assert stale_after_seconds > 0
        return self.claim_expired

    async def get_approval_decision(self, **_kwargs):
        return (AgentRunApprovalDecision.APPROVE_ONCE, {})

    async def get_tool_call(self, **_kwargs):
        return self.call

    async def get_tool_return(self, **_kwargs):
        return next(
            (m for m in self.appended if m.kind == MessageKind.TOOL_RETURN), None
        )

    async def append_message(self, *, conversation_id, agent_run_id, draft):
        message = Message.create(
            conversation_id=conversation_id,
            sequence=len(self.appended) + 2,
            agent_run_id=agent_run_id,
            role=MessageRole.ASSISTANT,
            kind=draft.kind,
            tool_name=draft.tool_name,
            tool_call_id=draft.tool_call_id,
            tool_result=draft.tool_result,
        )
        self.appended.append(message)
        return message

    async def get_agent_run(self, _run_id):
        # The paused run finished when it paused: an in-process pause ends its
        # run, and "still running" is the remote-harness park this is not.
        return None

    async def lock_conversation(self, _conversation_id) -> None:
        return None

    async def unresolved_pausing_call_ids(self, **_kwargs) -> list[str]:
        # The real query counts a call with a decision row as answered, which is
        # exactly why the resume used to start with this pause unrecorded.
        return []

    async def get_active_agent_run_for_update(self, _conversation_id):
        return None

    async def create_agent_run(self, **_kwargs):
        self.created_runs += 1
        return SimpleNamespace(id=uuid7())


class _AgentRepository:
    """The one agent the resume run would be started for."""

    def __init__(self, pod_id) -> None:
        self.agent = Agent(
            pod_id=pod_id,
            user_id=uuid4(),
            name="deployer",
            instruction="Deploy things.",
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        )

    async def get(self, _agent_id):
        return self.agent


def _coordinator(monkeypatch: pytest.MonkeyPatch, *, claim_expired: bool):
    repository = _Repository(claim_expired=claim_expired)
    uow = _Uow()
    monkeypatch.setattr(
        "app.modules.agent.services.pause_resume.publish_conversation_event",
        _noop,
    )
    coordinator = ApprovalCoordinator(
        uow,
        repository,
        ResumeToolReturnBuilder(uow, None),
        PauseResume(uow, repository, _AgentRepository(_POD_ID)),
    )
    return coordinator, repository


async def _noop(*_args, **_kwargs) -> None:
    return None


def _conversation(repository: _Repository):
    return SimpleNamespace(
        id=repository.conversation_id,
        agent_id=None,
        pod_id=_POD_ID,
        agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
    )


@pytest.mark.asyncio
async def test_a_live_execution_is_left_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An approved command can legitimately run for minutes. Until its holder is
    past the reconcile job's own ceiling it may still be working, so nothing is
    written over it and no resume run is started."""
    coordinator, repository = _coordinator(monkeypatch, claim_expired=False)

    await coordinator.resolve_user_approval_internal(
        conversation=_conversation(repository),
        approval_id=_APPROVAL_ID,
        user_id=uuid4(),
        pod_id=_POD_ID,
        decision=AgentRunApprovalDecision.APPROVE_ONCE,
    )

    assert repository.appended == []
    assert repository.created_runs == 0


@pytest.mark.asyncio
async def test_an_abandoned_execution_is_written_down_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past that ceiling nothing can still be running under the claim. The
    approval gets a terminal return saying so, which is what the resumed run
    replays -- rather than resuming with the request missing entirely."""
    coordinator, repository = _coordinator(monkeypatch, claim_expired=True)

    await coordinator.resolve_user_approval_internal(
        conversation=_conversation(repository),
        approval_id=_APPROVAL_ID,
        user_id=uuid4(),
        pod_id=_POD_ID,
        decision=AgentRunApprovalDecision.APPROVE_ONCE,
    )

    returns = [m for m in repository.appended if m.kind == MessageKind.TOOL_RETURN]
    assert [m.tool_call_id for m in returns] == [_APPROVAL_ID]
    result = returns[0].tool_result
    assert isinstance(result, dict), result
    assert result["success"] is False, result
    assert result["executed"] is False, result
    assert "cannot re-run" in str(result["error"]), result
    # And the turn is unblocked rather than left waiting forever.
    assert repository.created_runs == 1
