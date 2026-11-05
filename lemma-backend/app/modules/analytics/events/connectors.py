"""Third-party connectors an organization has authorized."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import AnalyticsActor, emit
from app.modules.analytics.events.wiring import origin_of
from app.modules.connectors.domain.events import (
    ConnectorConnectedEvent,
)

WIRED = frozenset({"connector.connected"})


@projection(
    ConnectorConnectedEvent.get_event_type(),
)
async def on_connector_event(event: dict[str, object]) -> None:
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

    await record()
