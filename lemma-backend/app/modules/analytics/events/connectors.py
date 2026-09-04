"""Third-party connectors an organization has authorized."""

from __future__ import annotations

from faststream import Depends, Logger

from app.core.analytics import AnalyticsActor, emit
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.analytics.events.wiring import origin_of, router
from app.modules.connectors.domain.events import (
    CONNECTOR_EVENTS_STREAM,
    ConnectorConnectedEvent,
)

WIRED = frozenset({"connector.connected"})


@reliable_redis_stream_subscriber(
    router,
    CONNECTOR_EVENTS_STREAM,
    group="analytics-connector",
    consumer="analytics-connector-consumer",
)
async def on_connector_event(
    event: dict[str, object],
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != ConnectorConnectedEvent.get_event_type():
        return

    async def record() -> None:
        parsed = ConnectorConnectedEvent.model_validate(event)
        emit(
            "connector.connected",
            actor=AnalyticsActor.user(parsed.user_id),
            origin=origin_of(event),
            organization_id=parsed.organization_id,
            properties={"connector_id": parsed.connector_id},
        )

    await inbox.process("analytics.connector", event, record)
