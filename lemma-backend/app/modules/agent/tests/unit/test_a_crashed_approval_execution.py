"""What happens to the approval a dying worker was in the middle of running.

Approving a `request_approval` runs the wrapped tool with the user's authority,
so it must happen at most once -- and the guard for that is a one-shot claim
row, deliberately never handed back. The gap is what the claim leaves behind
when its holder dies: an approval nothing can finish, because the tool return is
the only record the resumed run replays.

Left alone, the next reconcile skipped the execution (the claim is gone),
nothing was outstanding (a decided pause counts as answered), and the resume run rebuilt a history with the pausing call
dropped. The agent carried on as though it had never asked, and the person
believed their approval had been carried out -- PS-AGENT-020 says an approval
means the described action is performed.

Two items for one approval never run at once (the concurrency key), so an
``agent.approval.execute`` attempt that finds the claim already taken knows its
holder is gone: the lease it held expired. The transcript says so, at that
lease expiry rather than 35 minutes later, and the turn resumes.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4, uuid7

import pytest

from app.modules.agent.tests.unit.repository_fakes import (
    pause_for_call,
    wire_repositories,
)
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRuntimeConfig,
    MessageKind,
    MessageRole,
    RunTrigger,
)
from app.modules.agent.application.pauses.resume import ResumeRun
from app.modules.agent.services.approval_reconciliation import (
    unconfirmed_execution_return,
)
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.test_support.fakes import EmptyResultSession

pytestmark = pytest.mark.unit

_APPROVAL_ID = "call-approved"
_POD_ID = uuid7()


class _Uow:
    # The pod names no default runtime of its own.
    session = EmptyResultSession()

    def collect_events(self, _events) -> None:
        return None

    def after_commit(self, _callback) -> None:
        return None

    async def commit(self) -> None:
        return None


class _Repository:
    """One conversation whose approval was claimed and never returned."""

    def __init__(self) -> None:
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

        self.pause = pause_for_call(self.call)
        self.pause.resolve(AgentRunApprovalDecision.APPROVE_ONCE, by=uuid4())

    async def tool_return(self, **_kwargs):
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

    async def pending(self, *_args, **_kwargs) -> list:
        # A decided pause counts as answered, which is exactly why the resume
        # used to start with this pause unrecorded.
        return []

    async def get_active_agent_run_for_update(self, _conversation_id):
        return None

    async def create_agent_run(self, **_kwargs):
        self.created_runs += 1
        return SimpleNamespace(id=uuid7())

    async def get_conversation_status(self, _conversation_id):
        return None

    # Nothing was said while this approval was open.
    async def count_queued_user_messages(self, _run_id, **_kwargs) -> int:
        return 0

    async def claim_queued_user_messages(self, _run_id, **_kwargs) -> list:
        return []


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


def _resume_run(monkeypatch: pytest.MonkeyPatch):
    repository = _Repository()
    uow = _Uow()
    monkeypatch.setattr(
        "app.modules.agent.services.pause_resume.publish_conversation_event",
        _noop,
    )
    resume = wire_repositories(
        ResumeRun(uow, agent_repository=_AgentRepository(_POD_ID)),
        repository,
    )
    return resume, repository


async def _noop(*_args, **_kwargs) -> None:
    return None


def _conversation(repository: _Repository) -> Conversation:
    return Conversation(
        id=repository.conversation_id,
        agent_id=None,
        pod_id=_POD_ID,
        user_id=uuid7(),
        organization_id=None,
        agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        metadata={},
    )


async def _record_and_resume(resume, repository, *, claimed: bool) -> None:
    paused = repository.pause
    conversation = _conversation(repository)
    tool_return = unconfirmed_execution_return(paused, claimed=claimed)
    # The resume run is counted where it is created; staging its turn needs a
    # database this fake does not have.
    with suppress_agent_run_enqueue():
        await resume.resume(
            conversation=conversation,
            paused_run_id=paused.run_id,
            tool_call_id=_APPROVAL_ID,
            user_id=uuid4(),
            trigger=RunTrigger.RESUME,
            tool_return=tool_return,
            decided=paused,
        )


def _the_return(repository: _Repository) -> dict:
    returns = [m for m in repository.appended if m.kind == MessageKind.TOOL_RETURN]
    assert [m.tool_call_id for m in returns] == [_APPROVAL_ID]
    result = returns[0].tool_result
    assert isinstance(result, dict), result
    assert result["success"] is False, result
    assert result["executed"] is False, result
    return result


@pytest.mark.asyncio
async def test_an_abandoned_execution_is_written_down_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim was taken and its holder lost its lease: the tool may have
    run. The approval gets a terminal return saying so, which is what the
    resumed run replays -- rather than resuming with the request missing."""
    resume, repository = _resume_run(monkeypatch)

    await _record_and_resume(resume, repository, claimed=True)

    assert "cannot re-run" in str(_the_return(repository)["error"])
    # And the turn is unblocked rather than left waiting forever.
    assert repository.created_runs == 1


@pytest.mark.asyncio
async def test_an_execution_that_never_started_says_nothing_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its item went dead before the claim: nothing ran, and it says so."""
    resume, repository = _resume_run(monkeypatch)

    await _record_and_resume(resume, repository, claimed=False)

    assert "did not run" in str(_the_return(repository)["error"])
    assert repository.created_runs == 1
