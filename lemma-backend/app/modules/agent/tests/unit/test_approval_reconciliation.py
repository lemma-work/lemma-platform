"""Approvals must survive the deadline of whoever submitted them.

Resolving an approval used to run inside the HTTP request or the platform
webhook that carried the decision. Because resolving *resumes the agent*, a
slow resume outlived the caller's deadline: the decision had already committed,
the caller timed out, and the resume was cancelled halfway. These tests pin the
split that fixed it — commit the decision, queue the rest — and the cases that
must deliberately stay inline.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4, uuid7

import pytest

from app.modules.agent.api.controllers import conversation_controller
from app.modules.agent.domain.agent_host_permissions import (
    permission_approval_tool_args,
)
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    MessageKind,
    MessageRole,
)
from app.modules.agent.services import approval_reconciliation
from app.modules.agent.services.approval_reconciliation import (
    approval_reconcile_job_id,
    pending_user_approval_messages,
    queue_approval_reconciliation,
    should_defer_approved_tool,
)
from app.modules.test_support.authz import allow_all_context


def _message(
    kind: MessageKind,
    *,
    tool_name: str,
    tool_call_id: str,
) -> Message:
    return Message.create(
        conversation_id=uuid7(),
        sequence=1,
        agent_run_id=uuid7(),
        role=MessageRole.ASSISTANT,
        kind=kind,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )


class TestJobIdentity:
    def test_the_job_id_is_derived_from_the_approval(self) -> None:
        """Deterministic on purpose: a double-clicked Approve, or a retry after a
        worker crash, must re-enqueue the same job rather than stack a second."""
        conversation_id = uuid7()

        assert approval_reconcile_job_id(
            conversation_id, "call-1"
        ) == approval_reconcile_job_id(conversation_id, "call-1")
        assert approval_reconcile_job_id(
            conversation_id, "call-1"
        ) != approval_reconcile_job_id(conversation_id, "call-2")

    @pytest.mark.asyncio
    async def test_queueing_carries_everything_the_worker_needs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The worker re-loads the decision from the database, so the payload is
        only identity — but a missing pod/user id there is an unauthorized
        resume, not a crash, which is why each is asserted."""
        captured: dict = {}

        class _Queue:
            async def enqueue(self, name, **kwargs):
                captured["name"] = name
                captured.update(kwargs)

        monkeypatch.setattr(
            approval_reconciliation, "get_streaq_job_queue", lambda: _Queue()
        )
        conversation_id, user_id, pod_id = uuid7(), uuid4(), uuid4()

        await queue_approval_reconciliation(
            conversation_id=conversation_id,
            approval_id="call-1",
            user_id=user_id,
            pod_id=pod_id,
        )

        assert captured["name"] == "reconcile_agent_approval"
        assert captured["context"] == {
            "conversation_id": str(conversation_id),
            "approval_id": "call-1",
            "user_id": str(user_id),
            "pod_id": str(pod_id),
        }
        assert captured["_job_id"] == approval_reconcile_job_id(
            conversation_id, "call-1"
        )


class TestWhatGetsDeferred:
    """Only work that can outlive a caller's deadline belongs in a worker."""

    def test_an_approved_tool_is_deferred(self) -> None:
        assert should_defer_approved_tool(
            defer_reconciliation=True,
            kind="request_approval",
            tool_args={"tool_name": "exec_command"},
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            has_tool_return=False,
        )

    def test_a_denial_stays_inline(self) -> None:
        """A denial runs nothing; deferring it would only delay the agent."""
        assert not should_defer_approved_tool(
            defer_reconciliation=True,
            kind="request_approval",
            tool_args={"tool_name": "exec_command"},
            decision=AgentRunApprovalDecision.DENY,
            has_tool_return=False,
        )

    def test_ask_user_stays_inline(self) -> None:
        """Answers perform no external work at all."""
        assert not should_defer_approved_tool(
            defer_reconciliation=True,
            kind="ask_user",
            tool_args={},
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            has_tool_return=False,
        )

    def test_an_already_executed_approval_stays_inline(self) -> None:
        """A tool return means the tool already ran, so all that is left is the
        cheap self-heal — and re-queueing it risks running the tool twice."""
        assert not should_defer_approved_tool(
            defer_reconciliation=True,
            kind="request_approval",
            tool_args={"tool_name": "exec_command"},
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            has_tool_return=True,
        )

    def test_an_agent_host_permission_stays_inline(self) -> None:
        """It queues a command and pokes a host — milliseconds — while someone
        watches the agent wait for their answer."""
        assert not should_defer_approved_tool(
            defer_reconciliation=True,
            kind="request_approval",
            tool_args=permission_approval_tool_args({}, request_id="call-9"),
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            has_tool_return=False,
        )

    def test_a_caller_with_no_deadline_never_defers(self) -> None:
        """The worker job itself resolves with this off; otherwise it would
        endlessly re-queue itself."""
        assert not should_defer_approved_tool(
            defer_reconciliation=False,
            kind="request_approval",
            tool_args={"tool_name": "exec_command"},
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            has_tool_return=False,
        )


class TestPendingApprovals:
    def test_a_decided_approval_stays_listed_until_its_return_lands(self) -> None:
        """Listing by *decision* hid the card the moment Approve was clicked, so
        a worker that then died left no way to retry. Listing by tool return
        keeps it visible for the seconds the approved tool is actually running."""
        call = _message(
            MessageKind.TOOL_CALL, tool_name="request_approval", tool_call_id="c1"
        )

        assert pending_user_approval_messages([call]) == [call]

    def test_a_returned_approval_disappears(self) -> None:
        call = _message(
            MessageKind.TOOL_CALL, tool_name="request_approval", tool_call_id="c1"
        )
        returned = _message(
            MessageKind.TOOL_RETURN, tool_name="request_approval", tool_call_id="c1"
        )

        assert pending_user_approval_messages([call, returned]) == []

    def test_non_pausing_tool_calls_are_never_approvals(self) -> None:
        call = _message(
            MessageKind.TOOL_CALL, tool_name="exec_command", tool_call_id="c1"
        )

        assert pending_user_approval_messages([call]) == []


class TestControllerHandsOffTheSlowHalf:
    @pytest.mark.asyncio
    async def test_the_endpoint_asks_for_deferred_reconciliation(self) -> None:
        """Without this the browser's own timeout cancels the resume after the
        decision has already committed — the original bug."""
        captured: dict = {}

        class _Service:
            async def resolve_user_approval(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    status="queued", decision=AgentRunApprovalDecision.APPROVE_ONCE
                )

        conversation_id, pod_id = uuid7(), uuid4()
        response = await conversation_controller.resolve_approval(
            pod_id=pod_id,
            conversation_id=conversation_id,
            approval_id="call-1",
            data=SimpleNamespace(
                decision=AgentRunApprovalDecision.APPROVE_ONCE, response=None
            ),
            user=SimpleNamespace(id=uuid4()),
            service=_Service(),
            ctx=allow_all_context(),
        )

        assert captured["defer_reconciliation"] is True
        assert response.status == "queued"
        assert response.approval_id == "call-1"
