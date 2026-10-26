"""H6, reopened: an approval lends what its card showed, on the real shell token.

The sandbox's ``lemma`` calls are refused over HTTP, and the API records each
approvable refusal per session -- there is no exact call to key it by. Every
approval of an ``exec_command`` then lent the whole pile, including refusals
recorded after the person had answered or that no card ever named: the person
allowed ``python job.py`` for the conversation, the agent edited job.py to
delete a table, and asking for ``python job.py`` again ran it with the delete,
no card shown.

Real API, real refusal recording, real session-approval store; only the
command itself is not run, since what it may do is the question.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.agent.services.approval_reconciliation import (
    record_session_approvals,
)
from app.modules.agent.infrastructure.tools.approval.denials import (
    card_lends,
    lent_to_the_sandbox,
    show_card,
)
from app.modules.agent.infrastructure.tools.authority import (
    conversation_workload_actor_id,
)
from app.modules.agent.infrastructure.tools.user_interaction.pydantic_adapter import (
    _run_if_exact_match_already_approved,
)
from app.modules.test_support.e2e.function_helpers import create_table
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_TABLE = "chores"
_ERRANDS = "errands"


class _Shell:
    """The pod assistant's sandbox token, as its workspace is minted."""

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

    async def card(self, args: dict) -> str:
        """The run pausing on ``exec_command`` with these args."""
        approval_id = f"call-{uuid4().hex[:12]}"
        await show_card(
            conversation_id=self.conversation_id,
            approval_id=approval_id,
            person_user_id=self.owner_id,
            workload_actor_id=self.actor,
            tool_name="exec_command",
            args=args,
        )
        return approval_id

    def deps(self) -> SimpleNamespace:
        return SimpleNamespace(
            conversation_id=self.conversation_id,
            user_id=self.owner_id,
            # The assistant's id is its pod's.
            workload_id=self.pod_id,
            agent_run_id=uuid4(),
            supports_pause_signal=True,
        )


@pytest.fixture
async def shell(scenario) -> _Shell:
    await scenario.create_org_with_pod(name_prefix="Card scope")
    pod_id = UUID(scenario.pod_id)
    for table in (_TABLE, _ERRANDS):
        await create_table(
            scenario.owner_client, scenario.pod_id, table, enable_rls=False
        )
    opened = await scenario.owner_client.post(
        f"/pods/{pod_id}/conversations", json={"title": "Jobs", "type": "CHAT"}
    )
    assert opened.status_code == status.HTTP_201_CREATED, opened.text
    conversation_id = UUID(opened.json()["id"])
    service = WorkspaceSandboxService()
    try:
        env = await service.get_env_vars(
            UUID(scenario.owner_user["id"]),
            pod_id,
            organization_id=UUID(scenario.org_id),
            workload_type="agent",
            workload_id=pod_id,
            workload_name="pod_default",
            session_id=f"shell-{conversation_id}",
            conversation_id=conversation_id,
        )
    finally:
        await service.close()
    return _Shell(
        scenario, conversation_id, {"Authorization": f"Bearer {env['LEMMA_TOKEN']}"}
    )


async def test_allowing_a_script_for_the_conversation_does_not_lend_what_it_later_does(
    shell: _Shell, monkeypatch
) -> None:
    """The exact scenario: the repeat of ``python job.py`` must ask again."""
    job = {"cmd": "python job.py"}
    await record_session_approvals(
        conversation_id=shell.conversation_id,
        agent_id=None,
        pod_id=shell.pod_id,
        tool_args={"tool_name": "exec_command", "args": job},
        user_id=shell.owner_id,
        approval_id=await shell.card(job),
    )
    # job.py, edited since, deletes a table: refused, and recorded as such.
    await shell.refused(f"/datastore/tables/{_TABLE}")
    ran: list[str] = []

    async def run_it(self, *, deps, tool_name, args):
        ran.append(tool_name)
        return {"ok": True}

    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.approval.executor.ApprovalExecutor.execute_as_user",
        run_it,
    )

    repeat = await _run_if_exact_match_already_approved(
        deps=shell.deps(),  # type: ignore[arg-type]
        tool_name="exec_command",
        args=job,
    )

    assert repeat is None, "a refusal nobody approved goes to a card"
    assert ran == []
    await shell.refused(f"/datastore/tables/{_TABLE}")


async def test_a_one_time_approval_lends_only_what_its_card_showed(
    shell: _Shell, scenario
) -> None:
    created = await scenario.owner_client.post(
        f"/pods/{shell.pod_id}/datastore/tables/{_ERRANDS}/records",
        json={"data": {"title": "post"}},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    errand = f"/datastore/tables/{_ERRANDS}/records/{created.json()['id']}"
    await shell.refused(f"/datastore/tables/{_TABLE}")
    approval_id = await shell.card({"cmd": "lemma tables delete chores"})
    # Refused after the card went up: not on it, so not the answer's to lend.
    await shell.refused(errand)

    card = await card_lends(
        conversation_id=shell.conversation_id,
        approval_id=approval_id,
        person_user_id=shell.owner_id,
        workload_actor_id=shell.actor,
        tool_name="exec_command",
    )
    assert card.permission_ids == {"datastore.table.delete"}

    async with lent_to_the_sandbox(
        conversation_id=shell.conversation_id,
        person_user_id=shell.owner_id,
        workload_actor_id=shell.actor,
        permission_ids=card.permission_ids,
    ):
        deleted = await shell.delete(f"/datastore/tables/{_TABLE}")
        assert deleted.status_code == status.HTTP_204_NO_CONTENT, deleted.text
        await shell.refused(errand)
