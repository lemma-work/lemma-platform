"""The workspace's durability contract, against a real sandbox, in the gated lane.

Two invariants live here, and both were previously tested only where the test
could not run.

*Files survive a release.* Releasing stops compute and keeps the disk -- that is
the whole reason the idle sweep is allowed to run at all. It was covered by
`integration/test_sandbox_end_to_end.py`, which skips unless `WORKSPACE_IMAGE`
is set, and nothing in CI sets it. Those eight tests have never run in CI.

*The orphan sweep does not destroy a live workspace.* Covered by 24 integration
tests against a fake provider, none of which ran in CI either: that lane had no
database, so every one of them skipped itself. The sweep destroyed live user
workspaces in production for days while its own suite was green and absent.

So these are here, in the workspace e2e shard, which runs on every pull request
against a real Docker sandbox.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.agent.tools.workspace_cli.workspace_cli import exec_command_internal
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.services.sandbox_sweeper import SandboxSweeper

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(600)]


async def _context(authenticated_client, fixed_test_org, fixed_test_user):
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Durability Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod = response.json()
    ctx = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(pod["id"]),
        conversation_id=uuid4(),
        agent_name="workspace_durability_e2e",
        workload_type="agent",
    )

    async def warmup():
        return await exec_command_internal(
            ctx, ExecCommandRequest(cmd="true", timeout_seconds=180)
        )

    await eventually(
        label="real workspace sandbox warmup",
        probe=warmup,
        done=lambda result: result.success,
        timeout_seconds=300,
        interval_seconds=2.0,
    )
    return ctx


async def _run(ctx, command: str):
    result = await exec_command_internal(
        ctx, ExecCommandRequest(cmd=command, timeout_seconds=180)
    )
    assert result.success, getattr(result, "error", result)
    return result


def _sandbox_service():
    from app.modules.workspace.services.sandbox_composition import get_sandbox_service

    return get_sandbox_service()


async def test_a_workspace_keeps_its_files_across_a_release_and_resume(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
) -> None:
    """Releasing stops compute and keeps the disk. Everything else assumes it.

    The idle sweep releases a workspace after a few minutes of quiet, so this
    runs constantly in production. If it ever stopped being true, every user
    would lose their files a few minutes after they stopped typing.
    """
    del local_sandbox_server, backend_server, configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)
    user_id = UUID(fixed_test_user["id"])

    await _run(ctx, "mkdir -p /workspace/keep && echo durable > /workspace/keep/file")

    service = _sandbox_service()
    before = await service.get(user_id)
    assert before is not None
    await service.release(user_id)

    read_back = await _run(ctx, "cat /workspace/keep/file")
    assert "durable" in (read_back.stdout or ""), read_back

    after = await service.get(user_id)
    assert after is not None
    # The generation is the signal an agent reads as "your files are gone". A
    # release must never move it -- that is the difference between a resume and
    # a replacement, and the agent has no other way to tell them apart.
    assert after.storage_generation == before.storage_generation


async def test_the_orphan_sweep_leaves_a_live_workspace_and_its_files_alone(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_manager,
) -> None:
    """The regression that cost users their work, held against a real sandbox.

    Two sweeps, because they fail differently. The first runs against this
    database, where the workspace has a row: it must recognise the row and leave
    the object alone. The second runs against a database where the row has been
    hidden, which is exactly what one deployment's workspace looks like to
    another's sweep -- and is the case the old rule read as "ours and forgotten"
    before destroying it.
    """
    del local_sandbox_server, backend_server, configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)
    user_id = UUID(fixed_test_user["id"])

    await _run(ctx, "echo swept-but-alive > /workspace/sentinel")

    service = _sandbox_service()
    uow_factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    sweeper = SandboxSweeper(service=service, uow_factory=uow_factory)

    before = await service.get(user_id)
    assert before is not None

    assert await sweeper.reclaim_orphans() == ()

    survived = await _run(ctx, "cat /workspace/sentinel")
    assert "swept-but-alive" in (survived.stdout or ""), survived
    after = await service.get(user_id)
    assert after is not None
    assert after.storage_generation == before.storage_generation

    # Now the cross-database case. Marking the row DELETED would be a different
    # test -- that object really is reclaimable. What has to be proved is the
    # *unknown* object, so the sweep is pointed at a repository that reports no
    # row for anything, the way another deployment's database would.
    class _BlindRepository(SandboxRepository):
        async def get(self, sandbox_id):  # type: ignore[override]
            return None

    import app.modules.workspace.services.sandbox_sweeper as sweeper_module

    original = sweeper_module.SandboxRepository
    sweeper_module.SandboxRepository = _BlindRepository  # type: ignore[assignment]
    try:
        assert await sweeper.reclaim_orphans() == ()
    finally:
        sweeper_module.SandboxRepository = original  # type: ignore[assignment]

    still_there = await _run(ctx, "cat /workspace/sentinel")
    assert "swept-but-alive" in (still_there.stdout or ""), still_there


async def test_a_deleted_workspace_is_still_reclaimed(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_manager,
) -> None:
    """The sweep has to stay useful, not merely safe.

    A sweep that never destroys anything would pass the test above and leak
    every container the control plane has finished with, which is what the
    orphan sweep exists to stop.
    """
    del local_sandbox_server, backend_server, configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)
    user_id = UUID(fixed_test_user["id"])
    await _run(ctx, "true")

    service = _sandbox_service()
    uow_factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    sweeper = SandboxSweeper(service=service, uow_factory=uow_factory)

    from app.modules.workspace.domain.sandbox import SandboxDesiredState

    async with uow_factory() as uow:
        repository = SandboxRepository(uow)
        await repository.set_desired_state(user_id, SandboxDesiredState.DELETED)
        await uow.commit()

    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
    reclaimed: tuple[str, ...] = ()
    while datetime.now(timezone.utc) < deadline and not reclaimed:
        reclaimed = await sweeper.reclaim_orphans()
    assert reclaimed, "a sandbox this database asked to be rid of was not reclaimed"
