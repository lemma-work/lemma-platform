"""One organization's install, resolved for an authentication decision.

Authentication used to be handed a :class:`ConnectorEntity` -- the *catalog*
entry -- with runtime-only fields ``model_copy``-ed onto it: a resolved
``oauth2_config`` for native installs, a ``composio_toolkit_slug`` for brokered
ones. That entity is shared, cached catalog data, so grafting per-install state
onto a copy of it made "which of these fields describe the connector and which
describe *this* org's install" unanswerable at a glance, and left no room at all
for the parts of an install that are neither: its ``config``, its
``config_source``, its id.

This is the install, stated once. It is deliberately separate from
:class:`~app.modules.connectors.domain.kinds.ResolvedInstall`, which serves
execution and discovery: that one must never carry a decrypted OAuth client
secret, and this one has no business carrying an executor's spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.connector import (
    AuthScheme,
    ConnectorKind,
    OAuth2Config,
)


@dataclass(frozen=True, slots=True)
class ResolvedAuthInstall:
    """Everything an auth scheme needs, and nothing an executor does."""

    connector_id: str
    kind: ConnectorKind
    auth_scheme: AuthScheme
    auth_config_id: UUID
    organization_id: UUID
    config_source: AuthConfigSource
    # The install's own decrypted configuration. For a system-default OAuth2
    # install this is empty; for an org-custom one it holds the client the org
    # brought; for a GitHub App it holds the installation it is bound to.
    config: dict[str, Any]
    # Resolved endpoints *and* client credentials, from the org's own config
    # when it has one and the deployment's environment otherwise. None when the
    # scheme does not use OAuth2.
    oauth2: OAuth2Config | None = None
    # Brokered installs only: which Composio toolkit stands behind this
    # connector.
    composio_toolkit_slug: str | None = None
