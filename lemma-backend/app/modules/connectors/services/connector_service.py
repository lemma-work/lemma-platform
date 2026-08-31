from contextlib import suppress
from datetime import datetime
import secrets
from typing import Any, Optional
from uuid import UUID

from app.core.domain.uow import IUnitOfWork
from app.core.domain.errors import DomainError
from app.modules.connectors.services.account_identity import (
    resolve_account_identity,
)
from app.modules.connectors.domain.account import (
    AccountEntity,
    AccountStatus,
    OAuthCredentials,
)
from app.modules.connectors.domain.auth_config import (
    COMPOSIO_ORG_CUSTOM_REASON,
    COMPOSIO_SYSTEM_CREDENTIALS_ONLY,
    AuthConfigEntity,
    AuthConfigSource,
)
from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    AuthScheme,
    AuthProvider,
    ConnectorKind,
    ComposioProviderCapability,
    KindSpec,
    OAuth2Config,
    OAuth2CredentialConfig,
)
from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.domain.errors import (
    AccountAlreadyConnectedError,
    AccountNotFoundError,
    ConnectorNotFoundError,
    ConnectRequestNotFoundError,
    ConnectRequestStateRequiredError,
    ConnectorValidationError,
    CredentialsNotFoundError,
    OAuthWorkflowError,
    UnsupportedAuthProviderError,
)
from app.modules.connectors.domain.ports import (
    AccountRepositoryPort,
    ConnectorOperationRepositoryPort,
    ConnectorRepositoryPort,
    AppOperationGatewayPort,
    AuthProviderPort,
    AuthProviderRegistryPort,
    ConnectRequestRepositoryPort,
    OAuthRedirectUriBuilderPort,
    OrganizationAccessPort,
    SystemOAuthConfigPort,
)
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.services.auth_config_schemas import (
    default_auth_config_schema,
)
from app.modules.connectors.services.account_profile import (
    load_native_account_profile,
    profile_to_dict,
)
from app.modules.connectors.services.install_provisioning import (
    discover_install_operations,
    org_has_install,
    refresh_install_operations,
    resolve_install_kind,
    validate_install_config,
)
from app.modules.connectors.services.install_update import update_install
from app.modules.connectors.services.profile_operation_execution import (
    execute_profile_operation,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ConnectorService:
    """Service for connector application/account OAuth flows."""

    def __init__(
        self,
        *,
        uow: IUnitOfWork,
        connector_repository: ConnectorRepositoryPort,
        auth_config_repository: AuthConfigRepository,
        account_repository: AccountRepositoryPort,
        connect_request_repository: ConnectRequestRepositoryPort,
        auth_provider_registry: AuthProviderRegistryPort,
        redirect_uri_builder: OAuthRedirectUriBuilderPort,
        organization_access: OrganizationAccessPort,
        system_oauth_config: SystemOAuthConfigPort,
        operation_gateway: AppOperationGatewayPort | None = None,
        operation_repository: ConnectorOperationRepositoryPort | None = None,
        auth_config_operation_repository: Any | None = None,
    ):
        self.uow = uow
        self.connector_repository = connector_repository
        self.auth_config_repository = auth_config_repository
        self.account_repository = account_repository
        self.connect_request_repository = connect_request_repository
        self.auth_provider_registry = auth_provider_registry
        self.redirect_uri_builder = redirect_uri_builder
        self.organization_access = organization_access
        self.system_oauth_config = system_oauth_config
        self.operation_gateway = operation_gateway
        self.operation_repository = operation_repository
        self.auth_config_operation_repository = auth_config_operation_repository
        self._kind_dispatcher = None

    def _exception_details(self, exc: Exception) -> dict | None:
        details: dict[str, object] = {"error_type": type(exc).__name__}
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            details["upstream_status"] = status_code
        code = getattr(exc, "code", None)
        if isinstance(code, str) and len(code) <= 100:
            details["upstream_code"] = code
        return details

    async def _load_native_account_profile(
        self, connector: ConnectorEntity, credentials: OAuthCredentials
    ) -> dict | None:
        return await load_native_account_profile(connector, credentials)

    def _profile_to_dict(self, profile: object) -> dict | None:
        return profile_to_dict(profile)

    def _extract_account_email(
        self,
        connector_id: str,
        credentials: OAuthCredentials,
        profile: dict | None,
    ) -> str | None:
        raw_response = credentials.raw_response or {}
        sources = [profile or {}, credentials.user_data or {}, raw_response]
        candidate_paths = [
            "email",
            "emailAddress",
            "email_address",
            "emailaddress",
            "authed_user.email",
            "user.email",
            "user.profile.email",
            "profile.email",
            "user_info.user.profile.email",
            "user_info.user.email",
            # Microsoft Graph (Outlook/Teams): `mail` can be null for some
            # accounts, in which case userPrincipalName carries the address.
            "mail",
            "userPrincipalName",
        ]
        for source in sources:
            for path in candidate_paths:
                value = self._extract_nested_value(source, path)
                if isinstance(value, str) and "@" in value:
                    return value
        if connector_id == "gmail":
            value = self._extract_nested_value(profile or {}, "email_address")
            if isinstance(value, str) and "@" in value:
                return value
        return None

    def _extract_nested_value(self, data: dict | None, path: str) -> str | None:
        if not isinstance(data, dict):
            return None
        current: object = data
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current if isinstance(current, str) else None

    async def _fetch_account_profile(
        self,
        connector: ConnectorEntity,
        provider: str,
        credentials: OAuthCredentials,
    ) -> dict | None:
        """Fetch the account holder's own profile via the catalog-curated
        profile operation(s) for this connector+kind, so identity fields
        (email, name, workspace, ...) are populated the same way for any app
        the catalog has a profile operation for, not just a hardcoded few."""
        if self.operation_gateway is None or self.operation_repository is None:
            return None
        connector_id = connector.id
        try:
            capability = connector.capability_for(provider)
        except ValueError:
            return None
        kind = connector.default_kind_for_provider(provider).value
        for operation_name in capability.profile_operation_names or ():
            operation = await self.operation_repository.get_by_connector_kind_and_name(
                connector_id, kind, operation_name
            )
            if operation is None:
                continue
            try:
                result = await execute_profile_operation(
                    connector_id=connector_id,
                    kind=kind,
                    operation=operation,
                    provider=provider,
                    credentials=credentials,
                    operation_gateway=self.operation_gateway,
                    get_dispatcher=self._profile_dispatcher,
                )
            except Exception:
                logger.debug(
                    "connectors.connector_service.profile_operation_s_s_s.diagnostic",
                    operation_name=operation_name,
                    connector_id=connector_id,
                    exc_info=True,
                )
                continue
            profile = self._profile_to_dict(result)
            # Composio wraps every tool execution result in
            # {"data": ..., "successful": ..., "error": ...} (composio.tools.execute's
            # ToolExecutionResponse); the toolkit's actual fields (email, name, ...)
            # live one level down in `data`, not at the top level.
            if (
                isinstance(profile, dict)
                and provider.upper() == AuthProvider.COMPOSIO.value
            ):
                unwrapped = profile.get("data")
                if isinstance(unwrapped, dict):
                    profile = unwrapped
            if profile:
                return profile
        return None

    def _profile_dispatcher(self):
        if self._kind_dispatcher is None:
            from app.modules.connectors.services.execution.plumbing import (
                build_dispatcher,
            )

            self._kind_dispatcher = build_dispatcher(self.operation_gateway)
        return self._kind_dispatcher

    def _extract_provider_account_id_from_profile(
        self,
        connector_id: str,
        profile: dict | None,
    ) -> str | None:
        if not isinstance(profile, dict):
            return None
        candidate_paths_by_app = {
            "gmail": ("email_address", "emailAddress", "email"),
            "slack": (
                "user_id",
                "auth_test.user_id",
                "user_info.user.id",
                "user",
                "bot_id",
            ),
            "google_drive": ("user.permission_id", "user.email_address"),
            "github": ("login",),
        }
        for path in candidate_paths_by_app.get(connector_id, ()):
            value = self._extract_nested_value(profile, path)
            if value:
                return value
        return None

    def _get_auth_provider_by_name(self, provider_name: str) -> AuthProviderPort:
        provider = self.auth_provider_registry.get(provider_name)
        if not provider:
            raise UnsupportedAuthProviderError(provider_name)
        return provider

    async def _require_org_member(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        allowed_roles: list[str] | None = None,
    ) -> None:
        exists = await self.organization_access.organization_exists(organization_id)
        if not exists:
            raise AccountNotFoundError(str(organization_id))
        has_access = await self.organization_access.user_has_organization_role(
            user_id=user_id,
            organization_id=organization_id,
            allowed_roles=allowed_roles,
        )
        if not has_access:
            raise AccountNotFoundError(str(organization_id))

    def _provider_value(self, auth_config: AuthConfigEntity) -> str:
        provider = auth_config.provider
        return provider.value if hasattr(provider, "value") else str(provider)

    def _build_effective_connector(
        self,
        connector: ConnectorEntity,
        auth_config: AuthConfigEntity,
    ) -> ConnectorEntity:
        provider = self._provider_value(auth_config)
        provider_config = auth_config.provider_config or {}
        updates: dict[str, object] = {}

        if provider == AuthProvider.LEMMA.value:
            capability = self._lemma_capability(connector)
            if capability.auth_scheme != AuthScheme.OAUTH2:
                return connector
            if auth_config.config_source == AuthConfigSource.ORG_CUSTOM:
                credential_config = OAuth2CredentialConfig.model_validate(
                    provider_config.get("oauth2_credentials") or provider_config
                )
                # OAuth endpoints/scopes are resolved at runtime (stored
                # capability, else the native registry + env) rather than baked
                # into the DB, so org-custom credentials are paired with the
                # provider's canonical endpoints here.
                oauth2_defaults = self.system_oauth_config.resolve_oauth2_defaults(
                    connector
                )
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
                oauth2_config = self.system_oauth_config.get_default_oauth_config(
                    connector
                )
            if oauth2_config:
                updates["oauth2_config"] = (
                    oauth2_config
                    if isinstance(oauth2_config, OAuth2Config)
                    else OAuth2Config.model_validate(oauth2_config)
                )

        if provider == AuthProvider.COMPOSIO.value:
            capability = self._composio_capability(connector)
            updates["composio_toolkit_slug"] = capability.toolkit_slug

        return connector.model_copy(update=updates)

    def _should_revoke_account(
        self,
        *,
        connector: ConnectorEntity | None,
        auth_config: AuthConfigEntity,
    ) -> bool:
        if connector is None:
            return False
        provider = self._provider_value(auth_config)
        if provider == AuthProvider.COMPOSIO.value:
            return True
        if provider != AuthProvider.LEMMA.value:
            return False
        return self._lemma_capability(connector).auth_scheme == AuthScheme.OAUTH2

    def _lemma_capability(
        self,
        connector: ConnectorEntity,
    ) -> KindSpec:
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

    def _composio_capability(
        self,
        connector: ConnectorEntity,
    ) -> ComposioProviderCapability:
        try:
            capability = connector.capability_for(AuthProvider.COMPOSIO)
        except ValueError as exc:
            raise UnsupportedAuthProviderError(AuthProvider.COMPOSIO.value) from exc
        if not isinstance(capability, ComposioProviderCapability):
            raise UnsupportedAuthProviderError(AuthProvider.COMPOSIO.value)
        return capability

    def _enrich_connector_defaults(
        self,
        connector: ConnectorEntity,
    ) -> ConnectorEntity:
        capabilities = []
        for capability in connector.kinds:
            # "LEMMA" means any kind we serve ourselves, matching
            # _lemma_capability above -- narrowing to the package spec left
            # every other native kind's system_default_available always false.
            if capability.kind is not ConnectorKind.COMPOSIO:
                has_system_default = (
                    capability.auth_scheme != AuthScheme.OAUTH2
                    or self.system_oauth_config.has_default_oauth_config(connector)
                )
                # Surface the runtime-resolved OAuth defaults (native registry)
                # for apps that don't store their own, so the read API matches
                # what the connect flow will actually use.
                resolved_oauth2_defaults = (
                    capability.oauth2_defaults
                    if capability.oauth2_defaults is not None
                    else self.system_oauth_config.resolve_oauth2_defaults(connector)
                )
                capabilities.append(
                    capability.model_copy(
                        update={
                            "system_default_available": has_system_default,
                            "oauth2_defaults": resolved_oauth2_defaults,
                            "auth_config_schema": (
                                capability.auth_config_schema
                                if capability.auth_config_schema is not None
                                else default_auth_config_schema(
                                    capability.auth_scheme, connector.id
                                )
                            ),
                        }
                    )
                )
                continue
            if isinstance(capability, ComposioProviderCapability):
                # Composio runs on Lemma's own Composio account, so the system
                # default is always there.
                #
                # `auth_config_schema` is deliberately left as the catalog
                # stored it. For a non-OAuth toolkit it is the *account's*
                # credential form, not an org install config, so replacing it
                # with `default_auth_config_schema` (client_id/client_secret)
                # or blanking it to None would empty the connect dialog for
                # every API-key toolkit -- freshdesk, metabase, posthog and the
                # rest -- with no error to show for it.
                capabilities.append(
                    capability.model_copy(update={"system_default_available": True})
                )
                continue
            capabilities.append(capability)

        # `kinds`, not `provider_capabilities`: the latter is a read-only view,
        # so updating it here silently discarded the enrichment.
        return connector.model_copy(update={"kinds": capabilities})

    def _validate_auth_config_request(
        self,
        *,
        connector: ConnectorEntity,
        kind: ConnectorKind,
        config_source: AuthConfigSource,
        provider_config: dict | None,
    ) -> None:
        provider_config = provider_config or {}
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
        if config_source == AuthConfigSource.SYSTEM_DEFAULT:
            if not self.system_oauth_config.has_default_oauth_config(connector):
                raise ConnectorValidationError(
                    "System default OAuth credentials are not configured for this app. "
                    "Create an org custom auth config with OAuth credentials instead."
                )
            return

        credential_config = (
            provider_config.get("oauth2_credentials")
            if isinstance(provider_config, dict)
            else None
        ) or provider_config
        if not isinstance(credential_config, dict):
            raise ConnectorValidationError(
                "Org custom OAuth configs require oauth2_credentials."
            )
        OAuth2CredentialConfig.model_validate(credential_config)

    async def create_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        connector_id: str,
        config_source: str,
        kind: str | None = None,
        config: dict | None = None,
        name: str | None = None,
    ) -> AuthConfigEntity:
        await self._require_org_member(
            user_id=user_id,
            organization_id=organization_id,
            allowed_roles=["ORG_OWNER", "ORG_EDITOR"],
        )
        connector = await self.get_connector(connector_id)
        config_source_enum = AuthConfigSource(config_source)
        kind = resolve_install_kind(connector, kind)
        provider_config = config or None
        self._validate_auth_config_request(
            connector=connector,
            kind=kind,
            config_source=config_source_enum,
            provider_config=provider_config,
        )
        # No single-install check: an org may hold many installs of one
        # connector -- two Slack apps, several MCP servers. They are told apart
        # by name, and the first one becomes the default that a bare
        # connector_id lookup resolves to.
        # Every kind validates its own config here, including the three whose
        # config is entirely tenant-written. The previous validator returned
        # early for exactly those, so `additionalProperties: false` was
        # decorative and a server_url was never checked against anything.
        validated_config = await validate_install_config(
            connector=connector,
            kind=kind,
            config=provider_config or {},
            config_source=config_source_enum,
        )

        entity = AuthConfigEntity(
            organization_id=organization_id,
            connector_id=connector_id,
            kind=kind,
            config_source=config_source_enum,
            # Preserve "no config" as absent rather than an empty object:
            # validation normalizes None to {}, and the API distinguishes them.
            config=validated_config or None,
            name=name or connector_id,
            # First install of a connector answers a bare connector_id lookup.
            is_default=not await org_has_install(
                self.auth_config_repository, organization_id, connector_id
            ),
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        entity = await self.auth_config_repository.create(entity)
        await self.uow.commit()
        await discover_install_operations(
            entity,
            connector,
            repository=self.auth_config_operation_repository,
            uow=self.uow,
        )
        return entity

    async def refresh_auth_config_operations(
        self, *, user_id: UUID, organization_id: UUID, auth_config_name: str
    ) -> int:
        """Re-discover an install's operations. See ``install_provisioning``."""
        return await refresh_install_operations(
            self,
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )

    async def update_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
        name: str | None = None,
        config: dict | None = None,
        status: str | None = None,
        is_default: bool | None = None,
    ) -> tuple[AuthConfigEntity, int, int]:
        """Update an install in place. See ``install_update`` for the rules."""
        return await update_install(
            self,
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
            name=name,
            config=config,
            status=status,
            is_default=is_default,
        )

    async def list_auth_configs(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[list[AuthConfigEntity], UUID | None]:
        await self._require_org_member(user_id=user_id, organization_id=organization_id)
        configs, next_cursor = await self.auth_config_repository.list_by_org(
            organization_id,
            limit=limit,
            cursor=cursor,
        )
        return list(configs), next_cursor

    async def _resolve_auth_config(
        self,
        *,
        organization_id: UUID,
        connector_id: str | None = None,
        auth_config_id: UUID | None = None,
        auth_config_name: str | None = None,
    ) -> AuthConfigEntity:
        auth_config = None
        if auth_config_id is not None:
            auth_config = await self.auth_config_repository.get(auth_config_id)
            if auth_config and auth_config.organization_id != organization_id:
                auth_config = None
        elif auth_config_name is not None:
            auth_config = await self.auth_config_repository.get_active_by_org_and_name(
                organization_id, auth_config_name
            )
        elif connector_id is not None:
            auth_config = await self.auth_config_repository.get_active_by_org_and_app(
                organization_id, connector_id
            )
        if not auth_config:
            raise ConnectorNotFoundError(
                connector_id or auth_config_name or str(auth_config_id)
            )
        return auth_config

    async def get_auth_config_by_name(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
    ) -> AuthConfigEntity:
        await self._require_org_member(user_id=user_id, organization_id=organization_id)
        return await self._resolve_auth_config(
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )

    async def list_connectors(
        self, limit: int = 100, cursor: Optional[str] = None
    ) -> tuple[list[ConnectorEntity], Optional[str]]:
        connectors, next_cursor = await self.connector_repository.list_active(
            limit, cursor
        )
        return [self._enrich_connector_defaults(app) for app in connectors], next_cursor

    async def get_connector(self, connector_id: str) -> ConnectorEntity:
        connector = await self.connector_repository.get(connector_id)
        if not connector:
            raise ConnectorNotFoundError(connector_id)
        return self._enrich_connector_defaults(connector)

    async def get_connector_status(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
    ) -> dict:
        await self._require_org_member(user_id=user_id, organization_id=organization_id)

        configs, _ = await self.auth_config_repository.list_by_org(
            organization_id, limit=200
        )
        accounts, _ = await self.account_repository.list_by_user_and_org(
            user_id, organization_id, limit=200
        )

        app_ids = {c.connector_id for c in configs} | {a.connector_id for a in accounts}
        app_titles: dict[str, str | None] = {}
        for app_id in app_ids:
            app = await self.connector_repository.get(app_id)
            app_titles[app_id] = app.title if app else None

        return {
            "installed": [
                {
                    "name": c.name,
                    "connector_id": c.connector_id,
                    "title": app_titles.get(c.connector_id),
                    "status": c.status.value
                    if hasattr(c.status, "value")
                    else str(c.status),
                    "kind": c.kind.value if hasattr(c.kind, "value") else str(c.kind),
                }
                for c in configs
            ],
            "accounts": [
                {
                    "id": str(a.id),
                    "connector_id": a.connector_id,
                    "title": app_titles.get(a.connector_id),
                    "email": a.email,
                    "status": a.status.value
                    if hasattr(a.status, "value")
                    else str(a.status),
                }
                for a in accounts
            ],
        }

    async def initiate_connect_request(
        self,
        user_id: UUID,
        organization_id: UUID,
        connector_id: str | None = None,
        auth_config_id: UUID | None = None,
    ) -> ConnectRequestEntity:
        await self._require_org_member(user_id=user_id, organization_id=organization_id)
        auth_config = await self._resolve_auth_config(
            organization_id=organization_id,
            connector_id=connector_id,
            auth_config_id=auth_config_id,
        )
        connector = await self.get_connector(auth_config.connector_id)
        provider_value = self._provider_value(auth_config)
        if provider_value == AuthProvider.LEMMA.value:
            capability = self._lemma_capability(connector)
            if capability.auth_scheme != AuthScheme.OAUTH2:
                raise ConnectorValidationError(
                    "Credential-managed native accounts must be connected with the accounts API."
                )
        elif provider_value == AuthProvider.COMPOSIO.value:
            if self._composio_capability(connector).auth_scheme != AuthScheme.OAUTH2:
                raise ConnectorValidationError(
                    "Credential-managed Composio accounts must be connected with the accounts API."
                )

        # Multiple accounts are allowed per auth config, so a new connect request
        # is always permitted. The OAuth callback dedups by provider account id:
        # re-authing an existing identity updates it, a new identity is created.

        effective_connector = self._build_effective_connector(connector, auth_config)
        auth_provider = self._get_auth_provider_by_name(
            self._provider_value(auth_config)
        )
        state = secrets.token_urlsafe(32)
        redirect_uri = self.redirect_uri_builder.build()

        try:
            (
                authorization_url,
                provider_state,
            ) = await auth_provider.get_authorization_url(
                connector=effective_connector,
                user_id=user_id,
                state=state,
                redirect_uri=redirect_uri,
            )
        except DomainError:
            raise
        except Exception as exc:
            logger.debug(
                "connectors.connector_service.get_connector_authorization_url.propagated",
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise OAuthWorkflowError(
                "Unable to initiate the OAuth flow.",
                details=self._exception_details(exc),
            ) from exc

        connect_request = ConnectRequestEntity(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_id=auth_config.id,
            connector_id=connector.id,
            authorization_url=authorization_url,
            status=ConnectRequestStatus.PENDING,
            attributes={"state": state, "provider_state": provider_state},
        )
        connect_request = await self.connect_request_repository.create(connect_request)
        await self.uow.commit()

        return connect_request

    async def create_account(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_id: UUID | None = None,
        auth_config_name: str | None = None,
        credentials: dict | None = None,
        provider_account_id: str | None = None,
        email: str | None = None,
        preferences: dict | None = None,
        allowed_scopes: list[str] | None = None,
    ) -> AccountEntity:
        await self._require_org_member(
            user_id=user_id,
            organization_id=organization_id,
        )
        if auth_config_id is None and auth_config_name is None:
            raise ConnectorValidationError(
                "Either auth_config_id or auth_config_name is required."
            )

        auth_config = await self._resolve_auth_config(
            organization_id=organization_id,
            auth_config_id=auth_config_id,
            auth_config_name=auth_config_name,
        )
        connector = await self.get_connector(auth_config.connector_id)
        provider = AuthProvider(self._provider_value(auth_config))
        if provider == AuthProvider.LEMMA:
            auth_scheme = self._lemma_capability(connector).auth_scheme
        elif provider == AuthProvider.COMPOSIO:
            auth_scheme = self._composio_capability(connector).auth_scheme
        else:
            raise UnsupportedAuthProviderError(provider.value)

        if auth_scheme == AuthScheme.OAUTH2:
            raise ConnectorValidationError(
                "OAuth2 accounts must be connected with an OAuth connect request."
            )

        if not isinstance(credentials, dict) or not credentials:
            raise ConnectorValidationError(
                "Credential-managed accounts require a non-empty credentials object."
            )

        existing_account = await self.account_repository.get_by_user_and_auth_config(
            user_id, auth_config.id
        )
        # Multiple credential-managed accounts are allowed (e.g. several bot
        # tokens for different agents); the first connected becomes the default.
        is_default = existing_account is None

        # Composio credential-managed apps must establish a connected account on
        # Composio's side; native (Lemma) apps store the credentials verbatim.
        effective_connector = self._build_effective_connector(connector, auth_config)
        auth_provider = self._get_auth_provider_by_name(provider.value)
        stored_credentials = await auth_provider.connect_with_credentials(
            connector=effective_connector,
            user_id=user_id,
            credentials=credentials,
        )

        # Best-effort: fetch the account holder's own profile via the
        # catalog-curated profile operation for this connector+provider (if
        # any), so credential-managed accounts (e.g. a Notion integration
        # token) get the same identity enrichment OAuth accounts do. A
        # missing/failing operation just leaves profile=None; identity
        # resolution falls back to whatever the credentials carry.
        profile = await self._fetch_account_profile(
            connector, provider.value, stored_credentials
        )

        # Derive a stable identity + human-friendly label from the connected
        # credentials so the account is distinguishable and duplicates can be
        # rejected. A client-supplied provider_account_id/email takes precedence.
        identity = await resolve_account_identity(
            connector_id=connector.id, credentials=stored_credentials, profile=profile
        )
        provider_account_id = provider_account_id or identity.provider_account_id
        email = email or identity.email
        display_name = identity.display_name
        await self._reject_if_identity_already_connected(
            user_id=user_id,
            auth_config_id=auth_config.id,
            provider_account_id=provider_account_id,
            connector_id=connector.id,
        )

        account = await self.account_repository.create(
            AccountEntity(
                user_id=user_id,
                organization_id=organization_id,
                auth_config_id=auth_config.id,
                connector_id=connector.id,
                is_default=is_default,
                credentials=stored_credentials,
                provider_account_id=provider_account_id,
                email=email,
                display_name=display_name,
                preferences=preferences,
                allowed_scopes=allowed_scopes,
            )
        )
        await self.uow.commit()
        return account

    async def _reject_if_identity_already_connected(
        self,
        *,
        user_id: UUID,
        auth_config_id: UUID,
        provider_account_id: str | None,
        connector_id: str,
        exclude_account_id: UUID | None = None,
    ) -> None:
        """Reject connecting the same provider identity twice.

        A healthy (CONNECTED) account for this ``provider_account_id`` blocks a
        new connect; an unhealthy one (REAUTH_REQUIRED/DISCONNECTED) is left for
        the caller to update in place (a genuine reconnect). No-op when the
        identity couldn't be derived (``provider_account_id`` is None)."""
        if not provider_account_id:
            return
        existing = (
            await self.account_repository.get_by_user_auth_config_and_provider_account(
                user_id, auth_config_id, provider_account_id
            )
        )
        if existing is None:
            return
        if exclude_account_id is not None and existing.id == exclude_account_id:
            return
        if existing.status == AccountStatus.CONNECTED:
            raise AccountAlreadyConnectedError(connector_id)

    async def handle_oauth_callback(
        self,
        redirect_uri: str,
        state: Optional[str] = None,
    ) -> AccountEntity:
        if not state:
            raise ConnectRequestStateRequiredError()

        pending_request = await self.connect_request_repository.get_by_state(state)
        if not pending_request:
            raise ConnectRequestNotFoundError()

        user_id = pending_request.user_id
        auth_config = await self._resolve_auth_config(
            organization_id=pending_request.organization_id,
            auth_config_id=pending_request.auth_config_id,
        )
        connector = await self.get_connector(pending_request.connector_id)
        effective_connector = self._build_effective_connector(connector, auth_config)
        auth_provider = self._get_auth_provider_by_name(
            self._provider_value(auth_config)
        )

        try:
            credentials = await auth_provider.exchange_code_for_credentials(
                connector=effective_connector,
                redirect_uri=redirect_uri,
                user_id=user_id,
                state=None,
            )
        except DomainError:
            raise
        except Exception as exc:
            logger.debug(
                "connectors.connector_service.exchange_connector_authorization_code.propagated",
                error_type=type(exc).__name__,
                exc_info=True,
            )
            pending_request.status = ConnectRequestStatus.ERROR
            await self.connect_request_repository.update(pending_request)
            await self.uow.commit()
            raise OAuthWorkflowError(
                "Unable to complete the OAuth flow.",
                details=self._exception_details(exc),
            ) from exc
        provider_account_id = self._extract_provider_account_id(
            connector.id, credentials
        )
        native_profile = await self._load_native_account_profile(
            effective_connector, credentials
        )
        if native_profile:
            credentials = credentials.model_copy(
                update={
                    "user_data": {
                        **(credentials.user_data or {}),
                        "profile": native_profile,
                    }
                }
            )
        provider_account_id = (
            provider_account_id
            or self._extract_provider_account_id_from_profile(
                connector.id, native_profile
            )
        )
        email_profile = await self._fetch_account_profile(
            connector,
            self._provider_value(auth_config),
            credentials,
        )
        email = self._extract_account_email(
            connector.id, credentials, email_profile
        ) or self._extract_account_email(connector.id, credentials, native_profile)

        # Human-friendly label for the account list (team name / mailbox / …),
        # falling back to the email when the app has no better label. Prefer
        # the catalog-driven profile (works for any app with a profile
        # operation configured) over the Lemma-native one (Gmail/Drive/Slack
        # only) since only one of the two is ever populated for a given app.
        display_name = (
            await resolve_account_identity(
                connector_id=connector.id,
                credentials=credentials,
                profile=email_profile or native_profile,
            )
        ).display_name or email

        # Match the re-authing identity to an existing account:
        #  - provider account id known → the account with exactly that id. A new
        #    identity has none yet, so `account` stays None and the create branch
        #    below runs — we must NOT fall back to the default here, or a second
        #    identity's re-auth would clobber the default account's credentials
        #    (which may itself have a null provider_account_id).
        #  - provider account id absent → the user's default for this auth config
        #    (a plain re-auth of the existing/only account; can't disambiguate
        #    identities without an id).
        if provider_account_id:
            account = await self.account_repository.get_by_user_auth_config_and_provider_account(
                user_id, auth_config.id, provider_account_id
            )
            # A healthy account for this identity blocks a duplicate connect; an
            # unhealthy one is updated in place below (a genuine reconnect).
            if account is not None and account.status == AccountStatus.CONNECTED:
                raise AccountAlreadyConnectedError(connector.id)
        else:
            account = await self.account_repository.get_by_user_and_auth_config(
                user_id, auth_config.id
            )

        if account:
            account.credentials = credentials
            if provider_account_id:
                account.provider_account_id = provider_account_id
            if email:
                account.email = email
            if display_name:
                account.display_name = display_name
            # A successful (re-)auth restores the account to a usable state.
            account.status = AccountStatus.CONNECTED
            account = await self.account_repository.update(account)
        else:
            # A new identity is the default only if the user has no account yet.
            has_existing = await self.account_repository.get_by_user_and_auth_config(
                user_id, auth_config.id
            )
            account = await self.account_repository.create(
                AccountEntity(
                    user_id=user_id,
                    organization_id=pending_request.organization_id,
                    auth_config_id=auth_config.id,
                    connector_id=connector.id,
                    is_default=has_existing is None,
                    credentials=credentials,
                    provider_account_id=provider_account_id,
                    email=email,
                    display_name=display_name,
                )
            )

        pending_request.status = ConnectRequestStatus.SUCCESS
        await self.connect_request_repository.update(pending_request)
        await self.uow.commit()

        return account

    async def list_accounts(
        self,
        user_id: UUID,
        organization_id: UUID,
        connector_id: Optional[str] = None,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[list[AccountEntity], UUID | None]:
        await self._require_org_member(user_id=user_id, organization_id=organization_id)
        accounts, next_cursor = await self.account_repository.list_by_user_and_org(
            user_id,
            organization_id,
            connector_id=connector_id,
            limit=limit,
            cursor=cursor,
        )
        return list(accounts), next_cursor

    async def get_account(
        self,
        account_id: UUID,
        user_id: UUID,
        organization_id: UUID | None = None,
    ) -> AccountEntity:
        if organization_id is not None:
            await self._require_org_member(
                user_id=user_id,
                organization_id=organization_id,
            )
        account = await self.account_repository.get(account_id)
        if not account or account.user_id != user_id:
            raise AccountNotFoundError(str(account_id))
        if organization_id is not None and account.organization_id != organization_id:
            raise AccountNotFoundError(str(account_id))
        return account

    async def get_account_kind(self, account: AccountEntity) -> str | None:
        """The kind of the install backing an account -- exposed on the account
        response so API consumers (e.g. the CLI assembling a portable pod
        bundle) can resolve connector + kind from one account lookup instead of
        a second one keyed by auth config name."""
        auth_config = await self.auth_config_repository.get(account.auth_config_id)
        return auth_config.kind.value if auth_config is not None else None

    async def get_account_credentials(
        self,
        account_id: UUID,
        user_id: UUID,
        organization_id: UUID | None = None,
        force_refresh: bool = False,
    ) -> OAuthCredentials:
        account = await self.get_account(account_id, user_id, organization_id)
        resolved_organization_id = organization_id or account.organization_id
        credentials = account.credentials
        if not credentials:
            raise CredentialsNotFoundError(str(account_id))

        connector = await self.connector_repository.get(account.connector_id)
        if not connector:
            raise ConnectorNotFoundError(account.connector_id)
        auth_config = await self._resolve_auth_config(
            organization_id=resolved_organization_id,
            auth_config_id=account.auth_config_id,
        )
        effective_connector = self._build_effective_connector(connector, auth_config)

        oauth_credentials = self._to_oauth_credentials(credentials)
        expires_at = (
            oauth_credentials.expires_at
            if hasattr(oauth_credentials, "expires_at")
            else None
        )
        if expires_at is not None:
            now = (
                datetime.now(tz=expires_at.tzinfo)
                if expires_at.tzinfo is not None
                else datetime.now()
            )
            is_expired = expires_at < now
        else:
            is_expired = False
        should_refresh = force_refresh or is_expired

        if should_refresh:
            auth_provider = self._get_auth_provider_by_name(
                self._provider_value(auth_config)
            )
            can_refresh = bool(
                oauth_credentials.refresh_token or oauth_credentials.connection_id
            )

            if can_refresh:
                try:
                    new_credentials = await auth_provider.refresh_credentials(
                        connector=effective_connector,
                        credentials=oauth_credentials,
                        user_id=account.user_id,
                    )
                except Exception as exc:
                    # An expired token we cannot refresh means the account is
                    # unusable until the user reconnects.
                    if is_expired:
                        await self._persist_account_status(
                            account, AccountStatus.REAUTH_REQUIRED
                        )
                        if isinstance(exc, DomainError):
                            raise
                        raise OAuthWorkflowError(
                            "Unable to refresh connector credentials.",
                            details=self._exception_details(exc),
                        ) from exc
                    if isinstance(exc, DomainError):
                        raise
                    logger.debug(
                        "connectors.connector_service.credential_refresh_using_unexpired_stored.diagnostic",
                        account_id=str(account_id),
                        error_type=type(exc).__name__,
                    )
                else:
                    account.credentials = new_credentials
                    # A successful refresh restores a previously-degraded account.
                    account.status = AccountStatus.CONNECTED
                    account = await self.account_repository.update(account)
                    await self.uow.commit()
                    credentials = account.credentials
            elif is_expired:
                await self._persist_account_status(
                    account, AccountStatus.REAUTH_REQUIRED
                )
                raise OAuthWorkflowError(
                    "Credentials are expired and cannot be refreshed for this account."
                )

        return self._to_oauth_credentials(credentials)

    async def _persist_account_status(
        self, account: AccountEntity, status: AccountStatus
    ) -> AccountEntity:
        """Persist an account status transition (idempotent)."""
        if account.status == status:
            return account
        account.status = status
        account = await self.account_repository.update(account)
        await self.uow.commit()
        return account

    async def mark_account_reauth_required(
        self,
        account_id: UUID,
        user_id: UUID,
        organization_id: UUID | None = None,
    ) -> None:
        """Flag an account as needing re-authentication.

        Best-effort: a missing/inaccessible account is silently ignored so this
        never masks the original auth failure that triggered it.
        """
        try:
            account = await self.get_account(account_id, user_id, organization_id)
        except DomainError:
            return
        await self._persist_account_status(account, AccountStatus.REAUTH_REQUIRED)

    async def delete_account(
        self,
        account_id: UUID,
        user_id: UUID,
        organization_id: UUID | None = None,
    ) -> None:
        account = await self.get_account(account_id, user_id, organization_id)
        connector = await self.connector_repository.get(account.connector_id)
        resolved_organization_id = organization_id or account.organization_id
        auth_config = await self._resolve_auth_config(
            organization_id=resolved_organization_id,
            auth_config_id=account.auth_config_id,
        )
        effective_connector = (
            self._build_effective_connector(connector, auth_config)
            if connector
            else None
        )

        if account.credentials and self._should_revoke_account(
            connector=connector,
            auth_config=auth_config,
        ):
            try:
                auth_provider = self._get_auth_provider_by_name(
                    self._provider_value(auth_config)
                )
                await auth_provider.revoke_connection(
                    connector=effective_connector,
                    credentials=self._to_oauth_credentials(account.credentials),
                    user_id=user_id,
                )
            except Exception:
                logger.error(
                    "connectors.connector_service.revoke.failed", exc_info=True
                )

        await self.account_repository.delete(account_id)
        # Keep the "exactly one default per (user, auth_config)" invariant: if the
        # deleted account was the default, promote the oldest remaining one so
        # implicit account resolution never silently loses its default.
        if account.is_default and account.auth_config_id is not None:
            await self.account_repository.promote_next_default(
                user_id=account.user_id,
                auth_config_id=account.auth_config_id,
                exclude_account_id=account_id,
            )
        await self.uow.commit()

    async def delete_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_id: UUID | None = None,
        auth_config_name: str | None = None,
    ) -> None:
        await self._require_org_member(
            user_id=user_id,
            organization_id=organization_id,
            allowed_roles=["ORG_OWNER", "ORG_EDITOR"],
        )
        auth_config = await self._resolve_auth_config(
            organization_id=organization_id,
            auth_config_id=auth_config_id,
            auth_config_name=auth_config_name,
        )
        accounts = await self.account_repository.list_by_auth_config(auth_config.id)
        connector = await self.connector_repository.get(auth_config.connector_id)
        effective_connector = (
            self._build_effective_connector(connector, auth_config)
            if connector
            else None
        )
        auth_provider = self._get_auth_provider_by_name(
            self._provider_value(auth_config)
        )

        # Revoke first, with no connection held, then delete. Interleaving the
        # two meant a pooled connection stayed checked out across one provider
        # round trip per account, unbounded in the number of accounts — and
        # this runs under a request-scoped session, so it is a request holding
        # a connection for as long as the slowest provider takes, times N.
        revocable = [
            account
            for account in accounts
            if account.credentials
            and self._should_revoke_account(
                connector=connector,
                auth_config=auth_config,
            )
        ]
        credentials_to_revoke = [
            (account.user_id, self._to_oauth_credentials(account.credentials))
            for account in revocable
        ]
        await self.uow.commit()

        for user_id, credentials in credentials_to_revoke:
            # Best-effort, as before: a provider that will not revoke must not
            # strand the account rows in Lemma.
            with suppress(Exception):
                await auth_provider.revoke_connection(
                    connector=effective_connector,
                    credentials=credentials,
                    user_id=user_id,
                )

        for account in accounts:
            await self.account_repository.delete(account.id)

        await self.auth_config_repository.delete(auth_config.id)
        await self.uow.commit()

    def _to_oauth_credentials(self, credentials: object) -> OAuthCredentials:
        if isinstance(credentials, OAuthCredentials):
            return credentials
        # Coerce any other pydantic credential model (e.g. ComposioCredentials)
        # to a dict so its fields — crucially connection_id — are preserved.
        if not isinstance(credentials, dict):
            model_dump = getattr(credentials, "model_dump", None)
            credentials = model_dump() if callable(model_dump) else {}
        if isinstance(credentials, dict):
            return OAuthCredentials(
                access_token=credentials.get("access_token", ""),
                refresh_token=credentials.get("refresh_token"),
                token_type=credentials.get("token_type", "Bearer"),
                expires_at=credentials.get("expires_at"),
                raw_response=credentials.get("raw_response"),
                connection_id=credentials.get("connection_id"),
                user_data=credentials.get("user_data"),
            )
        return OAuthCredentials(access_token="")

    def _extract_provider_account_id(
        self, connector_id: str, credentials: object
    ) -> str | None:
        oauth_credentials = self._to_oauth_credentials(credentials)
        raw_response = (
            oauth_credentials.raw_response
            if isinstance(oauth_credentials.raw_response, dict)
            else {}
        )
        user_data = (
            oauth_credentials.user_data
            if isinstance(oauth_credentials.user_data, dict)
            else {}
        )

        def _nested(data: dict, *path: str) -> str | None:
            cur: object = data
            for key in path:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(key)
            if cur is None:
                return None
            value = str(cur).strip()
            return value or None

        app = (connector_id or "").lower()
        if app == "slack":
            # Slack user events use authed_user.id as external user identity.
            return (
                _nested(raw_response, "authed_user", "id")
                or _nested(raw_response, "user", "id")
                or _nested(raw_response, "user_id")
            )

        # Generic fallbacks across providers.
        return (
            _nested(raw_response, "provider_account_id")
            or _nested(raw_response, "user", "id")
            or _nested(raw_response, "user_id")
            or _nested(raw_response, "id")
            or _nested(user_data, "provider_account_id")
            or _nested(user_data, "user", "id")
            or _nested(user_data, "user_id")
            or _nested(user_data, "id")
            or _nested(user_data, "sub")
            or _nested(user_data, "oid")
        )
