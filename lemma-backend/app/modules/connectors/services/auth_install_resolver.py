"""Resolving one organization's install into what an auth scheme can act on.

Split out of ``ConnectorService`` because it is a pure function of the catalog
entry, the install row and the deployment's OAuth environment -- it reads no
repository and holds no session -- and because the service it came from sits at
its size ceiling.
"""

from __future__ import annotations

from app.modules.connectors.domain.auth_config import AuthConfigEntity, AuthConfigSource
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connector import (
    AuthProvider,
    AuthScheme,
    ComposioProviderCapability,
    ConnectorEntity,
    ConnectorKind,
    KindSpec,
    OAuth2Config,
    OAuth2CredentialConfig,
)
from app.modules.connectors.domain.ports import SystemOAuthConfigPort
from app.modules.connectors.domain.errors import (
    ConnectorValidationError,
    UnsupportedAuthProviderError,
)


def provider_value(auth_config: AuthConfigEntity) -> str:
    """The deprecated provider vocabulary, derived from the install's kind."""
    provider = auth_config.provider
    return provider.value if hasattr(provider, "value") else str(provider)


def lemma_capability(connector: ConnectorEntity) -> KindSpec:
    try:
        capability = connector.capability_for(AuthProvider.LEMMA)
    except ValueError as exc:
        raise UnsupportedAuthProviderError(AuthProvider.LEMMA.value) from exc
    # "LEMMA" means any kind we serve ourselves, which is now sql/mcp/http as
    # well as the vendored package. Asserting the package spec specifically
    # rejected every tenant-configured install.
    if capability.kind is ConnectorKind.COMPOSIO:
        raise UnsupportedAuthProviderError(AuthProvider.LEMMA.value)
    return capability


def composio_capability(connector: ConnectorEntity) -> ComposioProviderCapability:
    try:
        capability = connector.capability_for(AuthProvider.COMPOSIO)
    except ValueError as exc:
        raise UnsupportedAuthProviderError(AuthProvider.COMPOSIO.value) from exc
    if not isinstance(capability, ComposioProviderCapability):
        raise UnsupportedAuthProviderError(AuthProvider.COMPOSIO.value)
    return capability


def _auth_install(
    connector: ConnectorEntity,
    auth_config: AuthConfigEntity,
    *,
    auth_scheme: AuthScheme,
    oauth2: OAuth2Config | None = None,
    composio_toolkit_slug: str | None = None,
) -> ResolvedAuthInstall:
    return ResolvedAuthInstall(
        connector_id=connector.id,
        kind=auth_config.kind,
        auth_scheme=auth_scheme,
        auth_config_id=auth_config.id,
        organization_id=auth_config.organization_id,
        config_source=auth_config.config_source,
        config=auth_config.config or {},
        oauth2=oauth2,
        composio_toolkit_slug=composio_toolkit_slug,
    )


def resolve_auth_install(
    connector: ConnectorEntity,
    auth_config: AuthConfigEntity,
    system_oauth_config: SystemOAuthConfigPort,
) -> ResolvedAuthInstall:
    """State one organization's install once, for the auth scheme to act on.

    This used to return a ``model_copy`` of the *catalog* entry with a
    resolved ``oauth2_config`` or ``composio_toolkit_slug`` grafted on,
    which made per-install state indistinguishable from connector data and
    left the install's own id, config and source with nowhere to go.
    """
    provider = provider_value(auth_config)
    provider_config = auth_config.provider_config or {}
    oauth2_config: OAuth2Config | None = None
    toolkit_slug: str | None = None
    auth_scheme = AuthScheme.OAUTH2

    if provider == AuthProvider.LEMMA.value:
        capability = lemma_capability(connector)
        auth_scheme = capability.auth_scheme
        if capability.auth_scheme != AuthScheme.OAUTH2:
            return _auth_install(connector, auth_config, auth_scheme=auth_scheme)
        if auth_config.config_source == AuthConfigSource.ORG_CUSTOM:
            credential_config = OAuth2CredentialConfig.model_validate(
                provider_config.get("oauth2_credentials") or provider_config
            )
            # OAuth endpoints/scopes are resolved at runtime (stored
            # capability, else the native registry + env) rather than baked
            # into the DB, so org-custom credentials are paired with the
            # provider's canonical endpoints here.
            oauth2_defaults = system_oauth_config.resolve_oauth2_defaults(connector)
            if oauth2_defaults is None:
                raise ConnectorValidationError(
                    f"OAuth2 defaults are not configured for '{connector.id}'."
                )
            oauth2_config = OAuth2Config(
                **oauth2_defaults.model_dump(),
                client_id=credential_config.client_id,
                client_secret=credential_config.client_secret,
            )
        else:
            oauth2_config = system_oauth_config.get_default_oauth_config(connector)
        if oauth2_config is not None and not isinstance(oauth2_config, OAuth2Config):
            oauth2_config = OAuth2Config.model_validate(oauth2_config)

    if provider == AuthProvider.COMPOSIO.value:
        spec = composio_capability(connector)
        auth_scheme = spec.auth_scheme
        toolkit_slug = spec.toolkit_slug

    return _auth_install(
        connector,
        auth_config,
        auth_scheme=auth_scheme,
        oauth2=oauth2_config,
        composio_toolkit_slug=toolkit_slug,
    )
