"""An approved call runs with the person's authority -- through the real executor.

Approval is how an agent does what its own grants do not allow: the agent is
refused, it asks, a person approves, and that one call runs as the person. Every
earlier test of this path replaced ``execute_as_user`` with a stand-in, which is
how #597 could break it outright and ship: from then on an approved call from a
named agent was authorised as a placeholder agent with no grants and failed
every time (PS-AGENT-020, seen in development on 2026-09-28).

So nothing here is faked below the tool: the real dispatcher, the real pod
toolset, the real authorization service, a real database.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.approval_reconciliation import (
    record_session_approvals,
)
from app.modules.agent.tools.approval.executor import ApprovalExecutor
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher
from app.modules.test_support.e2e.function_helpers import create_table

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_TABLE = "chores"


def _uow_factory() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


class _Pod:
    """A pod with a named agent that holds no grants, and two rows."""

    def __init__(self, scenario, agent: dict, conversation_id: UUID) -> None:
        self.scenario = scenario
        self.agent = agent
        self.conversation_id = conversation_id

    @property
    def pod_id(self) -> str:
        return self.scenario.pod_id

    async def rows(self) -> dict[str, str]:
        response = await self.scenario.owner_client.get(
            f"/pods/{self.pod_id}/datastore/tables/{_TABLE}/records"
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return {row["title"]: row["id"] for row in response.json()["items"]}

    async def new_conversation(self) -> UUID:
        response = await self.scenario.owner_client.post(
            f"/pods/{self.pod_id}/conversations",
            json={"agent_name": self.agent["name"], "title": "Chores", "type": "CHAT"},
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text
        return UUID(response.json()["id"])

    def context(
        self, *, conversation_id: UUID | None = None, user_id: str | None = None
    ) -> ConversationContext:
        """The agent's run context, as a real run builds it."""
        return ConversationContext(
            user_id=UUID(user_id or self.scenario.owner_user["id"]),
            org_id=UUID(self.scenario.org_id),
            pod_id=UUID(self.pod_id),
            conversation_id=conversation_id or self.conversation_id,
            agent_name=self.agent["name"],
            workload_type="agent",
            workload_id=UUID(self.agent["id"]),
            is_pod_default_agent=False,
        )


@pytest.fixture
async def pod(scenario) -> _Pod:
    await scenario.create_org_with_pod(name_prefix="Approvals")
    response = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/agents",
        json={
            "name": f"chore-agent-{uuid4().hex[:8]}",
            "instruction": "Tidy the chores table when asked.",
            "toolsets": ["POD", "USER_INTERACTION"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    agent = response.json()
    await create_table(scenario.owner_client, scenario.pod_id, _TABLE, enable_rls=False)
    for title in ("first", "second", "third"):
        created = await scenario.owner_client.post(
            f"/pods/{scenario.pod_id}/datastore/tables/{_TABLE}/records",
            json={"data": {"title": title}},
        )
        assert created.status_code == status.HTTP_201_CREATED, created.text
    conversation = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={"agent_name": agent["name"], "title": "Chores", "type": "CHAT"},
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    return _Pod(scenario, agent, UUID(conversation.json()["id"]))


def _delete(row_id: str) -> dict[str, object]:
    return {"action": "delete", "table_name": _TABLE, "record_id": row_id}


async def _as_the_agent(ctx: ConversationContext, args: dict[str, object]) -> dict:
    """Call ``pod_write_record`` the way a run does: with the agent's own authority."""
    async with _uow_factory()() as uow:
        conversation = await ConversationRepository(uow).get_conversation(
            ctx.conversation_id, include_runs=False
        )
        assert conversation is not None and conversation.agent_id is not None
        agent = await AgentRepository(uow).get(conversation.agent_id)
    result = await AgentToolDispatcher(_uow_factory()).call_tool(
        agent=agent,
        conversation=conversation,
        ctx=ctx,
        name="pod_write_record",
        arguments=args,
    )
    assert isinstance(result, dict)
    return result


async def _approved(ctx: ConversationContext, args: dict[str, object]) -> dict:
    result = await ApprovalExecutor(_uow_factory()).execute_as_user(
        deps=ctx, tool_name="pod_write_record", args=args, approval_id="call-e2e"
    )
    assert isinstance(result, dict)
    return result


async def test_the_agent_on_its_own_is_refused_and_asks(pod: _Pod) -> None:
    rows = await pod.rows()

    result = await _as_the_agent(pod.context(), _delete(rows["first"]))

    assert result["needs_approval"] is True
    assert result["code"] == "MISSING_WORKLOAD_RESOURCE_GRANT"
    assert "first" in await pod.rows()


async def test_an_approved_call_runs_as_the_person_and_leaves_nothing_behind(
    pod: _Pod,
) -> None:
    """PS-AGENT-020: the described action happens -- and only that one."""
    rows = await pod.rows()

    approved = await _approved(pod.context(), _delete(rows["first"]))

    assert approved == {"success": True, "deleted": True}
    assert set(await pod.rows()) == {"second", "third"}
    # No standing access: the agent's next call is judged on its own grants.
    again = await _as_the_agent(pod.context(), _delete(rows["second"]))
    assert again["needs_approval"] is True
    assert "second" in await pod.rows()


async def test_an_approval_never_reaches_beyond_the_person(pod: _Pod) -> None:
    """The person lends their authority, not more: a viewer's approval deletes
    nothing, and says why."""
    viewer = await pod.scenario.create_user("approval-viewer")
    await pod.scenario.add_user_to_pod(user=viewer, role="POD_VIEWER")
    rows = await pod.rows()

    result = await _approved(pod.context(user_id=viewer["id"]), _delete(rows["first"]))

    assert result.get("success") is False
    assert "needs_approval" not in result
    assert "first" in await pod.rows()


async def test_a_session_approval_lets_the_agent_repeat_it_in_that_conversation_only(
    pod: _Pod,
) -> None:
    """APPROVE_FOR_SESSION records the action type for the agent in this
    conversation -- under the same key the agent's own context is checked by.

    The authorizer names only the first permission a call is missing, so the
    person is asked once per permission (DEV-ACCESS-002): a delete that needs
    both ``datastore.table.read`` and ``datastore.record.write`` takes two
    approvals, then runs. Modelled here as a run would see it.
    """
    rows = await pod.rows()
    approved_permissions: set[str] = set()
    result: dict = {}
    for _ in range(4):
        result = await _as_the_agent(pod.context(), _delete(rows["first"]))
        if not result.get("needs_approval"):
            break
        approval = result["approval"]
        assert isinstance(approval, dict)
        missing = set(approval.get("permission_ids") or [])
        assert missing - approved_permissions, (
            f"approved {approved_permissions} and still refused for {missing}: "
            "the approval was recorded under a key the check does not read"
        )
        approved_permissions |= missing
        await record_session_approvals(
            conversation_id=pod.conversation_id,
            agent_id=UUID(pod.agent["id"]),
            tool_args={
                "tool_name": approval["tool_name"],
                "args": approval["args"],
                "permission_ids": sorted(missing),
            },
            user_id=UUID(pod.scenario.owner_user["id"]),
        )

    assert result == {"success": True, "deleted": True}
    assert approved_permissions == {"datastore.table.read", "datastore.record.write"}
    # The approvals hold for the rest of this conversation...
    again = await _as_the_agent(pod.context(), _delete(rows["second"]))
    assert again == {"success": True, "deleted": True}
    # ...and nowhere else.
    elsewhere = await _as_the_agent(
        pod.context(conversation_id=await pod.new_conversation()),
        _delete(rows["third"]),
    )
    assert elsewhere["needs_approval"] is True
    assert "third" in await pod.rows()
