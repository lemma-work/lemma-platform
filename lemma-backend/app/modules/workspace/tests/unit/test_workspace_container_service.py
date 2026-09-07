from __future__ import annotations

from sandbox_runtime.errors import SandboxUnavailable

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.config import settings
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.services import (
    workspace_sandbox_service as container_service,
)
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)
from app.modules.workspace.config import workspace_settings


def _sandbox_info(
    user_id: UUID,
    *,
    allocation_id: UUID | None = None,
    allocation_epoch: int = 1,
    storage_generation: int = 1,
) -> SandboxInfo:
    return SandboxInfo(
        sandbox_id=str(user_id),
        name=str(user_id),
        status="RUNNING",
        image="",
        endpoint=f"sandbox://{user_id}",
        allocation_id=str(allocation_id or uuid4()),
        allocation_epoch=allocation_epoch,
        storage_generation=storage_generation,
    )


class _FakeSandbox:
    def __init__(self) -> None:
        self.infos: dict[UUID, SandboxInfo] = {}
        self.ensure_calls: list[UUID] = []
        self.suspended: list[UUID] = []

    async def ensure_sandbox(self, user_id: UUID) -> SandboxInfo:
        self.ensure_calls.append(user_id)
        await asyncio.sleep(0)
        return self.infos.setdefault(user_id, _sandbox_info(user_id))

    async def get_sandbox(self, user_id: UUID) -> SandboxInfo | None:
        return self.infos.get(user_id)

    async def suspend_sandbox(self, user_id: UUID) -> None:
        self.suspended.append(user_id)

    async def delete_sandbox(self, user_id: UUID) -> None:
        self.infos.pop(user_id, None)


class _FakeManagerClient:
    def __init__(self) -> None:
        self.directories: list[tuple[UUID, str]] = []

    async def create_directory(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at,
    ) -> None:
        del deadline_at
        self.directories.append((logical_id, path))


def _retryable_failure(code: str = "PROVIDER_UNAVAILABLE") -> SandboxUnavailable:
    """A failure the caller is expected to wait out and retry."""

    return SandboxUnavailable(code, retry_after_ms=250)


@pytest.fixture(autouse=True)
def _isolate_service_caches():
    """The singleflights and readiness caches are class attributes.

    They outlive an instance, so a test that leaves an entry behind changes what
    the next one measures -- and with tests running in random order that is a
    flake rather than a failure.
    """
    for cache in (
        WorkspaceSandboxService._inflight_ensures,
        WorkspaceSandboxService._inflight_directories,
        WorkspaceSandboxService._ready_directories,
        WorkspaceSandboxService._stopping,
    ):
        cache.clear()
    yield
    for cache in (
        WorkspaceSandboxService._inflight_ensures,
        WorkspaceSandboxService._inflight_directories,
        WorkspaceSandboxService._ready_directories,
        WorkspaceSandboxService._stopping,
    ):
        cache.clear()


def _service(sandbox: _FakeSandbox) -> WorkspaceSandboxService:
    return WorkspaceSandboxService(sandbox=sandbox)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ensure_returns_typed_sandbox_and_coalesces_concurrency() -> None:
    user_id = uuid4()
    sandbox = _FakeSandbox()
    service = _service(sandbox)

    first, second = await asyncio.gather(
        service.get_or_create_sandbox(user_id),
        service.get_or_create_sandbox(user_id),
    )

    assert isinstance(first, SandboxInfo)
    assert first == second
    assert first.sandbox_id == str(user_id)
    assert len(sandbox.ensure_calls) == 1


@pytest.mark.asyncio
async def test_sequential_ensure_inspects_ready_sandbox_before_put() -> None:
    user_id = uuid4()
    sandbox = _FakeSandbox()
    service = _service(sandbox)

    await service.get_or_create_sandbox(user_id)
    await service.get_or_create_sandbox(user_id)

    assert len(sandbox.ensure_calls) == 1


@pytest.mark.asyncio
async def test_stop_waits_for_inflight_ensure_before_suspend() -> None:
    user_id = uuid4()
    ensure_started = asyncio.Event()
    allow_ensure = asyncio.Event()

    class _SlowSandbox(_FakeSandbox):
        async def ensure_sandbox(self, user_id: UUID) -> SandboxInfo:
            ensure_started.set()
            await allow_ensure.wait()
            return await super().ensure_sandbox(user_id)

    sandbox = _SlowSandbox()
    service = _service(sandbox)
    ensure = asyncio.create_task(service.get_or_create_sandbox(user_id))
    await ensure_started.wait()
    stop = asyncio.create_task(service.stop_sandbox(user_id))
    await asyncio.sleep(0)
    assert sandbox.suspended == []

    allow_ensure.set()
    await asyncio.gather(ensure, stop)

    assert sandbox.suspended == [user_id]


@pytest.mark.asyncio
async def test_ensure_requested_during_stop_waits_then_recreates() -> None:
    user_id = uuid4()
    stop_started = asyncio.Event()
    allow_stop = asyncio.Event()

    class _SlowStopSandbox(_FakeSandbox):
        async def suspend_sandbox(self, received_user_id: UUID) -> None:
            stop_started.set()
            await allow_stop.wait()
            self.infos.pop(received_user_id, None)
            await super().suspend_sandbox(received_user_id)

    sandbox = _SlowStopSandbox()
    sandbox.infos[user_id] = _sandbox_info(user_id)
    service = _service(sandbox)
    stop = asyncio.create_task(service.stop_sandbox(user_id))
    await stop_started.wait()
    ensure = asyncio.create_task(service.get_or_create_sandbox(user_id))
    await asyncio.sleep(0)

    assert not ensure.done()
    assert sandbox.ensure_calls == []

    allow_stop.set()
    recreated, _ = await asyncio.gather(ensure, stop)

    assert recreated.status == "RUNNING"
    assert len(sandbox.ensure_calls) == 1
    assert sandbox.suspended == [user_id]


def test_callback_host_is_never_rewritten(monkeypatch) -> None:
    """The URL a sandbox calls back on is taken verbatim from config.

    This is the value that reaches the sandbox as LEMMA_BASE_URL via
    get_env_vars, so a rewrite here would silently point workspaces at the
    wrong host.
    """
    monkeypatch.setattr(workspace_settings, "workspace_callback_api_url", None)
    monkeypatch.setattr(settings, "cli_api_url", "http://127-0-0-1.sslip.io:8710")
    assert (
        WorkspaceSandboxService._resolve_workspace_api_url()
        == "http://127-0-0-1.sslip.io:8710"
    )

    monkeypatch.setattr(
        workspace_settings, "workspace_callback_api_url", "http://callback.test:9000"
    )
    assert (
        WorkspaceSandboxService._resolve_workspace_api_url()
        == "http://callback.test:9000"
    )


@pytest.mark.asyncio
async def test_ensure_propagates_provider_failure_without_lifecycle_lock() -> None:
    class _FailingSandbox(_FakeSandbox):
        async def ensure_sandbox(self, user_id: UUID) -> SandboxInfo:
            del user_id
            raise RuntimeError("provider unavailable")

    service = _service(_FailingSandbox())

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await service.get_or_create_sandbox(uuid4())

    # A failed ensure must not leave the singleflight entry behind, or every
    # later caller would await a task that already raised.
    assert not WorkspaceSandboxService._inflight_ensures


@pytest.mark.asyncio
async def test_stop_suspends_once() -> None:
    user_id = uuid4()
    sandbox = _FakeSandbox()
    sandbox.infos[user_id] = _sandbox_info(user_id)
    service = _service(sandbox)

    await service.stop_sandbox(user_id)

    assert sandbox.suspended == [user_id]


@pytest.mark.asyncio
async def test_get_session_uses_canonical_logical_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    sandbox = _FakeSandbox()
    service = _service(sandbox)
    manager_client = _FakeManagerClient()

    async def environment(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"LEMMA_TOKEN": "dynamic"}

    monkeypatch.setattr(service, "get_env_vars", environment)
    monkeypatch.setattr(service, "_get_manager_client", lambda: manager_client)

    session = await service.get_session(
        user_id=user_id,
        pod_id=None,
        session_id="conversation",
    )

    assert session.logical_id == user_id
    assert session.sandbox_id == str(user_id)
    assert session.client is manager_client
    assert session.env_vars == {"LEMMA_TOKEN": "dynamic"}
    assert manager_client.directories == [(user_id, "/workspace")]


@pytest.mark.asyncio
async def test_get_session_coalesces_concurrent_directory_checks_but_revalidates_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    first_allocation_id = uuid4()
    sandbox = _FakeSandbox()
    sandbox.infos[user_id] = _sandbox_info(
        user_id,
        allocation_id=first_allocation_id,
        allocation_epoch=1,
    )
    service = _service(sandbox)
    manager_client = _FakeManagerClient()

    async def environment(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"LEMMA_TOKEN": "dynamic"}

    monkeypatch.setattr(service, "get_env_vars", environment)
    monkeypatch.setattr(service, "_get_manager_client", lambda: manager_client)

    # Deliberately generous for the reuse half. What is under test is that a
    # call inside the window skips the mkdir, not that three in-memory calls
    # finish within 50ms of each other -- and with the window set to 50ms that
    # is what the assertion below was really measuring. On a loaded runner
    # under coverage tracing it would start reporting a second mkdir for
    # reasons that have nothing to do with reuse. Production allows 60s.
    monkeypatch.setattr(container_service, "_DIRECTORY_READY_SECONDS", 30.0)

    await asyncio.gather(
        service.get_session(user_id=user_id, pod_id=None, session_id="first"),
        service.get_session(user_id=user_id, pod_id=None, session_id="second"),
    )
    # Inside the readiness window, a later call reuses the directory rather than
    # re-running the mkdir round trip -- a real sandbox round trip, on a
    # directory created by the first command of the run.
    await service.get_session(user_id=user_id, pod_id=None, session_id="third")
    assert manager_client.directories == [(user_id, "/workspace")]

    # It is a window, not a permanent answer: the check comes back afterwards.
    # The window is compared against the loop clock on every read
    # (`loop.time() - ready_at < _DIRECTORY_READY_SECONDS`), so shrinking it
    # here expires the entry recorded above without waiting out the 30s. This
    # direction is safe to race: a slow machine only makes *more* time pass,
    # which is exactly what the assertion wants.
    monkeypatch.setattr(container_service, "_DIRECTORY_READY_SECONDS", 0.05)
    await asyncio.sleep(0.08)
    await service.get_session(user_id=user_id, pod_id=None, session_id="fourth")

    assert manager_client.directories == [
        (user_id, "/workspace"),
        (user_id, "/workspace"),
    ]

    # A container recreate keeps the disk, and /workspace IS the disk -- so the
    # directory is still there and must not be remade. Same allocation, new
    # epoch, same storage generation.
    sandbox.infos[user_id] = _sandbox_info(
        user_id,
        allocation_id=first_allocation_id,
        allocation_epoch=2,
        storage_generation=1,
    )
    await service.get_session(user_id=user_id, pod_id=None, session_id="fifth")
    assert manager_client.directories == [
        (user_id, "/workspace"),
        (user_id, "/workspace"),
    ]

    # A storage reset is the case where the files really are gone, so the
    # directory has to be created again.
    sandbox.infos[user_id] = _sandbox_info(
        user_id,
        allocation_id=first_allocation_id,
        allocation_epoch=2,
        storage_generation=2,
    )
    await service.get_session(user_id=user_id, pod_id=None, session_id="sixth")

    assert manager_client.directories == [
        (user_id, "/workspace"),
        (user_id, "/workspace"),
        (user_id, "/workspace"),
    ]


@pytest.mark.asyncio
async def test_get_session_reensures_after_missing_provider_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    sandbox = _FakeSandbox()
    service = _service(sandbox)

    class _RecoveringManagerClient(_FakeManagerClient):
        async def create_directory(
            self,
            logical_id: UUID,
            path: str,
            *,
            deadline_at,
        ) -> None:
            await super().create_directory(
                logical_id,
                path,
                deadline_at=deadline_at,
            )
            if len(self.directories) == 1:
                raise _retryable_failure()

    manager_client = _RecoveringManagerClient()

    async def environment(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"LEMMA_TOKEN": "dynamic"}

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(service, "get_env_vars", environment)
    monkeypatch.setattr(service, "_get_manager_client", lambda: manager_client)
    monkeypatch.setattr(asyncio, "sleep", no_wait)

    session = await service.get_session(
        user_id=user_id,
        pod_id=None,
        session_id="conversation",
    )

    assert session.sandbox_id == str(user_id)
    assert len(sandbox.ensure_calls) == 2
    assert manager_client.directories == [
        (user_id, "/workspace"),
        (user_id, "/workspace"),
    ]
