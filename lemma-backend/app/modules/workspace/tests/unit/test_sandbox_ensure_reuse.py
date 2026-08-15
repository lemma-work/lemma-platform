"""A sandbox ensured moments ago is not re-verified.

`ensure` had a singleflight, which collapses callers arriving *together*. One
shell tool call is not that: it acquires a workspace session, starts a process,
then reads the process output, and the sandbox client re-ensures on every one of
those. Measured against a real Docker sandbox that was exactly 3.0 ensures and
3.0 provider inspects per `exec_command`, and roughly 60% of the time the tool
spent inside our own code -- for an `echo` whose real work was about a
millisecond.

That matters far more in production than the local numbers suggest: production
runs the E2B provider, where an inspect is an API round trip to their fabric
rather than a call to a local Docker socket.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.workspace.domain.sandbox import SandboxHandle, SandboxKind
from app.modules.workspace.services import sandbox_service as sandbox_service_module
from app.modules.workspace.services.sandbox_service import SandboxService


class _CountingService(SandboxService):
    """Counts real ensures, so the cache is measured rather than assumed."""

    def __init__(self) -> None:
        super().__init__(provider=object(), uow_factory=object())
        self.ensures = 0

    async def _ensure_once(self, sandbox_id):  # type: ignore[override]
        self.ensures += 1
        return SandboxHandle(
            sandbox_id=sandbox_id,
            kind=SandboxKind.WORKSPACE,
            epoch=1,
            provider="fake",
            provider_id=f"sandbox-{sandbox_id}",
        )


@pytest.fixture(autouse=True)
def _isolate_service_state():
    """The caches are class attributes, so they outlive an instance."""
    SandboxService._inflight.clear()
    SandboxService._recent.clear()
    SandboxService._verified_running.clear()
    yield
    SandboxService._inflight.clear()
    SandboxService._recent.clear()
    SandboxService._verified_running.clear()


@pytest.mark.asyncio
async def test_sequential_ensures_within_one_tool_call_provision_once() -> None:
    service = _CountingService()
    sandbox_id = uuid4()

    # Session acquisition, start_process, read_process_output.
    handles = [await service.ensure(sandbox_id) for _ in range(3)]

    assert service.ensures == 1
    assert {handle.sandbox_id for handle in handles} == {sandbox_id}


@pytest.mark.asyncio
async def test_the_window_expires(monkeypatch) -> None:
    """Reuse is a within-operation shortcut, never a substitute for checking."""
    monkeypatch.setattr(sandbox_service_module, "ENSURE_REUSE_SECONDS", 0.05)
    service = _CountingService()
    sandbox_id = uuid4()

    await service.ensure(sandbox_id)
    await asyncio.sleep(0.08)
    await service.ensure(sandbox_id)

    assert service.ensures == 2


@pytest.mark.asyncio
async def test_two_sandboxes_do_not_share_an_entry() -> None:
    service = _CountingService()

    await service.ensure(uuid4())
    await service.ensure(uuid4())

    assert service.ensures == 2


@pytest.mark.asyncio
async def test_a_gone_sandbox_is_not_served_again_from_memory() -> None:
    """`ProviderGone` is documented as "re-ensure to get a current handle".

    A remembered handle would answer that re-ensure with the dead one. A
    sandbox can die without passing through release or destroy -- the sweeper
    destroys through the provider, E2B times sandboxes out server-side, another
    replica's sweep is invisible here -- so whatever discovers it must say so,
    or every call inside the window fails identically.
    """
    service = _CountingService()
    sandbox_id = uuid4()
    first = await service.ensure(sandbox_id)

    service.forget(sandbox_id)
    second = await service.ensure(sandbox_id)

    assert service.ensures == 2
    assert first is not second


@pytest.mark.asyncio
async def test_release_and_destroy_forget_the_handle() -> None:
    """Exercises the real methods, not the helper they call.

    Both forget before touching anything else, which is what this asserts: the
    unit of work is rigged to fail, so reaching the assertion at all proves the
    entry was dropped first.
    """
    for operation in ("release", "destroy"):
        service = _CountingService()
        sandbox_id = uuid4()
        await service.ensure(sandbox_id)
        assert [key for key in service._recent if key[1] == sandbox_id], operation

        def _explode():
            raise RuntimeError("uow should not be reached before forgetting")

        service._uow_factory = _explode
        with pytest.raises(RuntimeError):
            await getattr(service, operation)(sandbox_id)

        assert not [key for key in service._recent if key[1] == sandbox_id], operation


@pytest.mark.asyncio
async def test_concurrent_callers_still_collapse_to_one_provision() -> None:
    """The singleflight this sits in front of must keep working."""
    service = _CountingService()
    sandbox_id = uuid4()

    await asyncio.gather(*(service.ensure(sandbox_id) for _ in range(8)))

    assert service.ensures == 1


class _NullUoW:
    """The unit of work describe() opens; the repository is patched anyway."""

    async def __aenter__(self):
        return SimpleNamespace(session=None)

    async def __aexit__(self, *exc):
        return False


class _DescribeService(SandboxService):
    """Counts provider inspects while the row stays a live, mutable fake."""

    def __init__(self, row) -> None:
        super().__init__(provider=self, uow_factory=lambda: _NullUoW())
        self.row = row
        self.inspects = 0

    async def inspect(self, provider_id, *, deadline_at):
        del provider_id, deadline_at
        self.inspects += 1
        return SimpleNamespace(running=True)


@pytest.fixture
def _describe(monkeypatch):
    """Patch the repository so describe() reads our fake row."""

    def _make(row):
        service = _DescribeService(row)

        class _Repo:
            def __init__(self, _uow):
                pass

            async def get(self, sandbox_id):
                del sandbox_id
                return service.row

            async def current_instance(self, sandbox_id):
                del sandbox_id
                return SimpleNamespace(provider_id="prov-1")

        monkeypatch.setattr(sandbox_service_module, "SandboxRepository", _Repo)
        return service

    return _make


def _row(epoch: int, generation: int, sandbox_id):
    return SimpleNamespace(
        id=sandbox_id, epoch=epoch, storage_generation=generation, kind=None
    )


@pytest.mark.asyncio
async def test_the_running_check_is_not_repeated_within_the_window(_describe) -> None:
    sandbox_id = uuid4()
    service = _describe(_row(1, 1, sandbox_id))

    for _ in range(5):
        info = await service.describe(sandbox_id)
        assert info.status == "RUNNING"

    assert service.inspects == 1


@pytest.mark.asyncio
async def test_the_row_is_read_every_time_even_when_the_check_is_skipped(
    _describe,
) -> None:
    """The reason only the provider call is cached.

    Epoch and storage generation drive the directory key and the
    workspace-recreated notice, so a stale one is a silent wrong answer rather
    than a retryable failure.
    """
    sandbox_id = uuid4()
    service = _describe(_row(1, 1, sandbox_id))
    first = await service.describe(sandbox_id)
    assert (first.allocation_epoch, first.storage_generation) == (1, 1)

    # The disk was reset underneath us; no provider call is due yet.
    service.row = _row(1, 2, sandbox_id)
    second = await service.describe(sandbox_id)

    assert service.inspects == 1  # still cached
    assert second.storage_generation == 2  # but the row is current


@pytest.mark.asyncio
async def test_a_recreated_container_is_checked_again(_describe) -> None:
    sandbox_id = uuid4()
    service = _describe(_row(1, 1, sandbox_id))
    await service.describe(sandbox_id)

    service.row = _row(2, 1, sandbox_id)  # new epoch -- different container
    await service.describe(sandbox_id)

    assert service.inspects == 2


@pytest.mark.asyncio
async def test_forgetting_a_gone_sandbox_forces_a_fresh_check(_describe) -> None:
    sandbox_id = uuid4()
    service = _describe(_row(1, 1, sandbox_id))
    await service.describe(sandbox_id)

    service.forget(sandbox_id)
    await service.describe(sandbox_id)

    assert service.inspects == 2
