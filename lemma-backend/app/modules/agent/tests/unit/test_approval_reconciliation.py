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

from app.modules.agent.domain.agent_host_permissions import (
    permission_approval_tool_args,
)
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


class TestWhatRunsInTheWorker:
    """Only work that can outlive a caller's deadline belongs in a worker."""

    def test_an_approved_tool_runs_in_the_worker(self) -> None:
        assert runs_in_worker(
            kind="request_approval",
            tool_args={"tool_name": "exec_command"},
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            has_tool_return=False,
        )

    def test_a_denial_stays_inline(self) -> None:
        """A denial runs nothing; deferring it would only delay the agent."""
        assert not runs_in_worker(
            kind="request_approval",
            tool_args={"tool_name": "exec_command"},
            decision=AgentRunApprovalDecision.DENY,
            has_tool_return=False,
        )

    def test_ask_user_stays_inline(self) -> None:
        """Answers perform no external work at all."""
        assert not runs_in_worker(
            kind="ask_user",
            tool_args={},
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            has_tool_return=False,
        )

    def test_an_already_executed_approval_stays_inline(self) -> None:
        """A tool return means the tool already ran, so all that is left is the
        cheap self-heal -- and handing it to a worker risks running it twice."""
        assert not runs_in_worker(
            kind="request_approval",
            tool_args={"tool_name": "exec_command"},
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            has_tool_return=True,
        )

    def test_an_agent_host_permission_stays_inline(self) -> None:
        """It queues a command and pokes a host -- milliseconds -- while someone
        watches the agent wait for their answer."""
        assert not runs_in_worker(
            kind="request_approval",
            tool_args=permission_approval_tool_args({}, request_id="call-9"),
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            has_tool_return=False,
        )
