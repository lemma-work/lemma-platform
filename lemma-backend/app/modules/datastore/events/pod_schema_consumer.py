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
from app.core.log.log import get_logger
from app.modules.datastore.api.dependencies import get_schema_manager
from app.modules.pod.domain.events import PodCreatedEvent, PodEvents

router = RedisRouter()
logger = get_logger(__name__)


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
        # The process singleton, not a fresh manager: it remembers that the
        # query role is already ensured, so each pod costs no catalog probe.
        await get_schema_manager().create_datastore_schema(parsed.pod_id)

    await inbox.process("datastore.pod-schema-bootstrap", event, process)
