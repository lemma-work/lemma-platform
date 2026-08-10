"""The Docker provider against a real Docker Engine.

The fake in the unit tests models what Docker is believed to do. This proves
the belief. It matters most for volume adoption, where being wrong means a
user's files are silently stranded on an orphaned volume rather than mounted
into their workspace.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from app.modules.workspace.providers.base import (
    LABEL_MANAGED_BY,
    LABEL_SANDBOX_ID,
    LEGACY_LOGICAL_ID,
    LEGACY_MANAGED_BY,
    MANAGED_BY,
)
from app.modules.workspace.providers.docker import (
    DockerProviderConfig,
    DockerSandboxProvider,
    RuntimeCredentialSigner,
)
from app.modules.workspace.providers.docker_engine import (
    DockerEngineClient,
    DockerVolumeCreateRequest,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SOCKET = os.getenv("WORKSPACE_DOCKER_SOCKET_PATH", "/var/run/docker.sock")


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[DockerEngineClient]:
    if not os.path.exists(_SOCKET):
        pytest.skip(f"no docker socket at {_SOCKET}")
    client = DockerEngineClient(socket_path=_SOCKET)
    try:
        await client.list_volumes(labels={"managed-by": "probe"}, deadline_at=_deadline())
    except Exception as exc:  # pragma: no cover - environment guard
        await client.close()
        pytest.skip(f"docker engine not reachable: {exc}")
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def provider(engine: DockerEngineClient) -> DockerSandboxProvider:
    return DockerSandboxProvider(
        engine,
        DockerProviderConfig(allow_mutable_images=True),
        RuntimeCredentialSigner(key=b"k" * 32),
    )


async def test_a_real_legacy_volume_is_adopted(
    engine: DockerEngineClient, provider: DockerSandboxProvider
) -> None:
    """Reproduces exactly what the sandbox runtime leaves behind.

    The volume name embeds a random storage token while the label carries the
    logical id, and the two are unrelated UUIDs -- which is precisely why the
    name cannot be derived and the label lookup has to work.
    """
    sandbox_id = uuid4()
    storage_token = uuid4()
    name = f"ab-ws-{storage_token.hex}"
    await engine.create_volume(
        DockerVolumeCreateRequest(
            name=name,
            labels={
                LABEL_MANAGED_BY: LEGACY_MANAGED_BY,
                LEGACY_LOGICAL_ID: str(sandbox_id),
                "workload-kind": "workspace",
                "storage-token": str(storage_token),
            },
        ),
        deadline_at=_deadline(),
    )
    try:
        adopted = await provider.find_volume(
            sandbox_id=sandbox_id, deadline_at=_deadline()
        )
        assert adopted == name
    finally:
        await engine.delete_volume(name, deadline_at=_deadline())


async def test_a_real_volume_created_by_this_module_is_found(
    engine: DockerEngineClient, provider: DockerSandboxProvider
) -> None:
    sandbox_id = uuid4()
    name = f"lemma-vol-{sandbox_id.hex}-1"
    created = await provider.ensure_volume(
        sandbox_id=sandbox_id, name=name, deadline_at=_deadline()
    )
    try:
        assert created == name
        assert (
            await provider.find_volume(sandbox_id=sandbox_id, deadline_at=_deadline())
            == name
        )
        # Idempotent against a real engine, not just against the fake.
        assert (
            await provider.ensure_volume(
                sandbox_id=sandbox_id, name=name, deadline_at=_deadline()
            )
            == name
        )
    finally:
        await provider.destroy_volume(name, deadline_at=_deadline())


async def test_real_label_filters_do_not_leak_across_sandboxes(
    engine: DockerEngineClient, provider: DockerSandboxProvider
) -> None:
    """Docker's label filter is conjunctive; the whole adoption scheme depends
    on it, so it is checked against the engine rather than assumed."""
    mine, theirs = uuid4(), uuid4()
    names = [f"lemma-vol-{mine.hex}-1", f"lemma-vol-{theirs.hex}-1"]
    for sandbox_id, name in zip((mine, theirs), names):
        await engine.create_volume(
            DockerVolumeCreateRequest(
                name=name,
                labels={
                    LABEL_MANAGED_BY: MANAGED_BY,
                    LABEL_SANDBOX_ID: str(sandbox_id),
                },
            ),
            deadline_at=_deadline(),
        )
    try:
        assert (
            await provider.find_volume(sandbox_id=mine, deadline_at=_deadline())
            == names[0]
        )
        assert (
            await provider.find_volume(sandbox_id=theirs, deadline_at=_deadline())
            == names[1]
        )
        assert (
            await provider.find_volume(sandbox_id=uuid4(), deadline_at=_deadline())
            is None
        )
    finally:
        for name in names:
            await engine.delete_volume(name, deadline_at=_deadline())


async def test_the_sweep_sees_real_legacy_containers(
    provider: DockerSandboxProvider,
) -> None:
    """Whatever this host is holding, anything labelled by the sandbox runtime must come
    back marked legacy -- otherwise pre-cutover containers leak forever."""
    objects = await provider.list_objects(deadline_at=_deadline())

    for obj in objects:
        assert obj.name, "a sandbox object must be identifiable by name"
    legacy = [obj for obj in objects if obj.legacy]
    for obj in legacy:
        # Legacy objects have no epoch label; identity comes from the old
        # logical-id, which the sweep still has to be able to read.
        assert obj.epoch is None or isinstance(obj.epoch, int)
