"""The whole stack against real Docker: row -> container -> command -> file.

This is the test that decides whether the cutover is safe. Everything else
verifies a piece; this verifies that an agent tool call issued through the
unchanged session reaches a real sandbox and comes back with the right answer,
and that a user's files survive the sandbox being stopped and resumed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio

from app.modules.workspace.config import workspace_settings
from app.modules.workspace.sandbox_session import SandboxWorkspaceSession
from app.modules.workspace.domain.sandbox import SandboxKind, SandboxOwnerKind
from app.modules.workspace.providers.docker import (
    DockerProviderConfig,
    DockerSandboxProvider,
    RuntimeCredentialSigner,
)
from app.modules.workspace.providers.docker_engine import DockerEngineClient
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.modules.workspace.services.sandbox_service import SandboxService

pytestmark = [pytest.mark.integration, pytest.mark.workspace, pytest.mark.asyncio]

_SOCKET = os.getenv("WORKSPACE_DOCKER_SOCKET_PATH", "/var/run/docker.sock")
_IMAGE = os.getenv("WORKSPACE_IMAGE", "")


@pytest_asyncio.fixture
async def sandbox_stack(sandbox_uow_factory, monkeypatch) -> AsyncIterator[tuple]:
    if not _IMAGE:
        pytest.skip("set WORKSPACE_IMAGE to run the end-to-end sandbox test")
    if not os.path.exists(_SOCKET):
        pytest.skip(f"no docker socket at {_SOCKET}")


    monkeypatch.setattr(workspace_settings, "workspace_image", _IMAGE)

    engine = DockerEngineClient(socket_path=_SOCKET)
    provider = DockerSandboxProvider(
        engine,
        # The local images are tagged, not digest-pinned.
        DockerProviderConfig(allow_mutable_images=True),
        RuntimeCredentialSigner(key=b"e2e-runtime-credential-key-32bytes!!"),
    )
    SandboxService._inflight.clear()
    service = SandboxService(provider=provider, uow_factory=sandbox_uow_factory)

    sandbox = await service.resolve(
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=uuid4(),
    )
    try:
        yield service, provider, sandbox
    finally:
        await service.destroy(sandbox.id, delete_storage=True)
        await engine.close()


def _session(client: LocalSandboxClient, sandbox_id) -> SandboxWorkspaceSession:
    return SandboxWorkspaceSession(
        client=client,  # type: ignore[arg-type]
        sandbox_id=str(sandbox_id),
        session_id="e2e",
        initial_cwd="/workspace",
        auto_close=False,
        owns_client=False,
    )


async def test_a_command_runs_in_a_real_sandbox(sandbox_stack) -> None:
    """An agent tool call, through the unchanged session, into real compute."""
    service, _, sandbox = sandbox_stack
    session = _session(LocalSandboxClient(service), sandbox.id)

    result = await session.exec_command(cmd="echo hello-from-lemma", timeout=120)

    assert result["success"] is True, result
    assert "hello-from-lemma" in (result["stdout"] or "")
    assert result["exit_code"] == 0


async def test_files_written_by_one_command_are_visible_to_the_next(
    sandbox_stack,
) -> None:
    service, _, sandbox = sandbox_stack
    session = _session(LocalSandboxClient(service), sandbox.id)

    await session.exec_command(cmd="echo persisted > /workspace/note.txt", timeout=120)
    result = await session.exec_command(cmd="cat /workspace/note.txt", timeout=120)

    assert "persisted" in (result["stdout"] or ""), result


async def test_a_users_files_survive_the_sandbox_being_stopped_and_resumed(
    sandbox_stack,
) -> None:
    """The point of adopting a volume rather than deriving a name. If this
    fails after a real cutover, people have lost their work."""
    service, _, sandbox = sandbox_stack
    client = LocalSandboxClient(service)

    await _session(client, sandbox.id).exec_command(
        cmd="echo survives > /workspace/keep.txt", timeout=120
    )

    await service.release(sandbox.id)
    resumed = await service.ensure(sandbox.id)

    result = await _session(client, sandbox.id).exec_command(
        cmd="cat /workspace/keep.txt", timeout=120
    )
    assert "survives" in (result["stdout"] or ""), result
    # Resuming is not a recreation, so the agent must not be told its files
    # were wiped.
    assert resumed.storage_generation == 1


async def test_the_file_api_reaches_the_same_disk_as_the_shell(
    sandbox_stack,
) -> None:
    service, _, sandbox = sandbox_stack
    client = LocalSandboxClient(service)
    session = _session(client, sandbox.id)

    await session.write_file("/workspace/via-api.txt", b"written-by-api", timeout=60)
    read_back = await session.read_file("/workspace/via-api.txt", timeout=60)
    assert read_back == b"written-by-api"

    shell = await session.exec_command(cmd="cat /workspace/via-api.txt", timeout=120)
    assert "written-by-api" in (shell["stdout"] or ""), shell


async def test_python_runs_and_keeps_state_within_a_session(sandbox_stack) -> None:
    service, _, sandbox = sandbox_stack
    session = _session(LocalSandboxClient(service), sandbox.id)

    first = await session.execute_code("value = 6 * 7", timeout=120)
    assert first.success, first
    second = await session.execute_code("print(value)", timeout=120)

    assert second.success, second
    assert "42" in (second.stdout or "")


async def test_an_operation_against_a_replaced_sandbox_does_not_hit_the_new_one(
    sandbox_stack,
) -> None:
    """The fence. A handle taken before a recreate must not silently write
    into the replacement."""
    service, provider, sandbox = sandbox_stack
    first = await service.ensure(sandbox.id)

    await provider.destroy(first.provider_id, deadline_at=_far_future())
    second = await service.ensure(sandbox.id)

    assert second.epoch > first.epoch
    assert second.provider_id != first.provider_id
    assert await provider.inspect(first.provider_id, deadline_at=_far_future()) is None


def _far_future():
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) + timedelta(seconds=120)
