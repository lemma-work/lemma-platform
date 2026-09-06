"""Pod role visibility helpers."""

from __future__ import annotations

from collections.abc import Iterable

from app.core.authorization.roles import (
    ROLE_ALIASES,
    normalize_role_list,
    normalize_role_name,
)
from app.modules.pod.domain.roles import PodRole

__all__ = [
    "ROLE_ALIASES",
    "ROLE_HIERARCHY",
    "SYSTEM_POD_ROLE_VALUES",
    "normalize_role_list",
    "normalize_role_name",
    "normalize_system_pod_role",
]

# Re-exported rather than defined here. `app/core/authorization` owns the role
# set -- `SYSTEM_ROLE_PERMISSIONS` is keyed by it -- so it owns the spelling
# rules too; this module keeps the enum and the ordering, which are pod's.


ROLE_HIERARCHY: dict[str, int] = {
    PodRole.VIEWER.value: 1,
    PodRole.USER.value: 2,
    PodRole.EDITOR.value: 3,
    PodRole.ADMIN.value: 4,
}

SYSTEM_POD_ROLE_VALUES = set(ROLE_HIERARCHY)


def normalize_system_pod_role(value: str | PodRole) -> str:
    role = normalize_role_name(value)
    if role not in SYSTEM_POD_ROLE_VALUES:
        raise ValueError(f"Invalid system pod role: {value}")
    return role


def role_allows_required(assigned_role: str, required_role: str) -> bool:
    if required_role in ROLE_HIERARCHY:
        return ROLE_HIERARCHY.get(assigned_role, 0) >= ROLE_HIERARCHY[required_role]
    return assigned_role == required_role


def roles_allow_required(
    assigned_roles: Iterable[str | PodRole],
    required_role: str | PodRole,
) -> bool:
    required = normalize_role_name(required_role)
    return any(
        role_allows_required(normalize_role_name(role), required)
        for role in assigned_roles
    )


def highest_role(roles: Iterable[str | PodRole]) -> str:
    normalized = normalize_role_list(roles)
    if not normalized:
        return PodRole.VIEWER.value
    system_roles = [role for role in normalized if role in ROLE_HIERARCHY]
    if not system_roles:
        return PodRole.VIEWER.value
    return max(system_roles, key=lambda role: ROLE_HIERARCHY.get(role, 0))


def is_system_role(value: str | PodRole) -> bool:
    return normalize_role_name(value) in SYSTEM_POD_ROLE_VALUES
