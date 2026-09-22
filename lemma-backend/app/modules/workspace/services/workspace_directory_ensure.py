"""Making a conversation's working directory exist, and saying so when it cannot.

Its own module because the file it came from is at the size limit, and this is
the part with a shape of its own: a cache, one in-flight task shared by every
caller asking for the same directory, and a retry loop bounded by two different
deadlines -- the sandbox manager's, and the one a person waiting on a file
listing can afford.

A mixin rather than a helper class for the same reason `WorkspaceRuntimeBundleMixin`
is one: it needs the service's manager client, its caches and its
`get_or_create_sandbox`, and threading all four through free functions would
say less than it costs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from time import monotonic
from uuid import UUID

from sandbox_runtime.errors import SandboxError, SandboxUnauthorized, SandboxUnavailable

from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.sandbox_health import record_sandbox_reachable, record_sandbox_unreachable

logger = get_logger(__name__)

#: How long the sandbox manager is given to make a workspace usable. Long
#: because a first boot really does pull an image.
SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS = 300.0
# How long a created workspace directory is believed without re-checking. Long
# enough that a run's tool calls stop paying for it, short enough that an agent
# which deleted its own working directory recovers on its own.
DIRECTORY_READY_SECONDS = 60.0
# How long a person waits. The 300s ceiling above is the sandbox manager's, and
# it is right for a first boot that is genuinely pulling an image -- but it was
# also what a file listing waited, so a browser pane spun for five minutes and
# then showed a 500. Interactive callers pass this instead; the work itself is
# shielded and keeps running for whoever asks next.
INTERACTIVE_READY_SECONDS = 15.0


#: `(loop id, user, path, allocation epoch, storage generation)`. The last two
#: are what let a recreated workspace stop looking ready; when they cannot be
#: resolved the key still identifies the work, just not its freshness.
_CacheKey = tuple[int, UUID, str, int, str]


class ReadyBudget:
    """What is left of an interactive caller's patience.

    `None` means no ceiling, which is what a background caller gets: the
    sandbox manager's own deadline is the only limit that applies to it.

    One absolute deadline for the whole of `get_session`, not one per step.
    Acquiring the sandbox, creating the directory, installing the runtime
    bundle and writing the browser-proxy decision are four waits; a ceiling
    applied to one of them bounds nothing.
    """

    __slots__ = ("_deadline",)

    def __init__(self, seconds: float | None) -> None:
        self._deadline = None if seconds is None else monotonic() + seconds

    def remaining(self) -> float | None:
        if self._deadline is None:
            return None
        # Never zero or negative: `wait_for(0)` is an immediate timeout, and a
        # caller that has just run out should get one attempt's worth of answer
        # rather than a guaranteed failure.
        return max(0.05, self._deadline - monotonic())


class WorkspaceDirectoryEnsureMixin:
    """Ensures the conversation directory exists before a session opens.

    The three names below belong to the service that mixes this in; declared
    here so the class is readable on its own and the architecture gate can see
    what it reaches for.
    """

    _ready_directories: dict[tuple[int, UUID, str, int, str], float]
    _inflight_directories: dict[
        tuple[int, UUID, str, int, str], "asyncio.Task[SandboxInfo]"
    ]

    def _get_manager_client(self) -> "LocalSandboxClient":  # pragma: no cover
        raise NotImplementedError

    async def get_or_create_sandbox(  # pragma: no cover
        self, user_id: UUID, *, force_reconcile: bool = False
    ) -> SandboxInfo:
        raise NotImplementedError

    @classmethod
    def forget_workspace(cls, user_id: UUID) -> None:  # pragma: no cover
        raise NotImplementedError

    async def _ensure_workspace_directory(
        self,
        user_id: UUID,
        path: str,
        *,
        budget: ReadyBudget | None = None,
    ) -> SandboxInfo:
        deadline_at = datetime.now(timezone.utc) + timedelta(
            seconds=SANDBOX_MANAGER_HTTP_TIMEOUT_SECONDS
        )
        # The caller's budget across both phases. Acquiring the sandbox and
        # creating the directory are two waits, and the ceiling used to apply
        # only to the second -- so an interactive caller that asked for fifteen
        # seconds could spend the manager's full three hundred in
        # `get_or_create_sandbox` before its own limit was even consulted.
        budget = budget or ReadyBudget(None)
        sandbox_info = await self._await_shared(
            self.get_or_create_sandbox(user_id), budget.remaining()
        )
        resolved = self._directory_cache_key(user_id, path, sandbox_info)
        # Two separate questions, and conflating them was the bug here.
        #
        # Whether readiness may be *remembered* needs an epoch and a storage
        # generation, so that a recreated workspace stops looking ready. When
        # those are missing there is no safe key and readiness is not cached.
        #
        # Whether the in-flight task can be *cancelled* needs only the user, and
        # `stop_sandbox` cancels by prefix. A task outside that map survives the
        # stop and can re-provision the sandbox it was told to abandon, so this
        # branch still registers one -- under a sentinel key nothing reads back.
        cacheable = resolved is not None
        cache_key = resolved or (id(asyncio.get_running_loop()), user_id, path, -1, "")

        if cacheable and self._directory_is_still_ready(cache_key):
            # The freshly resolved info, never the one cached alongside the
            # readiness. Its storage generation is what tells a conversation
            # its workspace was recreated, the generation is not in the key,
            # and it is bumped in a different transaction from the epoch --
            # so returning a remembered copy can swallow the one notice that
            # stops an agent reading an empty workspace as "nothing was ever
            # here".
            return sandbox_info

        task = self._directory_task(
            cache_key,
            user_id,
            path,
            sandbox_info=sandbox_info,
            deadline_at=deadline_at,
        )
        info = await self._await_directory(task, budget.remaining())
        if cacheable:
            self._ready_directories[cache_key] = asyncio.get_running_loop().time()
        return info

    def _directory_is_still_ready(self, cache_key: _CacheKey) -> bool:
        """Whether this directory was made recently enough to be believed."""
        ready_at = self._ready_directories.get(cache_key)
        if ready_at is None:
            return False
        if (asyncio.get_running_loop().time() - ready_at) < DIRECTORY_READY_SECONDS:
            return True
        self._ready_directories.pop(cache_key, None)
        return False

    def _directory_task(
        self,
        cache_key: _CacheKey,
        user_id: UUID,
        path: str,
        *,
        sandbox_info: SandboxInfo,
        deadline_at: datetime,
    ) -> "asyncio.Task[SandboxInfo]":
        """The one ensure for this directory, joined rather than duplicated.

        Registered under `cache_key` whether or not readiness may be cached:
        `stop_sandbox` cancels by prefix, and a task outside this map survives
        the stop and can re-provision the sandbox it was told to abandon.
        """
        existing = self._inflight_directories.get(cache_key)
        if existing is not None:
            return existing

        task = create_inherited_task(
            self._create_workspace_directory_until_ready(
                user_id,
                path,
                sandbox_info=sandbox_info,
                deadline_at=deadline_at,
            ),
            name=f"workspace-directory-ensure:{user_id}:{path}",
        )
        self._inflight_directories[cache_key] = task

        def clear(completed: "asyncio.Task[SandboxInfo]") -> None:
            if self._inflight_directories.get(cache_key) is completed:
                self._inflight_directories.pop(cache_key, None)

        task.add_done_callback(clear)
        return task

    @staticmethod
    async def _await_shared[T](
        coroutine: "Coroutine[object, object, T]",
        ready_timeout_seconds: float | None,
    ) -> T:
        """Wait on work that is shared with other callers, but only so long.

        `get_or_create_sandbox` already shields the singleflight task it awaits,
        so giving up here abandons this caller's wait and leaves the ensure
        running for whoever else is waiting on it.
        """
        if ready_timeout_seconds is None:
            return await coroutine
        try:
            return await asyncio.wait_for(coroutine, timeout=ready_timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise SandboxUnavailable(
                "workspace is still starting; it was not ready within "
                f"{ready_timeout_seconds:.0f}s"
            ) from exc

    @staticmethod
    async def _await_directory(
        task: "asyncio.Task[SandboxInfo]",
        ready_timeout_seconds: float | None,
    ) -> SandboxInfo:
        """Wait for the ensure, but only as long as this caller can afford.

        `shield` rather than cancellation: a caller giving up must not abort a
        first boot that a slower caller is still legitimately waiting on. The
        work keeps running and the next request finds it in `_inflight`.
        """
        if ready_timeout_seconds is None:
            return await asyncio.shield(task)
        try:
            return await asyncio.wait_for(
                asyncio.shield(task), timeout=ready_timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            # The task outlives this request by design, so nobody is left to
            # read its outcome. Retrieve it on completion or asyncio reports an
            # unretrieved exception against a task that failed as expected.
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
            raise SandboxUnavailable(
                "workspace is still starting; it was not ready within "
                f"{ready_timeout_seconds:.0f}s"
            ) from exc

    async def _create_workspace_directory_until_ready(
        self,
        user_id: UUID,
        path: str,
        *,
        sandbox_info: SandboxInfo,
        deadline_at: datetime,
    ) -> SandboxInfo:
        force_reconcile = False
        attempts = 0
        last_error: SandboxUnavailable | None = None
        while datetime.now(timezone.utc) < deadline_at:
            reconciling = force_reconcile
            try:
                if reconciling:
                    sandbox_info = await self.get_or_create_sandbox(
                        user_id,
                        force_reconcile=True,
                    )
                    reconciling = False
                await self._get_manager_client().create_directory(
                    user_id,
                    path,
                    deadline_at=deadline_at,
                )
            except SandboxUnavailable as exc:
                attempts += 1
                last_error = exc
                remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:
                    break
                delay = max(0.05, (exc.retry_after_ms or 250) / 1000)
                await asyncio.sleep(min(delay, remaining))
                force_reconcile = True
                continue
            except SandboxError as exc:
                # Definitive, so not retried -- but a fabric that refuses Lemma's
                # credential, or cannot be reconciled at all, is not usable, and
                # capability health has to say so. Only those: a path conflict
                # is about this directory, not about the fabric.
                if reconciling or isinstance(exc, SandboxUnauthorized):
                    record_sandbox_unreachable()
                    logger.warning(
                        "workspace.sandbox_service.directory_ensure_refused.degraded",
                        user_id=str(user_id),
                        path=path,
                        reconciling=reconciling,
                        error_type=type(exc).__name__,
                    )
                raise
            record_sandbox_reachable()
            return sandbox_info
        # Every attempt raised, and until this was written each one's reason was
        # bound and dropped. What reached the caller was a bare `TimeoutError`
        # after the full deadline -- rendered as `500 INTERNAL_ERROR` with a null
        # message -- so the one sentence saying why a workspace never came up
        # existed on every iteration and survived none of them.
        reason = str(last_error) if last_error else "no attempt completed"
        # Everything remembered about this workspace was learned from a fabric
        # that has now failed every attempt, so none of it is worth believing:
        # the readiness cache would otherwise let the next request skip the
        # ensure entirely and go straight to an operation against the same dead
        # endpoint, for up to a minute.
        #
        # Forgetting, not replacing. On Desktop and E2B the sandbox *is* the
        # storage -- `ProviderStorageKind.SANDBOX_NATIVE` -- so destroying the
        # instance to get a fresh one would take the user's files with it.
        # Recovery here means dropping what we think we know and asking again.
        self.forget_workspace(user_id)
        # `/health/capabilities` said `ready` throughout an outage in which
        # every file listing timed out, because the startup probe only ever
        # proved a provider object could be built. An operation that gave up is
        # the strongest evidence there is that the fabric is not usable.
        record_sandbox_unreachable()
        logger.warning(
            "workspace.sandbox_service.directory_ensure_exhausted.degraded",
            user_id=str(user_id),
            path=path,
            attempts=attempts,
            reason=reason,
        )
        raise TimeoutError(
            f"workspace sandbox {user_id} did not become usable "
            f"after {attempts} attempts: {reason}"
        )

    def _directory_cache_key(
        self,
        user_id: UUID,
        path: str,
        sandbox_info: SandboxInfo,
    ) -> tuple[int, UUID, str, int, str] | None:
        """Identity for "this directory exists", which is the disk's, not the
        container's.

        ``/workspace`` is the mounted volume, so whether the directory is there
        is a property of the storage rather than of whichever container is
        currently attached to it. Keyed by the allocation epoch, a container
        recreate invalidated a directory that had never gone away -- paying a
        round trip to make a directory that was already present -- while a
        storage generation moving underneath the same epoch, which is the case
        where the files really are gone, did not invalidate anything.

        Keyed by the storage generation both come out right: a recreate keeps
        the entry, and a reset drops it.
        """
        if (
            sandbox_info.allocation_id is None
            or sandbox_info.storage_generation is None
        ):
            return None
        # The leading (loop id, user id) must stay a prefix of the ensure key:
        # stop_sandbox cancels directory tasks by matching that prefix.
        return (
            id(asyncio.get_running_loop()),
            user_id,
            sandbox_info.allocation_id,
            sandbox_info.storage_generation,
            path,
        )
