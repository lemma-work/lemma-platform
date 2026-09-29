"""A sandbox whose server is not running the installed overlay is restarted once.

The server imports its code when the container starts, so after an install it
still runs whatever it started with: the image's floor, which image reuse lets
fall behind the backend, or the previous overlay. These pin when that is worth
one restart, and that it is never worth two.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.bounded import BoundedDict
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.infrastructure.runtime_bundle import RuntimeBundle
from app.modules.workspace.providers.runtime_client import RuntimeState
from app.modules.workspace.services.workspace_runtime_bundle import (
    _REMEMBERED_SANDBOXES,
    WorkspaceRuntimeBundleMixin,
)
from app.modules.workspace.services.workspace_runtime_restart import (
    RuntimeRestart,
    decide_runtime_restart,
    ensure_runtime_current,
)
from sandbox_runtime.errors import SandboxUnavailable

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_VERSION = "sha256:" + "a" * 64
_OLDER = "sha256:" + "c" * 64
_BUNDLE = RuntimeBundle(
    version=_VERSION,
    archive=b"PK",
    archive_sha256="sha256:" + "b" * 64,
    requires=("lemma_sdk",),
)


def _state(
    version: str | None, *, processes: int = 0, sessions: int = 0
) -> RuntimeState:
    return RuntimeState(
        version=version, running_processes=processes, python_sessions=sessions
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (None, RuntimeRestart.UNKNOWN),
        (_state(None), RuntimeRestart.UNKNOWN),
        (_state(_VERSION), RuntimeRestart.CURRENT),
        (_state("floor"), RuntimeRestart.RESTART),
        (_state(_OLDER), RuntimeRestart.RESTART),
        (_state("floor", processes=1), RuntimeRestart.BUSY),
        (_state(_OLDER, sessions=1), RuntimeRestart.BUSY),
        # Busy or not, a server already running the overlay needs nothing.
        (_state(_VERSION, processes=3, sessions=2), RuntimeRestart.CURRENT),
    ],
)
async def test_the_decision(
    state: RuntimeState | None, expected: RuntimeRestart
) -> None:
    assert decide_runtime_restart(state, _VERSION) is expected


class _Client:
    """A manager client whose sandbox restarts onto `after` when released."""

    def __init__(self, before: RuntimeState | None, after: RuntimeState | None) -> None:
        self.states = [before, after]
        self.released: list[UUID] = []
        self.fail_release = False

    async def runtime_state(self, _kind: Any, _user_id: UUID) -> RuntimeState | None:
        return self.states[min(len(self.released), 1)]

    async def release_sandbox(self, _kind: Any, user_id: UUID) -> None:
        if self.fail_release:
            raise SandboxUnavailable("the provider refused")
        self.released.append(user_id)


class _ClientWithoutRuntime:
    """A fabric with no HTTP runtime: no `runtime_state` at all."""

    def __init__(self) -> None:
        self.released: list[UUID] = []

    async def release_sandbox(self, _kind: Any, user_id: UUID) -> None:
        self.released.append(user_id)


class _Service(WorkspaceRuntimeBundleMixin):
    def __init__(
        self, client: Any, *, installed: bool = True, epoch_after: int = 2
    ) -> None:
        self.client = client
        self.epoch_after = epoch_after
        self._installed_bundles = BoundedDict(_REMEMBERED_SANDBOXES)
        self._inflight_bundles = {}
        self.installed = installed

    def _get_manager_client(self) -> Any:
        return self.client

    def _runtime_bundle(self) -> RuntimeBundle | None:
        return _BUNDLE

    async def get_or_create_sandbox(self, user_id: UUID) -> SandboxInfo:
        return _info(epoch=self.epoch_after)

    async def current(self, user_id: UUID, info: SandboxInfo) -> bool:
        return await ensure_runtime_current(self, user_id, info)

    async def prepare(self, user_id: UUID, info: SandboxInfo) -> None:
        """Record the overlay installed, as `_ensure_runtime_bundle` would."""
        key = self._bundle_cache_key(user_id, info)
        if self.installed and key is not None:
            self._installed_bundles[key] = _VERSION


def _info(*, epoch: int = 1) -> SandboxInfo:
    return SandboxInfo(
        sandbox_id=str(uuid4()),
        status="RUNNING",
        image="",
        allocation_id="alloc-1",
        allocation_epoch=epoch,
        storage_generation=1,
    )


async def test_a_server_on_the_floor_is_restarted_once_onto_the_overlay() -> None:
    user_id = uuid4()
    client = _Client(_state("floor"), _state(_VERSION))
    service = _Service(client)
    info = _info()
    await service.prepare(user_id, info)

    assert await service.current(user_id, info) is True
    assert client.released == [user_id]

    # The next session, on the container it came back as.
    resumed = await service.get_or_create_sandbox(user_id)
    await service.prepare(user_id, resumed)
    assert await service.current(user_id, resumed) is False
    assert client.released == [user_id]


async def test_a_server_that_is_still_stale_after_its_restart_is_not_restarted_again() -> (
    None
):
    """The loop guard: one restart per incarnation, whatever happens to the epoch."""
    user_id = uuid4()
    client = _Client(_state("floor"), _state("floor"))
    service = _Service(client)
    info = _info()
    await service.prepare(user_id, info)

    assert await service.current(user_id, info) is True
    for incarnation in (info, await service.get_or_create_sandbox(user_id)):
        await service.prepare(user_id, incarnation)
        assert await service.current(user_id, incarnation) is False

    assert client.released == [user_id]


async def test_a_busy_sandbox_is_left_running() -> None:
    """Work in flight outranks being current; the next start picks it up."""
    user_id = uuid4()
    for busy in (_state("floor", processes=1), _state(_OLDER, sessions=1)):
        client = _Client(busy, _state(_VERSION))
        service = _Service(client)
        info = _info()
        await service.prepare(user_id, info)

        assert await service.current(user_id, info) is False
        assert client.released == []


async def test_a_runtime_that_does_not_say_what_it_runs_is_left_alone() -> None:
    """An image from before the version header: today's behaviour, unchanged."""
    user_id = uuid4()
    for client in (_Client(_state(None), _state(None)), _ClientWithoutRuntime()):
        service = _Service(client)
        info = _info()
        await service.prepare(user_id, info)

        assert await service.current(user_id, info) is False
        assert client.released == []


async def test_nothing_is_restarted_towards_an_overlay_that_did_not_install() -> None:
    """The server would come back on the floor, no better than it was."""
    user_id = uuid4()
    client = _Client(_state("floor"), _state(_VERSION))
    service = _Service(client, installed=False)
    info = _info()
    await service.prepare(user_id, info)

    assert await service.current(user_id, info) is False
    assert client.released == []


async def test_a_failed_restart_is_reported_and_not_retried() -> None:
    user_id = uuid4()
    client = _Client(_state("floor"), _state(_VERSION))
    client.fail_release = True
    service = _Service(client)
    info = _info()
    await service.prepare(user_id, info)

    # True: the sandbox may have gone down part-way, so the caller re-resolves.
    assert await service.current(user_id, info) is True
    client.fail_release = False
    assert await service.current(user_id, info) is False
    assert client.released == []
