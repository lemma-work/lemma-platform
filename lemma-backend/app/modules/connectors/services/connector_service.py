from datetime import datetime
import secrets
from typing import Optional
from uuid import UUID

from app.core.domain.uow import IUnitOfWork
from app.core.domain.errors import DomainError
from app.modules.connectors.domain.account import (
    AccountEntity,
    AccountStatus,
    OAuthCredentials,
)
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
)
from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    AuthScheme,
    AuthProvider,
    ComposioProviderCapability,
    LemmaProviderCapability,
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
    OAuthFlowError,
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
from app.modules.connectors.infrastructure.adapters.lemma_connector_factory import (
    create_lemma_execution_client,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Provider-agnostic profile operations that expose the account holder's email.
# The operation repository resolves each candidate against the account's
# provider, so only the matching one (e.g. composio GMAIL_GET_PROFILE vs native
# get_profile) actually runs. Order is preference, not provider.
_EMAIL_PROFILE_OPERATIONS: dict[str, tuple[str, ...]] = {
    "gmail": ("GMAIL_GET_PROFILE", "get_profile"),
    "outlook": ("OUTLOOK_GET_PROFILE",),
}


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

    def _exception_details(self, exc: Exception) -> dict | None:
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            return details
        if details is not None:
            return {"upstream": details}
        return {"upstream_message": str(exc)}

    async def _load_native_account_profile(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
    ) -> dict | None:
        try:
            connector.capability_for(AuthProvider.LEMMA)
        except ValueError:
            return None

        if connector.id == "slack":
            return await self._load_slack_account_profile(connector, credentials)

        profile_operation_by_app = {
            "gmail": ("users_get_profile", {"user_id": "me"}),
            "google_drive": ("about_get", {}),
        }
        config = profile_operation_by_app.get(connector.id)
        if not config:
            return None

        operation_name, payload = config
        try:
            client = create_lemma_execution_client(
                connector,
                credentials.model_dump(exclude_none=True),
            )
            profile = await client.execute_operation(operation_name, payload)
            profile_dict = self._profile_to_dict(profile)
            if profile_dict is not None:
                return profile_dict
        except Exception as exc:
            logger.warning(
                "Failed to enrich native account profile for %s: %s",
                connector.id,
                exc,
            )
        return None

    async def _load_slack_account_profile(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
    ) -> dict | None:
        if not credentials.access_token:
            return None

        try:
            client = create_lemma_execution_client(
                connector,
                credentials.model_dump(exclude_none=True),
            )
            auth_profile = self._profile_to_dict(
                await client.execute_operation(
                    "auth_test",
                    {"token": credentials.access_token},
                )
            )
            if not auth_profile:
                return None

            profile: dict = {"auth_test": auth_profile, **auth_profile}
            user_id = self._extract_nested_value(auth_profile, "user_id")
            if user_id:
                try:
                    user_info = self._profile_to_dict(
                        await client.execute_operation(
                            "users_info",
                            {"token": credentials.access_token, "user": user_id},
                        )
                    )
                    if user_info:
                        profile["user_info"] = user_info
                except Exception as exc:
                    logger.warning(
                        "Failed to enrich Slack user profile for %s: %s",
                        user_id,
                        exc,
                    )
            return profile
        except Exception as exc:
            logger.warning(
                "Failed to enrich native account profile for %s: %s",
                connector.id,
                exc,
            )
        return None

    def _profile_to_dict(self, profile: object) -> dict | None:
        if isinstance(profile, dict):
            return profile
        if hasattr(profile, "model_dump"):
            data = profile.model_dump(exclude_none=True, exclude_unset=True, mode="json")
            return data if isinstance(data, dict) else None
        return None

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

    async def _fetch_account_email_profile(
        self,
        connector_id: str,
        provider: str,
        credentials: OAuthCredentials,
    ) -> dict | None:
        """Fetch the account holder's profile via the provider's get-profile
        operation, so the email is populated the same way for both Lemma-native
        and Composio accounts (Gmail, Outlook, ...)."""
        if self.operation_gateway is None or self.operation_repository is None:
            return None
        for operation_name in _EMAIL_PROFILE_OPERATIONS.get(connector_id, ()):
            operation = (
                await self.operation_repository.get_by_connector_provider_and_name(
                    connector_id, provider, operation_name
                )
            )
            if operation is None:
                continue
            try:
                result = await self.operation_gateway.execute_operation(
                    connector_id=connector_id,
                    operation_name=operation.execution_name,
                    payload={},
                    third_party_credentials=credentials.model_dump(exclude_none=True),
                    provider=provider,
                )
            except Exception as exc:
                logger.warning(
                    "Profile operation %s failed for %s: %s",
                    operation_name,
                    connector_id,
                    exc,
                )
                continue
            profile = self._profile_to_dict(result)
            if profile:
                return profile
        return None

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
            if provider_config.get("composio_auth_config_id"):
                updates["composio_auth_config_id"] = provider_config[
                    "composio_auth_config_id"
                ]

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
    ) -> LemmaProviderCapability:
        try:
            capability = connector.capability_for(AuthProvider.LEMMA)
        except ValueError as exc:
            raise UnsupportedAuthProviderError(AuthProvider.LEMMA.value) from exc
        if not isinstance(capability, LemmaProviderCapability):
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

    def _default_auth_config_schema(self, auth_scheme: AuthScheme) -> dict:
        if auth_scheme != AuthScheme.OAUTH2:
            return {"type": "object", "properties": {}, "additionalProperties": False}
        return {
            "type": "object",
            "required": ["client_id", "client_secret"],
            "properties": {
                "client_id": {
                    "type": "string",
                    "title": "Client ID",
                },
                "client_secret": {
                    "type": "string",
                    "title": "Client secret",
                    "format": "password",
                },
            },
            "additionalProperties": False,
        }

    def _enrich_connector_defaults(
        self,
        connector: ConnectorEntity,
    ) -> ConnectorEntity:
        capabilities = []
        for capability in connector.provider_capabilities:
            if isinstance(capability, LemmaProviderCapability):
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
                                else self._default_auth_config_schema(
                                    capability.auth_scheme
                                )
                            ),
                        }
                    )
                )
                continue
            if isinstance(capability, ComposioProviderCapability):
                capabilities.append(
                    capability.model_copy(
                        update={
                            "system_default_available": True,
                            "supports_org_custom_auth_config": False,
                            "auth_config_schema": (
                                capability.auth_config_schema
                                if capability.auth_config_schema is not None
                                else (
                                    self._default_auth_config_schema(
                                        capability.auth_scheme
                                    )
                                    if capability.supports_org_custom_auth_config
                                    else None
                                )
                            ),
                        }
                    )
                )
                continue
            capabilities.append(capability)

        return connector.model_copy(update={"provider_capabilities": capabilities})

    def _validate_auth_config_request(
        self,
        *,
        connector: ConnectorEntity,
        provider: AuthProvider,
        config_source: AuthConfigSource,
        provider_config: dict | None,
    ) -> None:
        provider_config = provider_config or {}
        try:
            connector.capability_for(provider)
        except ValueError as exc:
            raise UnsupportedAuthProviderError(provider.value) from exc

        if provider == AuthProvider.COMPOSIO:
            if config_source != AuthConfigSource.SYSTEM_DEFAULT:
                raise ConnectorValidationError(
                    "Composio auth configs only support system default credentials in v1."
                )
            return

        if provider == AuthProvider.LEMMA:
            capability = self._lemma_capability(connector)
            if capability.auth_scheme != AuthScheme.OAUTH2:
                return
            if (
                config_source == AuthConfigSource.ORG_CUSTOM
                and not capability.supports_org_custom_oauth
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
                    "Org custom Lemma OAuth configs require oauth2_credentials."
                )
            OAuth2CredentialConfig.model_validate(credential_config)

    async def create_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        connector_id: str,
        provider: str,
        config_source: str,
        provider_config: dict | None = None,
        name: str | None = None,
    ) -> AuthConfigEntity:
        await self._require_org_member(
            user_id=user_id,
            organization_id=organization_id,
            allowed_roles=["ORG_OWNER", "ORG_EDITOR"],
        )
        connector = await self.get_connector(connector_id)
        provider_enum = AuthProvider(provider)
        config_source_enum = AuthConfigSource(config_source)
        try:
            connector.capability_for(provider_enum)
        except ValueError:
            raise UnsupportedAuthProviderError(provider_enum.value)
        provider_config = provider_config or None
        self._validate_auth_config_request(
            connector=connector,
            provider=provider_enum,
            config_source=config_source_enum,
            provider_config=provider_config,
        )
        existing = await self.auth_config_repository.get_active_by_org_and_app(
            organization_id, connector_id
        )
        if existing:
            raise AccountAlreadyConnectedError(connector_id)

        entity = AuthConfigEntity(
            organization_id=organization_id,
            connector_id=connector_id,
            provider=provider_enum,
            config_source=config_source_enum,
            provider_config=provider_config,
            name=name or connector_id,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        entity = await self.auth_config_repository.create(entity)
        await self.uow.commit()
        return entity

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
                    "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                    "provider": c.provider.value if hasattr(c.provider, "value") else str(c.provider),
                }
                for c in configs
            ],
            "accounts": [
                {
                    "id": str(a.id),
                    "connector_id": a.connector_id,
                    "title": app_titles.get(a.connector_id),
                    "email": a.email,
                    "status": a.status.value if hasattr(a.status, "value") else str(a.status),
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
        auth_provider = self._get_auth_provider_by_name(self._provider_value(auth_config))
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
            logger.error(f"Failed to get authorization URL: {exc}")
            raise OAuthFlowError(
                f"Failed to initiate OAuth flow: {exc}",
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
                preferences=preferences,
                allowed_scopes=allowed_scopes,
            )
        )
        await self.uow.commit()
        return account

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
        auth_provider = self._get_auth_provider_by_name(self._provider_value(auth_config))

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
            logger.error(f"Failed to exchange code for credentials: {exc}")
            pending_request.status = ConnectRequestStatus.ERROR
            await self.connect_request_repository.update(pending_request)
            await self.uow.commit()
            raise OAuthFlowError(
                f"Failed to complete OAuth flow: {exc}",
                details=self._exception_details(exc),
            ) from exc
        provider_account_id = self._extract_provider_account_id(
            connector.id, credentials
        )
        native_profile = await self._load_native_account_profile(effective_connector, credentials)
        if native_profile:
            credentials = credentials.model_copy(
                update={
                    "user_data": {
                        **(credentials.user_data or {}),
                        "profile": native_profile,
                    }
                }
            )
        provider_account_id = provider_account_id or self._extract_provider_account_id_from_profile(
            connector.id, native_profile
        )
        email_profile = await self._fetch_account_email_profile(
            connector.id,
            self._provider_value(auth_config),
            credentials,
        )
        email = self._extract_account_email(
            connector.id, credentials, email_profile
        ) or self._extract_account_email(
            connector.id, credentials, native_profile
        )

        # The user's default account for this auth config; a plain re-auth
        # updates it. Multiple identities may be connected per auth config, so
        # if the default belongs to a different provider identity, look up the
        # matching account (or fall through to creating a new one).
        account = await self.account_repository.get_by_user_and_auth_config(
            user_id, auth_config.id
        )
        if (
            account is not None
            and provider_account_id
            and account.provider_account_id
            and account.provider_account_id != provider_account_id
        ):
            account = await self.account_repository.get_by_user_auth_config_and_provider_account(
                user_id, auth_config.id, provider_account_id
            )

        if account:
            account.credentials = credentials
            if provider_account_id:
                account.provider_account_id = provider_account_id
            if email:
                account.email = email
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
            auth_provider = self._get_auth_provider_by_name(self._provider_value(auth_config))
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
                        raise OAuthFlowError(
                            f"Failed to refresh credentials: {exc}",
                            details=self._exception_details(exc),
                        ) from exc
                    if isinstance(exc, DomainError):
                        raise
                    logger.warning(
                        "Credential refresh failed for account %s; using stored token. %s",
                        account_id,
                        exc,
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
                raise OAuthFlowError(
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
            except Exception as exc:
                logger.warning(f"Failed to revoke connection: {exc}")

        await self.account_repository.delete(account_id)
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
        auth_provider = self._get_auth_provider_by_name(self._provider_value(auth_config))

        for account in accounts:
            if account.credentials and self._should_revoke_account(
                connector=connector,
                auth_config=auth_config,
            ):
                try:
                    await auth_provider.revoke_connection(
                        connector=effective_connector,
                        credentials=self._to_oauth_credentials(account.credentials),
                        user_id=account.user_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to revoke account %s while deleting auth config %s: %s",
                        account.id,
                        auth_config.id,
                        exc,
                    )
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
