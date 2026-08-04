"""Reading and applying a bundle's resource grants.

Grants are the last thing an import writes and the first thing that breaks a pod
when they're wrong, so they get their own module rather than living inside the
applier: both grantee types (AGENT and FUNCTION) run the same deferred step, and
the reading half is shared with the step runners that execute outside the apply
loop's unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.log.log import get_logger

if TYPE_CHECKING:
    from app.core.authorization.context import ResourceType

logger = get_logger(__name__)


@dataclass(frozen=True)
class GrantInput:
    """Adapts a bundle manifest grant entry to the ``ResourceGrantInputProtocol``
    the shared authorization layer expects (``resource_type`` / ``resource_name``
    / ``permission_ids``). ``resource_type`` holds a ``ResourceType`` enum — the
    annotation stays a string thanks to ``from __future__ import annotations`` so
    the module import stays lazy/cycle-free."""

    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str]


def grants_from_payload(payload: dict[str, Any]) -> list[GrantInput]:
    """Read ``permissions.grants`` (or a bare top-level ``grants`` list) off a
    resource manifest into typed grant inputs. Entries whose ``resource_type`` is
    not a known :class:`ResourceType` or that omit a ``resource_name`` are
    skipped with a warning rather than failing the whole import."""
    from app.core.authorization.context import ResourceType

    perms = payload.get("permissions")
    raw = perms.get("grants") if isinstance(perms, dict) else payload.get("grants")
    grants: list[GrantInput] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        raw_type = entry.get("resource_type")
        try:
            resource_type = ResourceType(str(raw_type))
        except ValueError:
            logger.debug(
                "pod_bundle.applier.skipping_grant_unknown_resource_type.diagnostic",
                raw_type=raw_type,
            )
            continue
        resource_name = entry.get("resource_name")
        if not resource_name:
            logger.debug(
                "pod_bundle.applier.skipping_grant_without_resource_name.diagnostic"
            )
            continue
        grants.append(
            GrantInput(
                resource_type=resource_type,
                resource_name=str(resource_name),
                permission_ids=[str(p) for p in entry.get("permission_ids") or []],
            )
        )
    return grants


def has_grants(payload: dict[str, Any]) -> bool:
    """True when a manifest says anything about permissions at all.

    An empty ``{"grants": []}`` counts: it means "this workload holds nothing",
    which is a write, not a no-op. Only a manifest with no ``permissions`` key
    leaves the target's existing grants alone.
    """
    perms = payload.get("permissions")
    if isinstance(perms, dict):
        return "grants" in perms or bool(perms)
    return "permissions" in payload or bool(payload.get("grants"))


async def apply_grants(
    session: Any,
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    grants: list[GrantInput],
    created_by_user_id: UUID | None,
) -> None:
    """Validate + normalize (resource_name -> id) + replace a grantee's resource
    grants. Mirrors the function/agent controllers' inline-grants path so an
    imported workload gets the same executable permissions a hand-authored one
    would."""
    if not grants:
        return
    from app.core.authorization.grants import (
        normalize_pod_resource_grants,
        replace_grantee_resource_grants,
        validate_pod_resource_grant_permissions,
    )

    validate_pod_resource_grant_permissions(grants)
    normalized = await normalize_pod_resource_grants(
        session, pod_id=pod_id, grants=grants
    )
    await replace_grantee_resource_grants(
        session,
        pod_id=pod_id,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        grants=normalized,
        created_by_user_id=created_by_user_id,
    )
