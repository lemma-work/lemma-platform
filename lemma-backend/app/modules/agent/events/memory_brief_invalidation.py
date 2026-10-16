"""Drop a cached memory-brief section when the file behind it changes.

An after-commit projection on the domain-event sink (``core/analytics/
domain_events``), registered in every process. The pod tools already
invalidate inline, which covers agents writing through ``pod_write_file``. This
covers the other writer, and it is the common one: an agent with a shell writes
memory with ``lemma files write``, which reaches the datastore over HTTP in the
API process and never enters the worker that ran the agent.

Best effort by design, as the stream consumer it replaces was: deleting a cache
entry twice costs nothing, and an event this misses (a full sink drops its
oldest, a crashed process loses what it had queued) costs only the cache TTL --
the behaviour that existed before any invalidation at all. The file write is
committed before this runs, so a projection never drops a section for a write
that rolled back.
"""

from __future__ import annotations

from uuid import UUID

from app.core.analytics.domain_events import projection
from app.modules.agent.services.agent_memory_brief import invalidate_memory_brief
from app.modules.datastore.contracts import (
    DatastoreFileCreatedEvent,
    DatastoreFileDeletedEvent,
    DatastoreFileUpdatedEvent,
)


@projection(
    DatastoreFileCreatedEvent.get_event_type(),
    DatastoreFileUpdatedEvent.get_event_type(),
    DatastoreFileDeletedEvent.get_event_type(),
)
async def on_datastore_file_written(event: dict[str, object]) -> None:
    pod_id = event.get("pod_id")
    path = event.get("path")
    if not pod_id or not path:
        return
    actor_id = event.get("actor_id")
    await invalidate_memory_brief(
        pod_id=UUID(str(pod_id)),
        path=str(path),
        user_id=UUID(str(actor_id)) if actor_id else None,
    )
