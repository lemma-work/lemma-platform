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
    yield
    SandboxService._inflight.clear()
    SandboxService._recent.clear()


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
    monkeypatch.setattr(sandbox_service_module, "_ENSURE_REUSE_SECONDS", 0.05)
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
