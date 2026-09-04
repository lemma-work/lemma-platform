"""Tables and documents added to a pod."""

from __future__ import annotations

from faststream import Depends, Logger

from app.core.analytics import emit
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.analytics.events.wiring import actor_or_system, origin_of, router
from app.modules.analytics.services.buckets import bytes_bucket, document_kind
from app.modules.datastore.domain.events import (
    DATASTORE_EVENTS_STREAM,
    DatastoreFileCreatedEvent,
    DatastoreTableCreatedEvent,
)

WIRED = frozenset({"table.created", "document.added"})


@reliable_redis_stream_subscriber(
    router,
    DATASTORE_EVENTS_STREAM,
    group="analytics-datastore",
    consumer="analytics-datastore-consumer",
)
async def on_datastore_event(
    event: dict[str, object],
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
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
                pod_id=parsed.pod_id,
                properties={"pod_id": parsed.pod_id, "table_id": parsed.table_id},
            )
        else:
            parsed_file = DatastoreFileCreatedEvent.model_validate(event)
            emit(
                "document.added",
                actor=actor_or_system(parsed_file.actor_id),
                origin=origin,
                pod_id=parsed_file.pod_id,
                # `path` is deliberately not forwarded: it is a filename, and
                # filenames are business content.
                properties={
                    "pod_id": parsed_file.pod_id,
                    "document_id": parsed_file.file_id,
                    # The kind, from a closed set, never the raw extension.
                    "kind": document_kind(parsed_file.path),
                    "size_bucket": bytes_bucket(
                        (parsed_file.metadata or {}).get("size_bytes")
                        if isinstance(parsed_file.metadata, dict)
                        else None
                    ),
                },
            )

    await inbox.process("analytics.datastore", event, record)
