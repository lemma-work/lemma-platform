from __future__ import annotations

from uuid import uuid4

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connector import (
    AuthProvider,
    AuthScheme,
    ConnectorKind,
)
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.composition.schedule_connectors import (
    ComposioScheduleManager,
    ManagersFactory,
)


def _install(
    connector_id: str, *, toolkit_slug: str | None = None
) -> ResolvedAuthInstall:
    return ResolvedAuthInstall(
        connector_id=connector_id,
        kind=ConnectorKind.COMPOSIO if toolkit_slug else ConnectorKind.PACKAGE,
        auth_scheme=AuthScheme.OAUTH2,
        auth_config_id=uuid4(),
        organization_id=uuid4(),
        config_source=AuthConfigSource.SYSTEM_DEFAULT,
        config={},
        composio_toolkit_slug=toolkit_slug,
    )


def test_manager_factory_prefers_composio_when_connector_has_composio_app_name():
    app_trigger = ConnectorTriggerEntity(
        id="google_calendar:event_created",
        connector_id="google_calendar",
        event_type="event_created",
    )
    install = _install("google_calendar", toolkit_slug="googlecalendar")

    manager = ManagersFactory.get_manager(
        app_trigger,
        AuthProvider.COMPOSIO.value,
        install=install,
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
    install = _install("jira")

    manager = ManagersFactory.get_manager(
        app_trigger,
        AuthProvider.LEMMA.value,
        install=install,
    )

    assert manager is None
