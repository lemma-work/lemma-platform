"""A pod connector as one caller sees it: its status, and whether they can use it.

Out of ``pod_connector_service`` so that file stays under the size ceiling, and
because the connections read (``pod_connections.py``) asks the same two
questions of every connector.
"""

from __future__ import annotations

from typing import Protocol

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    PodConnectorResponse,
    SharedAccountSummary,
)
from app.modules.connectors.domain.app_installation import app_is_installed
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigStatus,
)
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodConnectorEntity,
    PodConnectorMode,
    PodConnectorStatus,
    connector_status,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodAccountRepository,
)
from app.modules.connectors.services.auth.github_app_installations import (
    bound_app_installations,
)
from app.modules.connectors.services.pod_connector_use import can_use


class PresentingParts(Protocol):
    """The reads presenting a connector takes: ``PodConnectorService``'s."""

    uow: SqlAlchemyUnitOfWork
    auth_configs: AuthConfigRepository
    pod_accounts: PodAccountRepository
    accounts: AccountRepositoryPort


async def status_of(
    parts: PresentingParts,
    connector: PodConnectorEntity,
    auth_config: AuthConfigEntity | None,
    shared: PodAccountEntity | None,
) -> PodConnectorStatus:
    """``connector_status``, with the App's installations read for APP."""
    app_installed = False
    if connector.mode is PodConnectorMode.APP and auth_config is not None:
        app_installed = app_is_installed(
            await bound_app_installations(
                parts.uow.session, auth_config.organization_id
            )
        )
    return connector_status(
        connector,
        installation_active=auth_config is not None
        and auth_config.status is AuthConfigStatus.ACTIVE,
        shared_account=shared,
        app_installed=app_installed,
    )


async def present_connector(
    parts: PresentingParts, connector: PodConnectorEntity, ctx: Context
) -> PodConnectorResponse:
    """The connector as ``ctx`` sees it, ``can_use`` included: the one
    check the "connect yours" banner needs, asked of the caller alone."""
    auth_config = await parts.auth_configs.get(connector.auth_config_id)
    shared = (
        await parts.pod_accounts.get(connector.shared_pod_account_id)
        if connector.shared_pod_account_id
        else None
    )
    summary = None
    if shared is not None:
        account = await parts.accounts.get(shared.account_id)
        summary = SharedAccountSummary(
            pod_account_id=shared.id,
            owner_user_id=shared.owner_user_id,
            display_name=account.display_name if account else None,
            email=account.email if account else None,
        )
    status = await status_of(parts, connector, auth_config, shared)
    return PodConnectorResponse(
        **connector.model_dump(),
        installation_name=auth_config.name if auth_config else None,
        connector_id=auth_config.connector_id if auth_config else None,
        shared_account=summary,
        can_use=await can_use(ctx, connector, status, shared),
        status=status,
    )


__all__ = ["PresentingParts", "present_connector", "status_of"]
