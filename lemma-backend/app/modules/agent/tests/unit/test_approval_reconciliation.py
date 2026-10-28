"""Approvals must survive the deadline of whoever submitted them.

Resolving an approval used to run inside the HTTP request or the platform
webhook that carried the decision. Because resolving *resumes the agent*, a
slow resume outlived the caller's deadline: the decision had already committed,
the caller timed out, and the resume was cancelled halfway. These tests pin the
split that fixed it -- the decision commits with an ``agent.approval.execute``
item, the worker does the rest -- and the cases that must deliberately stay
inline.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4, uuid7

import pytest

from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.events.work_kinds import AGENT_APPROVAL_EXECUTE, approval_key
from app.modules.agent.services import approval_reconciliation
from app.modules.agent.services.approval_reconciliation import (
    ApprovalExecution,
    runs_in_worker,
    stage_approval_execution,
)

pytestmark = pytest.mark.unit


class TestTheItem:
    def test_the_key_is_derived_from_the_approval(self) -> None:
        """Deterministic on purpose: a double-clicked Approve, or a retry, must
        coalesce with the queued item rather than stack a second."""
        conversation_id = uuid7()

        assert approval_key(conversation_id, "call-1") == approval_key(
            conversation_id, "call-1"
        )
        assert approval_key(conversation_id, "call-1") != approval_key(
            conversation_id, "call-2"
        )

    @pytest.mark.asyncio
    async def test_staging_carries_everything_the_worker_needs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The worker re-loads the decision from the database, so the payload is
        only identity; the dedup and concurrency keys are both the approval's,
        so a second item can never run beside a live execution."""
        captured: dict = {}

        class _Writer:
            async def add(self, kind, ns, payload, **kwargs):
                captured.update(kind=kind, ns=ns, payload=payload, **kwargs)

        monkeypatch.setattr(
            approval_reconciliation.WorkWriter,
            "of",
            staticmethod(lambda uow: _Writer()),
        )
        conversation_id, user_id, pod_id = uuid7(), uuid4(), uuid4()

        await stage_approval_execution(
            SimpleNamespace(),  # type: ignore[arg-type]
            conversation_id=conversation_id,
            approval_id="call-1",
            user_id=user_id,
            pod_id=pod_id,
        )

        assert captured["kind"] == AGENT_APPROVAL_EXECUTE
        assert captured["ns"] == f"pod:{pod_id}"
        assert captured["payload"] == ApprovalExecution(
            conversation_id=conversation_id,
            approval_id="call-1",
            user_id=user_id,
            pod_id=pod_id,
        )
        key = approval_key(conversation_id, "call-1")
        assert captured["dedup_key"] == key
        assert captured["concurrency_key"] == key


def _decided(
    kind: PauseKind, decision: AgentRunApprovalDecision, *, returned: bool = False
) -> Pause:
    pause = Pause(
        conversation_id=uuid4(),
        run_id=uuid4(),
        pod_id=uuid4(),
        tool_call_id="call-9",
        tool_name="request_approval",
        kind=kind,
        returned=returned,
    )
    pause.resolve(decision, by=uuid4())
    return pause


class TestWhatRunsInTheWorker:
    """Only work that can outlive a caller's deadline belongs in a worker."""

    def test_an_approved_tool_runs_in_the_worker(self) -> None:
        assert runs_in_worker(
            _decided(PauseKind.APPROVAL, AgentRunApprovalDecision.APPROVE_ONCE)
        )

    def test_a_denial_stays_inline(self) -> None:
        """A denial runs nothing; deferring it would only delay the agent."""
        assert not runs_in_worker(
            _decided(PauseKind.APPROVAL, AgentRunApprovalDecision.DENY)
        )

    def test_ask_user_stays_inline(self) -> None:
        """Answers perform no external work at all."""
        assert not runs_in_worker(
            _decided(PauseKind.QUESTION, AgentRunApprovalDecision.APPROVE_ONCE)
        )

    def test_an_already_executed_approval_stays_inline(self) -> None:
        """A tool return means the tool already ran, so all that is left is the
        cheap self-heal -- and handing it to a worker risks running it twice."""
        assert not runs_in_worker(
            _decided(
                PauseKind.APPROVAL,
                AgentRunApprovalDecision.APPROVE_ONCE,
                returned=True,
            )
        )

    def test_an_agent_host_permission_or_a_budget_stays_inline(self) -> None:
        """A permission queues a command and pokes a host -- milliseconds --
        while someone watches the agent wait; a budget runs no tool at all."""
        for kind in (PauseKind.HOST_PERMISSION, PauseKind.BUDGET):
            assert not runs_in_worker(
                _decided(kind, AgentRunApprovalDecision.APPROVE_ONCE)
            )
