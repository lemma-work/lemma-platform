"""Turning a request to install a connector into a usable install.

Two steps that belong together and to neither service: validating the config the
tenant supplied, and populating the operation set for kinds that discover it
from a live server.

Validation runs for every kind, without exception. The previous validator
returned early for any non-OAuth2 native connector -- which is exactly sql, mcp
and http, the three whose config is entirely tenant-written -- so their
``additionalProperties: false`` was decorative and a ``server_url`` was never
checked against anything.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.domain.auth_config import AuthConfigEntity, AuthConfigSource
from app.modules.connectors.domain.connector import ConnectorEntity, ConnectorKind
from app.modules.connectors.domain.errors import (
    ConnectorDomainError,
    ConnectorValidationError,
    UnsupportedAuthProviderError,
)
from app.modules.connectors.services.install_service_seam import (
    InstallServiceSeam,
)

logger = get_logger(__name__)


def _registry():
    from app.modules.connectors.infrastructure.kinds import build_kind_registry

    # Neither gateway is reachable from install-time work: validation and
    # discovery never execute an operation.
    return build_kind_registry(composio_gateway=None, package_gateway=None)


def resolve_install_kind(connector: ConnectorEntity, kind: str | None) -> ConnectorKind:
    """Pick which of a connector's kinds this install uses.

    Most connectors offer exactly one, so asking the caller to name it would be
    ceremony; the ones that offer two (a vendored package *and* a Composio
    toolkit for the same app) genuinely need the answer, and guessing there
    would silently install the wrong one.
    """
    supported = connector.supported_kinds()
    if kind is None:
        if len(supported) == 1:
            return supported[0]
        raise ConnectorValidationError(
            f"Connector '{connector.id}' can be installed as more than one kind; "
            "specify which.",
            details={
                "reason": "ambiguous_kind",
                "supported_kinds": [item.value for item in supported],
            },
        )
    try:
        return connector.spec_for(kind).kind
    except ValueError as exc:
        raise ConnectorValidationError(
            f"Connector '{connector.id}' cannot be installed as '{kind}'.",
            details={
                "reason": "unsupported_kind",
                "supported_kinds": [item.value for item in supported],
            },
        ) from exc


async def org_has_install(
    auth_config_repository: Any, organization_id: UUID, connector_id: str
) -> bool:
    existing = await auth_config_repository.get_active_by_org_and_app(
        organization_id, connector_id
    )
    return existing is not None


async def validate_install_config(
    *,
    connector: ConnectorEntity,
    kind: ConnectorKind,
    config: dict[str, Any],
    config_source: AuthConfigSource,
) -> dict[str, Any]:
    """Validate and normalize the config for one install."""
    try:
        spec = connector.spec_for(kind)
    except ValueError as exc:
        raise UnsupportedAuthProviderError(str(kind)) from exc
    plugin = _registry().get(kind)
    if plugin.installer is None:
        return config
    return await plugin.installer.validate_install(
        spec=spec, config=config, config_source=config_source
    )


async def discover_install_operations(
    auth_config: AuthConfigEntity,
    connector: ConnectorEntity,
    *,
    repository: Any,
    uow: Any,
    credentials: dict[str, Any] | None = None,
) -> int:
    """Populate an install's operations, if its kind discovers them.

    Runs after the install is committed, so a discovery failure leaves a usable
    (if empty) install rather than rolling back the connection the user just
    made. Failures are logged, never raised -- the operator's recovery path is
    the refresh endpoint, which is why that exists.
    """
    if repository is None:
        return 0

    from app.modules.connectors.domain.kinds import ResolvedInstall
    from app.modules.connectors.services.discovery.base import assign_unique_names
    from app.modules.connectors.services.execution import KindDispatcher

    registry = _registry()
    if registry.get(auth_config.kind).discoverer is None:
        return 0

    install = ResolvedInstall(
        connector_id=auth_config.connector_id,
        kind=auth_config.kind,
        auth_config_id=auth_config.id,
        organization_id=auth_config.organization_id,
        config=auth_config.config or {},
        config_source=auth_config.config_source,
        spec=connector.spec_for(auth_config.kind),
    )
    try:
        found = await KindDispatcher(registry).discover(install, credentials)
    except ConnectorDomainError as exc:
        logger.warning(
            "connectors.connector_service.auth_config_operation_discovery.failed",
            auth_config_id=auth_config.id,
            error_type=type(exc).__name__,
        )
        return 0

    # Names are disambiguated before storage: two tools whose names normalize
    # alike would otherwise collide on the unique index and abort the whole
    # re-discovery.
    names = assign_unique_names([op.display_name or op.name for op in found])
    await repository.replace_for_auth_config(
        auth_config_id=auth_config.id,
        organization_id=auth_config.organization_id,
        operations=[
            {
                "name": name,
                "provider_operation_name": op.name,
                "display_name": op.display_name,
                "description": op.description,
                "input_schema": op.input_schema,
                "output_schema": op.output_schema,
                "execution": op.execution,
            }
            for name, op in zip(names, found)
        ],
    )
    await uow.commit()
    return len(found)


async def refresh_install_operations(
    service: InstallServiceSeam,
    *,
    user_id: UUID,
    organization_id: UUID,
    auth_config_name: str,
) -> int:
    """Re-run discovery for an existing install.

    The recovery path. Without it, a discovery that failed when the install was
    created could only be fixed by deleting the install -- and accounts cascade
    from it, so that would disconnect every user who had connected.
    """
    auth_config = await service.get_auth_config_by_name(
        user_id=user_id,
        organization_id=organization_id,
        auth_config_name=auth_config_name,
    )
    connector = await service.get_connector(auth_config.connector_id)
    return await discover_install_operations(
        auth_config,
        connector,
        repository=service.auth_config_operation_repository,
        uow=service.uow,
        credentials=await discovery_credentials(service, auth_config),
    )


async def discovery_credentials(
    service: InstallServiceSeam, auth_config: AuthConfigEntity
) -> dict[str, Any] | None:
    """A connected account's credentials, for an install that keeps none itself.

    Most installs carry whatever discovery needs in their own config -- an MCP
    server's ``bearer_token``, a database URL. An install authorized by OAuth
    carries nothing: the token belongs to the account, so discovering with the
    install alone reaches the server unauthenticated and is refused. That is
    what a real OAuth-protected MCP server did, answering 401 to a tool listing
    and leaving the install with zero operations.

    Any connected account will do. The tool list is a property of the server,
    not of who is asking, so this is not a choice between different answers --
    it is a choice of which valid token to ask with.
    """
    accounts = await service.account_repository.list_by_auth_config(auth_config.id)
    for account in accounts:
        if account.status is not AccountStatus.CONNECTED or not account.credentials:
            continue
        try:
            credentials = await service.get_account_credentials(
                account.id, account.user_id, auth_config.organization_id
            )
        except ConnectorDomainError as exc:
            # One unusable account should not stop discovery from trying the
            # next: a revoked token here is ordinary, not exceptional.
            logger.info(
                "connectors.connector_service.discovery_credentials.skipped",
                auth_config_id=auth_config.id,
                error_type=type(exc).__name__,
            )
            continue
        return credentials.model_dump(exclude_none=True)
    return None


async def discover_operations_for_new_account(
    service: InstallServiceSeam, auth_config: AuthConfigEntity
) -> None:
    """Fill in an OAuth install's operations once somebody has connected.

    An install whose credential lives on the account cannot be discovered when
    it is created -- there is no account yet -- so it is committed with zero
    operations and the first connection is the earliest moment discovery can
    succeed. Without this the install stays empty until an operator finds the
    refresh endpoint, which is not something a person connecting an MCP server
    should have to know about.

    Only when the install has none. The tool list belongs to the server, so
    re-running it for the second and later people to connect would re-ask the
    same question and answer it the same way.
    """
    repository = getattr(service, "auth_config_operation_repository", None)
    if repository is None:
        return
    existing = await repository.list_by_auth_config(auth_config.id, limit=1)
    if existing:
        return
    connector = await service.get_connector(auth_config.connector_id)
    await discover_install_operations(
        auth_config,
        connector,
        repository=repository,
        uow=service.uow,
        credentials=await discovery_credentials(service, auth_config),
    )
