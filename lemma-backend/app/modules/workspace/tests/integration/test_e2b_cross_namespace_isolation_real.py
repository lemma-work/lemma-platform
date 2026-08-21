"""The two things that kept a user's workspace, held against the real service.

Both come from one production incident. `lemma-dev` and `lemma-prod` each held
their own `E2B_API_KEY`, but the two keys resolved to a single E2B team, and
neither deployment set a metadata namespace -- so each backend enumerated the
other's sandboxes, found no row for them in its own database, and destroyed them
as orphans. One user's workspace was wiped five times inside a twenty-minute
conversation, each kill landing seconds after the other environment rebuilt it.

Two independent properties had to fail together for that to happen, so both are
checked here, against the real service, because both are about what E2B actually
returns:

* a provider must not *see* sandboxes labelled by another namespace, which is
  what stops the question being asked at all; and
* a sweep must not destroy a sandbox it cannot attribute to a row, which is what
  stops the wrong answer being fatal when the first property is misconfigured.

The unit and integration suites cover the sweep's decision table against a fake.
What they cannot cover is whether E2B's metadata query really is blind across
namespaces, and whether a sandbox this sweep declines to touch really does
survive -- so those are the assertions here.

Safety: everything runs under its own per-run namespace, so no query can match a
production sandbox. Only sandboxes this file created are ever killed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from sandbox_runtime.protocol import ByteRange

from app.modules.workspace.providers.base import ProviderCreateSpec
from app.modules.workspace.providers.e2b import E2BProviderConfig, E2BSandboxProvider
from app.modules.workspace.testing.fake_output_buffer import InMemoryOutputBuffer

pytestmark = [pytest.mark.integration, pytest.mark.provider, pytest.mark.asyncio]

_KEY = os.getenv("E2B_API_KEY", "")
_TEMPLATE = os.getenv("E2B_WORKSPACE_TEMPLATE", "")
# Two namespaces standing in for two deployments sharing one E2B team. Per-run
# on purpose: a fixed pair would collide between concurrent runs, which is the
# very failure this file is about.
_RUN = uuid4().hex[:8]
_NAMESPACE_A = f"lemma-xns-a-{_RUN}"
_NAMESPACE_B = f"lemma-xns-b-{_RUN}"


def _deadline(seconds: int = 120) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _build_provider(namespace: str) -> E2BSandboxProvider:
    return E2BSandboxProvider(
        E2BProviderConfig(
            api_key=_KEY,
            workspace_template=_TEMPLATE,
            function_template=_TEMPLATE,
            sandbox_timeout_seconds=300,
            metadata_namespace=namespace,
        ),
        output=InMemoryOutputBuffer(),
    )


def _spec(sandbox_id: UUID, *, epoch: int = 1) -> ProviderCreateSpec:
    return ProviderCreateSpec(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=epoch,
        name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, epoch),
        image="",
        profile_name="workspace",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=_deadline(),
    )


@pytest_asyncio.fixture
async def deployment_a() -> AsyncIterator[E2BSandboxProvider]:
    """One deployment's provider, plus the cleanup that owns what it made."""
    if not _KEY or not _TEMPLATE:
        pytest.skip(
            "set E2B_API_KEY and E2B_WORKSPACE_TEMPLATE to run cross-namespace tests"
        )
    instance = _build_provider(_NAMESPACE_A)
    created: list[str] = []
    instance._created = created  # type: ignore[attr-defined]
    try:
        yield instance
    finally:
        for provider_id in created:
            try:
                sandbox = await instance._connect(provider_id)
                await sandbox.kill(**instance._api())
            except Exception:
                continue


@pytest_asyncio.fixture
def deployment_b() -> E2BSandboxProvider:
    """The other deployment, sharing the account and nothing else."""
    if not _KEY or not _TEMPLATE:
        pytest.skip(
            "set E2B_API_KEY and E2B_WORKSPACE_TEMPLATE to run cross-namespace tests"
        )
    return _build_provider(_NAMESPACE_B)


async def _create(provider: E2BSandboxProvider, sandbox_id: UUID):
    instance = await provider.create(_spec(sandbox_id))
    provider._created.append(instance.provider_id)  # type: ignore[attr-defined]
    return instance


async def test_a_sandbox_is_invisible_to_another_namespace(
    deployment_a: E2BSandboxProvider, deployment_b: E2BSandboxProvider
) -> None:
    """The boundary itself, measured rather than assumed.

    Every safety argument in this module rests on E2B's metadata query being
    exact: the sandbox id is stored under a namespaced key, so a provider asking
    under a different namespace must get nothing back. Asserting it against the
    real service is the only way to know the key really is part of the query and
    not, say, matched loosely.
    """
    sandbox_id = uuid4()
    created = await _create(deployment_a, sandbox_id)

    assert await deployment_a._find_any(sandbox_id) is not None
    assert await deployment_b._find_any(sandbox_id) is None

    # And the sweep sees it the same way, which is the call that matters.
    seen_by_b = await deployment_b.list_objects(deadline_at=_deadline())
    assert created.provider_id not in {obj.provider_id for obj in seen_by_b}


async def test_a_sweep_does_not_destroy_a_sandbox_it_cannot_attribute(
    deployment_a: E2BSandboxProvider, sandbox_uow_factory
) -> None:
    """The second line of defence, exercised where it actually has to hold.

    A sandbox whose id has no row is precisely what one deployment's sandbox
    looks like to another's database. The old rule read that as "ours and
    forgotten" and destroyed it; there is no row here either, and the sandbox
    has to still be running afterwards.
    """
    from app.modules.workspace.services.sandbox_service import SandboxService
    from app.modules.workspace.services.sandbox_sweeper import SandboxSweeper

    sandbox_id = uuid4()
    created = await _create(deployment_a, sandbox_id)

    service = SandboxService(provider=deployment_a, uow_factory=sandbox_uow_factory)
    sweeper = SandboxSweeper(service=service, uow_factory=sandbox_uow_factory)

    reclaimed = await sweeper.reclaim_orphans()

    assert created.provider_id not in reclaimed
    still_there = await deployment_a._find_any(sandbox_id)
    assert still_there is not None, "the sweep destroyed a sandbox it did not own"
    assert still_there.provider_id == created.provider_id


async def test_a_pause_and_resume_keeps_the_files_and_the_sandbox(
    deployment_a: E2BSandboxProvider,
) -> None:
    """What the incident cost, stated as the property that has to survive.

    Not a duplicate of the lifecycle suite's pause test: that one proves pause
    keeps files. This one adds the identity claim the sweep depends on -- the
    resumed sandbox is the *same* E2B sandbox, so nothing along the way decided
    to replace it, and the storage generation therefore has no reason to move.
    """
    sandbox_id = uuid4()
    created = await _create(deployment_a, sandbox_id)
    await deployment_a.wait_ready(
        created, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    sentinel = "/workspace/cross-namespace-sentinel.txt"

    async def payload():
        yield b"survived"

    await deployment_a.write_file(
        created,
        path=sentinel,
        data=payload(),
        expected_sha256=None,
        deadline_at=_deadline(),
    )
    await deployment_a.release(
        created, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    resumed = await deployment_a._find_any(sandbox_id)
    assert resumed is not None
    assert resumed.provider_id == created.provider_id, (
        "a resume that changes the sandbox id has replaced the disk"
    )

    chunks = [
        chunk
        async for chunk in deployment_a.open_file(
            resumed,
            path=sentinel,
            byte_range=ByteRange(offset=0, length=None),
            deadline_at=_deadline(),
        )
    ]
    assert b"".join(chunks) == b"survived"
