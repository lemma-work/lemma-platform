"""The changes route against real rows: who may ask, and what the rows settle.

What a checkout's diff says is the unit tests' (``test_conversation_changes``,
real git). These are the answers that come from the database -- a conversation
with no repository, one whose last run worked on a Mac, a machine never
started -- and the refusals: nobody but the conversation's owner reads it.

None of them starts a sandbox. The machine one is the point: a conversation
whose machine is not running is answered ``machine_unavailable``, and asking
must not start it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import update
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.agent_host.control.run_records import record_run_execution
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    running_run,
    seeded_conversation,
)
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.test_support.e2e_authz import auth_headers
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.services.sandbox_composition import get_sandbox_service

pytestmark = [pytest.mark.e2e]

_REPO = ProjectRepo(owner="acme", repo="web")


@pytest.fixture(autouse=True)
def _a_provider_that_could_start_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider is configured, as it is in a deployment, so the answer
    is about the machine's state rather than a missing setting. Nothing here
    provisions: a machine with no instance is never inspected."""
    monkeypatch.setattr(
        workspace_settings, "runtime_credential_key", "changes-e2e-" + "k" * 32
    )


def _changes(seeded: Seeded) -> str:
    return f"/pods/{seeded.pod_id}/conversations/{seeded.conversation_id}/changes"


async def _in_repository(seeded: Seeded) -> None:
    """What opening it with ``repository: acme/web`` leaves on the row."""
    async with async_session_maker() as session:
        await session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == seeded.conversation_id)
            .values(repo=_REPO.to_json(), cwd=_REPO.worktree_dir("abc12345"))
        )
        await session.commit()


async def test_a_conversation_with_no_repository_says_so(scenario) -> None:
    seeded = await seeded_conversation(scenario)

    response = await scenario.owner_client.get(_changes(seeded))

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["state"] == "no_repository"
    assert body["files"] == []
    assert body["totals"] == {"files": 0, "additions": 0, "deletions": 0}


async def test_a_machine_that_is_not_running_is_not_started_to_answer(
    scenario,
) -> None:
    seeded = await seeded_conversation(scenario)
    await _in_repository(seeded)

    response = await scenario.owner_client.get(_changes(seeded) + "?patch=true")

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["state"] == "machine_unavailable"
    # Its owner's machine has still never run.
    info = await get_sandbox_service().describe(seeded.user_id)
    assert info is None or info.status != "RUNNING"


async def test_a_conversation_whose_last_run_was_on_a_mac_is_elsewhere(
    scenario,
) -> None:
    seeded = await seeded_conversation(scenario)
    await _in_repository(seeded)
    run_id = await running_run(seeded)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await record_run_execution(
            uow,
            run_id,
            {
                "target": "host",
                "host_id": str(uuid4()),
                "sandbox_id": str(uuid4()),
                "root": "/Users/ada/lemma",
            },
        )
        await uow.commit()

    response = await scenario.owner_client.get(_changes(seeded))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["state"] == "elsewhere"


async def test_a_run_on_the_vm_after_one_on_a_mac_is_read_on_the_vm(
    scenario,
) -> None:
    """The latest run decides: a conversation that has moved back to its VM
    is read there, not reported as elsewhere."""
    seeded = await seeded_conversation(scenario)
    await _in_repository(seeded)
    on_a_mac = await running_run(seeded)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await record_run_execution(uow, on_a_mac, {"target": "host"})
        await Runs(uow).finish_agent_run(
            agent_run_id=on_a_mac, status=AgentRunStatus.COMPLETED
        )
        await uow.commit()
    on_the_vm = await running_run(seeded)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await record_run_execution(uow, on_the_vm, {"target": "vm"})
        await uow.commit()

    response = await scenario.owner_client.get(_changes(seeded))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["state"] == "machine_unavailable"


async def test_another_member_cannot_read_a_conversations_changes(
    scenario,
) -> None:
    seeded = await seeded_conversation(scenario)
    await _in_repository(seeded)
    colleague = await scenario.create_user("changes-colleague")
    await scenario.add_user_to_pod(user=colleague, role="POD_EDITOR")

    response = await scenario.async_client.get(
        _changes(seeded), headers=auth_headers(colleague)
    )

    # Not found, not forbidden: whether it exists is not theirs to learn.
    assert response.status_code == status.HTTP_404_NOT_FOUND, response.text


async def test_somebody_outside_the_pod_is_refused(scenario) -> None:
    seeded = await seeded_conversation(scenario)
    stranger = await scenario.create_user("changes-stranger")

    response = await scenario.async_client.get(
        _changes(seeded), headers=auth_headers(stranger)
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text


async def test_a_conversation_that_does_not_exist_is_not_found(scenario) -> None:
    seeded = await seeded_conversation(scenario)

    response = await scenario.owner_client.get(
        f"/pods/{seeded.pod_id}/conversations/{uuid4()}/changes"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND, response.text


async def test_a_patch_cap_outside_its_bounds_is_refused(scenario) -> None:
    seeded = await seeded_conversation(scenario)

    response = await scenario.owner_client.get(
        _changes(seeded) + f"?patch=true&max_patch_bytes={10**9}"
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
