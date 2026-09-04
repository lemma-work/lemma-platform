"""What a failed readiness probe is allowed to cost.

`ensure_sandbox(verify_ready=True)` exists because adoption is not proof: on a
VM the process inside can die without the provider's answer changing, and a
function sandbox in exactly that state served 502 to every dispatch for 100
minutes. The recovery is to replace the sandbox once.

That recovery is only safe for a function sandbox, and the reason is written in
its own docstring -- "a function sandbox holds no files". A workspace owns the
user's disk, and on a SANDBOX_NATIVE provider the sandbox *is* the disk, so the
same recovery answers a twenty-second probe failure by deleting everything the
user had. `kind` was computed at that call site and never consulted, so the two
cases shared one recovery and only the caller list kept the workspace case from
happening.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.workspace.domain.sandbox import SandboxKind, SandboxOwnerKind
from app.modules.workspace.providers.base import ProviderNotReady
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.modules.workspace.services.sandbox_service import SandboxService
from app.modules.workspace.tests.integration.test_sandbox_service import FakeProvider
from sandbox_runtime.protocol import WorkloadKind

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(provider: FakeProvider, sandbox_uow_factory) -> LocalSandboxClient:
    SandboxService._inflight.clear()
    SandboxService._recent.clear()
    service = SandboxService(provider=provider, uow_factory=sandbox_uow_factory)
    return LocalSandboxClient(service=service)


def _refuse_readiness_once(provider: FakeProvider, *, on_call: int) -> None:
    """Fail exactly one readiness probe, the way a transiently wedged one does.

    Exactly one, not "every call from here on". A permanently unready provider
    sends `_ensure_once` into its five-minute retry budget, so the test would
    spend 300 seconds proving something about the retry loop instead of one
    second proving something about the recovery.

    Call 1 is the provision inside the first `ensure`; call 2 is the verify
    probe, which is the one under test.
    """
    original = provider.wait_ready
    calls = {"n": 0}

    async def flaky(instance, *, kind, deadline_at):
        calls["n"] += 1
        if calls["n"] == on_call:
            raise ProviderNotReady("runtime is not answering")
        return await original(instance, kind=kind, deadline_at=deadline_at)

    provider.wait_ready = flaky  # type: ignore[method-assign]


async def _workspace_row(client: LocalSandboxClient) -> UUID:
    """A workspace row, which `_ensure_row` does not create.

    Only a function runtime is resolved on first use; workspace rows are
    backfilled per user, so a test addressing one has to put it there itself.
    """
    user_id = uuid4()
    sandbox = await client._service.resolve(
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
    )
    return sandbox.id


async def test_a_workspace_that_fails_its_probe_keeps_its_disk(
    client: LocalSandboxClient, provider: FakeProvider
) -> None:
    """The one that mattered: a probe failure must not delete the user's files."""
    logical_id = await _workspace_row(client)
    _refuse_readiness_once(provider, on_call=2)

    await client.ensure_sandbox(WorkloadKind.WORKSPACE, logical_id, verify_ready=True)

    assert provider.destroyed == [], (
        "a readiness failure destroyed a workspace, and on E2B that is the disk"
    )


async def test_a_function_that_fails_its_probe_is_still_replaced(
    client: LocalSandboxClient, provider: FakeProvider
) -> None:
    """The recovery has to stay, or the 502-for-100-minutes failure comes back.

    A function sandbox refetches its artifact from the gateway, so replacing it
    costs a cold start and nothing else.
    """
    _refuse_readiness_once(provider, on_call=2)

    await client.ensure_sandbox(WorkloadKind.FUNCTION, uuid4(), verify_ready=True)

    assert provider.destroyed != [], "a wedged function sandbox must be replaced"
