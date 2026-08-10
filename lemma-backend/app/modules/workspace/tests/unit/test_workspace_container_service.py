from __future__ import annotations

from sandbox_runtime.errors import SandboxUnavailable

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.config import settings
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)


def _sandbox_info(
    user_id: UUID,
    *,
    allocation_id: UUID | None = None,
    allocation_epoch: int = 1,
) -> SandboxInfo:
    return SandboxInfo(
        sandbox_id=str(user_id),
        name=str(user_id),
        status="RUNNING",
        image="",
        endpoint=f"sandbox://{user_id}",
        allocation_id=str(allocation_id or uuid4()),
        allocation_epoch=allocation_epoch,
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
    monkeypatch.setattr(settings, "workspace_callback_api_url", None)
    monkeypatch.setattr(settings, "cli_api_url", "http://127-0-0-1.sslip.io:8710")
    assert (
        WorkspaceSandboxService._resolve_workspace_api_url()
        == "http://127-0-0-1.sslip.io:8710"
    )

    monkeypatch.setattr(
        settings, "workspace_callback_api_url", "http://callback.test:9000"
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

    await asyncio.gather(
        service.get_session(user_id=user_id, pod_id=None, session_id="first"),
        service.get_session(user_id=user_id, pod_id=None, session_id="second"),
    )
    await service.get_session(user_id=user_id, pod_id=None, session_id="third")

    assert manager_client.directories == [
        (user_id, "/workspace"),
        (user_id, "/workspace"),
    ]

    sandbox.infos[user_id] = _sandbox_info(
        user_id,
        allocation_id=uuid4(),
        allocation_epoch=2,
    )
    await service.get_session(user_id=user_id, pod_id=None, session_id="fourth")

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
