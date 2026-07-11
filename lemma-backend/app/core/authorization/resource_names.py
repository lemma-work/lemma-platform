"""Batched name<->id resolution for pod resources used in grant APIs.

Grant storage keeps UUIDs (renames must not break grants); the public APIs
speak resource names. This module is the single source of truth for which
resource types are name-addressable and how their names map to ids.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import ResourceType
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.datastore.infrastructure.models.datastore_models import (
    DatastoreFile,
    DatastoreTable,
)
from app.modules.apps.infrastructure.models import AppModel
from app.modules.function.infrastructure.models import FunctionModel
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.workflow.infrastructure.models import WorkflowModel

logger = structlog.get_logger(__name__)

# resource_type -> (id column, pod column, name column, extra filters)
_NAME_REGISTRY = {
    ResourceType.AGENT: (AgentModel.id, AgentModel.pod_id, AgentModel.name, ()),
    ResourceType.FUNCTION: (
        FunctionModel.id,
        FunctionModel.pod_id,
        FunctionModel.name,
        (),
    ),
    ResourceType.WORKFLOW: (WorkflowModel.id, WorkflowModel.pod_id, WorkflowModel.name, ()),
    ResourceType.APP: (AppModel.id, AppModel.pod_id, AppModel.name, ()),
    ResourceType.DATASTORE_TABLE: (
        DatastoreTable.id,
        DatastoreTable.pod_id,
        DatastoreTable.table_name,
        (),
    ),
    ResourceType.SCHEDULE: (
        Schedule.id,
        Schedule.pod_id,
        Schedule.name,
        (Schedule.is_internal.is_(False),),
    ),
    ResourceType.FOLDER: (
        DatastoreFile.id,
        DatastoreFile.pod_id,
        DatastoreFile.path,
        (),
    ),
    ResourceType.DOCUMENT: (
        DatastoreFile.id,
        DatastoreFile.pod_id,
        DatastoreFile.path,
        (),
    ),
}

# CONNECTOR_ACCOUNT has no human-assigned name; its public identifier is the
# account id string. Account grants are environment-specific by nature and are
# not portable across pods/environments.
NAME_ADDRESSABLE_RESOURCE_TYPES = frozenset(_NAME_REGISTRY) | {
    ResourceType.CONNECTOR,
    ResourceType.CONNECTOR_ACCOUNT,
}

# The datastore root path "/" is the pod-wide document grant: a folder grant
# named "/" resolves to the pod id sentinel, which the authorizer treats as
# matching every document/folder in the pod (see sql_actions / service.py).
_POD_WIDE_GRANT_NAME = "/"
_PREFIX_GRANT_RESOURCE_TYPES = (ResourceType.FOLDER, ResourceType.DOCUMENT)


_CONNECTOR_RESOURCE_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "lemma.connector.resource",
)


def connector_resource_id(connector_id: str) -> UUID:
    """Return the stable internal resource id for a global connector app."""
    return uuid5(_CONNECTOR_RESOURCE_NAMESPACE, connector_id)


async def resolve_resource_id_by_name(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_name: str,
) -> UUID | None:
    """Resolve one name to an id; returns None for unknown names or
    resource types that are not name-addressable."""
    if resource_type == ResourceType.CONNECTOR:
        connector = await session.get(Connector, resource_name)
        if connector is None or not connector.is_active:
            return None
        return connector_resource_id(resource_name)
    if resource_type == ResourceType.CONNECTOR_ACCOUNT:
        try:
            account_id = UUID(resource_name)
        except ValueError:
            return None
        return (
            await session.execute(select(Account.id).where(Account.id == account_id))
        ).scalar_one_or_none()
    if (
        resource_type in _PREFIX_GRANT_RESOURCE_TYPES
        and resource_name == _POD_WIDE_GRANT_NAME
    ):
        return pod_id
    entry = _NAME_REGISTRY.get(resource_type)
    if entry is None:
        return None
    id_col, pod_col, name_col, extra_filters = entry
    stmt = select(id_col).where(
        pod_col == pod_id,
        name_col == resource_name,
        *extra_filters,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def resolve_resource_ids_by_names(
    session: AsyncSession,
    *,
    pod_id: UUID,
    refs: Sequence[tuple[ResourceType, str]],
) -> dict[tuple[ResourceType, str], UUID]:
    """Resolve (resource_type, name) pairs to internal UUIDs.

    Batched: one query per resource type. Raises HTTP 400 listing every
    unresolved name so clients can fix a payload in one round trip.
    """
    names_by_type: dict[ResourceType, set[str]] = {}
    for resource_type, name in refs:
        names_by_type.setdefault(resource_type, set()).add(name)

    resolved: dict[tuple[ResourceType, str], UUID] = {}
    unsupported: set[ResourceType] = set()
    for resource_type, names in names_by_type.items():
        if resource_type == ResourceType.CONNECTOR:
            stmt = select(Connector.id).where(
                Connector.id.in_(names),
                Connector.is_active.is_(True),
            )
            for connector_id in (await session.execute(stmt)).scalars():
                resolved[(resource_type, connector_id)] = (
                    connector_resource_id(connector_id)
                )
            continue
        if resource_type == ResourceType.CONNECTOR_ACCOUNT:
            account_ids_by_name = {}
            for name in names:
                try:
                    account_ids_by_name[UUID(name)] = name
                except ValueError:
                    continue
            if account_ids_by_name:
                stmt = select(Account.id).where(
                    Account.id.in_(account_ids_by_name)
                )
                for account_id in (await session.execute(stmt)).scalars():
                    resolved[
                        (resource_type, account_ids_by_name[account_id])
                    ] = account_id
            continue
        entry = _NAME_REGISTRY.get(resource_type)
        if entry is None:
            unsupported.add(resource_type)
            continue
        query_names = set(names)
        if (
            resource_type in _PREFIX_GRANT_RESOURCE_TYPES
            and _POD_WIDE_GRANT_NAME in query_names
        ):
            resolved[(resource_type, _POD_WIDE_GRANT_NAME)] = pod_id
            query_names.discard(_POD_WIDE_GRANT_NAME)
        if not query_names:
            continue
        id_col, pod_col, name_col, extra_filters = entry
        stmt = select(name_col, id_col).where(
            pod_col == pod_id,
            name_col.in_(query_names),
            *extra_filters,
        )
        for name, resource_id in (await session.execute(stmt)).all():
            resolved[(resource_type, name)] = resource_id

    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=(
                "Resource type(s) do not support name-based grants: "
                f"{', '.join(sorted(t.value for t in unsupported))}"
            ),
        )
    missing = sorted(
        f"{resource_type.value}:{name}"
        for resource_type, name in {(t, n) for t, n in refs}
        if (resource_type, name) not in resolved
    )
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown resource name(s): {', '.join(missing)}",
        )
    return resolved


async def resolve_resource_names_by_ids(
    session: AsyncSession,
    *,
    pod_id: UUID,
    refs: Sequence[tuple[ResourceType, UUID]],
) -> dict[tuple[ResourceType, UUID], str]:
    """Resolve internal UUIDs back to resource names.

    Batched: one query per resource type. Ids that no longer resolve (deleted
    resources, deactivated connectors) are absent from the result; callers
    skip those grants.
    """
    ids_by_type: dict[ResourceType, set[UUID]] = {}
    for resource_type, resource_id in refs:
        ids_by_type.setdefault(resource_type, set()).add(resource_id)

    resolved: dict[tuple[ResourceType, UUID], str] = {}
    for resource_type, resource_ids in ids_by_type.items():
        if resource_type == ResourceType.CONNECTOR_ACCOUNT:
            stmt = select(Account.id).where(Account.id.in_(resource_ids))
            for account_id in (await session.execute(stmt)).scalars():
                resolved[(resource_type, account_id)] = str(account_id)
            continue
        if resource_type == ResourceType.CONNECTOR:
            # uuid5 mapping is one-way; scan the (small) active app catalog.
            stmt = select(Connector.id).where(Connector.is_active.is_(True))
            for connector_id in (await session.execute(stmt)).scalars():
                resource_id = connector_resource_id(connector_id)
                if resource_id in resource_ids:
                    resolved[(resource_type, resource_id)] = connector_id
            continue
        entry = _NAME_REGISTRY.get(resource_type)
        if entry is None:
            continue
        query_ids = set(resource_ids)
        if resource_type in _PREFIX_GRANT_RESOURCE_TYPES and pod_id in query_ids:
            resolved[(resource_type, pod_id)] = _POD_WIDE_GRANT_NAME
            query_ids.discard(pod_id)
        if not query_ids:
            continue
        id_col, pod_col, name_col, extra_filters = entry
        stmt = select(id_col, name_col).where(
            pod_col == pod_id,
            id_col.in_(query_ids),
            *extra_filters,
        )
        for resource_id, name in (await session.execute(stmt)).all():
            if name is not None:
                resolved[(resource_type, resource_id)] = name

    dangling = {(t, i) for t, i in refs} - set(resolved)
    if dangling:
        logger.debug(
            "authorization.resource_names.dangling_grants_skipped",
            pod_id=str(pod_id),
            refs=sorted(f"{t.value}:{i}" for t, i in dangling),
        )
    return resolved
