"""Connector accounts and operations, as another module's build step uses them.

Six operations, not `ConnectorService` and `ConnectorOperationService`. Handing
those out let the pod-bundle exporter reach `service.account_repository` and
`service.auth_config_repository` straight through the service object, so a
repository's read shape was part of another module's export code:
`resolve_account_connector` is that pair of reads, published as the one question
it was asking.

`resolve_operation` and `execute_resolved` stay separate for the reason the
operation service documents: resolving needs a DB session and running the
external call does not, and the publish job runs eight of these at once against
a pool of ten.

A submodule for the same reason as `retirement.py` next to it: importing this
pulls the model layer, and `contracts/__init__` is imported by anything that
wants any contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.api.dependencies import (
    build_connector_operation_service,
    get_connector_service,
)
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationExecutionResponse,
)
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution


async def get_account(uow, account_id: UUID) -> AccountEntity | None:
    """The connector account with this id, or ``None`` if it is gone."""
    return await get_connector_service(uow).account_repository.get(account_id)


async def get_account_kind(uow, account: AccountEntity) -> str | None:
    """How this account's connector is installed (``package``, ``composio``, …).

    One connector id can be installed more than one way, and the operation
    names differ between them, so a resource bound to an account is only
    portable to an account of the same kind.
    """
    return await get_connector_service(uow).get_account_kind(account)


async def resolve_account_connector(uow, account_id: UUID) -> tuple[str, str] | None:
    """Ground-truth ``(connector_id, kind)`` for an account, or ``None`` if gone.

    Resolved from the account and its auth config rather than guessed from the
    name of whatever holds it -- a surface's platform, a schedule's directory --
    since that guess is wrong for any resource with no platform of its own.
    """
    service = get_connector_service(uow)
    account = await service.account_repository.get(account_id)
    if account is None:
        return None
    auth_config = await service.auth_config_repository.get(account.auth_config_id)
    if auth_config is None:
        return None
    return account.connector_id, auth_config.kind.value


async def installation_for_bundle_name(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID, name: str
) -> UUID | None:
    """The organization's active installation a pod bundle means by ``name``.

    A bundle's grant names a pod connector by slug, and a pod connector is
    declared per pod: one exported before that, or from a pod whose connector
    the importing pod has never declared, names it by what the exporting pod
    called it -- the installation's name, or the catalog connector id the
    slug was made from. The installation of that name wins; otherwise the
    organization's default install of the catalog connector of that id.
    ``None`` when the organization has neither.
    """
    auth_configs = get_connector_service(uow).auth_config_repository
    install = await auth_configs.get_active_by_org_and_name(organization_id, name)
    if install is None:
        install = await auth_configs.get_active_by_org_and_app(organization_id, name)
    return install.id if install is not None else None


async def resolve_operation(
    uow,
    *,
    ctx: Context,
    pod_id: UUID,
    connector: str,
    operation_name: str,
    payload: dict[str, object],
) -> ResolvedConnectorExecution:
    """Authorize an operation through a pod connector and resolve its plan.

    ``connector`` is the pod connector's slug; the account follows from its
    mode and from ``ctx``. The only half that touches the database. Leave the
    session's scope before calling :func:`execute_resolved`.
    """
    return await build_connector_operation_service(uow).resolve_pod_connector_execution(
        ctx,
        pod_id=pod_id,
        slug=connector,
        operation_name=operation_name,
        payload=payload,
    )


async def execute_resolved(
    uow, resolved: ResolvedConnectorExecution
) -> OperationExecutionResponse:
    """Run an already-resolved operation. Issues no DB I/O of its own."""
    return await build_connector_operation_service(uow).execute_resolved(resolved)


__all__ = [
    "execute_resolved",
    "get_account",
    "get_account_kind",
    "installation_for_bundle_name",
    "resolve_account_connector",
    "resolve_operation",
]
