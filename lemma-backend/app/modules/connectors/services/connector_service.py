import secrets
from datetime import datetime
from collections.abc import Sequence
from typing import Any, Optional
from uuid import UUID

from app.core.domain.errors import DomainError
from app.core.domain.uow import IUnitOfWork
from app.core.log.log import get_logger
from app.modules.connectors.domain.account import (
    AccountEntity,
    AccountStatus,
    OAuthCredentials,
)
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
    reject_if_disabled,
)
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.domain.connector import (
    AuthProvider,
    AuthScheme,
    ComposioProviderCapability,
    ConnectorEntity,
    ConnectorKind,
    KindSpec,
)
from app.modules.connectors.domain.errors import (
    AccountAlreadyConnectedError,
    AccountNotFoundError,
    ConnectorNotFoundError,
    ConnectorValidationError,
    CredentialsNotFoundError,
    OAuthWorkflowError,
    OrganizationConnectorsNotFoundError,
    UnsupportedAuthProviderError,
)
from app.modules.connectors.domain.install_binding import resolve_external_ref
from app.modules.connectors.domain.ports import (
    AccountRepositoryPort,
    AppOperationGatewayPort,
    AuthProviderPort,
    AuthProviderRegistryPort,
    ConnectorOperationRepositoryPort,
    ConnectorRepositoryPort,
    ConnectRequestRepositoryPort,
    OAuthRedirectUriBuilderPort,
    OrganizationAccessPort,
    SystemOAuthConfigPort,
)
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.services.account_credentials import (
    validated_account_credentials,
)
from app.modules.connectors.services.account_identity import (
    resolve_account_identity,
)
from app.modules.connectors.services.account_profile import (
    load_native_account_profile,
    profile_to_dict,
)
from app.modules.connectors.services.account_revocation import revoke_one
from app.modules.connectors.services.auth.mcp_install_authorization import (
    negotiate_mcp_authorization,
)
from app.modules.connectors.services.auth_config_schemas import (
    default_auth_config_schema,
)
from app.modules.connectors.services.auth_install_resolver import (
    install_auth_schemes,
    validate_auth_config_request,
    composio_capability,
    lemma_capability,
    provider_value,
    resolve_auth_install,
)
from app.modules.connectors.services.connect_request_lifecycle import (
    pkce_verifier_for,
)
from app.modules.connectors.services.install_provisioning import (
    DiscoveryOutcome,
    discover_install_operations,
    org_has_install,
    refresh_install_operations,
    resolve_install_kind,
    validate_install_config,
)
from app.modules.connectors.services.install_update import update_install
from app.modules.connectors.services.oauth_callback import handle_oauth_callback
from app.modules.connectors.services.profile_operation_execution import (
    execute_profile_operation,
)

logger = get_logger(__name__)

# Who may install a connector, and -- since an install config is entirely
# tenant-written for the mcp/http/sql kinds -- who may read one back.
INSTALL_MANAGER_ROLES = ["ORG_OWNER", "ORG_EDITOR"]


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

    async def _fetch_account_profile(
        self,
        connector: ConnectorEntity,
        provider: str,
        credentials: OAuthCredentials,
    ) -> dict | None:
        """Fetch the account holder's own profile via the catalog-curated
        profile operation(s) for this connector+kind, so identity fields
        (email, name, workspace, ...) are populated the same way for any app
        the catalog has a profile operation for, not just a hardcoded few.

        Every operation row is read first and the connection handed back before
        the first of them runs. Interleaving the two put a provider round trip
        between two repository reads, which is a pooled connection held across
        external I/O however short the reads are."""
        if self.operation_gateway is None or self.operation_repository is None:
            return None
        connector_id = connector.id
        try:
            capability = connector.capability_for(provider)
        except ValueError:
            return None
        kind = connector.default_kind_for_provider(provider).value
        operation_names = list(capability.profile_operation_names or ())
        operations = [
            (
                name,
                await self.operation_repository.get_by_connector_kind_and_name(
                    connector_id, kind, name
                ),
            )
            for name in operation_names
        ]
        runnable = [(name, op) for name, op in operations if op is not None]
        if not runnable:
            return None
        await self.uow.commit()

        for operation_name, operation in runnable:
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
                # Warning, not debug: production runs at INFO, and the account
                # this was meant to label is about to be stored with no email,
                # no display name and -- for the kinds whose identity comes
                # only from here -- no provider account id at all. Once per
                # connect, so the volume is a person's clicks.
                logger.warning(
                    "connectors.connector_service.account_profile_operation.degraded",
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
            raise OrganizationConnectorsNotFoundError(str(organization_id))
        has_access = await self.organization_access.user_has_organization_role(
            user_id=user_id,
            organization_id=organization_id,
            allowed_roles=allowed_roles,
        )
        if not has_access:
            raise OrganizationConnectorsNotFoundError(str(organization_id))

    async def may_read_install_config(
        self, *, user_id: UUID, organization_id: UUID
    ) -> bool:
        """Whether this member may see an install's configuration, not just
        that it exists.

        Creating an install needs owner or editor; reading one needed only
        membership, and the config is where the tenant put its MCP server URL,
        its database host and its request headers. Masking secrets by key name
        cannot close that on its own, because the tenant chooses the key names
        in `extra_headers`/`default_headers` -- so the read is levelled with
        the write and the mask is a second line, not the only one.
        """
        return await self.organization_access.user_has_organization_role(
            user_id=user_id,
            organization_id=organization_id,
            allowed_roles=INSTALL_MANAGER_ROLES,
        )

    def _provider_value(self, auth_config: AuthConfigEntity) -> str:
        return provider_value(auth_config)

    def _resolve_auth_install(
        self,
        connector: ConnectorEntity,
        auth_config: AuthConfigEntity,
    ) -> ResolvedAuthInstall:
        return resolve_auth_install(connector, auth_config, self.system_oauth_config)

    def _lemma_capability(self, connector: ConnectorEntity) -> KindSpec:
        return lemma_capability(connector)

    def _composio_capability(
        self, connector: ConnectorEntity
    ) -> ComposioProviderCapability:
        return composio_capability(connector)

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
                # Resolved, not read, so the API matches the connect flow: a
                # stored URL may still carry an env placeholder to fill.
                resolved_oauth2_defaults = (
                    self.system_oauth_config.resolve_oauth2_defaults(connector)
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
        validate_auth_config_request(
            connector=connector,
            kind=kind,
            config_source=config_source,
            provider_config=provider_config,
            system_oauth_config=self.system_oauth_config,
        )

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
            allowed_roles=INSTALL_MANAGER_ROLES,
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
        # connector_id lookup resolves to. Answered here rather than inline in
        # the entity below so the last database read happens before the network
        # work, not after it.
        is_first_install = not await org_has_install(
            self.auth_config_repository, organization_id, connector_id
        )
        # Nothing is written yet, so this only hands the pooled connection back
        # -- which is the point. Validation resolves DNS inside the SSRF guard
        # and MCP negotiation is up to three 15s round trips to a server the
        # tenant names, and neither may be waited on holding one. Same shape as
        # `delete_auth_config` below.
        await self.uow.commit()

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

        validated_config = await negotiate_mcp_authorization(
            kind=kind,
            config=validated_config,
            redirect_uri=self.redirect_uri_builder.build(),
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
            is_default=is_first_install,
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
    ) -> DiscoveryOutcome:
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
    ) -> tuple[AuthConfigEntity, DiscoveryOutcome, int]:
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

    async def install_auth_schemes(
        self, auth_configs: Sequence[AuthConfigEntity]
    ) -> dict[UUID, str]:
        """How each install actually authenticates, keyed by install id.

        Batched over the page: the answer needs each install's catalog kinds,
        and reading whole connector rows one at a time is a round trip per
        install on the connectors page's single call.
        """
        if not auth_configs:
            return {}
        return install_auth_schemes(
            auth_configs,
            kinds_by_connector=await self.connector_repository.kinds_for(
                [config.connector_id for config in auth_configs]
            ),
            system_oauth_config=self.system_oauth_config,
        )

    async def _resolve_auth_config(
        self,
        *,
        organization_id: UUID,
        connector_id: str | None = None,
        auth_config_id: UUID | None = None,
        auth_config_name: str | None = None,
        require_active: bool = True,
    ) -> AuthConfigEntity:
        """The install a caller named, however they named it.

        ``require_active`` is on by default and off only for the two callers
        that manage an install rather than use one -- deleting an install, and
        deleting an account on it, both of which have to keep working after an
        admin has switched it off. Every other caller is connecting to it or
        executing against it, which a DISABLED install must not allow.

        The name and connector-id lookups filter on ACTIVE in SQL; the id
        lookup did not, so `status: DISABLED` -- the only way short of deletion
        to stop an install being used -- stopped nothing that addressed it by
        id, which is what the connect-request, account and execution paths all
        do.
        """
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
        if require_active:
            reject_if_disabled(auth_config)
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

        # One query for every title, not one per distinct connector: this is
        # the connectors landing page's single call, and an org with installs
        # of a dozen connectors was paying a round trip each on every load.
        app_ids = {c.connector_id for c in configs} | {a.connector_id for a in accounts}
        app_titles = await self.connector_repository.titles_for(sorted(app_ids))

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
        auth_install = self._resolve_auth_install(connector, auth_config)
        if provider_value == AuthProvider.LEMMA.value:
            # The *install's* scheme, not the catalog's. `mcp` is one catalog
            # entry standing for every server a tenant may point at, so whether
            # a given install signs in through a browser is a property of that
            # server -- discovered when the install was created.
            if auth_install.auth_scheme != AuthScheme.OAUTH2:
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

        auth_provider = self._get_auth_provider_by_name(
            self._provider_value(auth_config)
        )
        state = secrets.token_urlsafe(32)
        redirect_uri = self.redirect_uri_builder.build()
        code_verifier = pkce_verifier_for(auth_install)

        try:
            (
                authorization_url,
                provider_state,
            ) = await auth_provider.get_authorization_url(
                install=auth_install,
                user_id=user_id,
                state=state,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
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
            attributes={
                "state": state,
                "provider_state": provider_state,
                **({"code_verifier": code_verifier} if code_verifier else {}),
            },
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
        if provider not in (AuthProvider.LEMMA, AuthProvider.COMPOSIO):
            raise UnsupportedAuthProviderError(provider.value)

        # Composio credential-managed apps must establish a connected account on
        # Composio's side; native (Lemma) apps store the credentials verbatim.
        auth_install = self._resolve_auth_install(connector, auth_config)

        # The *install's* scheme, matching `initiate_connect_request`. Reading
        # the catalog's instead let an MCP install that had negotiated OAuth at
        # create time still accept an empty credential POST, because the `mcp`
        # entry says API_KEY -- so the person got an account that looked
        # connected, held no token, and 401'd every call. The two gates
        # disagreeing is what made that state reachable at all.
        if auth_install.auth_scheme == AuthScheme.OAUTH2:
            raise ConnectorValidationError(
                "OAuth2 accounts must be connected with an OAuth connect request."
            )

        credentials = validated_account_credentials(
            connector, auth_config.kind, credentials
        )

        existing_account = await self.account_repository.get_by_user_and_auth_config(
            user_id, auth_config.id
        )
        # Multiple credential-managed accounts are allowed (e.g. several bot
        # tokens for different agents); the first connected becomes the default.
        is_default = existing_account is None

        auth_provider = self._get_auth_provider_by_name(provider.value)
        # Resolve, release, call, persist -- the rule in `docs/development.md`.
        # Everything above was a read, and everything below until the create is
        # a provider round trip, so the connection goes back here rather than
        # being held across a Composio connect and a profile fetch.
        await self.uow.commit()
        stored_credentials = await auth_provider.connect_with_credentials(
            install=auth_install,
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
                external_ref=resolve_external_ref(connector.id, stored_credentials),
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
        """See `oauth_callback`, which owns the resolve/release/call/persist
        split this path needs and the request-scoped session cannot express
        inline."""
        return await handle_oauth_callback(self, redirect_uri=redirect_uri, state=state)

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
        auth_install = self._resolve_auth_install(connector, auth_config)

        oauth_credentials = self._to_oauth_credentials(credentials)
        expires_at = (
            oauth_credentials.expires_at
            if hasattr(oauth_credentials, "expires_at")
            else None
        )
        if expires_at is not None:
            now = datetime.now(tz=expires_at.tzinfo)
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
                        install=auth_install,
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
                    # The stored token has not expired yet, so the account
                    # keeps working -- for now. But a provider that rejects a
                    # refresh has usually revoked the grant, which is exactly
                    # the case an expiry check cannot see, and at debug this
                    # left no trace at all in production: the first signal was
                    # the account flipping to REAUTH_REQUIRED hours later, with
                    # nothing saying when it actually broke.
                    logger.warning(
                        "connectors.connector_service.credential_refresh_rejected.degraded",
                        account_id=str(account_id),
                        connector_id=account.connector_id,
                        error_type=type(exc).__name__,
                        exc_info=True,
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
        # Disconnecting from an install an admin has switched off is the one
        # thing that must still work while it is off.
        auth_config = await self._resolve_auth_config(
            organization_id=resolved_organization_id,
            auth_config_id=account.auth_config_id,
            require_active=False,
        )
        auth_install = (
            self._resolve_auth_install(connector, auth_config) if connector else None
        )

        revocable_credentials = (
            self._to_oauth_credentials(account.credentials)
            if account.credentials
            and self._should_revoke_account(
                connector=connector, auth_config=auth_config
            )
            else None
        )
        auth_provider = self._get_auth_provider_by_name(
            self._provider_value(auth_config)
        )
        # Revoke with no connection held, then delete -- what
        # `delete_auth_config` twenty lines below already does, and for the
        # same reason: a provider round trip on a request-scoped session pins a
        # pooled connection for as long as the provider takes.
        await self.uow.commit()

        if revocable_credentials is not None:
            await revoke_one(
                auth_provider,
                install=auth_install,
                credentials=revocable_credentials,
                user_id=user_id,
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
            allowed_roles=INSTALL_MANAGER_ROLES,
        )
        auth_config = await self._resolve_auth_config(
            organization_id=organization_id,
            auth_config_id=auth_config_id,
            auth_config_name=auth_config_name,
            require_active=False,
        )
        accounts = await self.account_repository.list_by_auth_config(auth_config.id)
        connector = await self.connector_repository.get(auth_config.connector_id)
        auth_install = (
            self._resolve_auth_install(connector, auth_config) if connector else None
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
            await revoke_one(
                auth_provider,
                install=auth_install,
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
