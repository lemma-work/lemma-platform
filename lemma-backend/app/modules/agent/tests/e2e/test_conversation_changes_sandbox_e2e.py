"""The changes route against a real Docker workspace: the read reaches the
conversation's checkout on its owner's machine, and leaves a released machine
asleep.

The checkout is laid out the way ``github_project`` lays one out -- one clone
under ``~/lemma/repos``, the conversation's worktree under
``~/lemma/worktrees`` on ``lemma/<slug>`` -- from an origin on the machine's
own disk, so nothing here reaches GitHub.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.infrastructure.tools.workspace_cli.models import (
    ExecCommandRequest,
)
from app.modules.agent.infrastructure.tools.workspace_cli.workspace_cli import (
    exec_command_internal,
)
from app.modules.test_support.e2e.waiters import eventually
from sandbox_runtime.paths import WORKSPACE_ROOT

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(600)]

_REPO = ProjectRepo(owner="acme", repo="web")
_SLUG = "chg12345"
_APP_DOMAIN = "apps.changes.test"

#: An origin, its clone and this conversation's worktree, then the work a
#: coding agent leaves: a commit not pushed, an edit not committed, a new file.
_LAY_OUT_A_CHECKOUT = f"""
set -e
export GIT_AUTHOR_NAME=Ada GIT_AUTHOR_EMAIL=ada@example.test
export GIT_COMMITTER_NAME=Ada GIT_COMMITTER_EMAIL=ada@example.test
root={WORKSPACE_ROOT}
rm -rf "$root/origin-src" "$root/origin-chg" "{_REPO.clone_dir}" "{_REPO.worktree_dir(_SLUG)}"
git init -q -b main "$root/origin-src"
printf 'one\\ntwo\\n' > "$root/origin-src/app.txt"
git -C "$root/origin-src" add . && git -C "$root/origin-src" commit -qm seed
git clone -q --bare "$root/origin-src" "$root/origin-chg" && rm -rf "$root/origin-src"
git clone -q "$root/origin-chg" "{_REPO.clone_dir}"
git -C "{_REPO.clone_dir}" worktree add -q -b lemma/{_SLUG} \\
  "{_REPO.worktree_dir(_SLUG)}" origin/main
cd "{_REPO.worktree_dir(_SLUG)}"
printf 'one\\nTWO\\n' > app.txt && git commit -qam 'agent work'
printf 'three\\n' >> app.txt
printf 'hello\\n' > notes.md
"""


async def _owner_context(client, org, user) -> tuple[ToolContext, str]:
    pod = await client.post(
        "/pods",
        json={
            "name": f"Changes Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": org["id"],
        },
    )
    assert pod.status_code == status.HTTP_201_CREATED, pod.text
    conversation = await client.post(
        f"/pods/{pod.json()['id']}/conversations",
        json={"title": "changes", "type": "CHAT"},
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = UUID(conversation.json()["id"])
    async with async_session_maker() as session:
        await session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .values(repo=_REPO.to_json(), cwd=_REPO.worktree_dir(_SLUG))
        )
        await session.commit()
    ctx = ToolContext(
        user_id=UUID(user["id"]),
        org_id=UUID(org["id"]),
        pod_id=UUID(pod.json()["id"]),
        conversation_id=uuid4(),
        agent_name="conversation_changes_e2e",
        workload_type="agent",
    )
    return ctx, f"/pods/{pod.json()['id']}/conversations/{conversation_id}/changes"


async def test_a_running_machine_answers_its_checkouts_changes_and_a_released_one_is_left_asleep(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    test_app,
    monkeypatch,
) -> None:
    del local_sandbox_server, backend_server, configure_workspace_api_url
    ctx, route = await _owner_context(
        authenticated_client, fixed_test_org, fixed_test_user
    )

    async def lay_out():
        return await exec_command_internal(
            ctx, ExecCommandRequest(cmd=_LAY_OUT_A_CHECKOUT, timeout_seconds=180)
        )

    laid = await eventually(
        label="a checkout laid out in a real workspace",
        probe=lay_out,
        done=lambda result: result.success and result.exit_code == 0,
        timeout_seconds=300,
        interval_seconds=2.0,
    )
    assert laid.exit_code == 0, laid

    response = await authenticated_client.get(route + "?patch=true")

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["state"] == "ready", body
    assert body["branch"] == f"lemma/{_SLUG}"
    assert body["base_ref"] == "origin/main"
    assert body["ahead"] == 1
    assert body["uncommitted"] is True
    files = {changed["path"]: changed for changed in body["files"]}
    assert files["app.txt"]["status"] == "modified"
    assert (files["app.txt"]["additions"], files["app.txt"]["deletions"]) == (2, 1)
    assert files["notes.md"]["status"] == "untracked"
    assert body["totals"] == {"files": 2, "additions": 3, "deletions": 1}
    assert "+three" in body["patch"]

    # An app's page in the pod, acting as the same person, reads the same
    # checkout on that person's own machine: the read resolves the machine as
    # a run of the conversation would, not as an app caller.
    monkeypatch.setattr(settings, "app_base_domain", "apps.changes.test")
    name = f"app_{uuid4().hex[:8]}"
    app = await authenticated_client.post(
        route.split("/conversations/")[0] + "/apps",
        json={"name": name, "public_slug": name.replace("_", "-")},
    )
    assert app.status_code == status.HTTP_201_CREATED, app.text
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as browser:
        from_app = await browser.get(
            route,
            headers={
                "Cookie": f"sAccessToken={fixed_test_user['token']}",
                "rid": "session",
                "Origin": f"https://{app.json()['public_slug']}.{_APP_DOMAIN}",
            },
        )
    assert from_app.status_code == status.HTTP_200_OK, from_app.text
    assert from_app.json()["state"] == "ready", from_app.json()
    assert from_app.json()["totals"] == body["totals"]

    # Released, the machine is asked nothing and stays asleep.
    from app.modules.workspace.services.sandbox_composition import (
        get_sandbox_service,
    )

    service = get_sandbox_service()
    owner = UUID(fixed_test_user["id"])
    await service.release(owner)

    asleep = await authenticated_client.get(route)

    assert asleep.status_code == status.HTTP_200_OK, asleep.text
    assert asleep.json()["state"] == "machine_unavailable"
    info = await service.describe(owner)
    assert info is not None and info.status != "RUNNING"
