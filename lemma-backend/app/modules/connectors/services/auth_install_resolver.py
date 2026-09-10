"""Resolving one organization's install into what an auth scheme can act on.

Split out of ``ConnectorService`` because it is a pure function of the catalog
entry, the install row and the deployment's OAuth environment -- it reads no
repository and holds no session -- and because the service it came from sits at
its size ceiling.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from app.core.domain.errors import DomainError
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
from app.modules.connectors.services.auth.mcp_install_authorization import (
    MCP_OAUTH_CONFIG_KEY,
)
from app.modules.connectors.domain.auth_config import (
    COMPOSIO_ORG_CUSTOM_REASON,
    COMPOSIO_SYSTEM_CREDENTIALS_ONLY,
)
from app.modules.connectors.domain.errors import (
    ConnectorValidationError,
    UnsupportedAuthProviderError,
)


def _discovered_mcp_oauth(auth_config: AuthConfigEntity) -> OAuth2Config | None:
    """The client this install registered with its server, if it registered one."""
    if auth_config.kind is not ConnectorKind.MCP:
        return None
    oauth = (auth_config.config or {}).get(MCP_OAUTH_CONFIG_KEY)
    if not isinstance(oauth, dict) or not oauth.get("client_id"):
        return None
    return OAuth2Config(
        client_id=str(oauth["client_id"]),
        # Dynamic registration commonly yields a public client, and `None` is
        # how authlib is told to send no client authentication at all -- which
        # is what `token_endpoint_auth_method: "none"` promised at registration.
        client_secret=(
            str(oauth["client_secret"]) if oauth.get("client_secret") else None
        ),
        authorization_url=str(oauth["authorization_endpoint"]),
        token_url=str(oauth["token_endpoint"]),
        default_scopes=list(oauth.get("scopes") or []),
        resource=oauth.get("resource"),
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

    # An MCP server that described its own authorization at install time is an
    # OAuth install, whatever the catalog's default scheme for the kind says.
    # The catalog cannot know: `mcp` is one entry standing for every server a
    # tenant might point at, and they do not agree on how to authenticate.
    discovered = _discovered_mcp_oauth(auth_config)
    if discovered is not None:
        return _auth_install(
            connector,
            auth_config,
            auth_scheme=AuthScheme.OAUTH2,
            oauth2=discovered,
        )
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


def _org_custom_oauth_is_possible(
    connector: ConnectorEntity,
    system_oauth_config: SystemOAuthConfigPort,
) -> bool:
    """Whether an org's own client id and secret have anywhere to be sent.

    Credentials are only half of an OAuth app; the other half is the
    connector's endpoints, which `resolve_auth_install` pairs them with. A
    connector carrying neither stored `oauth2_defaults` nor a registry entry
    accepts the install and then raises at *sign-in*, leaving a connection that
    can never be connected and a name now taken.
    """
    return system_oauth_config.resolve_oauth2_defaults(connector) is not None


def install_auth_schemes(
    auth_configs: Sequence[AuthConfigEntity],
    *,
    kinds_by_connector: Mapping[str, list[dict[str, object]]],
    system_oauth_config: SystemOAuthConfigPort,
) -> dict[UUID, str]:
    """How each install actually authenticates, keyed by install id.

    The *install's* scheme, not the catalog's, and the two genuinely differ.
    `mcp` is one catalog entry standing for every server a tenant may point at,
    and they do not agree on how to authenticate: the entry says `API_KEY`,
    while an install whose server described its own authorization at create
    time is an OAuth one. A client with only the catalog cannot tell, which is
    how an OAuth-only MCP server came to be "connected" with an empty
    credential set and 401 every call afterwards.

    Takes the kinds already fetched rather than a repository, so the caller can
    read a whole page in one query -- `ConnectorRepository.kinds_for` exists for
    this, as `titles_for` does for the same page's titles.
    """
    schemes: dict[UUID, str] = {}
    for auth_config in auth_configs:
        connector = ConnectorEntity(
            id=auth_config.connector_id,
            kinds=kinds_by_connector.get(auth_config.connector_id) or [],
        )
        try:
            install = resolve_auth_install(connector, auth_config, system_oauth_config)
        except DomainError:
            # An install whose catalog entry no longer offers its kind cannot be
            # connected at all; saying nothing about how is better than failing
            # the list it appears in.
            continue
        schemes[auth_config.id] = install.auth_scheme.value
    return schemes


def validate_auth_config_request(
    *,
    connector: ConnectorEntity,
    kind: ConnectorKind,
    config_source: AuthConfigSource,
    provider_config: Mapping[str, object] | None,
    system_oauth_config: SystemOAuthConfigPort,
) -> None:
    """Whether this organization may install this connector, this way.

    Lives here rather than on the service for the reason stated at the top of
    this module: it is a pure function of the catalog entry, the requested
    kind, and the deployment's OAuth environment.
    """
    supplied: Mapping[str, object] = provider_config or {}
    spec = connector.spec_for(kind)

    if kind is ConnectorKind.COMPOSIO:
        if config_source != AuthConfigSource.SYSTEM_DEFAULT:
            raise ConnectorValidationError(
                COMPOSIO_SYSTEM_CREDENTIALS_ONLY,
                details={"reason": COMPOSIO_ORG_CUSTOM_REASON},
            )
        return

    # Everything below is about who issued the OAuth tokens. A kind that
    # does not use OAuth -- sql, mcp, most http installs -- has nothing to
    # answer here, and its config is checked by its own install schema.
    if spec.auth_scheme != AuthScheme.OAUTH2:
        return
    if (
        config_source == AuthConfigSource.ORG_CUSTOM
        and not spec.supports_org_custom_oauth
    ):
        raise ConnectorValidationError(
            f"Org custom OAuth credentials are not supported for '{connector.id}'."
        )
    if config_source == AuthConfigSource.ORG_CUSTOM and not (
        _org_custom_oauth_is_possible(connector, system_oauth_config)
    ):
        raise ConnectorValidationError(
            f"This deployment has no OAuth endpoints configured for "
            f"'{connector.id}', so an org-supplied OAuth app has nowhere to "
            f"send people. Install it through a kind that does, or ask an "
            f"operator to configure the connector's OAuth endpoints."
        )
    if config_source == AuthConfigSource.SYSTEM_DEFAULT:
        if not system_oauth_config.has_default_oauth_config(connector):
            raise ConnectorValidationError(
                "System default OAuth credentials are not configured for this app. "
                "Create an org custom auth config with OAuth credentials instead."
            )
        return

    credential_config = supplied.get("oauth2_credentials") or supplied
    if not isinstance(credential_config, dict):
        raise ConnectorValidationError(
            "Org custom OAuth configs require oauth2_credentials."
        )
    OAuth2CredentialConfig.model_validate(credential_config)
