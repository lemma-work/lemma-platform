"""Connector domain events published to the ``connector_events`` stream.

Org-scoped, not pod-scoped. A connector account carries ``organization_id`` and
``user_id`` and nothing else -- there is no pod anywhere in this module -- so the
analytics catalog measures outside reach per organization and says so, rather
than declaring a pod that could never be filled in.
"""

from __future__ import annotations

from uuid import UUID

from app.core.domain.events import DomainEvent

CONNECTOR_EVENTS_STREAM = "connector_events"


class ConnectorDomainEvent(DomainEvent):
    @classmethod
    def stream_name(cls) -> str:
        return CONNECTOR_EVENTS_STREAM


class ConnectorConnectedEvent(ConnectorDomainEvent):
    event_type: str = "connector.connected"
    #: A slug (`gmail`, `agent-delete-app-4e9491dc`), not a UUID -- connectors are
    #: keyed by name throughout this module. Bounded enough to cross the
    #: analytics boundary, which accepts identifier-shaped strings.
    connector_id: str
    organization_id: UUID
    user_id: UUID
