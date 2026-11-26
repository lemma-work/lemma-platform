"""Operations through a pod connector: browsing them, and planning a call.

Out of ``connector_operation_service`` so that file stays under the size
ceiling, and because an APP connector changes both halves:

- **browsing** leaves out the operations GitHub opens only to a person
  (``operation_visibility.offered_by_mode``);
- **planning** a call on one has no account: no credential, no account id,
  ``act_as`` always ``app_only``, and the organization from the installation.
  Which of the App's installations the call acts on is picked in the execute
  phase (``with_app_installation``), which holds no connection while it may
  have to ask GitHub which account an installation is on.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationDiscoverResponse,
)
from app.modules.connectors.domain.app_identity import (
    TOKEN_REFUSED,
    USER_ONLY_OPERATION,
    AppIdentityUnavailableError,
)
from app.modules.connectors.domain.app_installation import (
    pick_installation,
    requested_owner,
)
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.connector import kind_to_provider
from app.modules.connectors.domain.errors import (
    ConnectorNotFoundError,
    ConnectorReauthRequiredError,
)
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution
from app.modules.connectors.domain.pod_connector import (
    PodConnectorEntity,
    PodConnectorMode,
)
from app.modules.connectors.domain.pod_connector_errors import (
    ConnectorUnavailableError,
)
from app.modules.connectors.services.auth.github_app_installations import (
    InstallationFetcher,
    checked_app_installations,
)
from app.modules.connectors.services.auth.github_installation import install_url
from app.modules.connectors.services.installation_reach import (
    browsable,
    connector_for_call,
)
from app.modules.connectors.services.operation_visibility import (
    count_operations_for_install,
    offered_by_mode,
)
from app.modules.connectors.services.pod_connector_resolution import (
    PodConnectorResolver,
    ResolvedAppConnector,
    reauth_required_for,
)

if TYPE_CHECKING:
    from app.modules.connectors.services.connector_operation_service import (
        ConnectorOperationService,
    )

#: Operations listed past ``limit`` for an APP connector, so leaving out the
#: user-only ones still fills the page. GitHub opens only a small, fixed set of
#: routes to people alone (``/user/...``, gists); this is comfortably more.
_USER_ONLY_MARGIN = 32


@dataclass(frozen=True, slots=True)
class BrowseTarget:
    """Whose operations a name shows, and the pod connector's mode if it is one."""

    connector_id: str
    kind: str | None
    auth_config_id: UUID | None
    mode: PodConnectorMode | None = None


async def is_app_connector(
    resolver: PodConnectorResolver, pod_id: UUID, slug: str
) -> bool:
    """Whether ``slug`` names one of the pod's APP connectors. A read only:
    the caller decides before resolving whether ``act_as`` needs a function."""
    found = await resolver.pod_connectors.get_by_slug(pod_id, slug)
    return found is not None and found.mode is PodConnectorMode.APP


async def usable_pod_connector(
    service: ConnectorOperationService, ctx: Context, pod_id: UUID, slug: str
) -> tuple[PodConnectorEntity, AuthConfigEntity]:
    connector = await service.pod_connector_resolver.connector(pod_id, slug)
    if service.connector_service is None:
        raise ConnectorNotFoundError(slug)
    auth_config = await service.connector_service.auth_config_repository.get(
        connector.auth_config_id
    )
    if auth_config is None:
        raise ConnectorNotFoundError(slug)
    await service.pod_connector_resolver.require_use(ctx, connector, auth_config)
    return connector, auth_config


async def browse(
    service: ConnectorOperationService, ctx: Context, pod_id: UUID, name: str
) -> BrowseTarget:
    """Connector, kind and install whose operations ``name`` shows: a pod
    connector the caller may use, else an installation or catalog entry
    the pod has none for (``browsable``)."""
    if service.connector_service is not None:
        found = await browsable(
            service.pod_connector_resolver,
            service.connector_service.connector_repository,
            ctx,
            pod_id,
            name,
        )
        if found is not None:
            return BrowseTarget(found.connector_id, found.kind, found.auth_config_id)
    connector, auth_config = await usable_pod_connector(service, ctx, pod_id, name)
    return BrowseTarget(
        auth_config.connector_id, auth_config.kind.value, auth_config.id, connector.mode
    )


async def pod_connector_operations(
    service: ConnectorOperationService,
    ctx: Context,
    *,
    pod_id: UUID,
    slug: str,
    query: str | None,
    limit: int | None,
) -> OperationDiscoverResponse:
    target = await browse(service, ctx, pod_id, slug)
    if target.mode is not PodConnectorMode.APP:
        return await service.discover_operations(
            target.connector_id,
            query=query,
            limit=limit,
            kind=target.kind,
            auth_config_id=target.auth_config_id,
        )
    listed = await service.list_operation_entities(
        target.connector_id,
        kind=target.kind,
        search_query=query,
        limit=None if limit is None else limit + _USER_ONLY_MARGIN,
        auth_config_id=target.auth_config_id,
    )
    offered = offered_by_mode(listed, PodConnectorMode.APP)
    total = (
        len(listed)
        if query is None and limit is None
        else await count_operations_for_install(
            catalog_repository=service.operation_repository,
            install_repository=service.auth_config_operation_repository,
            connector_id=target.connector_id,
            kind=target.kind,
            auth_config_id=target.auth_config_id,
        )
    )
    items = [
        service.operation_summary(operation, query=query)
        for operation in (offered if limit is None else offered[:limit])
    ]
    return OperationDiscoverResponse(
        connector_id=target.connector_id,
        query=query,
        items=items,
        # Less the user-only operations this listing saw. Exact for a full
        # listing; a narrowed one may still count the few it did not reach.
        total_operations=max(total - (len(listed) - len(offered)), len(items)),
        returned_count=len(items),
    )


async def resolve_pod_connector_execution(
    service: ConnectorOperationService,
    ctx: Context,
    *,
    pod_id: UUID,
    slug: str,
    operation_name: str,
    payload: dict[str, object],
    act_as: str,
) -> ResolvedConnectorExecution:
    """Authorize, pick the account -- or the App -- and load what the call needs."""
    resolver = service.pod_connector_resolver
    pod_connector = await connector_for_call(resolver, ctx, pod_id, slug)
    resolved = await resolver.resolve_any(ctx, pod_connector)
    if isinstance(resolved, ResolvedAppConnector):
        return await _app_plan(
            service, ctx, resolved, operation_name=operation_name, payload=payload
        )
    try:
        plan = await service.execution_for(
            ctx,
            auth_config=resolved.auth_config,
            account=resolved.account,
            operation_name=operation_name,
            payload=payload,
            act_as=act_as,
        )
    except ConnectorReauthRequiredError as exc:
        raise reauth_required_for(ctx, pod_connector, resolved) from exc
    return replace(plan, pod_connector=pod_connector.slug)


async def _app_plan(
    service: ConnectorOperationService,
    ctx: Context,
    resolved: ResolvedAppConnector,
    *,
    operation_name: str,
    payload: dict[str, object],
) -> ResolvedConnectorExecution:
    """The plan for a call as the App: nobody's account, ``app_only`` always.

    ``act_as`` has nothing to choose -- whatever the caller passed, every call
    is the App -- and a route GitHub opens only to a person is refused here,
    before anything is sent, with the reason a client can act on.
    """
    auth_config = resolved.auth_config
    slug = resolved.pod_connector.slug
    operation = await service.operation_for(auth_config, operation_name)
    if (operation.execution or {}).get("github_token_kind") == "user_only":
        raise AppIdentityUnavailableError(
            "GitHub lets only a person make this call, and this connector acts "
            "as the organization's app.",
            code=USER_ONLY_OPERATION,
            slug=slug,
        )
    kind = auth_config.kind.value
    return ResolvedConnectorExecution(
        connector_id=auth_config.connector_id,
        operation_execution_name=operation.execution_name,
        provider=kind_to_provider(kind).value,
        kind=kind,
        connection_config=getattr(auth_config, "config", None),
        execution=getattr(operation, "execution", None),
        operation_name=operation.name,
        input_schema=getattr(operation, "input_schema", None),
        output_schema=getattr(operation, "output_schema", None),
        third_party_credentials={},
        payload=payload or {},
        act_as="app_only",
        acting_user_id=ctx.delegating_user_id or ctx.user_id,
        organization_id=auth_config.organization_id,
        pod_connector=slug,
        app_installations=resolved.app_installations,
    )


async def with_app_installation(
    plan: ResolvedConnectorExecution,
    *,
    fetch: InstallationFetcher | None = None,
) -> ResolvedConnectorExecution:
    """An APP plan with the installation its payload's owner picks; any other
    plan unchanged. Holds no connection: it may ask GitHub which account an
    unchecked installation is on."""
    if plan.app_installations is None:
        return plan
    candidates = await checked_app_installations(plan.app_installations, fetch=fetch)
    picked = pick_installation(
        candidates,
        owner=requested_owner(plan.payload),
        slug=plan.pod_connector or plan.connector_id,
        install_url=install_url(),
    )
    return replace(
        plan, account_external_ref=picked.installation_id, app_installations=candidates
    )


@contextmanager
def app_refusals_translated(plan: ResolvedConnectorExecution) -> Iterator[None]:
    """An APP connector whose App GitHub would not mint a token for is a
    connector that cannot run right now (``CONNECTOR_UNAVAILABLE``), not a
    refusal of how the caller asked."""
    try:
        yield
    except AppIdentityUnavailableError as exc:
        if plan.acts_as_app_connector and exc.reason == TOKEN_REFUSED:
            raise ConnectorUnavailableError(
                slug=plan.pod_connector or plan.connector_id, app_token_refused=True
            ) from exc
        raise


__all__ = [
    "BrowseTarget",
    "app_refusals_translated",
    "browse",
    "is_app_connector",
    "pod_connector_operations",
    "resolve_pod_connector_execution",
    "usable_pod_connector",
    "with_app_installation",
]
