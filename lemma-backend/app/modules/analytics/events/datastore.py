"""Tables and documents added to a pod."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import emit
from app.modules.analytics.events.wiring import (
    ProjectionReads,
    actor_or_system,
    origin_of,
)
from app.modules.analytics.services.buckets import bytes_bucket, document_kind
from app.modules.datastore.domain.events import (
    DatastoreFileCreatedEvent,
    DatastoreTableCreatedEvent,
)

WIRED = frozenset({"table.created", "document.added"})


@projection(
    DatastoreTableCreatedEvent.get_event_type(),
    DatastoreFileCreatedEvent.get_event_type(),
)
async def on_datastore_event(
    event: dict[str, object],
    reads: ProjectionReads | None = None,
) -> None:
    reads = reads or ProjectionReads()
    event_type = event.get("event_type")
    if event_type not in {
        DatastoreTableCreatedEvent.get_event_type(),
        DatastoreFileCreatedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = origin_of(event)
        if event_type == DatastoreTableCreatedEvent.get_event_type():
            parsed = DatastoreTableCreatedEvent.model_validate(event)
            emit(
                "table.created",
                actor=actor_or_system(parsed.actor_id),
                origin=origin,
                organization_id=await reads.organization_of_pod(parsed.pod_id),
                pod_id=parsed.pod_id,
                properties={"pod_id": parsed.pod_id, "table_id": parsed.table_id},
            )
        else:
            parsed_file = DatastoreFileCreatedEvent.model_validate(event)
            emit(
                "document.added",
                actor=actor_or_system(parsed_file.actor_id),
                origin=origin,
                organization_id=await reads.organization_of_pod(parsed_file.pod_id),
                pod_id=parsed_file.pod_id,
                # `path` is deliberately not forwarded: it is a filename, and
                # filenames are business content.
                properties={
                    "pod_id": parsed_file.pod_id,
                    "document_id": parsed_file.file_id,
                    # The kind, from a closed set, never the raw extension.
                    "kind": document_kind(parsed_file.path),
                    # The file's own size. This used to be read from the file's
                    # metadata, which nothing writes a size into, so the band
                    # was always empty.
                    "size_bucket": bytes_bucket(parsed_file.size_bytes),
                },
            )

    await record()
