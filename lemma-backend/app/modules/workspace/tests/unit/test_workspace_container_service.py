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


def _service(
    sandbox: _FakeSandbox,
    *,
    storage_generation_store: object | None = None,
    manager_client: object | None = None,
) -> WorkspaceSandboxService:
    """The service under test, with its collaborators passed in.

    Through the constructor rather than by replacing the service's own
    methods: a double inside the subject certifies the half nobody wrote.
    """
    return WorkspaceSandboxService(
        sandbox=sandbox,  # type: ignore[arg-type]
        storage_generation_store=storage_generation_store,  # type: ignore[arg-type]
        manager_client=manager_client,  # type: ignore[arg-type]
    )


def _mint_session_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session tokens without an identity database: the identity module's
    minter, not the workspace service, is what is replaced."""

    async def mint(**_: object) -> str:
        return "dynamic"

    monkeypatch.setattr(
        "app.modules.identity.contracts.delegated_tokens.mint_delegated_token", mint
    )


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
    manager_client = _FakeManagerClient()

    _mint_session_tokens(monkeypatch)
    service = _service(sandbox, manager_client=manager_client)

    session = await service.get_session(
        user_id=user_id,
        pod_id=None,
        session_id="conversation",
    )

    assert session.logical_id == user_id
    assert session.sandbox_id == str(user_id)
    assert session.client is manager_client
    # What `get_env_vars` built for this session, not a stand-in for it.
    assert session.env_vars["LEMMA_TOKEN"] == "dynamic"
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
    manager_client = _FakeManagerClient()

    _mint_session_tokens(monkeypatch)
    service = _service(sandbox, manager_client=manager_client)

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

    _mint_session_tokens(monkeypatch)
    service = _service(sandbox, manager_client=manager_client)
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
    manager_client = _FakeManagerClient()

    _mint_session_tokens(monkeypatch)
    service = _service(sandbox, manager_client=manager_client)

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

    class _NeverReady:
        async def create_directory(self, *_args: Any, **_kwargs: Any) -> None:
            raise SandboxUnavailable("the guest is not answering")

    monkeypatch.setattr(
        workspace_directory_ensure, "SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS", 0.3
    )
    service = _service(sandbox, manager_client=_NeverReady())
    sandbox_health._capability.update({"status": "ready", "detail": "provisioned"})

    service._ready_directories[
        (id(asyncio.get_running_loop()), user_id, "/x", 1, "g")
    ] = asyncio.get_running_loop().time()

    with pytest.raises(TimeoutError) as caught:
        await service.get_session(user_id=user_id, pod_id=None)

    # The reason survives the loop -- it used to be bound and dropped on every
    # attempt, leaving a bare TimeoutError that said nothing.
    assert "not answering" in str(caught.value)
    assert len(service._ready_directories) == 0
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
    started = asyncio.Event()

    class _Slow:
        async def create_directory(self, *_args: Any, **_kwargs: Any) -> None:
            started.set()
            await asyncio.sleep(30)

    service = _service(_FakeSandbox(), manager_client=_Slow())
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


class _BundledService(WorkspaceSandboxService):
    """A service with a runtime bundle to install, through the seam for it."""

    def _runtime_bundle(self):
        from app.modules.workspace.infrastructure.runtime_bundle import RuntimeBundle

        return RuntimeBundle(
            version="sha256:" + "a" * 64,
            archive=b"PK\x03\x04 pretend this is a zip",
            archive_sha256="sha256:" + "b" * 64,
            requires=("lemma_sdk",),
        )


class _HangingManagerClient(_FakeManagerClient):
    """Answers the directory ensure at once and hangs on file I/O."""

    async def read_file(self, *_args: Any, **_kwargs: Any) -> bytes:
        await asyncio.sleep(30)
        return b""

    async def write_file(self, *_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(30)


@pytest.mark.asyncio
async def test_the_interactive_ceiling_covers_installing_the_runtime_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The directory was the only step the ceiling bounded.

    A first session after an update installs the bundle, on its own much
    longer budget; the caller's fifteen seconds did not apply to it at all.
    The install is shared, so giving up must leave it running for the next
    caller rather than cancel it.
    """
    from sandbox_runtime.errors import SandboxUnavailable

    service = _BundledService(
        sandbox=_FakeSandbox(), manager_client=_HangingManagerClient()
    )  # type: ignore[arg-type]

    started = asyncio.get_running_loop().time()
    with pytest.raises(SandboxUnavailable):
        await service.get_session(
            user_id=uuid4(), pod_id=None, ready_timeout_seconds=0.2
        )
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 5, f"the ceiling did not cover the bundle; waited {elapsed:.1f}s"

    installs = [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("workspace-runtime-bundle:")
    ]
    assert installs and not any(task.cancelled() for task in installs), (
        "giving up must abandon the wait, not the shared install"
    )
    for task in installs:
        task.cancel()


@pytest.mark.asyncio
async def test_a_slow_browser_proxy_write_degrades_within_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Written on every session, and allowed to fail without failing it."""
    service = _service(_FakeSandbox(), manager_client=_HangingManagerClient())

    started = asyncio.get_running_loop().time()
    session = await service.get_session(
        user_id=uuid4(), pod_id=None, env_vars={}, ready_timeout_seconds=0.2
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert session is not None
    assert elapsed < 5, f"the proxy write held the session {elapsed:.1f}s"


@pytest.fixture
def _health_ready():
    """Capability health as the startup probe leaves a working install."""
    from app import sandbox_health

    before = dict(sandbox_health._capability)
    sandbox_health._capability.update({"status": "ready", "detail": "provisioned"})
    yield sandbox_health
    sandbox_health._capability.clear()
    sandbox_health._capability.update(before)


@pytest.mark.asyncio
async def test_a_runtime_that_refuses_the_credential_is_reported_unavailable(
    monkeypatch: pytest.MonkeyPatch, _health_ready
) -> None:
    """Through the real ensure, not the health setter.

    A 401/403 from the runtime is `SandboxUnauthorized`: definitive, so not
    retried -- which also meant it left the loop before the only health update,
    and `/health/capabilities` said `ready` while every operation failed.
    """
    from sandbox_runtime.errors import SandboxUnauthorized

    attempts: list[str] = []

    class _Refuses:
        async def create_directory(self, *_args: Any, **_kwargs: Any) -> None:
            attempts.append("mkdir")
            raise SandboxUnauthorized("the runtime rejected Lemma's credential")

    service = _service(_FakeSandbox(), manager_client=_Refuses())

    with pytest.raises(SandboxUnauthorized):
        await service.get_session(user_id=uuid4(), pod_id=None, env_vars={})

    assert attempts == ["mkdir"], "a refused credential is not retried"
    assert _health_ready.sandbox_capability()["status"] == "unavailable"


@pytest.mark.asyncio
async def test_a_sandbox_that_cannot_be_reconciled_is_reported_unavailable(
    monkeypatch: pytest.MonkeyPatch, _health_ready
) -> None:
    """The reconcile used to sit above the `try`, outside every health update."""
    from sandbox_runtime.errors import SandboxRejected

    sandbox = _FakeSandbox()
    ensures = 0

    async def ensure_once_then_refuse(user_id: UUID) -> SandboxInfo:
        nonlocal ensures
        ensures += 1
        if ensures > 1:
            raise SandboxRejected("capacity exhausted")
        return sandbox.infos.setdefault(user_id, _sandbox_info(user_id))

    monkeypatch.setattr(sandbox, "ensure_sandbox", ensure_once_then_refuse)

    class _NotYet:
        async def create_directory(self, *_args: Any, **_kwargs: Any) -> None:
            raise SandboxUnavailable("the guest is not answering")

    service = _service(sandbox, manager_client=_NotYet())
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    with pytest.raises(SandboxRejected):
        await service.get_session(user_id=uuid4(), pod_id=None, env_vars={})

    assert _health_ready.sandbox_capability()["status"] == "unavailable"


@pytest.mark.asyncio
async def test_a_path_conflict_is_not_a_fabric_outage(
    monkeypatch: pytest.MonkeyPatch, _health_ready
) -> None:
    """Definitive refusals about the directory itself leave health alone."""
    from sandbox_runtime.errors import SandboxPathConflict

    class _IsAFile:
        async def create_directory(self, *_args: Any, **_kwargs: Any) -> None:
            raise SandboxPathConflict("a file is in the way")

    service = _service(_FakeSandbox(), manager_client=_IsAFile())

    with pytest.raises(SandboxPathConflict):
        await service.get_session(user_id=uuid4(), pod_id=None, env_vars={})

    assert _health_ready.sandbox_capability()["status"] == "ready"


@pytest.mark.asyncio
async def test_a_refusal_does_not_hide_a_setup_problem(
    monkeypatch: pytest.MonkeyPatch, _health_ready
) -> None:
    """`needs_setup` is more actionable than any symptom of it."""
    from sandbox_runtime.errors import SandboxUnauthorized

    class _Refuses:
        async def create_directory(self, *_args: Any, **_kwargs: Any) -> None:
            raise SandboxUnauthorized("the runtime rejected Lemma's credential")

    _health_ready._capability.update({"status": "needs_setup", "detail": "no socket"})
    service = _service(_FakeSandbox(), manager_client=_Refuses())

    with pytest.raises(SandboxUnauthorized):
        await service.get_session(user_id=uuid4(), pod_id=None, env_vars={})

    assert _health_ready.sandbox_capability()["status"] == "needs_setup"


_real_sleep = asyncio.sleep


async def _no_sleep(_delay: float, *args: Any) -> None:
    await _real_sleep(0)


@pytest.mark.asyncio
async def test_the_interactive_ceiling_covers_minting_the_session_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token minting and organization resolution are reads like any other."""
    from sandbox_runtime.errors import SandboxUnavailable

    async def never_mints(**_: object) -> str:
        await asyncio.sleep(30)
        return "unreachable"

    monkeypatch.setattr(
        "app.modules.identity.contracts.delegated_tokens.mint_delegated_token",
        never_mints,
    )
    service = _service(_FakeSandbox(), manager_client=_FakeManagerClient())

    started = asyncio.get_running_loop().time()
    with pytest.raises(SandboxUnavailable):
        await service.get_session(
            user_id=uuid4(),
            pod_id=None,
            organization_id=uuid4(),
            ready_timeout_seconds=0.2,
        )
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 5, f"minting the environment held the session {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_a_stalled_storage_generation_read_costs_the_notice_not_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Best effort, so a stall degrades to "not recreated" within the ceiling."""

    class _StalledStore:
        async def observe_storage_generation(self, **_: object) -> bool:
            await asyncio.sleep(30)
            return True

    service = _service(
        _FakeSandbox(),
        storage_generation_store=_StalledStore(),
        manager_client=_FakeManagerClient(),
    )

    started = asyncio.get_running_loop().time()
    session = await service.get_session(
        user_id=uuid4(),
        pod_id=None,
        session_id="conversation",
        env_vars={},
        ready_timeout_seconds=0.2,
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 5, f"the storage read held the session {elapsed:.1f}s"
    assert session is not None
