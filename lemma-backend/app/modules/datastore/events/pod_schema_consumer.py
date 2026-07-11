"""Eagerly bootstrap datastore schemas from durable pod lifecycle events."""

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.pod.domain.events import PodCreatedEvent, PodEvents

router = RedisRouter()


@reliable_redis_stream_subscriber(
    router,
    PodEvents.STREAM,
    group="pod-provisioning-events",
    consumer="pod-provisioning-events-consumer",
)
async def on_pod_created(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != PodCreatedEvent.get_event_type():
        return

    async def process() -> None:
        parsed = PodCreatedEvent.model_validate(event)
        fs_logger.info(f"Processing PodCreatedEvent for pod {parsed.pod_id}")
        await SchemaManager().create_datastore_schema(parsed.pod_id)
        fs_logger.info(f"Created pod data schema for pod {parsed.pod_id}")

    await inbox.process("datastore.pod-schema-bootstrap", event, process)
