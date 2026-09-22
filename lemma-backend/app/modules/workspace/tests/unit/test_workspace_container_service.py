from __future__ import annotations

from sandbox_runtime.errors import SandboxUnavailable

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from sandbox_runtime.paths import WORKSPACE_ROOT
from app.core.config import settings
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.services import workspace_directory_ensure
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
        #: Every file written into the sandbox, by path. The session path
        #: writes one: the browser-proxy decision, which is asserted on
        #: every session so that withdrawing a proxy takes effect without
        #: anybody replacing a sandbox.
        self.files: dict[str, bytes] = {}

    async def create_directory(
        self,
        logical_id: UUID,
        path: str,
        *,
        deadline_at,
    ) -> None:
        del deadline_at
        self.directories.append((logical_id, path))

    async def write_file(
        self,
        logical_id: UUID,
        path: str,
        data: bytes,
        *,
        deadline_at=None,
        **_kwargs,
    ) -> None:
        del logical_id, deadline_at
        self.files[path] = data


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
    assert manager_client.directories == [(user_id, f"{WORKSPACE_ROOT}")]


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
    monkeypatch.setattr(workspace_directory_ensure, "DIRECTORY_READY_SECONDS", 30.0)

    await asyncio.gather(
        service.get_session(user_id=user_id, pod_id=None, session_id="first"),
        service.get_session(user_id=user_id, pod_id=None, session_id="second"),
    )
    # Inside the readiness window, a later call reuses the directory rather than
    # re-running the mkdir round trip -- a real sandbox round trip, on a
    # directory created by the first command of the run.
    await service.get_session(user_id=user_id, pod_id=None, session_id="third")
    assert manager_client.directories == [(user_id, f"{WORKSPACE_ROOT}")]

    # It is a window, not a permanent answer: the check comes back afterwards.
    # The window is compared against the loop clock on every read
    # (`loop.time() - ready_at < _DIRECTORY_READY_SECONDS`), so shrinking it
    # here expires the entry recorded above without waiting out the 30s. This
    # direction is safe to race: a slow machine only makes *more* time pass,
    # which is exactly what the assertion wants.
    monkeypatch.setattr(workspace_directory_ensure, "DIRECTORY_READY_SECONDS", 0.05)
    await asyncio.sleep(0.08)
    await service.get_session(user_id=user_id, pod_id=None, session_id="fourth")

    assert manager_client.directories == [
        (user_id, f"{WORKSPACE_ROOT}"),
        (user_id, f"{WORKSPACE_ROOT}"),
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
        (user_id, f"{WORKSPACE_ROOT}"),
        (user_id, f"{WORKSPACE_ROOT}"),
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
        (user_id, f"{WORKSPACE_ROOT}"),
        (user_id, f"{WORKSPACE_ROOT}"),
        (user_id, f"{WORKSPACE_ROOT}"),
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

    # Yields once rather than returning outright, and that one `await` is
    # load-bearing. `async def no_wait(_): return None` never reaches the
    # event loop, so the retry loop it stands in for -- `while now <
    # deadline: ... await asyncio.sleep(delay)` -- stops being cooperative
    # and becomes a wall-clock spin that starves the very task it is waiting
    # for. Measured on this interpreter: 2,127,213 iterations in 200ms with
    # the concurrent task never once scheduled, against one iteration and the
    # task running when the sleep yields. That is the shape of a CI run where
    # this test took 1341 seconds -- about four turns of the 300s directory
    # deadline -- while the rest of the suite finished in its usual 233.
    real_sleep = asyncio.sleep

    async def no_wait(_seconds: float) -> None:
        await real_sleep(0)

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
        (user_id, f"{WORKSPACE_ROOT}"),
        (user_id, f"{WORKSPACE_ROOT}"),
    ]


async def test_a_session_tells_the_sandbox_whether_to_use_a_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Written on every session, and written even when the answer is "no".

    The viewer path is not enough on its own: an agent typing
    `agent-browser open` in its own shell reaches `lemma-ensure-display`
    without the backend in the loop. A sandbox no person ever watches would
    never hear the decision, and an older one would go on using the value
    baked into its creation environment -- which is the bug, because that
    value could never be withdrawn.
    """
    from app.modules.workspace.services.browser_proxy import (
        BROWSER_PROXY_DECISION_PATH,
    )

    user_id = uuid4()
    sandbox = _FakeSandbox()
    service = _service(sandbox)
    manager_client = _FakeManagerClient()

    async def environment(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"LEMMA_TOKEN": "dynamic"}

    monkeypatch.setattr(service, "get_env_vars", environment)
    monkeypatch.setattr(service, "_get_manager_client", lambda: manager_client)

    await service.get_session(user_id=user_id, pod_id=None, session_id="conversation")

    assert BROWSER_PROXY_DECISION_PATH in manager_client.files
    assert manager_client.files[BROWSER_PROXY_DECISION_PATH] == b"", (
        "an empty pool is still a decision -- it is how a withdrawal reaches "
        "a sandbox that already has a proxy"
    )


@pytest.mark.asyncio
async def test_an_exhausted_ensure_stops_believing_what_it_knew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fabric that failed every attempt is not one to keep notes about.

    The readiness cache holds a directory for a minute. Left in place after an
    ensure gave up, the next request skipped the ensure entirely and went
    straight to an operation against the same dead endpoint -- so a workspace
    that had just spent its whole deadline failing reported a cached success
    for the following minute.

    Recovery is forgetting, not replacing: on Desktop and E2B the sandbox *is*
    the storage, so destroying it to get a fresh one would take the user's
    files with it.
    """
    from sandbox_runtime.errors import SandboxUnavailable

    from app import sandbox_health

    user_id = uuid4()
    sandbox = _FakeSandbox()
    service = _service(sandbox)

    class _NeverReady:
        async def create_directory(self, *_args: Any, **_kwargs: Any) -> None:
            raise SandboxUnavailable("the guest is not answering")

    monkeypatch.setattr(
        workspace_directory_ensure, "SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS", 0.3
    )
    monkeypatch.setattr(service, "_get_manager_client", lambda: _NeverReady())
    sandbox_health._capability.update({"status": "ready", "detail": "provisioned"})

    service._ready_directories[
        (id(asyncio.get_running_loop()), user_id, "/x", 1, "g")
    ] = asyncio.get_running_loop().time()

    with pytest.raises(TimeoutError) as caught:
        await service.get_session(user_id=user_id, pod_id=None)

    # The reason survives the loop -- it used to be bound and dropped on every
    # attempt, leaving a bare TimeoutError that said nothing.
    assert "not answering" in str(caught.value)
    assert service._ready_directories == {}
    assert sandbox_health.sandbox_capability()["status"] == "unavailable"

    sandbox_health._capability.update({"status": "ready", "detail": "provisioned"})


@pytest.mark.asyncio
async def test_the_interactive_ceiling_covers_acquiring_the_sandbox_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two waits, one budget.

    Acquiring the sandbox and creating the directory are separate awaits, and
    the caller's ceiling used to apply only to the second. A slow provider
    could therefore spend the sandbox manager's full 300s before an
    interactive caller's own 15s limit was consulted at all.
    """
    from sandbox_runtime.errors import SandboxUnavailable

    user_id = uuid4()
    service = _service(_FakeSandbox())

    async def never_acquires(*_args: Any, **_kwargs: Any):
        await asyncio.sleep(30)

    monkeypatch.setattr(service, "get_or_create_sandbox", never_acquires)

    started = asyncio.get_running_loop().time()
    with pytest.raises(SandboxUnavailable):
        await service.get_session(
            user_id=user_id, pod_id=None, ready_timeout_seconds=0.2
        )
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 5, f"the ceiling did not cover acquisition; waited {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_a_directory_task_without_a_cache_key_is_still_cancellable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`stop_sandbox` cancels by prefix, so an unregistered task outlives it.

    When there is no epoch or storage generation to key on, readiness cannot
    be remembered -- but the in-flight task still has to be reachable, or it
    survives the stop and re-provisions the sandbox it was told to abandon.
    """
    user_id = uuid4()
    service = _service(_FakeSandbox())
    started = asyncio.Event()

    class _Slow:
        async def create_directory(self, *_args: Any, **_kwargs: Any) -> None:
            started.set()
            await asyncio.sleep(30)

    monkeypatch.setattr(service, "_get_manager_client", lambda: _Slow())
    # No cache key: the branch this test is about.
    monkeypatch.setattr(service, "_directory_cache_key", lambda *_a, **_k: None)

    waiting = asyncio.ensure_future(service.get_session(user_id=user_id, pod_id=None))
    await asyncio.wait_for(started.wait(), timeout=5)

    loop_key = (id(asyncio.get_running_loop()), user_id)
    tracked = [
        key for key in service._inflight_directories if key[: len(loop_key)] == loop_key
    ]
    assert tracked, "the task is invisible to stop_sandbox"

    await service.stop_sandbox(user_id)
    waiting.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await waiting
