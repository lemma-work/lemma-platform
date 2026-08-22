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
from app.core.authorization.session_approvals import exact_command_permission_id
from app.core.domain.errors import DomainError
from app.modules.agent.services.approval_reconciliation import (
    record_session_approvals,
)
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
    conversation = SimpleNamespace(id=uuid4(), agent_id=uuid4())
    user_id = uuid4()

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        tool_args={
            "tool_name": "pod_delete_table",
            "permission_ids": ["datastore.table.delete", "folder.delete", "", 7],
        },
        user_id=user_id,
    )

    # An exact-command entry is always recorded first (alongside any structured
    # permission ids), so a later identical request_approval call can auto-run
    # without re-prompting even for tools with no permission_ids at all.
    assert recorded[0]["permission_id"].startswith("exact_command:pod_delete_table:")
    assert [r["permission_id"] for r in recorded[1:]] == [
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
    conversation = SimpleNamespace(id=uuid4(), agent_id=None)

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        tool_args={"permission_ids": ["datastore.table.delete"]},
        user_id=uuid4(),
    )

    assert recorded[0]["workload_actor_id"] == f"agent:{DEFAULT_POD_AGENT_ID}"


@pytest.mark.asyncio
async def test_approve_for_session_without_permission_ids_records_only_exact_command(
    monkeypatch,
):
    """exec_command has no structured permission to unlock as a category — the
    only reuse it gets is the exact-command key, never a broader grant."""
    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        "app.core.authorization.session_approvals.record_session_approval",
        fake_record,
    )
    conversation = SimpleNamespace(id=uuid4(), agent_id=None)

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        tool_args={"tool_name": "exec_command", "args": {"cmd": "ls -la"}},
        user_id=uuid4(),
    )

    assert len(recorded) == 1
    assert recorded[0]["permission_id"] == exact_command_permission_id(
        "exec_command", {"cmd": "ls -la"}
    )


@pytest.mark.asyncio
async def test_approve_for_session_without_tool_name_records_nothing(monkeypatch):
    async def fail_record(**kwargs):
        raise AssertionError("nothing to key an exact-command approval on")

    monkeypatch.setattr(
        "app.core.authorization.session_approvals.record_session_approval",
        fail_record,
    )
    conversation = SimpleNamespace(id=uuid4(), agent_id=None)

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        tool_args={},
        user_id=uuid4(),
    )


class TestTheSecondDenialOfTheSameCall:
    """DEV-ACCESS-002: approve -> still denied -> approve -> still denied.

    Reading a table needs two permissions and the authorizer stops at the first
    missing one, so the agent hits a second denial for the *same* call. The
    exact-command key is tool-name-plus-args, so that second attempt matches the
    session approval recorded by the first and reaches the auto-approve path --
    which used to execute the tool and return, swallowing the request without
    ever recording the new permission. The agent hit the same denial on its own
    next attempt, forever.

    The fix is that the fast path now *requires* every permission the call names,
    rather than recording them. A call asking for something not yet granted
    falls through to the pause, so the user is asked once and
    `record_session_approvals` writes it with a human behind it.
    """

    @staticmethod
    def _deps(conversation_id, user_id):
        return SimpleNamespace(
            conversation_id=conversation_id,
            user_id=user_id,
            workload_id=None,
            agent_run_id=uuid4(),
            supports_pause_signal=True,
        )

    @staticmethod
    def _patch(monkeypatch, *, granted: set[str], executed: list[str]):
        async def has_session_approval(*, session_id, workload_actor_id, permission_id):
            return permission_id in granted

        async def record_session_approval(**kwargs):  # pragma: no cover
            raise AssertionError(
                "the auto-approve path must never mint a grant; only a resolved "
                f"approval may do that (tried to record {kwargs['permission_id']!r})"
            )

        async def execute_as_user(self, *, deps, tool_name, args):
            executed.append(tool_name)
            return {"ok": True}

        monkeypatch.setattr(
            "app.core.authorization.session_approvals.has_session_approval",
            has_session_approval,
        )
        monkeypatch.setattr(
            "app.core.authorization.session_approvals.record_session_approval",
            record_session_approval,
        )
        monkeypatch.setattr(
            "app.modules.agent.tools.approval.executor.ApprovalExecutor.execute_as_user",
            execute_as_user,
        )

    @pytest.mark.asyncio
    async def test_a_new_permission_falls_through_to_the_pause(self, monkeypatch):
        """The loop, closed: the request is no longer swallowed."""
        from app.core.authorization.session_approvals import exact_command_permission_id
        from app.modules.agent.tools.user_interaction import (
            pydantic_adapter as approvals,
        )

        args = {"table": "orders"}
        executed: list[str] = []
        # The user approved this exact call earlier, but has never been asked
        # about the permission the second denial named.
        self._patch(
            monkeypatch,
            granted={exact_command_permission_id("pod_get_records", args)},
            executed=executed,
        )

        result = await approvals._run_if_exact_match_already_approved(
            deps=self._deps(uuid4(), uuid4()),
            tool_name="pod_get_records",
            args=args,
            permission_ids=["datastore.record.read"],
        )

        assert result is None, "must pause and ask, not silently self-approve"
        assert executed == [], "nothing may run before the user has been asked"

    @pytest.mark.asyncio
    async def test_a_fully_granted_call_still_auto_executes(self, monkeypatch):
        """Once everything is granted, the repeat runs without re-prompting."""
        from app.core.authorization.session_approvals import exact_command_permission_id
        from app.modules.agent.tools.user_interaction import (
            pydantic_adapter as approvals,
        )

        args = {"table": "orders"}
        executed: list[str] = []
        self._patch(
            monkeypatch,
            granted={
                exact_command_permission_id("pod_get_records", args),
                "datastore.record.read",
            },
            executed=executed,
        )

        result = await approvals._run_if_exact_match_already_approved(
            deps=self._deps(uuid4(), uuid4()),
            tool_name="pod_get_records",
            args=args,
            permission_ids=["datastore.record.read"],
        )

        assert result is not None and result.executed is True
        assert executed == ["pod_get_records"]

    @pytest.mark.asyncio
    async def test_the_model_cannot_mint_a_grant_it_was_never_given(self, monkeypatch):
        """The escalation this path must not allow.

        `permission_ids` is a tool argument, so a model that has read a poisoned
        page writes whatever that page said. One approval of a harmless
        `exec_command` must not become `pod.delete` without a card.
        """
        from app.core.authorization.session_approvals import exact_command_permission_id
        from app.modules.agent.tools.user_interaction import (
            pydantic_adapter as approvals,
        )

        args = {"cmd": "echo hi"}
        executed: list[str] = []
        self._patch(
            monkeypatch,
            granted={exact_command_permission_id("exec_command", args)},
            executed=executed,
        )

        result = await approvals._run_if_exact_match_already_approved(
            deps=self._deps(uuid4(), uuid4()),
            tool_name="exec_command",
            args=args,
            permission_ids=["pod.delete", "datastore.table.delete"],
        )

        # `_patch` raises if anything tries to record a grant, so reaching here
        # at all is half the assertion.
        assert result is None
        assert executed == []

    @pytest.mark.asyncio
    async def test_a_call_carrying_no_permissions_still_auto_executes(
        self, monkeypatch
    ):
        """exec_command has no structured permission -- the exact-command key is
        the whole grant, and that path must keep working."""
        from app.core.authorization.session_approvals import exact_command_permission_id
        from app.modules.agent.tools.user_interaction import (
            pydantic_adapter as approvals,
        )

        args = {"cmd": "ls"}
        executed: list[str] = []
        self._patch(
            monkeypatch,
            granted={exact_command_permission_id("exec_command", args)},
            executed=executed,
        )

        result = await approvals._run_if_exact_match_already_approved(
            deps=self._deps(uuid4(), uuid4()),
            tool_name="exec_command",
            args=args,
            permission_ids=None,
        )

        assert result is not None and result.executed is True
        assert executed == ["exec_command"]
