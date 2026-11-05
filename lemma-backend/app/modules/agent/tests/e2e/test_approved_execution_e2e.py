"""An approved call runs as the agent, lent what it was refused -- through the
real executor.

Approval is how an agent does what its own grants do not allow: the agent is
refused, it asks, a person approves, and that one call runs with the agent's
own delegated context plus exactly the permissions the server refused it for,
under the person's ceiling. Every earlier test of this path replaced
``execute_as_user`` with a stand-in, which is how #597 could break it outright
and ship: from then on an approved call from a named agent was authorised as a
placeholder agent with no grants and failed every time (PS-AGENT-020, seen in
development on 2026-09-28). And until H7 it ran as the person, whole: an
approval of one row's delete lent the call everything the person could do.

So nothing here is faked below the tool: the real dispatcher, the real pod
toolset, the real authorization service, a real database.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.session_approvals import record_session_approval
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.context import ApprovedExecution
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.approval_reconciliation import (
    record_session_approvals,
)
from app.modules.agent.tools.approval.executor import ApprovalExecutor
from app.modules.agent.tools.approval_denials import (
    lent_to_the_sandbox,
    sandbox_refusals,
    show_card,
)
from app.modules.agent.tools.authority import (
    conversation_workload_actor_id,
    tool_authorization_context,
    workload_actor_id,
)
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher
from app.modules.test_support.e2e.function_helpers import create_table
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

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


async def _card(
    ctx: ConversationContext, tool_name: str, args: dict[str, object]
) -> str:
    """The run pausing for a card: what is pending now is what it lends."""
    approval_id = f"call-{uuid4().hex[:12]}"
    await show_card(
        conversation_id=ctx.conversation_id,
        approval_id=approval_id,
        person_user_id=ctx.user_id,
        workload_actor_id=workload_actor_id(ctx),
        tool_name=tool_name,
        args=args,
    )
    return approval_id


async def _approved(ctx: ConversationContext, args: dict[str, object]) -> dict:
    result = await ApprovalExecutor(_uow_factory()).execute_as_user(
        deps=ctx,
        tool_name="pod_write_record",
        args=args,
        approval_id=await _card(ctx, "pod_write_record", args),
    )
    assert isinstance(result, dict)
    return result


async def test_the_agent_on_its_own_is_refused_and_asks(pod: _Pod) -> None:
    rows = await pod.rows()

    result = await _as_the_agent(pod.context(), _delete(rows["first"]))

    assert result["needs_approval"] is True
    assert result["code"] == "MISSING_WORKLOAD_RESOURCE_GRANT"
    assert "first" in await pod.rows()


async def test_one_approval_carries_a_record_delete(pod: _Pod) -> None:
    """PS-AGENT-020, H7 and one card per action.

    The authorizer stops at the first missing permission, so a delete used to
    be refused for reading the table, approved, run, refused for the delete,
    and approved again. The refusal now names everything the delete is missing,
    the approval lends exactly that, and the described action happens -- and
    only that one.
    """
    rows = await pod.rows()
    first = _delete(rows["first"])

    refused = await _as_the_agent(pod.context(), first)
    assert refused["approval"]["permission_ids"] == [
        "datastore.table.read",
        "datastore.record.delete",
    ]

    approved = await _approved(pod.context(), first)

    assert approved == {"success": True, "deleted": True}
    assert set(await pod.rows()) == {"second", "third"}
    # No standing access: the agent's next call is judged on its own grants.
    again = await _as_the_agent(pod.context(), _delete(rows["second"]))
    assert again["needs_approval"] is True
    assert "second" in await pod.rows()


async def test_approving_a_call_the_server_never_refused_lends_nothing(
    pod: _Pod,
) -> None:
    """H7: an approval lends what the server refused, not the person. A call
    the agent asked to have approved without ever being refused runs with the
    agent's own authority -- which here deletes nothing."""
    rows = await pod.rows()

    result = await _approved(pod.context(), _delete(rows["first"]))

    assert result.get("success") is False, result
    assert result["needs_approval"] is True
    assert "first" in await pod.rows()


async def _approved_context(pod: _Pod, *, user_id: str, lent: set[str]) -> Context:
    """The authorization context an approved call of this agent runs under."""
    deps = pod.context(user_id=user_id).model_copy(
        update={
            "approved_execution": ApprovedExecution(
                approver_user_id=UUID(user_id),
                agent_id=UUID(pod.agent["id"]),
                conversation_id=pod.conversation_id,
                tool_name="pod_tables",
                permission_ids=frozenset(lent),
            )
        }
    )
    async with _uow_factory()() as uow:
        return await tool_authorization_context(uow, deps)


async def _table_ref(pod: _Pod) -> ResourceRef:
    response = await pod.scenario.owner_client.get(
        f"/pods/{pod.pod_id}/datastore/tables/{_TABLE}"
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return ResourceRef(
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id=UUID(response.json()["id"]),
        pod_id=UUID(pod.pod_id),
    )


async def test_an_approved_call_is_still_the_agent_acting(pod: _Pod) -> None:
    """H7: approving one destructive action lends that action, not the rest of
    what the owner may do -- deleting the pod stays refused, as it would be for
    the agent unapproved, though its owner could."""
    table = await _table_ref(pod)
    ctx = await _approved_context(
        pod,
        user_id=pod.scenario.owner_user["id"],
        lent={"datastore.table.read", "datastore.table.delete"},
    )

    assert ctx.is_named_workload, "an approved call is the agent acting"
    await ctx.require("datastore.table.delete", table)
    with pytest.raises(DomainError) as refused:
        await ctx.require("pod.delete", ResourceRef.pod(UUID(pod.pod_id)))
    assert refused.value.code == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"


async def test_an_approved_destructive_call_stays_under_the_person_s_ceiling(
    pod: _Pod,
) -> None:
    """A person lends only what they hold: lent ``datastore.table.delete`` by a
    viewer, the agent is still refused it -- for the person, not the grant."""
    viewer = await pod.scenario.create_user("approval-ceiling-viewer")
    await pod.scenario.add_user_to_pod(user=viewer, role="POD_VIEWER")
    table = await _table_ref(pod)
    ctx = await _approved_context(
        pod,
        user_id=viewer["id"],
        lent={"datastore.table.read", "datastore.table.delete"},
    )

    with pytest.raises(DomainError) as refused:
        await ctx.require("datastore.table.delete", table)
    assert refused.value.code == "DELEGATION_EXCEEDS_INVOKER"


async def test_an_approval_never_reaches_beyond_the_person(pod: _Pod) -> None:
    """The person lends their authority, not more: a viewer's approval deletes
    nothing, and says why."""
    viewer = await pod.scenario.create_user("approval-viewer")
    await pod.scenario.add_user_to_pod(user=viewer, role="POD_VIEWER")
    rows = await pod.rows()
    as_the_viewer = pod.context(user_id=viewer["id"])
    # Refused for the read first -- a viewer may read, so that much is lent.
    await _as_the_agent(as_the_viewer, _delete(rows["first"]))

    result = await _approved(as_the_viewer, _delete(rows["first"]))

    assert result.get("success") is False
    assert "needs_approval" not in result
    assert "first" in await pod.rows()


async def test_a_session_approval_lets_the_agent_repeat_it_in_that_conversation_only(
    pod: _Pod,
) -> None:
    """APPROVE_FOR_SESSION records the action type for the agent in this
    conversation -- under the same key the agent's own context is checked by.

    A delete needs both ``datastore.table.read`` and ``datastore.record.delete``;
    the refusal names both, so the person is asked once (DEV-ACCESS-002 used to
    take an approval per permission). Modelled here as a run would see it.
    """
    rows = await pod.rows()
    approved_permissions: set[str] = set()
    result: dict = {}
    asked = 0
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
        asked += 1
        await record_session_approvals(
            conversation_id=pod.conversation_id,
            agent_id=UUID(pod.agent["id"]),
            pod_id=UUID(pod.pod_id),
            tool_args={
                "tool_name": approval["tool_name"],
                "args": approval["args"],
                "permission_ids": sorted(missing),
            },
            user_id=UUID(pod.scenario.owner_user["id"]),
            approval_id=await _card(
                pod.context(), approval["tool_name"], approval["args"]
            ),
        )

    assert result == {"success": True, "deleted": True}
    assert approved_permissions == {"datastore.table.read", "datastore.record.delete"}
    assert asked == 1, "one action, one card"
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


class _AssistantShell:
    """The pod's assistant's sandbox token, as its workspace is minted -- in
    the VM sandbox, or on the person's Mac through the Agent Host."""

    def __init__(self, scenario, conversation_id: UUID, headers: dict) -> None:
        self.scenario = scenario
        self.conversation_id = conversation_id
        self.headers = headers

    @property
    def pod_id(self) -> UUID:
        return UUID(self.scenario.pod_id)

    @property
    def owner_id(self) -> UUID:
        return UUID(self.scenario.owner_user["id"])

    @property
    def actor(self) -> str:
        return conversation_workload_actor_id(agent_id=None, pod_id=self.pod_id)

    async def delete(self, path: str):
        return await self.scenario.owner_client.delete(
            f"/pods/{self.pod_id}{path}", headers=self.headers
        )

    async def refused(self, path: str) -> None:
        response = await self.delete(path)
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.text

    async def approve_for_the_session(self, *, by: UUID, permission_ids: list[str]):
        """APPROVE_FOR_SESSION of a ``lemma`` command, with whatever
        ``permission_ids`` the model copied from the shell's output, on the
        card shown to ``by``."""
        command = {"cmd": "lemma tables delete chores"}
        approval_id = f"call-{uuid4().hex[:12]}"
        await show_card(
            conversation_id=self.conversation_id,
            approval_id=approval_id,
            person_user_id=by,
            workload_actor_id=self.actor,
            tool_name="exec_command",
            args=command,
        )
        await record_session_approvals(
            conversation_id=self.conversation_id,
            agent_id=None,
            pod_id=self.pod_id,
            tool_args={
                "tool_name": "exec_command",
                "args": command,
                "permission_ids": permission_ids,
            },
            user_id=by,
            approval_id=approval_id,
        )


@pytest.fixture
async def shell(scenario) -> _AssistantShell:
    await scenario.create_org_with_pod(name_prefix="Assistant approvals")
    pod_id = UUID(scenario.pod_id)
    for table in (_TABLE, "errands"):
        await create_table(
            scenario.owner_client, scenario.pod_id, table, enable_rls=False
        )
    opened = await scenario.owner_client.post(
        f"/pods/{pod_id}/conversations", json={"title": "Reset", "type": "CHAT"}
    )
    assert opened.status_code == status.HTTP_201_CREATED, opened.text
    conversation_id = UUID(opened.json()["id"])
    service = WorkspaceSandboxService()
    try:
        workspace_env = await service.get_env_vars(
            UUID(scenario.owner_user["id"]),
            pod_id,
            organization_id=UUID(scenario.org_id),
            workload_type="agent",
            # The assistant's id is its pod's.
            workload_id=pod_id,
            workload_name="pod_default",
            session_id=f"shell-{conversation_id}",
            conversation_id=conversation_id,
        )
    finally:
        await service.close()
    return _AssistantShell(
        scenario,
        conversation_id,
        {"Authorization": f"Bearer {workspace_env['LEMMA_TOKEN']}"},
    )


async def test_approving_the_sandbox_for_the_session_unlocks_what_it_was_refused(
    shell: _AssistantShell,
) -> None:
    """The assistant deletes with ``lemma``, on its workspace's token: refused,
    the person allows it for the conversation, and the retry goes through.

    What is unlocked is what the API refused that session's sandbox -- written
    down by the error handler from the refusal itself. Not the model's copy of
    it (H6): ``pod.delete`` added to the request stays refused. And the
    approval is the person's (M5): another member approving the same request
    unlocks nothing for this token, which acts for the owner.
    """
    await shell.refused(f"/datastore/tables/{_TABLE}")

    await shell.approve_for_the_session(
        by=uuid4(), permission_ids=["datastore.table.delete"]
    )
    await shell.refused(f"/datastore/tables/{_TABLE}")

    await shell.approve_for_the_session(
        by=shell.owner_id, permission_ids=["datastore.table.delete", "pod.delete"]
    )
    deleted = await shell.delete(f"/datastore/tables/{_TABLE}")
    assert deleted.status_code == status.HTTP_204_NO_CONTENT, deleted.text
    await shell.refused("")  # the pod itself: never refused, so never approved


async def test_a_one_time_approval_lends_the_sandbox_its_refusal_for_one_command(
    shell: _AssistantShell,
) -> None:
    """H7 for the sandbox: an approved ``lemma`` command keeps the agent's own
    token, and what it was refused is lent for that command alone. The token
    used to be minted for the person -- their whole authority, in a shell the
    agent went on using."""
    await shell.refused(f"/datastore/tables/{_TABLE}")
    key = {
        "conversation_id": shell.conversation_id,
        "person_user_id": shell.owner_id,
        "workload_actor_id": shell.actor,
    }
    refused = await sandbox_refusals(**key)
    assert refused == {"datastore.table.delete"}

    async with lent_to_the_sandbox(permission_ids=refused, **key):
        deleted = await shell.delete(f"/datastore/tables/{_TABLE}")
        assert deleted.status_code == status.HTTP_204_NO_CONTENT, deleted.text

    # The loan ended with the command, and the refusal it answered is spent.
    assert await sandbox_refusals(**key) == frozenset()
    await shell.refused("/datastore/tables/errands")


async def test_a_session_approval_reaches_the_pod_assistants_own_cli(
    shell: _AssistantShell,
) -> None:
    """Neither half of the key used to match: the approval was written for
    ``DEFAULT_POD_AGENT_ID``, which no run carries, and the token named the
    shell (or, on the Mac, the run) as its session. So the person approved, the
    agent retried, and was refused again, every time. The person is in the key
    too (M5): somebody else's approval is not this token's."""
    await shell.refused(f"/datastore/tables/{_TABLE}")
    key = {
        "session_id": str(shell.conversation_id),
        "workload_actor_id": shell.actor,
        "permission_id": "datastore.table.delete",
    }
    await record_session_approval(person_user_id=uuid4(), **key)
    await shell.refused(f"/datastore/tables/{_TABLE}")

    await record_session_approval(person_user_id=shell.owner_id, **key)
    deleted = await shell.delete(f"/datastore/tables/{_TABLE}")
    assert deleted.status_code == status.HTTP_204_NO_CONTENT, deleted.text
