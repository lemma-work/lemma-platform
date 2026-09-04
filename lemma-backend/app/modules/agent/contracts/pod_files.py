"""A run's authority to touch its pod's datastore, for a module that has none.

`agent_surfaces` sends an email with the files a run attached to it, and those
files live in the pod datastore. Datastore publishes the reads already
(`datastore/contracts/surfaces.py`); what it needs and cannot make is the
authorization to perform them. Only `agent` can build that, because it is
derived from the run: the workload behind the tool call, whether the run is the
pod's default agent, and the conversation a session approval was granted
against.

So this publishes the authority, not the reads. `agent_surfaces` opens the
block, calls datastore's operations inside it, and no service crosses a module
boundary in either direction.

Replaces `pod_services` in `app/composition/surface_agent.py`, which handed out
a `PodServices` carrying datastore's table, record and file services -- three
services, to a caller that read a file.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.file_access import is_datastore_path
from app.modules.agent.tools.pod.pod_data_access import pod_services


@dataclass(frozen=True, slots=True)
class PodDatastoreAccess:
    """A unit of work, and the run's authority to use it against ``pod_id``."""

    uow: SqlAlchemyUnitOfWork
    ctx: Context
    pod_id: UUID


def is_pod_datastore_path(path: str) -> bool:
    """True when this attachment path addresses the pod datastore.

    The alternative is the run's sandbox workspace, which the caller reads
    through its own file manager and which datastore knows nothing about.
    """
    return is_datastore_path(path)


@asynccontextmanager
async def pod_datastore_access(
    deps: BaseAgentContext,
) -> AsyncIterator[PodDatastoreAccess]:
    """The run's delegated-workload authorization, for the length of the block.

    Ambient for the duration, because datastore's record authorization reads the
    current context rather than taking one. Commits on a clean exit.
    """
    async with pod_services(deps) as services:
        yield PodDatastoreAccess(
            uow=services.uow, ctx=services.ctx, pod_id=deps.pod_id
        )


__all__ = ["PodDatastoreAccess", "is_pod_datastore_path", "pod_datastore_access"]
