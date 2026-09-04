"""Which accounts can carry a remote trigger subscription, and which cannot.

Only Composio brokers subscriptions. A native (lemma) install with no toolkit
gets none, and that is not a failure: the caller binds a routing key instead.
Answering it here rather than at the caller is what keeps a schedule from being
written for a trigger nothing subscribed to.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connector import (
    AuthProvider,
    AuthScheme,
    ConnectorKind,
)
from app.modules.connectors.infrastructure.composio_triggers import (
    supports_provider_subscription,
)


def _install(connector_id: str, *, toolkit_slug: str | None = None):
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


def test_a_composio_toolkit_can_be_subscribed():
    install = _install("google_calendar", toolkit_slug="googlecalendar")

    assert supports_provider_subscription(AuthProvider.COMPOSIO.value, install) is True


def test_a_native_install_with_no_toolkit_cannot():
    install = _install("jira")

    assert supports_provider_subscription(AuthProvider.LEMMA.value, install) is False


def test_a_composio_provider_counts_even_without_a_resolved_install():
    """The install is derived; the auth config's provider is stored."""
    assert supports_provider_subscription(AuthProvider.COMPOSIO.value, None) is True
