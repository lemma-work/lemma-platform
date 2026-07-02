"""Unit tests for the request_approval session flow.

Covers the two plumbing ends the authorizer's session-approval check relies on:
the denied-tool-result payload (permission ids ride in the approval envelope)
and the APPROVE_FOR_SESSION resolution recording per-permission approvals.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
from app.core.domain.errors import DomainError
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.tools.tool_errors import approval_error_result


def test_approval_error_result_carries_permission_ids_for_destructive_denial():
    exc = DomainError(
        "Missing permission datastore.table.delete",
        code="DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
        status_code=403,
        details={"permission_ids": ["datastore.table.delete"]},
    )

    result = approval_error_result(
        exc, tool_name="pod_delete_table", args={"table": "orders"}
    )

    assert result["needs_approval"] is True
    approval = result["approval"]
    assert approval["tool_name"] == "pod_delete_table"
    assert approval["reason_code"] == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"
    assert approval["permission_ids"] == ["datastore.table.delete"]


def test_approval_error_result_without_details_still_flags_approval():
    exc = DomainError(
        "Missing permission folder.read",
        code="MISSING_WORKLOAD_RESOURCE_GRANT",
        status_code=403,
    )

    result = approval_error_result(exc, tool_name="pod_read_file", args={})

    assert result["needs_approval"] is True
    assert "permission_ids" not in result["approval"]


def test_non_approval_codes_do_not_flag():
    exc = DomainError("nope", code="INSUFFICIENT_PERMISSION", status_code=403)
    result = approval_error_result(exc, tool_name="pod_read_file", args={})
    assert "needs_approval" not in result


@pytest.mark.asyncio
async def test_approve_for_session_records_each_permission(monkeypatch):
    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        "app.core.authorization.session_approvals.record_session_approval",
        fake_record,
    )
    service = ConversationService.__new__(ConversationService)
    conversation = SimpleNamespace(id=uuid4(), agent_id=uuid4())
    user_id = uuid4()

    await service._record_session_approvals(
        conversation=conversation,
        tool_args={
            "tool_name": "pod_delete_table",
            "permission_ids": ["datastore.table.delete", "folder.delete", "", 7],
        },
        user_id=user_id,
    )

    assert [r["permission_id"] for r in recorded] == [
        "datastore.table.delete",
        "folder.delete",
    ]
    assert all(r["session_id"] == str(conversation.id) for r in recorded)
    assert all(
        r["workload_actor_id"] == f"agent:{conversation.agent_id}" for r in recorded
    )
    assert all(r["resolved_by_user_id"] == user_id for r in recorded)


@pytest.mark.asyncio
async def test_approve_for_session_defaults_to_pod_default_agent(monkeypatch):
    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        "app.core.authorization.session_approvals.record_session_approval",
        fake_record,
    )
    service = ConversationService.__new__(ConversationService)
    conversation = SimpleNamespace(id=uuid4(), agent_id=None)

    await service._record_session_approvals(
        conversation=conversation,
        tool_args={"permission_ids": ["datastore.table.delete"]},
        user_id=uuid4(),
    )

    assert recorded[0]["workload_actor_id"] == f"agent:{DEFAULT_POD_AGENT_ID}"


@pytest.mark.asyncio
async def test_approve_for_session_without_permission_ids_is_a_noop(monkeypatch):
    async def fail_record(**kwargs):
        raise AssertionError("nothing should be recorded without permission ids")

    monkeypatch.setattr(
        "app.core.authorization.session_approvals.record_session_approval",
        fail_record,
    )
    service = ConversationService.__new__(ConversationService)
    conversation = SimpleNamespace(id=uuid4(), agent_id=None)

    await service._record_session_approvals(
        conversation=conversation,
        tool_args={"tool_name": "exec_command"},
        user_id=uuid4(),
    )
