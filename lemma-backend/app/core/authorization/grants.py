"""Helpers for module-owned resource permission grant APIs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import ResourceType
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import PERMISSION_BY_ID
from app.core.authorization.resource_actions import RESOURCE_ACTIONS
from app.core.authorization.resource_names import (
    connector_resource_id,
    resolve_resource_ids_by_names,
    resolve_resource_names_by_ids,
)

__all__ = [
    "NormalizedResourceGrant",
    "ResourceGrantInputProtocol",
    "delete_grantee_grants",
    "delete_resource_grants",
    "delete_resource_grantee_grant",
    "delete_resource_sharing_grants",
    "ensure_grant_uses_resource_name",
    "grant_resource_type_values",
    "connector_resource_id",
    "list_grantee_resource_grants",
    "list_resource_grants",
    "normalize_pod_resource_grants",
    "replace_grantee_resource_grants",
    "replace_resource_grantee_grant",
    "validate_pod_resource_grant_permissions",
]

# Grantee types representing humans (sharing); AGENT/FUNCTION grantees are
# workload capability grants and must survive visibility transitions.
HUMAN_GRANTEE_TYPES: tuple[str, ...] = ("ROLE", "POD_MEMBER")


class ResourceGrantInputProtocol(Protocol):
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str]


def ensure_grant_uses_resource_name(data: Any) -> Any:
    """Pydantic ``mode="before"`` pre-validator for ``*ResourcePermissionRequest``
    schemas.

    Resource grants are keyed by ``resource_name`` so renames don't break grants,
    but *exported* bundles carry ``resource_id``. Replaying an export into the
    write API otherwise failed with a generic "resource_name field required" (or,
    on raw-dict paths, a bare ``KeyError: 'resource_name'``). Detect the mistake
    up front and explain the name/id split.
    """
    if (
        isinstance(data, dict)
        and not data.get("resource_name")
        and data.get("resource_id")
    ):
        raise ValueError(
            "Resource grants are keyed by 'resource_name', not 'resource_id'. "
            "Exported bundles carry resource_id, but the write API resolves names "
            "(so renames don't break grants) — pass the resource's name instead."
        )
    return data


@dataclass(frozen=True, slots=True)
class NormalizedResourceGrant:
    resource_type: ResourceType
    resource_id: UUID
    permission_ids: list[str]


def validate_pod_resource_grant_permissions(
    grants: Sequence[ResourceGrantInputProtocol],
) -> None:
    unknown = {
        permission_id
        for grant in grants
        for permission_id in grant.permission_ids
        if permission_id not in PERMISSION_BY_ID
    }
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown permission id(s): {', '.join(sorted(unknown))}",
        )
    non_pod = {
        permission_id
        for grant in grants
        for permission_id in grant.permission_ids
        if PERMISSION_BY_ID[permission_id].scope.value != "POD"
    }
    if non_pod:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only pod-scoped permissions can be granted to pod resources: "
                f"{', '.join(sorted(non_pod))}"
            ),
        )
    mismatched = sorted(
        {
            f"{grant.resource_type.value}:{permission_id}"
            for grant in grants
            for permission_id in set(grant.permission_ids)
            - set(_applicable_permission_ids(grant.resource_type))
        }
    )
    if mismatched:
        raise HTTPException(
            status_code=400,
            detail=(
                "Permission id(s) do not apply to the resource type: "
                f"{', '.join(mismatched)}"
            ),
        )


def _applicable_permission_ids(resource_type: ResourceType) -> frozenset[str]:
    if resource_type in (
        ResourceType.CONNECTOR,
        ResourceType.CONNECTOR_ACCOUNT,
    ):
        return frozenset(
            permission_id
            for permission_id, definition in PERMISSION_BY_ID.items()
            if definition.resource_type == resource_type.value
            and definition.scope.value == "POD"
        )
    return frozenset(RESOURCE_ACTIONS.get(resource_type, ()))


async def normalize_pod_resource_grants(
    session: AsyncSession,
    *,
    pod_id: UUID,
    grants: Sequence[ResourceGrantInputProtocol],
) -> list[NormalizedResourceGrant]:
    """Resolve grant resource names to the UUIDs used internally.

    Unknown names raise HTTP 400 listing every unresolved name.
    """
    resolved = await resolve_resource_ids_by_names(
        session,
        pod_id=pod_id,
        refs=[(grant.resource_type, grant.resource_name) for grant in grants],
    )
    return [
        NormalizedResourceGrant(
            resource_type=grant.resource_type,
            resource_id=resolved[(grant.resource_type, grant.resource_name)],
            permission_ids=list(grant.permission_ids),
        )
        for grant in grants
    ]


async def replace_grantee_resource_grants(
    session: AsyncSession,
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    grants: Sequence[NormalizedResourceGrant],
    created_by_user_id: UUID | None,
) -> None:
    await session.execute(
        delete(ResourcePermissionGrantModel).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.grantee_type == grantee_type,
            ResourcePermissionGrantModel.grantee_id == grantee_id,
        )
    )
    grant_rows = {
        (grant.resource_type, grant.resource_id, permission_id)
        for grant in grants
        for permission_id in grant.permission_ids
    }
    for resource_type, resource_id, permission_id in sorted(
        grant_rows,
        key=lambda item: (item[0].value, str(item[1]), item[2]),
    ):
        session.add(
            ResourcePermissionGrantModel(
                pod_id=pod_id,
                resource_type=resource_type.value,
                resource_id=resource_id,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
                permission_id=permission_id,
                created_by_user_id=created_by_user_id,
            )
        )
    await session.flush()


async def replace_resource_grantee_grant(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    permission_ids: Sequence[str],
    created_by_user_id: UUID | None,
) -> None:
    await session.execute(
        delete(ResourcePermissionGrantModel).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_type == resource_type.value,
            ResourcePermissionGrantModel.resource_id == resource_id,
            ResourcePermissionGrantModel.grantee_type == grantee_type,
            ResourcePermissionGrantModel.grantee_id == grantee_id,
        )
    )
    for permission_id in sorted(set(permission_ids)):
        session.add(
            ResourcePermissionGrantModel(
                pod_id=pod_id,
                resource_type=resource_type.value,
                resource_id=resource_id,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
                permission_id=permission_id,
                created_by_user_id=created_by_user_id,
            )
        )
    await session.flush()


async def delete_resource_grantee_grant(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
) -> None:
    await session.execute(
        delete(ResourcePermissionGrantModel).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_type == resource_type.value,
            ResourcePermissionGrantModel.resource_id == resource_id,
            ResourcePermissionGrantModel.grantee_type == grantee_type,
            ResourcePermissionGrantModel.grantee_id == grantee_id,
        )
    )
    await session.flush()


async def delete_resource_grants(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
) -> None:
    """Delete every grant on a resource (all grantee types). Use on delete."""
    await session.execute(
        delete(ResourcePermissionGrantModel).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_type.in_(
                grant_resource_type_values(resource_type)
            ),
            ResourcePermissionGrantModel.resource_id == resource_id,
        )
    )
    await session.flush()


async def delete_resource_sharing_grants(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
) -> None:
    """Delete human-sharing grants (ROLE/POD_MEMBER) on a resource.

    Workload capability grants (AGENT/FUNCTION grantees) are preserved; use
    this when a resource leaves RESTRICTED visibility.
    """
    await session.execute(
        delete(ResourcePermissionGrantModel).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_type.in_(
                grant_resource_type_values(resource_type)
            ),
            ResourcePermissionGrantModel.resource_id == resource_id,
            ResourcePermissionGrantModel.grantee_type.in_(HUMAN_GRANTEE_TYPES),
        )
    )
    await session.flush()


async def delete_grantee_grants(
    session: AsyncSession,
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
) -> None:
    """Delete every grant held by a grantee. Use when the grantee is deleted."""
    await session.execute(
        delete(ResourcePermissionGrantModel).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.grantee_type == grantee_type,
            ResourcePermissionGrantModel.grantee_id == grantee_id,
        )
    )
    await session.flush()


async def list_resource_grants(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
) -> dict[tuple[str, UUID], list[str]]:
    stmt = (
        select(ResourcePermissionGrantModel)
        .where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_type == resource_type.value,
            ResourcePermissionGrantModel.resource_id == resource_id,
        )
        .order_by(
            ResourcePermissionGrantModel.grantee_type,
            ResourcePermissionGrantModel.grantee_id,
            ResourcePermissionGrantModel.permission_id,
        )
    )
    rows = list((await session.execute(stmt)).scalars().all())
    grouped: dict[tuple[str, UUID], list[str]] = {}
    for row in rows:
        grouped.setdefault((row.grantee_type, row.grantee_id), []).append(
            row.permission_id
        )
    return grouped


def grant_resource_type_values(resource_type: ResourceType) -> tuple[str, ...]:
    """Grant rows for folders and documents are interchangeable.

    FOLDER and DOCUMENT are one logical resource (a row in ``datastore_files``)
    distinguished only by ``kind``. They share the same model, permission
    family (``folder.*``) and path-based name resolution, so a grant stored
    under either type matches the other. The physical enum is kept split for
    backward compatibility with existing stored grants.
    """
    values = {resource_type.value}
    if resource_type == ResourceType.DOCUMENT:
        values.add(ResourceType.FOLDER.value)
    elif resource_type == ResourceType.FOLDER:
        values.add(ResourceType.DOCUMENT.value)
    return tuple(sorted(values))


async def list_grantee_resource_grants(
    session: AsyncSession,
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
) -> dict[tuple[ResourceType, str], list[str]]:
    """Return a grantee's grants keyed by (resource_type, resource_name).

    Grants whose resource no longer resolves to a name (deleted resources,
    deactivated connectors) are skipped.

    Effectiveness contract: a listed FOLDER/DOCUMENT grant is effective for a
    resource R iff R's path has the granted path as a prefix (a folder grant
    cascades to its descendants). The matcher in ``service.py`` and the SQL
    projection in ``sql_actions.py`` enforce exactly that rule, so a grant shown
    here on a folder authorizes every document beneath it — not just the folder
    row itself.
    """
    stmt = (
        select(ResourcePermissionGrantModel)
        .where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.grantee_type == grantee_type,
            ResourcePermissionGrantModel.grantee_id == grantee_id,
        )
        .order_by(
            ResourcePermissionGrantModel.resource_type,
            ResourcePermissionGrantModel.resource_id,
            ResourcePermissionGrantModel.permission_id,
        )
    )
    rows = list((await session.execute(stmt)).scalars().all())
    names = await resolve_resource_names_by_ids(
        session,
        pod_id=pod_id,
        refs=[(ResourceType(row.resource_type), row.resource_id) for row in rows],
    )
    grouped: dict[tuple[ResourceType, str], list[str]] = {}
    for row in rows:
        resource_type = ResourceType(row.resource_type)
        resource_name = names.get((resource_type, row.resource_id))
        if resource_name is None:
            continue
        grouped.setdefault((resource_type, resource_name), []).append(
            row.permission_id
        )
    return grouped
