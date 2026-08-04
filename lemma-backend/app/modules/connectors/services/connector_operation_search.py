"""Operation search across every connector installed in an organization.

Discovery is keyed by install name, which assumes the caller already knows which
connector provides what. "Which operation sends an email?" doesn't — and the only
way to answer it was to fan out one request per install (ten on a real org) or
give up and ask a human. This does that fan-out once, server-side and bounded,
and labels every hit with the install to execute it against.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.connectors.domain.errors import ConnectorDomainError
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationDiscoverResponse,
)

if TYPE_CHECKING:
    from app.modules.connectors.services.connector_operation_service import (
        ConnectorOperationService,
    )

logger = get_logger(__name__)

# Caps on the work one request may do: how many installs are considered, and how
# many are queried at a time. An org with many installs must not turn a single
# search into an unbounded burst against the operation store.
MAX_AUTH_CONFIGS = 50
CONCURRENCY = 8


async def search_across_auth_configs(
    service: ConnectorOperationService,
    *,
    user_id: UUID,
    organization_id: UUID,
    query: str | None = None,
    limit: int | None = None,
) -> OperationDiscoverResponse:
    """Ranked operations from every install, each labelled with its auth config."""
    connector_service = getattr(service, "connector_service", None)
    if connector_service is None:
        return _empty(query)

    configs, _cursor = await connector_service.list_auth_configs(
        user_id=user_id,
        organization_id=organization_id,
        limit=MAX_AUTH_CONFIGS,
    )
    if not configs:
        return _empty(query)

    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(config: Any) -> tuple[str, OperationDiscoverResponse | None]:
        async with gate:
            try:
                return config.name, await service.discover_operations_for_auth_config(
                    user_id=user_id,
                    organization_id=organization_id,
                    auth_config_name=config.name,
                    query=query,
                    limit=limit,
                )
            except ConnectorDomainError:
                # One unreachable or misconfigured install must not sink the
                # search. Every realistic failure here — unknown install, access
                # denied, provider temporarily unavailable — is a
                # ConnectorDomainError; anything else is a genuine bug and
                # should surface rather than be swallowed.
                logger.debug(
                    "connectors.search.install_failed.diagnostic",
                    auth_config=config.name,
                )
                return config.name, None

    results = await asyncio.gather(*(one(config) for config in configs))

    items: list[Any] = []
    total = 0
    for name, response in results:
        if response is None:
            continue
        total += response.total_operations
        items.extend(
            item.model_copy(
                update={"auth_config": name, "connector_id": response.connector_id}
            )
            for item in response.items
        )
    items.sort(
        key=lambda hit: float(getattr(hit, "relevance_score", 0.0) or 0.0),
        reverse=True,
    )
    capped = items[: (limit or 100)]
    return OperationDiscoverResponse(
        connector_id="",
        items=capped,
        returned_count=len(capped),
        total_operations=total,
        query=query,
    )


def _empty(query: str | None) -> OperationDiscoverResponse:
    return OperationDiscoverResponse(
        connector_id="",
        items=[],
        returned_count=0,
        total_operations=0,
        query=query,
    )
