from __future__ import annotations

from app.modules.connectors.domain.connector import ConnectorEntity, AuthProvider
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.composition.schedule_connectors import (
    ComposioScheduleManager,
    ManagersFactory,
)


def test_manager_factory_prefers_composio_when_connector_has_composio_app_name():
    app_trigger = ConnectorTriggerEntity(
        id="google_calendar:event_created",
        connector_id="google_calendar",
        event_type="event_created",
    )
    connector = ConnectorEntity(
        id="google_calendar",
        composio_toolkit_slug="googlecalendar",
    )

    manager = ManagersFactory.get_manager(
        app_trigger,
        AuthProvider.COMPOSIO.value,
        connector=connector,
    )

    assert isinstance(manager, ComposioScheduleManager)


def test_manager_factory_returns_none_for_lemma_native_provider():
    """Native (lemma) triggers are no longer supported — only composio. A LEMMA
    auth provider with no composio toolkit gets no external manager."""
    app_trigger = ConnectorTriggerEntity(
        id="jira:issue_created",
        connector_id="jira",
        event_type="jira_issue_created",
    )
    connector = ConnectorEntity(
        id="jira",
    )

    manager = ManagersFactory.get_manager(
        app_trigger,
        AuthProvider.LEMMA.value,
        connector=connector,
    )

    assert manager is None
