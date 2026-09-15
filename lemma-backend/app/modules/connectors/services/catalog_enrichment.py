"""Fill in the parts of a catalog entry that only this deployment knows.

A connector row is shared across every deployment of Lemma; whether *this* one
can sign in to it is not. A native OAuth connector is connectable only where the
operator configured its client, and the endpoints a stored row carries may still
hold an environment placeholder. Both are resolved here, on the way out.

The Composio half is the interesting one, and it is deliberately a
pass-through -- see ``_composio_capability`` below.
"""

from __future__ import annotations

from app.modules.connectors.domain.connector import (
    AuthScheme,
    ComposioProviderCapability,
    ConnectorEntity,
    ConnectorKind,
    KindSpec,
)
from app.modules.connectors.domain.ports import SystemOAuthConfigPort
from app.modules.connectors.services.auth_config_schemas import (
    default_auth_config_schema,
)


def _native_capability(
    capability: KindSpec,
    connector: ConnectorEntity,
    system_oauth_config: SystemOAuthConfigPort,
) -> KindSpec:
    """Any kind Lemma serves itself: http, sql, mcp.

    Narrowing this to the vendored-package spec once left every other native
    kind's ``system_default_available`` permanently false.
    """
    has_system_default = (
        capability.auth_scheme != AuthScheme.OAUTH2
        or system_oauth_config.has_default_oauth_config(connector)
    )
    return capability.model_copy(
        update={
            "system_default_available": has_system_default,
            # Resolved, not read, so the API matches the connect flow: a stored
            # URL may still carry an env placeholder to fill.
            "oauth2_defaults": system_oauth_config.resolve_oauth2_defaults(connector),
            "auth_config_schema": (
                capability.auth_config_schema
                if capability.auth_config_schema is not None
                else default_auth_config_schema(capability.auth_scheme, connector.id)
            ),
        }
    )


def _composio_capability(capability: ComposioProviderCapability) -> KindSpec:
    """A brokered toolkit, exactly as the catalog holds it.

    Unlike a native OAuth connector, whose system default depends on env vars
    this deployment may or may not have, a Composio toolkit's answer depends on
    Composio's own account and nothing here can observe it: ``COMPOSIO_API_KEY``
    says only that we can reach Composio, never which toolkits it holds
    credentials for. This used to overwrite the flag with ``True``, which is how
    eight brokered toolkits came to advertise a sign-in that could only 500.

    ``auth_config_schema`` is left alone for a second reason. For a non-OAuth
    toolkit it is the *account's* credential form, not an org install config, so
    replacing it with ``default_auth_config_schema`` (client_id/client_secret)
    or blanking it to None would empty the connect dialog for every API-key
    toolkit -- freshdesk, metabase, posthog and the rest -- with no error to
    show for it. The org's install form, where there is one, is
    ``install_config_schema``, which the importer derives per toolkit.
    """
    return capability


def enrich_connector_defaults(
    connector: ConnectorEntity,
    system_oauth_config: SystemOAuthConfigPort,
) -> ConnectorEntity:
    """A catalog entry as this deployment can actually offer it."""
    capabilities = []
    for capability in connector.kinds:
        if capability.kind is not ConnectorKind.COMPOSIO:
            capabilities.append(
                _native_capability(capability, connector, system_oauth_config)
            )
        elif isinstance(capability, ComposioProviderCapability):
            capabilities.append(_composio_capability(capability))
        else:
            capabilities.append(capability)
    return connector.model_copy(update={"kinds": capabilities})
