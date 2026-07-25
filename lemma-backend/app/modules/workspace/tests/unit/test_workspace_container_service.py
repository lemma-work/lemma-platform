from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from agentbox_client import AgentBoxApiError, RetryDisposition
from agentbox_client.models import AgentBoxErrorBody, AgentBoxErrorResponse
from app.core.config import settings
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)


def _sandbox_info(user_id: UUID) -> SandboxInfo:
    return SandboxInfo(
        sandbox_id=str(user_id),
        name=str(user_id),
        namespace=None,
        status="RUNNING",
        image="",
        endpoint=f"agentbox://{user_id}",
    )


class _FakeSandbox:
    def __init__(self) -> None:
        self.infos: dict[UUID, SandboxInfo] = {}
        self.ensure_calls: list[tuple[UUID, dict[str, str] | None]] = []
        self.suspended: list[UUID] = []

    async def ensure_sandbox(
        self, user_id: UUID, *, env: dict[str, str] | None = None
    ) -> SandboxInfo:
        self.ensure_calls.append((user_id, env))
        await asyncio.sleep(0)
        return self.infos.setdefault(user_id, _sandbox_info(user_id))

    async def get_sandbox(self, user_id: UUID) -> SandboxInfo | None:
        return self.infos.get(user_id)

    async def suspend_sandbox(self, user_id: UUID) -> None:
        self.suspended.append(user_id)

    async def delete_sandbox(self, user_id: UUID) -> None:
        self.infos.pop(user_id, None)


class _FakeStateStore:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.errors: list[str] = []

    async def mark_creating(self, **_kwargs: Any) -> None:
        self.states.append("CREATING")

    async def get_state(self, **_kwargs: Any):
        return None

    async def mark_running(self, **_kwargs: Any) -> None:
        self.states.append("RUNNING")

    async def mark_error(self, *, error: str, **_kwargs: Any) -> None:
        self.states.append("ERROR")
        self.errors.append(error)

    async def mark_stopped(self, **_kwargs: Any) -> None:
        self.states.append("STOPPED")


class _FakeActivityStore:
    def __init__(self) -> None:
        self.marked: list[dict[str, Any]] = []
        self.removed: list[UUID] = []

    async def mark_active(self, **kwargs: Any) -> None:
        self.marked.append(kwargs)

    async def remove(self, *, runtime: str, user_id: UUID) -> None:
        del runtime
        self.removed.append(user_id)


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


def _api_error(retry: RetryDisposition) -> AgentBoxApiError:
    response = httpx.Response(
        503,
        request=httpx.Request(
            "PUT", "http://agentbox.test/sandboxes/workspace/id/directories"
        ),
    )
    return AgentBoxApiError(
        response,
        AgentBoxErrorResponse(
            error=AgentBoxErrorBody(
                code="PROVIDER_UNAVAILABLE",
                message="sandbox provider allocation no longer exists",
                retry=retry,
            )
        ),
    )


def _service(
    sandbox: _FakeSandbox,
    *,
    runtime: str = "agentbox",
    state: _FakeStateStore | None = None,
    activity: _FakeActivityStore | None = None,
) -> WorkspaceSandboxService:
    return WorkspaceSandboxService(
        runtime=runtime,
        sandbox=sandbox,  # type: ignore[arg-type]
        state_store=state or _FakeStateStore(),  # type: ignore[arg-type]
        activity_store=activity or _FakeActivityStore(),  # type: ignore[arg-type]
    )


def test_service_uses_one_agentbox_runtime() -> None:
    assert _service(_FakeSandbox()).runtime == "agentbox"


@pytest.mark.asyncio
async def test_ensure_returns_typed_sandbox_and_coalesces_concurrency() -> None:
    user_id = uuid4()
    sandbox = _FakeSandbox()
    state = _FakeStateStore()
    activity = _FakeActivityStore()
    service = _service(sandbox, state=state, activity=activity)

    first, second = await asyncio.gather(
        service.get_or_create_sandbox(user_id),
        service.get_or_create_sandbox(user_id),
    )

    assert isinstance(first, SandboxInfo)
    assert first == second
    assert first.sandbox_id == str(user_id)
    assert len(sandbox.ensure_calls) == 1
    assert state.states.count("CREATING") == 1
    assert state.states.count("RUNNING") == 1
    assert len(activity.marked) == 1


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
        async def ensure_sandbox(
            self, user_id: UUID, *, env: dict[str, str] | None = None
        ) -> SandboxInfo:
            ensure_started.set()
            await allow_ensure.wait()
            return await super().ensure_sandbox(user_id, env=env)

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


@pytest.mark.asyncio
async def test_ensure_never_rewrites_the_configured_callback_host(monkeypatch) -> None:
    user_id = uuid4()
    sandbox = _FakeSandbox()
    monkeypatch.setattr(settings, "workspace_callback_api_url", None)
    monkeypatch.setattr(settings, "cli_api_url", "http://127-0-0-1.sslip.io:8710")
    service = _service(sandbox, runtime="docker")

    await service.get_or_create_sandbox(user_id)

    assert sandbox.ensure_calls == [
        (user_id, {"LEMMA_BASE_URL": "http://127-0-0-1.sslip.io:8710"})
    ]


@pytest.mark.asyncio
async def test_ensure_records_failure_without_backend_lifecycle_lock() -> None:
    class _FailingSandbox(_FakeSandbox):
        async def ensure_sandbox(
            self, user_id: UUID, *, env: dict[str, str] | None = None
        ) -> SandboxInfo:
            del user_id, env
            raise RuntimeError("provider unavailable")

    state = _FakeStateStore()
    service = _service(_FailingSandbox(), state=state)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await service.get_or_create_sandbox(uuid4())

    assert state.states == ["CREATING", "ERROR"]
    assert state.errors == ["provider unavailable"]


@pytest.mark.asyncio
async def test_stop_suspends_once_and_updates_caches() -> None:
    user_id = uuid4()
    sandbox = _FakeSandbox()
    sandbox.infos[user_id] = _sandbox_info(user_id)
    state = _FakeStateStore()
    activity = _FakeActivityStore()
    service = _service(sandbox, state=state, activity=activity)

    await service.stop_sandbox(user_id)

    assert sandbox.suspended == [user_id]
    assert activity.removed == [user_id]
    assert state.states == ["STOPPED"]


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
                raise _api_error(RetryDisposition.SAFE_SAME_OPERATION)

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
