"""Reading one sandbox process's state, without provisioning anything.

This exists for durable waits: a suspended agent run has to be told when the
build it is waiting on finished, and nothing anywhere publishes a process exit.
The E2B watcher records the exit into Redis and tells nobody
(`providers/e2b_process_lifetime.watch_for_exit`), and docker and lemma_local
keep terminal state inside the sandbox. So the answer has to be pulled, on a
worker, a few seconds at a time.

The shape is deliberately `SandboxSweeper._is_busy`'s, because that method had
to learn the same three lessons the hard way and they are all still true here:
resolve the instance through the provider rather than assembling one from the
row, read *state* rather than exit code, and never read an unreachable sandbox
as a finished one.

What it must not do is go through `WorkspaceSandboxService.get_session`. That
runs `get_or_create_sandbox` and `_ensure_workspace_directory`, so a poll every
few seconds would re-provision a sandbox that was deliberately released and
touch `last_used_at` each time -- pinning forever exactly the thing the idle
sweep exists to reclaim.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.workspace.domain.sandbox import SandboxKind, SandboxOwnerKind
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.process_output import has_stopped
from app.modules.workspace.providers.base import (
    ProviderFailed,
    ProviderGone,
    ProviderNotReady,
    ProviderRejected,
)
from sandbox_runtime.errors import SandboxError

logger = get_logger(__name__)


class SandboxRepositoryPort(Protocol):
    """The two reads this needs, named so the seam has a type rather than `Any`."""

    async def list_for_owner(
        self, *, kind: SandboxKind, owner_kind: SandboxOwnerKind, owner_id: UUID
    ) -> tuple[object, ...]: ...

    async def current_instance(self, sandbox_id: UUID) -> object | None: ...


# One probe's whole budget. Short because a caller is holding a worker slot for
# it and there is another check a few seconds behind this one -- a slow answer
# is worth less than a quick "ask me again".
_PROBE_DEADLINE_SECONDS = 15


class ProcessProbeStatus(str, Enum):
    """What could be established about the process, which is not always much."""

    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    #: The sandbox is not there any more, or no longer reports this process. Both
    #: mean the same thing to a caller: the outcome cannot be learned here, ever.
    #: Not distinguished from "never existed" because nothing can distinguish
    #: them -- a finished process ages out of the index like any other.
    GONE = "GONE"
    #: The provider could not be reached. Says nothing about the process, and in
    #: particular does not say it stopped; ask again.
    UNREADABLE = "UNREADABLE"


@dataclass(frozen=True, slots=True)
class ProcessProbe:
    status: ProcessProbeStatus
    exit_code: int | None = None
    started_at: datetime | None = None
    command: str = ""


async def probe_process(
    *,
    user_id: UUID,
    process_id: str,
    service=None,
    uow_factory=None,
    repository: Callable[[object], SandboxRepositoryPort] = SandboxRepository,
) -> ProcessProbe:
    """Read one process in one of ``user_id``'s workspace sandboxes.

    The three collaborators are parameters, defaulting to the ones the sweep
    uses, so a test can drive this without a provider or a database. That is the
    only way the four outcomes below are cheap to cover -- and the alternative,
    reaching in to replace a name inside this module, both proves less and
    leaks into whatever runs next.
    """
    if uow_factory is None:
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

        uow_factory = SessionUnitOfWorkFactory(async_session_maker)

    async with uow_factory() as uow:
        sandboxes_of = repository(uow)
        sandboxes = await sandboxes_of.list_for_owner(
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=user_id,
        )
        instances = [
            (sandbox, await sandboxes_of.current_instance(sandbox.id))
            for sandbox in sandboxes
        ]

    live = [
        instance
        for _sandbox, instance in instances
        if instance is not None and instance.provider_id
    ]
    if not live:
        return ProcessProbe(status=ProcessProbeStatus.GONE)

    # Only now is a provider needed. Building one first meant a deployment
    # without sandbox credentials raised from inside a wait that had a perfectly
    # good answer available from the database alone -- there is no sandbox, so
    # the process is gone -- and the exception reached the resolver rather than
    # the agent.
    if service is None:
        from app.modules.workspace.services.sandbox_composition import (
            get_sandbox_service,
        )

        service = get_sandbox_service()
    provider = service._provider
    deadline_at = datetime.now(timezone.utc) + timedelta(
        seconds=_PROBE_DEADLINE_SECONDS
    )

    for instance in live:
        try:
            resolved = await provider.inspect(
                instance.provider_id, deadline_at=deadline_at
            )
            if resolved is None:
                # The sweeper learned this one by releasing a sandbox mid-command:
                # a transient empty result from the provider's metadata listing is
                # not evidence that anything stopped.
                return ProcessProbe(status=ProcessProbeStatus.UNREADABLE)
            processes = await provider.list_processes(resolved, deadline_at=deadline_at)
        except (
            SandboxError,
            ProviderFailed,
            ProviderGone,
            ProviderNotReady,
            ProviderRejected,
        ) as exc:
            logger.debug(
                "workspace.process_probe.unreadable",
                process_id=process_id,
                error_type=type(exc).__name__,
            )
            return ProcessProbe(status=ProcessProbeStatus.UNREADABLE)

        for descriptor in processes:
            if str(descriptor.process_id) != process_id:
                continue
            status = (
                ProcessProbeStatus.FINISHED
                if has_stopped(descriptor)
                else ProcessProbeStatus.RUNNING
            )
            return ProcessProbe(
                status=status,
                exit_code=descriptor.exit_code,
                started_at=descriptor.started_at,
                command=descriptor.command,
            )

    return ProcessProbe(status=ProcessProbeStatus.GONE)
