"""Restarting a workspace sandbox once, so its server runs the overlay it was sent.

The runtime overlay reaches everything a sandbox starts after it is installed,
and nothing already running. The workspace server is started with the
container, so it runs whatever it imported then: the image's floor on a fresh
container, or the previous overlay on a resumed one. The floor can be older
than the backend -- the image is reused across releases whose image inputs did
not change -- and nothing negotiates a protocol version between the two, so a
server that old could be asked for something it does not understand.

So after the overlay is installed, the server says which code it runs
(`running_runtime_version`, in the `/health` header) and a server running
anything else is restarted once: released and resumed through the provider,
which keeps the home and the overlay, both on their own mounts. Only when
nothing would be lost -- no running process and no Python session -- because
this runs on the way to handing out a session, not beside one. A sandbox busy
now is left to pick the overlay up at its next start.

Functions over the service rather than another mixin on it: the service is
already made of as many classes as the architecture ratchet allows.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.core.bounded import BoundedDict
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.infrastructure.runtime_bundle import RuntimeBundle
from app.modules.workspace.providers.runtime_client import RuntimeState
from app.modules.workspace.services.workspace_runtime_bundle import _SANDBOX_FAILURES
from sandbox_runtime.protocol import WorkloadKind

logger = get_logger(__name__)

#: How many sandboxes' restarts one process remembers. An eviction costs at
#: most one more restart of a sandbox whose server still disagrees.
_REMEMBERED_RESTARTS = 2048

_Incarnation = tuple[int, UUID, str, int, int]
_RestartKey = tuple[int, UUID, str, int, int, str]

#: (loop, user, allocation, epoch, storage generation, version) for every
#: sandbox incarnation already restarted towards that version, recorded before
#: the restart. Both the incarnation that was restarted and the one it came back
#: as are recorded, so a server that still disagrees afterwards -- whatever the
#: provider did to the epoch -- is never restarted again.
_restarted: BoundedDict[_RestartKey, bool] = BoundedDict(
    _REMEMBERED_RESTARTS, name="workspace.restarted_runtimes"
)
_inflight: dict[
    tuple[int, UUID], asyncio.Task[bool]
] = {}  # memory: bounded -- in flight only


class RuntimeRestart(StrEnum):
    """What to do about the server a sandbox is running."""

    #: It runs the overlay just installed.
    CURRENT = "current"
    #: It cannot say what it runs: an image from before the version was
    #: reported, or a fabric with no HTTP runtime. Left alone, as before.
    UNKNOWN = "unknown"
    #: It runs other code, but a restart would end somebody's work.
    BUSY = "busy"
    RESTART = "restart"


def decide_runtime_restart(
    state: RuntimeState | None, installed_version: str
) -> RuntimeRestart:
    """Whether the server in this state should be restarted onto the overlay."""
    if state is None or state.version is None:
        return RuntimeRestart.UNKNOWN
    if state.version == installed_version:
        return RuntimeRestart.CURRENT
    if state.running_processes or state.python_sessions:
        return RuntimeRestart.BUSY
    return RuntimeRestart.RESTART


class _Client(Protocol):
    async def release_sandbox(
        self, workload_kind: WorkloadKind, logical_id: UUID
    ) -> None:
        """Stop the sandbox, keeping its storage; the next ensure resumes it."""


class RestartHost(Protocol):
    """What these functions need from the workspace sandbox service."""

    _installed_bundles: BoundedDict[_Incarnation, str]

    def _get_manager_client(self) -> _Client:
        """The client sandbox operations go through."""

    def _runtime_bundle(self) -> RuntimeBundle | None:
        """The overlay this process installs."""

    def _bundle_cache_key(
        self, user_id: UUID, sandbox_info: SandboxInfo
    ) -> _Incarnation | None:
        """The sandbox incarnation an installed overlay is remembered under."""

    async def get_or_create_sandbox(self, user_id: UUID) -> SandboxInfo:
        """The user's workspace sandbox, resumed or created as needed."""


def was_restarted(user_id: UUID, version: str) -> bool:
    """Whether this process restarted any sandbox of this user towards `version`."""
    return any(key[1] == user_id and key[-1] == version for key in list(_restarted))


async def ensure_runtime_current(
    host: RestartHost, user_id: UUID, sandbox_info: SandboxInfo
) -> bool:
    """Restart the sandbox once if its server is not running the overlay.

    Returns whether it restarted, in which case the caller's `SandboxInfo` is
    stale and must be resolved again. Concurrent callers share one decision, so
    two sessions opening together restart it once.
    """
    key = (id(asyncio.get_running_loop()), user_id)
    task = _inflight.get(key)
    if task is None:
        task = create_inherited_task(
            _restart_if_stale(host, user_id, sandbox_info),
            name=f"workspace-runtime-restart:{user_id}",
        )
        _inflight[key] = task
        task.add_done_callback(
            lambda done: (
                _inflight.pop(key, None) if _inflight.get(key) is done else None
            )
        )
    return await asyncio.shield(task)


async def _restart_if_stale(
    host: RestartHost, user_id: UUID, sandbox_info: SandboxInfo
) -> bool:
    bundle = host._runtime_bundle()
    incarnation = host._bundle_cache_key(user_id, sandbox_info)
    # Only towards an overlay this sandbox is known to have: restarting onto a
    # failed install would bring the server back on the floor.
    if (
        bundle is None
        or incarnation is None
        or host._installed_bundles.get(incarnation) != bundle.version
    ):
        return False
    state = await _runtime_state(host, user_id)
    decision = decide_runtime_restart(state, bundle.version)
    if decision is RuntimeRestart.BUSY:
        logger.info(
            "workspace.runtime_restart.deferred_busy",
            user_id=str(user_id),
            version=bundle.version,
            running_version=state.version if state else None,
        )
        return False
    restart_key = (*incarnation, bundle.version)
    if decision is not RuntimeRestart.RESTART or _restarted.get(restart_key):
        return False
    _restarted[restart_key] = True
    return await _restart(host, user_id, bundle.version, state)


async def _runtime_state(host: RestartHost, user_id: UUID) -> RuntimeState | None:
    """The runtime's own answer, or None when it cannot give one.

    A getattr, because a manager client standing in for a fabric with no HTTP
    runtime has no such call, and that is the same answer as a runtime that
    does not report its version.
    """
    read = getattr(host._get_manager_client(), "runtime_state", None)
    if read is None:
        return None
    try:
        return await read(WorkloadKind.WORKSPACE, user_id)
    except _SANDBOX_FAILURES:
        logger.warning(
            "workspace.runtime_restart.state_unavailable.degraded",
            user_id=str(user_id),
            exc_info=True,
        )
        return None


async def _restart(
    host: RestartHost, user_id: UUID, version: str, before: RuntimeState | None
) -> bool:
    logger.info(
        "workspace.runtime_restart.restarting",
        user_id=str(user_id),
        version=version,
        running_version=before.version if before else None,
    )
    try:
        await host._get_manager_client().release_sandbox(
            WorkloadKind.WORKSPACE, user_id
        )
        resumed = await host.get_or_create_sandbox(user_id)
    except _SANDBOX_FAILURES:
        logger.warning(
            "workspace.runtime_restart.restart_failed.degraded",
            user_id=str(user_id),
            version=version,
            exc_info=True,
        )
        return True
    resumed_incarnation = host._bundle_cache_key(user_id, resumed)
    if resumed_incarnation is not None:
        _restarted[(*resumed_incarnation, version)] = True
    after = await _runtime_state(host, user_id)
    if after is not None and after.version != version:
        logger.error(
            "workspace.runtime_restart.still_stale.failed",
            user_id=str(user_id),
            version=version,
            running_version=after.version,
        )
    return True


__all__ = [
    "RestartHost",
    "RuntimeRestart",
    "decide_runtime_restart",
    "ensure_runtime_current",
    "was_restarted",
]
