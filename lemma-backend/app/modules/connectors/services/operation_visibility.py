"""What operations an install actually exposes.

An install can draw operations from two places: the global catalog, keyed by
(connector_id, kind), and its own discovered set in ``auth_config_operations``,
populated from a live MCP or OpenAPI server.

Execution already understood this -- it looks up the install's own operation
first and falls back to the catalog. Every *listing* path did not: they resolved
the auth config, took its ``connector_id`` and ``kind``, and then queried the
catalog alone. For a Composio or package install that is right, because the
catalog is where its operations live. For mcp and http it means the catalog has
nothing to offer and the answer is empty -- so an MCP server's tools were
executable only by someone who already knew a name, while list, detail, search
and the agent toolset all reported that the install had no operations at all.

Precedence matches execution exactly: an install's own operation wins over a
catalog operation of the same name, because where both exist the install
describes the server actually being called.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


async def list_operations_for_install(
    *,
    catalog_repository: Any,
    install_repository: Any | None,
    connector_id: str,
    kind: str | None = None,
    search_query: str | None = None,
    limit: int | None = None,
    auth_config_id: UUID | None = None,
) -> list[Any]:
    """Every operation an install exposes, from both sources."""
    if kind:
        catalog = await catalog_repository.list_by_connector_kind(
            connector_id, kind, search_query=search_query, limit=limit
        )
    else:
        catalog = await catalog_repository.list_by_connector(
            connector_id, search_query=search_query, limit=limit
        )
    return await merge_install_and_catalog_operations(
        repository=install_repository,
        auth_config_id=auth_config_id,
        catalog_operations=list(catalog),
        search_query=search_query,
        limit=limit,
    )


async def merge_install_and_catalog_operations(
    *,
    repository: Any | None,
    auth_config_id: UUID | None,
    catalog_operations: list[Any],
    search_query: str | None = None,
    limit: int | None = None,
) -> list[Any]:
    """Return the operations an install exposes, install-first."""
    if repository is None or auth_config_id is None:
        return catalog_operations

    install_operations = list(
        await repository.list_by_auth_config(
            auth_config_id, search_query=search_query, limit=limit
        )
    )
    if not install_operations:
        return catalog_operations

    shadowed = {str(operation.name).lower() for operation in install_operations}
    merged = install_operations + [
        operation
        for operation in catalog_operations
        if str(operation.name).lower() not in shadowed
    ]
    # `limit` was applied to each source separately, so it has to be applied
    # again to the union or a caller asking for 10 could receive 20.
    return merged[:limit] if limit is not None else merged


async def find_install_or_catalog_operation(
    *,
    catalog_repository: Any,
    install_repository: Any | None,
    connector_id: str,
    operation_name: str,
    kind: str | None = None,
    auth_config_id: UUID | None = None,
) -> Any | None:
    """Resolve one operation by name, install-first, then catalog."""
    if install_repository is not None and auth_config_id is not None:
        found = await install_repository.get_by_auth_config_and_name(
            auth_config_id, operation_name
        )
        if found is not None:
            return found
    if kind:
        return await catalog_repository.get_by_connector_kind_and_name(
            connector_id, kind, operation_name
        )
    return await catalog_repository.get_by_connector_and_name(
        connector_id, operation_name
    )
