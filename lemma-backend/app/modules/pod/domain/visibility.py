"""Pod role visibility helpers."""

from __future__ import annotations

from collections.abc import Iterable

from app.modules.pod.domain.roles import PodRole


ROLE_HIERARCHY: dict[str, int] = {
    PodRole.VIEWER.value: 1,
    PodRole.USER.value: 2,
    PodRole.EDITOR.value: 3,
    PodRole.ADMIN.value: 4,
}

ROLE_ALIASES: dict[str, str] = {
    "VIEWER": PodRole.VIEWER.value,
    "USER": PodRole.USER.value,
    "EDITOR": PodRole.EDITOR.value,
    "ADMIN": PodRole.ADMIN.value,
}

SYSTEM_POD_ROLE_VALUES = set(ROLE_HIERARCHY)


def normalize_role_name(value: str | PodRole) -> str:
    raw = value.value if isinstance(value, PodRole) else str(value)
    normalized = raw.strip().upper()
    normalized = ROLE_ALIASES.get(normalized, normalized)
    if not normalized:
        raise ValueError("Role name is required")
    if len(normalized) > 120:
        raise ValueError("Role name must be 120 characters or fewer")
    if not all(char.isalnum() or char in {"_", "-"} for char in normalized):
        raise ValueError("Role names may contain only letters, numbers, underscore, and dash")
    return normalized


def normalize_system_pod_role(value: str | PodRole) -> str:
    role = normalize_role_name(value)
    if role not in SYSTEM_POD_ROLE_VALUES:
        raise ValueError(f"Invalid system pod role: {value}")
    return role


def normalize_role_list(values: Iterable[str | PodRole] | None) -> list[str]:
    seen: set[str] = set()
    roles: list[str] = []
    for value in values or []:
        role = normalize_role_name(value)
        if role in seen:
            continue
        seen.add(role)
        roles.append(role)
    return roles


def role_allows_required(assigned_role: str, required_role: str) -> bool:
    if required_role in ROLE_HIERARCHY:
        return ROLE_HIERARCHY.get(assigned_role, 0) >= ROLE_HIERARCHY[required_role]
    return assigned_role == required_role


def roles_allow_required(
    assigned_roles: Iterable[str | PodRole],
    required_role: str | PodRole,
) -> bool:
    required = normalize_role_name(required_role)
    return any(role_allows_required(normalize_role_name(role), required) for role in assigned_roles)


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
