"""A fresh workspace's server moves from the image floor onto the overlay, once.

Through the real backend path, against the real workspace image: the first
session installs the overlay and restarts the sandbox so its server imports the
overlay's code, and every later session finds the server current and leaves
the sandbox alone. The server's own report of what it imported is the proof --
`running_runtime_version` reads it from `sandbox_runtime.__file__` in that
process -- and the start time of the container's first process is the proof
that nothing restarted a second time.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.agent.tools.workspace_cli.workspace_cli import exec_command_internal
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.infrastructure.runtime_bundle import runtime_bundle
from app.modules.workspace.services.workspace_runtime_restart import was_restarted
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)
from sandbox_runtime.protocol import WorkloadKind

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(900)]

_BACKEND = Path(__file__).resolve().parents[5]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """This checkout's overlay, configured as the one the backend installs.

    Set on the settings object as well as cleared from the loader's cache: the
    in-process settings were built before this module ran, and the bundle is
    read once per process.
    """
    spec = spec_from_file_location(
        "build_runtime_bundle", _BACKEND / "scripts" / "build_runtime_bundle.py"
    )
    assert spec is not None and spec.loader is not None
    builder = module_from_spec(spec)
    spec.loader.exec_module(builder)
    out_dir = tmp_path_factory.mktemp("bundle")
    manifest = builder.build(out_dir)

    configured = workspace_settings.runtime_bundle_dir
    workspace_settings.runtime_bundle_dir = str(out_dir)
    runtime_bundle.cache_clear()
    try:
        yield manifest["version"]
    finally:
        workspace_settings.runtime_bundle_dir = configured
        runtime_bundle.cache_clear()


async def _context(authenticated_client, fixed_test_org, fixed_test_user):
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Runtime Restart Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(response.json()["id"]),
        conversation_id=uuid4(),
        agent_name="workspace_runtime_restart_e2e",
        workload_type="agent",
    )


async def _run(ctx, command: str) -> str:
    result = await exec_command_internal(
        ctx, ExecCommandRequest(cmd=command, timeout_seconds=180)
    )
    assert result.success, getattr(result, "error", result)
    return (result.stdout or "").strip()


async def _running_version(user_id: UUID) -> str | None:
    client = WorkspaceSandboxService()._get_manager_client()
    state = await client.runtime_state(WorkloadKind.WORKSPACE, user_id)
    assert state is not None, "the Docker fabric reports no runtime state"
    return state.version


async def test_the_server_runs_the_overlay_after_one_restart_and_no_more(
    bundle: str,
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
) -> None:
    del local_sandbox_server, backend_server, configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)
    user_id = UUID(fixed_test_user["id"])

    # The first session: install, restart, then the command.
    await eventually(
        label="the first session",
        probe=lambda: exec_command_internal(
            ctx, ExecCommandRequest(cmd="true", timeout_seconds=180)
        ),
        done=lambda result: result.success,
        timeout_seconds=600,
        interval_seconds=2.0,
    )
    assert await _running_version(user_id) == bundle, (
        "the workspace server is not running the overlay it was sent"
    )
    # It got there by the restart, not by starting on it: the overlay's volume
    # was empty when this sandbox's container started, so its server began on
    # the floor.
    assert was_restarted(user_id, bundle), (
        "the server runs the overlay but no restart was recorded"
    )
    started = await _run(ctx, "ps -o lstart= -p 1")

    # Later sessions find it current and leave it running.
    for _ in range(2):
        await _run(ctx, "true")
    assert await _run(ctx, "ps -o lstart= -p 1") == started, (
        "a session restarted a sandbox whose server was already current"
    )
    assert await _running_version(user_id) == bundle
